import base64
import json
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-panel-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-panel-test.db')

from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.db import execute, init_db
from app.main import _setting, _set, app
from app.nodes import ensure as ensure_nodes, upsert

init_db()
ensure_nodes()
client = TestClient(app)


PANEL_MODULES = [
    'js/core.js', 'js/ui.js', 'js/session.js', 'js/api.js', 'js/store.js', 'js/pwa.js',
    'js/views/dashboard.js', 'js/views/nodes.js', 'js/views/users.js', 'js/views/system.js',
    'js/views/customize.js', 'js/views/tools.js', 'js/views/advanced.js', 'js/views/guide.js',
    'js/app.js',
]
LEGACY_MODULES = ['app.js', 'app-dashboard.js', 'app-nodes.js', 'app-users.js', 'app-panel.js']


def _admin_password():
    return _setting('admin_password') or cfg.admin_password


def h():
    return {'X-Admin-Password': _admin_password()}


def _seed_nodes():
    execute('DELETE FROM nodes')
    upsert('railway-direct', 'railway', 'railway.example.com', 443, True, 'railway.example.com', 'railway.example.com', 'railway', {})
    upsert('cloudflare-01', 'cloudflare', '104.16.1.1', 443, True, 'worker.example.workers.dev', 'worker.example.workers.dev', 'cloudflare-probe', {})
    execute("UPDATE nodes SET latency_ms=25.0 WHERE name='cloudflare-01'")


def test_static_assets_are_served():
    # The panel is worthless without its stylesheet: every asset must be mounted.
    css = client.get('/static/app.css')
    assert css.status_code == 200
    assert 'text/css' in css.headers['content-type']
    assert 'Vazirmatn' in css.text
    for name in PANEL_MODULES:
        js = client.get(f'/static/{name}')
        assert js.status_code == 200, name
        assert 'javascript' in js.headers['content-type']
    # The old five-script bundle is gone; a stale copy would shadow the modules.
    for name in LEGACY_MODULES:
        assert client.get(f'/static/{name}').status_code == 404, name


def test_stylesheet_keeps_the_layout_inside_a_phone_viewport():
    # The RTL phone screenshots came from grid/flex children that could not
    # shrink, plus subscription URLs that refused to wrap. Both guards are
    # part of the shipped design system, so they are asserted here.
    css = client.get('/static/app.css').text
    assert 'min-width:0' in css
    assert 'overflow-wrap:anywhere' in css
    assert '-webkit-line-clamp:2' in css
    assert '@media (max-width:700px)' in css
    assert 'max-width:900px' in css


def test_phone_bottom_bar_does_not_clip_its_group_sheet():
    # On a phone the sidebar *is* the fixed bottom bar, and the desktop rule
    # clips it to its rounded corners. That ``overflow:hidden`` also swallowed
    # ``.nav-group-items`` — the sheet the group heads open *above* the bar — so
    # tapping «کاربران و نودها», «شبکه و لبه» or «پنل و ظاهر» highlighted the head
    # but never showed its two tabs. Only single-tab groups worked, because they
    # navigate straight to their section instead of unfolding.
    css = client.get('/static/app.css').text
    clipped = css.index('.sidebar{')
    assert 'overflow:hidden' in css[clipped:css.index('}', clipped)], 'the desktop clip this test guards against is gone'
    # The phone override must come after it to win the cascade, and pin the sheet
    # to the whole bar (not to one ~78px cell, which ran off the screen edge).
    phone = css.index('.sidebar{overflow:visible}')
    assert phone > clipped
    assert '.sidebar .nav-groups .nav-group{position:static}' in css
    assert '.sidebar .nav-groups .nav-group-items{left:0;right:0;min-width:0}' in css
    # The phone sheet is the absolute panel that sits above the bar.
    assert 'position:absolute;bottom:calc(100% + 12px)' in css


def test_every_module_import_resolves():
    # An ES module that imports a missing file fails at load time in the browser,
    # so the module graph is verified here as well.
    import re
    seen = set()
    for name in PANEL_MODULES:
        source = client.get(f'/static/{name}').text
        for target in re.findall(r"from\s+'([^']+)'", source):
            if not target.startswith('.'):
                continue
            base = '/'.join(name.split('/')[:-1])
            parts = (base + '/' + target).split('/')
            resolved = []
            for part in parts:
                if part == '.':
                    continue
                if part == '..':
                    resolved.pop()
                else:
                    resolved.append(part)
            path = '/'.join(resolved)
            assert path in PANEL_MODULES, f'{name} imports unknown module {path}'
            seen.add(path)
    assert 'js/core.js' in seen


def test_dashboard_shell_references_assets():
    r = client.get('/', headers=h())
    assert r.status_code == 200
    assert '/static/app.css' in r.text
    # One module entry point: the imports own the load order, not the template.
    assert '<script type="module" src="/static/js/app.js"></script>' in r.text
    assert r.text.count('<script ') == 1
    for name in LEGACY_MODULES:
        assert f'/static/{name}"' not in r.text


def test_panel_carries_no_legacy_brand():
    # The panel was renamed: no shipped asset may still carry the old codename.
    for name in PANEL_MODULES:
        assert 'ZEUS' not in client.get(f'/static/{name}').text


