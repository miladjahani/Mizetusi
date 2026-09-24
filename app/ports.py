"""Which public port each raw-TCP capability is really published on.

A listener inside this container binds a fixed port: Reality on 8443, AnyTLS on
8444, MTProto on 8446, the HTTP and SOCKS5 web proxies on 8448/8449. On a host
that owns its ports — a VPS, Docker, Fly — that number *is* the public port, and
nothing here matters.

On Railway it is not. Railway only forwards TCP through a **TCP proxy**, it
allocates that proxy's port at random, and the number it allocates is the one a
client has to dial. So every card here needs two different ports and only one of
them may end up in a link:

===================  =========================  =====================
                     listen (bind)              published (dial)
===================  =========================  =====================
VPS / Docker         ``8443``                   ``8443``
Railway (proxy)      ``8443``                   ``23177`` (random)
===================  =========================  =====================

This module is the one place that answers the second column. :mod:`app.railway`
creates the proxies and :mod:`app.autoconfig` calls it once at deploy time; the
resulting mapping is stored in the settings table as JSON, so a redeploy (or a
restart by the platform) keeps publishing the same ports the clients already
have. Every lookup falls back to the listen port, which is what makes a VPS and
a Railway deployment share exactly one code path.

Nothing here ever invents a port: when no proxy exists for a capability, the
module says so and the card falls back to the listen port *and* its reachability
check keeps saying why.
"""
import json

from app.config import settings
from app.db import execute, row

# One settings row, holding ``{application_port: {id, host, port, sync, at}}``.
STORE = 'tcp_public_ports'
# The marker the deploy-time pass writes, so a redeploy does not re-create the
# proxies that already exist (and does not restart the service again).
DONE = 'tcp_proxies_done'
LOG = 'tcp_proxies_log'


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


def load():
    """The stored mapping, ``{application port (int): endpoint (dict)}``.

    A malformed row yields ``{}`` rather than an exception: this is read on every
    subscription build, and a broken JSON blob must never be able to empty a
    user's subscription.
    """
    try:
        data = json.loads(_setting(STORE) or '{}')
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        try:
            port = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get('host') and int(value.get('port') or 0) > 0:
            out[port] = value
    return out


def save(items):
    """Replace the mapping. ``items`` is ``{application port: {host, port, ...}}``."""
    clean = {}
    for key, value in (items or {}).items():
        try:
            port = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get('host') and int(value.get('port') or 0) > 0:
            clean[str(port)] = {'id': str(value.get('id') or ''), 'host': str(value['host']),
                                'port': int(value['port']),
                                'application_port': port,
                                'sync': str(value.get('sync') or '')}
    _store(STORE, json.dumps(clean, ensure_ascii=False))
    return sorted(int(key) for key in clean)


def entry(application_port):
    """The endpoint published for one listen port, or ``None``."""
    try:
        port = int(application_port)
    except (TypeError, ValueError):
        return None
    return load().get(port)


def forward(item):
    """Normalise one API row (or stored entry) into what :func:`save` takes."""
    try:
        application = int(item.get('applicationPort') or item.get('application_port') or 0)
        public = int(item.get('proxyPort') or item.get('port') or 0)
    except (TypeError, ValueError):
        return None, None
    if not (application and public and item.get('domain')):
        return None, None
    return application, {'id': str(item.get('id') or ''), 'host': str(item['domain']),
                         'port': public, 'sync': str(item.get('syncStatus') or '')}


def merge(rows):
    """Fold API rows into the stored mapping without dropping known-good ones.

    A proxy that was deleted on Railway's side is *not* removed here on purpose:
    the panel would then publish a wrong port, and a wrong port is worse than a
    stale one the admin can see and re-create.
    """
    items = load()
    changed = []
    for item in rows or []:
        application, endpoint = forward(item)
        if not application:
            continue
        if items.get(application) != endpoint:
            changed.append(application)
        items[application] = endpoint
    if changed:
        save(items)
    return sorted(changed)


