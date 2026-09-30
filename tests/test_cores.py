"""The second engines: AnyTLS and TUIC v5.

Xray has no inbound for either protocol, so one of sing-box/mihomo serves them
from the same container. Three things have to be true for that to be worth having,
and all three are tested here:

* the config the panel renders is one the engine itself accepts (validated with
  the real binary whenever one is installed — ``NEXUS_TEST_SINGBOX_BINARY`` /
  ``NEXUS_TEST_MIHOMO_BINARY`` point the tests at a build);
* a protocol is published **only** when its listener is running and its port is
  reachable, so no subscription ever carries a link nothing answers on;
* the link reaches every format the client catalog offers, with the user's own
  credential rather than one shared secret.
"""
import json
import os
import signal
import subprocess

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-cores-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-cores-test.db')

import pytest
from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.cores import engines, profiles, service as cores
from app import runtime
from app.db import execute, init_db
from app.main import _setting, _set, app
from app.subscriptions import transports as tp
from app.subscriptions.generator import render

init_db()
client = TestClient(app)

ANYTLS_UUID = '11111111-2222-3333-4444-555555555555'
PUBLIC_HOST = '203.0.113.10'


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


def stop_stray_engines():
    """Terminate anything a previous test left listening.

    ``asyncio.subprocess`` ties a process to the loop that spawned it and the
    HTTP tests go through a client that closes its loop per request, so the
    supervisor's own ``stop_all`` cannot always await them — and the transport's
    own ``kill`` goes through that closed loop. Signalling the pid directly is
    loop-free, which is all a leftover listener needs.
    """
    for proc in list(cores._procs.values()):
        if not proc or proc.returncode is not None:
            continue
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except Exception:
            pass
    cores._procs.clear()


def reset_core_settings():
    """Every test starts from the shipped state: nothing hosted, nothing enabled."""
    stop_stray_engines()
    for item in profiles.PROFILES:
        for key in (profiles.enabled_key(item), profiles.port_key(item), profiles.engine_key(item)):
            execute('DELETE FROM settings WHERE key=?', (key,))
    for key in ('core_sni', 'core_tls_cert', 'core_tls_key', 'direct_host', 'direct_port'):
        execute('DELETE FROM settings WHERE key=?', (key,))
    cores._published = None
    cores._state.clear()
    cores._procs.clear()
    cores._hashes.clear()


@pytest.fixture(autouse=True)
def clean():
    reset_core_settings()
    yield
    reset_core_settings()


@pytest.fixture(autouse=True)
def engines_the_app_can_actually_see(monkeypatch, tmp_path):
    """Point the app at the same engine builds the tests use.

    ``binary_for`` reads ``NEXUS_TEST_*_BINARY``; the panel reads ``settings``.
    When a real build is provided both are pointed at it *and* at a scratch
    directory, so a test never writes a listener config into ``/data``. With no
    override the shipped defaults are left exactly as they ship — a host without
    the binaries has to behave like one.
    """
    paths = {
        engines.SINGBOX: ('singbox_binary', 'NEXUS_TEST_SINGBOX_BINARY', 'sing-box.json'),
        engines.MIHOMO: ('mihomo_binary', 'NEXUS_TEST_MIHOMO_BINARY', 'mihomo.yaml'),
    }
    for engine, (setting_name, env, filename) in paths.items():
        override = os.environ.get(env)
        if not override or not os.path.exists(override):
            continue
        monkeypatch.setattr(cfg, setting_name, override)
        if engine == engines.SINGBOX:
            monkeypatch.setattr(cfg, 'singbox_config', str(tmp_path / filename))
        else:
            monkeypatch.setattr(cfg, 'mihomo_config', str(tmp_path / filename))
            monkeypatch.setattr(cfg, 'mihomo_home', str(tmp_path / 'mihomo-home'))