def test_pwa_assets_are_installable():
    # Without a manifest, a service worker and real icons the browser will not
    # offer the install prompt.
    manifest = client.get('/manifest.webmanifest')
    assert manifest.status_code == 200
    assert 'manifest' in manifest.headers['content-type']
    body = manifest.json()
    assert body['start_url'].startswith('/')
    assert body['display'] == 'standalone'
    sizes = {icon['sizes'] for icon in body['icons']}
    assert {'192x192', '512x512'} <= sizes
    assert any(icon.get('purpose') == 'maskable' for icon in body['icons'])

    worker = client.get('/sw.js')
    assert worker.status_code == 200
    assert 'javascript' in worker.headers['content-type']
    # Scope / requires the worker to be served from the origin root.
    assert worker.headers.get('service-worker-allowed') == '/'
    # The build token is injected per deployment: without it an installed phone
    # would keep serving the previous shell (the stale-CSS symptom).
    assert "const VERSION = 'nexus-" in worker.text
    assert 'nexus-dev' not in worker.text
    token = worker.headers.get('x-nexus-build')
    assert token and len(token) == 12
    assert f"const VERSION = 'nexus-{token}';" in worker.text
    assert client.get('/api/version').json()['build'] == token

    for name in ('icon-192.png', 'icon-512.png', 'icon-maskable-512.png', 'apple-touch-icon.png', 'favicon-32.png'):
        icon = client.get(f'/static/icons/{name}')
        assert icon.status_code == 200, name
        assert icon.content[:8] == b'\x89PNG\r\n\x1a\n', name
    logo = client.get('/static/icons/nexus.svg')
    assert logo.status_code == 200 and 'svg' in logo.headers['content-type']


def test_panel_shell_declares_pwa_metadata():
    page = client.get('/', headers=h()).text
    assert 'rel="manifest" href="/manifest.webmanifest"' in page
    assert '/static/icons/apple-touch-icon.png' in page
    assert 'apple-mobile-web-app-capable' in page
    assert 'data-pwa-install' in page


def test_session_lifetime_is_admin_configurable():
    # The panel used to sign the admin out constantly; the lifetime is now a
    # validated setting and remember-me extends it further.
    assert client.post('/api/settings', headers=h(), json={'session_days': '0'}).status_code == 400
    assert client.post('/api/settings', headers=h(), json={'session_days': '999'}).status_code == 400
    assert client.post('/api/settings', headers=h(), json={'ping_interval': 'abc'}).status_code == 400
    assert client.post('/api/settings', headers=h(), json={'accent': 'blue'}).status_code == 400
    saved = client.post('/api/settings', headers=h(), json={'session_days': '14', 'ping_interval': '20', 'accent': '#12ab34', 'app_name': 'NEXUS-CORE'})
    assert saved.status_code == 200, saved.text
    payload = client.get('/api/settings', headers=h()).json()
    assert payload['session_days'] == '14'
    assert payload['security']['session_days'] == 14
    assert payload['brand']['app_name'] == 'NEXUS-CORE'
    assert payload['pwa']['service_worker'] == '/sw.js'

    remember = _login(remember=True)
    plain = _login()
    assert remember.json()['ttl_days'] >= 30
    assert plain.json()['expires_in'] < remember.json()['expires_in']
    manifest = client.get('/manifest.webmanifest').json()
    assert 'NEXUS-CORE' in manifest['name']

    client.post('/api/settings', headers=h(), json={'session_days': '', 'ping_interval': '', 'accent': '', 'app_name': ''})
    client.post('/api/settings', headers=h(), json={'accent_secondary': ''})


def test_reload_self_heals_the_node_catalog():
    # A fresh deployment that only read the database would publish an empty
    # catalog; opening the panel must create the origin node. It is named after
    # the platform it runs on (railway-direct, vps-direct, render-direct…).
    from app.nodes import origin_node_name
    execute('DELETE FROM nodes')
    assert client.get('/api/metrics', headers=h()).json()['totals']['nodes'] == 0
    page = client.get('/', headers=h())
    assert page.status_code == 200
    nodes = client.get('/api/nodes', headers=h()).json()
    origin = [node for node in nodes if node['kind'] == 'railway']
    assert origin, 'the origin node must exist on any platform'
    assert origin[0]['name'] == origin_node_name()
    assert origin[0]['role'] == 'origin'
    _seed_nodes()


def test_panel_requires_auth():
    # A fresh client: the shared one may already hold a session cookie from an
    # earlier login test.
    anonymous = TestClient(app)
    assert anonymous.get('/api/metrics').status_code == 401
    assert anonymous.get('/api/settings').status_code == 401
    assert anonymous.get('/api/users/x/links').status_code == 401
    assert anonymous.post('/api/nodes/ping', json={}).status_code == 401


def test_metrics_payload_drives_charts():
    _seed_nodes()
    r = client.get('/api/metrics', headers=h())
    assert r.status_code == 200
    data = r.json()
    for key in ('totals', 'series_hourly', 'series_daily', 'protocols', 'nodes', 'top_users', 'server_time', 'uptime_seconds'):
        assert key in data
    assert data['totals']['nodes'] == 2
    assert data['totals']['railway_nodes'] == 1
    assert data['totals']['cloudflare_nodes'] == 1
    assert len(data['nodes']) == 2


def test_user_links_cover_every_target_and_node():
    _seed_nodes()
    execute('DELETE FROM users')
    created = client.post('/api/users', headers=h(), json={'username': 'linksuser', 'protocol': 'vless'})
    assert created.status_code == 200, created.text
    data = client.get('/api/users/linksuser/links', headers=h()).json()

    targets = {s['target'] for s in data['subscriptions']}
    assert {'auto', 'all', 'vless', 'trojan', 'vmess', 'ss', 'base64', 'singbox', 'clash', 'xray', 'json'} <= targets
    assert data['node_count'] == 2
    # The panel offers one subscription per published transport, per node.
    transport_ids = {t['target'] for t in data['transports']}
    assert {'vless-ws', 'vless-cdn', 'vmess-ws', 'trojan-ws', 'ss-ws'} <= transport_ids
    hit = [n for n in data['nodes'] if n['name'] == 'cloudflare-01'][0]
    assert {'vless', 'trojan', 'vmess', 'singbox', 'clash', 'xray', 'primary'} <= set(hit['links'])
    assert hit['links']['primary'].startswith('vless://')
    assert 'node=cloudflare-01' in hit['subscription']
    assert {s['target'] for s in hit['subscriptions']} == {'vless', 'trojan', 'vmess', 'base64', 'singbox', 'clash', 'xray'}
    assert transport_ids == {s['target'] for s in hit['transport_subscriptions']}
    # Every profile on that node carries a real link plus the JSON variants.
    assert {p['id'] for p in hit['profiles']} >= {'vless-ws', 'vmess-ws', 'ss-ws'}
    for profile in hit['profiles']:
        assert profile['singbox']['type'] and profile['clash']['type'] and profile['xray']['protocol']
        if profile['protocol'] in ('vless', 'trojan', 'vmess'):
            assert '://' in profile['link']
    assert client.get('/api/users/ghost/links', headers=h()).status_code == 404


