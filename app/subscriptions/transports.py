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
import json
import os
import secrets

from app import runtime
from app.config import settings
from app.db import row, execute

EDGE = 'edge'
DIRECT = 'direct'
WARP = 'warp'

# The protocols a user can be subscribed to. Every user is created with the full
# set, so every path is live by default and an admin only narrows it on purpose.
PROTOCOLS = ('vless', 'vmess', 'trojan', 'ss')
ALL_PROTOCOLS = 'all'

# Shadowsocks ciphers, one profile/listener per cipher and path shape.
#
# Every cipher is served in **single-user** mode (one PSK per cipher, shared by
# the clients that hold a link). That is deliberate, and it is what made
# Shadowsocks disappear from every client list before:
#
# * Xray 26 refuses to build a *multi-user* Shadowsocks-2022 inbound for anything
#   but ``blake3-aes-*-gcm`` ("only blake3-aes-*-gcm methods are supported"), and
#   one such inbound aborts the whole engine — every protocol stopped answering,
#   not just Shadowsocks;
# * even where multi-user builds, clients disagree about the key a user has to
#   present (user PSK alone vs ``userPSK:serverPSK``), so a published link would
#   silently fail to authenticate.
#
# A single PSK is accepted by every client (Xray, sing-box, mihomo, v2rayNG,
# NekoBox, Shadowrocket) in the plain SIP002 form, so Shadowsocks now works
# everywhere. ``key_len`` is the cipher's key size in bytes.
SS_CIPHERS = (
    {'id': 'ss', 'method': '2022-blake3-aes-128-gcm', 'key_len': 16,
     'tag': 'SS-2022 · AES-128', 'path': '/ws/ss', 'cdn_path': '/cdn/ss'},
    {'id': 'ss-aes256', 'method': '2022-blake3-aes-256-gcm', 'key_len': 32,
     'tag': 'SS-2022 · AES-256', 'path': '/ws/ss-aes256', 'cdn_path': '/cdn/ss-aes256'},
    {'id': 'ss-chacha', 'method': '2022-blake3-chacha20-poly1305', 'key_len': 32,
     'tag': 'SS-2022 · ChaCha20', 'path': '/ws/ss-chacha', 'cdn_path': '/cdn/ss-chacha'},
    {'id': 'ss-legacy', 'method': 'chacha20-ietf-poly1305', 'key_len': 0,
     'tag': 'SS · ChaCha20-IETF (سازگاری بالا)', 'path': '/ws/ss-legacy', 'cdn_path': '/cdn/ss-legacy'},
)
# The first cipher is the one the panel calls "Shadowsocks" in one-click presets.
SS_METHOD = SS_CIPHERS[0]['method']
REALITY_SNI = 'www.cloudflare.com'

# The engine that Xray actually built on the last successful (re)start. Links are
# never published for a listener that does not exist, so this is what keeps the
# subscription honest when a transport has to be dropped to keep the engine
# alive. ``None`` means "not known yet" (fresh process, tests): publish the full
# matrix so the panel and the generator still have something to show.
_served_profiles = None


def set_served(profile_ids):
    """Remember which profiles the running Xray config really contains."""
    global _served_profiles
    _served_profiles = None if profile_ids is None else {str(p) for p in profile_ids}


def served_profiles():
    return None if _served_profiles is None else set(_served_profiles)


def is_served(profile_id):
    served = _served_profiles
    return served is None or str(profile_id) in served


