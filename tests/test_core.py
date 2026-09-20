import os
os.environ['ENVIRONMENT']='test'; os.environ['SQLITE_PATH']='/tmp/nexus-test.db'; os.environ['DATABASE_URL']='sqlite:////tmp/nexus-test.db'
os.environ['ADMIN_PASSWORD']='x'*20; os.environ['JWT_SECRET']='y'*40
from app.db import init_db
init_db()
from app.proxy.manager import parse
from app.subscriptions.generator import render
from app.users.service import create_user
from app.core.models import UserCreate

def test_proxy_parser(): assert parse('socks5://u:p@example.com:1080')['port']==1080
def test_user_and_subscription():
 from app.db import execute
 execute("DELETE FROM users")
 execute("DELETE FROM nodes")
 from app.nodes import upsert
 upsert('railway-direct','railway','example.com',443,True,'example.com','example.com','railway',{})
 u=create_user(UserCreate(username='alice',protocol='vless')); s=render(u,'https://example.com','vless'); assert s.startswith('vless://') and u['uuid'] in s

def test_subscription_contains_railway_and_cloudflare_nodes():
 from app.db import execute
 from app.nodes import upsert
 execute("DELETE FROM nodes")
 upsert('railway-direct','railway','railway.example.com',443,True,'railway.example.com','railway.example.com','railway',{})
 upsert('cloudflare-01','cloudflare','104.16.1.1',443,True,'worker.example.workers.dev','worker.example.workers.dev','cloudflare-probe',{})
 execute("UPDATE nodes SET latency_ms=25.0 WHERE name='cloudflare-01'")
 u=create_user(UserCreate(username='bob',protocol='vless'))
 text=render(u,'https://railway.example.com','vless')
 assert 'railway.example.com' in text
 assert '104.16.1.1' in text
 assert 'cloudflare-01' in text


def test_default_subscription_carries_every_protocol_by_default():
 """The default subscription must already contain every published transport,
 for every node, without the admin configuring anything."""
 import base64, json
 from app.db import execute
 from app.nodes import upsert
 from app.subscriptions import transports as tp
 execute("DELETE FROM nodes")
 upsert('railway-direct','railway','railway.example.com',443,True,'railway.example.com','railway.example.com','railway',{})
 execute("UPDATE nodes SET latency_ms=12.0 WHERE name='railway-direct'")
 u=create_user(UserCreate(username='matrix',protocol='vless'))

 text=render(u,'https://railway.example.com','auto')
 schemes={line.split('://',1)[0] for line in text.strip().splitlines()}
 assert {'vless','trojan','vmess'} <= schemes
 import urllib.parse
 decoded_text=urllib.parse.unquote(text)  # link params are percent-encoded
 for path in ('/ws/vless', '/cdn/vless', '/ws/trojan', '/cdn/trojan'):
  assert path in decoded_text, path
 # VMess is a base64 JSON blob; its transport must survive the round trip.
 lines=[l for l in text.splitlines() if l.startswith('vmess://')]
 blobs=[json.loads(base64.b64decode(l.split('://',1)[1]).decode()) for l in lines]
 assert {b['path'] for b in blobs} == {'/ws/vmess', '/cdn/vmess'}
 assert all(b['net'] == 'ws' for b in blobs)
 blob=blobs[0]
 assert blob['id'] == u['uuid'] and blob['tls'] == 'tls'
 assert blob['sni'] and blob['host']

 # The base64 container (v2rayNG, Shadowrocket, …) carries the same full matrix.
 # A Shadowsocks-only path never appears here: it lives in the JSON formats.
 decoded=base64.b64decode(render(u,'https://railway.example.com','base64')).decode()
 assert 'vmess://' in decoded and 'trojan://' in decoded and 'vless://' in decoded
 # A single transport can be subscribed to on its own.
 only=render(u,'https://railway.example.com','vless-cdn')
 assert len(only.strip().splitlines()) == 1
 assert '/cdn/vless' in urllib.parse.unquote(only)
 assert 'vmess://' not in only
 # Nodes come fastest-first.
 execute("UPDATE nodes SET latency_ms=-1.0 WHERE name='railway-direct'")
 assert 'railway-direct' in render(u,'https://railway.example.com','auto')