def test_xray_config_registers_every_user_on_every_edge_inbound():
    """The generated config must be coherent: one inbound per published profile,
    a distinct listener port each, and every active user on every inbound."""
    from app import xray
    from app.subscriptions import transports as tp

    execute('DELETE FROM users')
    execute('INSERT INTO users(username,uuid,protocol,is_active,created_at) VALUES(?,?,?,1,0)',
            ('cfg-all', '11111111-2222-3333-4444-555555555555', 'all'))
    execute('INSERT INTO users(username,uuid,protocol,is_active,created_at) VALUES(?,?,?,1,0)',
            ('cfg-two', '22222222-2222-3333-4444-666666666666', 'vless,trojan'))
    execute('INSERT INTO users(username,uuid,protocol,is_active,created_at) VALUES(?,?,?,0,0)',
            ('cfg-off', '33333333-2222-3333-4444-777777777777', 'all'))

    config = xray._config()
    inbounds = [item for item in config['inbounds'] if item['tag'] in {p['id'] for p in tp.EDGE_PROFILES}]
    assert {item['tag'] for item in inbounds} == {p['id'] for p in tp.EDGE_PROFILES}
    ports = [item['port'] for item in inbounds]
    assert len(set(ports)) == len(ports), 'two transports must never share a listener port'
    assert len({item['tag'] for item in config['inbounds']}) == len(config['inbounds'])

    for item in inbounds:
        assert item['listen'] == '127.0.0.1', 'edge listeners must stay on loopback'
        if item['protocol'] == 'shadowsocks':
            continue
        emails = {client.get('email') for client in item['settings']['clients']}
        assert 'cfg-all@nexus.local' in emails, item['tag']
        # A narrowed user is only on the inbounds it selected, and a disabled
        # user is on none of them.
        assert ('cfg-two@nexus.local' in emails) is (item['tag'].startswith('vless') or item['tag'].startswith('trojan'))
        assert 'cfg-off@nexus.local' not in emails

    # Shadowsocks is the one listener without a client list: it authenticates a
    # single key per cipher, because a multi-user 2022 inbound is rejected by
    # Xray for every method but blake3-aes-gcm - and one rejected inbound used to
    # abort startup and take every other protocol down with it.
    import base64 as b64
    shadowsocks = [item for item in inbounds if item['protocol'] == 'shadowsocks']
    assert {item['settings']['method'] for item in shadowsocks} == {c['method'] for c in tp.SS_CIPHERS}
    for item in shadowsocks:
        cipher = next(c for c in tp.SS_CIPHERS if c['method'] == item['settings']['method'])
        assert 'clients' not in item['settings'], item['tag']
        assert item['settings']['password'] == tp.ss_shared_key(cipher)
        assert len(b64.b64decode(item['settings']['password'])) == (cipher['key_len'] or 20)

    # Every path the edge bridges has a route, and the Worker proxies them all.
    assert set(xray.edge_routes()) == {p['path'] for p in tp.EDGE_PROFILES} | {'/ws/warp'}
    execute('DELETE FROM users')


def test_a_rejected_transport_shrinks_the_config_instead_of_killing_it():
    """The startup ladder must always end in a config the engine can serve.

    Xray validates the whole file at once, so one inbound it dislikes (an SS cipher
    it refuses, a Reality/WARP build the host forbids) used to abort startup and
    stop every protocol. Each rung is tried in order and the profiles it really
    contains are what gets published.
    """
    from app import xray
    from app.subscriptions import transports as tp

    ladder = list(xray._candidate_configs())
    assert len(ladder) == 2 + len(tp.SS_CIPHERS)
    served_sets = [set(served) for _config, served, _note in ladder]
    assert all(len(before) >= len(after) for before, after in zip(served_sets, served_sets[1:]))
    assert len(served_sets[0]) == len(xray._config()['inbounds'])
    # The last resort serves the edge without any Shadowsocks listener at all.
    assert not [item for item in ladder[-1][0]['inbounds'] if item['protocol'] == 'shadowsocks']

    for config, served, _note in ladder:
        assert len({item['tag'] for item in config['inbounds']}) == len(config['inbounds'])
        ports = [item['port'] for item in config['inbounds']]
        assert len(set(ports)) == len(ports)
        assert all(item['listen'] == '127.0.0.1' for item in config['inbounds'])
        # ``served`` is exactly what the panel may publish for this rung.
        assert {item['tag'] for item in config['inbounds']} == set(served)


def test_transport_catalog_endpoint_describes_the_matrix():
    _seed_nodes()
    data = client.get('/api/transports', headers=h()).json()
    ids = {p['id'] for p in data['profiles']}
    assert {'vless-ws', 'vless-cdn', 'vmess-ws', 'vmess-cdn', 'trojan-ws', 'trojan-cdn', 'ss-ws'} <= ids
    assert {'vless', 'vmess', 'trojan', 'ss'} <= set(data['protocols'])
    # gRPC / XHTTP / HTTPUpgrade cannot ride an HTTPS-only edge, so they are
    # advertised as planned (with the reason) instead of as broken links.
    assert {p['id'] for p in data['planned']} >= {'vless-grpc', 'vless-xhttp'}
    assert all(p['needs'] for p in data['planned'])
    assert data['nodes'] and all(node['transports'] for node in data['nodes'])
    assert data['xray']['vless_listener'] == cfg.xray_vless_port


