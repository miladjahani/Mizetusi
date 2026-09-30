"""Advanced obfuscation — the transports, the ports they need, and the link.

The feature is one rule applied three times: **an advanced transport is
published only when it is switched on *and* its own public port is really
reachable.** So the tests are the three ways that rule can be broken, and what
each of them owes the admin:

* a switch that was never flipped (a deployment must not start answering on
  ports nobody asked for);
* a switch on a deployment with no raw TCP endpoint at all;
* a switch on a deployment whose public port for that transport does not exist
  yet — the Railway case, where the public number is allocated at random and only
  :mod:`app.ports` knows it.

Then the two halves that make the disguise real rather than nominal: the engine
builds one listener per transport on its own port (and drops the advanced half
before the plain Reality node when the engine refuses it), and the link carries
the transport's own parameters, the uTLS fingerprint, Reality's spider path and
the per-user fragment options.
"""
import json
import os
import urllib.parse

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-obfuscation-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-obfuscation-test.db')

import pytest
from fastapi.testclient import TestClient

from app import ports as port_map
from app import runtime, xray
from app.config import settings as cfg
from app.db import execute, init_db
from app.main import _setting, _set, app
from app.subscriptions import generator
from app.subscriptions import obfuscation as obfs
from app.subscriptions import transports as tp

init_db()
client = TestClient(app)

PUBLIC_HOST = '198.51.100.7'
USER_UUID = '99999999-8888-7777-6666-555555555555'
USER = {'uuid': USER_UUID, 'username': 'obfs-user', 'protocol': 'all',
        'fingerprint': 'firefox', 'frag_len': '100-200', 'frag_int': '10-20',
        'advanced_frag': 'tlshello', 'tls_mask': '1.1.1.1', 'is_active': 1}
NODE = {'name': 'railway-direct', 'kind': 'railway', 'server': PUBLIC_HOST, 'port': 443,
        'tls': 1, 'host': 'panel.example.com', 'sni': 'panel.example.com',
        'latency_ms': 20.0, 'metadata': {}}

KEYS = ('direct_host', 'direct_port', 'reality_private', 'reality_public',
        'reality_short_id', 'obfs_spider_x', 'obfs_flow', 'obfs_packet_encoding',
        port_map.STORE, port_map.DONE, port_map.LOG) + tuple(
    obfs.switch_key(item['id']) for item in obfs.PROFILES)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


def a_direct_deployment():
    """A host that owns its ports: a public address and a Reality key pair."""
    _set('direct_host', PUBLIC_HOST)
    _set('direct_port', '8443')
    _set('reality_private', 'PRIV')
    _set('reality_public', 'PUB')
    _set('reality_short_id', '0123abcd')


