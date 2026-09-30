"""Advanced obfuscation: the transports and link parameters that make a
connection look like ordinary HTTPS rather than a proxy.

Reality alone is not the whole story. Three separate things are configured here,
and they are deliberately kept apart because they fail for different reasons:

1. **The transports** (:data:`PROFILES`) — XHTTP (H2/H3), gRPC and HTTPUpgrade,
   each on its *own* public TCP port, each fronted by Reality. These used to be a
   roadmap list in the panel: they cannot ride the HTTPS-terminating edge, and
   they were never given a listener. They are real now, one port each, because a
   transport that has no port of its own is a transport nobody can dial.

   A transport is published only when three things are all true: the admin
   switched it on, this deployment has a raw TCP endpoint at all, and *this
   profile's* port is really reachable — on Railway that means a TCP proxy for
   that port, because Railway allocates the public port at random and the panel
   only learns it through :mod:`app.ports`. Publishing a link for a port that is
   not forwarded is the one failure mode this module exists to prevent.

2. **The per-link parameters** (:func:`link_params`) — the ones a client reads
   straight out of the sharing URI: the uTLS fingerprint, Reality's ``spx``
   spider path, packet encoding, fragment length/interval, the TLS-in-TLS
   fragment mode and the TLS mask. The per-user half of these are columns the
   user form already owns (``fingerprint``, ``frag_len``, ``frag_int``,
   ``advanced_frag``, ``tls_mask``); the deployment-wide defaults live in the
   settings table so a new deployment can set them once.

3. **The VLESS flow** (``obfs_flow``) — ``xtls-rprx-vision`` on the direct
   (Reality) inbounds. It is not a disguise by itself, but it is what stops the
   post-handshake traffic from having the shape a DPI looks for, so it belongs
   with the rest. It is off until an admin asks for it, and it only ever applies
   to a Reality inbound: on the WebSocket edge a flow is invalid and Xray
   refuses the whole config.

Nothing here is imported by :mod:`app.subscriptions.transports` at module level
in the other direction: this module reads what it needs from ``transports`` at
call time, which keeps the import graph flat.
"""

from app import ports
from app.config import settings
from app.db import row, execute

# One settings row per transport switch, so «which of the advanced transports is
# on» is stored where every other switch in the panel lives.
SWITCH_PREFIX = 'obfs_'

# Deployment-wide link defaults. The per-user columns win over these, so an admin
# can set a house style once and still hand one user something else.
SPIDER_X = 'obfs_spider_x'
FLOW = 'obfs_flow'
PACKET_ENCODING = 'obfs_packet_encoding'
DEFAULT_SPIDER_X = '/'
# Xray 26 serves this one on a Reality inbound. An unknown value would be
# rejected by the engine and take every protocol down with it (the candidate
# ladder would drop the whole direct half), so the list is closed.
FLOWS = ('', 'xtls-rprx-vision')
PACKET_ENCODINGS = ('', 'xudp', 'packetaddr')

# The advanced transports. Each one owns a public TCP port of its own
# (``port_setting``), so turning one on never disturbs the others, and each
# carries the one thing a client needs to reach it.
PROFILES = (
    {'id': 'vless-xhttp', 'protocol': 'vless', 'network': 'xhttp', 'path': '/xh',
     'security': 'reality', 'mode': 'auto', 'tag': 'VLESS · XHTTP (H2/H3)',
     'port_setting': 'xray_fallback_xhttp_port',
     'note': 'جریان روی H2/H3؛ شبیه‌ترین شکل به ترافیک واقعی مرورگر'},
    {'id': 'vless-grpc', 'protocol': 'vless', 'network': 'grpc', 'path': '/grpc',
     'security': 'reality', 'mode': 'gun', 'tag': 'VLESS · gRPC',
     'port_setting': 'xray_fallback_grpc_port',
     'note': 'gRPC روی HTTP/2 با نام سرویس دلخواه'},
    {'id': 'vless-httpupgrade', 'protocol': 'vless', 'network': 'httpupgrade', 'path': '/hu',
     'security': 'reality', 'tag': 'VLESS · HTTPUpgrade',
     'port_setting': 'xray_fallback_httpupgrade_port',
     'note': 'آپگرید HTTP/1.1 — سبک‌ترین گزینه برای DPI'},
    {'id': 'vmess-grpc', 'protocol': 'vmess', 'network': 'grpc', 'path': '/vgrpc',
     'security': 'reality', 'mode': 'gun', 'tag': 'VMess · gRPC',
     'port_setting': 'xray_fallback_vmess_grpc_port',
     'note': 'همان gRPC برای کلاینت‌هایی که فقط VMess می‌پذیرند'},
    {'id': 'trojan-grpc', 'protocol': 'trojan', 'network': 'grpc', 'path': '/tgrpc',
     'security': 'reality', 'mode': 'gun', 'tag': 'Trojan · gRPC',
     'port_setting': 'xray_fallback_trojan_grpc_port',
     'note': 'همان gRPC برای کلاینت‌هایی که فقط Trojan می‌پذیرند'},
)

