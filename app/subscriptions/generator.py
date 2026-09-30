"""Subscription rendering.

One subscription entry is a **(node × transport profile)** pair, so a user's
default subscription already carries every protocol this deployment serves —
VLESS, VMess, Trojan and (in the JSON formats) Shadowsocks — on every node, in
the fastest-first order the ping loop measured.

``active_nodes`` stays the one rule the whole panel shares: a subscription hands
out **enabled** nodes ordered by the last measured ping, and measured health is
a sorting signal, never a filter. Nodes are generated automatically (Railway
origin, Cloudflare clean IPs); an admin never has to build them by hand.
"""
import base64, json, urllib.parse
from app.db import rows
from app.subscriptions.clients import CLIENT_FORMATS, FORMATS
from app.subscriptions import transports as tp
from app.subscriptions import scope as scopes
from app.subscriptions import yamlout

# Every client format NEXUS can emit, plus one target per known client id so a
# client can subscribe with the exact name it is listed under in the panel.
TARGETS = list(FORMATS) + [cid for cid in CLIENT_FORMATS if cid not in FORMATS]
ALIASES = {
    'v2ray': 'base64', 'v2ray-base64': 'base64', 'base64-sub': 'base64',
    'sing-box': 'singbox', 'singbox-json': 'singbox', 'sb': 'singbox',
    'mihomo': 'clash', 'clash-meta': 'clash', 'yaml': 'clash',
    'xray-json': 'xray', 'xray-outbound': 'xray',
    'vless-all': 'vless', 'trojan-all': 'trojan',
    'mix': 'all', 'full': 'all', 'everything': 'all',
    'shadowsocks': 'ss', 'shadow-socks': 'ss',
    'websocket': 'ws', 'httpupgrade': 'httpupgrade', 'xhttp': 'xhttp',
}
# client id -> format (e.g. nekoboxplus -> singbox, bettbox -> base64)
ALIASES.update({cid: fmt for cid, fmt in CLIENT_FORMATS.items() if cid not in FORMATS})

# Targets that pick profiles by protocol or by transport instead of by format.
PROTOCOL_TARGETS = ('vless', 'trojan', 'vmess', 'ss', tp.ANYTLS, tp.TUIC)
TRANSPORT_TARGETS = ('ws', 'cdn', 'reality', 'grpc', 'httpupgrade', 'xhttp', 'warp')
LINE_FORMATS = ('auto', 'all', 'base64', 'vless', 'trojan', 'vmess')


def normalize_target(target):
    value = (target or 'auto').strip().lower()
    value = ALIASES.get(value, value)
    # A transport profile id is a valid target too, so "just the Reality node" or
    # "just this CDN path" is a normal subscription URL.
    if value not in TARGETS and not tp.find(value):
        raise ValueError('unsupported target: ' + str(target))
    return value


def label(user, node, profile=None, prefix=''):
    parts = [p for p in (prefix or '', tp.node_label(node)) if p]
    if profile and profile.get('tag'):
        parts.append(profile['tag'])
    return ' · '.join(parts)


def hy2_node():
    """A pseudo-node describing the external Hysteria2 endpoint.

    Hysteria2 does not belong to the Node Catalog (it is not this server's
    listener), but it must still be named and flagged like the others, so it is
    rendered through exactly the same label path.
    """
    hy2 = tp.hysteria_config() or {}
    host = str(hy2.get('host') or '')
    return {'name': hy2.get('label') or host or 'Hysteria2', 'kind': 'hy2', 'server': host,
            'port': int(hy2.get('port') or 443), 'tls': 1, 'host': host,
            'sni': hy2.get('sni') or host, 'latency_ms': None,
            'metadata': {'role': 'hysteria2', 'provider': 'hysteria2'}}


def core_node(profile=None):
    """A pseudo-node for this deployment's own hosted protocols.

    AnyTLS and TUIC answer on the server itself, so they are *one* node — the
    origin — with a raw port each, not a node per edge location: a CDN forwards
    neither a raw TLS connection nor QUIC to an origin. Naming it after the
    origin node keeps a client list readable (the same server appears once per
    transport family) and keeps its country flag and label truthful.
    """
    core = tp.core_config() or {}
    host = str(core.get('host') or '')
    node = None
    try:
        from app.nodes import origin_node_name
        found = rows('SELECT * FROM nodes WHERE name=?', (origin_node_name(),))
        node = dict(found[0]) if found else None
    except Exception:
        node = None
    if not node:
        node = {'name': host or 'NEXUS', 'kind': 'core', 'server': host, 'port': 443,
                'tls': 1, 'host': host, 'sni': core.get('sni') or host,
                'latency_ms': None, 'metadata': {}}
    node = dict(node)
    node.update({'kind': node.get('kind') or 'core', 'server': host, 'host': host,
                 'sni': core.get('sni') or host, 'tls': 1})
    return node


def _is_origin(node):
    """Whether a node is this deployment itself (so a hosted link belongs on it)."""
    try:
        from app.nodes import origin_node_name
        return str((node or {}).get('name') or '') == origin_node_name()
    except Exception:
        return False


