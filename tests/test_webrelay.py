"""Telegram Desktop's **WEB** proxy: the one Telegram proxy with no raw port.

The promise this card makes is narrower and stranger than the other three, so the
tests here are about the two things that can silently be wrong:

* **the link is a WEB link** — ``tg://webproxy?server=<our host>&secret=dd…``,
  with *no* port in it (Telegram refuses one), a ``dd`` secret rather than the
  FakeTLS ``ee`` one, and nothing published until both halves of the relay are
  really up;
* **the bridge is served from our own domain** — Telegram Desktop loads the page
  and then talks to a same-origin socket, so the request that reaches the loopback
  relay must still carry the public Host, and the carrier subprotocol has to
  survive the hop;
* **the carrier socket really completes** — the page loading is not the promise. A
  WebSocket upgrade carries *one* ``Host`` header, the relay routes by it verbatim,
  and a second copy (one from the URL, one from the caller) leaves the relay
  answering ``404`` to the socket while the page it belongs to loads perfectly:
  a WEB proxy that connects nothing, which is what the card's own check now
  measures instead of «the port is bound».

``NEXUS_TEST_MTPROTOZIG_BINARY`` (or the binary in the image) points the
real-binary tests at a build; without one they skip, exactly like the other
engines' tests.
"""
import asyncio
import base64
import hashlib
import os
import re
import socket
import subprocess
import threading
import time
import urllib.parse

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-webrelay-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-webrelay-test.db')

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings as cfg
from app.db import execute, init_db
from app.main import _setting, _set, app
from app.telegram import service as tg_service, webrelay

init_db()
client = TestClient(app)

DOMAIN = 'nexus-demo.up.railway.app'
KEYS = (webrelay.ENABLED, webrelay.SECRET, webrelay.DOMAIN, webrelay.SESSIONS, webrelay.STREAMS)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


@pytest.fixture(autouse=True)
def clean_settings():
    """The WEB proxy lives in the shared settings table: never leak a switch."""
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))
    yield
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))


@pytest.fixture
def published(monkeypatch):
    """A relay that is installed, named and really running — nothing more."""
    monkeypatch.setattr(webrelay, 'available', lambda: True)
    monkeypatch.setattr(webrelay, 'domain', lambda: DOMAIN)
    monkeypatch.setattr(webrelay, 'running', lambda: True)
    _set(webrelay.ENABLED, '1')


def binary_for():
    """A real ``mtproto-proxy`` from the environment or the image, or ``None``."""
    for candidate in (os.environ.get('NEXUS_TEST_MTPROTOZIG_BINARY'), cfg.webrelay_binary):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


# --------------------------------------------------------------------- the link
def test_the_link_is_a_web_link_and_carries_no_port(published):
    """Telegram's own rule: the carrier *is* an HTTPS site, so a port is refused.

    The secret is the same 16 bytes as an MTProto link but encoded as ``dd``: the
    relay is a raw byte pipe and adds no TLS-emulation record, and a client that
    is handed an ``ee`` secret reports the proxy as unsupported.
    """
    link = webrelay.links()
    assert link and link['server'] == DOMAIN
    assert link['tg'].startswith('tg://webproxy?')
    query = urllib.parse.parse_qs(urllib.parse.urlparse(link['tg']).query)
    assert set(query) == {'server', 'secret'}
    assert query['server'] == [DOMAIN]
    secret = query['secret'][0]
    assert secret.startswith('dd') and len(secret) == 34
    assert all(char in '0123456789abcdef' for char in secret[2:])
    assert f'server={DOMAIN}' in link['tme'] and link['tme'].startswith('https://t.me/webproxy?')


def test_nothing_is_published_until_both_halves_really_run(monkeypatch):
    """Enabled is not published: a WebView that loads a page nothing answers is worse."""
    monkeypatch.setattr(webrelay, 'available', lambda: True)
    monkeypatch.setattr(webrelay, 'domain', lambda: DOMAIN)
    _set(webrelay.ENABLED, '1')
    monkeypatch.setattr(webrelay, 'running', lambda: False)
    assert webrelay.published() is False
    assert webrelay.links() is None
    assert webrelay.status()['running'] is False
    monkeypatch.setattr(webrelay, 'running', lambda: True)
    assert webrelay.published() is True and webrelay.links()