def query_of(link):
    """The query string of a sharing URI, as a dict."""
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(link).query))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Nothing this module writes may reach another test or another module."""
    previous = xray._last_served
    tp.set_served(None)
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))
    monkeypatch.setattr(runtime, 'direct', lambda: None)
    yield
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))
    port_map.save({})
    tp.set_served(previous)


# ------------------------------------------------------------- nothing unasked
def test_a_fresh_deployment_publishes_no_advanced_transport():
    """The default is silence: five extra public ports is not a thing a panel
    opens on its own."""
    assert [item['id'] for item in obfs.PROFILES] == [
        'vless-xhttp', 'vless-grpc', 'vless-httpupgrade', 'vmess-grpc', 'trojan-grpc']
    assert all(item['enabled'] is False for item in obfs.catalog()['profiles'])
    assert obfs.available() == []
    a_direct_deployment()
    assert obfs.available() == []
    # The only direct profile a fresh deployment serves is the plain Reality one.
    assert [item['id'] for item in tp.available_profiles() if item['group'] == tp.DIRECT] == [
        'vless-reality']


def test_a_switch_without_a_public_endpoint_publishes_nothing():
    """Enabled, but there is nowhere for a client to dial: the card has to say so."""
    obfs.set_enabled('vless-xhttp', True)
    assert obfs.available() == []
    row = next(item for item in obfs.catalog()['profiles'] if item['id'] == 'vless-xhttp')
    assert row['enabled'] is True and row['reachable'] is False
    assert 'TCP' in row['reason']
    # And the engine builds no listener for it either.
    assert all(item['tag'] != 'vless-xhttp' for item in xray._config()['inbounds'])


# ------------------------------------------------------- the port is the point
def test_each_transport_binds_its_own_port_and_dials_its_own_number():
    """One listener per transport, on the port that transport declares."""
    a_direct_deployment()
    for item in obfs.PROFILES:
        obfs.set_enabled(item['id'], True)
    available = {item['id']: item for item in obfs.available()}
    assert set(available) == {item['id'] for item in obfs.PROFILES}

    inbounds = {item['tag']: item for item in xray._config()['inbounds']}
    ports_used = []
    for item in obfs.PROFILES:
        inbound = inbounds[item['id']]
        listen = obfs.listen_port(item)
        assert inbound['port'] == listen
        # A raw-TCP transport is the only one that may be on the public address;
        # the rest are published because they own a port, not because they are
        # exposed on every interface by accident.
        assert inbound['listen'] == '0.0.0.0'
        assert inbound['streamSettings']['security'] == 'reality'
        assert inbound['streamSettings']['realitySettings']['privateKey'] == 'PRIV'
        ports_used.append(listen)
        # The link is built from that same number, not from the Reality parent's.
        address, port = tp.node_address(NODE, available[item['id']])
        assert (address, port) == (PUBLIC_HOST, listen)
    # The transports never collide with each other or with the Reality parent.
    assert len(set(ports_used)) == len(ports_used)
    assert cfg.xray_reality_port not in ports_used


def test_the_plain_reality_node_is_never_the_casualty_of_an_extra_disguise():
    """The advanced half is dropped first, because it is the newest inbound."""
    a_direct_deployment()
    obfs.set_enabled('vless-xhttp', True)
    obfs.set_enabled('vless-grpc', True)
    full = xray._config()
    lean = xray._config(advanced=False)
    assert {'vless-xhttp', 'vless-grpc'} <= {item['tag'] for item in full['inbounds']}
    assert 'vless-xhttp' not in {item['tag'] for item in lean['inbounds']}
    # The parent Reality node survives, and so does every WebSocket edge profile.
    assert 'vless-reality' in {item['tag'] for item in lean['inbounds']}
    assert 'vless-ws' in {item['tag'] for item in lean['inbounds']}


# --------------------------------------------------------- Railway's random port
def test_on_railway_a_transport_waits_for_its_own_tcp_proxy(monkeypatch):
    """A TCP proxy the panel created for the *Reality* port says nothing about
    the advanced transports' own ports — Railway allocates each one separately."""
    a_direct_deployment()
    monkeypatch.setattr(tp, 'direct_endpoint', lambda: {'host': 'roundhouse.proxy.rlwy.net',
                                                         'port': 23177, 'source': 'railway'})
    obfs.set_enabled('vless-grpc', True)
    # The Reality port has a proxy, so the plain node is published …
    port_map.save({cfg.xray_reality_port: {'host': 'roundhouse.proxy.rlwy.net', 'port': 23177}})
    # … but gRPC's own port does not, so its link would dial a number nothing
    # forwards. It is withheld, and the card names the missing step.
    assert obfs.available() == []
    row = obfs.catalog()['profiles'][1]
    assert row['id'] == 'vless-grpc' and row['enabled'] is True and row['reachable'] is False
    assert 'TCP Proxy' in row['reason']


def test_a_created_proxy_makes_the_transport_publishable(monkeypatch):
    """Once the mapping exists, the link carries the **forwarded** number."""
    a_direct_deployment()
    monkeypatch.setattr(tp, 'direct_endpoint', lambda: {'host': 'roundhouse.proxy.rlwy.net',
                                                         'port': 23177, 'source': 'railway'})
    obfs.set_enabled('vless-grpc', True)
    listen = obfs.listen_port(obfs.profile('vless-grpc'))
    port_map.save({listen: {'host': 'roundhouse.proxy.rlwy.net', 'port': 28443}})
    profile = obfs.available()[0]
    assert profile['endpoint'] == {'host': 'roundhouse.proxy.rlwy.net', 'port': 28443,
                                   'listen_port': listen, 'proxied': True}
    assert tp.node_address(NODE, profile) == ('roundhouse.proxy.rlwy.net', 28443)
    assert obfs.catalog()['published'] == ['vless-grpc']
    # The listener still binds the internal number, never the forwarded one.
    inbound = next(item for item in xray._config()['inbounds'] if item['tag'] == 'vless-grpc')
    assert inbound['port'] == listen