def _profile_for(profile_id):
    found = tp.find(profile_id)
    if not found:
        raise ValueError('transport not available: ' + str(profile_id))
    return found


# --------------------------------------------------------------- line protocols
def _reality_query(node, profile, user):
    keys = tp.reality_keys() or {}
    query = {
        'encryption': 'none', 'security': 'reality', 'sni': tp.REALITY_SNI,
        'fp': (user.get('fingerprint') or 'chrome'), 'pbk': keys.get('public_key', ''),
        'sid': keys.get('short_id', ''), 'type': 'tcp', 'headerType': 'none',
    }
    if user.get('frag_len'):
        query['fragment'] = user['frag_len']
    return query


def _ws_query(node, profile, user):
    query = {
        'encryption': 'none', 'security': 'tls' if node.get('tls') else 'none',
        'type': 'ws', 'host': tp.node_host_header(node), 'path': profile['path'],
    }
    sni = tp.node_sni(node)
    if sni:
        query['sni'] = sni
    if user.get('frag_len'):
        query['fragment'] = user['frag_len']
    if user.get('fingerprint'):
        query['fp'] = user['fingerprint']
    if user.get('tls_mask'):
        query['tlsMask'] = user['tls_mask']
    return query


def profile_query(node, profile, user):
    """The share-link query string for one profile."""
    if profile.get('security') == 'reality':
        return _reality_query(node, profile, user)
    return _ws_query(node, profile, user)


def _params(query):
    return urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, '')}, safe='')


def vless_uri(user, node, profile, prefix=''):
    address, port = tp.node_address(node, profile)
    name = urllib.parse.quote(label(user, node, profile, prefix), safe='')
    return f"vless://{user['uuid']}@{address}:{port}?{_params(profile_query(node, profile, user))}#{name}"


def trojan_uri(user, node, profile, prefix=''):
    address, port = tp.node_address(node, profile)
    name = urllib.parse.quote(label(user, node, profile, prefix), safe='')
    return f"trojan://{urllib.parse.quote(user['uuid'], safe='')}@{address}:{port}?{_params(profile_query(node, profile, user))}#{name}"


def ss_uri(user, node, profile, prefix=''):
    """Shadowsocks as a SIP002 link, in the form every client imports.

    Shadowsocks has no transport of its own, so the edge is reached with the
    ``v2ray-plugin`` SIP003 plugin in WebSocket+TLS mode — the plugin sing-box,
    mihomo, v2rayNG, NekoBox and Shadowrocket all implement. This is what puts
    Shadowsocks into the client config list next to VLESS/VMess/Trojan.
    """
    address, port = tp.node_address(node, profile)
    method = profile.get('method') or tp.SS_METHOD
    secret = tp.ss_key(user, profile)
    userinfo = base64.urlsafe_b64encode(f'{method}:{secret}'.encode()).decode().rstrip('=')
    query = {'plugin': tp.ss_plugin_opts(profile, tp.node_host_header(node))}
    name = urllib.parse.quote(label(user, node, profile, prefix), safe='')
    return f"ss://{userinfo}@{address}:{port}?{_params(query)}#{name}"


def hy2_uri(user, node, profile, prefix=''):
    """Hysteria2 as the ``hysteria2://`` link its clients import.

    The one-line form is understood by every sing-box/mihomo based client
    (Hiddify, NekoBox, Karing, v2rayN, Streisand). The password is the endpoint's
    own, since a hysteria2 server authenticates its users itself.
    """
    hy2 = tp.hysteria_config()
    if not hy2:
        raise ValueError('Hysteria2 is not configured yet')
    query = {'sni': hy2['sni'], 'insecure': '1' if hy2['insecure'] else ''}
    if hy2['obfs']:
        query['obfs'] = hy2['obfs']
        if hy2['obfs_password']:
            query['obfs-password'] = hy2['obfs_password']
    name = urllib.parse.quote(label(user, hy2_node(), profile, prefix), safe='')
    return (f"hysteria2://{urllib.parse.quote(hy2['password'], safe='')}@"
            f"{hy2['host']}:{int(hy2['port'])}?{_params(query)}#{name}")


def anytls_uri(user, node, profile, prefix=''):
    """AnyTLS as the ``anytls://`` link its clients import.

    The query keys are the ones the clients actually read for this protocol
    (``insecure``/``sni``/``fp``), and the password is the user's own credential —
    the hosted listener authenticates one entry per user, exactly like the Xray
    inbounds do.
    """
    address, port = tp.node_address(node, profile)
    core = tp.core_config() or {}
    query = {'insecure': '1', 'sni': core.get('sni') or address,
             'fp': user.get('fingerprint') or 'chrome'}
    name = urllib.parse.quote(label(user, node, profile, prefix), safe='')
    return (f"anytls://{urllib.parse.quote(user['uuid'], safe='')}@"
            f"{address}:{port}?{_params(query)}#{name}")


