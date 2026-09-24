"""The one-time automatic configuration pass.

The claim under test is narrow on purpose: after a deploy with no admin in the
loop, the panel switches on exactly the capability that needs nothing arranged for
it — Telegram Desktop's WEB proxy — and it never overrules a human.

So the tests are about the three ways this could go wrong: switching on something
that cannot work, doing it twice, or doing it *again* after an admin said no.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-autoconfig-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-autoconfig-test.db')

import pytest
from fastapi.testclient import TestClient

from app import autoconfig, ports
from app.config import settings as cfg
from app.db import execute, init_db
from app.main import _setting, app
from app.telegram import webrelay

init_db()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}

KEYS = (autoconfig.DONE, autoconfig.SWITCH, autoconfig.LOG,
        webrelay.ENABLED, webrelay.SECRET, webrelay.DOMAIN,
        # The TCP-proxy half stores its mapping and its markers in the same table,
        # and one of its tests switches a capability on to have something to
        # forward — none of that may reach another test or another module.
        ports.DONE, ports.LOG, ports.STORE, 'tg_mtproto_enabled')


@pytest.fixture(autouse=True)
def clean():
    """Nothing this pass writes may leak into another test or another module."""
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))
    yield
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))


@pytest.fixture
def relay_installed(monkeypatch):
    monkeypatch.setattr(webrelay, 'available', lambda: True)


def test_a_fresh_deployment_switches_the_web_proxy_on(relay_installed):
    assert autoconfig.apply(force=True) == ['webrelay']
    assert webrelay.enabled() is True
    # The domain is deliberately left empty, so the link follows the deployment's
    # own hostname (and a redeploy that gets a new one keeps working).
    assert webrelay._setting(webrelay.DOMAIN) in (None, '')


def test_the_pass_runs_once_and_does_not_fight_the_admin(relay_installed):
    autoconfig.apply(force=True)
    assert autoconfig.apply(force=True) == []          # the marker is the guard
    webrelay.save({'enabled': '0'})                    # an admin says no
    assert autoconfig.apply(force=True) == []
    assert webrelay.enabled() is False


def test_an_admin_choice_is_never_overwritten(relay_installed):
    webrelay.save({'enabled': '0'})
    assert autoconfig.apply(force=True) == []
    assert webrelay.enabled() is False


def test_a_host_that_cannot_serve_it_switches_nothing_on(monkeypatch):
    """Without the relay binary the switch would only produce a card full of reasons."""
    monkeypatch.setattr(webrelay, 'available', lambda: False)
    assert autoconfig.apply(force=True) == []
    assert webrelay._setting(webrelay.ENABLED) is None
    candidates = autoconfig.candidates()
    assert candidates and candidates[0]['ready'] is False
    assert 'mtproto-proxy' in candidates[0]['reason']


def test_a_boot_without_the_relay_binary_does_not_retire_the_pass(monkeypatch):
    """The marker means «the pass has nothing left to do», not «the pass ran».

    A relay binary that is missing when the container boots is the one thing that
    can change before the next boot (a redeploy onto a fixed image), so the WEB
    proxy still has to be switched on by itself then — instead of becoming the one
    thing an admin has to be asked to turn on by hand.
    """
    monkeypatch.setattr(webrelay, 'available', lambda: False)
    assert autoconfig.apply(force=True) == []
    assert autoconfig._setting(autoconfig.DONE) is None      # not retired
    monkeypatch.setattr(webrelay, 'available', lambda: True)
    assert autoconfig.apply(force=True) == ['webrelay']      # done, unasked
    assert webrelay.enabled() is True
    assert autoconfig._setting(autoconfig.DONE) is not None


def test_an_admin_who_said_no_still_ends_the_pass(monkeypatch, relay_installed):
    """The opposite case: once an admin has had their say, there is nothing left to
    do, so the marker is correct and the pass does not raise the question again."""
    webrelay.save({'enabled': '0'})
    assert autoconfig.apply(force=True) == []
    assert autoconfig._setting(autoconfig.DONE) is not None
    assert webrelay.enabled() is False


def test_the_whole_pass_can_be_switched_off(relay_installed):
    autoconfig._store(autoconfig.SWITCH, '0')
    assert autoconfig.allowed() is False
    assert autoconfig.apply(force=True) == []
    assert webrelay._setting(webrelay.ENABLED) is None
    assert autoconfig._setting(autoconfig.DONE) is None   # not even the marker


def test_the_test_environment_never_configures_itself(monkeypatch, relay_installed):
    """The suite must not depend on a boot-time switch: only an explicit call acts.

    The environment is pinned here rather than read from the process, because the
    suite imports the settings object once and another module may have moved it.
    """
    monkeypatch.setattr(autoconfig.settings, 'environment', 'test')
    assert autoconfig.apply() == []
    assert webrelay._setting(webrelay.ENABLED) is None
    assert autoconfig._setting(autoconfig.DONE) is None


def test_the_panel_can_report_what_the_pass_did(relay_installed):
    autoconfig.apply(force=True)
    state = autoconfig.state()
    assert state['allowed'] is True and state['done'] is True
    assert state['applied'] == ['webrelay'] and state['ran_at'] > 0
    assert state['candidates'] == []


# --------------------------------------------------------------- the panel API
def test_the_admin_api_needs_a_session():
    assert client.get('/api/system/autoconfig').status_code == 401
    assert client.post('/api/system/autoconfig', json={}).status_code == 401


def test_the_admin_api_reports_the_pass_and_refuses_an_unknown_action(relay_installed):
    body = client.get('/api/system/autoconfig', headers=h()).json()
    assert body['success'] is True
    # The card reads every one of these off the top level, so the shape is pinned.
    assert {'allowed', 'done', 'ran_at', 'applied', 'candidates', 'railway'} <= set(body)
    assert {'api', 'candidates', 'proxies', 'created', 'done'} <= set(body['railway'])
    assert {'configured', 'missing', 'endpoint'} <= set(body['railway']['api'])
    assert {'catalog', 'count'} <= set(body['railway']['proxies'])
    for item in body['railway']['proxies']['catalog']:
        assert {'id', 'label', 'port', 'enabled', 'proxied', 'public_host', 'public_port'} <= set(item)
    bad = client.post('/api/system/autoconfig', headers=h(), json={'action': 'nope'})
    assert bad.status_code == 400 and 'action' in bad.json()['detail']


def test_the_railway_half_needs_a_token_and_says_so(monkeypatch):
    """Without a token nothing is created — and the card is told which variable is
    missing rather than being handed a silent success."""
    for name in ('RAILWAY_API_TOKEN', 'RAILWAY_TOKEN', 'NEXUS_RAILWAY_TOKEN'):
        monkeypatch.delenv(name, raising=False)
    response = client.post('/api/system/autoconfig', headers=h(), json={'action': 'railway'})
    assert response.status_code == 400
    assert 'RAILWAY_API_TOKEN' in response.json()['detail']


def _fake_railway(monkeypatch, existing=()):
    """Railway as the pass sees it: no token needed, one proxy per created port.

    Returns the variables the pass wrote, so a test can assert that the HTTP port
    was pinned (and that nothing else was touched).
    """
    from app import railway

    written = {}
    monkeypatch.setattr(railway, 'configured', lambda: True)
    monkeypatch.setattr(railway, 'proxies', lambda: (list(existing), ''))
    monkeypatch.setattr(railway, 'create', lambda port: (
        {'id': f'p{port}', 'domain': 'roundhouse.proxy.rlwy.net',
         'proxyPort': 20000 + int(port), 'applicationPort': int(port),
         'syncStatus': 'ACTIVE'}, ''))
    monkeypatch.setattr(railway, 'redeploy', lambda: (True, ''))

    def set_variable(name, value, redeploy=False):
        written[name] = value
        return True, ''

    monkeypatch.setattr(railway, 'set_variable', set_variable)
    # The edge port the container is really on, so the real ``pin_http_port``
    # resolution runs instead of being stubbed away.
    monkeypatch.setenv('PORT', '8080')
    monkeypatch.delenv('NEXUS_HTTP_PORT', raising=False)
    return written


def test_the_railway_half_forwards_exactly_what_is_switched_on(monkeypatch):
    """One TCP proxy per capability an admin enabled — and no proxy for one that
    is off, because a public port in front of a listener nobody binds is a port
    that answers nothing."""
    _fake_railway(monkeypatch)
    outcome = autoconfig.railway_ports(force=True)
    assert outcome['ok'] is True and outcome['reason'] == ''
    enabled = {item['port'] for item in ports.catalog() if item['enabled']}
    assert set(outcome['created']) == enabled
    # Reality's switch is the platform itself, so it is always in the set.
    assert 8443 in outcome['created']
    # What the pass learned is what a link is built from, not the listen port.
    assert ports.published_port(8443) == 28443
    assert ports.published_host(8443) == 'roundhouse.proxy.rlwy.net'
    assert autoconfig.state()['railway']['created'] == [8443]


def test_the_pass_pins_the_http_port_before_it_moves_it(monkeypatch):
    """Railway hands a service with a TCP proxy that proxy's *application* port as
    ``PORT`` — and that is the port Xray already owns for the raw transport, so the
    panel would come back up crash-looping on a port somebody else is listening on.
    Pinning the port the edge is really on, once, is what keeps both on one service.
    """
    written = _fake_railway(monkeypatch)
    assert autoconfig.railway_ports(force=True)['created'] == [8443]
    assert written == {'PORT': '8080'}


def test_a_pass_that_creates_nothing_writes_no_variable(monkeypatch):
    """The pin belongs to the change that needs it, not to every boot."""
    written = _fake_railway(monkeypatch, existing=[
        {'id': 'p8443', 'domain': 'roundhouse.proxy.rlwy.net', 'proxyPort': 28443,
         'applicationPort': 8443, 'syncStatus': 'ACTIVE'}])
    outcome = autoconfig.railway_ports(force=True)
    assert outcome['created'] == [] and written == {}


def test_a_pin_that_cannot_be_written_is_reported_not_hidden(monkeypatch):
    """A proxy in front of a port the panel cannot come back up on is not a success."""
    from app import railway

    _fake_railway(monkeypatch)
    monkeypatch.setattr(railway, 'pin_http_port',
                        lambda: (False, 'توکن API رِیلوی مجاز نیست'))
    outcome = autoconfig.railway_ports(force=True)
    assert outcome['ok'] is False and outcome['failed'][0]['id'] == 'http-port'
    assert 'مجاز نیست' in outcome['reason']


def test_the_http_port_prefers_the_operators_override(monkeypatch):
    """The ``Dockerfile`` gives uvicorn the same two names in the same order.

    If these ever disagree, the pass would pin the edge to a port nothing serves on
    — which is the failure this whole path exists to prevent.
    """
    from app import railway

    written = {}

    def record(name, value, redeploy=False):
        written[name] = value
        return True, ''

    monkeypatch.setattr(railway, 'set_variable', record)
    monkeypatch.setenv('PORT', '8080')
    monkeypatch.delenv('NEXUS_HTTP_PORT', raising=False)
    assert railway.http_port() == 8080
    assert railway.pin_http_port() == (True, '')
    assert written == {'PORT': '8080'}
    monkeypatch.setenv('NEXUS_HTTP_PORT', '9000')
    assert railway.http_port() == 9000            # the operator's override wins
    monkeypatch.setenv('PORT', 'nonsense')
    monkeypatch.delenv('NEXUS_HTTP_PORT', raising=False)
    assert railway.http_port() == 8080            # the Dockerfile's own default


def test_a_later_run_remembers_the_ports_an_earlier_one_created(monkeypatch):
    """An admin who switches MTProto on afterwards creates its proxy in a second
    run; the card must still list both, or the first port looks like it vanished."""
    _fake_railway(monkeypatch)
    assert autoconfig.railway_ports(force=True)['created'] == [8443]
    # The marker is what stops the automatic path once it has run; the panel's own
    # button (and the environment changing) is what runs it a second time.
    execute('DELETE FROM settings WHERE key=?', (ports.DONE,))
    execute("INSERT INTO settings(key,value) VALUES('tg_mtproto_enabled','1') "
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value')
    _fake_railway(monkeypatch, existing=[{'id': 'p8443', 'domain': 'roundhouse.proxy.rlwy.net',
                                          'proxyPort': 28443, 'applicationPort': 8443,
                                          'syncStatus': 'ACTIVE'}])
    second = autoconfig.railway_ports(force=True)
    assert second['created'] == [8446]          # 8443 already has a proxy
    assert second['known'] == [8443, 8446]
    assert autoconfig.state()['railway']['created'] == [8443, 8446]


def test_the_ports_the_panel_publishes_are_the_ones_a_proxy_forwards():
    """The whole point of the mapping: Railway's public port is random, so a link
    has to carry the forwarded one — and a host that owns its ports (no proxy)
    keeps the listen port, which is what makes VPS and Railway one code path."""
    from app import ports
    ports.save({})
    try:
        assert ports.published_port(8446) == 8446            # nothing stored
        assert ports.published_host(8446) == ''
        assert ports.proxied(8446) is False
        ports.save({8446: {'id': 'p1', 'host': 'roundhouse.proxy.rlwy.net', 'port': 23177}})
        assert ports.published_port(8446) == 23177
        assert ports.published_host(8446) == 'roundhouse.proxy.rlwy.net'
        assert ports.proxied(8446) is True
        assert ports.published_port(8448, 8448) == 8448      # a port with no proxy
        assert ports.status()['count'] == 1
    finally:
        ports.save({})
