"""Safe panel updates from the GitHub commit Railway/Render should deploy.

The production image is built from GitHub and does not contain a writable ``.git``
checkout. Pulling files inside the running container would update the template but
not the installed Python dependencies or pinned proxy binaries, and replacing the
whole container without persistent storage would erase users and their UUID-based
subscription links. This module therefore does the only safe thing a running
panel can do:

* compare the deployed commit with the configured GitHub branch;
* refuse the update unless storage is external or SQLite is on a mounted volume;
* write a pre-update backup on that persistent volume;
* ask Railway to redeploy (or call a Render deploy hook), so GitHub remains the
  source of truth and Docker installs the complete new image;
* leave a guard in the database so the new process verifies that every user UUID
  survived the restart before the panel reports success.

No GitHub token is needed for the public repository, and no database secret is
returned to the browser.
"""
import hashlib
import json
import os
import re
import time
import urllib.parse

import httpx

from app import db, railway, runtime
from app.db import execute, row, rows
from app.services.backup import export_all

STARTED_AT = time.time()
MARKER = 'panel_update_guard'
DEFAULT_REPOSITORY = 'miladjahani/Mizetusi'
DEFAULT_BRANCH = 'main'
GITHUB_API = 'https://api.github.com'
TIMEOUT = httpx.Timeout(12.0, connect=6.0)


def _env(*names, default=''):
    for name in names:
        value = (os.getenv(name) or '').strip()
        if value:
            return value
    return default


def repository():
    return _env('NEXUS_UPDATE_REPOSITORY', default=DEFAULT_REPOSITORY)


def branch():
    return _env('NEXUS_UPDATE_BRANCH', 'RAILWAY_GIT_BRANCH', 'RENDER_GIT_BRANCH', default=DEFAULT_BRANCH)


def current_commit():
    return _env('NEXUS_GIT_COMMIT', 'RAILWAY_GIT_COMMIT_SHA', 'RENDER_GIT_COMMIT', 'GITHUB_SHA')


def _valid_repository(value):
    return bool(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value or ''))


def github_latest():
    """The branch head on GitHub, or ``(None, readable_error)``."""
    repo = repository()
    if not _valid_repository(repo):
        return None, 'NEXUS_UPDATE_REPOSITORY باید با قالب owner/repository نوشته شود'
    url = f'{GITHUB_API}/repos/{repo}/commits/{urllib.parse.quote(branch(), safe="")}'
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.get(url, headers={'Accept': 'application/vnd.github+json',
                                                 'X-GitHub-Api-Version': '2022-11-28'})
    except Exception as exc:
        return None, f'ارتباط با GitHub برقرار نشد ({type(exc).__name__})'
    if response.status_code != 200:
        return None, f'GitHub نسخهٔ جدید را برنگرداند (HTTP {response.status_code})'
    try:
        payload = response.json()
        commit = payload.get('commit') or {}
        message = str(commit.get('message') or '').splitlines()[0][:120]
        return {'sha': str(payload.get('sha') or ''), 'message': message,
                'url': str(payload.get('html_url') or '')}, ''
    except Exception:
        return None, 'پاسخ GitHub خوانده نشد'


def storage():
    """Whether a redeploy can preserve the database and every UUID-based link."""
    if db.is_pg():
        return {'safe': True, 'kind': 'PostgreSQL', 'detail': 'دیتابیس خارج از کانتینر است'}
    path = os.path.abspath(db.SQLITE_PATH)
    if path == ':memory:':
        return {'safe': False, 'kind': 'SQLite', 'detail': 'دیتابیس حافظه‌ای پس از ری‌استارت از بین می‌رود'}
    parent = os.path.dirname(path)
    mounted = os.path.ismount(parent)
    if not mounted and _env('NEXUS_PERSISTENT_STORAGE', default='') in ('1', 'true', 'yes', 'on'):
        mounted = True
    return {'safe': mounted, 'kind': 'SQLite', 'path': path,
            'detail': ('روی volume پایدار است' if mounted else
                       'SQLite روی volume پایدار نیست؛ ابتدا یک volume برای /data بسازید')}


def provider():
    """How this deployment asks its PaaS to build the latest GitHub commit."""
    if railway.configured():
        return {'id': 'railway', 'label': 'Railway', 'configured': True, 'missing': []}
    hook = _env('RENDER_DEPLOY_HOOK_URL')
    if hook:
        return {'id': 'render', 'label': 'Render', 'configured': True, 'missing': []}
    missing = railway.missing()
    if runtime.platform() == 'render':
        missing = ['RENDER_DEPLOY_HOOK_URL']
    elif runtime.platform() == 'railway':
        pass
    else:
        return {'id': runtime.platform(), 'label': runtime.label(), 'configured': False,
                'missing': ['RAILWAY_API_TOKEN یا RENDER_DEPLOY_HOOK_URL']}
    return {'id': 'railway', 'label': 'Railway', 'configured': False, 'missing': missing}


def _identity():
    users = rows('SELECT id,uuid FROM users ORDER BY id')
    digest = hashlib.sha256(json.dumps(users, separators=(',', ':'), sort_keys=True).encode()).hexdigest()
    return {'users': len(users), 'nodes': int(row('SELECT COUNT(*) AS n FROM nodes')['n']),
            # The guard marker itself is written after this snapshot, so it is
            # excluded from the count rather than looking like a changed setting.
            'settings': int(row('SELECT COUNT(*) AS n FROM settings WHERE key<>?', (MARKER,))['n']),
            'identity': digest}