# ------------------------------------------------------------------- the link
def test_an_advanced_link_carries_its_own_transport_and_the_reality_credentials():
    """A link on the wrong path is worse than no link, so both halves travel."""
    a_direct_deployment()
    obfs.set_enabled('vless-grpc', True)
    profile = tp.find('vless-grpc')
    query = query_of(generator.vless_uri(USER, NODE, profile))
    assert query['type'] == 'grpc' and query['serviceName'] == 'grpc'
    assert query['security'] == 'reality' and query['pbk'] == 'PUB' and query['sid'] == '0123abcd'
    assert query['sni'] == tp.REALITY_SNI
    # The uTLS fingerprint, the spider path, the packet encoding and the per-user
    # fragment options: the whole disguise, on one link.
    assert query['fp'] == 'firefox'
    assert query['spx'] == '/'
    assert query['fragment'] == '100-200' and query['fragmentInterval'] == '10-20'
    assert query['advancedFragment'] == 'tlshello' and query['tlsMask'] == '1.1.1.1'
    # A Reality link keeps no HTTP header type: that is a raw-TCP field, and a
    # client that read it on a gRPC link would be dialling the wrong thing.
    assert 'headerType' not in query
    # …nor a flow: the gRPC inbound is built with none, so the link must not
    # carry one either.
    assert 'flow' not in query


@pytest.mark.parametrize('profile_id,expected', [
    ('vless-xhttp', {'type': 'xhttp', 'mode': 'auto', 'path': '/xh', 'host': 'www.cloudflare.com'}),
    ('vless-httpupgrade', {'type': 'httpupgrade', 'path': '/hu', 'host': 'www.cloudflare.com'}),
])
def test_every_advanced_transport_speaks_its_own_parameters(profile_id, expected):
    a_direct_deployment()
    obfs.set_enabled(profile_id, True)
    query = query_of(generator.vless_uri(USER, NODE, tp.find(profile_id)))
    assert {key: query[key] for key in expected} == expected


def test_the_deployment_defaults_win_only_where_the_user_has_none():
    """A house style is set once; a per-user column still overrides it."""
    a_direct_deployment()
    obfs.set_enabled('vless-xhttp', True)
    obfs.save({'spider_x': 'assets', 'packet_encoding': 'xudp'})
    profile = tp.find('vless-xhttp')
    query = query_of(generator.vless_uri(USER, NODE, profile))
    assert query['spx'] == '/assets' and query['packetEncoding'] == 'xudp'
    plain = query_of(generator.vless_uri({'uuid': USER_UUID, 'fingerprint': 'chrome'},
                                         NODE, profile))
    assert plain['spx'] == '/assets' and plain['fp'] == 'chrome'


# ------------------------------------------------------------------ the flow
def test_the_flow_is_off_until_asked_and_then_only_on_reality():
    """A flow on a WebSocket or gRPC inbound is a config Xray refuses, and one
    refused inbound takes every protocol down with it."""
    a_direct_deployment()
    obfs.set_enabled('vless-grpc', True)
    assert obfs.user_flow({'protocol': 'vless', 'network': 'tcp', 'security': 'reality'}) == ''
    assert 'flow' not in query_of(generator.vless_uri(USER, NODE, tp.find('vless-grpc')))

    obfs.save({'flow': 'xtls-rprx-vision'})
    reality = {'protocol': 'vless', 'network': 'tcp', 'security': 'reality'}
    assert obfs.user_flow(reality) == 'xtls-rprx-vision'
    # Everywhere else: empty, so a gRPC or WS client is never handed one.
    assert obfs.user_flow({'protocol': 'vless', 'network': 'ws', 'security': 'tls'}) == ''
    assert obfs.user_flow({'protocol': 'vless', 'network': 'grpc', 'security': 'reality'}) == ''
    assert obfs.user_flow({'protocol': 'trojan', 'network': 'tcp', 'security': 'reality'}) == ''

    # The link and the engine agree, which is the only thing that makes it work.
    query = query_of(generator.vless_uri(USER, NODE, tp.find('vless-reality')))
    assert query['flow'] == 'xtls-rprx-vision'
    # …and the inbound's own clients are built with it, not with a bare ''.
    from app.xray import _clients_for
    execute('DELETE FROM users')
    from app.users.service import create_user
    from app.core.models import UserCreate
    created = create_user(UserCreate(username='flow-user'))
    inbound = next(item for item in xray._config()['inbounds'] if item['tag'] == 'vless-reality')
    assert inbound['settings']['clients'] == [
        {'id': created['uuid'], 'email': 'flow-user@nexus.local', 'level': 0,
         'flow': 'xtls-rprx-vision'}]
    # The gRPC inbound of the same deployment carries none.
    grpc = next(item for item in xray._config()['inbounds'] if item['tag'] == 'vless-grpc')
    assert [item['flow'] for item in grpc['settings']['clients']] == ['']
    execute('DELETE FROM users')