def tuic_uri(user, node, profile, prefix=''):
    """TUIC v5 as the ``tuic://`` link its clients import.

    Two spellings of "the certificate is self-signed" are in use — ``allow_insecure``
    is what v2rayN writes and reads, ``insecure`` is what the sing-box based
    clients use — so both are sent: unknown query parameters are ignored, and
    sending only one would leave the other family of clients rejecting a
    self-signed certificate that can never be anything else.
    """
    address, port = tp.node_address(node, profile)
    core = tp.core_config() or {}
    uuid = urllib.parse.quote(user['uuid'], safe='')
    query = {'allow_insecure': '1', 'insecure': '1', 'sni': core.get('sni') or address,
             'congestion_control': 'bbr'}
    name = urllib.parse.quote(label(user, node, profile, prefix), safe='')
    return f"tuic://{uuid}:{uuid}@{address}:{port}?{_params(query)}#{name}"


def vmess_uri(user, node, profile, prefix=''):
    """VMess has no parameterised URI: it is one base64 JSON blob."""
    address, port = tp.node_address(node, profile)
    reality = profile.get('security') == 'reality'
    keys = tp.reality_keys() or {}
    payload = {
        'v': '2', 'ps': label(user, node, profile, prefix), 'add': address, 'port': str(port),
        'id': user['uuid'], 'aid': '0', 'scy': 'auto', 'type': 'none',
        'net': 'tcp' if reality else 'ws', 'host': tp.node_host_header(node) if not reality else '',
        'path': '' if reality else profile['path'],
        'tls': 'reality' if reality else 'tls',
        'sni': tp.REALITY_SNI if reality else tp.node_sni(node),
        'fp': user.get('fingerprint') or 'chrome',
    }
    if reality:
        payload['pbk'] = keys.get('public_key', '')
        payload['sid'] = keys.get('short_id', '')
    if user.get('frag_len'):
        payload['fragment'] = user['frag_len']
    return 'vmess://' + base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()


URI_BUILDERS = {'vless': vless_uri, 'trojan': trojan_uri, 'vmess': vmess_uri, 'ss': ss_uri,
                'hy2': hy2_uri, 'anytls': anytls_uri, 'tuic': tuic_uri}


def uri(user, node, profile, prefix=''):
    builder = URI_BUILDERS.get(profile['protocol'])
    if not builder:
        raise ValueError('no share link for transport: ' + profile['id'])
    return builder(user, node, profile, prefix)


# ------------------------------------------------------------------ json formats
def _tls_block(node, profile, user):
    if profile.get('security') == 'reality':
        keys = tp.reality_keys() or {}
        return {'enabled': True, 'server_name': tp.REALITY_SNI, 'insecure': False,
                'utls': {'enabled': True, 'fingerprint': user.get('fingerprint') or 'chrome'},
                'reality': {'enabled': True, 'public_key': keys.get('public_key', ''),
                            'short_id': keys.get('short_id', '')}}
    return {'enabled': bool(node.get('tls')), 'server_name': tp.node_sni(node), 'insecure': False,
            'utls': {'enabled': True, 'fingerprint': user.get('fingerprint') or 'chrome'}}


def _transport(node, profile):
    network = profile['network']
    if network == 'ws':
        return {'type': 'ws', 'path': profile['path'], 'headers': {'Host': tp.node_host_header(node)}}
    if network == 'grpc':
        return {'type': 'grpc', 'service_name': profile['path'].lstrip('/')}
    if network == 'httpupgrade':
        return {'type': 'httpupgrade', 'path': profile['path'], 'host': tp.node_host_header(node)}
    if network == 'xhttp':
        return {'type': 'xhttp', 'path': profile['path'], 'mode': 'auto'}
    return None


def singbox(user, node, profile, prefix=''):
    address, port = tp.node_address(node, profile)
    protocol = profile['protocol']
    if profile.get('group') == tp.CORE:
        # A hosted protocol terminates its own TLS on this server, one credential
        # per user, so the outbound names the user's uuid instead of an endpoint
        # password. ``insecure`` is unavoidable: the certificate is self-signed. 
        core = tp.core_config() or {}
        tls = {'enabled': True, 'server_name': core.get('sni') or address, 'insecure': True}
        entry = {'tag': label(user, node, profile, prefix), 'server': address, 'server_port': port}
        if protocol == tp.ANYTLS:
            entry.update({'type': 'anytls', 'password': user['uuid'],
                          'tls': {**tls, 'utls': {'enabled': True,
                                                  'fingerprint': user.get('fingerprint') or 'chrome'}}})
            return entry
        entry.update({'type': 'tuic', 'uuid': user['uuid'], 'password': user['uuid'],
                      'congestion_control': 'bbr', 'udp_relay_mode': 'native', 'tls': tls})
        return entry
    if profile.get('group') == tp.HY2:
        hy2 = tp.hysteria_config() or {}
        entry = {'tag': label(user, node, profile, prefix), 'type': 'hysteria2',
                 'server': address, 'server_port': port, 'password': hy2.get('password') or '',
                 'tls': {'enabled': True, 'server_name': hy2.get('sni') or address,
                         'insecure': bool(hy2.get('insecure'))}}
        if hy2.get('obfs'):
            entry['obfs'] = {'type': hy2['obfs'], 'password': hy2.get('obfs_password') or ''}
        return entry
    if protocol == 'ss':
        # sing-box has no ``transport`` field on a Shadowsocks outbound: the
        # WebSocket edge belongs in the plugin options, and an unknown field
        # there would make the whole subscription fail to import.
        return {'tag': label(user, node, profile, prefix), 'type': 'shadowsocks',
                'server': address, 'server_port': port,
                'method': profile.get('method') or tp.SS_METHOD,
                'password': tp.ss_key(user, profile),
                'plugin': 'v2ray-plugin',
                'plugin_opts': tp.ss_plugin_opts(profile, tp.node_host_header(node), leading_name=False)}
    entry = {'tag': label(user, node, profile, prefix), 'server': address, 'server_port': port,
             'tls': _tls_block(node, profile, user)}
    transport = _transport(node, profile)
    if transport:
        entry['transport'] = transport
    if protocol == 'vless':
        entry.update({'type': 'vless', 'uuid': user['uuid'], 'flow': ''})
    elif protocol == 'vmess':
        entry.update({'type': 'vmess', 'uuid': user['uuid'], 'alter_id': 0, 'security': 'auto'})
    elif protocol == 'trojan':
        entry.update({'type': 'trojan', 'password': user['uuid']})
    else:
        raise ValueError('unsupported protocol: ' + protocol)
    return entry