def test_subscription_targets_and_per_node_links():
    _seed_nodes()
    execute('DELETE FROM users')
    uuid_value = client.post('/api/users', headers=h(), json={'username': 'subuser', 'protocol': 'vless'}).json()['uuid']

    auto = client.get(f'/sub/{uuid_value}?target=auto')
    assert auto.status_code == 200 and auto.text.startswith('vless://')

    both = client.get(f'/sub/{uuid_value}?target=all').text
    assert 'vless://' in both and 'trojan://' in both

    one = client.get(f'/sub/{uuid_value}?target=vless&node=cloudflare-01')
    assert '104.16.1.1' in one.text
    assert one.headers['x-nexus-node-count'] == '1'
    assert one.headers['x-nexus-target'] == 'vless'

    path_style = client.get(f'/sub/{uuid_value}/railway-direct?target=vless')
    assert path_style.status_code == 200 and 'railway.example.com' in path_style.text

    payload = json.loads(client.get(f'/sub/{uuid_value}?target=xray').text)
    assert payload['outbounds'][0]['protocol'] == 'vless'
    assert payload['outbounds'][0]['streamSettings']['network'] == 'ws'
    # One entry per node and transport: 2 nodes × (ws + cdn) for VMess.
    assert client.get(f'/sub/{uuid_value}?target=vmess').text.count('vmess://') == 4
    # target=ws keeps the WebSocket transports only: 2 nodes × 2 VLESS paths.
    assert client.get(f'/sub/{uuid_value}?target=ws').text.count('vless://') == 4

    aliases = json.loads(client.get(f'/sub/{uuid_value}?target=sing-box').text)
    assert len(aliases['outbounds']) == 2 * len(transport_ids())

    assert client.get(f'/sub/{uuid_value}?target=unknown').status_code == 400
    assert client.get(f'/sub/{uuid_value}?node=does-not-exist').status_code == 404


def transport_ids():
    return [p['id'] for p in client.get('/api/transports', headers=h()).json()['profiles']]


def test_settings_roundtrip_and_prefix_reaches_links():
    _seed_nodes()
    execute('DELETE FROM users')
    uuid_value = client.post('/api/users', headers=h(), json={'username': 'prefixuser'}).json()['uuid']

    payload = client.get('/api/settings', headers=h()).json()
    assert 'resolved_base_url' in payload
    assert payload['subscription']['targets'][0] == 'auto'

    assert client.post('/api/settings', headers=h(), json={'public_base_url': 'ftp://nope'}).status_code == 400
    assert client.post('/api/settings', headers=h(), json={'default_protocol': 'wireguard'}).status_code == 400

    saved = client.post('/api/settings', headers=h(), json={'sub_prefix': 'NEXUS-TEST', 'default_limit_gb': '50'})
    assert saved.status_code == 200
    after = client.get('/api/settings', headers=h()).json()
    assert after['sub_prefix'] == 'NEXUS-TEST'
    assert after['defaults']['limit_gb'] == '50'
    assert 'NEXUS-TEST' in client.get(f'/sub/{uuid_value}?target=vless').text

    client.post('/api/settings', headers=h(), json={'sub_prefix': ''})


def test_password_change_flow():
    original = _admin_password()
    assert client.post('/api/settings/password', headers=h(), json={'current': 'nope', 'new': 'nexus-test-pass'}).status_code == 403
    assert client.post('/api/settings/password', headers=h(), json={'current': original, 'new': 'short'}).status_code == 400
    assert client.post('/api/settings/password', headers=h(), json={'current': original, 'new': 'nexus-test-pass-1'}).status_code == 200
    assert client.post('/api/login', json={'password': 'nexus-test-pass-1'}).status_code == 200
    _set('admin_password', original)


def _login(**extra):
    body = {'password': _admin_password()}
    if 'remember' in extra:
        body['remember'] = extra.pop('remember')
    return client.post('/api/login', json=body, **extra)


def test_session_header_works_without_cookies():
    token = _login().json()['token']
    anonymous = TestClient(app)
    assert anonymous.get('/api/settings').status_code == 401
    assert anonymous.get('/api/settings', headers={'X-Nexus-Session': token}).status_code == 200
    assert anonymous.get('/api/settings', headers={'X-Nexus-Session': 'not-a-token'}).status_code == 401


def test_cookie_policy_follows_request_context():
    # Plain HTTP: no Secure flag, Lax so a top-level navigation still works.
    plain = _login().headers['set-cookie'].lower()
    assert 'httponly' in plain and 'samesite=lax' in plain and 'secure;' not in plain
    # HTTPS same-site keeps the strictest policy.
    strict = _login(headers={'X-Forwarded-Proto': 'https', 'Sec-Fetch-Site': 'same-origin'}).headers['set-cookie'].lower()
    assert 'samesite=strict' in strict and 'secure' in strict
    # Embedded on another origin: only SameSite=None; Secure is sent back.
    embedded = _login(headers={'X-Forwarded-Proto': 'https', 'Sec-Fetch-Site': 'cross-site'}).headers['set-cookie'].lower()
    assert 'samesite=none' in embedded and 'secure' in embedded


def test_panel_bootstraps_from_url_token():
    token = _login().json()['token']
    anonymous = TestClient(app)
    page = anonymous.get(f'/?token={token}')
    assert page.status_code == 200
    assert '/static/js/app.js' in page.text
    # Without a session (bad token or no token at all) the shell must not render.
    assert anonymous.get('/?token=bogus', follow_redirects=False).status_code == 303
    assert anonymous.get('/', follow_redirects=False).headers['location'] == '/login'


def test_node_update_and_delete():
    execute('DELETE FROM nodes')
    upsert('manual-01', 'railway', 'manual.example.com', 443, True, 'manual.example.com', 'manual.example.com', 'manual', {})
    updated = client.put('/api/nodes/manual-01', headers=h(), json={'port': 8443, 'enabled': 0})
    assert updated.status_code == 200
    assert updated.json()['node']['port'] == 8443
    assert updated.json()['node']['enabled'] == 0
    assert client.put('/api/nodes/manual-01', headers=h(), json={'kind': 'bogus'}).status_code == 400
    assert client.put('/api/nodes/ghost', headers=h(), json={'port': 1}).status_code == 404
    deleted = client.delete('/api/nodes/manual-01', headers=h())
    assert deleted.status_code == 200 and deleted.json()['success'] is True