def test_a_host_without_the_binary_or_a_domain_publishes_nothing(monkeypatch):
    _set(webrelay.ENABLED, '1')
    monkeypatch.setattr(webrelay, 'available', lambda: False)
    monkeypatch.setattr(webrelay, 'running', lambda: True)
    ok, reason = webrelay.reachable()
    assert ok is False and 'mtproto-proxy' in reason
    monkeypatch.setattr(webrelay, 'available', lambda: True)
    monkeypatch.setattr(webrelay, 'domain', lambda: '')
    ok, reason = webrelay.reachable()
    assert ok is False and 'دامنه' in reason


def test_the_secret_survives_a_redeploy_and_rotation_revokes_it():
    first = webrelay.secret()
    assert len(first) == 32 and all(char in '0123456789abcdef' for char in first)
    assert webrelay.secret() == first          # stored, not regenerated
    assert webrelay.link_secret() == 'dd' + first
    rotated = webrelay.rotate_secret()
    assert rotated != first and webrelay.secret() == rotated


# -------------------------------------------------------------------- the card
def test_the_caps_leave_room_for_the_data_plane(published):
    """The data plane's ceiling has to cover what the relay is allowed to open.

    One WEB client holds a connection per logical stream, and the binary warns when
    the two do not fit; sizing them here is what keeps that warning out of a
    deployment nobody is watching.
    """
    _set(webrelay.SESSIONS, '4')
    _set(webrelay.STREAMS, '16')
    assert webrelay.max_connections() >= webrelay.sessions() * (webrelay.streams() + 1)
    assert webrelay.status()['max_connections'] == webrelay.max_connections()


def test_a_bad_domain_or_cap_is_refused_before_anything_is_stored():
    with pytest.raises(ValueError):
        webrelay.save({'domain': 'nodots'})
    with pytest.raises(ValueError):
        webrelay.save({'sessions': '999'})
    with pytest.raises(ValueError):
        webrelay.save({'streams': '0'})
    assert webrelay._setting(webrelay.DOMAIN) is None
    assert webrelay._setting(webrelay.SESSIONS) is None


def test_saving_the_card_stores_the_domain_and_the_switch():
    changed = webrelay.save({'enabled': '1', 'domain': 'https://nexus-demo.up.railway.app/',
                             'sessions': 8, 'streams': 64})
    assert set(changed) == {webrelay.ENABLED, webrelay.DOMAIN, webrelay.SESSIONS, webrelay.STREAMS}
    assert webrelay.enabled() is True
    assert webrelay.domain() == DOMAIN          # scheme and trailing slash are stripped
    assert webrelay.sessions() == 8 and webrelay.streams() == 64


def test_the_panel_payload_carries_the_web_card(published):
    payload = client.get('/api/telegram', headers=h()).json()
    card = payload['webrelay']
    assert card['enabled'] is True and card['published'] is True
    assert card['links']['tg'].startswith('tg://webproxy?')
    assert card['domain'] == DOMAIN and card['ws_path'] == webrelay.WS_PATH
    assert 'mtproto-proxy' in card['engine']
    assert payload['counts']['webrelay'] == 1
    assert any('webproxy' in note or 'WEB' in note for note in payload['notes'])


def test_the_switch_round_trips_over_the_api(published):
    saved = client.post('/api/telegram', headers=h(),
                        json={'webrelay': {'enabled': '1', 'domain': DOMAIN,
                                           'sessions': 3, 'streams': 12}})
    assert saved.status_code == 200, saved.text
    assert webrelay.sessions() == 3 and webrelay.streams() == 12
    off = client.post('/api/telegram', headers=h(), json={'webrelay': {'enabled': '0'}}).json()
    assert off['webrelay']['enabled'] is False and off['webrelay']['links'] is None