def clash(user, node, profile, prefix=''):
    address, port = tp.node_address(node, profile)
    if profile.get('group') == tp.CORE:
        core = tp.core_config() or {}
        entry = {'name': label(user, node, profile, prefix), 'server': address, 'port': port,
                 'udp': True, 'sni': core.get('sni') or address, 'skip-cert-verify': True,
                 'client-fingerprint': user.get('fingerprint') or 'chrome'}
        if profile['protocol'] == tp.ANYTLS:
            entry.update({'type': 'anytls', 'password': user['uuid']})
        else:
            entry.update({'type': 'tuic', 'uuid': user['uuid'], 'password': user['uuid'],
                          'congestion-controller': 'bbr', 'udp-relay-mode': 'native'})
        return entry
    if profile.get('group') == tp.HY2:
        hy2 = tp.hysteria_config() or {}
        entry = {'name': label(user, node, profile, prefix), 'type': 'hysteria2',
                 'server': address, 'port': port, 'password': hy2.get('password') or '',
                 'sni': hy2.get('sni') or address,
                 'skip-cert-verify': bool(hy2.get('insecure'))}
        if hy2.get('obfs'):
            entry['obfs'] = hy2['obfs']
            entry['obfs-password'] = hy2.get('obfs_password') or ''
        return entry
    if profile['protocol'] == 'ss':
        # mihomo takes the WebSocket edge as ``plugin-opts`` (network: ws on an
        # ss proxy is not a thing, and would silently dial plain Shadowsocks).
        return {'name': label(user, node, profile, prefix), 'type': 'ss', 'server': address,
                'port': port, 'udp': True,
                'cipher': profile.get('method') or tp.SS_METHOD,
                'password': tp.ss_key(user, profile),
                'plugin': 'v2ray-plugin',
                'plugin-opts': {'mode': 'websocket', 'tls': True,
                                'host': tp.node_host_header(node),
                                'path': profile.get('path') or ''},
                'client-fingerprint': user.get('fingerprint') or 'chrome'}
    reality = profile.get('security') == 'reality'
    keys = tp.reality_keys() or {}
    entry = {'name': label(user, node, profile, prefix), 'server': address, 'port': port,
             'udp': True, 'client-fingerprint': user.get('fingerprint') or 'chrome'}
    if reality:
        entry.update({'tls': True, 'servername': tp.REALITY_SNI, 'network': profile['network'],
                      'reality-opts': {'public-key': keys.get('public_key', ''), 'short-id': keys.get('short_id', '')}})
    else:
        entry.update({'tls': bool(node.get('tls')), 'servername': tp.node_sni(node),
                      'network': 'ws', 'ws-opts': {'path': profile['path'],
                                                   'headers': {'Host': tp.node_host_header(node)}}})
    protocol = profile['protocol']
    if protocol == 'vless':
        entry.update({'type': 'vless', 'uuid': user['uuid']})
    elif protocol == 'vmess':
        entry.update({'type': 'vmess', 'uuid': user['uuid'], 'alterId': 0, 'cipher': 'auto'})
    elif protocol == 'trojan':
        entry.update({'type': 'trojan', 'password': user['uuid']})
    else:
        raise ValueError('unsupported protocol: ' + protocol)
    return entry


# ------------------------------------------------------------- clash profile
# Clash/Mihomo is handed a **whole profile**, not a fragment. That is the format:
# every one of its clients (Clash Verge, Mihomo, FlClash, Bettbox, Stash) imports
# a YAML config carrying ``proxies``, ``proxy-groups``, ``rules`` and ``dns`` — a
# bare ``proxies:`` array is not a config, which is why the link used to look
# broken. The shape below is the one working providers publish: one selector on
# top, one latency group per country (the same country the node *names* carry),
# an automatic group over everything, and local/Iranian traffic going direct.
SELECT_GROUP = '🚀 انتخاب مسیر'
AUTO_GROUP = '♻️ خودکار (کمترین تاخیر)'
ORIGIN_GROUP = '🏠 سرور اصلی'
PROBE_URL = 'https://www.gstatic.com/generate_204'

