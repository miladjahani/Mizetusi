"""Telegram proxies: MTProto, the HTTP/SOCKS5 web proxy and Telegram Web.

Telegram is the one thing a user asks for before anything else, and each of the
three answers is a different product with a different failure mode. What is tested
here is therefore not «does the card render» but the three promises the card makes:

* a link is published **only** when its listener is really running and its port is
  reachable — otherwise the reason is named (the MTProto secret, the Xray inbound,
  the public address);
* the credential belongs to **one user**: disabling a user revokes their proxy
  line, and no two users share a password;
* what is rendered is what the engines accept — ``mtg access`` parses the MTProto
  config, ``xray run -test`` parses the web-proxy inbound, and (when the binaries
  are installed) both really listen and the HTTP proxy really carries traffic.

``NEXUS_TEST_MTG_BINARY`` / ``NEXUS_TEST_XRAY_BINARY`` point the real-binary tests
at a build; without one they skip, exactly like the second engines' tests.
"""
import asyncio
import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-telegram-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-telegram-test.db')

import pytest
from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.db import execute, init_db
from app.main import _setting, _set, app
from app.subscriptions import transports as tp
from app.telegram import mtproto, service as tg_service, webapp, webproxy
from app import runtime, xray

init_db()
client = TestClient(app)

PUBLIC_HOST = '203.0.113.10'
HTTP_PORT = '23948'
SOCKS_PORT = '23949'
MTPROTO_PORT = '23950'
USER_UUID = '11111111-2222-3333-4444-555555555555'

TG_KEYS = ('tg_mtproto_enabled', 'tg_mtproto_port', 'tg_mtproto_secret', 'tg_mtproto_domain',
           'tg_mtproto_concurrency', 'tg_mtproto_dns', 'tg_mtproto_front_ip',
           'tg_web_http_enabled', 'tg_web_http_port', 'tg_web_socks_enabled', 'tg_web_socks_port',
           'tg_web_enabled', 'direct_host', 'direct_port')


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


def a_vps():
    """What a VPS admin configures: a public address, so a raw port is reachable."""
    _set('direct_host', PUBLIC_HOST)
    _set('direct_port', '8443')


class FakeProc:
    """A stand-in for the running mtg process (``published()`` reads it)."""

    returncode = None
    pid = 4242


@pytest.fixture(autouse=True)
def clean():
    """Every test starts where the image ships: nothing switched on, nothing stored."""
    previous = mtproto._proc
    mtproto._proc = None
    for key in TG_KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))
    execute('DELETE FROM users')
    try:
        yield
    finally:
        mtproto._proc = previous if previous is None else None
        for key in TG_KEYS:
            execute('DELETE FROM settings WHERE key=?', (key,))
        execute('DELETE FROM users')


@pytest.fixture(autouse=True)
def a_host_that_owns_its_ports(monkeypatch):
    """These tests describe a VPS/Docker host: a raw TCP port is available.

    Which host withholds what is :mod:`app.runtime`'s own concern; leaving it to
    the ambient environment would make this module pass or fail depending on where
    it runs. The tests that exercise withholding override these explicitly.
    """
    monkeypatch.setattr(runtime, 'has_tcp', lambda: True)


@pytest.fixture(autouse=True)
def every_proxy_type_is_published():
    """This module describes the «همهٔ انواع» mode, not the deploy's default.

    A fresh deployment hands a user *only* the WEB proxy (app/telegram/service.py),
    because that is the one that needs no raw TCP port. Everything tested here is
    about the three that do — the ``tg://`` MTProto link, the HTTP/SOCKS5 web
    proxies and Telegram Web — so the mode is pinned rather than inherited from
    whatever a previous run left in the shared database.
    """
    _set(tg_service.MODE, tg_service.ALL)
    yield
    execute('DELETE FROM settings WHERE key=?', (tg_service.MODE,))


def a_user(username='tguser', uuid=USER_UUID):
    from app.core.models import UserCreate
    from app.users.service import create_user, list_users
    create_user(UserCreate(username=username, protocol='all'))
    found = next(item for item in list_users() if item['username'] == username)
    execute('UPDATE users SET uuid=? WHERE username=?', (uuid, username))
    return next(item for item in list_users() if item['username'] == username)


