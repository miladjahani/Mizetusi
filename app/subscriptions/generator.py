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
PROTOCOL_TARGETS = ('vless', 'trojan', 'vmess', 'ss')
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
    parts = [p for p in (prefix or '', node.get('name', '')) if p]
    if profile and profile.get('tag'):
        parts.append(profile['tag'])
    return ' · '.join(parts)


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


URI_BUILDERS = {'vless': vless_uri, 'trojan': trojan_uri, 'vmess': vmess_uri, 'ss': ss_uri}


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


def xray(user, node, profile, prefix=''):
    address, port = tp.node_address(node, profile)
    protocol = profile['protocol']
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
def active_nodes(include_unhealthy=False):
    """Enabled nodes ordered by their last measured ping.

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


def _json_subscription(user, nodes, profiles, kind, prefix=''):
    builder = {'singbox': singbox, 'clash': clash, 'xray': xray}[kind]
    key = 'proxies' if kind == 'clash' else 'outbounds'
    return json.dumps({key: [builder(user, n, p, prefix) for n in nodes for p in profiles]},
                      ensure_ascii=False, indent=2)


def render(user, base, target, nodes=None, prefix=''):
    target = normalize_target(target)
    nodes = nodes if nodes is not None else active_nodes()
    if not nodes:
        raise ValueError('no enabled nodes available')

    # Every user gets the full matrix by default; a narrowed protocol set only
    # ever removes entries this user explicitly turned off.
    protocols = tp.user_protocols(user)
    # A client id resolves to the format that client imports best.
    resolved = CLIENT_FORMATS.get(target, target)
    if resolved in ('singbox', 'clash', 'xray'):
        return _json_subscription(user, nodes, profiles_for('all', protocols), resolved, prefix)
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
            return _json_subscription(user, nodes, profiles, 'singbox', prefix)
        raise ValueError(f"{target}: روی این نصب هنوز منتشر نشده است "
                         f"(نیازمند پورت TCP اختصاصی یا فعال‌سازی WARP)")
    # Node-major and fastest-first: the first entries of the subscription are the
    # fastest node's full transport set, which is what a client shows on top.
    lines = [uri(user, n, p, prefix) for n in nodes for p in line_profiles]
    body = '\n'.join(lines) + '\n'
    if resolved == 'base64':
        return base64.b64encode(body.encode()).decode()
    return body


def node_links(user, node, prefix=''):
    """Every raw link combination for ONE node (used by the panel drawers)."""
    profiles = tp.available_profiles(tp.user_protocols(user))
    entries = []
    for profile in profiles:
        entry = {'id': profile['id'], 'tag': profile['tag'], 'protocol': profile['protocol'],
                 'network': profile['network'], 'group': profile['group'],
                 'security': profile.get('security') or 'tls'}
        if profile['protocol'] in tp.URI_PROTOCOLS:
            entry['link'] = uri(user, node, profile, prefix)
        entry['singbox'] = singbox(user, node, profile, prefix)
        entry['clash'] = clash(user, node, profile, prefix)
        entry['xray'] = xray(user, node, profile, prefix)
        entries.append(entry)
    primary = next((p for p in entries if p['id'] == 'vless-ws'), entries[0] if entries else None)
    return {
        'name': node.get('name'), 'kind': node.get('kind'), 'server': node.get('server'),
        'port': int(node.get('port') or 443), 'tls': bool(node.get('tls')),
        'sni': node.get('sni'), 'host': node.get('host'), 'latency_ms': node.get('latency_ms'),
        'enabled': bool(node.get('enabled', 1)),
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
