"""The MTProto proxy: Telegram's own proxy protocol, served by ``mtg``.

A VPN subscription and a Telegram proxy are two different products. A user who
only wants Telegram to work opens the app, pastes one ``tg://proxy`` link and is
done — nothing to install, nothing to import. MTProto is the protocol behind that
link, and the implementation that speaks the *current* (FakeTLS) handshake is
`mtg <https://github.com/9seconds/mtg>`_, so the pinned binary ships inside the
image next to sing-box and mihomo (see the ``Dockerfile``).

What the panel owns here is the honesty it applies everywhere else: the link is
published only when the binary is installed, the listener is really up **and** the
public port is reachable from outside (a VPS owns its ports; on Railway every
port needs its own TCP proxy, and Render forwards nothing). Otherwise the card
names which of the three is missing instead of handing a user a link that answers
nothing.

Two details that are easy to get wrong and are pinned by tests:

* **The secret is generated here**, not shelled out to ``mtg generate-secret``:
  the FakeTLS secret is ``ee`` + 16 random bytes + the hex of the fronting
  hostname, the value has to stay stable across restarts (a client that already
  added the proxy would lose it), and a fronting name the admin changes reissues
  the secret because the name is *inside* it.
* **``mtg access`` validates the config**, never ``mtg doctor``: doctor performs
  live network checks (it insists the fronting name resolve to this server), which
  is useful advice but not a reason to refuse a working proxy. ``access -i`` is an
  offline parse of exactly the file the process is about to run.
"""
import asyncio
import hashlib
import os
import secrets
import urllib.parse

from app import runtime
from app.config import settings
from app.db import execute, row

# The settings keys this feature owns. The secret lives in the database like the
# Reality key pair and the Shadowsocks PSKs, so a redeploy does not reissue every
# link a user already added.
ENABLED = 'tg_mtproto_enabled'
PORT = 'tg_mtproto_port'
SECRET = 'tg_mtproto_secret'
DOMAIN = 'tg_mtproto_domain'
CONCURRENCY = 'tg_mtproto_concurrency'
DNS = 'tg_mtproto_dns'
FRONT_IP = 'tg_mtproto_front_ip'

# ``ee`` + 16 random bytes (32 hex chars), then the hex of the domain.
FAKETLS_PREFIX = 'ee'
FAKETLS_SECRET_HEX = 32

_proc = None
_hash = None
_state = {'profiles': [], 'error': '', 'running': False}


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


def enabled():
    """Whether the admin switched the MTProto proxy on (off by default)."""
    return (_setting(ENABLED) or '0') == '1'


def binary():
    return settings.mtg_binary


def available():
    """Whether the ``mtg`` binary is really installed in this image."""
    return os.path.exists(binary())


def config_path():
    return settings.mtg_config


def domain():
    """The FakeTLS fronting hostname (also the SNI a probe sees)."""
    raw = str(_setting(DOMAIN) or settings.telegram_mtproto_domain).strip().lower()
    return raw or settings.telegram_mtproto_domain


def dns():
    return str(_setting(DNS) or settings.telegram_mtproto_dns).strip() or settings.telegram_mtproto_dns


def front_ip():
    """Optional fronting IP: used when the name cannot be resolved locally."""
    return str(_setting(FRONT_IP) or '').strip()


def concurrency():
    raw = _setting(CONCURRENCY)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return value if value >= 1 else int(settings.telegram_mtproto_concurrency)


def port():
    raw = _setting(PORT)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return value if 1 <= value <= 65535 else int(settings.telegram_mtproto_port)


# --------------------------------------------------------------------- secret
def embedded_domain(secret):
    """The fronting name a FakeTLS secret carries ('' when it is not one)."""
    raw = str(secret or '').strip().lower()
    if not raw.startswith(FAKETLS_PREFIX) or len(raw) <= 2 + FAKETLS_SECRET_HEX:
        return ''
    try:
        return bytes.fromhex(raw[2 + FAKETLS_SECRET_HEX:]).decode()
    except (ValueError, UnicodeDecodeError):
        return ''


def generate_secret(hostname):
    """A FakeTLS secret for one fronting name, in the format clients expect."""
    return FAKETLS_PREFIX + secrets.token_bytes(16).hex() + str(hostname).encode().hex()


def secret(create=True, hostname=None):
    """The live secret, reissued when the fronting name it carries changed."""
    name = str(hostname or domain()).strip().lower()
    current = str(_setting(SECRET) or '').strip()
    if current and embedded_domain(current) == name and name:
        return current
    if not create:
        return current
    value = generate_secret(name)
    _store(SECRET, value)
    return value


def rotate_secret():
    """Issue a new secret — every link handed out before it stops working."""
    value = generate_secret(domain())
    _store(SECRET, value)
    return value


