import base64, json, urllib.parse
from app.db import rows


def _node_host(node):
    return node.get('server') or ''


def _query(node, user):
    q = {
        'encryption': 'none',
        'security': 'tls' if int(node.get('tls') or 0) else 'none',
        'type': 'ws',
        'host': node.get('host') or node.get('server') or '',
        'path': '/ws/vless' if user.get('protocol','vless') == 'vless' else '/ws/trojan',
    }
    if node.get('sni'):
        q['sni'] = node['sni']
    if user.get('frag_len'):
        q['fragment'] = user['frag_len']
    if user.get('fingerprint'):
        q['fp'] = user['fingerprint']
    return q


def vless(user, node):
    q = _query(node, user)
    return f"vless://{user['uuid']}@{_node_host(node)}:{int(node.get('port') or 443)}?{urllib.parse.urlencode(q)}#{urllib.parse.quote(user['username'] + ' · ' + node['name'])}"


def trojan(user, node):
    q = {'security': 'tls', 'type': 'ws', 'host': node.get('host') or node.get('server') or '', 'path': '/ws/trojan'}
    if node.get('sni'):
        q['sni'] = node['sni']
    return f"trojan://{user['uuid']}@{_node_host(node)}:{int(node.get('port') or 443)}?{urllib.parse.urlencode(q)}#{urllib.parse.quote(user['username'] + ' · ' + node['name'])}"


def singbox(user, node):
    protocol = user.get('protocol', 'vless')
    p = {
        'type': protocol,
        'tag': f"{user['username']} · {node['name']}",
        'server': _node_host(node),
        'server_port': int(node.get('port') or 443),
    }
    if protocol == 'vless':
        p['uuid'] = user['uuid']
    else:
        p['password'] = user['uuid']
    p['tls'] = {'enabled': bool(int(node.get('tls') or 0)), 'server_name': node.get('sni') or node.get('host') or node.get('server')}
    p['transport'] = {'type': 'ws', 'path': '/ws/vless' if user.get('protocol','vless') == 'vless' else '/ws/trojan', 'headers': {'Host': node.get('host') or node.get('server') or ''}}
    return p


def clash(user, node):
    protocol = user.get('protocol', 'vless')
    p = {
        'name': f"{user['username']} · {node['name']}",
        'type': protocol,
        'server': _node_host(node),
        'port': int(node.get('port') or 443),
        'uuid': user['uuid'] if protocol == 'vless' else None,
        'password': user['uuid'] if protocol == 'trojan' else None,
        'tls': bool(int(node.get('tls') or 0)),
        'network': 'ws',
        'ws-opts': {'path': '/ws/vless' if user.get('protocol','vless') == 'vless' else '/ws/trojan', 'headers': {'Host': node.get('host') or node.get('server') or ''}},
    }
    if protocol == 'trojan': p.pop('uuid', None)
    else: p.pop('password', None)
    if node.get('sni'):
        p['servername'] = node['sni']
    return p


def active_nodes(include_unhealthy=False):
    sql = 'SELECT * FROM nodes WHERE enabled=1'
    if not include_unhealthy:
        sql += ' AND (kind=\'railway\' OR latency_ms IS NOT NULL)'
    sql += " ORDER BY CASE WHEN kind='railway' THEN 0 ELSE 1 END, CASE WHEN latency_ms IS NULL THEN 1 ELSE 0 END, latency_ms ASC, name ASC"
    return rows(sql)


def render(user, base, target, nodes=None):
    target = target.lower()
    nodes = nodes if nodes is not None else active_nodes()
    if not nodes:
        raise ValueError('no enabled nodes available')

    if target == 'vless':
        if user.get('protocol', 'vless') != 'vless':
            raise ValueError('user protocol is not vless')
        return '\n'.join(vless(user, n) for n in nodes) + '\n'
    if target == 'trojan':
        if user.get('protocol') != 'trojan':
            raise ValueError('user protocol is not trojan')
        return '\n'.join(trojan(user, n) for n in nodes) + '\n'
    if target in {'base64', 'vless-base64'}:
        if user.get('protocol', 'vless') != 'vless':
            raise ValueError('base64 target currently supports vless users')
        payload = '\n'.join(vless(user, n) for n in nodes) + '\n'
        return base64.b64encode(payload.encode()).decode()
    if target in {'singbox', 'sing-box'}:
        return json.dumps({'outbounds': [singbox(user, n) for n in nodes]}, ensure_ascii=False, indent=2)
    if target == 'clash':
        return json.dumps({'proxies': [clash(user, n) for n in nodes]}, ensure_ascii=False, indent=2)
    if target == 'json':
        return json.dumps({'nodes': [
            {'name': n['name'], 'kind': n['kind'], 'server': n['server'], 'port': n['port'], 'tls': bool(n['tls']), 'sni': n['sni'], 'host': n['host'], 'latency_ms': n['latency_ms']}
            for n in nodes
        ]}, ensure_ascii=False, indent=2)
    raise ValueError('unsupported target')