@pytest.fixture
def mtg_installed(monkeypatch):
    """Pretend the image really ships ``mtg``.

    The panel's rule is «published only when the binary is installed and the port
    is reachable»; a test host without the binary has to be able to exercise the
    *other* half of that rule, so availability is patched rather than assumed.
    """
    monkeypatch.setattr(mtproto, 'available', lambda: True)


def binary_for(env_name, setting_name):
    """A real binary from the environment or the image, or ``None``."""
    override = os.environ.get(env_name)
    if override:
        return override if os.path.exists(override) else None
    path = getattr(cfg, setting_name)
    return path if os.path.exists(path) else None


# ------------------------------------------------------------------- MTProto
def test_the_secret_is_the_format_telegram_clients_accept():
    """``ee`` + 16 random bytes + the hex of the fronting name, and nothing else.

    The domain is *inside* the secret, which is why it is generated here instead
    of being pasted in: mtg refuses a secret that does not match its config, and a
    client that added the proxy keeps working because the value is stored.
    """
    value = mtproto.secret()
    assert value.startswith('ee')
    assert len(value) == 2 + 32 + len('www.cloudflare.com'.encode().hex())
    assert mtproto.embedded_domain(value) == 'www.cloudflare.com'
    assert mtproto.secret() == value, 'the secret must survive a second read'
    assert mtproto.secret(create=False) == value


def test_changing_the_fronting_name_reissues_the_secret():
    original = mtproto.secret()
    mtproto.save({'domain': 'cdn.example.com'})
    reissued = mtproto.secret()
    assert reissued != original
    assert mtproto.embedded_domain(reissued) == 'cdn.example.com'


def test_a_bad_fronting_name_or_port_is_refused_before_anything_is_stored():
    for payload in ({'domain': 'nodots'}, {'domain': ''}, {'port': 'oops'}, {'port': '70000'},
                    {'concurrency': '0'}, {'front_ip': '10.0.0.1'}):
        with pytest.raises(ValueError):
            mtproto.save(payload)
    assert mtproto._setting(mtproto.DOMAIN) is None
    assert mtproto._setting(mtproto.PORT) is None


def test_the_config_is_the_shape_mtg_reads():
    text = mtproto.config_text()
    assert f'bind-to = "0.0.0.0:{mtproto.port()}"' in text
    assert f'secret = "{mtproto.secret()}"' in text
    assert 'concurrency = ' in text
    # The two blocks that keep a deploy from looking like a scanner and from
    # binding a second listener nobody asked for.
    assert '[defense.blocklist]' in text and 'enabled = false' in text
    assert '[stats.prometheus]' in text
    assert '[defense.anti-replay]' in text


def test_a_disabled_mtproto_proxy_publishes_nothing():
    a_vps()
    assert mtproto.enabled() is False
    assert mtproto.published() is False
    assert mtproto.links() is None
    assert 'enabled' in mtproto.status() and mtproto.status()['published'] is False


def test_an_enabled_mtproto_proxy_without_a_public_address_is_withheld(mtg_installed, monkeypatch):
    monkeypatch.setattr(runtime, 'direct', lambda: None)
    monkeypatch.setattr(runtime, 'has_tcp', lambda: False)
    mtproto.save({'enabled': '1', 'port': MTPROTO_PORT})
    ok, reason = mtproto.reachable()
    assert ok is False and 'پورت خام' in reason
    assert mtproto.published() is False
    assert mtproto.links() is None


def test_the_mtproto_link_is_exactly_what_a_client_adds(mtg_installed):
    a_vps()
    mtproto.save({'enabled': '1', 'port': MTPROTO_PORT})
    mtproto._proc = FakeProc()
    links = mtproto.links()
    assert links['server'] == PUBLIC_HOST and links['port'] == int(MTPROTO_PORT)
    assert links['tg'] == (f"tg://proxy?server={PUBLIC_HOST}&port={MTPROTO_PORT}"
                           f"&secret={links['secret']}")
    assert links['tme'].startswith('https://t.me/proxy?')
    assert links['secret'] in links['tme']
    # The card only shows the link once the listener is really up.
    assert mtproto.published() is True
    assert mtproto.status()['links']['tg'] == links['tg']


