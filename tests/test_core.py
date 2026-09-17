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


def test_singbox_contains_all_nodes():
 from app.db import execute
 execute("DELETE FROM nodes")
 from app.nodes import upsert
 upsert('railway-direct','railway','railway.example.com',443,True,'railway.example.com','railway.example.com','railway',{})
 upsert('cloudflare-01','cloudflare','104.16.1.1',443,True,'worker.example.workers.dev','worker.example.workers.dev','cloudflare-probe',{})
 execute("UPDATE nodes SET latency_ms=25.0 WHERE name='cloudflare-01'")
 u=create_user(UserCreate(username='carol',protocol='vless'))
 import json
 obj=json.loads(render(u,'https://railway.example.com','singbox'))
 assert len(obj['outbounds']) == 2