def test_the_portal_hands_the_user_the_web_link(published):
    from app.core.models import UserCreate
    from app.users.service import create_user, list_users
    # The suite shares one database file, so the user from an earlier run is
    # removed first rather than assumed absent.
    execute('DELETE FROM users WHERE username=?', ('webuser',))
    create_user(UserCreate(username='webuser', protocol='all'))
    user = next(item for item in list_users() if item['username'] == 'webuser')
    section = tg_service.portal_payload('https://' + DOMAIN, user)
    assert section['webrel']['url'].startswith('tg://webproxy?')
    assert section['webrel']['label'].startswith('WEB')
    assert section['webrel']['secret'].startswith('dd')


# ------------------------------------------------------------- bridge + socket
def test_the_bridge_is_forwarded_to_the_relay_with_our_own_host(published, monkeypatch):
    """A bare forward would send ``Host: 127.0.0.1`` and the relay would 404.

    The page and its socket have to look like one site to the WebView, so the
    public Host (and the forwarded client address) are restored on the way in, and
    the capability in the query is passed on untouched — that is what the relay
    authenticates.
    """
    seen = {}

    async def fake_get(self, url, **kwargs):
        seen['url'] = str(url)
        seen['headers'] = dict(kwargs.get('headers') or {})
        return httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'},
                              content=b'<html>bridge</html>')

    monkeypatch.setattr(httpx.AsyncClient, 'get', fake_get)
    response = client.get('/?bridge=abc123')
    assert response.status_code == 200 and b'bridge' in response.content
    assert seen['url'].endswith('/?bridge=abc123')
    assert seen['headers']['host'] == DOMAIN
    assert seen['headers']['x-forwarded-proto'] == 'https'


def test_the_bridge_is_refused_without_a_live_relay(monkeypatch):
    monkeypatch.setattr(webrelay, 'available', lambda: True)
    monkeypatch.setattr(webrelay, 'running', lambda: False)
    response = client.get('/?bridge=abc123')
    assert response.status_code == 403
    assert 'WEB' in response.text


def test_the_panel_itself_still_answers_the_root(monkeypatch):
    """The bridge branch must not swallow the panel: no ``bridge``, no relay."""
    monkeypatch.setattr(webrelay, 'published', lambda: True)
    response = client.get('/', follow_redirects=False)
    assert response.status_code in (200, 303, 307)
    assert 'bridge' not in response.text[:200]


def test_the_carrier_socket_is_closed_while_the_relay_is_off(monkeypatch):
    monkeypatch.setattr(webrelay, 'available', lambda: True)
    monkeypatch.setattr(webrelay, 'running', lambda: False)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(webrelay.WS_PATH) as socket:
            socket.receive()


