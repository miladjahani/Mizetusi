"""Supervising the second engines.

The panel asks this module exactly one question — *is this protocol really
listening right now?* — and it answers with the same honesty
``app/xray.py`` applies to the Xray inbounds: a profile is published only when an
engine is running it **and** the port is reachable from outside; otherwise it is
withheld and the reason is spelled out for the panel.

Lifecycle mirrors the Xray supervisor on purpose so there is one mental model:
keep the last config hash, validate the new config with the engine itself
(``sing-box check`` / ``mihomo -t``) before restarting anything, and never let a
bad config take down a listener that is currently working.
"""
import asyncio
import hashlib
import json
import os

from app import ports, runtime
from app.config import settings
from app.cores import engines, profiles
from app.db import execute, row, rows

# engine -> running process, and the hash of the config it was started with.
_procs = {}
_hashes = {}
# engine -> {'profiles': [...], 'error': str, 'warning': str}
_state = {}
# profile ids really listening (``None`` = not known yet, e.g. in a test process
# that never started an engine: the full enabled matrix is then published, which
# is what keeps the generator and the panel useful without a live listener).
_published = None


# ------------------------------------------------------------------- settings
def _setting(key, default=None):
    try:
        found = row('SELECT value FROM settings WHERE key=?', (key,))
    except Exception:
        found = None
    value = (found or {}).get('value') if found else None
    return value if value not in (None, '') else default