@pytest.mark.skipif(binary_for('NEXUS_TEST_MTG_BINARY', 'mtg_binary') is None,
                    reason='mtg is not installed here')
def test_the_real_mtg_accepts_the_rendered_config(monkeypatch, tmp_path):
    """``mtg access`` parses exactly the file ``mtg run`` will be given."""
    binary = binary_for('NEXUS_TEST_MTG_BINARY', 'mtg_binary')
    monkeypatch.setattr(cfg, 'mtg_binary', binary)
    monkeypatch.setattr(cfg, 'mtg_config', str(tmp_path / 'mtg.toml'))
    mtproto.save({'enabled': '1', 'port': MTPROTO_PORT})
    path = mtproto._write(mtproto.config_text())
    done = subprocess.run(mtproto.validate_argv(path), capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (done.stdout + done.stderr)[-500:]
    # …and a config mtg refuses is reported instead of being started.
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('secret = "eeZZ"\nbind-to = "0.0.0.0:23950"\n')
    refused = subprocess.run(mtproto.validate_argv(path), capture_output=True, text=True, timeout=60)
    assert refused.returncode != 0


@pytest.mark.skipif(binary_for('NEXUS_TEST_MTG_BINARY', 'mtg_binary') is None,
                    reason='mtg is not installed here')
def test_the_supervisor_starts_and_stops_a_real_mtg_listener(monkeypatch, tmp_path):
    binary = binary_for('NEXUS_TEST_MTG_BINARY', 'mtg_binary')
    monkeypatch.setattr(cfg, 'mtg_binary', binary)
    monkeypatch.setattr(cfg, 'mtg_config', str(tmp_path / 'mtg.toml'))
    a_vps()
    mtproto.save({'enabled': '1', 'port': MTPROTO_PORT})

    async def scenario():
        try:
            result = await mtproto.sync(force=True)
            assert result['running'] is True, result
            # A TCP connect proves the listener is really bound, which is the
            # half a rendered config cannot prove.
            probe = socket.socket()
            assert probe.connect_ex(('127.0.0.1', int(MTPROTO_PORT))) == 0
            probe.close()
            assert mtproto.published() is True
            # Switching it off stops the process and with it the link.
            mtproto.save({'enabled': '0'})
            await mtproto.sync()
            assert mtproto.published() is False
            again = socket.socket()
            assert again.connect_ex(('127.0.0.1', int(MTPROTO_PORT))) != 0
            again.close()
        finally:
            await mtproto.stop_all()

    asyncio.run(scenario())


# ------------------------------------------------------------------ web proxy
def test_the_web_proxies_are_xray_inbounds_with_one_account_per_user():
    a_vps()
    a_user()
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT},
                   'web-socks': {'enabled': '1', 'port': SOCKS_PORT}})
    inbounds = webproxy.xray_inbounds()
    assert [item['protocol'] for item in inbounds] == ['http', 'socks']
    assert all(item['listen'] == '0.0.0.0' for item in inbounds), 'a public listener'
    http = inbounds[0]
    assert http['tag'] == 'tg-http' and http['port'] == int(HTTP_PORT)
    assert http['settings']['accounts'] == [{'user': 'tguser', 'pass': USER_UUID}]
    socks = inbounds[1]
    assert socks['settings']['auth'] == 'password'
    assert socks['settings']['accounts'] == [{'user': 'tguser', 'pass': USER_UUID}]
    assert socks['settings']['udp'] is True


def test_the_panel_config_carries_them_into_the_running_engine():
    """They are in *Xray's* config, so the engine that serves the VPN serves these."""
    a_vps()
    a_user()
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT}})
    tags = [item['tag'] for item in xray._config()['inbounds']]
    assert 'tg-http' in tags
    # …and switching it off takes it back out of the config.
    webproxy.save({'web-http': {'enabled': '0'}})
    assert 'tg-http' not in [item['tag'] for item in xray._config()['inbounds']]