def test_audit_trail_is_written_and_cleared():
    execute('DELETE FROM users')
    assert client.post('/api/users', headers=h(), json={'username': 'audituser'}).status_code == 200
    logs = client.get('/api/logs', headers=h()).json()
    assert any(item['action'] == 'user.create' for item in logs)
    assert client.delete('/api/logs', headers=h()).json()['success'] is True
    remaining = client.get('/api/logs', headers=h()).json()
    assert [item['action'] for item in remaining] == ['logs.clear']


def _clear_defaults():
    client.post('/api/settings', headers=h(), json={
        'default_protocol': 'vless', 'default_limit_gb': '', 'default_expiry_days': '', 'default_ip_limit': '',
    })


def test_preset_catalog_exposes_iran_bundles():
    payload = client.get('/api/presets', headers=h()).json()
    assert {p['id'] for p in payload['presets']} >= {'iran-fast', 'iran-unlimited', 'global-clean'}
    assert payload['default'] == 'iran-fast'
    fast = [p for p in payload['presets'] if p['id'] == 'iran-fast'][0]
    assert fast['fields']['frag_len'] and fast['fields']['frag_int']
    assert fast['highlights']


def test_client_catalog_covers_named_clients():
    data = client.get('/api/clients', headers=h()).json()
    ids = {c['id'] for c in data['clients']}
    # The clients the panel promises a dedicated subscription for.
    assert {'bettbox', 'exclusive', 'nekoboxplus', 'v2rayng', 'hiddify', 'clash', 'singbox'} <= ids
    for entry in data['clients']:
        if entry['id'] == 'smart':
            continue
        assert entry['download'].startswith('http'), entry['id']
    # Bettbox runs on Clash/Mihomo, so it is only ever handed YAML — never Base64.
    bettbox = [c for c in data['clients'] if c['id'] == 'bettbox'][0]
    assert bettbox['targets'][0] == 'clash'
    assert bettbox['family'] == 'mihomo' and 'base64' not in bettbox['targets']
    nekobox = [c for c in data['clients'] if c['id'] == 'nekoboxplus'][0]
    assert nekobox['targets'][0] == 'singbox' and nekobox['family'] == 'singbox'
    # Every client carries the engine family the sublink lists are grouped by.
    assert {'core', 'xray', 'singbox', 'mihomo', 'tools'} <= {f['id'] for f in data['families']}
    assert all(c.get('family') and c.get('family_label') for c in data['clients'])


def test_quick_create_applies_iran_preset_and_returns_links():
    _seed_nodes()
    _clear_defaults()
    execute('DELETE FROM users')
    response = client.post('/api/users/quick', headers=h(), json={'preset': 'iran-fast'})
    assert response.status_code == 200, response.text
    data = response.json()
    user = data['user']
    # Real engine: the user exists with the tuned settings applied.
    assert user['username'].startswith('nxs-')
    assert user['protocol'] == 'vless'
    assert user['frag_len'] == '100-200' and user['frag_int'] == '10-20'
    assert user['fingerprint'] == 'chrome'
    assert bool(user['block_ads']) is True
    assert user['ip_limit'] == 2
    assert user['limit_gb'] == 60 and user['expiry_days'] == 30
    assert data['applied']
    # The response already carries the status window and every client link.
    assert data['portal_url'].endswith('/portal/' + user['uuid'])
    assert {c['id'] for c in data['clients']} >= {'bettbox', 'exclusive', 'nekoboxplus'}
    assert data['targets']
    # And the generated subscription really renders (YAML for bettbox).
    sub = client.get(f"/sub/{user['uuid']}?target=bettbox")
    assert sub.status_code == 200
    assert sub.headers['x-nexus-target'] == 'clash' and 'proxies' in sub.text


def test_quick_create_precedence_and_validation():
    _seed_nodes()
    _clear_defaults()
    execute('DELETE FROM users')
    named = client.post('/api/users/quick', headers=h(), json={'preset': 'global-clean', 'username': 'quicknamed', 'limit_gb': 5})
    assert named.status_code == 200, named.text
    user = named.json()['user']
    assert user['username'] == 'quicknamed' and user['limit_gb'] == 5
    assert not user['frag_len']  # this preset deliberately ships without fragment
    assert client.post('/api/users/quick', headers=h(), json={'preset': 'nope'}).status_code == 400
    assert client.post('/api/users/quick', headers=h(), json={'username': 'quicknamed'}).status_code == 400
    # Admin defaults win over the preset, but the tuning still applies.
    client.post('/api/settings', headers=h(), json={'default_limit_gb': '7', 'default_ip_limit': '4'})
    tweaked = client.post('/api/users/quick', headers=h(), json={'preset': 'iran-fast', 'username': 'defaultswin'}).json()['user']
    assert tweaked['limit_gb'] == 7 and tweaked['ip_limit'] == 4
    assert tweaked['frag_len'] == '100-200'
    _clear_defaults()


def test_per_client_subscription_formats():
    _seed_nodes()
    execute('DELETE FROM users')
    uuid_value = client.post('/api/users', headers=h(), json={'username': 'clientuser'}).json()['uuid']

    # Bettbox is a Clash/Mihomo client: YAML only, never a Base64 container.
    bettbox = client.get(f'/sub/{uuid_value}?target=bettbox')
    assert bettbox.status_code == 200
    assert 'proxies' in bettbox.text and 'vless://' not in bettbox.text
    assert bettbox.headers['x-nexus-target'] == 'clash'

    clash = client.get(f'/sub/{uuid_value}?target=clash')
    assert clash.headers['x-nexus-target'] == 'clash'

    exclusive = client.get(f'/sub/{uuid_value}/cloudflare-01?target=exclusive')
    assert exclusive.status_code == 200
    assert '104.16.1.1' in base64.b64decode(exclusive.text).decode()

    nekobox = client.get(f'/sub/{uuid_value}?target=nekoboxplus')
    assert len(json.loads(nekobox.text)['outbounds']) == 2 * len(transport_ids())

    assert client.get(f'/sub/{uuid_value}?target=unknown-client').status_code == 400


