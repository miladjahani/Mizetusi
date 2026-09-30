"""Panel extras that answer the three newest admin workflows.

Kept out of ``app/main.py`` (which is already the core routing module) and out of
``app/api_extra.py`` (customization / tools / Telegram) so each file stays about
one thing:

* ``/api/nodes/select`` and ``/api/nodes/selection`` — **manual selection of
  scanned nodes**. A scan parks every freshly discovered edge node as a
  *candidate*; these endpoints are how an admin publishes the ones they want (and
  how they hand the decision back to the scan with the auto-publish switch).
* ``/api/settings/sections`` and ``/api/settings/reset`` — **reset to defaults,
  per panel tab**. One mapping (below) says which settings keys belong to which
  tab, and resetting a tab deletes exactly those keys.
* ``/api/subscription/*`` — the **advanced subscription** surface: a usage report
  with expiry/quota alerts, per-user token rotation, and per-client target
  fine-tuning.

Every route authenticates through the panel's own session, resolved lazily so this
module imports into ``app.main`` without a cycle.
"""
import json
import time
import urllib.parse
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.core.settings_store import store
from app.db import execute
from app.subscriptions import reports
from app.subscriptions.clients import catalog as client_catalog
from app.subscriptions.clients import CLIENTS
from app.subscriptions.clients import subscription_url as client_subscription_url

router = APIRouter()

# Which settings keys belong to which panel tab. A tab's «بازگردانی پیش‌فرض»
# deletes exactly its own keys, so resetting «نودها» can never quietly drop the
# brand colours an admin spent time on. Keys that were never set are harmless
# here (DELETE is a no-op), so a section can list a key before the first write.
SETTINGS_SECTIONS = {
    'customize': ('portal_banner', 'support_url', 'flags_enabled', 'default_format',
                  'app_name', 'accent', 'accent_secondary', 'geo_lookup',
                  'default_max_configs', 'default_scope'),
    'subs': ('sub_prefix', 'default_protocol', 'default_limit_gb', 'default_expiry_days',
             'default_ip_limit', 'default_format', 'default_max_configs', 'default_scope',
             'alert_expiry_days', 'alert_quota_percent'),
    'nodes': ('edge_auto_publish',),
    'advanced': ('hy2_enabled', 'hy2_host', 'hy2_port', 'hy2_sni', 'hy2_obfs',
                 'hy2_insecure', 'hy2_label', 'trust_client_ip', 'trusted_proxy_cidrs',
                 # Advanced obfuscation: the deployment defaults plus one switch
                 # per transport (app/subscriptions/obfuscation.py).
                 'obfs_spider_x', 'obfs_flow', 'obfs_packet_encoding',
                 'obfs_vless-xhttp', 'obfs_vless-grpc', 'obfs_vless-httpupgrade',
                 'obfs_vmess-grpc', 'obfs_trojan-grpc',
                 # What the deployment does not announce about itself
                 # (app/core/stealth.py).
                 'stealth_headers', 'stealth_docs', 'stealth_health',
                 'stealth_sub_headers', 'stealth_server_header'),
    'settings': ('public_base_url', 'session_days', 'ping_interval'),
}

# The per-client fine-tuning map: client id -> the subscription target its link
# should use. Absent means «the format this client imports by default».
CLIENT_OVERRIDE_KEY = 'client_target_overrides'


def _main():
    from app import main
    return main


def _auth(request):
    _main().auth(request)


def _set(key, value):
    return store.set(key, value)


def _audit(action, detail=''):
    _main()._audit(action, detail)


async def _body(request):
    try:
        body = await request.json()
    except Exception:
        body = None
    return body if isinstance(body, dict) else {}


# ------------------------------------------------------- manual node selection
@router.post('/api/nodes/select')
async def select_nodes(request: Request):
    """Publish or park scanned edge nodes — the manual-selection half of a scan."""
    _auth(request)
    body = await _body(request)
    names = [str(item).strip() for item in (body.get('names') or []) if str(item).strip()]
    if not names:
        raise HTTPException(400, 'حداقل یک نود لازم است')
    enabled = bool(body.get('enabled', True))
    catalog = _main().node_catalog
    chosen = catalog.select(names, enabled=enabled)
    _audit('nodes.select', f"{'publish' if enabled else 'park'} {len(chosen)}")
    return {'success': True, 'names': chosen, 'enabled': enabled,
            'candidates': len(catalog.candidates())}


@router.post('/api/nodes/selection')
async def node_selection(request: Request):
    """Read or set the «publish scanned nodes automatically» switch.

    Turning it on publishes everything currently waiting as a candidate, so the
    switch and the manual list can never disagree about the same node.
    """
    _auth(request)
    body = await _body(request)
    catalog = _main().node_catalog
    if 'auto' not in body:
        return {'success': True, 'auto': catalog.auto_publish(),
                'candidates': len(catalog.candidates())}
    auto = bool(body.get('auto'))
    _set('edge_auto_publish', '1' if auto else '0')
    published = []
    if auto:
        published = catalog.select([n['name'] for n in catalog.candidates()], enabled=True)
    _audit('nodes.selection', 'auto' if auto else 'manual')
    return {'success': True, 'auto': auto, 'published': published,
            'candidates': len(catalog.candidates())}


