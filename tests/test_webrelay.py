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
  survive the hop.

``NEXUS_TEST_MTPROTOZIG_BINARY`` (or the binary in the image) points the
real-binary tests at a build; without one they skip, exactly like the other
engines' tests.
"""
import os
import socket
import subprocess
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
