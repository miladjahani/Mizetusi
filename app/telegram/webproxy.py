"""The HTTP/SOCKS5 web proxy Telegram Desktop and a browser can use.

Telegram Desktop's «Use custom proxy» takes an ordinary HTTP or SOCKS5 proxy with
a username and password, and so does every browser. That is a *different* product
from the VPN subscription: it is one host:port plus credentials, not a config
list, and Telegram's own traffic (and nothing else) is what the user wants to
route through it.

Xray — the engine this deployment already runs — serves both inbound types with a
**real account list**, so the panel does not need a second binary for this. One
credential per active user is inserted into the same config that carries the
transports, which means:

* a user who is disabled loses their proxy line with every other link;
* nobody shares a password, so one leaked line can be revoked on its own;
* the listener is a public TCP port, so it is published only where a raw port is
  reachable (a VPS owns its ports; on Railway each port needs its own TCP proxy)
  and the card names what is missing otherwise.

The inbounds are ``http`` and ``socks`` with ``auth: password``; the account user
is the panel username and the password is the user's one credential (their uuid),
exactly like the AnyTLS/TUIC listeners — a second secret would be one more thing
to keep in sync.
"""
import urllib.parse

from app import ports, runtime
from app.config import settings
from app.db import execute, row, rows

HTTP = 'http'
SOCKS = 'socks'

# Two profiles, one listener each: an HTTP proxy and a SOCKS5 one. They are kept
# apart rather than merged into one «mixed» port because neither Xray inbound
# speaks both, and a client that wants one should not have to guess.
PROFILES = (
    {
        'id': 'web-http', 'protocol': HTTP, 'tag': 'وب‌پروکسی HTTP', 'label': 'HTTP',
        'needs': 'tcp', 'engine': 'xray', 'port_key': 'tg_web_http_port',
        'enabled_key': 'tg_web_http_enabled', 'default_port': 'telegram_http_port',
        'scheme': 'http', 'note': 'همان «Custom Proxy» تلگرام دسکتاپ با نوع HTTP؛ در مرورگر هم کار می‌کند.',
    },
    {
        'id': 'web-socks', 'protocol': SOCKS, 'tag': 'وب‌پروکسی SOCKS5', 'label': 'SOCKS5',
        'needs': 'tcp', 'engine': 'xray', 'port_key': 'tg_web_socks_port',
        'enabled_key': 'tg_web_socks_enabled', 'default_port': 'telegram_socks_port',
        'scheme': 'socks5', 'note': 'SOCKS5 با احراز هویت؛ برای تلگرام دسکتاپ و هر برنامه‌ای که SOCKS5 می‌شناسد.',
    },
)

PROFILE_IDS = tuple(item['id'] for item in PROFILES)
BY_ID = {item['id']: item for item in PROFILES}

# The Xray inbound tags. They are stable strings rather than profile ids because
# the Xray config, the panel's truth view and the tests all read them.
TAG = {'web-http': 'tg-http', 'web-socks': 'tg-socks'}


def profile(profile_id):
    return BY_ID.get(str(profile_id or '').strip().lower())


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


def enabled(item):
    """Whether an admin switched this proxy on (off by default)."""
    return (_setting(item['enabled_key']) or '0') == '1'


def port(item):
    """The public port this proxy listens on."""
    raw = _setting(item['port_key'])
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return value if 1 <= value <= 65535 else int(getattr(settings, item['default_port']))


def listen_port(item):
    """The port this proxy binds — for a raw profile *or* one from :func:`chosen`.

    ``port()`` resolves a raw catalog entry through its settings key, while an
    entry from :func:`chosen` already carries the resolved number. Both shapes
    travel around this module (the tests hand a raw profile to :func:`reachable`),
    so the one place that answers «which port» accepts either.
    """
    try:
        value = int(item.get('port'))
    except (TypeError, ValueError):
        value = 0
    return value if 1 <= value <= 65535 else port(item)


def chosen():
    """Both proxies with their resolved settings, in catalog order.

    ``port`` is what Xray binds inside this container; ``published_port`` is what a
    user has to dial. They differ on Railway, where a TCP proxy forwards an
    arbitrary public port to the container's — and only the forwarded one belongs
    in a line handed to a user (app/ports.py).
    """
    out = []
    for item in PROFILES:
        entry = dict(item)
        listen = listen_port(item)
        entry.update({'enabled': enabled(item), 'port': listen,
                      'published_port': ports.published_port(listen, listen)})
        out.append(entry)
    return out


# ------------------------------------------------------------------ reachability
def host(item=None):
    """Where these listeners are reachable from outside, or ``''``.

    With an ``item`` the TCP proxy made for that listener's own port wins: Railway
    gives each forwarded port its own name, and a line has to name the one that
    really forwards it.
    """
    forwarded = ports.published_host(listen_port(item)) if item else ''
    if forwarded:
        return forwarded
    from app.subscriptions import transports
    endpoint = transports.direct_endpoint() or {}
    return str(endpoint.get('host') or '')


def reachable(item):
    """``(ok, reason)`` — could a client outside really dial this proxy?"""
    if not runtime.has_tcp() and not ports.proxied(listen_port(item)):
        return False, 'این پلتفرم پورت خام نمی‌دهد؛ وب‌پروکسی روی VPS یا با یک TCP Proxy (Railway) منتشر می‌شود'
    if not host(item):
        return False, 'آدرس عمومی پیدا نشد (روی Railway یک TCP Proxy بسازید یا direct_host را ست کنید)'
    return True, ''


def served_tags():
    """Which inbound tags the running Xray config really contains (``None`` = unknown)."""
    from app import xray
    return xray.served_tags()


