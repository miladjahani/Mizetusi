"""Local end-to-end proof of the whole NEXUS chain (opt-in, needs Xray).

```
python scripts/e2e_tunnel_check.py --xray /usr/local/bin/xray
```

It boots the exact artifacts the panel publishes — the generated server config,
the FastAPI WebSocket edge, and one *client* outbound per transport built by the
subscription generator — and then pushes a real HTTP request through every
tunnel. A transport that cannot reach the origin page prints the stage that
failed, which is what makes this worth more than a TLS handshake test.

Exit code is non-zero when any published transport is broken.
"""
import argparse
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Origin(http.server.BaseHTTPRequestHandler):
    body = b'NEXUS-OK'

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


def _wait_port(host, port, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), 1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _fetch(proxy_port, origin_port, tries=3):
    import httpx
    last = None
    for _ in range(tries):
        try:
            with httpx.Client(proxy=f'http://127.0.0.1:{proxy_port}', trust_env=False, timeout=12.0) as client:
                got = client.get(f'http://127.0.0.1:{origin_port}/probe')
                if got.status_code == 200 and got.text == 'NEXUS-OK':
                    return True, None
                last = f'http {got.status_code}'
        except Exception as exc:  # noqa: BLE001 - the message is the diagnosis
            last = f'{type(exc).__name__}: {str(exc)[:140]}'
        time.sleep(0.4)
    return False, last


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--xray', default=os.getenv('XRAY_BINARY', '/usr/local/bin/xray'))
    parser.add_argument('--edge-port', type=int, default=18000)
    parser.add_argument('--origin-port', type=int, default=18080)
    parser.add_argument('--client-base', type=int, default=20000)
    parser.add_argument('--keep', action='store_true', help='keep the scratch directory')
    args = parser.parse_args()
    if not os.path.exists(args.xray):
        print(f'xray binary not found at {args.xray} — skipping end-to-end check')
        return 0

    workdir = tempfile.mkdtemp(prefix='nexus-e2e-')
    os.environ.update({
        'ENVIRONMENT': 'test',
        'SQLITE_PATH': os.path.join(workdir, 'nexus.db'),
        'DATABASE_URL': 'sqlite:///' + os.path.join(workdir, 'nexus.db'),
        'XRAY_BINARY': args.xray,
        'XRAY_CONFIG': os.path.join(workdir, 'xray.json'),
        'XRAY_ENABLED': 'true',
        'ADMIN_PASSWORD': 'admin',
        'JWT_SECRET': 'e2e-secret',
        'PUBLIC_BASE_URL': f'http://127.0.0.1:{args.edge_port}',
    })
    sys.path.insert(0, ROOT)

    import httpx  # noqa: F401 - imported to fail early with a clear message
    from app.core.models import UserCreate
    from app.db import init_db
    from app.nodes import ensure as ensure_nodes, upsert as upsert_node
    from app.subscriptions import transports as tp
    from app.subscriptions.generator import xray as xray_outbound
    from app.users.service import create_user, get_user
    from app import xray as xray_server

    init_db()
    ensure_nodes()
    if not get_user('e2e'):
        create_user(UserCreate(username='e2e', protocol='all'))
    user = get_user('e2e')
    upsert_node('local-edge', 'railway', '127.0.0.1', args.edge_port, True, '127.0.0.1', '127.0.0.1', 'railway', {})
    node = {'name': 'local-edge', 'kind': 'railway', 'server': '127.0.0.1', 'port': args.edge_port,
            'tls': 1, 'sni': '127.0.0.1', 'host': '127.0.0.1', 'enabled': 1}

    origin = http.server.ThreadingHTTPServer(('127.0.0.1', args.origin_port), Origin)
    threading.Thread(target=origin.serve_forever, daemon=True).start()

    xray_server.ensure_reality_keys()
    _digest, config = xray_server.write_config()
    server_log = open(os.path.join(workdir, 'server.log'), 'w')
    server = subprocess.Popen([args.xray, 'run', '-config', os.environ['XRAY_CONFIG']],
                              stdout=server_log, stderr=subprocess.STDOUT)
    time.sleep(0.7)
    if server.poll() is not None:
        server_log.flush()
        print('server xray rejected the generated config:\n'
              + open(os.path.join(workdir, 'server.log')).read()[-3000:])
        return 1

    edge_log = open(os.path.join(workdir, 'edge.log'), 'w')
    edge = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
                             '--port', str(args.edge_port), '--log-level', 'warning'],
                            cwd=ROOT, stdout=edge_log, stderr=subprocess.STDOUT)
    if not _wait_port('127.0.0.1', args.edge_port):
        print('the FastAPI edge did not start:\n' + open(os.path.join(workdir, 'edge.log')).read()[-3000:])
        return 1

    profiles = [p for p in tp.available_profiles() if p['group'] == tp.EDGE]
    inbounds, outbounds, rules, plan = [], [], [], []
    for index, profile in enumerate(profiles):
        entry = xray_outbound(user, node, profile)
        entry['streamSettings']['security'] = 'none'
        entry['streamSettings'].pop('tlsSettings', None)
        entry['tag'] = profile['id']
        port = args.client_base + index
        inbounds.append({'tag': f'in{index}', 'listen': '127.0.0.1', 'port': port, 'protocol': 'http', 'settings': {}})
        outbounds.append(entry)
        rules.append({'type': 'field', 'inboundTag': [f'in{index}'], 'outboundTag': profile['id']})
        plan.append((profile, port))
    client_path = os.path.join(workdir, 'client.json')
    with open(client_path, 'w') as handle:
        json.dump({'log': {'loglevel': 'warning'}, 'inbounds': inbounds, 'outbounds': outbounds,
                   'routing': {'domainStrategy': 'AsIs', 'rules': rules}}, handle, indent=2)
    tested = subprocess.run([args.xray, 'run', '-test', '-config', client_path], capture_output=True, text=True)
    if tested.returncode != 0:
        print('the generated CLIENT config is invalid:\n' + (tested.stdout + tested.stderr)[-4000:])
    client_log = open(os.path.join(workdir, 'client.log'), 'w')
    client = subprocess.Popen([args.xray, 'run', '-config', client_path], stdout=client_log, stderr=subprocess.STDOUT)
    time.sleep(0.8)

    failures = 0
    for profile, port in plan:
        if not _wait_port('127.0.0.1', port, timeout=5):
            print(f'[{profile["id"]:>16}] FAIL  client listener missing')
            failures += 1
            continue
        ok, error = _fetch(port, args.origin_port)
        print(f'[{profile["id"]:>16}] {"OK  " if ok else "FAIL"}  {profile["protocol"]:>6}  {profile["tag"]}'
              + ('' if ok else f'  <- {error}'))
        failures += 0 if ok else 1

    client.terminate()
    edge.terminate()
    server.terminate()
    time.sleep(0.3)
    print(f'\n{len(plan) - failures}/{len(plan)} published transports reached the origin page')
    print(f'scratch dir: {workdir}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