# ------------------------------------------------------- reset-to-default tabs
@router.get('/api/settings/sections')
def settings_sections(request: Request):
    """Which panel tabs have resettable defaults, and what each one restores."""
    _auth(request)
    main = _main()
    known = set(main.GENERAL_SETTING_KEYS)
    return {'success': True, 'sections': {
        section: {'keys': list(keys),
                  'tracked': [key for key in keys if key in known]}
        for section, keys in SETTINGS_SECTIONS.items()}}


@router.post('/api/settings/reset')
async def reset_settings(request: Request):
    """Delete one tab's settings so its defaults apply again."""
    _auth(request)
    body = await _body(request)
    section = str(body.get('section') or '').strip().lower()
    if section not in SETTINGS_SECTIONS:
        raise HTTPException(400, 'بخش ناشناخته برای بازگردانی پیش‌فرض')
    keys = SETTINGS_SECTIONS[section]
    for key in keys:
        execute('DELETE FROM settings WHERE key=?', (key,))
    _audit('settings.reset', section)
    return {'success': True, 'section': section, 'keys': list(keys)}


# --------------------------------------------------------- advanced subscription
def _client_overrides():
    raw = store.get(CLIENT_OVERRIDE_KEY)
    try:
        value = json.loads(raw) if raw else {}
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _client_payload(base, token, overrides):
    """Every client with the target it really subscribes to (the override wins)."""
    from app.subscriptions import generator
    items = []
    for client in CLIENTS:
        target = overrides.get(client['id']) or client['format']
        try:
            target = generator.normalize_target(target)
        except ValueError:
            target = client['format']
        items.append({
            'id': client['id'],
            'name': client['name'],
            'platform': client['platform'],
            'format': client['format'],
            'override': overrides.get(client['id']) or '',
            'target': target,
            'url': client_subscription_url(base, token, target, '') if token else '',
        })
    return items


@router.get('/api/subscription/report')
def subscription_report(request: Request, sort: str = 'usage', user: str = '', target: str = ''):
    """Advanced subscription payload: usage, alerts, per-client fine-tuning.

    ``user`` (optional) is who the per-client links are built for; without it the
    report still carries the usage/alerts the admin reads first.
    """
    _auth(request)
    main = _main()
    payload = reports.report(sort=sort)
    overrides = _client_overrides()
    base = main.public_base(request)
    token = ''
    username = ''
    if user:
        account = main.get_user(user)
        if not account:
            raise HTTPException(404, 'user not found')
        username = account['username']
        token = urllib.parse.quote(account['uuid'], safe='')
    payload.update({
        'success': True,
        'user': username,
        'base_url': base,
        'overrides': overrides,
        'clients': _client_payload(base, token, overrides),
        'targets': [{'target': item, 'label': main._target_label(item)}
                    for item in main.SUB_TARGETS if item != 'auto'],
    })
    return payload


@router.post('/api/subscription/alerts')
async def save_alert_thresholds(request: Request):
    """Save the expiry/quota alert thresholds the report is graded against."""
    _auth(request)
    body = await _body(request)
    try:
        thresholds = reports.save_thresholds(body.get('expiry_days'), body.get('quota_percent'))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    _audit('subscription.alerts',
           f"expiry={thresholds['expiry_days']}d quota={thresholds['quota_percent']}%")
    return {'success': True, 'thresholds': thresholds}


@router.post('/api/subscription/clients')
async def save_client_overrides(request: Request):
    """Fine-tune which subscription target each client receives."""
    _auth(request)
    body = await _body(request)
    raw = body.get('overrides')
    if not isinstance(raw, dict):
        raise HTTPException(400, 'invalid payload')
    from app.subscriptions import generator
    known = {client['id'] for client in CLIENTS}
    overrides = {}
    for client_id, target in raw.items():
        target = str(target or '').strip()
        if not target:
            continue
        if client_id not in known:
            raise HTTPException(400, f'unknown client: {client_id}')
        try:
            overrides[client_id] = generator.normalize_target(target)
        except ValueError:
            raise HTTPException(400, f'unsupported target for {client_id}: {target}')
    _set(CLIENT_OVERRIDE_KEY, json.dumps(overrides, ensure_ascii=False))
    _audit('subscription.clients', ','.join(sorted(overrides)) or 'reset')
    return {'success': True, 'overrides': overrides}


@router.post('/api/users/{username}/rotate')
def rotate_user_token(request: Request, username: str):
    """Issue a fresh subscription token, invalidating every URL already shared.

    This is the per-user half of link management: the moment a link leaks — a
    screenshot, a forwarded message — one call makes it worthless without
    touching the account's quota, expiry or node scope.
    """
    _auth(request)
    main = _main()
    account = main.get_user(username)
    if not account:
        raise HTTPException(404, 'user not found')
    token = str(uuid.uuid4())
    execute('UPDATE users SET uuid=? WHERE username=?', (token, username))
    _audit('user.rotate', username)
    base = main.public_base(request)
    quoted = urllib.parse.quote(token, safe='')
    return {
        'success': True,
        'uuid': token,
        'subscription': f'{base}/sub/{quoted}?target=auto',
        'portal_url': f'{base}/portal/{quoted}',
        'rotated_at': int(time.time()),
    }