def test_every_user_gets_their_own_proxy_line():
    a_vps()
    a_user('firstuser', USER_UUID)
    second = '99999999-8888-7777-6666-555555555555'
    a_user('seconduser', second)
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT}})
    lines = webproxy.lines()
    assert len(lines) == 2
    urls = {item['username']: item['url'] for item in lines}
    assert urls['firstuser'] == f'http://firstuser:{USER_UUID}@{PUBLIC_HOST}:{HTTP_PORT}'
    assert urls['seconduser'] == f'http://seconduser:{second}@{PUBLIC_HOST}:{HTTP_PORT}'
    # One user's lines only, which is what the status window renders.
    mine = webproxy.lines('firstuser', USER_UUID)
    assert [item['username'] for item in mine] == ['firstuser']
    assert second not in mine[0]['url']


def test_a_disabled_user_loses_their_proxy_line():
    a_vps()
    a_user()
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT}})
    assert webproxy.lines()
    execute('UPDATE users SET is_active=0 WHERE username=?', ('tguser',))
    assert webproxy.lines() == []
    assert webproxy.status()['counts']['accounts'] == 0


def test_a_platform_without_a_raw_port_withholds_the_web_proxy(monkeypatch):
    monkeypatch.setattr(runtime, 'has_tcp', lambda: False)
    a_user()
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT}})
    item = webproxy.BY_ID['web-http']
    ok, reason = webproxy.reachable(item)
    assert ok is False and 'TCP Proxy' in reason
    assert webproxy.xray_inbounds() == []
    assert webproxy.lines() == []
    assert webproxy.status()['catalog'][0]['published'] is False


def test_a_bad_web_proxy_port_is_refused_before_anything_is_stored():
    for payload in ({'web-http': {'port': 'nope'}}, {'web-http': {'port': '0'}},
                    {'web-socks': {'port': '99999'}}):
        with pytest.raises(ValueError):
            webproxy.save(payload)
    assert webproxy._setting('tg_web_http_port') is None


@pytest.mark.skipif(binary_for('NEXUS_TEST_XRAY_BINARY', 'xray_binary') is None,
                    reason='the Xray binary is not installed here')