def _marker():
    try:
        value = (row('SELECT value FROM settings WHERE key=?', (MARKER,)) or {}).get('value')
        return json.loads(value) if value else None
    except Exception:
        return None


def _save_marker(value):
    execute('INSERT INTO settings(key,value) VALUES(?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (MARKER, json.dumps(value)))


def _clear_marker():
    execute('DELETE FROM settings WHERE key=?', (MARKER,))


def _guard():
    marker = _marker()
    if not marker:
        return None
    current = _identity()
    expected = marker.get('identity') or {}
    requested_by_process = float(marker.get('process_started_at') or 0)
    restarted = (abs(STARTED_AT - requested_by_process) > 0.001 if requested_by_process
                 else STARTED_AT >= float(marker.get('requested_at') or 0) - 5)
    # A new release may add nodes/settings through migrations. That is healthy;
    # losing an existing row is not. User UUIDs must match exactly because those
    # are the immutable tokens in every portal and subscription link.
    links_preserved = (current.get('users') == expected.get('users')
                       and current.get('identity') == expected.get('identity'))
    counts_preserved = all(int(current.get(key) or 0) >= int(expected.get(key) or 0)
                           for key in ('nodes', 'settings'))
    preserved = links_preserved and counts_preserved
    return {**marker, 'restarted': restarted, 'links_preserved': links_preserved,
            'counts_preserved': counts_preserved, 'preserved': preserved,
            'verified': restarted and preserved}


def status(check_remote=True):
    """Everything the confirmation dialog needs before it offers an update."""
    store = storage()
    target = provider()
    remote, remote_error = github_latest() if check_remote else (None, '')
    current_sha = current_commit()
    latest_sha = (remote or {}).get('sha') or ''
    update_available = bool(latest_sha and current_sha and latest_sha != current_sha)
    reason = ''
    if not store['safe']:
        reason = store['detail']
    elif not target['configured']:
        reason = 'توکن استقرار آماده نیست: ' + '، '.join(target.get('missing') or [])
    elif check_remote and remote_error:
        reason = remote_error
    return {
        'repository': repository(), 'branch': branch(), 'url': f'https://github.com/{repository()}/tree/{branch()}',
        'current_commit': current_sha, 'latest': remote, 'remote_error': remote_error,
        'update_available': update_available, 'storage': store, 'provider': target,
        'safe': not reason, 'reason': reason, 'guard': _guard(),
    }


def _backup():
    """A durable JSON snapshot beside SQLite; external databases need no local copy."""
    if db.is_pg():
        return '', 'PostgreSQL outside the container'
    parent = os.path.dirname(os.path.abspath(db.SQLITE_PATH))
    folder = _env('NEXUS_UPDATE_BACKUP_DIR', default=os.path.join(parent, 'backups'))
    os.makedirs(folder, exist_ok=True)
    name = f'pre-update-{int(time.time())}-{os.getpid()}.json'
    path = os.path.join(folder, name)
    payload = {'created_at': int(time.time()), 'repository': repository(), 'branch': branch(),
               'commit': current_commit(), 'tables': export_all()}
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(',', ':'))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path, 'SQLite backup written to persistent storage'


def _trigger():
    target = provider()
    if target['id'] == 'railway' and target['configured']:
        return railway.redeploy()
    if target['id'] == 'render' and target['configured']:
        hook = _env('RENDER_DEPLOY_HOOK_URL')
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                response = client.post(hook)
        except Exception as exc:
            return False, f'اجرای Render deploy hook ناموفق بود ({type(exc).__name__})'
        if not 200 <= response.status_code < 300:
            return False, f'Render deploy hook خطا داد (HTTP {response.status_code})'
        return True, ''
    return False, 'این پلتفرم از بروزرسانی مستقیم پشتیبانی نمی‌کند'


def apply():
    """Back up, arm the identity guard, and ask the PaaS to redeploy GitHub."""
    before = status(check_remote=True)
    if not before['safe']:
        return {'ok': False, **before}
    target_sha = (before.get('latest') or {}).get('sha') or ''
    if not target_sha:
        return {'ok': False, **before, 'reason': 'commit جدید از GitHub خوانده نشد'}
    try:
        backup_path, backup_note = _backup()
        identity = _identity()
    except Exception as exc:
        return {'ok': False, **before, 'reason': f'پشتیبان پیش از بروزرسانی ساخته نشد ({type(exc).__name__})'}
    marker = {'requested_at': time.time(), 'process_started_at': STARTED_AT,
              'target_commit': target_sha, 'previous_commit': before['current_commit'], 'identity': identity,
              'backup_path': backup_path, 'provider': before['provider']['id']}
    _save_marker(marker)
    ok, reason = _trigger()
    if not ok:
        _clear_marker()
        return {'ok': False, **before, 'reason': reason, 'backup_path': backup_path,
                'backup_note': backup_note}
    return {'ok': True, 'accepted': True, 'target_commit': target_sha,
            'provider': before['provider'], 'backup_path': backup_path,
            'backup_note': backup_note, 'guard': marker}