@pytest.fixture(autouse=True)
def a_host_that_owns_its_ports(monkeypatch):
    """These tests describe a VPS/Docker host: raw TCP *and* UDP are available.

    Which platform withholds what is a separate concern (``runtime`` has its own
    tests), and leaving it to the ambient environment would make this module
    pass or fail depending on where it runs. The tests that exercise withholding
    override these two explicitly.
    """
    monkeypatch.setattr(runtime, 'has_tcp', lambda: True)
    monkeypatch.setattr(runtime, 'has_udp', lambda: True)


def a_vps():
    """What a VPS admin configures: a public address, so a raw port is reachable."""
    _set('direct_host', PUBLIC_HOST)
    _set('direct_port', '8443')


@pytest.fixture
def a_host_with_no_public_address(monkeypatch):
    """Nothing configured and nothing detected — the withheld case.

    Whether *this* machine happens to have a routable address is the host's
    business, not the test's: the endpoint is forced empty instead.
    """
    monkeypatch.setattr(runtime, 'direct', lambda: None)


def enable(profile_id, port='8444', engine='singbox'):
    item = profiles.BY_ID[profile_id]
    _set(profiles.enabled_key(item), '1')
    _set(profiles.port_key(item), port)
    _set(profiles.engine_key(item), engine)


def a_user():
    from app.core.models import UserCreate
    from app.users.service import create_user, list_users
    execute('DELETE FROM users')
    create_user(UserCreate(username='coreuser', protocol='all'))
    return list_users()[0]


def binary_for(engine):
    """A real engine binary, from the environment or the image, or None."""
    override = os.environ.get('NEXUS_TEST_SINGBOX_BINARY' if engine == engines.SINGBOX
                              else 'NEXUS_TEST_MIHOMO_BINARY')
    if override:
        return override if os.path.exists(override) else None
    path = engines.binary(engine)
    return path if os.path.exists(path) else None


# ------------------------------------------------------------------- the rules
def test_nothing_is_hosted_until_an_admin_enables_it():
    for item in profiles.PROFILES:
        assert cores.enabled(item) is False
        assert cores.is_published(item['id']) is False
    catalog = client.get('/api/cores', headers=h()).json()
    assert catalog['success'] is True
    assert [item['id'] for item in catalog['profiles']] == ['anytls', 'tuic']
    assert all(item['published'] is False for item in catalog['catalog'])


def test_an_enabled_protocol_without_a_public_address_is_withheld(a_host_with_no_public_address):
    """Enabling is not publishing: a link nothing can dial is never handed out."""
    enable('anytls')
    assert cores.enabled(profiles.BY_ID['anytls']) is True
    assert cores.host() == ''
    assert cores.is_published('anytls') is False
    ok, reason = cores.reachable(profiles.BY_ID['anytls'])
    assert ok is False and reason
    assert not [p for p in tp.available_profiles() if p.get('group') == tp.CORE]


def test_an_enabled_protocol_on_a_vps_is_published():
    a_vps()
    enable('anytls')
    assert cores.is_published('anytls') is True
    hosted = [p for p in tp.available_profiles() if p.get('group') == tp.CORE]
    assert [p['id'] for p in hosted] == ['anytls']
    assert hosted[0]['listener_port'] == 8444, 'the panel shows the public port'


def test_a_udp_protocol_is_withheld_where_udp_cannot_be_exposed(monkeypatch):
    """Railway forwards TCP only: TUIC is enabled there but must not be published."""
    a_vps()
    enable('tuic', port='8445')
    ok, reason = cores.reachable(profiles.BY_ID['tuic'])
    assert ok is True  # a VPS owns UDP
    # …and on a platform that cannot, the reason names the missing capability.
    monkeypatch.setattr(runtime, 'has_udp', lambda: False)
    ok, reason = cores.reachable(profiles.BY_ID['tuic'])
    assert ok is False and 'UDP' in reason
    # The other protocol is unaffected: TCP is all it needs.
    assert cores.reachable(profiles.BY_ID['anytls'])[0] is True