# ------------------------------------------------------------------ publishing
def host():
    """Where a client outside can dial this listener, or ``''``."""
    from app.subscriptions import transports
    endpoint = transports.direct_endpoint() or {}
    return str(endpoint.get('host') or '')


def reachable():
    """``(ok, reason)`` — could a Telegram client outside really connect here?"""
    if not available():
        return False, f'باینری mtg نصب نیست ({binary()})'
    if not runtime.has_tcp():
        return False, 'این پلتفرم پورت خام نمی‌دهد؛ برای پروکسی MTProto یک VPS لازم است (یا روی Railway یک TCP Proxy بسازید)'
    if not host():
        return False, 'آدرس عمومی پیدا نشد (روی Railway یک TCP Proxy بسازید یا direct_host را ست کنید)'
    return True, ''


def published():
    """Whether the ``tg://`` link may be handed to users right now."""
    if not enabled():
        return False
    if not reachable()[0]:
        return False
    return bool(_proc and _proc.returncode is None)


def links():
    """The ``tg://`` and ``t.me`` links, or ``None`` while nothing is published.

    No link is ever built for a listener that is not running: a user who adds a
    proxy from here has no way to tell a dead link from a working one, and the
    card shows the reason instead (see :func:`status`).
    """
    if not published():
        return None
    server, number, value = host(), port(), secret()
    if not (server and number and value):
        return None
    query = urllib.parse.urlencode({'server': server, 'port': number, 'secret': value})
    return {
        'server': server, 'port': number, 'secret': value,
        'tg': 'tg://proxy?' + query,
        'tme': 'https://t.me/proxy?' + query,
    }


# --------------------------------------------------------------------- config
def config_text():
    """The ``mtg`` config.

    Every block is spelled out on purpose: the defaults are fine, but an admin
    reading this card has to be able to see *what it will not do* — no remote
    blocklist download at boot (a deploy must not look like a scanner) and no
    Prometheus listener bound inside the container.
    """
    lines = [
        '# NEXUS-generated mtg config — see app/telegram/mtproto.py',
        'bind-to = "0.0.0.0:%d"' % port(),
        'concurrency = %d' % concurrency(),
        'secret = "%s"' % secret(),
        '',
        '[network]',
        'dns = "%s"' % dns(),
        '',
        '[domain-fronting]',
    ]
    if front_ip():
        lines.append('ip = "%s"' % front_ip())
    else:
        lines.append('# ip = "1.2.3.4"   # ست کنید اگر دامنهٔ fronting روی این سرور DNS نمی‌شود')
    lines += [
        '',
        '[defense.anti-replay]',
        'enabled = true',
        'max-size = "1mib"',
        'error-rate = 0.001',
        '',
        '[defense.blocklist]',
        'enabled = false',
        '',
        '[defense.doppelganger]',
        'urls = []',
        '',
        '[stats.prometheus]',
        'enabled = false',
        '',
    ]
    return '\n'.join(lines)


def validate_argv(path):
    """How to make ``mtg`` itself parse a rendered config, without any network.

    ``access`` builds the access information for one address; ``-i 127.0.0.1``
    gives it a public IP to work with, so it never discovers one over the
    internet. A config ``mtg`` refuses exits non-zero with the reason on stderr.
    """
    return [binary(), 'access', '-i', '127.0.0.1', '-x', path]


def run_argv(path):
    return [binary(), 'run', path]


# ------------------------------------------------------------------ lifecycle
def _write(text):
    path = config_path()
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)
    return path


