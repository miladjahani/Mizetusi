"""End-to-end proof that the generated transports really carry traffic.

Starts the NEXUS Xray config locally, then a second Xray instance as a client
for each published transport and fetches a URL through it:

* ``vless-reality`` — the direct/TCP node (needs ``direct_host``/``direct_port``);
* ``warp-*`` — only when ``WARP_CONFIG`` holds a registered peer, in which case
  the answer must come back with ``warp=on`` in Cloudflare's trace.

Nothing here is needed at runtime — it exists so a change to the transport set
can be verified without deploying.

    XRAY_BIN=/usr/local/bin/xray python scripts/check_transports_e2e.py

Exits 0 without testing when the Xray binary is missing, so a developer without
the build is not blocked by it.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

os.environ['ENVIRONMENT'] = 'development'
os.environ['SQLITE_PATH'] = os.path.join(tempfile.gettempdir(), 'nexus-e2e.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.environ['SQLITE_PATH']
os.environ.setdefault('XRAY_BINARY', os.environ.get('XRAY_BIN', '/usr/local/bin/xray'))

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db import init_db, execute, row  # noqa: E402
from app.subscriptions import transports as tp  # noqa: E402
from app import xray  # noqa: E402

BINARY = os.environ['XRAY_BINARY']
if not os.path.exists(BINARY):
    print('xray binary not found at', BINARY, '- skipping')
    raise SystemExit(0)

UUID = '11111111-2222-3333-4444-555555555555'
SOCKS_PORT = 11080
init_db()
execute('DELETE FROM users')
execute('INSERT INTO users(username,uuid,protocol,is_active,created_at) VALUES(?,?,?,1,0)',
        ('e2e-vless', UUID, 'vless'))


def put(key, value):
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, value))


put('direct_host', '127.0.0.1')
put('direct_port', str(xray.settings.xray_reality_port))
if os.environ.get('WARP_CONFIG'):
    put('warp_config', os.environ['WARP_CONFIG'])
keys = xray.ensure_reality_keys()
if not keys:
    print('reality keys unavailable - skipping')
    raise SystemExit(0)

server = xray._config()
for inbound in server['inbounds']:
    inbound['listen'] = '127.0.0.1'  # the whole test runs on loopback
server_path = os.path.join(tempfile.gettempdir(), 'nexus-e2e-server.json')
with open(server_path, 'w', encoding='utf-8') as handle:
    json.dump(server, handle, ensure_ascii=False, indent=2)


def base_client(inbound, stream):
    return {
        'log': {'loglevel': 'warning'},
        'inbounds': [{'tag': 'socks', 'listen': '127.0.0.1', 'port': SOCKS_PORT, 'protocol': 'socks',
                      'settings': {'udp': False, 'auth': 'noauth'}}],
        'outbounds': [{
            'protocol': inbound['protocol'], 'tag': 'proxy',
            'settings': {'vnext': [{'address': '127.0.0.1', 'port': inbound['port'], 'users': [
                {'id': UUID, 'encryption': 'none', 'flow': ''}]}]},
            'streamSettings': stream,
        }, {'protocol': 'freedom', 'tag': 'direct'}],
    }


def reality_stream(profile):
    stream = {'network': profile['network']}
    if profile['network'] == 'grpc':
        stream['grpcSettings'] = {'serviceName': (profile['path'] or '').lstrip('/')}
    return {**stream, 'security': 'reality', 'realitySettings': {
        'serverName': tp.REALITY_SNI, 'fingerprint': 'chrome',
        'publicKey': keys['public_key'], 'shortId': keys['short_id'], 'spiderX': '/'}}


def ws_stream(profile):
    return {'network': 'ws', 'security': 'none',
            'wsSettings': {'path': profile['path'], 'headers': {'Host': '127.0.0.1'}}}


def stop(proc):
    proc.terminate()
    try:
        proc.wait(5)
    except Exception:
        proc.kill()


def fetch(url='/dev/null'):
    out = subprocess.run(['curl', '-s', '-m', '15', '-w', '\n%{http_code}',
                          '--socks5-hostname', f'127.0.0.1:{SOCKS_PORT}', url],
                         capture_output=True, text=True)
    body, _, code = out.stdout.rpartition('\n')
    return code.strip(), body.strip()


probe = subprocess.run([BINARY, 'run', '-test', '-config', server_path], capture_output=True, text=True)
print('server config:', 'OK' if probe.returncode == 0 else (probe.stdout + probe.stderr)[-400:])
if probe.returncode:
    raise SystemExit(1)

inbounds = {item['tag']: item for item in server['inbounds']}
checks = []
for profile in tp.DIRECT_PROFILES:
    if profile['id'] in inbounds:
        checks.append((profile, inbounds[profile['id']], reality_stream(profile)))
if tp.warp_config() and tp.WARP_PROFILE['id'] in inbounds:
    checks.append((tp.WARP_PROFILE, inbounds[tp.WARP_PROFILE['id']], ws_stream(tp.WARP_PROFILE)))

failures = []
server_proc = subprocess.Popen([BINARY, 'run', '-config', server_path],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
try:
    for profile, inbound, stream in checks:
        cfg = base_client(inbound, stream)
        path = os.path.join(tempfile.gettempdir(), f'nexus-e2e-{profile["id"]}.json')
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(cfg, handle, ensure_ascii=False, indent=2)
        check = subprocess.run([BINARY, 'run', '-test', '-config', path], capture_output=True, text=True)
        if check.returncode:
            print(f'{profile["id"]:16s} client config rejected')
            failures.append(profile['id'])
            continue
        proc = subprocess.Popen([BINARY, 'run', '-config', path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        try:
            code, body = fetch('https://www.cloudflare.com/cdn-cgi/trace')
            warp = re.search(r'^warp=(\S+)', body, re.M)
            note = f'warp={warp.group(1)}' if warp else (body.splitlines()[0][:40] if body else 'no body')
            print(f'{profile["id"]:16s} -> HTTP {code or "no response"} · {note}')
            wants_warp = profile['id'] == tp.WARP_PROFILE['id']
            ok = code in ('200', '204') and (not wants_warp or (warp and warp.group(1) == 'on'))
            if not ok:
                failures.append(profile['id'])
        finally:
            stop(proc)
finally:
    stop(server_proc)

print('failures:', failures or 'none')
raise SystemExit(1 if failures else 0)