def _store(key, value):
    try:
        execute('INSERT INTO settings(key,value) VALUES(?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
    except Exception:
        pass


def enabled(profile):
    """Whether an admin turned this protocol on (off by default)."""
    return (_setting(profiles.enabled_key(profile)) or '0') == '1'


def port(profile):
    """The public port the listener binds."""
    default = settings.core_anytls_port if profile['id'] == profiles.ANYTLS else settings.core_tuic_port
    raw = _setting(profiles.port_key(profile))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return value if 1 <= value <= 65535 else int(default)


def engine_of(profile):
    """Which engine hosts this protocol (default: sing-box)."""
    chosen = str(_setting(profiles.engine_key(profile)) or profile['engine']).strip().lower()
    return chosen if chosen in profiles.ENGINES else profile['engine']


def chosen():
    """Every hosted protocol with its resolved settings, in catalog order."""
    out = []
    for profile in profiles.PROFILES:
        item = dict(profile)
        item.update({'enabled': enabled(profile), 'port': port(profile), 'engine': engine_of(profile)})
        out.append(item)
    return out


# ------------------------------------------------------------------ reachability
def _transports():
    """Lazy: ``transports`` imports this module's facade for the catalog."""
    from app.subscriptions import transports
    return transports


def host(profile=None):
    """Where these listeners are reachable from outside, or ''.

    With a profile the TCP proxy created for that listener's own port wins: on
    Railway every forwarded port has its own name and only that one forwards it
    (app/ports.py). Without one the panel's general direct endpoint is used.
    """
    if profile is not None:
        forwarded = ports.published_host(port(profile))
        if forwarded:
            return forwarded
    endpoint = _transports().direct_endpoint() or {}
    return str(endpoint.get('host') or '')


def reachable(profile):
    """``(ok, reason)`` — could a client outside really dial this protocol?

    A VPS owns its ports; Railway only forwards TCP, and only after an admin
    creates a TCP proxy per port; Render, Heroku and Vercel forward nothing. A
    protocol that cannot be reached is never published — the panel shows which of
    the two is missing instead of handing out a link nothing answers on.
    """
    if profile['needs'] == 'udp' and not runtime.has_udp():
        return False, 'این پلتفرم پورت UDP نمی‌دهد؛ برای این پروتکل یک VPS لازم است (یا NEXUS_UDP=1)'
    if profile['needs'] == 'tcp' and not runtime.has_tcp() and not ports.proxied(port(profile)):
        return False, 'این پلتفرم پورت خام نمی‌دهد؛ روی Railway یک TCP Proxy بسازید'
    if not host(profile):
        return False, 'آدرس عمومی پیدا نشد (روی Railway یک TCP Proxy بسازید یا direct_host را ست کنید)'
    return True, ''


def published_profiles():
    """``None`` while nothing was ever started, else the profile ids really up."""
    return None if _published is None else set(_published)


def is_published(profile_id):
    """Whether a hosted protocol may appear in a subscription right now."""
    item = profiles.profile(profile_id)
    if not item:
        return False
    if not enabled(item):
        return False
    if not reachable(item)[0]:
        return False
    live = _published
    return live is None or item['id'] in live


# ------------------------------------------------------------------------ users
def users_for(profile):
    """One credential per active user, exactly like the Xray inbounds.

    ``(username, uuid)`` pairs: the panel gives every user one credential for
    every protocol, so an AnyTLS or TUIC link needs no second secret and
    disabling a user revokes these nodes with all the others.
    """
    transports = _transports()
    try:
        active = rows('SELECT username,uuid,protocol,is_active FROM users WHERE is_active=1')
    except Exception:
        active = []
    return [(str(item.get('username')), str(item.get('uuid')))
            for item in active if profile['protocol'] in transports.user_protocols(item)]


def tls_material():
    """The certificate the hosted protocols present, minted once and kept.

    Regenerating it on every reload would invalidate every link a client already
    pinned, and these protocols do their own TLS, so the pair has to outlive the
    process the same way the Reality key pair and the Shadowsocks PSKs do.
    """
    cert = _setting('core_tls_cert')
    key = _setting('core_tls_key')
    sni = str(_setting('core_sni') or settings.core_sni).strip() or settings.core_sni
    if cert and key:
        return {'cert': cert, 'key': key, 'sni': sni}
    try:
        material = engines.certificate(sni)
    except Exception:
        return None
    _store('core_tls_cert', material['cert'])
    _store('core_tls_key', material['key'])
    return material


# ----------------------------------------------------------------------- render
def listeners(engine):
    """The listener list handed to the renderer for one engine."""
    material = tls_material()
    if not material:
        return []
    out = []
    for item in chosen():
        if not item['enabled'] or item['engine'] != engine:
            continue
        users = users_for(item)
        if not users:
            # Nothing to authenticate: a listener with an empty user list would
            # accept nobody, and publishing it would be a link that cannot work.
            continue
        out.append({'profile': item, 'port': item['port'], 'users': users, **material})
    return out


# -------------------------------------------------------------------- lifecycle
async def _validate(engine, path):
    try:
        proc = await asyncio.create_subprocess_exec(
            *engines.validate_argv(engine, path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
    except Exception as exc:
        return f'{engines.label(engine)} اجرا نشد ({type(exc).__name__})'
    if proc.returncode != 0:
        return ((err or out) or b'').decode(errors='ignore').strip()[-600:] or 'invalid config'
    return None


async def _stop(engine):
    proc = _procs.pop(engine, None)
    if not proc or proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


async def _write(engine, text):
    path = engines.config_path(engine)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    os.makedirs(engines.home(engine), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)
    return path


async def start_or_reload(engine, force=False):
    """Bring one engine to the state its enabled protocols ask for."""
    state = _state.setdefault(engine, {'profiles': [], 'error': '', 'warning': ''})
    if not settings.cores_enabled or not engines.available(engine):
        await _stop(engine)
        state.update({'profiles': [], 'error': '' if settings.cores_enabled else 'غیرفعال است',
                      'warning': '' if engines.available(engine)
                      else f'باینری {engines.label(engine)} نصب نیست'})
        return {'running': False, 'profiles': [], 'reason': state['warning'] or state['error']}
    wanted = listeners(engine)
    if not wanted:
        await _stop(engine)
        state.update({'profiles': [], 'error': '', 'warning': ''})
        return {'running': False, 'profiles': [], 'reason': None}
    text = engines.config_text(engine, wanted, api=settings.mihomo_api)
    digest = hashlib.sha256(text.encode()).hexdigest()
    current = _procs.get(engine)
    ids = [item['profile']['id'] for item in wanted]
    if current and current.returncode is None and _hashes.get(engine) == digest and not force:
        state.update({'profiles': ids, 'error': ''})
        return {'running': True, 'pid': current.pid, 'profiles': ids, 'reloaded': False}
    path = await _write(engine, text)
    problem = await _validate(engine, path)
    if problem:
        # A config the engine refuses must not cost the listener that is already
        # serving: the failure is reported and the old process keeps running.
        state.update({'error': problem})
        return {'running': bool(current and current.returncode is None),
                'profiles': state.get('profiles') or [], 'reason': problem}
    await _stop(engine)
    proc = await asyncio.create_subprocess_exec(
        *engines.run_argv(engine, path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _procs[engine] = proc
    _hashes[engine] = digest
    await asyncio.sleep(0.3)
    if proc.returncode is not None:
        detail = (await proc.stderr.read()).decode(errors='ignore').strip()[-400:]
        state.update({'profiles': [], 'error': detail or f'{engines.label(engine)} بلافاصله بسته شد'})
        return {'running': False, 'profiles': [], 'reason': state['error']}
    state.update({'profiles': ids, 'error': '', 'warning': ''})
    return {'running': True, 'pid': proc.pid, 'profiles': ids, 'reloaded': True}


def _refresh():
    """Recompute what is published: running engine **and** a reachable port."""
    global _published
    live = set()
    for engine, proc in _procs.items():
        if proc and proc.returncode is None:
            live.update(_state.get(engine, {}).get('profiles') or [])
    _published = {profile_id for profile_id in live
                  if (profiles.profile(profile_id) and reachable(profiles.profile(profile_id))[0])}


async def sync(force=False):
    """Start, reload or stop every engine, then publish what is really up."""
    wanted = {item['engine'] for item in chosen() if item['enabled']}
    results = {}
    for engine in profiles.ENGINES:
        if engine in wanted:
            results[engine] = await start_or_reload(engine, force=force)
        else:
            await _stop(engine)
            _state.setdefault(engine, {'profiles': [], 'error': '', 'warning': ''})
            _state[engine].update({'profiles': []})
            results[engine] = {'running': False, 'profiles': [], 'reason': None}
    _refresh()
    return results


async def loop():
    while True:
        try:
            await sync()
        except Exception:
            pass
        await asyncio.sleep(max(5, settings.cores_sync_interval))


async def stop_all():
    for engine in list(_procs):
        await _stop(engine)


# ----------------------------------------------------------------------- status
def status():
    """The panel's view: each engine, each protocol, and which one is published."""
    live = _published
    catalog = []
    for item in chosen():
        ok, reason = reachable(item)
        running = bool(_procs.get(item['engine']) and _procs[item['engine']].returncode is None)
        hosted = item['id'] in (_state.get(item['engine'], {}).get('profiles') or [])
        served = running and hosted
        if not reason and item['enabled'] and not served:
            # Reachable and switched on, yet not served — the engine is missing,
            # refused the config or died. The panel prints this line under the
            # row, and a silent «منتشر نشده» is the one thing an admin cannot
            # act on.
            engine_state = _state.get(item['engine'], {})
            reason = engine_state.get('error') or engine_state.get('warning') or ''
        catalog.append({
            'id': item['id'], 'protocol': item['protocol'], 'tag': item['tag'], 'note': item['note'],
            'needs': item['needs'], 'port': item['port'], 'engine': item['engine'],
            'enabled': item['enabled'], 'reachable': ok, 'reason': reason,
            'running': served,
            'published': is_published(item['id']), 'unknown': live is None,
        })
    return {
        'enabled': bool(settings.cores_enabled),
        'host': host(), 'port_host': host(),
        'tcp': runtime.has_tcp(), 'udp': runtime.has_udp(),
        'sni': str(_setting('core_sni') or settings.core_sni),
        'catalog': catalog,
        'engines': [{
            'id': engine, 'label': engines.label(engine),
            'binary': engines.binary(engine), 'installed': engines.available(engine),
            'running': bool(_procs.get(engine) and _procs[engine].returncode is None),
            'pid': _procs[engine].pid if _procs.get(engine) and _procs[engine].returncode is None else None,
            'profiles': list(_state.get(engine, {}).get('profiles') or []),
            'error': _state.get(engine, {}).get('error') or '',
        } for engine in profiles.ENGINES],
        'published': sorted(live) if live is not None else None,
        'counts': {
            'enabled': sum(1 for item in catalog if item['enabled']),
            'published': sum(1 for item in catalog if item['published']),
            'engines': sum(1 for engine in profiles.ENGINES
                           if _procs.get(engine) and _procs[engine].returncode is None),
        },
        'notes': _notes(),
    }


def _notes():
    """One line per reason a hosted protocol cannot be published here."""
    from app.runtime import PLATFORMS, platform
    mode = PLATFORMS.get(platform(), PLATFORMS['local'])
    notes = []
    if not runtime.has_tcp():
        notes.append('این پلتفرم پورت خام نمی‌دهد؛ AnyTLS فقط با یک TCP Proxy (Railway) یا روی VPS منتشر می‌شود.')
    if not runtime.has_udp():
        notes.append('پورت UDP در دسترس نیست؛ TUIC روی VPS یا سرور خودتان منتشر می‌شود.')
    if mode['tcp'] == 'proxy':
        notes.append('روی Railway برای هر پورت یک TCP Proxy جدا بسازید و پورت عمومی آن را در همین کارت وارد کنید.')
    return notes


def catalog():
    """The panel's one payload: the live status, plus the raw profile list.

    Flat on purpose (:func:`status` is spread in rather than nested): the card
    reads ``catalog``, ``engines``, ``host``, ``sni`` and ``notes`` off the same
    object it stores, and a nested copy is how a save ends up sending an empty
    profile map because the ids it needed were one level down.
    """
    return {**status(),
            'profiles': [{'id': item['id'], 'protocol': item['protocol'], 'tag': item['tag'],
                          'network': item['network'], 'security': item['security'],
                          'needs': item['needs'], 'engine': item['engine'], 'enabled': item['enabled'],
                          'port': item['port']} for item in chosen()]}


def save(updates):
    """Store the switches, ports and engine choices. Returns the changed keys.

    Validation lives here (not in the route) so an unusable port or an unknown
    engine is a readable error instead of a listener that silently never starts.
    The payload is checked *whole* before anything is written: the route answers
    400 without reconciling the engines, so a half-applied save would leave the
    switch reading «روشن» in the database while nothing was ever restarted.
    """
    plan = []
    for item in profiles.PROFILES:
        payload = updates.get(item['id'])
        if not isinstance(payload, dict):
            continue
        if 'enabled' in payload:
            value = str(payload['enabled']).strip().lower()
            plan.append((profiles.enabled_key(item), '0' if value in ('0', 'false', 'off', 'no') else '1'))
        if 'port' in payload:
            try:
                number = int(float(str(payload['port']).strip()))
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f'پورت {item["tag"]} باید یک عدد باشد')
            if not 1 <= number <= 65535:
                raise ValueError(f'پورت {item["tag"]} باید بین ۱ و ۶۵۵۳۵ باشد')
            plan.append((profiles.port_key(item), str(number)))
        if 'engine' in payload:
            engine = str(payload['engine']).strip().lower()
            if engine not in profiles.ENGINES:
                raise ValueError('موتور انتخاب‌شده پشتیبانی نمی‌شود')
            plan.append((profiles.engine_key(item), engine))
    sni = updates.get('sni')
    if isinstance(sni, str) and sni.strip():
        plan.append(('core_sni', sni.strip()[:80]))
    for key, value in plan:
        _store(key, value)
    return [key for key, _ in plan]