def test_a_user_is_provisioned_on_every_inbound_by_default():
    """One credential, all paths: the user must exist in every protocol inbound.

    This is the bug behind "you cannot select all protocols for one user": the
    inbounds used to filter users by the single ``protocol`` column, so a VLESS
    user's VMess/Trojan/Shadowsocks links were published but rejected by Xray.
    """
    from app import xray

    _seed_nodes()
    execute('DELETE FROM users')
    created = client.post('/api/users', headers=h(), json={'username': 'allproto'}).json()
    assert created['protocols'] == ['vless', 'vmess', 'trojan', 'ss']
    assert 'همه' in created['protocol_label']

    for protocol in ('vless', 'vmess', 'trojan'):
        clients = xray._clients_for(protocol, {'method': '2022-blake3-aes-128-gcm', 'key_len': 16})
        assert 'allproto@nexus.local' in [entry['email'] for entry in clients], protocol

    # Shadowsocks carries a single key per cipher instead of a client list (see
    # the inbound test), so ``all protocols`` there means the listener exists and
    # the user's subscription carries its link.
    from app.subscriptions import transports as tp
    assert xray._clients_for('ss', {'method': '2022-blake3-aes-128-gcm', 'key_len': 16}) == []
    ss_inbound = xray._protocol_settings({'protocol': 'ss', 'method': '2022-blake3-aes-128-gcm', 'key_len': 16})
    assert ss_inbound['password'] == tp.ss_shared_key({'method': '2022-blake3-aes-128-gcm'})

    data = client.get('/api/users/allproto/links', headers=h()).json()
    assert {profile['protocol'] for profile in data['profiles']} == {'vless', 'vmess', 'trojan', 'ss'}
    # And every one of those really renders a usable subscription.
    for target in ('vless', 'vmess', 'trojan', 'ss'):
        text = client.get(f"/sub/{data['uuid']}?target={target}").text
        assert text.count(target + '://') == 2 * len([p for p in data['profiles'] if p['protocol'] == target])
        assert text.count(target + '://') >= 2
    # Shadowsocks now has link form too (SIP002 + the WebSocket plugin), so a
    # dead-transport install is the only thing left that falls back to JSON.
    text = client.get(f"/sub/{data['uuid']}?target=ss").text
    assert text.count('ss://') >= 2 and not text.lstrip().startswith('{')


def test_protocol_selection_can_be_all_or_a_subset():
    """The panel sends a list; a subset genuinely narrows every link."""
    _seed_nodes()
    execute('DELETE FROM users')
    client.post('/api/users', headers=h(), json={'username': 'multi'})

    # All protocols selected at once (what the default form submits).
    every = client.put('/api/users/multi', headers=h(),
                       json={'protocol': ['vless', 'vmess', 'trojan', 'ss']}).json()
    assert sorted(every['protocols']) == ['ss', 'trojan', 'vless', 'vmess']
    assert every['protocol_value'] == 'all'

    # A real subset only offers what was selected.
    narrow = client.put('/api/users/multi', headers=h(), json={'protocol': ['vless', 'trojan']}).json()
    assert narrow['protocols'] == ['vless', 'trojan']
    assert narrow['protocol_value'] == 'vless,trojan'
    data = client.get('/api/users/multi/links', headers=h()).json()
    assert {p['protocol'] for p in data['profiles']} == {'vless', 'trojan'}
    assert {t['protocol'] for t in data['transports']} == {'vless', 'trojan'}
    assert client.get(f"/sub/{data['uuid']}?target=vmess").status_code == 400
    assert 'trojan://' in client.get(f"/sub/{data['uuid']}?target=all").text

    # The status window of the narrowed user lists only the live transports.
    portal = client.get(f"/portal/{data['uuid']}/json").json()
    assert {t['protocol'] for t in portal['transports']} == {'vless', 'trojan'}
    assert portal['protocol_label'] == 'VLESS · TROJAN'

    # A string list works too, an unknown protocol is rejected, and turning
    # every protocol off falls back to the full set instead of locking the user
    # out of every inbound.
    assert client.put('/api/users/multi', headers=h(), json={'protocol': 'vless,trojan'}).json()['protocols'] == ['vless', 'trojan']
    assert client.post('/api/users', headers=h(), json={'username': 'bad', 'protocol': 'wireguard'}).status_code == 422
    assert client.put('/api/users/multi', headers=h(), json={'protocol': []}).json()['protocols'] == ['vless', 'vmess', 'trojan', 'ss']

    # Legacy single-value rows keep meaning "the whole matrix".
    execute("UPDATE users SET protocol='vless' WHERE username='multi'")
    legacy = client.get('/api/users/multi/links', headers=h()).json()
    assert {p['protocol'] for p in legacy['profiles']} == {'vless', 'vmess', 'trojan', 'ss'}
    assert 'vless' in legacy['subscription']