def test_the_real_xray_accepts_the_web_proxy_inbound(monkeypatch, tmp_path):
    """The strongest check there is: hand the inbounds to Xray itself."""
    binary = binary_for('NEXUS_TEST_XRAY_BINARY', 'xray_binary')
    monkeypatch.setattr(cfg, 'xray_binary', binary)
    a_vps()
    a_user()
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT},
                   'web-socks': {'enabled': '1', 'port': SOCKS_PORT}})
    path = str(tmp_path / 'xray-telegram.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'log': {'loglevel': 'warning'}, 'inbounds': webproxy.xray_inbounds(),
                   'outbounds': [{'protocol': 'freedom', 'tag': 'direct'}]}, handle)
    done = subprocess.run([binary, 'run', '-test', '-config', path],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (done.stdout + done.stderr)[-800:]
    # …and the panel's own config — edge inbounds, WARP, the lot — still validates
    # with the Telegram inbounds added to it.
    monkeypatch.setattr(cfg, 'xray_config', str(tmp_path / 'panel.json'))
    xray.write_config()
    panel = subprocess.run([binary, 'run', '-test', '-config', cfg.xray_config],
                           capture_output=True, text=True, timeout=60)
    assert panel.returncode == 0, (panel.stdout + panel.stderr)[-800:]


@pytest.mark.skipif(binary_for('NEXUS_TEST_XRAY_BINARY', 'xray_binary') is None,
                    reason='the Xray binary is not installed here')
def test_the_http_web_proxy_really_carries_traffic(monkeypatch, tmp_path):
    """A real request through the real proxy, against a local origin.

    «The config validates» and «a browser can use it» are two different claims;
    only the second one is worth having, so the panel's own inbound is started and
    an HTTP request is sent *through* it. No external network is touched: the
    origin is served by this test.
    """
    import httpx

    binary = binary_for('NEXUS_TEST_XRAY_BINARY', 'xray_binary')
    monkeypatch.setattr(cfg, 'xray_binary', binary)
    a_vps()
    a_user()
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT}})

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'nexus-through-the-proxy'
            self.send_response(200)
            self.send_header('content-type', 'text/plain')
            self.send_header('content-length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    origin = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    config = {'log': {'loglevel': 'warning'}, 'inbounds': webproxy.xray_inbounds(),
              'outbounds': [{'protocol': 'freedom', 'tag': 'direct'}]}
    path = str(tmp_path / 'xray-tg.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(config, handle)
    proc = subprocess.Popen([binary, 'run', '-config', path],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            probe = socket.socket()
            if probe.connect_ex(('127.0.0.1', int(HTTP_PORT))) == 0:
                probe.close()
                break
            probe.close()
            time.sleep(0.2)
        target = f'http://127.0.0.1:{origin.server_port}/'
        with httpx.Client(proxy=f'http://tguser:{USER_UUID}@127.0.0.1:{HTTP_PORT}', timeout=10) as good:
            assert good.get(target).text == 'nexus-through-the-proxy'
        # A wrong password is not let through: that is the whole point of holding
        # one account per user instead of one shared secret.
        with httpx.Client(proxy=f'http://tguser:wrong@127.0.0.1:{HTTP_PORT}', timeout=10) as bad:
            try:
                assert bad.get(target).status_code not in (200,)
            except httpx.HTTPError:
                pass
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        origin.shutdown()


# --------------------------------------------------------------- Telegram Web
def assert_disabled():
    response = client.get('/tg/k/')
    assert response.status_code == 403
    assert 'خاموش است' in response.text


def test_the_web_app_proxy_is_off_until_it_is_switched_on():
    assert_disabled()
    _set('tg_web_enabled', '1')
    assert webapp.enabled() is True


def test_only_telegram_hosts_may_be_proxied():
    """The allowlist is the difference between a proxy and an SSRF hole."""
    _set('tg_web_enabled', '1')
    for good in ('web.telegram.org', 'pluto.web.telegram.org', 'telegram.org', 't.me'):
        assert webapp.allowed_host(good) is True, good
    for bad in ('example.com', 'web.telegram.org.evil.example', '', '127.0.0.1:22',
                'localhost', 'metadata.google.internal', 'web.telegram.org/../x'):
        assert webapp.allowed_host(bad) is False, bad
    assert client.get('/tg/__p/evil.example/secret').status_code == 403


@pytest.fixture
def telegram_origin(monkeypatch):
    """A stand-in for web.telegram.org, so the proxy is tested without the network."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/k/'):
                body = (b'<!doctype html><html><head><title>Telegram Web</title></head><body>'
                        b'<script type="module" crossorigin src="./index-abc.js"></script>'
                        b'<link rel="icon" href="' + cfg.telegram_web_origin.encode() + b'/favicon.ico">'
                        b'</body></html>')
                self.respond(200, body, 'text/html')
            elif self.path.startswith('/asset.js'):
                self.respond(200, b"const host = 'pluto.web.telegram.org';", 'application/javascript')
            elif self.path.startswith('/jump'):
                self.send_response(302)
                self.send_header('location', cfg.telegram_web_origin + '/after')
                self.end_headers()
            elif self.path.startswith('/cookie'):
                body = b'ok'
                self.send_response(200)
                self.send_header('content-type', 'text/plain')
                self.send_header('set-cookie', 'session=abc; Domain=.web.telegram.org; Path=/')
                self.send_header('content-length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.respond(404, b'missing', 'text/plain')

        def do_POST(self):
            length = int(self.headers.get('content-length') or 0)
            body = self.rfile.read(length)
            self.respond(200, b'posted:' + body, 'text/plain')

        def respond(self, status, body, content_type):
            self.send_response(status)
            self.send_header('content-type', content_type)
            self.send_header('content-length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(cfg, 'telegram_web_origin', origin)
    _set('tg_web_enabled', '1')
    try:
        yield origin
    finally:
        server.shutdown()


def test_the_app_shell_is_served_with_the_shim_and_rewritten_urls(telegram_origin):
    response = client.get('/tg/k/')
    assert response.status_code == 200
    text = response.text
    assert 'NEXUS · Telegram Web proxy shim' in text
    # The shim has to run before the bundle does.
    assert text.index('NEXUS · Telegram Web proxy shim') < text.index('index-abc.js')
    # An absolute URL back to the origin becomes a path on this deployment, so the
    # browser does not dial Telegram directly (which is the blocked half).
    assert f'{telegram_origin}/favicon.ico' not in text
    assert f'{webapp.path_prefix()}/favicon.ico' in text
    # The shim carries the allowlist and both tunnels, so the socket and the API
    # calls the app computes at runtime are routed through this deployment instead
    # of being opened straight at Telegram (the half that is blocked).
    assert f"'{webapp.default_host()}'" in text and "'telegram.org'" in text
    assert '/__ws/' in text and '/__p/' in text
    assert 'WebSocket' in text and 'XMLHttpRequest' in text
    # The sponsor is a row in the authenticated chat list, not an admin-panel
    # link. It waits for Telegram's list, survives its re-renders, and opens the
    # public channel through this proxy so a blocked direct t.me request is not
    # what the user depends on.
    assert f"const SPONSOR_URL = '{webapp.SPONSOR_URL}'" in text
    assert f"const SPONSOR_HANDLE = '{webapp.SPONSOR_HANDLE}'" in text
    assert "const SPONSOR_ID = 'nexus-sponsor-chat'" in text
    assert "PREFIX + '/__p/t.me/' + SPONSOR_HANDLE" in text
    assert "'#chat-list'" in text and 'findChatList' in text
    assert 'new MutationObserver(schedule)' in text
    assert "entry.target = '_blank'" in text and "entry.rel = 'noopener noreferrer'" in text


def test_a_non_html_asset_streams_through_untouched(telegram_origin):
    response = client.get('/tg/asset.js')
    assert response.status_code == 200
    assert "pluto.web.telegram.org" in response.text
    assert 'shim' not in response.text.lower()


def test_a_redirect_back_to_telegram_stays_on_this_domain(telegram_origin):
    response = client.get('/tg/jump', follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['location'] == webapp.path_prefix() + '/after'


def test_a_cookie_telegram_set_is_rescoped_to_this_domain(telegram_origin):
    response = client.get('/tg/cookie')
    cookie = response.headers.get('set-cookie', '')
    assert 'session=abc' in cookie
    assert 'Domain' not in cookie and '.web.telegram.org' not in cookie


def test_a_post_body_reaches_the_upstream(telegram_origin):
    response = client.post('/tg/login', content=b'user=1')
    assert response.status_code == 200
    assert response.text == 'posted:user=1'


def test_the_api_prefix_proxies_the_telegram_web_hosts(telegram_origin):
    """``/tg/__p/<host>/…`` is what the shim rewrites absolute calls to.

    The host is the one this test's origin answers on: in production that is
    ``web.telegram.org``, which is the *default* host, so the same branch that
    injects the shim has to serve the ``__p`` form as well.
    """
    response = client.get('/tg/__p/127.0.0.1/k/')
    assert response.status_code == 200
    assert 'NEXUS · Telegram Web proxy shim' in response.text


@pytest.fixture
def telegram_socket(monkeypatch):
    """A stand-in for the app's own API socket (``<dc>.web.telegram.org/apiws``)."""
    from websockets.sync.server import serve

    def echo(socket):
        for message in socket:
            socket.send('echo:' + message)

    server = serve(echo, '127.0.0.1', 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(cfg, 'telegram_web_origin', f'http://127.0.0.1:{server.socket.getsockname()[1]}')
    _set('tg_web_enabled', '1')
    try:
        yield server
    finally:
        server.shutdown()


def test_the_api_socket_is_tunnelled_through_this_deployment(telegram_socket):
    """The half a plain HTTP reverse proxy cannot do.

    Telegram Web opens a WebSocket to ``<dc>.web.telegram.org``; without this the
    page loads from our domain and then never connects. The shim rewrites the URL
    to ``/tg/__ws/<host>/…`` and this is the bridge behind it.
    """
    with client.websocket_connect('/tg/__ws/127.0.0.1/apiws') as socket:
        socket.send_text('hello')
        assert socket.receive_text() == 'echo:hello'


def test_an_unlisted_host_cannot_open_a_tunnel():
    with pytest.raises(Exception):
        with client.websocket_connect('/tg/__ws/evil.example/apiws') as socket:
            socket.send_text('hello')
            socket.receive_text()


def test_the_status_reports_what_this_server_can_reach(telegram_origin):
    """"Configured" and "reachable" are two different answers."""
    result = asyncio.run(webapp.probe())
    assert result['ok'] is True and result['status'] == 200


def test_the_web_proxy_status_carries_the_public_url():
    status = webapp.status('https://panel.example.com')
    assert status['path'] == webapp.path_prefix()
    assert status['url'] == f'https://panel.example.com{webapp.path_prefix()}/'
    assert status['enabled'] is False


# -------------------------------------------------------------------- service
def test_nothing_is_published_until_each_switch_is_on():
    a_vps()
    a_user()
    payload = tg_service.payload('https://panel.example.com')
    assert payload['mtproto']['published'] is False
    assert payload['webproxy']['counts']['published'] == 0
    assert payload['webapp']['enabled'] is False
    assert payload['counts']['published'] == 0
    # …and an end user's status window grows no Telegram section at all.
    assert tg_service.portal_payload('https://panel.example.com', {'username': 'tguser',
                                                                  'uuid': USER_UUID}) == {}


def test_the_portal_payload_carries_only_this_users_credential(mtg_installed):
    a_vps()
    a_user('firstuser', USER_UUID)
    a_user('seconduser', '99999999-8888-7777-6666-555555555555')
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT}})
    mtproto.save({'enabled': '1', 'port': MTPROTO_PORT})
    mtproto._proc = FakeProc()
    _set('tg_web_enabled', '1')
    payload = tg_service.portal_payload('https://panel.example.com',
                                        {'username': 'firstuser', 'uuid': USER_UUID})
    assert payload['mtproto']['url'].startswith('tg://proxy?')
    assert payload['mtproto']['url'].startswith('tg://proxy?')
    assert [item['username'] for item in payload['lines']] == ['firstuser']
    assert 'seconduser' not in json.dumps(payload)
    assert payload['web']['url'] == 'https://panel.example.com/tg/'


