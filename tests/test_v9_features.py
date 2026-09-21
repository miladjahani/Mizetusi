"""v9 features: flags, the universal Shadowsocks cipher, Hysteria2 and more.

What this release adds is all *end-user visible*, and every one of those things
is easy to break silently — a link that only one client imports, a config cap
applied in the line format but not in sing-box, a location pack that creates
sources without nodes, a flag that disappears from a subscription. These tests
assert the contract end to end:

* country flags on node labels, in the panel and in the status window, with an
  admin switch that turns them off everywhere at once;
* the Shadowsocks cipher every client implements (``aes-256-gcm`` over
  ``v2ray-plugin``), so a Shadowsocks node is not Happ-only;
* the external Hysteria2 node, published only once it is configured;
* the four core sublinks of the status window and the per-user config count;
* multi-location packs and the subscription importer (the reference list);
* the network-tools endpoints.
"""
import base64
import json
import os
import urllib.parse

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-v9-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-v9-test.db')

import pytest

from fastapi.testclient import TestClient

from app.config import settings
from app.core.models import UserCreate
from app.core.settings_store import store
from app.db import execute, init_db
from app.edge import packs as edge_packs
from app.edge import sources as edge
from app.main import _setting, app
from app.nodes import catalog, ensure as ensure_nodes, upsert
from app.subscriptions import flags as sub_flags
from app.subscriptions import transports as tp
from app.subscriptions.generator import node_links, render
from app.users.service import create_user

init_db()
ensure_nodes()
client = TestClient(app)

# A handful of the endpoints the reference subscription publishes: two clean
# domains, two destinations of one relay, and one clean Cloudflare IP. The
# remark is the only place the country is written down, exactly like the real
# list.
REFERENCE_LINKS = '\n'.join([
    'vless://11111111-2222-3333-4444-555555555555@cdn1.torvixa.ir:443'
    '?encryption=none&security=tls&type=ws&host=cdn1.torvixa.ir&sni=cdn1.torvixa.ir&path=%2Fws'
    '#ZEUS%20%7C%20%F0%9F%87%A9%F0%9F%87%AA%20%7C%2066EF9OOY%2030',
    'trojan://secret@cdn1.melqora.ir:443?security=tls&type=ws&path=%2Fws'
    '#ZEUS%20%7C%20%F0%9F%87%B5%F0%9F%87%B1%20%7C%20POLAND',
    'ss://YWVzLTI1Ni1nY206NTA0MTRlNDUtNGM1Zi01YTQ1LTU1NTMtYzJhZGRjNmU2ZDI5@104.24.39.128:443/'
    '?plugin=v2ray-plugin%3Bpath%3D%2Fstream%2FPANEL_ZEUS%2Fc2addc6e6d29%2Floc-0%3Bmux%3D0'
    '%3Bhost%3Dz4z88xefuxss.sgvdsgvrse435.workers.dev%3Btls'
    '#ZEUS%20%7C%20%F0%9F%87%B1%F0%9F%87%B7%20%7C%2066EF9OOY%2030',
    'hysteria2://pass@cdn16.qemitra.ir:30521?sni=cdn16.qemitra.ir'
    '#ZEUS%20%7C%20%F0%9F%87%AB%F0%9F%87%AE%20%7C%20FINLAND',
])
# What the panel hands out to a user, so a subscription entry has something to
# resolve against.
SHARED_OPTIONS = ('portal_banner', 'support_url', 'flags_enabled', 'default_format',
                  'default_max_configs', 'hy2_enabled', 'hy2_host', 'hy2_port', 'hy2_password',
                  'hy2_sni', 'hy2_obfs', 'hy2_obfs_password', 'hy2_insecure', 'hy2_label')


def h():
    return {'X-Admin-Password': _setting('admin_password') or settings.admin_password}


def _seed_nodes():
    """Two nodes in two countries, which is the only interesting case here."""
    execute('DELETE FROM nodes')
    upsert('de-edge', 'edge', 'de.example.com', 443, True, 'de.example.com', 'de.example.com',
           'domain', {'location': 'de', 'provider': 'domain'})
    upsert('nl-edge', 'edge', 'nl.example.com', 443, True, 'nl.example.com', 'nl.example.com',
           'domain', {'location': 'nl', 'provider': 'domain'})