# A short, curated block list. It is rendered only when the user's own
# «بلاک تبلیغات» / «محتوای بزرگسال» switches are on, so a profile that never
# asked for filtering does not silently drop a domain.
AD_DOMAINS = (
    'doubleclick.net', 'googlesyndication.com', 'googleadservices.com',
    'adservice.google.com', 'ads.yahoo.com', 'amazon-adsystem.com',
    'criteo.com', 'taboola.com', 'outbrain.com', 'scorecardresearch.com',
    'adcolony.com', 'applovin.com', 'adjust.com', 'ads-twitter.com',
)
BLOCKED_CONTENT = (
    'pornhub.com', 'xvideos.com', 'xnxx.com', 'xhamster.com', 'redtube.com',
    'youporn.com', 'onlyfans.com', 'chaturbate.com',
)


def _flag_on(user, key):
    """Whether a per-user switch is really on (it arrives as 0/1 or as text)."""
    value = (user or {}).get(key)
    if isinstance(value, str):
        return value.strip().lower() not in ('', '0', 'false', 'no', 'off')
    return bool(value)


def _group_name(node):
    """The proxy group a node belongs to: its country, or this server.

    A multi-location subscription is only usable when its entries are grouped the
    same way they are named, so the group carries the flag and Persian name the
    node labels do (``app/subscriptions/flags.py``) — and it follows the panel's
    «پرچم کشور روی نام نودها» switch, because the names inside it do.
    """
    location = tp.node_location(node)
    if not location:
        return ORIGIN_GROUP
    from app.subscriptions import flags
    name = flags.name(location) or location.upper()
    return f'{tp.node_flag(node)} {name}'.strip()


def _dns_block():
    """A resolver setup that works from inside Iran and behind a CDN.

    ``fake-ip`` is what the clients in this market already run; the local
    resolvers answer the direct routes quickly, the encrypted fallbacks resolve
    the proxied half, and a resolved address inside Iran follows the fallback
    answer instead of a faked one.
    """
    return {
        'enable': True,
        'ipv6': False,
        'enhanced-mode': 'fake-ip',
        'fake-ip-range': '198.18.0.1/16',
        'fake-ip-filter': ['*.lan', '*.local', '+.ir'],
        'default-nameserver': ['223.5.5.5', '1.1.1.1'],
        'nameserver': ['https://dns.alidns.com/dns-query', 'https://doh.pub/dns-query'],
        'fallback': ['https://1.1.1.1/dns-query', 'https://8.8.8.8/dns-query'],
        'fallback-filter': {'geoip': True, 'geoip-code': 'IR'},
    }


def _rules(user):
    """Local and Iranian traffic direct, filtered domains rejected, the rest proxied."""
    items = ['GEOIP,LAN,DIRECT', 'GEOIP,IR,DIRECT', 'DOMAIN-SUFFIX,ir,DIRECT']
    if _flag_on(user, 'block_ads'):
        items += [f'DOMAIN-SUFFIX,{domain},REJECT' for domain in AD_DOMAINS]
    if _flag_on(user, 'block_porn'):
        items += [f'DOMAIN-SUFFIX,{domain},REJECT' for domain in BLOCKED_CONTENT]
    return items + [f'MATCH,{SELECT_GROUP}']


def clash_document(user, nodes=None, profiles=None, prefix='', include_extras=True):
    """The whole Clash/Mihomo profile as a plain document.

    Kept separate from its YAML text so the panel and the tests can assert on the
    structure instead of parsing the serialisation back. The defaults mirror what
    :func:`render` resolves, so ``clash_document(user)`` is exactly the document a
    ``?target=clash`` request returns.
    """
    if nodes is None:
        nodes = active_nodes(scope=tp.user_scope(user))
    if profiles is None:
        profiles = profiles_for('all', tp.user_protocols(user))
    proxies, members, used = [], [], set()
    for node, profile in entry_pairs(user, nodes, profiles, include_extras):
        entry = clash(user, node, profile, prefix)
        name = str(entry.get('name') or '').strip()
        if not name:
            continue
        # A Clash profile silently keeps the *last* proxy of a repeated name, so
        # a collision would drop a node without saying anything.
        if name in used:
            suffix = 2
            while f'{name} {suffix}' in used:
                suffix += 1
            name = f'{name} {suffix}'
            entry['name'] = name
        used.add(name)
        proxies.append(entry)
        members.append((_group_name(node), name))
    if not proxies:
        raise ValueError('no proxies for this subscription')
    grouped = {}
    for group, name in members:
        grouped.setdefault(group, []).append(name)
    country_groups = [{'name': group, 'type': 'url-test', 'url': PROBE_URL,
                       'interval': 600, 'tolerance': 80, 'proxies': names}
                      for group, names in grouped.items()]
    return {
        'mixed-port': 7890,
        'allow-lan': False,
        'bind-address': '127.0.0.1',
        'mode': 'rule',
        'log-level': 'info',
        'ipv6': False,
        'unified-delay': True,
        'external-controller': '127.0.0.1:9090',
        'secret': str((user or {}).get('uuid') or ''),
        'dns': _dns_block(),
        'proxies': proxies,
        'proxy-groups': [
            {'name': SELECT_GROUP, 'type': 'select',
             'proxies': [AUTO_GROUP] + [group['name'] for group in country_groups] + ['DIRECT']},
            {'name': AUTO_GROUP, 'type': 'url-test', 'url': PROBE_URL,
             'interval': 300, 'tolerance': 50,
             'proxies': [proxy['name'] for proxy in proxies]},
        ] + country_groups,
        'rules': _rules(user),
        'sniffer': {'enable': True, 'force-dns-mapping': True, 'parse-pure-ip': True,
                    'override-destination': True,
                    'sniff': {'HTTP': {'ports': [80, '8080-8880']}, 'TLS': {'ports': [443]},
                              'QUIC': {'ports': [443]}}},
        'profile': {'store-selected': True, 'store-fake-ip': True},
    }


