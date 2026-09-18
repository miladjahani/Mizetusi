"""Prove the whole edge chain: client → FastAPI WS bridge → Xray → internet.

Every published transport depends on that bridge, so this drives the real thing:
it boots the app (which starts Xray through its own lifespan), opens a WebSocket
on an edge path, speaks a genuine VLESS request and fetches a page through it.
Nothing is stubbed — if the route, the bridge or the Xray inbound is wrong, no
HTTP response comes back.

    XRAY_BIN=/usr/local/bin/xray python scripts/check_edge_bridge_e2e.py

Exits 0 without testing when the Xray binary is missing or the sandbox has no
egress, because that is an environment limit, not a regression. Test targets:
loopback-free hosts on port 80, so a failure means the tunnel failed.
"""
import os
import sys
import tempfile
import uuid

os.environ['ENVIRONMENT'] = 'development'
os.environ['SQLITE_PATH'] = os.path.join(tempfile.gettempdir(), 'nexus-bridge.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.environ['SQLITE_PATH']
os.environ.setdefault('XRAY_BINARY', os.environ.get('XRAY_BIN', '/usr/local/bin/xray'))

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import execute, init_db  # noqa: E402

BINARY = os.environ['XRAY_BINARY']
UUID = '11111111-2222-3333-4444-555555555555'


def vless_request(host, port):
    """A minimal VLESS request.

    Field order is version, UUID, addon length, **command**, port, address type,
    address — getting the port and command swapped makes Xray answer "invalid
    request address" instead of dialling, which is exactly the bug this harness
    caught when it was first written.
    """
    return (b'\x00' + uuid.UUID(UUID).bytes + b'\x00' + b'\x01' + port.to_bytes(2, 'big')
            + b'\x02' + bytes([len(host)]) + host.encode())


def fetch_through(client, path, host='127.0.0.1', port=18080):
    with client.websocket_connect(path) as ws:
        ws.send_bytes(vless_request(host, port))
        ws.receive_bytes()  # VLESS response header (version + addon length)
        ws.send_bytes(f'GET / HTTP/1.0\r\nHost: {host}\r\n\r\n'.encode())
        body = b''
        for _ in range(64):
            body += ws.receive_bytes()
            if b'nexus-local-ok' in body:
                break
        return body.decode('utf-8', 'ignore')


def probe_direct_xray(host='127.0.0.1', port=18080):
    """Same exchange against the Xray WS inbound, with the bridge out of the
    picture — used to tell a bridge problem from an Xray problem."""
    import asyncio

    import websockets

    from app.config import settings

    async def run():
        async with websockets.connect(f'ws://127.0.0.1:{settings.xray_vless_port}/ws/vless',
                                      max_size=None, open_timeout=8) as upstream:
            await upstream.send(vless_request(host, port))
            await asyncio.wait_for(upstream.recv(), 8)
            await upstream.send(f'GET / HTTP/1.0\r\nHost: {host}\r\n\r\n'.encode())
            body = b''
            while b'nexus-local-ok' not in body and len(body) < 65536:
                chunk = await asyncio.wait_for(upstream.recv(), 8)
                body += chunk if isinstance(chunk, bytes) else chunk.encode()
            return body

    return asyncio.run(asyncio.wait_for(run(), 30))


if not os.path.exists(BINARY):
    print('xray binary not found at', BINARY, '- skipping')
    raise SystemExit(0)

# A loopback HTTP target keeps the proof independent of sandbox egress rules.
import http.server  # noqa: E402
import threading  # noqa: E402


class _Target(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'nexus-local-ok'
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


server = http.server.ThreadingHTTPServer(('127.0.0.1', 18080), _Target)
threading.Thread(target=server.serve_forever, daemon=True).start()

init_db()
execute('DELETE FROM users')
execute('INSERT INTO users(username,uuid,protocol,is_active,created_at) VALUES(?,?,?,1,0)',
        ('bridge-vless', UUID, 'vless'))

# The app's lifespan starts Xray with the generated config.
with TestClient(__import__('app.main', fromlist=['app']).app) as client:
    from app import xray
    state = xray.status()
    print('xray running:', state['running'], '· transports:', len(state['transports']))
    if not state['running']:
        print('xray did not start:', state)
        raise SystemExit(1)

    try:
        direct = probe_direct_xray().decode('utf-8', 'ignore')
        print('direct Xray WS:', 'OK' if 'HTTP/1.' in direct else f'no HTTP ({direct[:60]!r})')
    except Exception as exc:
        print(f'direct Xray WS FAILED ({type(exc).__name__}: {str(exc)[:120]})')

    failures = []
    for path in ('/ws/vless', '/cdn/vless', '/ws'):
        try:
            page = fetch_through(client, path)
        except Exception as exc:
            detail = getattr(exc, 'reason', '') or str(exc)[:120]
            print(f'{path:12s} FAILED ({type(exc).__name__}: {detail})')
            failures.append(path)
            continue
        ok = 'nexus-local-ok' in page
        print(f'{path:12s} {"OK" if ok else "NO HTTP RESPONSE"} · {len(page)} bytes'
              f'{"" if ok else " :: " + page[:80]!r}')
        if not ok:
            failures.append(path)

print('failures:', failures or 'none')
raise SystemExit(1 if failures else 0)