@pytest.fixture(autouse=True)
def clean_state():
    """Sources, flags and the Hysteria2 endpoint live in shared settings rows.

    Both the row *and* the store's in-process cache have to be reset: a peer test
    module may have written ``flags_enabled`` through the store, and a bare SQL
    delete would leave the cached value behind for the next read.
    """
    saved = {key: _setting(key) for key in SHARED_OPTIONS}
    for key in SHARED_OPTIONS:
        store.delete(key)
    edge._write_sources([])
    _seed_nodes()
    yield
    for key, value in saved.items():
        if value is None:
            store.delete(key)
        else:
            store.set(key, value)
    edge._write_sources([])
    _seed_nodes()


_seed_nodes()


def _user(username, **kwargs):
    execute('DELETE FROM users WHERE username=?', (username,))
    return create_user(UserCreate(username=username, protocol='all', **kwargs))


def _remarks(text):
    """The name a client displays for every line of a subscription."""
    return [urllib.parse.unquote(line.split('#', 1)[1]) for line in text.splitlines() if '#' in line]


# ------------------------------------------------------------------------- flags
def test_a_location_becomes_a_country_flag_on_every_node_name():
    node = catalog.get('de-edge')
    assert tp.node_flag(node) == '🇩🇪'
    assert tp.node_label(node) == '🇩🇪 de-edge'

    user = _user('flaguser')
    lines = render(user, 'https://panel.example.com', 'auto')
    named = {name.split(' · ')[0] for name in _remarks(lines)}
    assert named == {'🇩🇪 de-edge', '🇳🇱 nl-edge'}
    # And the remark really carries it over the wire (percent-encoded), which is
    # the only thing a client ever sees.
    assert '%F0%9F%87%A9%F0%9F%87%AA' in lines and '%F0%9F%87%B3%F0%9F%87%B1' in lines


def test_the_admin_switch_removes_flags_from_links_and_the_status_window():
    user = _user('noflaguser')
    assert any('🇩🇪' in name for name in _remarks(render(user, 'https://panel.example.com', 'auto')))

    assert client.post('/api/customization', headers=h(), json={'flags_enabled': '0'}).status_code == 200
    lines = render(user, 'https://panel.example.com', 'auto')
    assert all('🇩🇪' not in name for name in _remarks(lines))
    assert any(name.startswith('de-edge') for name in _remarks(lines))

    data = client.get(f"/portal/{user['uuid']}/json").json()
    assert data['flags'] is False
    assert all(item['flag'] == '' for item in data['nodes'])
    assert all('🇩🇪' not in item['label'] for item in data['nodes'])

    client.post('/api/customization', headers=h(), json={'flags_enabled': '1'})
    assert any('🇩🇪' in name for name in _remarks(render(user, 'https://panel.example.com', 'auto')))


def test_a_flag_emoji_in_a_remark_is_read_back_as_a_country():
    """The reference list writes the country *only* as an emoji on the remark."""
    assert sub_flags.country_code('ZEUS | 🇵🇱 | 66EF9OOY 30') == 'pl'
    assert sub_flags.flag('pl') == '🇵🇱'
    assert sub_flags.country_code('🇫🇮') == 'fi'
    assert sub_flags.country_code('de-cloudflare-01') == 'de'
    assert sub_flags.country_code('آلمان · کلودفلر') == 'de'
    assert sub_flags.name('de') == 'آلمان'
    # An unknown location yields no flag rather than a wrong one.
    assert sub_flags.flag_for('nowhere', 'some-random-host') == ''


# ------------------------------------------------------------------ shadowsocks
def test_the_shadowsocks_node_is_the_cipher_every_client_implements():
    """Happ was the only client that pinged the 2022 ciphers, so the default node
    is the classic AEAD one, reached through the plugin every client ships."""
    catalog_data = tp.catalog()
    assert catalog_data['ss_method'] == 'aes-256-gcm'
    assert catalog_data['ss_methods'][0] == 'aes-256-gcm'

    user = _user('ssuser')
    node = catalog.get('de-edge')
    links = node_links(user, node)
    classic = next(item for item in links['profiles'] if item['id'] == 'ss-classic-ws')
    link = classic['link']
    assert link.startswith('ss://')

    userinfo = link.split('://', 1)[1].split('@', 1)[0]
    userinfo += '=' * (-len(userinfo) % 4)
    method, _, secret = base64.urlsafe_b64decode(userinfo).decode().partition(':')
    assert method == 'aes-256-gcm' and len(secret) >= 16

    query = link.split('?', 1)[1].split('#', 1)[0]
    plugin = next(part for part in query.split('&') if part.startswith('plugin='))
    plugin = plugin.split('=', 1)[1].replace('%3B', ';').replace('%3D', '=')
    assert plugin.startswith('v2ray-plugin;')
    # ``mux=0`` is what stops several clients from wrapping the stream the edge
    # does not terminate; ``tls`` is the bare flag at the end.
    assert 'mode=websocket' in plugin and 'mux=0' in plugin
    assert plugin.endswith(';tls') and 'host=de.example.com' in plugin

    # sing-box and mihomo get the same edge, in their own spelling.
    assert classic['singbox']['plugin'] == 'v2ray-plugin'
    assert classic['singbox']['plugin_opts'].startswith('mode=websocket;')
    assert classic['clash']['plugin'] == 'v2ray-plugin'
    assert classic['clash']['plugin-opts']['mode'] == 'websocket'