def test_the_panel_endpoint_needs_a_session_and_reports_every_switch():
    assert client.get('/api/telegram').status_code == 401
    assert client.post('/api/telegram', json={}).status_code == 401
    assert client.post('/api/telegram/probe', json={}).status_code == 401
    a_vps()
    a_user()
    payload = client.get('/api/telegram', headers=h()).json()
    # The card reads every one of these off the top level, so the shape is pinned.
    for key in ('mtproto', 'webproxy', 'webapp', 'notes', 'counts', 'host', 'tcp', 'enabled'):
        assert key in payload, key
    assert payload['success'] is True
    assert payload['webproxy']['accounts'][0]['username'] == 'tguser'


def test_saving_turns_the_switches_on_and_reports_the_result():
    a_vps()
    a_user()
    saved = client.post('/api/telegram', headers=h(), json={
        'mtproto': {'enabled': '1', 'port': MTPROTO_PORT, 'domain': 'cdn.example.com'},
        'webproxy': {'web-http': {'enabled': '1', 'port': HTTP_PORT}},
        'webapp': {'enabled': '1'},
    }).json()
    assert saved['success'] is True
    assert 'tg_mtproto_enabled' in saved['changed'] and 'tg_web_http_port' in saved['changed']
    assert saved['mtproto']['enabled'] is True and saved['mtproto']['port'] == int(MTPROTO_PORT)
    assert saved['mtproto']['domain'] == 'cdn.example.com'
    assert saved['webapp']['enabled'] is True
    catalog = {item['id']: item for item in saved['webproxy']['catalog']}
    assert catalog['web-http']['enabled'] is True and catalog['web-http']['port'] == int(HTTP_PORT)
    assert catalog['web-socks']['enabled'] is False
    # The web proxies are in the engine's config, so whether they are *published*
    # depends on the engine really running them, and the payload has to say which.
    assert saved['sync']['xray']['running'] in (True, False)
    assert client.get('/api/telegram', headers=h()).json()['webproxy']['catalog'][0]['enabled'] is True


