"""Subscription rendering.

``active_nodes`` is the one rule the whole panel shares: a subscription hands
out **enabled** nodes, ordered by the last measured ping. Measured health is a
sorting and labelling signal, never a filter — the fallback path exists, so a
transient probe failure can never empty a user's subscription or 404 their
client's refresh. Nodes are generated automatically (Railway origin, Cloudflare
clean IPs); an admin never has to build them by hand.
"""
import base64, json, urllib.parse
from app.db import rows
from app.subscriptions.clients import CLIENT_FORMATS, FORMATS

# Every client format NEXUS can emit, plus one target per known client id so a
# client can subscribe with the exact name it is listed under in the panel.
TARGETS = list(FORMATS) + [cid for cid in CLIENT_FORMATS if cid not in FORMATS]
ALIASES = {
    'v2ray': 'base64', 'v2ray-base64': 'base64', 'base64-sub': 'base64',
    'sing-box': 'singbox', 'singbox-json': 'singbox', 'sb': 'singbox',
    'mihomo': 'clash', 'clash-meta': 'clash', 'yaml': 'clash',
    'xray-json': 'xray', 'xray-outbound': 'xray',
    'vless-all': 'vless', 'trojan-all': 'trojan',
}
# client id -> format (e.g. nekoboxplus -> singbox, bettbox -> base64)
ALIASES.update({cid: fmt for cid, fmt in CLIENT_FORMATS.items() if cid not in FORMATS})


def normalize_target(target):
    value = (target or 'auto').strip().lower()
    value = ALIASES.get(value, value)
    if value not in TARGETS:
        raise ValueError('unsupported target: ' + str(target))
    return value


def label(user, node, prefix=''):
    parts = [p for p in (prefix or '', user.get('username', ''), node.get('name', '')) if p]
    return ' · '.join(parts)


def _node_host(node):
    return node.get('server') or ''


def _path(user):
    return '/ws/vless' if (user.get('protocol') or 'vless') == 'vless' else '/ws/trojan'


def _query(node, user):
    q = {
        'encryption': 'none',
        'security': 'tls' if int(node.get('tls') or 0) else 'none',
        'type': 'ws',
        'host': node.get('host') or node.get('server') or '',
        'path': _path(user),
    }
    if node.get('sni'):
        q['sni'] = node['sni']
    if user.get('frag_len'):
        q['fragment'] = user['frag_len']
    if user.get('fingerprint'):
        q['fp'] = user['fingerprint']
    if user.get('tls_mask'):
        q['tlsMask'] = user['tls_mask']
    return q


def vless(user, node, prefix=''):
    q = _query(node, user)
    params = urllib.parse.urlencode({k: v for k, v in q.items() if v}, safe='')
    return f"vless://{user['uuid']}@{_node_host(node)}:{node['port']}?{params}#{urllib.parse.quote(label(user, node, prefix), safe='')}"


def trojan(user, node, prefix=''):
    q = _query(node, user)
    params = urllib.parse.urlencode({k: v for k, v in q.items() if v}, safe='')
    return f"trojan://{urllib.parse.quote(user['uuid'], safe='')}@{_node_host(node)}:{node['port']}?{params}#{urllib.parse.quote(label(user, node, prefix), safe='')}"


def singbox(user, node, prefix=''):
    tls = bool(int(node.get('tls') or 0))
    return {
        'tag': label(user, node, prefix),
        'type': 'vless' if (user.get('protocol') or 'vless') == 'vless' else 'trojan',
        'server': _node_host(node),
        'server_port': int(node.get('port') or 443),
        'uuid': user['uuid'],
        'flow': '',
        'tls': {'enabled': tls, 'server_name': node.get('sni') or node.get('host') or _node_host(node),
                'insecure': False, 'utls': {'enabled': bool(user.get('fingerprint')), 'fingerprint': user.get('fingerprint') or 'chrome'}},
        'transport': {'type': 'ws', 'path': _path(user), 'headers': {'Host': node.get('host') or _node_host(node)}},
    }