def test_the_published_matrix_covers_every_cipher_on_both_path_shapes():
    profiles = [item for item in tp.available_profiles() if item['protocol'] == 'ss']
    assert {item['method'] for item in profiles} >= {
        'aes-256-gcm', '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm',
        '2022-blake3-chacha20-poly1305', 'chacha20-ietf-poly1305'}
    # Each cipher is published on both edge path shapes, so a blocked path never
    # takes Shadowsocks down.
    paths = tp.edge_paths()
    for cipher in tp.SS_CIPHERS:
        assert cipher['path'] in paths and cipher['cdn_path'] in paths


# -------------------------------------------------------------------- hysteria2
def test_hysteria2_stays_out_until_it_is_configured_and_then_reaches_every_format():
    user = _user('hy2user')
    assert 'hysteria2://' not in render(user, 'https://panel.example.com', 'auto')

    saved = client.post('/api/hysteria', headers=h(), json={
        'action': 'save', 'host': 'hy2.example.com', 'port': 8443, 'password': 'secret',
        'sni': 'hy2.example.com', 'label': 'Hysteria2 · DE', 'enabled': '1'})
    assert saved.status_code == 200, saved.text
    assert saved.json()['hysteria']['configured'] is True

    lines = render(user, 'https://panel.example.com', 'auto')
    assert 'hysteria2://' in lines and 'hy2.example.com:8443' in lines

    singbox = json.loads(render(user, 'https://panel.example.com', 'singbox'))
    assert any(item['type'] == 'hysteria2' and item['server'] == 'hy2.example.com'
               for item in singbox['outbounds'])
    clash = json.loads(render(user, 'https://panel.example.com', 'clash'))
    assert any(item['type'] == 'hysteria2' for item in clash['proxies'])

    # It is one shared external endpoint, so a per-node subscription leaves it out
    # and Xray (which has no hysteria2 outbound) never carries it.
    assert 'hysteria2://' not in render(user, 'https://panel.example.com', 'auto', include_hy2=False)
    assert 'hysteria2' not in render(user, 'https://panel.example.com', 'xray')

    off = client.post('/api/hysteria', headers=h(), json={'action': 'disable'}).json()
    assert off['hysteria']['configured'] is False
    assert 'hysteria2://' not in render(user, 'https://panel.example.com', 'auto')

    # A half-configured node (a host but no password) is refused, not published.
    assert client.post('/api/hysteria', headers=h(), json={'action': 'clear'}).status_code == 200
    bad = client.post('/api/hysteria', headers=h(), json={'host': 'hy2.example.com'})
    assert bad.status_code == 400 and 'رمز' in bad.json()['detail']
    assert client.get('/api/hysteria', headers=h()).json()['hysteria']['configured'] is False