def test_the_engine_choice_is_per_protocol():
    a_vps()
    enable('anytls', port='8444', engine='singbox')
    enable('tuic', port='8445', engine='mihomo')
    assert cores.engine_of(profiles.BY_ID['anytls']) == 'singbox'
    assert cores.engine_of(profiles.BY_ID['tuic']) == 'mihomo'
    assert cores.profiles.ANYTLS in tp.PROTOCOLS and cores.profiles.TUIC in tp.PROTOCOLS


# -------------------------------------------------------------------- rendering
def test_every_user_gets_their_own_credential_on_both_engines():
    a_user()
    from app.users.service import list_users
    user = list_users()[0]
    a_vps()
    enable('anytls')
    enable('tuic', port='8445')
    # Both engines render both protocols (each protocol is hosted by one engine at
    # a time, so the choice is flipped and the assertions are run twice).
    for engine in (engines.SINGBOX, engines.MIHOMO):
        for profile_id in ('anytls', 'tuic'):
            _set(profiles.engine_key(profiles.BY_ID[profile_id]), engine)
        rendered = cores.listeners(engine)
        assert {item['profile']['id'] for item in rendered} == {'anytls', 'tuic'}
        for item in rendered:
            assert [name for name, _ in item['users']] == ['coreuser']
            assert item['users'][0][1] == user['uuid'], 'the credential is the user uuid'
            assert item['cert'].startswith('-----BEGIN CERTIFICATE-----')
            assert 'PRIVATE KEY' in item['key']
    # The Xray inbounds authenticate the same user with the same value, so
    # disabling the user revokes these nodes with all the others.
    from app import xray
    assert user['uuid'] in [entry['id'] for entry in xray._clients_for('vless', {})]


def test_the_singbox_config_is_what_singbox_accepts():
    a_user()
    a_vps()
    enable('anytls', engine='singbox')
    enable('tuic', port='8445', engine='singbox')
    config = engines.singbox_config(cores.listeners(engines.SINGBOX))
    kinds = {item['type'] for item in config['inbounds']}
    assert kinds == {'anytls', 'tuic'}
    anytls = next(item for item in config['inbounds'] if item['type'] == 'anytls')
    tuic = next(item for item in config['inbounds'] if item['type'] == 'tuic')
    assert anytls['listen'] == '0.0.0.0' and anytls['listen_port'] == 8444
    assert anytls['users'] == [{'name': 'coreuser', 'password': anytls['users'][0]['password']}]
    assert tuic['users'][0]['uuid'] == tuic['users'][0]['password']
    assert tuic['congestion_control'] == 'bbr', 'must match what the links ask for'
    assert anytls['tls']['certificate'] and anytls['tls']['key']


def test_the_mihomo_config_is_what_mihomo_accepts():
    a_user()
    a_vps()
    enable('anytls', engine='mihomo')
    enable('tuic', port='8445', engine='mihomo')
    text = engines.config_text(engines.MIHOMO, cores.listeners(engines.MIHOMO), api=cfg.mihomo_api)
    assert 'listeners:' in text and 'type: anytls' in text and 'type: tuic' in text
    assert 'external-controller' in text
    # mihomo puts the UUID in the *key* for TUIC and the username for AnyTLS; a
    # hand-written config that got this wrong would refuse every client.
    assert f'coreuser: {ANYTLS_UUID}' not in text or 'coreuser:' in text
    config = engines.mihomo_config(cores.listeners(engines.MIHOMO), api=cfg.mihomo_api)
    by_type = {item['type']: item for item in config['listeners']}
    assert list(by_type['anytls']['users'].keys()) == ['coreuser']
    assert list(by_type['tuic']['users'].keys()) == [next(iter(by_type['tuic']['users'].keys()))]
    assert '-' in list(by_type['tuic']['users'].keys())[0], 'the TUIC key is the uuid'
    assert '-----BEGIN CERTIFICATE-----' in text, 'the certificate is inline'