def running(item):
    """Whether this proxy's inbound is in the config the engine is running."""
    tags = served_tags()
    if tags is None:
        # No engine was ever started here (a fresh process, a test): the enabled
        # matrix is what the links are built from, exactly like the transports.
        return True
    return TAG[item['id']] in tags


def published(item):
    """Whether the proxy line may be handed to a user right now."""
    if not enabled(item):
        return False
    if not reachable(item)[0]:
        return False
    return running(item)


# ------------------------------------------------------------------------ users
def users():
    """One account per active user: ``(username, password)``.

    The password is the user's single credential (their uuid) — the same value the
    VPN inbounds authenticate — so revoking a user revokes their proxy with it.
    """
    try:
        active = rows('SELECT username,uuid FROM users WHERE is_active=1 ORDER BY username')
    except Exception:
        active = []
    return [(str(item.get('username') or ''), str(item.get('uuid') or ''))
            for item in active if item.get('username') and item.get('uuid')]


def accounts():
    """The Xray account list (user/pass pairs) for one inbound."""
    return [{'user': name, 'pass': password} for name, password in users()]


# ---------------------------------------------------------------------- xray
def xray_inbounds():
    """The inbounds Xray renders for every published web proxy.

    Mimicking the edge transports: a listener only exists when the admin enabled
    it, the host can expose the port and there is at least one user to
    authenticate — a proxy with an empty account list would accept nobody.
    """
    out = []
    for item in chosen():
        if not item['enabled'] or not reachable(item)[0]:
            continue
        entries = accounts()
        if not entries:
            continue
        if item['protocol'] == HTTP:
            out.append({
                'tag': TAG[item['id']], 'listen': '0.0.0.0', 'port': int(item['port']),
                'protocol': 'http',
                'settings': {'accounts': entries, 'allowTransparent': False},
                'sniffing': {'enabled': True, 'destOverride': ['http', 'tls']},
            })
        else:
            out.append({
                'tag': TAG[item['id']], 'listen': '0.0.0.0', 'port': int(item['port']),
                'protocol': 'socks',
                'settings': {'auth': 'password', 'accounts': entries, 'udp': True, 'ip': '127.0.0.1'},
                'sniffing': {'enabled': True, 'destOverride': ['http', 'tls']},
            })
    return out


# ---------------------------------------------------------------------- links
def line(item, username, password, hostname=None):
    """One ready-to-paste proxy line for one user ('' while unpublished)."""
    address = hostname or host(item)
    if not (address and username and password):
        return ''
    user = urllib.parse.quote(str(username), safe='')
    secret = urllib.parse.quote(str(password), safe='')
    number = int(item.get('published_port') or listen_port(item))
    return f"{item['scheme']}://{user}:{secret}@{address}:{number}"


def lines(username=None, password=None):
    """Every published proxy line, optionally for one user only."""
    out = []
    for item in chosen():
        if not published(item):
            continue
        if username and password:
            out.append({'id': item['id'], 'protocol': item['protocol'], 'label': item['label'],
                        'note': item['note'], 'host': host(item),
                        'port': int(item['published_port']), 'listen_port': int(item['port']),
                        'username': username, 'password': password,
                        'url': line(item, username, password)})
            continue
        for name, secret in users():
            out.append({'id': item['id'], 'protocol': item['protocol'], 'label': item['label'],
                        'note': item['note'], 'host': host(item),
                        'port': int(item['published_port']), 'listen_port': int(item['port']),
                        'username': name, 'password': secret,
                        'url': line(item, name, secret)})
    return out


def status():
    """The panel's view: each proxy, its port and what is published."""
    catalog = []
    for item in chosen():
        ok, reason = reachable(item)
        live = running(item)
        if not reason and item['enabled'] and not live:
            reason = 'اینباند در کانفیگ در حال اجرای Xray نیست'
        catalog.append({
            'id': item['id'], 'protocol': item['protocol'], 'tag': item['tag'],
            'label': item['label'], 'note': item['note'], 'engine': item['engine'],
            'enabled': item['enabled'], 'port': int(item['published_port']),
            'listen_port': int(item['port']),
            'reachable': ok, 'reason': reason, 'running': live, 'published': published(item),
        })
    return {
        'catalog': catalog,
        'host': host(),
        # One row per user, because that is how the card renders it — the account
        # list is what Xray was given, spelled out so an admin can hand a line to
        # one user without picking it out of a table.
        'accounts': [{'username': name, 'uuid': secret} for name, secret in users()],
        'lines': lines(),
        'counts': {'enabled': sum(1 for item in catalog if item['enabled']),
                   'published': sum(1 for item in catalog if item['published']),
                   'accounts': len(users())},
    }


def save(updates):
    """Store the switches and ports; returns the changed keys.

    The payload is validated whole before anything is written, so a 400 never
    leaves the card showing a switch that was never applied.
    """
    plan = []
    for item in PROFILES:
        payload = updates.get(item['id'])
        if not isinstance(payload, dict):
            continue
        if 'enabled' in payload:
            value = str(payload['enabled']).strip().lower()
            plan.append((item['enabled_key'], '0' if value in ('0', 'false', 'off', 'no') else '1'))
        if 'port' in payload:
            try:
                number = int(float(str(payload['port']).strip()))
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f'پورت {item["label"]} باید یک عدد باشد')
            if not 1 <= number <= 65535:
                raise ValueError(f'پورت {item["label"]} باید بین ۱ و ۶۵۵۳۵ باشد')
            plan.append((item['port_key'], str(number)))
    for key, value in plan:
        _store(key, value)
    return [key for key, _ in plan]
