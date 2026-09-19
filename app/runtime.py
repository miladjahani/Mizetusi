"""Where this deployment is actually running.

NEXUS started life on Railway, so the origin hostname, the raw-TCP endpoint and
the database directory were read from ``RAILWAY_*`` variables. Nothing in the
proxy path is Railway-specific — the same image runs on Render, Fly.io, Koyeb,
Heroku or a plain VPS — so this module answers the three questions the rest of
the app asks, for every platform:

* **public host** — the hostname clients reach the panel on. Every PaaS injects
  its own variable, so they are all tried, plus the generic overrides
  ``NEXUS_PUBLIC_DOMAIN`` / ``PUBLIC_BASE_URL``.
* **raw TCP endpoint** — needed by the Reality transport (one public TCP port).
  A VPS always has one, Railway only after the admin adds a TCP proxy, Render and
  Heroku never do. ``direct()`` therefore returns ``None`` when it has nothing
  truthful to publish; a link is never advertised before its listener exists.
* **data directory** — ``/data`` is a mounted volume on Railway/Render-with-disk
  but absent on most VPS deployments and on Heroku, so a writable alternative is
  chosen instead of crashing at boot.
"""

import ipaddress
import os
import socket
import urllib.parse

# ``tcp`` says how the platform can expose a raw TCP port:
#   always -> ports are free (VPS, Docker, Fly)
#   proxy  -> only when a TCP proxy/port was provisioned (Railway)
#   none   -> HTTP(S) only (Render without a TCP port, Heroku, Vercel)
PLATFORMS = {
    'railway': {'label': 'Railway', 'tcp': 'proxy'},
    'render': {'label': 'Render', 'tcp': 'none'},
    'fly': {'label': 'Fly.io', 'tcp': 'always'},
    'koyeb': {'label': 'Koyeb', 'tcp': 'none'},
    'heroku': {'label': 'Heroku', 'tcp': 'none'},
    'vercel': {'label': 'Vercel', 'tcp': 'none'},
    'replit': {'label': 'Replit', 'tcp': 'none'},
    'docker': {'label': 'VPS / Docker', 'tcp': 'always'},
    'local': {'label': 'Local', 'tcp': 'always'},
}

# Platform markers, checked in order. The value is what ``platform()`` returns.
PLATFORM_MARKERS = (
    ('railway', ('RAILWAY_ENVIRONMENT_ID', 'RAILWAY_PROJECT_ID', 'RAILWAY_SERVICE_ID', 'RAILWAY_PUBLIC_DOMAIN')),
    ('render', ('RENDER_SERVICE_ID', 'RENDER_EXTERNAL_URL', 'RENDER_EXTERNAL_HOSTNAME', 'RENDER')),
    ('fly', ('FLY_APP_NAME', 'FLY_ALLOC_ID', 'FLY_REGION')),
    ('koyeb', ('KOYEB_APP_NAME', 'KOYEB_SERVICE_NAME', 'KOYEB_PUBLIC_DOMAIN')),
    ('heroku', ('DYNO', 'HEROKU_APP_NAME')),
    ('vercel', ('VERCEL', 'VERCEL_URL')),
    ('replit', ('REPL_ID', 'REPLIT_DEV_DOMAIN')),
)

# Hostnames the platforms inject. Ordered so an explicit operator override wins.
HOST_KEYS = (
    'NEXUS_PUBLIC_DOMAIN', 'PUBLIC_BASE_URL',
    'RAILWAY_PUBLIC_DOMAIN', 'RAILWAY_STATIC_URL',
    'RENDER_EXTERNAL_HOSTNAME', 'RENDER_EXTERNAL_URL',
    'KOYEB_PUBLIC_DOMAIN',
    'HEROKU_APP_DOMAIN',
    'WEBSITE_HOSTNAME',
    'VERCEL_URL',
    'REPLIT_DEV_DOMAIN',
)

DIRECT_HOST_KEYS = ('NEXUS_DIRECT_HOST', 'RAILWAY_TCP_PROXY_DOMAIN')
DIRECT_PORT_KEYS = ('NEXUS_DIRECT_PORT', 'RAILWAY_TCP_PROXY_PORT')

# The port Reality tries to bind/publish when nothing else was configured. 8443
# is free on a VPS and is not one of the WebSocket listener ports.
DEFAULT_DIRECT_PORT = 8443

_public_ip_cache = {'value': None, 'at': 0.0}
_PUBLIC_IP_TTL = 900.0


def _env(*names):
    """First non-empty environment value among ``names``."""
    for name in names:
        value = (os.getenv(name) or '').strip()
        if value:
            return value
    return ''


def platform():
    """The platform id this process runs on (``RAILWAY_PLATFORM`` wins)."""
    override = (os.getenv('NEXUS_PLATFORM') or '').strip().lower()
    if override in PLATFORMS:
        return override
    if override in ('vps', 'self', 'selfhosted', 'self-hosted'):
        return 'docker'
    for pid, keys in PLATFORM_MARKERS:
        if _env(*keys):
            return pid
    if os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv'):
        return 'docker'
    return 'local'


def label():
    return PLATFORMS.get(platform(), PLATFORMS['local'])['label']


def host():
    """The public hostname of this deployment, or ``None``."""
    raw = _env(*HOST_KEYS)
    if not raw:
        app = _env('FLY_APP_NAME')
        raw = f'{app}.fly.dev' if app else ''
    if not raw:
        name = _env('HEROKU_APP_NAME')
        raw = f'{name}.herokuapp.com' if name else ''
    if not raw:
        raw = _env('KOYEB_APP_NAME')  # last resort: the app name itself
    if not raw:
        return None
    if '://' not in raw:
        raw = 'https://' + raw
    return urllib.parse.urlparse(raw).hostname or None