def published_port(application_port, fallback=None):
    """The port a client should dial, defaulting to the listen port."""
    found = entry(application_port)
    if found:
        try:
            return int(found['port'])
        except (TypeError, ValueError, KeyError):
            pass
    try:
        return int(fallback if fallback is not None else application_port)
    except (TypeError, ValueError):
        return 0


def published_host(application_port=None):
    """The host a proxied capability is reachable on, or ``''``.

    With no application port the first stored endpoint is used, which is what a
    card that has no port of its own («where is this deployment reachable at
    all?») needs. On a host that owns its ports this is empty and the caller
    falls back to the platform's own hostname.
    """
    if application_port is not None:
        found = entry(application_port)
        return str((found or {}).get('host') or '')
    items = load()
    if not items:
        return ''
    return str(items[sorted(items)[0]].get('host') or '')


def proxied(application_port):
    """Whether a TCP proxy really fronts this listen port."""
    return entry(application_port) is not None


# ------------------------------------------------------------------- catalog
# The listen ports the panel can publish behind a Railway TCP proxy, in the order
# they matter: the direct (Reality) transport first, because the panel's own
# `railway-direct` node is built from it and it is the one a deployment gets by
# default; then the opt-in capabilities, each with the switch that decides
# whether it is worth a proxy at all.
#
# TUIC is deliberately absent: it is QUIC over UDP, which a TCP proxy cannot
# carry (see ``runtime.has_udp``), so there is nothing here to forward.
CATALOG = (
    {'id': 'reality', 'label': 'Reality (مسیر مستقیم)', 'port_key': None,
     'default_port': 'xray_reality_port', 'switch': None},
    {'id': 'anytls', 'label': 'AnyTLS', 'port_key': 'core_anytls_port',
     'default_port': 'core_anytls_port', 'switch': 'core_anytls_enabled'},
    {'id': 'mtproto', 'label': 'MTProto', 'port_key': 'tg_mtproto_port',
     'default_port': 'telegram_mtproto_port', 'switch': 'tg_mtproto_enabled'},
    {'id': 'http', 'label': 'وب‌پروکسی HTTP', 'port_key': 'tg_web_http_port',
     'default_port': 'telegram_http_port', 'switch': 'tg_web_http_enabled'},
    {'id': 'socks', 'label': 'وب‌پروکسی SOCKS5', 'port_key': 'tg_web_socks_port',
     'default_port': 'telegram_socks_port', 'switch': 'tg_web_socks_enabled'},
)


def _enabled(key):
    if key is None:
        return True
    return (_setting(key) or '0') == '1'


def application_port(item):
    """The port a capability's listener binds — the same rule its own module uses.

    Read through the settings table first (an admin may have moved it) and the
    config default second, so this module and the card that renders the listener
    can never disagree about which port is being forwarded.
    """
    raw = _setting(item.get('port_key') or '') if item.get('port_key') else None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if 1 <= value <= 65535:
        return value
    return int(getattr(settings, item['default_port']))


def catalog():
    """The ports worth forwarding here, with which capability each belongs to.

    A capability whose admin switch is off is left out: creating a public port for
    a listener nothing will bind is exactly the kind of unrequested change the rest
    of the panel refuses to make.
    """
    out = []
    for item in CATALOG:
        try:
            port = application_port(item)
        except Exception:
            continue
        out.append({'id': item['id'], 'label': item['label'], 'port': port,
                    'switch': item['switch'], 'enabled': _enabled(item['switch'])})
    return out


def wanted():
    """The listen ports to forward right now (enabled capabilities only)."""
    return [item['port'] for item in catalog() if item['enabled']]


def status():
    """What the panel shows: every capability, its listen port and its public one."""
    items = load()
    rows = []
    for item in catalog():
        found = items.get(item['port']) or {}
        rows.append({**item, 'proxied': bool(found),
                     'public_host': str(found.get('host') or ''),
                     'public_port': int(found.get('port') or 0)})
    return {
        'store': STORE,
        'done': bool(_setting(DONE)),
        'log': [part for part in (_setting(LOG) or '').split(',') if part],
        'catalog': rows,
        'count': len(items),
    }