PROFILE_MAP = {item['id']: item for item in PROFILES}
NETWORKS = ('xhttp', 'grpc', 'httpupgrade')


def profile(profile_id):
    return PROFILE_MAP.get(str(profile_id or '').strip().lower())


# --------------------------------------------------------------------- settings
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
    return str(value)


def switch_key(profile_id):
    return SWITCH_PREFIX + str(profile_id or '').strip().lower()


def is_enabled(profile_id):
    """Whether an admin switched one advanced transport on.

    Off is the default: a deployment that starts answering on four extra public
    ports it was never asked for is a deployment whose address a censor can
    recognise.
    """
    return (_setting(switch_key(profile_id)) or '') == '1'


def set_enabled(profile_id, on):
    item = profile(profile_id)
    if not item:
        raise ValueError('unknown advanced transport: ' + str(profile_id))
    return _store(switch_key(item['id']), '1' if on else '0')


def spider_x():
    return _setting(SPIDER_X, DEFAULT_SPIDER_X) or DEFAULT_SPIDER_X


def flow():
    value = (_setting(FLOW) or '').strip()
    return value if value in FLOWS else ''


def packet_encoding():
    value = (_setting(PACKET_ENCODING) or '').strip().lower()
    return value if value in PACKET_ENCODINGS else ''


def defaults():
    return {'spider_x': spider_x(), 'flow': flow(), 'packet_encoding': packet_encoding(),
            'flows': [item for item in FLOWS if item],
            'packet_encodings': [item for item in PACKET_ENCODINGS if item]}


def validate(name, value):
    """Normalise one deployment default, refusing a value Xray would reject.

    The engine validates the whole config at once, so one bad ``flow`` takes
    every protocol down — hence the closed lists here rather than free text.
    """
    name = str(name or '').strip().lower()
    value = str(value or '').strip()
    if name == 'spider_x':
        return spider_x() if not value else ('/' + value.lstrip('/'))
    if name == 'flow':
        value = value.lower()
        if value not in FLOWS:
            raise ValueError('flow must be empty or ' + ' / '.join(x for x in FLOWS if x))
        return value
    if name == 'packet_encoding':
        value = value.lower()
        if value not in PACKET_ENCODINGS:
            raise ValueError('packet encoding must be empty or ' +
                             ' / '.join(x for x in PACKET_ENCODINGS if x))
        return value
    raise ValueError('unknown obfuscation setting: ' + name)


def save(payload):
    """Write the deployment-wide defaults, validating each one."""
    changed = []
    for name, key in (('spider_x', SPIDER_X), ('flow', FLOW),
                      ('packet_encoding', PACKET_ENCODING)):
        if name not in (payload or {}):
            continue
        value = validate(name, (payload or {}).get(name))
        _store(key, value)
        changed.append(name)
    return changed


# -------------------------------------------------------------------- endpoints
def listen_port(item):
    """The port the listener binds inside this container."""
    return int(getattr(settings, item['port_setting'], 0) or 0)


def public_endpoint(item, endpoint=None):
    """Where a client dials this transport, and why it cannot when it cannot.

    The host is the deployment's own raw TCP endpoint (the same one Reality is
    published on); the **port** is this transport's own, because each one binds
    a different number. On Railway a public port exists only once a TCP proxy
    has been created for that number (:mod:`app.ports`), and its value is random —
    so a transport whose proxy is missing is withheld rather than advertised on
    a port nothing forwards.

    ``endpoint`` lets a caller resolve the deployment's endpoint **once** for a
    whole page: detecting it can cost an outbound request, and this runs per
    transport.
    """
    from app.subscriptions import transports as tp
    endpoint = tp.direct_endpoint() if endpoint is None else endpoint
    if not endpoint:
        return None, 'این نصب هیچ پورت خام TCP ندارد (یک TCP Proxy بسازید یا direct_host را ست کنید)'
    listen = listen_port(item)
    if not listen:
        return None, 'پورت این انتقال تنظیم نشده است'
    forwarded = ports.entry(listen)
    if forwarded:
        try:
            return {'host': str(forwarded['host']), 'port': int(forwarded['port']),
                    'listen_port': listen, 'proxied': True}, ''
        except (TypeError, ValueError, KeyError):
            pass
    if str(endpoint.get('source') or '') == 'railway':
        return None, 'روی Railway برای این پورت باید یک TCP Proxy ساخته شود'
    return {'host': str(endpoint.get('host') or ''), 'port': listen,
            'listen_port': listen, 'proxied': False}, ''


