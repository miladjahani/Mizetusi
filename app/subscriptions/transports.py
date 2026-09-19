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

# The protocols a user can be subscribed to. Every user is created with the full
# set, so every path is live by default and an admin only narrows it on purpose.
PROTOCOLS = ('vless', 'vmess', 'trojan', 'ss')
ALL_PROTOCOLS = 'all'

# Shadowsocks-2022 needs a fixed-length key per user, so each cipher family gets
# its own profile/listener. ``key_len`` is the cipher's key size in bytes (0 for
# a legacy cipher, whose password is free-form); deriving the key from the UUID
# keeps one credential per user across every protocol.
SS_CIPHERS = (
    {'id': 'ss', 'method': '2022-blake3-aes-128-gcm', 'key_len': 16,
     'tag': 'SS-2022 · AES-128', 'path': '/ws/ss', 'cdn_path': '/cdn/ss'},
    {'id': 'ss-aes256', 'method': '2022-blake3-aes-256-gcm', 'key_len': 32,
     'tag': 'SS-2022 · AES-256', 'path': '/ws/ss-aes256', 'cdn_path': '/cdn/ss-aes256'},
    {'id': 'ss-chacha', 'method': '2022-blake3-chacha20-poly1305', 'key_len': 32,
     'tag': 'SS-2022 · ChaCha20', 'path': '/ws/ss-chacha', 'cdn_path': '/cdn/ss-chacha'},
)
# The first cipher is the one the panel calls "Shadowsocks" in one-click presets.
SS_METHOD = SS_CIPHERS[0]['method']
REALITY_SNI = 'www.cloudflare.com'


def _ss_profiles():
    """One profile per cipher, in both edge path shapes.

    Shadowsocks has no single-line sharing URI, so these only surface in the
    JSON formats (sing-box/Clash/Xray) — but each cipher still needs its own
    listener and its own subscription target.
    """
    items = []
    for cipher in SS_CIPHERS:
        slug = cipher['id'].replace('-', '_')
        items.append({'id': cipher['id'] + '-ws', 'protocol': 'ss', 'network': 'ws',
                      'path': cipher['path'], 'security': 'tls',
                      'method': cipher['method'], 'key_len': cipher['key_len'],
                      'tag': cipher['tag'] + ' · WS', 'port_setting': 'xray_' + slug + '_port'})
        items.append({'id': cipher['id'] + '-cdn', 'protocol': 'ss', 'network': 'ws',
                      'path': cipher['cdn_path'], 'security': 'tls',
                      'method': cipher['method'], 'key_len': cipher['key_len'],
                      'tag': cipher['tag'] + ' · CDN', 'port_setting': 'xray_' + slug + '_cdn_port'})
    return items


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
    *_ss_profiles(),
]

# Every public WebSocket path this deployment serves. The Cloudflare Worker and
# the FastAPI edge both route exactly this set, so the two can never drift.
def edge_paths():
    paths = [profile['path'] for profile in EDGE_PROFILES]
    if WARP_PROFILE['path'] not in paths:
        paths.append(WARP_PROFILE['path'])
    return paths


# ------------------------------------------------------------------- protocol set
def parse_protocols(value):
    """The protocol set a user is subscribed to.

    ``protocol`` is stored as a comma-separated list. Two values keep their
    historical meaning: an empty value and the bare single token older releases
    wrote (``vless``) both mean **all protocols** — those users have always
    received the full matrix, so nothing about them changes. Only an explicit
    multi-token list narrows the set, which is what the panel's checkboxes send.
    """
    raw = str(value or '').strip().lower()
    if raw in ('', ALL_PROTOCOLS, '*', 'everything', 'full'):
        return set(PROTOCOLS)
    parts = [part for part in raw.replace('+', ',').replace(' ', ',').split(',') if part]
    known = {part for part in parts if part in PROTOCOLS}
    if not known or len(parts) < 2:
        return set(PROTOCOLS)
    return known


def protocol_value(protocols):
    """Store a selection in the deterministic order the panel displays."""
    if protocols is None:
        return ALL_PROTOCOLS
    if isinstance(protocols, str):
        return protocols.strip().lower() or ALL_PROTOCOLS
    wanted = [p for p in PROTOCOLS if p in set(protocols)]
    if not wanted or len(wanted) == len(PROTOCOLS):
        return ALL_PROTOCOLS
    return ','.join(wanted)


def user_protocols(user):
    """Shorthand used by the generator, the panel and the Xray config."""
    return parse_protocols((user or {}).get('protocol'))


def protocol_catalog():
    """Panel view of the protocol selector (labels + the SS cipher families)."""
    labels = {'vless': 'VLESS', 'vmess': 'VMess', 'trojan': 'Trojan', 'ss': 'Shadowsocks'}
    return {
        'all': ALL_PROTOCOLS,
        'protocols': [{'id': p, 'label': labels[p]} for p in PROTOCOLS],
        'shadowsocks': [{'id': c['id'], 'method': c['method'], 'key_bytes': c['key_len'],
                         'tag': c['tag']} for c in SS_CIPHERS],
    }

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


def available_profiles(protocols=None):
    """Every profile this deployment can actually serve right now.

    ``protocols`` narrows the list to one user's enabled protocol set; the
    default (``None``) is the whole deployment-wide matrix.
    """
    items = [dict(p, group=EDGE) for p in EDGE_PROFILES]
    if warp_config():
        items.append(dict(WARP_PROFILE, group=WARP))
    if direct_endpoint() and reality_keys():
        items.extend(dict(p, group=DIRECT) for p in DIRECT_PROFILES)
    if protocols is not None:
        wanted = set(protocols)
        items = [item for item in items if item['protocol'] in wanted]
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
        'protocol_catalog': protocol_catalog(),
        'networks': sorted({p['network'] for p in available_profiles()}),
        'direct': direct,
        'reality_sni': REALITY_SNI,
        'warp': bool(warp_config()),
        'ss_method': SS_METHOD,
        'ss_methods': [c['method'] for c in SS_CIPHERS],
        'paths': edge_paths(),
        'planned': [dict(p, group=DIRECT) for p in PLANNED_PROFILES],
    }


# ------------------------------------------------------------------ credentials
def ss_key_len(profile=None):
    """Key size of a Shadowsocks profile (16 bytes by default)."""
    try:
        return int((profile or {}).get('key_len') or 16)
    except (TypeError, ValueError):
        return 16


def ss_key(user, profile=None):
    """Deterministic Shadowsocks-2022 key for one user and cipher.

    Shadowsocks-2022 requires a key of exactly the cipher's size, so the digest
    is stretched to ``key_len`` and base64-encoded. The cipher name is part of
    the seed, so two ciphers of the same length get unrelated keys; the same
    UUID always yields the same key for a given cipher.
    """
    identity = str((user or {}).get('uuid') or (user or {}).get('username') or 'nexus')
    length = ss_key_len(profile)
    method = str((profile or {}).get('method') or '')
    seed = f'{identity}|{method}|{length}'.encode()
    raw = hashlib.sha256(seed).digest()
    while len(raw) < length:
        raw += hashlib.sha256(raw).digest()
    return base64.b64encode(raw[:length]).decode()


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
