"""Protocol + transport profiles.

One subscription entry is a **(node × profile)** pair. A profile is the complete
description of how a client reaches this deployment:

* ``edge`` profiles ride the FastAPI/Xray WebSocket bridge, so they are live on
  every Railway deployment with **zero configuration** — the panel publishes
  VLESS, VMess, Trojan and Shadowsocks for every node by default.
* ``direct`` profiles need a raw TCP endpoint (Railway TCP proxy or a custom
  host:port). They are declared here, built into the Xray config as soon as
  ``direct_host``/``direct_port`` are known, and only then published — a link is
  never advertised before its listener exists.
* ``warp`` is the WireGuard outbound: Xray owns a real WARP tunnel and routes
  the ``/ws/warp`` inbound through it, so a WARP node is a genuine exit node.

Adding a transport here is the only edit needed for the generator, the panel
coverage view and the Xray config to pick it up.
"""
import base64
import hashlib
import json
import os

from app.config import settings
from app.db import row

EDGE = 'edge'
DIRECT = 'direct'
WARP = 'warp'

# Shadowsocks-2022 needs a fixed-length key per user; deriving it from the UUID
# keeps one credential per user across every protocol.
SS_METHOD = '2022-blake3-aes-128-gcm'
REALITY_SNI = 'www.cloudflare.com'

EDGE_PROFILES = [
    {'id': 'vless-ws', 'protocol': 'vless', 'network': 'ws', 'path': '/ws/vless',
     'security': 'tls', 'tag': 'VLESS · WS', 'port_setting': 'xray_vless_port'},
    {'id': 'vless-cdn', 'protocol': 'vless', 'network': 'ws', 'path': '/cdn/vless',
     'security': 'tls', 'tag': 'VLESS · CDN', 'port_setting': 'xray_vless_cdn_port'},
    {'id': 'vmess-ws', 'protocol': 'vmess', 'network': 'ws', 'path': '/ws/vmess',
     'security': 'tls', 'tag': 'VMess · WS', 'port_setting': 'xray_vmess_port'},
    {'id': 'vmess-cdn', 'protocol': 'vmess', 'network': 'ws', 'path': '/cdn/vmess',
     'security': 'tls', 'tag': 'VMess · CDN', 'port_setting': 'xray_vmess_cdn_port'},
    {'id': 'trojan-ws', 'protocol': 'trojan', 'network': 'ws', 'path': '/ws/trojan',
     'security': 'tls', 'tag': 'Trojan · WS', 'port_setting': 'xray_trojan_port'},
    {'id': 'trojan-cdn', 'protocol': 'trojan', 'network': 'ws', 'path': '/cdn/trojan',
     'security': 'tls', 'tag': 'Trojan · CDN', 'port_setting': 'xray_trojan_cdn_port'},
    {'id': 'ss-ws', 'protocol': 'ss', 'network': 'ws', 'path': '/ws/ss',
     'security': 'tls', 'tag': 'SS · WS', 'port_setting': 'xray_ss_port'},
]

# Reality terminates TLS inside Xray with the certificate of a real site, so the
# handshake is indistinguishable from ordinary browsing. This is the one direct
# transport Xray 26 serves reliably on a single public TCP port.
#
# gRPC / XHTTP / HTTPUpgrade are deliberately NOT listed: Reality only accepts
# RAW, XHTTP and gRPC clients, the h2-shaped ones need their own dedicated port
# (Reality fallbacks are not honoured for them in 26.9.9 — verified), and a
# Railway service can publish exactly one raw TCP port. Those transports stay
# listed in the panel as "planned" so nobody wonders where they went.
DIRECT_PROFILES = [
    {'id': 'vless-reality', 'protocol': 'vless', 'network': 'tcp', 'path': '',
     'security': 'reality', 'tag': 'VLESS · Reality'},
]

# Declared for the panel's roadmap view only — never published as a link.
PLANNED_PROFILES = [
    {'id': 'vless-grpc', 'protocol': 'vless', 'network': 'grpc', 'path': '/grpc',
     'security': 'reality', 'tag': 'VLESS · gRPC', 'needs': 'پورت TCP اختصاصی'},
    {'id': 'vless-xhttp', 'protocol': 'vless', 'network': 'xhttp', 'path': '/xh',
     'security': 'reality', 'tag': 'VLESS · XHTTP (H2/H3)', 'needs': 'پورت TCP اختصاصی'},
    {'id': 'vless-httpupgrade', 'protocol': 'vless', 'network': 'httpupgrade', 'path': '/hu',
     'security': 'reality', 'tag': 'VLESS · HTTPUpgrade', 'needs': 'پورت TCP اختصاصی'},
    {'id': 'vmess-grpc', 'protocol': 'vmess', 'network': 'grpc', 'path': '/vgrpc',
     'security': 'reality', 'tag': 'VMess · gRPC', 'needs': 'پورت TCP اختصاصی'},
    {'id': 'trojan-grpc', 'protocol': 'trojan', 'network': 'grpc', 'path': '/tgrpc',
     'security': 'reality', 'tag': 'Trojan · gRPC', 'needs': 'پورت TCP اختصاصی'},
]