def test_shadowsocks_ships_every_cipher_family():
    """Shadowsocks is several ciphers, each with its own listener, key and link."""
    from app.subscriptions import transports as tp

    _seed_nodes()
    execute('DELETE FROM users')
    data = client.get('/api/transports', headers=h()).json()
    methods = {p['method'] for p in data['profiles'] if p['protocol'] == 'ss'}
    # The classic aes-256-gcm cipher is published too, and first: it is the one
    # method every client implements, so Shadowsocks shows up (and can be
    # pinged) outside the SIP022-aware apps as well.
    assert methods == {'aes-256-gcm', '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm',
                       '2022-blake3-chacha20-poly1305', 'chacha20-ietf-poly1305'}
    assert set(data['ss_methods']) == methods
    assert tp.SS_CIPHERS[0]['method'] == 'aes-256-gcm', 'the universal cipher must come first'
    variants = {variant['id'] for variant in data['protocol_catalog']['shadowsocks']}
    assert variants == {'ss-classic', 'ss', 'ss-aes256', 'ss-chacha', 'ss-legacy'}
    # Every cipher is linkable, so it reaches a client's config list.
    assert all(variant['linkable'] for variant in data['protocol_catalog']['shadowsocks'])

    # One key per cipher (not per user), sized to the cipher: 16 bytes for
    # AES-128, 32 for the other 2022 methods, 20 for the classic passwords.
    import base64 as b64
    user = {'uuid': '11111111-2222-3333-4444-555555555555'}
    keys = {c['id']: tp.ss_key(user, c) for c in tp.SS_CIPHERS}
    assert len(set(keys.values())) == len(tp.SS_CIPHERS)
    assert {c['id']: len(b64.b64decode(keys[c['id']])) for c in tp.SS_CIPHERS} == {
        'ss-classic': 20, 'ss': 16, 'ss-aes256': 32, 'ss-chacha': 32, 'ss-legacy': 20}
    # The same key is what the listener authenticates and what every link carries.
    assert tp.ss_key({'uuid': 'someone-else'}, tp.SS_CIPHERS[0]) == keys['ss-classic']
    from app import xray
    chacha = next(c for c in tp.SS_CIPHERS if c['id'] == 'ss-chacha')
    assert xray._protocol_settings(dict(chacha, protocol='ss'))['password'] == keys['ss-chacha']

    uuid_value = client.post('/api/users', headers=h(), json={'username': 'ssuser'}).json()['uuid']

    # SIP002 links: every SS node is importable, and the edge rides a plugin.
    lines = [line for line in client.get(f'/sub/{uuid_value}?target=ss').text.splitlines()
             if line.startswith('ss://')]
    assert len(lines) == 2 * len([p for p in data['profiles'] if p['protocol'] == 'ss'])
    # The SIP002 plugin options use the spelling every client parses: the plugin
    # name first, then mode/path/host and the bare tls flag last.
    assert all('plugin=v2ray-plugin' in line and 'mode%3Dwebsocket' in line
               and 'host%3D' in line and '%3Btls' in line for line in lines)
    userinfo = lines[0].split('://', 1)[1].split('@', 1)[0]
    method, secret = b64.b64decode(userinfo + '=' * (-len(userinfo) % 4)).decode().split(':', 1)
    assert method in methods and secret == tp.ss_key(user, {'method': method})

    # Every cipher lands in the JSON subscriptions with its own method.
    outbounds = json.loads(client.get(f'/sub/{uuid_value}?target=singbox').text)['outbounds']
    shadowsocks = [o for o in outbounds if o['type'] == 'shadowsocks']
    assert {o['method'] for o in shadowsocks} == methods
    assert len({o['password'] for o in shadowsocks}) == len(tp.SS_CIPHERS)
    assert all(o['plugin'] == 'v2ray-plugin' and 'mode=websocket' in o['plugin_opts'] for o in shadowsocks)
    clash = json.loads(client.get(f'/sub/{uuid_value}?target=clash').text)['proxies']
    assert {p['cipher'] for p in clash if p['type'] == 'ss'} == methods
    xray_out = json.loads(client.get(f'/sub/{uuid_value}?target=xray').text)['outbounds']
    assert {o['settings']['servers'][0]['method'] for o in xray_out if o['protocol'] == 'shadowsocks'} == methods


def test_shadowsocks_keys_can_be_rotated_from_the_panel():
    """A key shared by every link needs a revoke path: rotating changes them all."""
    from app.subscriptions import transports as tp

    before = {c['id']: tp.ss_key({}, c) for c in tp.SS_CIPHERS}
    response = client.post('/api/settings/rotate-shadowsocks', headers=h(), json={})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['rotated'] == [c['id'] for c in tp.SS_CIPHERS]
    after = {c['id']: tp.ss_key({}, c) for c in tp.SS_CIPHERS}
    assert set(after) == set(before) and all(after[cipher] != before[cipher] for cipher in before)
    # The panel learns the new state (served profiles + keys) from the same call.
    assert data['transports']['protocols'] and 'ss' in data['transports']['protocols']


def test_ping_probes_every_node_and_records_the_result():
    _seed_nodes()
    # Cloudflare clean IPs are dialled by IP with the Worker host as SNI, the
    # Railway origin gets a full TLS handshake. Without egress both just record
    # a failure, so the assertions stay about persistence, not connectivity.
    response = client.post('/api/nodes/ping', headers=h(), json={'timeout': 1})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['probed'] == 2
    assert payload['healthy'] + payload['failed'] == 2
    assert {r['name'] for r in payload['results']} == {'railway-direct', 'cloudflare-01'}
    for node in payload['nodes']:
        assert node['latency_ms'] is not None  # measured, or -1 for a failed probe
    failed = client.get('/api/logs', headers=h()).json()
    assert any(item['action'] == 'nodes.ping' for item in failed)
    _seed_nodes()  # restore the fixture for the tests that follow


def test_worker_edge_paths_match_the_published_transports():
    """The Worker's path table must equal the paths the edge and the panel publish.

    A path the panel hands out but the Worker 404s is a Cloudflare node that only
    works on the Railway origin - the exact "the Worker is broken" report.
    """
    import os
    import re

    from app.subscriptions import transports as tp

    path = os.path.join(os.path.dirname(__file__), '..', 'cloudflare-worker', 'worker.js')
    with open(path, encoding='utf-8') as handle:
        source = handle.read()
    block = source.split('const EDGE_PATHS = [', 1)[1].split('];', 1)[0]
    advertised = set(re.findall(r"'([^']+)'", block))
    assert advertised == set(tp.edge_paths()) | {'/ws'}
    # Both the FastAPI edge and the Worker are generated from the same profile
    # table, so every published path is proxied on both sides.
    assert set(tp.edge_paths()) <= advertised