def test_a_rejected_save_leaves_the_settings_alone():
    bad = client.post('/api/telegram', headers=h(), json={'mtproto': {'enabled': '1', 'port': 'nope'}})
    assert bad.status_code == 400 and 'پورت' in bad.json()['detail']
    assert mtproto.enabled() is False
    bad_proxy = client.post('/api/telegram', headers=h(),
                            json={'webproxy': {'web-socks': {'enabled': '1', 'port': '70000'}}})
    assert bad_proxy.status_code == 400
    assert webproxy.enabled(webproxy.BY_ID['web-socks']) is False


def test_rotating_the_secret_reissues_the_link():
    a_vps()
    mtproto.save({'enabled': '1', 'port': MTPROTO_PORT})
    before = mtproto.secret()
    rotated = client.post('/api/telegram', headers=h(), json={'action': 'rotate'}).json()
    assert rotated['secret'] != before
    assert mtproto.secret() == rotated['secret']
    assert rotated['mtproto']['secret'] == rotated['secret']


def test_the_public_status_window_carries_the_telegram_section(mtg_installed):
    a_vps()
    user = a_user()
    webproxy.save({'web-http': {'enabled': '1', 'port': HTTP_PORT}})
    mtproto.save({'enabled': '1', 'port': MTPROTO_PORT})
    mtproto._proc = FakeProc()
    payload = client.get(f"/portal/{user['uuid']}/json").json()
    assert payload['telegram']['mtproto']['url'].startswith('tg://proxy?')
    assert payload['telegram']['lines'][0]['url'].startswith('http://tguser:')