# ------------------------------------------------------- config count / portal
def test_the_per_user_config_count_is_honoured_by_every_format():
    """The cap is applied once, so the line formats, sing-box, Clash and Xray
    hand out the same set — a client importing two of them sees one list."""
    user = _user('capuser', max_configs=5)
    assert tp.user_max_configs(user) == 5

    lines = [line for line in render(user, 'https://panel.example.com', 'auto').splitlines() if line]
    assert len(lines) == 5
    decoded = base64.b64decode(render(user, 'https://panel.example.com', 'base64')).decode()
    assert len([line for line in decoded.splitlines() if line]) == 5
    assert len(json.loads(render(user, 'https://panel.example.com', 'singbox'))['outbounds']) == 5
    assert len(json.loads(render(user, 'https://panel.example.com', 'clash'))['proxies']) == 5
    assert len(json.loads(render(user, 'https://panel.example.com', 'xray'))['outbounds']) == 5

    # Empty means every published combination, and the API round-trips the value.
    unlimited = _user('uncappeduser')
    assert tp.user_max_configs(unlimited) == 0
    assert len([line for line in render(unlimited, 'https://panel.example.com', 'auto').splitlines() if line]) > 5

    edited = client.put('/api/users/capuser', headers=h(), json={'max_configs': 3})
    assert edited.status_code == 200, edited.text
    assert edited.json()['max_configs'] == 3
    assert len([line for line in render(
        client.get('/api/users/capuser', headers=h()).json(), 'https://panel.example.com', 'auto'
    ).splitlines() if line]) == 3
    assert client.post('/api/users', headers=h(), json={'username': 'badcap', 'max_configs': 900}).status_code == 422


def test_the_status_window_groups_the_client_sublinks_by_engine_and_reports_the_config_count():
    user = _user('portalcap', max_configs=6)
    client.post('/api/customization', headers=h(), json={'portal_banner': 'تمدید از پشتیبانی', 'default_format': 'clash'})

    data = client.get(f"/portal/{user['uuid']}/json").json()
    # The window keeps its previous shape: the smart link plus one link per client,
    # but the client list is split by engine (and each engine collapses).
    assert data['smart_url'].startswith(data['base_url']) and '/sub/' in data['smart_url']
    groups = {group['id']: group for group in data['client_groups']}
    assert list(groups) == ['core', 'xray', 'singbox', 'mihomo', 'tools']
    assert all(member['url'].startswith(data['base_url']) and '/sub/' in member['url']
               for group in data['client_groups'] for member in group['clients'])

    # Bettbox is Clash/Mihomo: YAML only, with nothing else offered next to it.
    bettbox = [member for member in groups['mihomo']['clients'] if member['id'] == 'bettbox'][0]
    assert bettbox['format'] == 'clash'
    assert [alt['target'] for alt in bettbox['alternatives']] == []
    # An Xray client is never offered the Clash YAML link.
    v2rayng = [member for member in groups['xray']['clients'] if member['id'] == 'v2rayng'][0]
    assert v2rayng['format'] == 'base64'
    assert 'clash' not in [alt['target'] for alt in v2rayng['alternatives']]
    assert all(member['family'] == 'singbox' for member in groups['singbox']['clients'])

    # The transport list is a real list of links, not just profile metadata.
    assert data['transports']
    assert all(item['url'].startswith(data['base_url']) and 'target=' in item['url']
               for item in data['transports'])

    assert data['config_count'] == 6 and data['config_limit'] == 6
    # The admin's support link always wins; with nothing configured the window
    # falls back to the built-in support channel instead of dropping the button.
    from app.config import SUPPORT_CHANNEL
    assert data['banner'] == 'تمدید از پشتیبانی' and data['support_url'] == SUPPORT_CHANNEL
    client.post('/api/customization', headers=h(), json={'support_url': 'https://t.me/my_desk'})
    try:
        assert client.get(f"/portal/{user['uuid']}/json").json()['support_url'] == 'https://t.me/my_desk'
    finally:
        client.post('/api/customization', headers=h(), json={'support_url': ''})
    assert data['flags'] is True
    # The admin's preferred shape is what the window marks as recommended.
    assert data['default_format'] == 'clash'

    page = client.get(f"/portal/{user['uuid']}")
    assert page.status_code == 200
    for label in ('لینک اشتراک هوشمند', 'خانواده Clash / Mihomo — فقط YAML', 'خانواده Xray',
                  'خانواده sing-box', 'پیشنهادی'):
        assert label in page.text, label
    assert 'details class="glass fam"' in page.text
    assert 'تمدید از پشتیبانی' in page.text

    unlimited = client.get(f"/portal/{_user('portalfree')['uuid']}/json").json()
    assert unlimited['max_configs'] is None and unlimited['config_count'] > 0