def test_railway_baseline_survives_a_failed_probe():
    # A single failed probe of the only Railway node used to empty every
    # subscription; the baseline is always publishable.
    from app.db import execute
    from app.nodes import upsert, FAILED_LATENCY
    from app.subscriptions.generator import active_nodes
    execute("DELETE FROM nodes")
    upsert('railway-direct', 'railway', 'railway.example.com', 443, True, 'railway.example.com', 'railway.example.com', 'railway', {})
    upsert('cloudflare-01', 'cloudflare', '104.16.1.1', 443, True, 'worker.example.workers.dev', 'worker.example.workers.dev', 'cloudflare-probe', {})
    execute('UPDATE nodes SET latency_ms=? WHERE name=?', (FAILED_LATENCY, 'railway-direct'))
    execute('UPDATE nodes SET latency_ms=? WHERE name=?', (FAILED_LATENCY, 'cloudflare-01'))

    names = [node['name'] for node in active_nodes()]
    assert names == ['railway-direct']

    execute('UPDATE nodes SET latency_ms=12.0 WHERE name=?', ('cloudflare-01',))
    # Healthy nodes come first, the failed baseline still ships after them.
    assert [node['name'] for node in active_nodes()] == ['cloudflare-01', 'railway-direct']


def test_edge_detection_works_before_any_probe_has_succeeded():
    """A fresh deployment has no measured clean IPs yet.

    Edge detection (which is what produces the Cloudflare nodes with no admin
    action) must not depend on a previous probe having already set ok=1.
    """
    import asyncio, time
    from app.db import execute
    from app.nodes import NodeCatalog, NodeProbe, catalog as node_catalog
    execute('DELETE FROM cf_ips')
    execute('INSERT INTO cf_ips(ip,source,enabled,ok,last_seen) VALUES(?,?,1,0,?)',
            ('104.16.0.1', 'test', int(time.time())))
    assert NodeCatalog.clean_candidates(3)[0]['ip'] == '104.16.0.1'

    async def fake_tcp(host, port, timeout=4.0, tls=False, server_hostname=None):
        return 12.5, None

    # tcp is a staticmethod; restoring it as a plain function would rebind self
    # and make every later probe call fail with a TypeError.
    original = NodeProbe.tcp
    NodeProbe.tcp = staticmethod(fake_tcp)
    try:
        NodeCatalog.edge_cache.update({'host': None, 'at': 0.0})
        host = asyncio.run(node_catalog.detect_edge('https://panel.example.com', force=True))
        assert host == 'panel.example.com'
        # The detected edge becomes the SNI/Host of every Cloudflare node.
        node_catalog.sync('https://panel.example.com', None, host)
        assert any(n['kind'] == 'cloudflare' and n['sni'] == 'panel.example.com'
                   for n in node_catalog.list())
    finally:
        NodeProbe.tcp = staticmethod(original)
        NodeCatalog.edge_cache.update({'host': None, 'at': 0.0})
        execute('DELETE FROM cf_ips')
        execute('DELETE FROM nodes')


def test_singbox_contains_all_nodes():
 from app.db import execute
 execute("DELETE FROM nodes")
 from app.nodes import upsert
 from app.subscriptions import transports as tp
 upsert('railway-direct','railway','railway.example.com',443,True,'railway.example.com','railway.example.com','railway',{})
 upsert('cloudflare-01','cloudflare','104.16.1.1',443,True,'worker.example.workers.dev','worker.example.workers.dev','cloudflare-probe',{})
 execute("UPDATE nodes SET latency_ms=25.0 WHERE name='cloudflare-01'")
 u=create_user(UserCreate(username='carol',protocol='vless'))
 import json
 obj=json.loads(render(u,'https://railway.example.com','singbox'))
 # Every node is published once per transport, so sing-box imports the whole
 # protocol matrix (VLESS/VMess/Trojan/Shadowsocks) in a single subscription.
 profiles=tp.available_profiles()
 assert len(obj['outbounds']) == 2 * len(profiles)
 assert {'vless','trojan','vmess'} <= {o['type'] for o in obj['outbounds']}
 assert any(o['type'] == 'shadowsocks' for o in obj['outbounds'])
 assert all(o['tag'] for o in obj['outbounds'])