def clash_profile(user, nodes=None, profiles=None, prefix='', include_extras=True):
    """The Clash/Mihomo subscription body: the document above, serialised as YAML."""
    return yamlout.dump(clash_document(user, nodes, profiles, prefix, include_extras))


def xray(user, node, profile, prefix=''):
    address, port = tp.node_address(node, profile)
    protocol = profile['protocol']
    if profile.get('group') == tp.CORE:
        # Xray has no AnyTLS or TUIC outbound either: they are served by sing-box
        # and mihomo only, which is exactly why they are hosted ones now.
        raise ValueError(f'xray cannot express {protocol}')
    if protocol == 'hy2':
        # Xray has no Hysteria2 outbound at all: QUIC terminates in a hysteria2
        # server, so the Xray JSON subscription simply omits this entry instead of
        # emitting a line every Xray client would reject.
        raise ValueError('xray cannot express hysteria2')
    if protocol == 'vless':
        settings = {'vnext': [{'address': address, 'port': port, 'users': [
            {'id': user['uuid'], 'encryption': 'none', 'flow': '', 'level': 0}]}]}
    elif protocol == 'vmess':
        settings = {'vnext': [{'address': address, 'port': port, 'users': [
            {'id': user['uuid'], 'alterId': 0, 'security': 'auto', 'level': 0}]}]}
    elif protocol == 'trojan':
        settings = {'servers': [{'address': address, 'port': port, 'password': user['uuid'], 'level': 0}]}
    elif protocol == 'ss':
        settings = {'servers': [{'address': address, 'port': port,
                                 'method': profile.get('method') or tp.SS_METHOD,
                                 'password': tp.ss_key(user, profile), 'level': 0}]}
    else:
        raise ValueError('unsupported protocol: ' + protocol)
    stream = {'network': profile['network'], 'security': profile.get('security') or 'none'}
    transport = _transport(node, profile)
    if profile['network'] == 'tcp':
        stream['tcpSettings'] = {}
    elif profile['network'] == 'ws':
        stream['wsSettings'] = transport and {'path': profile['path'],
                                             'headers': {'Host': tp.node_host_header(node)}}
    elif profile['network'] == 'grpc':
        stream['grpcSettings'] = {'serviceName': profile['path'].lstrip('/')}
    elif profile['network'] == 'httpupgrade':
        stream['httpupgradeSettings'] = {'path': profile['path'], 'host': tp.node_host_header(node)}
    elif profile['network'] == 'xhttp':
        stream['xhttpSettings'] = {'path': profile['path'], 'mode': 'auto'}
    if profile.get('security') == 'reality':
        keys = tp.reality_keys() or {}
        stream['realitySettings'] = {'serverName': tp.REALITY_SNI, 'publicKey': keys.get('public_key', ''),
                                     'shortId': keys.get('short_id', ''),
                                     'fingerprint': user.get('fingerprint') or 'chrome', 'spiderX': '/'}
    elif node.get('tls'):
        stream['tlsSettings'] = {'serverName': tp.node_sni(node), 'allowInsecure': False,
                                 'fingerprint': user.get('fingerprint') or 'chrome'}
    return {'tag': label(user, node, profile, prefix) or profile['id'],
            'protocol': {'ss': 'shadowsocks'}.get(protocol, protocol),
            'settings': settings, 'streamSettings': stream}