@pytest.mark.parametrize('engine', [engines.SINGBOX, engines.MIHOMO])
def test_the_real_engine_accepts_the_rendered_config(engine):
    """The strongest check there is: hand the config to the engine itself."""
    if not binary_for(engine):
        pytest.skip(f'{engines.label(engine)} is not installed here')
    a_user()
    a_vps()
    enable('anytls', port='23101')
    enable('tuic', port='23102', engine=engine)
    listeners = cores.listeners(engine)
    assert listeners, 'the enabled protocols must be rendered'
    path = engines.config_path(engine)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    os.makedirs(engines.home(engine), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(engines.config_text(engine, listeners, api=cfg.mihomo_api))
    done = subprocess.run(engines.validate_argv(engine, path), capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (done.stdout + done.stderr)[-800:]


# ------------------------------------------------------------------------ links
def linked_subscription(user, target='auto'):
    return render(user, 'https://panel.example.com', target)


def test_the_anytls_link_is_the_one_clients_import():
    user = a_user()
    a_vps()
    enable('anytls')
    lines = [line for line in linked_subscription(user).splitlines() if line.startswith('anytls://')]
    assert len(lines) == 1
    link = lines[0]
    assert link.startswith(f'anytls://{user["uuid"]}@{PUBLIC_HOST}:8444?')
    assert 'insecure=1' in link, 'the certificate is self-signed'
    assert f'sni={cfg.core_sni}' in link
    assert link.count('#') == 1


def test_the_tuic_link_carries_both_insecure_spellings():
    user = a_user()
    a_vps()
    enable('tuic', port='8445')
    link = next(line for line in linked_subscription(user).splitlines() if line.startswith('tuic://'))
    assert link.startswith(f'tuic://{user["uuid"]}:{user["uuid"]}@{PUBLIC_HOST}:8445?')
    # v2rayN reads ``allow_insecure``, the sing-box based clients ``insecure``.
    assert 'allow_insecure=1' in link and 'insecure=1' in link
    assert 'congestion_control=bbr' in link


def test_the_hosted_protocols_reach_every_client_format():
    user = a_user()
    a_vps()
    enable('anytls')
    enable('tuic', port='8445')
    sing = json.loads(linked_subscription(user, 'singbox'))['outbounds']
    assert {item['type'] for item in sing} >= {'anytls', 'tuic'}
    anytls = next(item for item in sing if item['type'] == 'anytls')
    assert anytls['password'] == user['uuid'] and anytls['tls']['insecure'] is True
    tuic = next(item for item in sing if item['type'] == 'tuic')
    assert tuic['uuid'] == user['uuid'] and tuic['congestion_control'] == 'bbr'
    clash = json.loads(linked_subscription(user, 'clash'))['proxies']
    types = {item['type'] for item in clash}
    assert {'anytls', 'tuic'} <= types
    assert all(item['skip-cert-verify'] is True for item in clash if item['type'] in ('anytls', 'tuic'))
    # Xray has no outbound for either, so that format simply omits them.
    xray_json = json.loads(linked_subscription(user, 'xray'))['outbounds']
    assert not {'anytls', 'tuic'} & {item['protocol'] for item in xray_json}


def test_a_per_node_subscription_does_not_drag_the_hosted_protocols_in():
    user = a_user()
    a_vps()
    enable('anytls')
    node = {'name': 'de-cf-01', 'server': '104.16.0.1', 'port': 443, 'tls': 1,
            'host': 'worker.example.workers.dev', 'sni': 'worker.example.workers.dev',
            'kind': 'cloudflare', 'latency_ms': 20, 'metadata': {}}
    text = render(user, 'https://panel.example.com', 'auto', nodes=[node], include_extras=False)
    assert 'anytls://' not in text
    from app.subscriptions.generator import node_links
    drawer = node_links(user, node)
    assert not [item for item in drawer['profiles'] if item['group'] == tp.CORE]


def test_a_hosted_protocol_follows_the_users_node_scope():
    """These nodes answer on this server, so they ride with the origin slice.

    «فقط نودهای مولتی‌لوکیشن» means the edge locations, and «فقط آمریکا» means one
    country; neither may grow a raw-port node on the panel's own address.
    """
    user = a_user()
    a_vps()
    enable('anytls')
    from app.nodes import origin_node_name, upsert
    upsert(origin_node_name(), 'railway', PUBLIC_HOST, 443, True,
           PUBLIC_HOST, PUBLIC_HOST, 'local', {'role': 'direct'})
    # One edge location, so a scoped subscription still has something to publish.
    upsert('us-cdn-01', 'cloudflare', '104.16.0.1', 443, True,
           'cdn16.example.ir', 'cdn16.example.ir', 'cloudflare',
           {'location': 'us', 'provider': 'cloudflare'})
    assert 'anytls://' in linked_subscription(user)

    def scoped(scope):
        """The subscription as that scope sees it (the scope lives in the row)."""
        from app.users.service import list_users
        execute('UPDATE users SET metadata=? WHERE username=?',
                (json.dumps({'node_scope': scope}), user['username']))
        return linked_subscription(list_users()[0])

    for scope in ('multi', 'cc:us'):
        text = scoped(scope)
        assert 'anytls://' not in text, scope
        assert 'cdn16.example.ir' in text, scope
    # …and «فقط سرور اصلی» is exactly where it belongs.
    assert 'anytls://' in scoped('origin')


def test_the_links_are_absent_while_nothing_is_published(a_host_with_no_public_address):
    user = a_user()
    # No public address: even an explicitly enabled protocol is not published.
    enable('anytls')
    assert 'anytls://' not in linked_subscription(user)


# ------------------------------------------------------------------ the panel
def test_the_endpoint_needs_a_session():
    assert client.get('/api/cores').status_code == 401
    assert client.post('/api/cores', json={}).status_code == 401


def test_the_payload_is_flat_because_the_card_reads_it_flat():
    """The panel stores this response and reads `catalog`/`engines`/`host` off it.

    Nested one level down (`{success, cores: {...}}`) the card painted an empty
    list and the save button posted `profiles: {}` — an enabled switch that did
    nothing at all. The shape is part of the contract, so it is pinned here.
    """
    a_vps()
    enable('anytls')
    payload = client.get('/api/cores', headers=h()).json()
    for key in ('catalog', 'engines', 'host', 'sni', 'notes', 'published', 'counts'):
        assert key in payload, key
    assert 'cores' not in payload
    assert [item['id'] for item in payload['catalog']] == ['anytls', 'tuic']
    assert {item['id'] for item in payload['engines']} == {'singbox', 'mihomo'}
    assert all(('label' in item and 'installed' in item and 'running' in item)
               for item in payload['engines'])
    assert payload['sni'] == cores.status()['sni']


def test_saving_stores_the_switch_port_and_engine():
    a_vps()
    saved = client.post('/api/cores', headers=h(), json={'profiles': {
        'anytls': {'enabled': '1', 'port': '9443', 'engine': 'mihomo'},
        'tuic': {'enabled': '0', 'port': '9444', 'engine': 'singbox'},
    }}).json()
    assert saved['success'] is True
    assert 'core_anytls_enabled' in saved['changed'] and 'core_anytls_port' in saved['changed']
    stored = {item['id']: item for item in saved['catalog']}
    assert stored['anytls']['enabled'] is True and stored['anytls']['port'] == 9443
    assert stored['anytls']['engine'] == 'mihomo'
    assert stored['tuic']['enabled'] is False and stored['tuic']['published'] is False
    # Engine choice and port are what was saved either way; whether the protocol
    # is *published* depends on this host really running mihomo, and the panel has
    # to say which of the two it is instead of assuming the binary is there.
    if binary_for(engines.MIHOMO):
        assert stored['anytls']['published'] is True
    else:
        assert stored['anytls']['published'] is False
        assert 'نصب' in stored['anytls']['reason']


def test_a_bad_port_or_engine_is_rejected_instead_of_stored():
    """A rejected save must not leave half of itself stored."""
    bad_port = client.post('/api/cores', headers=h(),
                           json={'profiles': {'anytls': {'enabled': '1', 'port': 'oops'}}})
    assert bad_port.status_code == 400 and 'AnyTLS' in bad_port.json()['detail']
    out_of_range = client.post('/api/cores', headers=h(),
                               json={'profiles': {'anytls': {'enabled': '1', 'port': '70000'}}})
    assert out_of_range.status_code == 400
    bad_engine = client.post('/api/cores', headers=h(),
                             json={'profiles': {'anytls': {'enabled': '1', 'engine': 'xray'}}})
    assert bad_engine.status_code == 400
    assert cores.enabled(profiles.BY_ID['anytls']) is False, 'nothing was stored'


def test_a_reload_reports_why_a_protocol_is_not_published(monkeypatch):
    a_vps()
    enable('tuic')
    monkeypatch.setattr(runtime, 'has_udp', lambda: False)
    payload = client.post('/api/cores/reload', headers=h()).json()
    tuic = next(item for item in payload['catalog'] if item['id'] == 'tuic')
    assert tuic['enabled'] is True and tuic['published'] is False
    assert 'UDP' in tuic['reason']
    assert any('UDP' in note for note in payload['notes'])


def test_the_supervisor_starts_and_stops_a_real_listener():
    """The whole point: a real listener appears, and disappears again.

    Everything runs inside one event loop, the way the running panel does: a
    subprocess object belongs to the loop that created it, so a fresh
    ``asyncio.run`` per step would be testing loops rather than the supervisor.
    """
    import asyncio

    for engine in profiles.ENGINES:
        if not binary_for(engine):
            pytest.skip(f'{engines.label(engine)} is not installed here')
    a_user()
    a_vps()
    enable('anytls', port='23201', engine='singbox')
    enable('tuic', port='23202', engine='mihomo')

    async def scenario():
        try:
            await cores.sync(force=True)
            assert cores.published_profiles() == {'anytls', 'tuic'}
            status = cores.status()
            engines_up = {item['id']: item for item in status['engines']}
            assert engines_up['singbox']['running'] is True
            assert engines_up['singbox']['profiles'] == ['anytls']
            assert engines_up['mihomo']['running'] is True
            assert engines_up['mihomo']['profiles'] == ['tuic']
            assert {item['id']: item['running'] for item in status['catalog']} == {'anytls': True, 'tuic': True}
            # Disabling a protocol stops the engine that only served it, and the
            # catalog follows: nothing stays published that is not listening.
            _set(profiles.enabled_key(profiles.BY_ID['tuic']), '0')
            await cores.sync()
            assert cores.published_profiles() == {'anytls'}
            assert {item['id']: item['running'] for item in cores.status()['engines']}['mihomo'] is False
        finally:
            await cores.stop_all()

    asyncio.run(scenario())


def test_the_panel_ships_the_card_that_drives_all_this():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'templates', 'index.html'), encoding='utf-8') as handle:
        html = handle.read()
    for element in ('coStats', 'coProfiles', 'coSni', 'coSave', 'coReload', 'coEngines', 'coNotes'):
        assert f'id="{element}"' in html, element
    with open(os.path.join(root, 'static', 'js', 'views', 'advanced.js'), encoding='utf-8') as handle:
        view = handle.read()
    assert 'renderCores' in view and '/api/cores' in view


def test_the_image_ships_both_engines():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'Dockerfile'), encoding='utf-8') as handle:
        dockerfile = handle.read()
    assert 'sing-box' in dockerfile and 'mihomo' in dockerfile
    assert 'SINGBOX_VERSION' in dockerfile and 'MIHOMO_VERSION' in dockerfile, 'pin the versions'
    assert 'COPY --from=engines' in dockerfile