def test_a_value_the_engine_would_reject_is_refused_at_the_api():
    """Xray validates the whole config at once, so a bad flow is a panel-wide
    outage — it has to be refused where it is typed."""
    for bad in ('xtls-rprx-splice', 'vision', 'xtls-rprx-vision,xtls-rprx-vision'):
        with pytest.raises(ValueError):
            obfs.validate('flow', bad)
    assert obfs.validate('flow', '') == ''
    assert obfs.validate('packet_encoding', 'xudp') == 'xudp'
    with pytest.raises(ValueError):
        obfs.validate('packet_encoding', 'rot13')
    # A spider path is a path, whatever the admin typed into the field.
    assert obfs.validate('spider_x', 'assets') == '/assets'
    assert obfs.validate('spider_x', '/assets') == '/assets'


def test_the_panel_answers_with_a_reason_rather_than_a_broken_link():
    _auth = client.get('/api/obfuscation')
    assert _auth.status_code == 401
    body = client.get('/api/obfuscation', headers=h()).json()
    assert {'profiles', 'defaults', 'published', 'networks'} <= set(body)
    assert body['networks'] == ['xhttp', 'grpc', 'httpupgrade']
    for item in body['profiles']:
        assert {'id', 'tag', 'network', 'listen_port', 'enabled', 'reachable', 'reason'} <= set(item)


def test_the_admin_api_switches_one_transport_and_keeps_the_engine_in_step():
    a_direct_deployment()
    saved = client.post('/api/obfuscation', headers=h(), json={'id': 'vless-xhttp', 'enabled': '1'})
    assert saved.status_code == 200
    assert 'vless-xhttp' in saved.json()['obfuscation']['published']
    assert obfs.catalog()['published'] == ['vless-xhttp']
    # An unknown transport is the caller's mistake, not a silent no-op.
    bad = client.post('/api/obfuscation', headers=h(), json={'id': 'vless-quantum', 'enabled': '1'})
    assert bad.status_code == 400 and 'vless-quantum' not in bad.text
    off = client.post('/api/obfuscation', headers=h(), json={'id': 'vless-xhttp', 'enabled': '0'})
    assert off.json()['obfuscation']['published'] == []


def test_the_json_formats_speak_the_advanced_transports_too():
    """sing-box / Clash / Xray get the same transport block as the link."""
    a_direct_deployment()
    obfs.set_enabled('vless-xhttp', True)
    obfs.set_enabled('vless-grpc', True)
    profile = tp.find('vless-grpc')
    sb = generator.singbox(USER, NODE, profile)
    assert sb['transport'] == {'type': 'grpc', 'service_name': 'grpc', 'multi_mode': False}
    assert sb['tls']['reality']['public_key'] == 'PUB'
    clash = generator.clash(USER, NODE, profile)
    assert clash['network'] == 'grpc' and clash['grpc-opts'] == {'grpc-service-name': 'grpc'}
    assert clash['reality-opts']['short-id'] == '0123abcd'
    xr = generator.xray(USER, NODE, tp.find('vless-xhttp'))
    assert xr['streamSettings']['xhttpSettings'] == {'path': '/xh', 'mode': 'auto',
                                                     'host': tp.REALITY_SNI}
    assert xr['streamSettings']['realitySettings']['spiderX'] == '/'
    # Every format is importable JSON, which is what a client does with it.
    assert json.loads(json.dumps(xr))['protocol'] == 'vless'


def test_a_vmess_gRPC_link_uses_vmess_own_spelling():
    """VMess has no parameterised URI: the same transport has to be spelled in
    its own JSON, or the client reads a field that does not exist there."""
    a_direct_deployment()
    obfs.set_enabled('vmess-grpc', True)
    import base64
    payload = json.loads(base64.b64decode(generator.vmess_uri(USER, NODE, tp.find('vmess-grpc'))
                                          .split('://', 1)[1]).decode())
    assert payload['net'] == 'grpc' and payload['serviceName'] == 'vgrpc'
    assert payload['tls'] == 'reality' and payload['pbk'] == 'PUB'
    assert payload['fp'] == 'firefox' and payload['spx'] == '/'
    # …and none of VLESS's own fields, which VMess would reject.
    assert 'encryption' not in payload and 'packetEncoding' not in payload


def test_railway_can_forward_the_advanced_ports_too():
    """The port the panel publishes is the one a TCP proxy really forwards."""
    a_direct_deployment()
    obfs.set_enabled('vless-xhttp', True)
    catalog = {item['id']: item for item in port_map.catalog()}
    entry = catalog['vless-xhttp']
    assert entry['port'] == obfs.listen_port(obfs.profile('vless-xhttp'))
    assert entry['enabled'] is True
    assert entry['port'] in port_map.wanted()
    obfs.set_enabled('vless-xhttp', False)
    assert entry['port'] not in port_map.wanted()