# ------------------------------------------------------------------------ nodes
def active_nodes(include_unhealthy=False, location='', scope=''):
    """Enabled nodes ordered by their last measured ping.

    Two filters are applied before health: ``location`` (one country, from the
    ``?location=`` parameter) and ``scope`` (``all`` / ``multi`` / ``origin`` /
    a country, from the user's own setting or ``?scope=``). Together they are how
    "only the US edge" or "only my own server" becomes an ordinary subscription.

    Measured health is a *sort key*, not a filter — with one exception: a
    clean-IP node whose probe failed is not published at all, because a client
    would dial a dead address. The Railway origin keeps its hostname resolution
    even after a transient probe failure, so it always stays published and
    no subscription can ever come back empty.

    * measured nodes come first, fastest ping first;
    * unmeasured nodes follow, with the Railway origin ahead of fresh
      Cloudflare IPs;
    * failed Railway nodes go last; failed Cloudflare IPs are dropped.
    """
    rows_ = rows("SELECT * FROM nodes WHERE enabled=1 ORDER BY "
                 "CASE WHEN latency_ms IS NULL THEN 1 WHEN latency_ms < 0 THEN 2 ELSE 0 END, "
                 "latency_ms ASC, CASE WHEN kind='railway' THEN 0 ELSE 1 END, name ASC")
    # Multi-location: a subscription can ask for one location only, which is what
    # makes "just the German edge" (or "just the Iranian one") a normal URL.
    wanted_location = str(location or '').strip().lower()
    if wanted_location:
        rows_ = [n for n in rows_ if tp.node_location(n) == wanted_location]
    # Node scope: which slice of the catalog this subscription may carry at all.
    if scope:
        from app.subscriptions import scope as scopes
        rows_ = scopes.filter_nodes(rows_, scope)
    if include_unhealthy:
        return rows_
    # The one health filter: never advertise a dead clean IP. The Railway
    # origin keeps its hostname (DNS still resolves during a transient
    # failure), so it always stays published.
    keep = [n for n in rows_
            if n['latency_ms'] is None or float(n['latency_ms']) >= 0 or n['kind'] == 'railway']
    # Last resort: a catalog where everything measured bad still publishes the
    # first entry so a client refresh never 404s.
    if not keep:
        keep = rows_[:1]
    return keep


def profiles_for(target, protocols=None):
    """Which transport profiles a target asks for.

    ``protocols`` is the user's enabled protocol set: a target that names a
    protocol this user does not have is a real error rather than a silently
    empty subscription, so the caller can say why.
    """
    wanted = None if protocols is None else set(protocols)
    exact = tp.find(target)
    if exact:
        if wanted is not None and exact['protocol'] not in wanted:
            raise ValueError(_disabled_message(exact['protocol']))
        return [exact]
    available = tp.available_profiles(protocols)
    if target in ('auto', 'all'):
        return available
    if target in PROTOCOL_TARGETS:
        if wanted is not None and target not in wanted:
            raise ValueError(_disabled_message(target))
        return [p for p in available if p['protocol'] == target]
    if target in TRANSPORT_TARGETS:
        if target == 'ws':
            return [p for p in available if p['network'] == 'ws' and p['group'] == tp.EDGE]
        if target == 'cdn':
            return [p for p in available if p['id'].endswith('-cdn')]
        if target == 'reality':
            return [p for p in available if p['group'] == tp.DIRECT]
        if target == 'warp':
            return [p for p in available if p['group'] == tp.WARP]
        return [p for p in available if p['network'] == target]
    raise ValueError('unsupported target: ' + str(target))


def _disabled_message(protocol):
    return f"پروتکل {str(protocol).upper()} برای این کاربر فعال نشده است"


def entry_pairs(user, nodes, profiles, include_extras=True):
    """The (node × profile) pairs a subscription really contains.

    This is the **only** place the per-user config cap is applied, so the line
    formats, sing-box, Clash and Xray hand out exactly the same set and a client
    that imports several of them sees one consistent list.

    Two kinds of entry are not tied to the node list: the external Hysteria2
    endpoint (one shared endpoint) and the hosted protocols (one node — this
    server — with a raw port each). Both are appended once and only when the
    caller wants them, because a per-node subscription must not drag them in.
    """
    extra_groups = (tp.HY2, tp.CORE)
    node_profiles = [p for p in profiles if p.get('group') not in extra_groups]
    pairs = [(n, p) for n in nodes for p in node_profiles]
    if include_extras:
        pairs += [(hy2_node(), p) for p in profiles if p.get('group') == tp.HY2]
        # A hosted protocol answers on *this* server, so it belongs to the origin
        # slice and follows the user's node scope like the origin node does: a
        # user scoped to «فقط نودهای مولتی‌لوکیشن» or «فقط آمریکا» must not be
        # handed a raw-port node on the panel's own address.
        core = core_node()
        if scopes.matches(core, tp.user_scope(user)):
            pairs += [(core, p) for p in profiles if p.get('group') == tp.CORE]
    limit = tp.user_max_configs(user)
    return pairs[:limit] if limit else pairs


def _json_subscription(user, nodes, profiles, kind, prefix='', include_extras=True):
    # Clash/Mihomo gets a whole profile rather than a proxy list: its clients
    # import a config (proxies + groups + rules), not a fragment of one.
    if kind == 'clash':
        return clash_profile(user, nodes, profiles, prefix, include_extras)
    builder = {'singbox': singbox, 'xray': xray}[kind]
    entries = []
    for node, profile in entry_pairs(user, nodes, profiles, include_extras):
        # Xray has neither a hysteria2 nor an AnyTLS/TUIC outbound: those entries
        # are left out rather than emitted as a line every Xray client rejects.
        if kind == 'xray' and profile.get('group') in (tp.HY2, tp.CORE):
            continue
        entries.append(builder(user, node, profile, prefix))
    return json.dumps({'outbounds': entries}, ensure_ascii=False, indent=2)


