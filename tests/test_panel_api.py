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
    'js/views/dashboard.js', 'js/views/nodes.js', 'js/views/users.js', 'js/views/system.js', 'js/app.js',
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
    # A fresh Railway deployment that only read the database would publish an
    # empty catalog; opening the panel must create the origin node.
    execute('DELETE FROM nodes')
    assert client.get('/api/metrics', headers=h()).json()['totals']['nodes'] == 0
    page = client.get('/', headers=h())
    assert page.status_code == 200
    nodes = client.get('/api/nodes', headers=h()).json()
    assert any(node['name'] == 'railway-direct' and node['kind'] == 'railway' for node in nodes)
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
    bettbox = [c for c in data['clients'] if c['id'] == 'bettbox'][0]
    assert bettbox['targets'][0] == 'base64'
    nekobox = [c for c in data['clients'] if c['id'] == 'nekoboxplus'][0]
    assert nekobox['targets'][0] == 'singbox'


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
    # And the generated subscription really renders (base64 for bettbox).
    sub = client.get(f"/sub/{user['uuid']}?target=bettbox")
    assert sub.status_code == 200
    assert 'vless://' in base64.b64decode(sub.text).decode()


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

    bettbox = client.get(f'/sub/{uuid_value}?target=bettbox')
    assert bettbox.status_code == 200
    assert 'vless://' in base64.b64decode(bettbox.text).decode()
    assert bettbox.headers['x-nexus-target'] == 'base64'

    exclusive = client.get(f'/sub/{uuid_value}/cloudflare-01?target=exclusive')
    assert exclusive.status_code == 200
    assert '104.16.1.1' in base64.b64decode(exclusive.text).decode()

    nekobox = client.get(f'/sub/{uuid_value}?target=nekoboxplus')
    assert len(json.loads(nekobox.text)['outbounds']) == 2 * len(transport_ids())

    assert client.get(f'/sub/{uuid_value}?target=unknown-client').status_code == 400


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