def public_base():
    """The scheme+host of this deployment, or ``None`` when unknown."""
    hostname = host()
    if not hostname:
        return None
    return 'http://localhost' if hostname in ('localhost', '127.0.0.1') else f'https://{hostname}'


def has_tcp():
    """Whether a raw TCP port is available *here* (a Reality prerequisite)."""
    mode = PLATFORMS.get(platform(), PLATFORMS['local'])['tcp']
    if mode == 'always':
        return True
    if mode == 'proxy':
        return bool(_env(*DIRECT_HOST_KEYS) and _env(*DIRECT_PORT_KEYS))
    return bool(_env('NEXUS_DIRECT_HOST') and _env('NEXUS_DIRECT_PORT'))


def is_public_address(value):
    """True for an IPv4 address the internet can actually dial."""
    try:
        address = ipaddress.ip_address(str(value or '').strip())
    except ValueError:
        return False
    return address.version == 4 and address.is_global


def public_ip(timeout=1.5, force=False):
    """Best-effort public IPv4 of this machine (never leaves the host).

    A UDP socket to a public resolver only performs a route lookup — no packet
    is sent — so this costs nothing and reveals the address clients would dial
    a VPS on. Cached, because it cannot change while the process lives.

    A container behind NAT (Docker bridge, a cloud private network) resolves to a
    *private* address, which no client could reach; publishing it would hand users
    a dead Reality link, so only a globally routable address is returned. An
    explicit ``NEXUS_PUBLIC_IP`` is trusted as-is.
    """
    import time
    now = time.time()
    if not force and _public_ip_cache['value'] and now - _public_ip_cache['at'] < _PUBLIC_IP_TTL:
        return _public_ip_cache['value']
    address = _env('NEXUS_PUBLIC_IP') or _env('PUBLIC_IP') or None
    if not address and not os.getenv('NEXUS_NO_PUBLIC_IP'):
        for probe in (('1.1.1.1', 53), ('8.8.8.8', 53)):
            sock = None
            found = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(timeout)
                sock.connect(probe)
                found = sock.getsockname()[0]
            except Exception:
                found = None
            finally:
                if sock is not None:
                    sock.close()
            if found and is_public_address(found):
                address = found
                break
    _public_ip_cache.update({'value': address, 'at': now})
    return address


def direct():
    """Where a raw TCP client can reach this deployment, if anywhere.

    Explicit configuration always wins (``direct_host``/``direct_port`` settings
    are handled by the caller). A VPS needs no configuration at all: the port is
    free, so the machine's own public IP plus :data:`DEFAULT_DIRECT_PORT` is a
    truthful endpoint. Platforms without a raw port return ``None`` rather than
    publishing a node nobody can dial.
    """
    hostname = _env(*DIRECT_HOST_KEYS)
    raw_port = _env(*DIRECT_PORT_KEYS)
    try:
        port = int(raw_port or 0)
    except (TypeError, ValueError):
        port = 0
    mode = PLATFORMS.get(platform(), PLATFORMS['local'])['tcp']
    if hostname and port:
        return {'host': hostname, 'port': port, 'source': 'env'}
    if mode == 'always':
        address = hostname or public_ip()
        # A private address (NAT'd container, cloud private network) would be a
        # link no client can dial, so it is never published automatically.
        if address and (hostname or is_public_address(address)):
            return {'host': address, 'port': port or DEFAULT_DIRECT_PORT, 'source': 'auto'}
    return None


def data_dir():
    """A writable directory for the sqlite file (``/data`` when available)."""
    for candidate in (_env('NEXUS_DATA_DIR'), '/data'):
        if not candidate:
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, '.nexus-write-test')
            with open(probe, 'w', encoding='utf-8') as handle:
                handle.write('ok')
            os.remove(probe)
            return candidate
        except Exception:
            continue
    fallback = os.path.join(os.getcwd(), 'data')
    try:
        os.makedirs(fallback, exist_ok=True)
    except Exception:
        return os.getcwd()
    return fallback


def info():
    """Panel/health view of the runtime — secrets are never included."""
    pid = platform()
    endpoint = direct()
    return {
        'id': pid,
        'label': label(),
        'tcp': PLATFORMS.get(pid, PLATFORMS['local'])['tcp'],
        'has_tcp': has_tcp(),
        'host': host(),
        'public_base': public_base(),
        'direct': endpoint,
        'data_dir': data_dir(),
        'notes': _notes(pid, endpoint),
    }


def _notes(pid, endpoint):
    if pid == 'render':
        return ['Render فقط HTTP/HTTPS می‌دهد؛ مسیرهای WebSocket کار می‌کنند و برای Reality یک پورت TCP لازم است.']
    if pid == 'railway':
        return (['پورت TCP پروکسی فعال است؛ Reality منتشر می‌شود.'] if endpoint
                else ['برای Reality در Railway یک TCP Proxy بسازید (یا NEXUS_DIRECT_HOST/PORT را ست کنید).'])
    if pid in ('docker', 'local'):
        return (['پورت %s برای Reality در دسترس است.' % endpoint['port']] if endpoint
                else ['آی‌پی عمومی قابل‌اتکا پیدا نشد (شاید پشت NAT باشید)؛ NEXUS_DIRECT_HOST و '
                      'NEXUS_DIRECT_PORT را ست کنید یا یک پورت TCP را به این سرور فوروارد کنید.'])
    return ['مسیرهای WebSocket فعال‌اند؛ برای Reality یک پورت TCP لازم است.']
