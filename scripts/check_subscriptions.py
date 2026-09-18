"""Print exactly what a user receives, without deploying.

Creates a throwaway database, seeds the node catalog, creates one user and then
renders every subscription target (formats, protocols, transports and client
ids) plus the per-node transport links. Useful when a link looks wrong: the
output here is byte-for-byte what a client would download.

    python scripts/check_subscriptions.py
"""
import json
import os
import sys
import tempfile

os.environ['ENVIRONMENT'] = 'development'
os.environ['SQLITE_PATH'] = os.path.join(tempfile.gettempdir(), 'nexus-subs.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.environ['SQLITE_PATH']

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.models import UserCreate  # noqa: E402
from app.db import execute, init_db  # noqa: E402
from app.nodes import ensure as ensure_nodes, upsert  # noqa: E402
from app.subscriptions import transports as tp  # noqa: E402
from app.subscriptions.generator import TARGETS, node_links, render  # noqa: E402
from app.users.service import create_user  # noqa: E402

init_db()
ensure_nodes()
execute('DELETE FROM nodes')
execute('DELETE FROM users')
upsert('railway-direct', 'railway', 'nexus.up.railway.app', 443, True,
       'nexus.up.railway.app', 'nexus.up.railway.app', 'railway', {})
upsert('cloudflare-01', 'cloudflare', '104.16.1.1', 443, True,
       'worker.example.workers.dev', 'worker.example.workers.dev', 'cloudflare-probe', {})
execute("UPDATE nodes SET latency_ms=18.0 WHERE name='cloudflare-01'")
execute("UPDATE nodes SET latency_ms=42.0 WHERE name='railway-direct'")

# With a direct endpoint configured the Reality profile joins the matrix; its key
# pair is generated exactly like the running server would generate it.
if tp.direct_endpoint():
    from app import xray
    xray.ensure_reality_keys()

user = create_user(UserCreate(username='sample', protocol='vless'))
print('nodes :', [n['name'] for n in __import__('app.subscriptions.generator', fromlist=['x']).active_nodes()])
print('user  :', user['username'], user['uuid'])
print('transports:', [(p['id'], p['tag'], p['group']) for p in tp.available_profiles()])
print()

for target in TARGETS:
    try:
        text = render(user, 'https://nexus.up.railway.app', target)
    except ValueError as exc:
        print(f'{target:14s} -- {exc}')
        continue
    if text.lstrip().startswith('{'):
        body = json.loads(text)
        items = body.get('outbounds') or body.get('proxies') or body.get('nodes')
        print(f'{target:14s} format=json entries={len(items)}')
    else:
        lines = [line for line in text.splitlines() if line]
        print(f'{target:14s} format=lines entries={len(lines)}')
        for line in lines[:2]:
            print(f'               {line[:150]}')
    print()

first = __import__('app.subscriptions.generator', fromlist=['x']).active_nodes()[0]
links = node_links(user, first, 'NEXUS')
print(f'per-node link table for {first["name"]}:')
for profile in links['profiles']:
    link = profile.get('link') or f"({profile['protocol']} — JSON formats only)"
    print(f'  {profile["tag"]:22s} {link[:120]}')