def render(user, base, target, nodes=None, prefix='', include_extras=True):
    target = normalize_target(target)
    # With no explicit node list the user's own scope decides what is published,
    # so every caller (the subscription routes *and* the panel's previews) hands
    # out exactly the same set — the scope cannot be applied in one place only.
    nodes = nodes if nodes is not None else active_nodes(scope=tp.user_scope(user))
    if not nodes:
        raise ValueError('no enabled nodes available')

    # Every user gets the full matrix by default; a narrowed protocol set only
    # ever removes entries this user explicitly turned off.
    protocols = tp.user_protocols(user)
    # A client id resolves to the format that client imports best.
    resolved = CLIENT_FORMATS.get(target, target)
    if resolved in ('singbox', 'clash', 'xray'):
        return _json_subscription(user, nodes, profiles_for('all', protocols), resolved, prefix, include_extras)
    if resolved == 'json':
        return json.dumps({'transports': [{'id': p['id'], 'tag': p['tag'], 'protocol': p['protocol'],
                                          'network': p['network'], 'group': p['group']}
                                         for p in tp.available_profiles()],
                           'nodes': [{'name': n['name'], 'kind': n['kind'], 'server': n['server'],
                                      'port': n['port'], 'tls': bool(n['tls']), 'sni': n['sni'],
                                      'host': n['host'], 'latency_ms': n['latency_ms']} for n in nodes]},
                          ensure_ascii=False, indent=2)

    profiles = profiles_for('auto' if resolved in ('base64', 'auto', 'all') else resolved, protocols)
    line_profiles = [p for p in profiles if p['protocol'] in tp.URI_PROTOCOLS]
    if not line_profiles:
        if profiles:
            # A profile set with no link form at all (Reality only, when it is
            # the sole published transport) still returns something a client can
            # import instead of a 400 that reads as "broken".
            return _json_subscription(user, nodes, profiles, 'singbox', prefix, include_extras)
        raise ValueError(f"{target}: روی این نصب هنوز منتشر نشده است "
                         f"(نیازمند پورت TCP اختصاصی یا فعال‌سازی WARP)")
    # Node-major and fastest-first: the first entries of the subscription are the
    # fastest node's full transport set, which is what a client shows on top.
    # Every entry name carries its country flag, so a mixed-location list stays
    # readable in a client that shows nothing but the remark.
    lines = [uri(user, n, p, prefix) for n, p in entry_pairs(user, nodes, line_profiles, include_extras)]
    body = '\n'.join(lines) + '\n'
    if resolved == 'base64':
        return base64.b64encode(body.encode()).decode()
    return body


def node_links(user, node, prefix=''):
    """Every raw link combination for ONE node (used by the panel drawers).

    A hosted protocol answers on this deployment, not on an edge location, so it
    only appears in the drawer of the origin node — offering an AnyTLS link under
    a Cloudflare node's name would dial a different server than the card says.
    """
    profiles = [p for p in tp.available_profiles(tp.user_protocols(user))
                if p.get('group') != tp.CORE or _is_origin(node)]
    entries = []
    for profile in profiles:
        entry = {'id': profile['id'], 'tag': profile['tag'], 'protocol': profile['protocol'],
                 'network': profile['network'], 'group': profile['group'],
                 'security': profile.get('security') or 'tls'}
        if profile['protocol'] in tp.URI_PROTOCOLS:
            entry['link'] = uri(user, node, profile, prefix)
        entry['singbox'] = singbox(user, node, profile, prefix)
        entry['clash'] = clash(user, node, profile, prefix)
        # Neither Hysteria2 nor a hosted protocol has an Xray outbound, so that
        # column stays empty for them rather than failing the whole drawer.
        entry['xray'] = (None if profile.get('group') in (tp.HY2, tp.CORE)
                         else xray(user, node, profile, prefix))
        entries.append(entry)
    primary = next((p for p in entries if p['id'] == 'vless-ws'), entries[0] if entries else None)
    return {
        'name': node.get('name'), 'label': tp.node_label(node), 'flag': tp.node_flag(node),
        'kind': node.get('kind'), 'server': node.get('server'),
        'port': int(node.get('port') or 443), 'tls': bool(node.get('tls')),
        'sni': node.get('sni'), 'host': node.get('host'), 'latency_ms': node.get('latency_ms'),
        'enabled': bool(node.get('enabled', 1)),
        # Which edge source (location/provider) this node belongs to, so a user can
        # be handed "just the German edge" and the panel can group the nodes.
        'location': tp.node_location(node), 'provider': tp.node_provider(node),
        # Whether the user's own node scope publishes this node at all. The panel
        # badges the rows it excludes (and never offers them as a working link),
        # which is what keeps the admin's link drawer honest for a scoped user.
        'in_scope': scopes.matches(node, tp.user_scope(user)),
        'scope': tp.user_scope(user),
        'profiles': entries,
        'links': {
            'primary': primary and primary.get('link') or '',
            'vless': next((e['link'] for e in entries if e['id'] == 'vless-ws' and e.get('link')), ''),
            'trojan': next((e['link'] for e in entries if e['id'] == 'trojan-ws' and e.get('link')), ''),
            'vmess': next((e['link'] for e in entries if e['id'] == 'vmess-ws' and e.get('link')), ''),
            'singbox': primary and primary['singbox'],
            'clash': primary and primary['clash'],
            'xray': primary and primary['xray'],
        },
    }