def available():
    """The advanced profiles this deployment can really serve right now.

    Same rule as everywhere else in the panel: enabled **and** reachable, so no
    subscription can point at a listener that does not exist.
    """
    from app.subscriptions import transports as tp
    endpoint = tp.direct_endpoint()
    if not (endpoint and tp.reality_keys()):
        return []
    out = []
    for item in PROFILES:
        if not is_enabled(item['id']):
            continue
        published, reason = public_endpoint(item, endpoint)
        if not published:
            continue
        out.append({**item, 'group': tp.DIRECT, 'endpoint': published,
                    'path': item['path'], 'port_setting': item['port_setting'],
                    'order': len(out), 'reason': reason})
    return out


def catalog():
    """The panel view: every advanced transport, on or off, reachable or not."""
    from app.subscriptions import transports as tp
    endpoint = tp.direct_endpoint()
    rows = []
    for item in PROFILES:
        published, reason = public_endpoint(item, endpoint)
        rows.append({
            'id': item['id'], 'tag': item['tag'], 'protocol': item['protocol'],
            'network': item['network'], 'path': item['path'], 'note': item['note'],
            'mode': item.get('mode') or '', 'enabled': is_enabled(item['id']),
            'listen_port': listen_port(item),
            'host': (published or {}).get('host', ''),
            'public_port': int((published or {}).get('port') or 0),
            'proxied': bool((published or {}).get('proxied')),
            'reachable': bool(published), 'reason': reason,
        })
    return {'profiles': rows, 'defaults': defaults(),
            'published': [item['id'] for item in available()],
            'networks': list(NETWORKS)}


def published_ids():
    return {item['id'] for item in available()}


# ------------------------------------------------------------------ link params
def user_value(user, key, default=''):
    """One per-user advanced option, as text (``0``/``''`` mean «off»)."""
    value = (user or {}).get(key)
    if value in (None, ''):
        return default
    return str(value).strip()


def link_params(item, user):
    """The extra query parameters one advanced transport's link carries.

    The transport's own parameters come first (``type`` and whatever that
    network needs to reach it), then the obfuscation half: uTLS fingerprint,
    Reality's spider path, packet encoding, and the per-user fragment/TLS-mask
    options. The per-user column wins over the deployment default, so a house
    style can be set once and overridden for one user.
    """
    from app.subscriptions import transports as tp
    query = {}
    network = item['network']
    query['type'] = network
    if network == 'xhttp':
        query['mode'] = item.get('mode') or 'auto'
        query['path'] = item['path']
        query['host'] = tp.REALITY_SNI
    elif network == 'grpc':
        query['serviceName'] = item['path'].lstrip('/')
        query['mode'] = item.get('mode') or 'gun'
    elif network == 'httpupgrade':
        query['path'] = item['path']
        query['host'] = tp.REALITY_SNI
    # uTLS: the fingerprint a real browser sends is the single most effective
    # thing in this list, and it is per user, not per deployment.
    query['fp'] = user_value(user, 'fingerprint', 'chrome') or 'chrome'
    query['spx'] = spider_x()
    encoding = packet_encoding()
    if encoding:
        query['packetEncoding'] = encoding
    # Fragmentation: length + interval, plus the TLS-in-TLS mode, which is the
    # one a client applies to the ClientHello itself.
    frag = user_value(user, 'frag_len')
    interval = user_value(user, 'frag_int')
    if frag:
        query['fragment'] = frag
    if interval:
        query['fragmentInterval'] = interval
    advanced = user_value(user, 'advanced_frag')
    if advanced:
        query['advancedFragment'] = advanced
    mask = user_value(user, 'tls_mask')
    if mask:
        query['tlsMask'] = mask
    return query


def user_flow(profile):
    """The VLESS flow one inbound's clients must present (``''`` = none).

    Only a Reality inbound may carry a flow, so this returns ``''`` for every
    other network rather than handing Xray a config it would refuse.
    """
    if (profile or {}).get('security') != 'reality':
        return ''
    if (profile or {}).get('protocol') != 'vless':
        return ''
    # Only the RAW (tcp) transport carries a flow: Xray accepts a flow on an
    # XHTTP inbound syntactically but then serves no traffic through it, so the
    # deployment-wide switch is honoured on ``tcp`` and nowhere else.
    if str((profile or {}).get('network') or '') != 'tcp':
        return ''
    return flow()
