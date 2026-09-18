"""Validate the Xray config NEXUS generates, using the real Xray binary.

Run it after touching the transport/profile set or :mod:`app.xray`:

    XRAY_BIN=/usr/local/bin/xray python scripts/check_xray_config.py

It fabricates a throwaway database with one user per protocol, a Reality key
pair and an optionally supplied WARP peer, then runs ``xray run -test`` on both
the full config and the trimmed fallback config. Exit code is non-zero when
either one is rejected, so this can guard a deploy.
"""
import json
import os
import subprocess
import sys
import tempfile

os.environ['ENVIRONMENT'] = 'development'
os.environ['SQLITE_PATH'] = os.path.join(tempfile.gettempdir(), 'nexus-xray-check.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.environ['SQLITE_PATH']
os.environ.setdefault('XRAY_BINARY', os.environ.get('XRAY_BIN', '/usr/local/bin/xray'))

AI = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, AI)

from app.db import init_db, execute  # noqa: E402
from app.subscriptions import transports as tp  # noqa: E402
from app import xray  # noqa: E402

init_db()
for name in ('alice-vless', 'bob-vmess', 'carol-trojan', 'dave-ss'):
    protocol = name.split('-')[1]
    execute('DELETE FROM users WHERE username=?', (name,))
    execute('INSERT INTO users(username,uuid,protocol,is_active,created_at) VALUES(?,?,?,1,0)',
            (name, '11111111-2222-3333-4444-%012d' % (abs(hash(name)) % 10 ** 12), protocol))

keys = xray.ensure_reality_keys()
print('reality keys:', 'generated' if keys else 'unavailable (no binary)')
warp_raw = os.environ.get('WARP_CONFIG')
if warp_raw:
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('warp_config', warp_raw))
if os.environ.get('DIRECT_HOST'):
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('direct_host', os.environ['DIRECT_HOST']))
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('direct_port', os.environ.get('DIRECT_PORT', '443')))

binary = os.environ['XRAY_BINARY']
if not os.path.exists(binary):
    print('xray binary not found at', binary)
    raise SystemExit(2)

config = xray._config()
print('profiles:', [p['id'] for p in tp.available_profiles()])
print('inbound tags:', [i['tag'] for i in config['inbounds']])
print('outbound tags:', [o['tag'] for o in config['outbounds']])

failures = 0
for label, payload in (('full', config), ('fallback', xray._without_optional(config))):
    path = os.path.join(tempfile.gettempdir(), f'nexus-xray-{label}.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    result = subprocess.run([binary, 'run', '-test', '-config', path], capture_output=True, text=True)
    tail = (result.stdout + result.stderr).strip().splitlines()
    print(f'[{label}] rc={result.returncode} :: {tail[-1] if tail else ""}')
    if result.returncode:
        failures += 1
        print('\n'.join(tail[-12:]))

raise SystemExit(1 if failures else 0)