# ------------------------------------------------------- packs / subscription
def test_a_location_pack_creates_one_source_per_country_and_removes_it_again():
    listing = client.get('/api/edge/packs', headers=h()).json()
    packs = {item['id']: item for item in listing['packs']}
    assert {'multi-cdn', 'iran-tunnels'} <= set(packs)
    assert packs['multi-cdn']['installed'] == 0 and packs['multi-cdn']['locations'] >= 4
    assert all(entry['host'] and entry['location'] for entry in packs['multi-cdn']['entries'])

    installed = client.post('/api/edge/packs', headers=h(), json={'id': 'multi-cdn', 'action': 'install'})
    assert installed.status_code == 200, installed.text
    body = installed.json()
    assert len(body['created']) == packs['multi-cdn']['locations']
    assert {item['location'] for item in body['sources']} >= {'de', 'nl'}

    catalog.sync('https://panel.example.com', None)
    names = {node['name'] for node in catalog.list()}
    # One node per location, named after the location it terminates in.
    assert {'de', 'nl', 'us'} <= names

    again = client.get('/api/edge/packs', headers=h()).json()
    assert {item['id']: item['installed'] for item in again['packs']}['multi-cdn'] == len(body['created'])

    removed = client.post('/api/edge/packs', headers=h(), json={'id': 'multi-cdn', 'action': 'uninstall'}).json()
    assert len(removed['removed']) == len(body['created'])
    assert removed['sources'] == []
    assert client.post('/api/edge/packs', headers=h(), json={'id': 'nope'}).status_code == 400


def test_a_location_pack_can_be_installed_with_domains_the_admin_owns():
    created = client.post('/api/edge/packs', headers=h(), json={
        'id': 'iran-tunnels', 'action': 'install',
        'hosts': 'mine1.example.com, mine2.example.com'}).json()
    hosts = {item['host'] for item in created['sources']}
    assert hosts == {'mine1.example.com', 'mine2.example.com'}
    # Each tunnel keeps its own destination country *and* its own port.
    ports = sorted(item['port'] for item in created['sources'])
    assert len(set(ports)) == len(ports) and min(ports) > 1000


def test_the_reference_subscription_becomes_one_location_per_country():
    preview = client.post('/api/edge/import', headers=h(), json={'text': REFERENCE_LINKS}).json()
    assert preview['found'] == 4
    assert set(preview['locations']) == {'de', 'pl', 'lr', 'fi'}
    hosts = {item['host'] for item in preview['preview']}
    assert {'cdn1.torvixa.ir', 'cdn1.melqora.ir', '104.24.39.128', 'cdn16.qemitra.ir'} == hosts

    applied = client.post('/api/edge/import', headers=h(), json={'text': REFERENCE_LINKS, 'apply': True}).json()
    assert len(applied['created']) == 4
    sources = {item['location']: item for item in applied['sources']}
    assert sources['fi']['port'] == 30521 and sources['de']['host'] == 'cdn1.torvixa.ir'
    assert all(item['pack'] == 'imported' for item in applied['sources'])

    catalog.sync('https://panel.example.com', None)
    assert {'de', 'pl', 'lr', 'fi'} <= {node['name'] for node in client.get('/api/nodes', headers=h()).json()}

    # A body that is not a subscription is an error, not a silent no-op.
    assert client.post('/api/edge/import', headers=h(), json={'text': '<html>login</html>'}).status_code == 400
    assert client.post('/api/edge/import', headers=h(), json={}).status_code == 400


def test_a_subscription_url_is_downloaded_and_imported(monkeypatch):
    calls = {}

    def fake_fetch(url, timeout=15):
        calls['url'] = url
        return REFERENCE_LINKS

    monkeypatch.setattr(edge, '_http_text', fake_fetch)
    body = client.post('/api/edge/import', headers=h(), json={
        'url': 'https://subs.bikara.net/sub/N2QzZ18zNjc4ODU4MTcsMTc4OTQ3NjMyOAfILZH6A9TZ',
        'max_nodes': 2, 'apply': True}).json()
    assert calls['url'].startswith('https://subs.bikara.net/')
    assert body['url'] and len(body['created']) == 4
    assert all(item['max'] == 2 for item in body['sources'])

    bad = client.post('/api/edge/import', headers=h(), json={'url': 'ftp://nope.example.com'})
    assert bad.status_code == 400 and 'http' in bad.json()['detail']