# ------------------------------------------------------- the panel and image
def test_the_panel_ships_the_tab_that_drives_all_this():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'templates', 'index.html'), encoding='utf-8') as handle:
        html = handle.read()
    for element in ('section-telegram', 'tgMtTag', 'tgMtEnabled', 'tgMtPort', 'tgMtLinks',
                    'tgSave', 'tgReload', 'tgMtRotate', 'tgWebProfiles', 'tgWebAccounts',
                    'tgAppEnabled', 'tgAppProbe', 'tgAppInfo', 'tgNotes',
                    # The proxy-type mode and the WEB card: the default is «only the
                    # WEB proxy», so both the picker and the card a deployment lands
                    # on have to be in the template.
                    'tgMode', 'tgModeSave', 'tgModeTag', 'tgOtherTypes', 'tgOtherWeb',
                    'tgRelayEnabled', 'tgRelaySave', 'tgRelayRotate', 'tgRelayDomain',
                    'tgRelayInfo', 'tgRelayLinks', 'tgRelayTag'):
        assert f'id="{element}"' in html, element
    with open(os.path.join(root, 'static', 'js', 'views', 'telegram.js'), encoding='utf-8') as handle:
        view = handle.read()
    assert '/api/telegram' in view and 'renderMtproto' in view
    # The mode picker and the WEB card have to be wired in the view, not just
    # present in the markup: a select nobody reads is a switch that does nothing.
    assert 'renderMode' in view and 'tgModeSave' in view and 'renderWebRelay' in view
    with open(os.path.join(root, 'templates', 'portal.html'), encoding='utf-8') as handle:
        portal = handle.read()
    assert 'p.telegram.webrel' in portal
    with open(os.path.join(root, 'templates', 'portal.html'), encoding='utf-8') as handle:
        portal = handle.read()
    assert 'p.telegram.mtproto' in portal and 'p.telegram.lines' in portal
    with open(os.path.join(root, 'static', 'js', 'store.js'), encoding='utf-8') as handle:
        store = handle.read()
    assert "{ id: 'telegram'" in store


def test_the_image_ships_the_mtproto_binary():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'Dockerfile'), encoding='utf-8') as handle:
        dockerfile = handle.read()
    assert 'MTG_VERSION' in dockerfile, 'pin the version'
    assert '9seconds/mtg' in dockerfile
    assert 'COPY --from=engines /usr/local/bin/mtg' in dockerfile
    # The ports the panel publishes have to be declared, or a VPS deployment has
    # no idea what to forward.
    assert '8446' in dockerfile and '8448' in dockerfile and '8449' in dockerfile


def test_the_worker_carries_the_telegram_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'cloudflare-worker', 'worker.js'), encoding='utf-8') as handle:
        worker = handle.read()
    assert "const TELEGRAM_PREFIX = '/tg'" in worker
    assert 'isTelegramPath(url.pathname)' in worker
    assert "forward.headers.set('X-Forwarded-Proto', 'https')" in worker