async def _validate(path):
    try:
        proc = await asyncio.create_subprocess_exec(
            *validate_argv(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
    except Exception as exc:
        return f'mtg اجرا نشد ({type(exc).__name__})'
    if proc.returncode != 0:
        return ((err or out) or b'').decode(errors='ignore').strip()[-400:] or 'invalid config'
    return None


async def _stop():
    global _proc
    proc = _proc
    if not proc or proc.returncode is not None:
        _proc = None
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
    _proc = None


async def start_or_reload(force=False):
    """Run the proxy, reload it when its config changed, stop it when switched off."""
    global _proc, _hash
    if not settings.cores_enabled or not available():
        await _stop()
        _state.update({'running': False, 'error': '' if settings.cores_enabled else 'غیرفعال است',
                       'warning': '' if available() else f'باینری mtg نصب نیست ({binary()})'})
        return {'running': False, 'reason': _state.get('warning') or None}
    if not enabled():
        await _stop()
        _state.update({'running': False, 'error': '', 'warning': ''})
        return {'running': False, 'reason': None}
    ok, reason = reachable()
    if not ok:
        # Enabled but unreachable: running a listener nothing can dial would be a
        # process for its own sake. The reason is kept for the card.
        await _stop()
        _state.update({'running': False, 'error': '', 'warning': reason})
        return {'running': False, 'reason': reason}
    text = config_text()
    digest = hashlib.sha256(text.encode()).hexdigest()
    if _proc and _proc.returncode is None and _hash == digest and not force:
        _state.update({'running': True, 'error': ''})
        return {'running': True, 'pid': _proc.pid, 'reloaded': False}
    path = _write(text)
    problem = await _validate(path)
    if problem:
        _state.update({'error': problem, 'running': bool(_proc and _proc.returncode is None)})
        return {'running': bool(_proc and _proc.returncode is None), 'reason': problem}
    await _stop()
    try:
        _proc = await asyncio.create_subprocess_exec(
            *run_argv(path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    except Exception as exc:
        _state.update({'running': False, 'error': f'mtg اجرا نشد ({type(exc).__name__})'})
        return {'running': False, 'reason': _state['error']}
    _hash = digest
    await asyncio.sleep(0.3)
    if _proc.returncode is not None:
        detail = (await _proc.stderr.read()).decode(errors='ignore').strip()[-400:]
        _state.update({'running': False, 'error': detail or 'mtg بلافاصله بسته شد'})
        return {'running': False, 'reason': _state['error']}
    _state.update({'running': True, 'error': ''})
    return {'running': True, 'pid': _proc.pid, 'reloaded': True}


async def sync(force=False):
    if not settings.cores_enabled:
        await _stop()
        _state.update({'running': False})
        return {'running': False, 'reason': 'غیرفعال است'}
    return await start_or_reload(force=force)


async def loop():
    while True:
        try:
            await sync()
        except Exception:
            pass
        await asyncio.sleep(max(5, settings.telegram_sync_interval))


async def stop_all():
    await _stop()


# ---------------------------------------------------------------------- status
def status():
    """What the panel card reads: the switch, the listener, the link and why not."""
    ok, reason = reachable()
    if not reason and enabled() and not (_proc and _proc.returncode is None):
        reason = _state.get('error') or _state.get('warning') or ''
    return {
        'enabled': enabled(),
        'installed': available(),
        'binary': binary(),
        'running': bool(_proc and _proc.returncode is None),
        'pid': _proc.pid if _proc and _proc.returncode is None else None,
        'reachable': ok,
        'reason': reason,
        'published': published(),
        'host': host(),
        'port': port(),
        'secret': secret() or '',
        'domain': domain(),
        'dns': dns(),
        'front_ip': front_ip(),
        'concurrency': concurrency(),
        'links': links(),
        'reload': {'running': _state.get('running', False), 'error': _state.get('error') or '',
                   'warning': _state.get('warning') or ''},
    }


def save(updates):
    """Store the switch, port and fronting values. Returns the changed keys.

    Validation lives here so a half-applied save cannot leave the card reading
    «روشن» while nothing was ever restarted: an unusable port or a fronting name
    that is not a hostname is a readable error before anything is written.
    """
    plan = []
    if 'enabled' in updates:
        value = str(updates['enabled']).strip().lower()
        plan.append((ENABLED, '0' if value in ('0', 'false', 'off', 'no') else '1'))
    if 'port' in updates:
        try:
            number = int(float(str(updates['port']).strip()))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('پورت پروکسی MTProto باید یک عدد باشد')
        if not 1 <= number <= 65535:
            raise ValueError('پورت پروکسی MTProto باید بین ۱ و ۶۵۵۳۵ باشد')
        plan.append((PORT, str(number)))
    if 'concurrency' in updates:
        try:
            value = int(float(str(updates['concurrency']).strip()))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('تعداد اتصال همزمان باید یک عدد باشد')
        if not 1 <= value <= 100000:
            raise ValueError('تعداد اتصال همزمان باید بین ۱ و ۱۰۰۰۰۰ باشد')
        plan.append((CONCURRENCY, str(value)))
    if 'domain' in updates:
        name = str(updates['domain'] or '').strip().lower()
        if not name or '.' not in name or ' ' in name or '/' in name:
            raise ValueError('دامنهٔ fronting معتبر نیست (مثال: www.cloudflare.com)')
        plan.append((DOMAIN, name))
    if 'dns' in updates:
        value = str(updates['dns'] or '').strip()
        if value and not value.startswith(('https://', 'tls://', 'http://', 'udp://')) and value.count('.') != 3:
            raise ValueError('DoH باید آدرس https:// یا tls:// یا یک آی‌پی باشد')
        plan.append((DNS, value or settings.telegram_mtproto_dns))
    if 'front_ip' in updates:
        value = str(updates['front_ip'] or '').strip()
        if value and not runtime.is_public_address(value):
            raise ValueError('آی‌پی fronting باید یک IPv4 عمومی باشد')
        plan.append((FRONT_IP, value))
    for key, value in plan:
        _store(key, value)
    # The fronting name is *inside* the secret, so changing it reissues the
    # secret — and with it the link — instead of leaving a mismatch mtg refuses.
    secret(create=True)
    return [key for key, _ in plan]