# --------------------------------------------------------------- network tools
def test_the_tools_tab_reaches_the_endpoints_it_needs(monkeypatch):
    cidr = client.post('/api/tools/cidr', headers=h(), json={
        'value': '104.16.0.0/13, 151.101.0.0/16, junk', 'limit': 6}).json()
    assert cidr['invalid'] == ['junk'] and cidr['count'] == 2
    assert len(cidr['addresses']) == 6
    assert cidr['total_addresses'] > 60000
    assert client.post('/api/tools/cidr', headers=h(), json={'value': 'junk'}).status_code == 400

    parsed = client.post('/api/tools/parse', headers=h(), json={'text': REFERENCE_LINKS}).json()
    assert parsed['count'] == 4
    assert set(parsed['protocols']) == {'vless', 'trojan', 'ss', 'hysteria2'}
    assert set(parsed['locations']) == {'de', 'pl', 'lr', 'fi'}
    assert client.post('/api/tools/parse', headers=h(), json={'text': ''}).status_code == 400

    # The reachability check dials a real socket; patch the probe so the suite
    # stays offline, then assert both the result shape and an unreachable host.
    import app.nodes as nodes_module

    async def fake_tcp(host, port, timeout=4, tls=False, server_hostname=''):
        return (12.5, None) if host == 'reachable.example.com' else (None, 'timeout')

    async def fake_handshake(host, port, timeout=4, server_hostname=''):
        return (12.5, None, True) if host == 'reachable.example.com' else (None, 'timeout', False)

    async def fake_doh(name, endpoint='https://cloudflare-dns.com/dns-query'):
        return {'Answer': [{'name': name, 'data': '203.0.113.7'}]}

    monkeypatch.setattr(nodes_module.NodeProbe, 'tcp', staticmethod(fake_tcp))
    monkeypatch.setattr(nodes_module.NodeProbe, 'handshake', staticmethod(fake_handshake))
    monkeypatch.setattr('app.dns.service.doh', fake_doh)

    checked = client.post('/api/tools/check', headers=h(), json={
        'host': 'reachable.example.com', 'port': 443, 'tls': True}).json()
    assert checked['results']['tls']['ok'] is True
    assert checked['results']['tls']['latency_ms'] == 12.5
    assert checked['results']['tls']['verified'] is True
    assert checked['results']['tcp']['ok'] is True

    dead = client.post('/api/tools/check', headers=h(), json={
        'host': 'blocked.example.com', 'port': 443}).json()
    assert dead['results']['tcp']['ok'] is False and dead['results']['tcp']['error']

    dns = client.get('/api/tools/dns?name=example.com', headers=h()).json()
    assert dns['answer']['Answer'][0]['data'] == '203.0.113.7'
    assert client.get('/api/tools/dns', headers=h()).status_code == 400
    assert client.post('/api/tools/check', headers=h(), json={'host': ''}).status_code == 400


# --------------------------------------------------------------- customization
def test_customization_validates_what_it_stores():
    ok = client.post('/api/customization', headers=h(), json={
        'app_name': 'ZEUS', 'accent': '#5ad1ff', 'accent_secondary': '#8b7bff',
        'default_format': 'base64', 'default_max_configs': 12,
        'support_url': 'https://t.me/support'})
    assert ok.status_code == 200, ok.text
    data = ok.json()['customization']
    assert data['app_name'] == 'ZEUS' and data['accent'] == '#5ad1ff'
    assert data['default_format'] == 'base64' and data['default_max_configs'] == '12'
    assert [item['id'] for item in data['core_formats']] == ['auto', 'base64', 'singbox', 'clash']

    assert client.post('/api/customization', headers=h(), json={'accent': 'blue'}).status_code == 400
    assert client.post('/api/customization', headers=h(), json={'support_url': 't.me/x'}).status_code == 400
    assert client.post('/api/customization', headers=h(), json={'default_format': 'yaml'}).status_code == 400
    assert client.post('/api/customization', headers=h(), json={'default_max_configs': -3}).status_code == 400

    # A quick-created user picks the admin's default config count up.
    client.post('/api/customization', headers=h(), json={'default_max_configs': 7})
    quick = client.post('/api/users/quick', headers=h(), json={'preset': 'global-clean', 'username': 'quickcap'})
    assert quick.status_code == 200, quick.text
    assert quick.json()['user']['max_configs'] == 7


def test_the_config_default_is_reported_where_the_forms_read_it():
    """The create/edit form prefills from the settings defaults, and the
    quick-create button from ``/api/presets``: both have to carry the value."""
    client.post('/api/customization', headers=h(), json={'default_max_configs': 9})
    assert client.get('/api/customization', headers=h()).json()['default_max_configs'] == '9'
    # ``/api/presets`` feeds the quick-create button and the form's defaults.
    assert client.get('/api/presets', headers=h()).json()['defaults']['max_configs'] == '9'
