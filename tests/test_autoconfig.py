"""The one-time automatic configuration pass.

The claim under test is narrow on purpose, and it is a *negative* one: a deploy
with no admin in the loop switches **nothing** on by itself. A capability whose
carrier is the deployment's own domain is exactly the thing a boot pass must never
imply — a domain that serves Telegram-proxy traffic is the domain a network blocks,
and an address recognised that way stops opening without a VPN.

So the tests are about the three ways this could go wrong: switching something on
that nobody asked for, switching it on a second time, or overriding an admin who has
already had their say.
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

init_db()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}

KEYS = (autoconfig.DONE, autoconfig.SWITCH, autoconfig.LOG,
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


def test_a_fresh_deployment_switches_nothing_on():
    """Nothing a deploy implies: the pass's only job is the marker, and it flips
    no capability switch while writing it."""
    assert autoconfig.apply(force=True) == []
    assert _setting('tg_mtproto_enabled') is None


def test_the_pass_runs_once():
    assert autoconfig.apply(force=True) == []
    assert autoconfig.apply(force=True) == []          # the marker is the guard
    assert autoconfig._setting(autoconfig.DONE) is not None


def test_an_admin_choice_is_never_overwritten():
    """Whichever way an admin set it, a later boot cannot flip the switch: the pass
    runs once, writes its marker, and touches no setting after that."""
    execute("INSERT INTO settings(key,value) VALUES('tg_mtproto_enabled','1') "
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value')
    assert autoconfig.apply(force=True) == []
    assert _setting('tg_mtproto_enabled') == '1'
    execute("UPDATE settings SET value='0' WHERE key='tg_mtproto_enabled'")
    assert autoconfig.apply(force=True) == []
    assert _setting('tg_mtproto_enabled') == '0'


def test_the_pass_offers_nothing_to_switch_on():
    """The card has nothing left to offer: anything an admin may enable is an
    ordinary card of its own, so the candidate list stays empty and honest."""
    assert autoconfig.candidates() == []
    assert autoconfig.apply(force=True) == []
    assert autoconfig.candidates() == []


def test_the_marker_goes_down_even_when_nothing_was_created(monkeypatch):
    """«Nothing left to do» must stay true on a host without a Railway token too:
    the marker is about the pass having run, not about what Railway answered."""
    for name in ('RAILWAY_API_TOKEN', 'RAILWAY_TOKEN', 'NEXUS_RAILWAY_TOKEN'):
        monkeypatch.delenv(name, raising=False)
    assert autoconfig.apply(force=True) == []
    assert autoconfig._setting(autoconfig.DONE) is not None


def test_the_whole_pass_can_be_switched_off():
    autoconfig._store(autoconfig.SWITCH, '0')
    assert autoconfig.allowed() is False
    assert autoconfig.apply(force=True) == []
    assert autoconfig._setting(autoconfig.DONE) is None   # not even the marker


def test_the_test_environment_never_configures_itself(monkeypatch):
    """The suite must not depend on a boot-time switch: only an explicit call acts.

    The environment is pinned here rather than read from the process, because the
    suite imports the settings object once and another module may have moved it.
    """
    monkeypatch.setattr(autoconfig.settings, 'environment', 'test')
    assert autoconfig.apply() == []
    assert autoconfig._setting(autoconfig.DONE) is None


def test_the_panel_can_report_what_the_pass_did():
    autoconfig.apply(force=True)
    state = autoconfig.state()
    assert state['allowed'] is True and state['done'] is True
    assert state['applied'] == [] and state['ran_at'] > 0
    # Nothing is waiting to be switched on: the offer list is empty on purpose.
    assert state['candidates'] == []


# --------------------------------------------------------------- the panel API
def test_the_admin_api_needs_a_session():
    assert client.get('/api/system/autoconfig').status_code == 401
    assert client.post('/api/system/autoconfig', json={}).status_code == 401


def test_the_admin_api_reports_the_pass_and_refuses_an_unknown_action():
    body = client.get('/api/system/autoconfig', headers=h()).json()
    assert body['success'] is True
    # The card reads every one of these off the top level, so the shape is pinned.
    assert {'allowed', 'done', 'ran_at', 'applied', 'candidates', 'railway'} <= set(body)
    assert {'api', 'candidates', 'proxies', 'created', 'done'} <= set(body['railway'])
    assert {'configured', 'missing', 'endpoint'} <= set(body['railway']['api'])
    # An admin holding one value (the project id off the dashboard URL) has to be
    # told where the other three come from, and that a token is what unblocks the
    # call — otherwise the card is a list of variable names with no way to satisfy
    # them, which is exactly how a project id ends up looking like a fix.
    guide = ' '.join(body['railway']['api']['guide'])
    assert 'RAILWAY_API_TOKEN' in guide and 'RAILWAY_PROJECT_ID' in guide
    assert 'Account Settings' in guide
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