def clash(user, node, prefix=''):
    tls = bool(int(node.get('tls') or 0))
    return {
        'name': label(user, node, prefix),
        'type': 'vless' if (user.get('protocol') or 'vless') == 'vless' else 'trojan',
        'server': _node_host(node),
        'port': int(node.get('port') or 443),
        'uuid': user['uuid'],
        'password': user['uuid'],
        'udp': True,
        'tls': tls,
        'servername': node.get('sni') or node.get('host') or _node_host(node),
        'network': 'ws',
        'ws-opts': {'path': _path(user), 'headers': {'Host': node.get('host') or _node_host(node)}},
        'client-fingerprint': user.get('fingerprint') or 'chrome',
    }


def xray(user, node, prefix=''):
    protocol = (user.get('protocol') or 'vless')
    settings = (
        {'vnext': [{'address': _node_host(node), 'port': int(node.get('port') or 443), 'users': [
            {'id': user['uuid'], 'encryption': 'none', 'flow': '', 'level': 0}]}]}
        if protocol == 'vless' else
        {'servers': [{'address': _node_host(node), 'port': int(node.get('port') or 443), 'password': user['uuid'], 'level': 0}]}
    )
    stream = {
        'network': 'ws',
        'security': 'tls' if int(node.get('tls') or 0) else 'none',
        'wsSettings': {'path': _path(user), 'headers': {'Host': node.get('host') or _node_host(node)}},
    }
    if int(node.get('tls') or 0):
        stream['tlsSettings'] = {'serverName': node.get('sni') or node.get('host') or _node_host(node),
                                 'allowInsecure': False, 'fingerprint': user.get('fingerprint') or 'chrome'}
    return {'tag': label(user, node, prefix) or 'proxy', 'protocol': protocol, 'settings': settings, 'streamSettings': stream}


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


def render(user, base, target, nodes=None, prefix=''):
    target = normalize_target(target)
    if target == 'auto':
        target = user.get('protocol', 'vless')
    nodes = nodes if nodes is not None else active_nodes()
    if not nodes:
        raise ValueError('no enabled nodes available')

    if target == 'vless':
        return '\n'.join(vless(user, n, prefix) for n in nodes) + '\n'
    if target == 'trojan':
        return '\n'.join(trojan(user, n, prefix) for n in nodes) + '\n'
    if target == 'all':
        # Both protocols for every node: Xray accepts the same UUID as the
        # Trojan password, so every node/protocol combination is usable.
        lines = []
        for n in nodes:
            lines.append(vless(user, n, prefix))
            lines.append(trojan(user, n, prefix))
        return '\n'.join(lines) + '\n'
    if target in {'base64', 'vless-base64'}:
        payload = '\n'.join(vless(user, n, prefix) for n in nodes) + '\n'
        return base64.b64encode(payload.encode()).decode()
    if target == 'singbox':
        return json.dumps({'outbounds': [singbox(user, n, prefix) for n in nodes]}, ensure_ascii=False, indent=2)
    if target == 'clash':
        return json.dumps({'proxies': [clash(user, n, prefix) for n in nodes]}, ensure_ascii=False, indent=2)
    if target == 'xray':
        return json.dumps({'outbounds': [xray(user, n, prefix) for n in nodes]}, ensure_ascii=False, indent=2)
    if target == 'json':
        return json.dumps({'nodes': [
            {'name': n['name'], 'kind': n['kind'], 'server': n['server'], 'port': n['port'], 'tls': bool(n['tls']), 'sni': n['sni'], 'host': n['host'], 'latency_ms': n['latency_ms']}
            for n in nodes
        ]}, ensure_ascii=False, indent=2)
    raise ValueError('unsupported target')


def node_links(user, node, prefix=''):
    """Every raw link combination for ONE node (used by the panel drawers)."""
    protocol = (user.get('protocol') or 'vless')
    return {
        'name': node.get('name'), 'kind': node.get('kind'), 'server': node.get('server'),
        'port': int(node.get('port') or 443), 'tls': bool(node.get('tls')),
        'sni': node.get('sni'), 'host': node.get('host'), 'latency_ms': node.get('latency_ms'),
        'enabled': bool(node.get('enabled', 1)),
        'links': {
            'primary': vless(user, node, prefix) if protocol == 'vless' else trojan(user, node, prefix),
            'vless': vless(user, node, prefix),
            'trojan': trojan(user, node, prefix),
            'singbox': singbox(user, node, prefix),
            'clash': clash(user, node, prefix),
            'xray': xray(user, node, prefix),
        },
    }