def test_worker_code_is_prefilled_and_downloadable():
    data = client.get('/api/cloudflare/worker-code', headers=h()).json()
    assert data['filename'] == 'nexus-worker.js'
    assert data['steps']
    assert 'NEXUS_ORIGIN' in data['code']
    assert 'const ORIGIN_FALLBACK = "";' not in data['code']  # prefilled, paste-ready
    assert data['origin'] in data['code']
    assert '_worker_source' not in data['code']
    download = client.get('/api/cloudflare/worker-download', headers=h())
    assert download.status_code == 200
    assert 'attachment' in download.headers['content-disposition']
    assert 'nexus-worker.js' in download.headers['content-disposition']
    # Credentials come from the cookie session, so only a fresh client is anonymous.
    assert TestClient(app).get('/api/cloudflare/worker-code').status_code == 401


def test_client_download_links_can_be_overridden_from_the_panel():
    saved = client.post('/api/settings/clients', headers=h(), json={'bettbox': 'https://example.com/bettbox.apk'})
    assert saved.status_code == 200, saved.text
    bettbox = [c for c in saved.json()['clients'] if c['id'] == 'bettbox'][0]
    assert bettbox['download'] == 'https://example.com/bettbox.apk'
    # The panel settings payload (which drives the editor) carries it as well.
    panel = {c['id']: c['download'] for c in client.get('/api/settings', headers=h()).json()['clients']}
    assert panel['bettbox'] == 'https://example.com/bettbox.apk'
    assert client.post('/api/settings/clients', headers=h(), json={'bettbox': 'not-a-url'}).status_code == 400
    assert client.post('/api/settings/clients', headers=h(), json={'ghost': 'https://example.com'}).status_code == 400
    assert client.post('/api/settings/clients', headers=h(), json={'reset': True}).status_code == 200
    defaults = {c['id']: c['download'] for c in client.get('/api/clients', headers=h()).json()['clients']}
    assert defaults['bettbox'] != 'https://example.com/bettbox.apk'


def test_public_status_window_lists_links_clients_and_nodes():
    _seed_nodes()
    execute('DELETE FROM users')
    user = client.post('/api/users', headers=h(), json={'username': 'portaluser'}).json()
    anonymous = TestClient(app)

    page = anonymous.get(f"/portal/{user['uuid']}")
    assert page.status_code == 200
    assert 'portaluser' in page.text and 'NEXUS' in page.text
    assert '/sub/' in page.text
    for name in ('Bettbox', 'NekoBoxPlus', 'Exclusive', 'v2rayNG'):
        assert name in page.text

    data = anonymous.get(f"/portal/{user['uuid']}/json").json()
    assert data['username'] == 'portaluser'
    assert data['token'] == user['uuid']
    assert {c['id'] for c in data['clients']} >= {'bettbox', 'exclusive', 'nekoboxplus'}
    assert data['nodes_total'] == len(data['nodes']) == 2
    assert data['portal_url'].endswith('/portal/' + user['uuid'])

    # Username lookup works too, and the legacy admin URL renders the same window.
    assert anonymous.get(f"/portal/{user['username']}").status_code == 200
    legacy = anonymous.get('/status/portaluser')
    assert legacy.status_code == 200 and 'portaluser' in legacy.text
    assert anonymous.get('/portal/ghost-token').status_code == 404
    assert anonymous.get('/portal/ghost-token/json').status_code == 404


def test_user_links_expose_portal_and_client_targets():
    _seed_nodes()
    execute('DELETE FROM users')
    user = client.post('/api/users', headers=h(), json={'username': 'linkportal'}).json()
    data = client.get('/api/users/linkportal/links', headers=h()).json()
    assert data['portal_url'].endswith('/portal/' + user['uuid'])
    assert data['smart_url'].endswith('target=auto')
    assert {c['id'] for c in data['clients']} >= {'bettbox', 'exclusive', 'nekoboxplus', 'v2rayng'}
    assert {s['target'] for s in data['subscriptions']} >= {'bettbox', 'exclusive', 'nekoboxplus'}
    for item in data['nodes']:
        assert {c['id'] for c in item['clients']} >= {'bettbox', 'nekoboxplus'}
        assert 'node=' + item['name'] in item['clients'][0]['url']
        assert 'target=all' in item['subscription_all'] and 'node=' + item['name'] in item['subscription_all']
        assert item['subscription_all'] != item['subscription']


def test_every_transport_path_is_bridged_to_its_xray_listener():
    """Each published transport has a WebSocket route on the edge, wired to the
    local Xray listener that speaks that protocol.

    A stub listener stands in for Xray: it echoes the payload reversed, so a
    route pointed at the wrong port (or a missing route) fails here instead of
    in a user's client.
    """
    import asyncio
    import threading

    import websockets

    from app.main import EDGE_ROUTES
    from app.subscriptions import transports as tp

    assert EDGE_ROUTES == {p['path']: getattr(cfg, p['port_setting']) for p in tp.EDGE_PROFILES} | {
        '/ws/warp': cfg.xray_warp_port}

    def serve(port, ready, stop):
        async def echo(connection):
            async for message in connection:
                data = message if isinstance(message, bytes) else message.encode()
                await connection.send(data[::-1])

        async def run():
            async with websockets.serve(echo, '127.0.0.1', port):
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(0.05)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run())

    for path, port in EDGE_ROUTES.items():
        ready, stop = threading.Event(), threading.Event()
        thread = threading.Thread(target=serve, args=(port, ready, stop), daemon=True)
        thread.start()
        assert ready.wait(5), f'stub listener for {path} did not start'
        try:
            with client.websocket_connect(path) as ws:
                ws.send_bytes(b'nexus')
                assert ws.receive_bytes() == b'suxen', path  # b'nexus'[::-1]
        finally:
            stop.set()
            thread.join(5)