def fake_relay(port, record):
    """A loopback stand-in for ``web-relay``: record the upgrade, answer 101.

    Only the handshake matters here. The real relay matches the ``Host`` header
    against ``[web].domain`` verbatim, so a second copy of that header is the whole
    difference between a working carrier and a page whose socket is answered with
    ``404``.
    """
    def serve():
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('127.0.0.1', port))
            server.listen(1)
            conn, _ = server.accept()
            try:
                request = b''
                while b'\r\n\r\n' not in request:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    request += chunk
                text = request.decode('latin-1')
                record['request'] = text
                key = re.search(r'(?im)^sec-websocket-key: (.+)$', text).group(1).strip()
                accept = base64.b64encode(hashlib.sha1(
                    (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                wanted = re.search(r'(?im)^sec-websocket-protocol: (.+)$', text)
                lines = ['HTTP/1.1 101 Switching Protocols', 'Upgrade: websocket',
                         'Connection: Upgrade', f'Sec-WebSocket-Accept: {accept}']
                if wanted:
                    lines.append('Sec-WebSocket-Protocol: ' + wanted.group(1).strip())
                conn.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())
                time.sleep(0.4)
            finally:
                conn.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def test_the_carrier_socket_names_our_host_exactly_once(published, monkeypatch):
    """One ``Host`` header, and it is ours — the relay routes by it verbatim.

    Handing the public host to the WebSocket client as an *extra* header is how this
    went wrong: the client writes the URL's host first and appends ours after it, the
    relay reads ``127.0.0.1:<port>``, and the socket is answered with ``404`` while
    the page it belongs to loads perfectly.
    """
    port = free_port()
    monkeypatch.setattr(cfg, 'webrelay_port', port)
    record = {}
    fake_relay(port, record)
    with client.websocket_connect(webrelay.WS_PATH, subprotocols=['tproxy-v1.abc']) as socket:
        assert socket.accepted_subprotocol == 'tproxy-v1.abc'
    head = record['request'].split('\r\n\r\n')[0].split('\r\n')
    hosts = [line for line in head if line.lower().startswith('host:')]
    assert hosts == ['Host: ' + DOMAIN], record['request']
    assert any(line.lower().startswith('sec-websocket-protocol: tproxy-v1.abc') for line in head)
    assert any(line.lower().startswith('x-forwarded-host: ' + DOMAIN) for line in head)


def test_the_probe_is_not_fooled_by_a_page_whose_socket_is_refused(published, monkeypatch):
    """A page that loads is not a WEB proxy that works — and «the port is bound»
    was the check that let a broken carrier look healthy."""
    _set(webrelay.SECRET, 'ab' * 16)
    monkeypatch.setattr(cfg, 'webrelay_port', free_port())      # nothing is listening

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'},
                              content=b'<html>TOKEN="tok"</html>')

    monkeypatch.setattr(httpx.AsyncClient, 'get', fake_get)
    result = asyncio.run(webrelay.probe())
    assert result['page']['ok'] is True and result['status'] == 200
    assert result['socket']['ok'] is False
    assert result['ok'] is False and 'ConnectionRefusedError' in result['error']


def test_a_probe_without_the_switch_says_so(monkeypatch):
    monkeypatch.setattr(webrelay, 'available', lambda: True)
    monkeypatch.setattr(webrelay, 'running', lambda: True)
    result = asyncio.run(webrelay.probe())
    assert result['ok'] is False and 'خاموش' in result['error']


# --------------------------------------------------------------- real binaries
def test_the_rendered_config_is_one_the_binary_accepts(monkeypatch, tmp_path):
    """What the panel renders is what ``mtproto-proxy`` itself parses."""
    binary = binary_for()
    if not binary:
        pytest.skip('mtproto-proxy binary not installed')
    monkeypatch.setattr(cfg, 'webrelay_binary', binary)
    monkeypatch.setattr(cfg, 'webrelay_config', str(tmp_path / 'webrelay.toml'))
    monkeypatch.setattr(webrelay, 'domain', lambda: DOMAIN)
    path = webrelay._write(webrelay.config_text())
    result = subprocess.run(webrelay.validate_argv(path), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    text = webrelay.config_text()
    for line in ('[web]', 'enabled = true', 'domain = "%s"' % DOMAIN,
                 'ws_path = "%s"' % webrelay.WS_PATH,
                 'backend = "127.0.0.1:%d"' % webrelay.backend_port(),
                 '[access.users]'):
        assert line in text, line


def test_the_real_relay_really_binds_its_loopback_port(monkeypatch, tmp_path):
    """The relay half is started for real: the page has to have somewhere to go."""
    binary = binary_for()
    if not binary:
        pytest.skip('mtproto-proxy binary not installed')
    port = free_port()
    monkeypatch.setattr(cfg, 'webrelay_binary', binary)
    monkeypatch.setattr(cfg, 'webrelay_config', str(tmp_path / 'webrelay.toml'))
    monkeypatch.setattr(cfg, 'webrelay_port', port)
    monkeypatch.setattr(webrelay, 'domain', lambda: DOMAIN)
    path = webrelay._write(webrelay.config_text())
    proc = subprocess.Popen(webrelay.relay_argv(path),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 8
        connected = False
        while time.time() < deadline and proc.poll() is None:
            with socket.socket() as probe:
                probe.settimeout(0.4)
                try:
                    probe.connect(('127.0.0.1', port))
                    connected = True
                    break
                except OSError:
                    time.sleep(0.2)
        assert connected, (proc.stderr.read() or b'').decode(errors='ignore')[-400:]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def relay_on_a_loopback_port(monkeypatch, tmp_path):
    """The real relay, wired into the settings, on its own ephemeral port.

    Skips (rather than fails) when no binary is installed — the same rule the other
    engine tests follow — and fails loudly when the binary is there but does not
    come up, because that is a real problem rather than a missing dependency.
    """
    binary = binary_for()
    if not binary:
        pytest.skip('mtproto-proxy binary not installed')
    port = free_port()
    monkeypatch.setattr(webrelay, 'available', lambda: True)
    monkeypatch.setattr(webrelay, 'running', lambda: True)
    monkeypatch.setattr(webrelay, 'domain', lambda: DOMAIN)
    monkeypatch.setattr(cfg, 'webrelay_binary', binary)
    monkeypatch.setattr(cfg, 'webrelay_config', str(tmp_path / 'webrelay.toml'))
    monkeypatch.setattr(cfg, 'webrelay_port', port)
    _set(webrelay.ENABLED, '1')
    path = webrelay._write(webrelay.config_text())
    proc = subprocess.Popen(webrelay.relay_argv(path),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 8
    while time.time() < deadline and proc.poll() is None:
        with socket.socket() as probe:
            probe.settimeout(0.4)
            try:
                probe.connect(('127.0.0.1', port))
                return proc, port
            except OSError:
                time.sleep(0.2)
    detail = (proc.stderr.read() or b'').decode(errors='ignore')[-400:]
    proc.terminate()
    pytest.fail('relay did not bind its loopback port: ' + detail)


def test_the_probe_completes_the_carrier_handshake_against_the_real_relay(monkeypatch, tmp_path):
    """The card's own check, against the binary: page *and* socket, both real.

    Nothing is faked but the URL's scheme: the capability is derived from our own
    secret the way Telegram derives it, the page is the relay's own, the token comes
    out of it, and the subprotocol echoed back is the proof that the carrier
    completed.
    """
    proc, port = relay_on_a_loopback_port(monkeypatch, tmp_path)
    try:
        result = asyncio.run(webrelay.probe())
        assert result['page']['ok'] is True, result
        assert result['ok'] is True, result
        assert result['socket']['protocol'].startswith('tproxy-v1.')
        assert f'127.0.0.1:{port}' in result['url']
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_the_panel_route_completes_the_handshake_the_relay_verifies(monkeypatch, tmp_path):
    """End to end through the panel's own route, with the token the relay issued.

    A relay that is not handed the capability it derives answers ``404`` to the
    socket, and a relay that is handed two ``Host`` headers answers ``404`` as well:
    so a subprotocol echoed all the way back to the client is this deployment really
    serving the WEB proxy, not a port that happens to be open.
    """
    proc, port = relay_on_a_loopback_port(monkeypatch, tmp_path)
    try:
        page = httpx.get(f'http://127.0.0.1:{port}/?bridge={webrelay.bridge_capability()}',
                         headers={'host': DOMAIN}, timeout=6)
        assert page.status_code == 200, page.text
        token = re.search(r'TOKEN="([^"]+)"', page.text).group(1)
        with client.websocket_connect(webrelay.WS_PATH,
                                      subprotocols=['tproxy-v1.' + token]) as socket:
            assert socket.accepted_subprotocol == 'tproxy-v1.' + token
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_the_check_round_trips_over_the_api(published, monkeypatch):
    """The card's «تست اتصال» button is one action on the Telegram endpoint."""
    async def fake_probe(timeout=6.0):
        return {'ok': True, 'error': '', 'status': 200, 'bytes': 12,
                'url': 'http://127.0.0.1:8081/?bridge=x',
                'page': {'ok': True, 'status': 200, 'bytes': 12, 'error': ''},
                'socket': {'ok': True, 'protocol': 'tproxy-v1.tok', 'error': ''}}

    monkeypatch.setattr(webrelay, 'probe', fake_probe)
    body = client.post('/api/telegram', headers=h(),
                       json={'action': 'probe-webrelay'}).json()
    assert body['success'] is True and body['webrelay_probe']['ok'] is True
    assert body['webrelay_probe']['socket']['protocol'] == 'tproxy-v1.tok'