WARP_PROFILE = {'id': 'warp-ws', 'protocol': 'vless', 'network': 'ws', 'path': '/ws/warp',
                'security': 'tls', 'tag': 'WARP · WS', 'port_setting': 'xray_warp_port'}

# Every protocol that can be expressed as a one-line sharing URI. Shadowsocks
# over WebSocket has no URI form, so it ships in the JSON formats only rather
# than as a link that would silently dial the wrong endpoint.
URI_PROTOCOLS = ('vless', 'trojan', 'vmess')

TRANSPORT_GROUPS = (
    ('all', 'همه'),
    ('ws', 'WebSocket'),
    ('reality', 'Reality'),
    ('grpc', 'gRPC'),
    ('httpupgrade', 'HTTPUpgrade'),
    ('xhttp', 'XHTTP · H2/H3'),
    ('warp', 'WARP'),
)


def profile_port(profile):
    key = profile.get('port_setting')
    return int(getattr(settings, key)) if key else None


# --------------------------------------------------------------------- settings
def _setting(key, default=None):
    try:
        found = row('SELECT value FROM settings WHERE key=?', (key,))
    except Exception:
        found = None
    value = (found or {}).get('value') if found else None
    return value if value not in (None, '') else default


def reality_keys():
    """The Reality key pair, cached in settings so links stay stable."""
    private = _setting('reality_private')
    public = _setting('reality_public')
    if private and public:
        return {'private_key': private, 'public_key': public,
                'short_id': _setting('reality_short_id') or '6ba85179e30d4fc2'}
    return None


def direct_endpoint():
    """Where a raw TCP client can reach this deployment, if anywhere at all.

    Railway injects the TCP-proxy hostname only after the admin enables one, so
    the direct transports switch themselves on with no code change.
    """
    host = (_setting('direct_host') or settings.direct_host
            or os.getenv('NEXUS_DIRECT_HOST') or os.getenv('RAILWAY_TCP_PROXY_DOMAIN') or '').strip()
    port = (_setting('direct_port') or settings.direct_port
            or os.getenv('NEXUS_DIRECT_PORT') or os.getenv('RAILWAY_TCP_PROXY_PORT') or 0)
    try:
        port = int(port or 0)
    except (TypeError, ValueError):
        port = 0
    if host and port:
        return {'host': host, 'port': port}
    return None


def warp_config():
    """The registered WARP peer, and only once an admin enabled it.

    A WARP tunnel needs outbound WireGuard to be allowed by the host, which the
    panel cannot assume; publishing the node before someone confirmed it works
    would hand users an endpoint that silently dead-ends.
    """
    if (_setting('warp_enabled') or '') != '1':
        return None
    raw = _setting('warp_config')
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get('secret_key') or not data.get('peer_public_key'):
        return None
    return data


def available_profiles():
    """Every profile this deployment can actually serve right now."""
    items = [dict(p, group=EDGE) for p in EDGE_PROFILES]
    if warp_config():
        items.append(dict(WARP_PROFILE, group=WARP))
    if direct_endpoint() and reality_keys():
        items.extend(dict(p, group=DIRECT) for p in DIRECT_PROFILES)
    for index, item in enumerate(items):
        item.setdefault('order', index)
    return items


def profile_map():
    return {item['id']: item for item in available_profiles()}


def find(profile_id):
    return profile_map().get(str(profile_id or '').strip().lower())


def uri_profiles():
    """Profiles that can be written as a single sharing link."""
    return [p for p in available_profiles() if p['protocol'] in URI_PROTOCOLS]


def by_protocol(protocol):
    return [p for p in available_profiles() if p['protocol'] == protocol]


def by_network(network):
    network = str(network or '').lower()
    return [p for p in available_profiles() if p['network'] == network]


def catalog():
    """Panel view of the profile set (labels, counts, availability)."""
    direct = direct_endpoint()
    return {
        'profiles': available_profiles(),
        'uri_profiles': [p['id'] for p in uri_profiles()],
        'protocols': sorted({p['protocol'] for p in available_profiles()}),
        'networks': sorted({p['network'] for p in available_profiles()}),
        'direct': direct,
        'reality_sni': REALITY_SNI,
        'warp': bool(warp_config()),
        'ss_method': SS_METHOD,
        'planned': [dict(p, group=DIRECT) for p in PLANNED_PROFILES],
    }


# ------------------------------------------------------------------ credentials
def ss_key(user):
    """Deterministic Shadowsocks-2022 key for one user."""
    seed = str((user or {}).get('uuid') or (user or {}).get('username') or 'nexus').encode()
    return base64.b64encode(hashlib.sha256(seed).digest()[:16]).decode()


def node_address(node, profile):
    """The host:port a client dials for this profile."""
    if profile['group'] == DIRECT:
        endpoint = direct_endpoint() or {}
        return str(endpoint.get('host') or node.get('server') or ''), int(endpoint.get('port') or 443)
    return str(node.get('server') or ''), int(node.get('port') or 443)


def node_host_header(node):
    return str(node.get('host') or node.get('server') or '')


def node_sni(node):
    return str(node.get('sni') or node.get('host') or node.get('server') or '')