def _ss_profiles():
    """One profile per cipher, in both edge path shapes.

    Each cipher gets its own listener, its own key and its own subscription
    target, and each is published as a SIP002 link (`ss://`) carrying the
    ``v2ray-plugin`` WebSocket edge, so it lands in a client's config list like
    every other protocol.
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
                         'tag': c['tag'], 'linkable': True} for c in SS_CIPHERS],
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
# rides the edge through the ``v2ray-plugin`` SIP003 plugin, which is exactly what
# the SIP002 ``plugin=`` parameter is for, so it is a first-class link too: the
# Shadowsocks nodes now show up in v2rayNG/NekoBox config lists like the rest.
URI_PROTOCOLS = ('vless', 'trojan', 'vmess', 'ss')

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

    Precedence: the panel's own setting, then the environment, then — on a host
    that owns a public port (a VPS, Docker, Fly) — the machine's own address via
    :mod:`app.runtime`. Railway only gets one after the admin enables a TCP
    proxy, and Render/Heroku never do, so nothing is published there.
    """
    host = (_setting('direct_host') or settings.direct_host or '').strip()
    port = _setting('direct_port') or settings.direct_port or 0
    try:
        port = int(port or 0)
    except (TypeError, ValueError):
        port = 0
    if host and port:
        return {'host': host, 'port': port, 'source': 'panel'}
    detected = runtime.direct()
    if detected:
        return {'host': detected['host'], 'port': int(detected['port']),
                'source': detected.get('source') or 'env'}
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
    default (``None``) is the whole deployment-wide matrix. A profile the running
    Xray config does not contain (dropped to keep the engine alive) is never
    returned, so no subscription can point at a dead listener.
    """
    items = [dict(p, group=EDGE) for p in EDGE_PROFILES]
    if warp_config():
        items.append(dict(WARP_PROFILE, group=WARP))
    if direct_endpoint() and reality_keys():
        items.extend(dict(p, group=DIRECT) for p in DIRECT_PROFILES)
    served = _served_profiles
    if served is not None:
        items = [item for item in items if item['id'] in served]
    if protocols is not None:
        wanted = set(protocols)
        items = [item for item in items if item['protocol'] in wanted]
    for index, item in enumerate(items):
        item.setdefault('order', index)
        # The internal listener port: what the edge bridges to (and what an
        # admin sees in the panel next to every published link).
        item['listener_port'] = profile_port(item)
        item.setdefault('path', item.get('path') or '')
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
    served = _served_profiles
    return {
        'profiles': available_profiles(),
        'served': None if served is None else sorted(served),
        'withheld': [] if served is None else [p['id'] for p in EDGE_PROFILES if p['id'] not in served],
        'ss_shared_keys': [c['id'] for c in SS_CIPHERS],
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


def ss_key_setting(profile=None):
    """Settings key holding the PSK of one cipher."""
    method = str((profile or {}).get('method') or SS_METHOD)
    return 'ss_psk:' + method


def ss_shared_key(profile=None, create=True):
    """The PSK a cipher's listener and its links use (created once).

    Shadowsocks needs a key of exactly the cipher's size for 2022 methods (any
    string for the older ones), so it is generated from the OS entropy pool once
    and then reused: every restart, and therefore every published link, keeps
    working. Rotating it is what revokes Shadowsocks access for everyone.
    """
    setting = ss_key_setting(profile)
    found = _setting(setting)
    if found or not create:
        return found
    length = ss_key_len(profile) if str((profile or {}).get('method') or '').startswith('2022-') else 20
    value = base64.b64encode(secrets.token_bytes(max(16, length))).decode()
    try:
        execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (setting, value))
    except Exception:
        pass
    return value


def rotate_ss_keys():
    """Regenerate every Shadowsocks PSK (revokes the old links)."""
    rotated = []
    for cipher in SS_CIPHERS:
        profile = {'method': cipher['method'], 'key_len': cipher['key_len']}
        setting = ss_key_setting(profile)
        length = ss_key_len(profile) if str(cipher['method']).startswith('2022-') else 20
        value = base64.b64encode(secrets.token_bytes(max(16, length))).decode()
        try:
            execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (setting, value))
            rotated.append(cipher['id'])
        except Exception:
            continue
    return rotated


def ss_key(user, profile=None):
    """The password a Shadowsocks client must present for this cipher.

    One cipher = one key: the same value the listener authenticates, so the key
    is identical for every user of that cipher and cannot be derived per user
    without breaking the clients (see :data:`SS_CIPHERS`).
    """
    return ss_shared_key(profile) or ''


def ss_plugin_opts(profile, host, leading_name=True):
    """SIP003 plugin options for Shadowsocks over the WebSocket edge.

    Shadowsocks has no transport of its own, so the client reaches the edge with
    ``v2ray-plugin`` in WebSocket+TLS mode — the one plugin every mainstream
    client implements (sing-box, mihomo, v2rayNG, NekoBox, Shadowrocket).

    ``leading_name`` controls the two spellings in use: a SIP002 link carries the
    plugin name first (``v2ray-plugin;tls;mode=websocket;…``) while sing-box takes
    bare options (``tls;mode=websocket;…``) next to its own ``plugin`` field.
    """
    path = str((profile or {}).get('path') or '')
    opts = f'tls;mode=websocket;host={host};path={path}'
    return f'v2ray-plugin;{opts}' if leading_name else opts


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


def node_location(node):
    """The location label of an edge source ('' for the deployment's own node)."""
    from app.edge.sources import parse_metadata
    return str(parse_metadata((node or {}).get('metadata')).get('location') or '').strip().lower()


def node_provider(node):
    """Which edge source published this node ('' when unknown)."""
    from app.edge.sources import parse_metadata
    raw = parse_metadata((node or {}).get('metadata'))
    return str(raw.get('provider') or (node or {}).get('source') or '').strip().lower()
