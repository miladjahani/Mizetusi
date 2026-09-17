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
    return f"vless://{user['uuid']}@{_node_host(node)}:{int(node.get('port') or 443)}?{urllib.parse.urlencode(q)}#{urllib.parse.quote(label(user, node, prefix))}"


def trojan(user, node, prefix=''):
    q = {'security': 'tls', 'type': 'ws', 'host': node.get('host') or node.get('server') or '', 'path': '/ws/trojan'}
    if node.get('sni'):
        q['sni'] = node['sni']
    return f"trojan://{user['uuid']}@{_node_host(node)}:{int(node.get('port') or 443)}?{urllib.parse.urlencode(q)}#{urllib.parse.quote(label(user, node, prefix))}"


def singbox(user, node, prefix=''):
    protocol = user.get('protocol', 'vless')
    p = {
        'type': protocol,
        'tag': label(user, node, prefix),
        'server': _node_host(node),
        'server_port': int(node.get('port') or 443),
    }
    if protocol == 'vless':
        p['uuid'] = user['uuid']
    else:
        p['password'] = user['uuid']
    p['tls'] = {'enabled': bool(int(node.get('tls') or 0)), 'server_name': node.get('sni') or node.get('host') or node.get('server')}
    p['transport'] = {'type': 'ws', 'path': _path(user), 'headers': {'Host': node.get('host') or node.get('server') or ''}}
    return p


def clash(user, node, prefix=''):
    protocol = user.get('protocol', 'vless')
    p = {
        'name': label(user, node, prefix),
        'type': protocol,
        'server': _node_host(node),
        'port': int(node.get('port') or 443),
        'uuid': user['uuid'] if protocol == 'vless' else None,
        'password': user['uuid'] if protocol == 'trojan' else None,
        'tls': bool(int(node.get('tls') or 0)),
        'network': 'ws',
        'ws-opts': {'path': _path(user), 'headers': {'Host': node.get('host') or node.get('server') or ''}},
    }
    if protocol == 'trojan': p.pop('uuid', None)
    else: p.pop('password', None)
    if node.get('sni'):
        p['servername'] = node['sni']
    return p


def xray(user, node, prefix=''):
    """Xray-core outbound object for this node, ready to paste into a config."""
    protocol = user.get('protocol', 'vless')
    host = node.get('host') or node.get('server') or ''
    tls_on = bool(int(node.get('tls') or 0))
    stream = {
        'network': 'ws',
        'security': 'tls' if tls_on else 'none',
        'wsSettings': {'path': _path(user), 'headers': {'Host': host}},
    }
    if tls_on:
        stream['tlsSettings'] = {'serverName': node.get('sni') or host, 'allowInsecure': False}
        if user.get('fingerprint'):
            stream['tlsSettings']['fingerprint'] = user['fingerprint']
    settings = (
        {'vnext': [{'address': _node_host(node), 'port': int(node.get('port') or 443),
                    'users': [{'id': user['uuid'], 'encryption': 'none', 'level': 0}]}]}
        if protocol == 'vless' else
        {'servers': [{'address': _node_host(node), 'port': int(node.get('port') or 443), 'password': user['uuid']}]}
    )
    out = {'tag': label(user, node, prefix), 'protocol': protocol, 'settings': settings, 'streamSettings': stream}
    if user.get('frag_len'):
        out['streamSettings']['sockopt'] = {'dialerProxy': 'fragment'}
    return out


def node_links(user, node, prefix=''):
    """Every shareable representation of one user on one node."""
    protocol = user.get('protocol', 'vless')
    return {
        'name': node['name'],
        'kind': node.get('kind'),
        'server': node.get('server'),
        'port': int(node.get('port') or 443),
        'sni': node.get('sni'),
        'host': node.get('host'),
        'latency_ms': node.get('latency_ms'),
        'enabled': bool(int(node.get('enabled') or 0)),
        'links': {
            'vless': vless(user, node, prefix),
            'trojan': trojan(user, node, prefix),
            'singbox': singbox(user, node, prefix),
            'clash': clash(user, node, prefix),
            'xray': xray(user, node, prefix),
            'primary': vless(user, node, prefix) if protocol == 'vless' else trojan(user, node, prefix),
        },
    }


def active_nodes(include_unhealthy=False):
    """Nodes a subscription may hand out, fastest first.

    A probe writes the measured latency and marks a failure with -1, so NEXUS
    orders by real ping and holds failed Cloudflare IPs back. Two deliberate
    exceptions keep the panel from going dark:

    * the Railway origin is always publishable — it is the baseline path, and a
      single transient probe failure must not remove every user's only node;
    * if nothing measured healthy at all, the enabled catalog is returned instead
      of an empty subscription that would 404 on every client refresh.
    """
    sql = ('SELECT * FROM nodes WHERE enabled=1 ORDER BY '
           'CASE WHEN latency_ms IS NULL OR latency_ms < 0 THEN 1 ELSE 0 END, '
           "latency_ms ASC, CASE WHEN kind='railway' THEN 0 ELSE 1 END, name ASC")
    items = rows(sql)
    if include_unhealthy or not items:
        return items

    def measured_ok(node):
        return node['latency_ms'] is not None and float(node['latency_ms']) >= 0

    healthy = [node for node in items if measured_ok(node)]
    baseline = [node for node in items if node['kind'] == 'railway' and not measured_ok(node)]
    picked = healthy + baseline
    return picked or items


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
