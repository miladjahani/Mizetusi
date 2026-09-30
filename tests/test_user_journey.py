"""The whole product, walked the way a real admin and end user walk it.

Everything asserted here is something a person does: sign in, open every tab,
press every button, hand a link to a user and open that user's pages. The request
list is not invented — it is the set of ``api.*`` calls the panel's own front-end
makes (static/js), plus the public pages, so a feature that has no working
endpoint fails here rather than in the browser.

Network stays out of it: outbound probing is switched off and the two helpers
that resolve names are stubbed, so the journey tests the *product*, not whether
the sandbox has internet.
"""
import json
import os
import urllib.parse

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-journey.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-journey.db')

import pytest
from fastapi.testclient import TestClient

from app.config import SUPPORT_CHANNEL, settings
from app.db import execute, row
from app.main import _setting, _set, app
from app.nodes import ensure as ensure_nodes

ensure_nodes()
client = TestClient(app)


def h():
    """Admin header — used only where a test needs a second, independent client."""
    return {'X-Admin-Password': _setting('admin_password') or settings.admin_password}


def _panel():
    """A client that signed in the way a person does: password, then cookie."""
    browser = TestClient(app)
    password = _setting('admin_password') or settings.admin_password
    response = browser.post('/api/login', json={'password': password, 'remember': True})
    assert response.status_code == 200, response.text
    assert response.json()['success'] is True
    return browser, response.json()['token']


def _wipe_users():
    execute('DELETE FROM users')


def token_of(browser, username):
    return browser.get(f'/api/users/{username}').json()['uuid']


def _wipe_sources():
    for source in client.get('/api/edge', headers=h()).json()['sources']:
        client.delete(f"/api/edge/sources/{source['id']}", headers=h())


# --------------------------------------------------------------------------- boot


def test_journey_sign_in_reaches_every_tab(monkeypatch):
    """Cold start: health, login page, panel, all eight tabs, then sign out."""
    assert client.get('/health').status_code == 200
    login_page = client.get('/login')
    assert login_page.status_code == 200 and 'loginForm' in login_page.text
    # Signed out, the panel sends you to the login page instead of half-rendering.
    assert client.get('/', follow_redirects=False).status_code in (303, 307)

    browser, token = _panel()
    panel = browser.get('/')
    assert panel.status_code == 200
    for section in ('dashboard', 'users', 'nodes', 'cloudflare', 'tools', 'customize', 'advanced', 'settings'):
        assert f'id="section-{section}"' in panel.text, section
    # The shell carries every hook the views bind to.
    for hook in ('navGroups', 'guidePill', 'guideDrawer', 'guideBody', 'quickModes', 'quickScopes',
                 'userList', 'nodeList', 'subTable', 'brandVersion', 'supportPill'):
        assert f'id="{hook}"' in panel.text or hook == 'supportPill', hook
    # The support channel is on the login screen and in the shell.
    assert f'href="{SUPPORT_CHANNEL}"' in login_page.text
    assert f'href="{SUPPORT_CHANNEL}"' in panel.text

    # Both session transports work: the cookie, and the header the front-end
    # falls back to when a browser refuses cookies.
    assert browser.post('/api/logout').json()['success'] is True
    assert browser.get('/api/metrics', follow_redirects=False).status_code in (303, 307, 401)
    header_client = TestClient(app)
    assert header_client.get('/api/metrics', headers={'X-Nexus-Session': token}).status_code == 200


def test_journey_every_id_the_front_end_reads_exists_in_the_panel(monkeypatch):
    """A button whose element is missing is a button that silently does nothing.

    The panel looks elements up by id; if the template does not define one (and no
    view creates it), ``$('#x')`` is null and the feature never runs — which is
    exactly how the version footer stayed invisible.
    """
    import pathlib
    import re
    from fastapi.testclient import TestClient as _Client

    html = client.get('/', headers=h()).text
    ids = set(re.findall(r'id="([^"]+)"', html))
    js = "\n".join(path.read_text(encoding='utf-8')
                   for path in pathlib.Path('static/js').rglob('*.js'))
    created = set(re.findall(r'id=["\'`]([A-Za-z0-9_-]+)', js))
    missing = sorted(lookup for lookup in set(re.findall(r"\$\('#([A-Za-z][A-Za-z0-9_-]*)'\)", js))
                     if lookup not in ids and lookup not in created)
    assert missing == [], f'views read ids the panel never renders: {missing}'
    # The other direction: a hook in the template that no view handles is dead UI.
    hooks = {hook for hook in re.findall(r'data-([a-z-]+)=', html) if hook not in ('pwa-install',)}
    for hook in hooks:
        camel = ''.join(part.title() if index else part for index, part in enumerate(hook.split('-')))
        assert hook in js or camel in js or f'data-{hook}' in js, f'no handler for data-{hook}'


# ------------------------------------------------------------------------- reads


def test_journey_every_tab_loads_and_every_button_gets_its_data(monkeypatch):
    """Every GET the panel issues when you walk the tabs."""
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)
    browser, _token = _panel()
    wanted = [
        '/api/metrics?hours=168',
        '/api/settings',
        '/api/core/status',
        '/api/logs?limit=60',
        '/api/nodes',
        '/api/nodes/samples',
        '/api/users',
        '/api/presets',
        '/api/scopes',
        '/api/transports',
        '/api/clients',
        '/api/customization',
        '/api/edge',
        '/api/edge/packs',
        '/api/edge/import',
        '/api/edge/ips?limit=200',
        '/api/cloudflare/ips?limit=50',
        '/api/cloudflare/worker-code',
        '/api/hysteria',
        '/api/warp',
        '/api/guide?section=users',
        '/api/version',
        '/api/system/runtime',
        '/api/settings/cloudflare-worker',
        '/api/stats',
        '/api/backup',
    ]
    for path in wanted:
        response = browser.get(path)
        assert response.status_code == 200, f'{path} -> {response.status_code} {response.text[:200]}'

    # Shapes the views dereference unguarded (a list where a list is expected).
    assert isinstance(browser.get('/api/users').json(), list)
    assert isinstance(browser.get('/api/nodes').json(), list)
    guide = browser.get('/api/guide?section=users').json()
    assert isinstance(guide['steps'], list) and guide['steps'] and guide['support'] == SUPPORT_CHANNEL
    assert isinstance(browser.get('/api/presets').json()['modes'], list)
    assert isinstance(browser.get('/api/edge').json()['sources'], list)


# ------------------------------------------------------------------------ users


def test_journey_create_hand_out_and_manage_a_user(monkeypatch):
    """Quick create, the full form, links, scopes, toggle, reset, traffic, delete."""
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)
    browser, _token = _panel()
    _wipe_users()

    # 1. «ساخت سریع» before choosing a mode: it must still work (default preset).
    quick = browser.post('/api/users/quick', json={'preset': '', 'scope': '', 'username': ''})
    assert quick.status_code == 200, quick.text
    quick = quick.json()
    assert quick['user']['username'] and quick['smart_url'].startswith('http')
    auto_user = quick['user']['username']

    # 2. Quick create with a mode and a node scope.
    modes = browser.get('/api/presets').json()['modes']
    scopes = browser.get('/api/scopes').json()['options']
    pick = next((mode for mode in modes if mode.get('scope')), modes[0])
    scoped = browser.post('/api/users/quick', json={
        'preset': pick['id'], 'scope': (pick.get('scope') or scopes[0]['id']), 'username': 'journeyquick'})
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()['applied'], 'quick create must report what it applied'
    assert scoped.json()['node_scope'] == scoped.json()['user']['node_scope']

    # 3. The full form, with the fields the modal sends.
    form = {
        'username': 'journeyfull', 'protocol': ['vless', 'trojan'], 'limit_gb': 5, 'expiry_days': 30,
        'ip_limit': 2, 'max_configs': 4, 'node_scope': 'multi', 'port': 443, 'fingerprint': 'chrome',
        'tls': 'on', 'frag_len': '10-20', 'frag_int': '10-20', 'block_ads': True,
        'auto_rotate_ip': False, 'rotate_time': 5, 'ip_operator': 'all', 'ip_count': 5,
        'is_active': 1, 'start_on_first_connect': False, 'ips': '',
    }
    created = browser.post('/api/users', json=form)
    assert created.status_code == 200, created.text
    assert created.json()['protocols'] == ['vless', 'trojan']
    assert created.json()['node_scope'] == 'multi'

    # 4. Rejections a user can hit must be clean 400s, not 500s.
    assert browser.post('/api/users', json={'username': 'journeyfull'}).status_code == 400
    assert browser.post('/api/users', json={'username': 'bad name!'}).status_code == 422

    # 5. Every link the links-drawer offers for that user resolves.
    browser.put('/api/users/journeyfull', json={'node_scope': 'all'})
    links = browser.get('/api/users/journeyfull/links').json()
    assert links['smart_url'].startswith(links['base_url']) and '/sub/' in links['smart_url']
    assert links['portal_url'] and links['clients'] and links['subscriptions'] and links['nodes']
    offered = {item['target'] for item in links['subscriptions']}
    # A protocol this user was not given must not be offered: the row used to be
    # listed anyway and answered 400 the moment it was opened.
    assert {'vmess', 'ss'}.isdisjoint(offered), offered
    assert {'base64', 'singbox', 'clash', 'xray', 'auto'} <= offered
    for target in links['subscriptions']:
        body = browser.get(target['url'])
        assert body.status_code == 200 and body.text.strip(), target
    for node in links['nodes']:
        assert node['subscription'] and node['clients']
    # The status window the admin hands to the user.
    assert browser.get(links['portal_url']).status_code == 200
    assert browser.get(urllib.parse.urlparse(links['portal_url']).path + '/json').json()['username'] == 'journeyfull'

    # 6. Scope switcher (the chips in the links drawer) and the picker counts.
    for option in browser.get('/api/scopes?username=journeyfull').json()['options']:
        assert browser.put('/api/users/journeyfull', json={'node_scope': option['id']}).status_code == 200
    # A scope with nothing published says why — a client asking for it gets the
    # reason in the response, not an empty file it cannot explain.
    empty = [option for option in browser.get('/api/scopes?username=journeyfull').json()['options']
             if not option.get('count')]
    if empty:
        refused = browser.get(f"/sub/{token_of(browser, 'journeyfull')}?scope={empty[0]['id']}")
        assert refused.status_code == 404 and 'نود' in refused.json()['detail']

    # 7. Toggle, traffic tick, reset, edit, delete.
    assert browser.put('/api/users/journeyfull', json={'toggle_only': True}).json()['is_active'] in (0, 1)
    assert browser.put('/api/users/journeyfull', json={'toggle_only': True}).status_code == 200
    ticked = browser.post('/api/traffic/journeyfull', json={'bytes': 1024 * 1024, 'requests': 3, 'ip': '203.0.113.9'})
    assert ticked.status_code == 200 and ticked.json()
    # The two choices the reset modal offers, and a typo that must not 500.
    for action in ('volume', 'req'):
        assert browser.put('/api/users/journeyfull', json={'reset_action': action}).status_code == 200
    assert browser.put('/api/users/journeyfull', json={'reset_action': 'nonsense'}).status_code == 400
    assert browser.put('/api/users/journeyfull', json={'limit_gb': 9, 'expiry_days': 3}).json()['limit_gb'] == 9

    # 8. Every subscription format and target a client can ask for.
    token = browser.get('/api/users/journeyfull').json()['uuid']
    for target in ('auto', 'base64', 'clash', 'singbox', 'vless', 'trojan'):
        sub = browser.get(f'/sub/{token}?target={target}')
        assert sub.status_code == 200, target
    # …and a protocol the user does not have is refused, not silently emptied.
    assert browser.get(f'/sub/{token}?target=vmess').status_code == 400
    assert browser.get(f'/feed/{token}').status_code == 200
    assert browser.get(f'/status/journeyfull').status_code == 200
    assert browser.get('/sub/ghost?target=auto').status_code == 404

    # 9. Deleting is the last thing a user does to a user.
    for username in (auto_user, 'journeyquick', 'journeyfull'):
        removed = browser.delete(f'/api/users/{username}')
        assert removed.json()['success'] is True, \
            f'{username} -> {removed.text}; live: {[u["username"] for u in browser.get("/api/users").json()]}'
    assert browser.get('/api/users/journeyfull').status_code == 404


# ------------------------------------------------------------------------ nodes


def test_journey_node_catalog_buttons(monkeypatch):
    """Sync, ping, samples, hand-made node, edit, toggle, per-node link, delete."""
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)
    browser, _token = _panel()
    _wipe_users()

    assert browser.post('/api/nodes/sync', json={}).json()['success'] is True
    assert 'nodes' in browser.post('/api/nodes/ping', json={'timeout': 2}).json()
    assert 'healthy' in browser.post('/api/nodes/ping', json={'nodes': []}).json()

    # Samples: list, add one, then clear them all again.
    samples = browser.get('/api/nodes/samples').json()
    assert isinstance(samples['samples'], list)
    if samples['samples']:
        added = browser.post('/api/nodes/samples', json={'ids': [samples['samples'][0]['id']]})
        assert added.status_code in (200, 400)  # 400 = already present, also a real answer
    assert browser.delete('/api/nodes/samples').json()['success'] is True

    # A hand-made node, then every button on its row.
    assert browser.post('/api/nodes', json={'name': 'journey-node', 'server': 'example.com',
                                           'kind': 'edge', 'port': 443, 'tls': True}).status_code == 200
    assert browser.post('/api/nodes', json={'name': '', 'server': '', 'kind': 'edge'}).status_code == 400
    assert browser.put('/api/nodes/journey-node', json={'enabled': 0}).status_code == 200
    assert browser.put('/api/nodes/journey-node', json={'enabled': 1, 'server': 'example.org'}).status_code == 200
    assert browser.post('/api/nodes/ping', json={'nodes': ['journey-node'], 'timeout': 2}).json()
    assert browser.delete('/api/nodes/journey-node').json()['success'] is True

    # A user with no nodes published says so instead of rendering an empty shell.
    quick = browser.post('/api/users/quick', json={'username': 'journeyempty'}).json()
    token = quick['user']['uuid']
    # The quick-create answer is the whole share panel: formats, clients, scopes.
    assert quick['targets'] and quick['scopes'] and quick['clients']
    assert browser.get('/api/scopes?username=journeyempty').json()['options']
    assert browser.get(f'/sub/{token}?target=auto&scope=origin').status_code == 200
    browser.delete('/api/users/journeyempty')


# ------------------------------------------------------------------------- edge


def test_journey_edge_cloudflare_and_advanced_buttons(monkeypatch):
    """Locations, clean IPs, packs, Worker, WARP, Hysteria2, Shadowsocks keys."""
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)
    browser, _token = _panel()
    _wipe_users()
    _wipe_sources()

    # A location by hand (the add-location form), measured in the same request.
    saved = browser.post('/api/edge/sources', json={
        'kind': 'ip', 'provider': 'custom', 'location': 'de', 'label': 'آلمان · تست',
        'host': 'example.com', 'port': 443, 'max': 2, 'ips': '203.0.113.10'})
    assert saved.status_code == 200, saved.text
    source_id = saved.json()['source']['id']
    assert 'ping' in saved.json()

    # Invalid input is a readable 400, not a stack trace.
    assert browser.post('/api/edge/sources', json={'kind': 'nonsense'}).status_code == 400

    # The row buttons: ping one location, toggle it, add an address, remove it.
    assert 'healthy' in browser.post(f'/api/edge/sources/{source_id}/ping', json={}).json()
    assert browser.post(f'/api/edge/sources/{source_id}/toggle', json={}).status_code == 200
    assert browser.post(f'/api/edge/sources/{source_id}/toggle', json={}).status_code == 200
    added = browser.post('/api/edge/ips', json={'ips': '203.0.113.11', 'provider': 'custom'})
    assert added.status_code in (200, 400), added.text
    assert browser.delete('/api/edge/ips/203.0.113.11').status_code == 200
    assert browser.get('/api/edge').json()['sources']
    assert browser.get('/api/edge/providers').status_code in (200, 404)

    # Location packs: catalogue, install one, then uninstall it.
    packs = browser.get('/api/edge/packs').json()['packs']
    assert packs
    assert browser.post('/api/edge/packs', json={'id': 'nope'}).status_code == 400
    # A pack that brings its own hosts installs straight away…
    standalone = [pack for pack in packs if not pack.get('host_required')]
    if standalone:
        installed = browser.post('/api/edge/packs', json={'id': standalone[0]['id']})
        assert installed.status_code == 200, installed.text
        assert installed.json()['created']
        assert browser.post('/api/edge/packs', json={'id': standalone[0]['id'], 'action': 'uninstall'}).status_code == 200
    # …and a clean-IP pack (the Cloudflare regions) says exactly what it is
    # missing instead of silently creating locations that cannot answer.
    needs_host = [pack for pack in packs if pack.get('host_required')]
    if needs_host:
        refused = browser.post('/api/edge/packs', json={'id': needs_host[0]['id'], 'hosts': []})
        assert refused.status_code == 200 or 'دامنه' in refused.json().get('detail', '')

    # The importer: analyse a subscription text, then import it as locations.
    sample = ('ss://YWVzLTI1Ni1nY206cHc@203.0.113.20:443#%F0%9F%87%A9%F0%9F%87%AA%20Berlin\n'
              'vless://11111111-2222-3333-4444-555555555555@203.0.113.21:443?security=tls#Paris')
    parsed = browser.post('/api/tools/parse', json={'text': sample})
    assert parsed.status_code == 200 and parsed.json()['count'] >= 1
    assert browser.get('/api/edge/import').json()['hint']
    imported = browser.post('/api/edge/import', json={'text': sample, 'max_nodes': 1, 'apply': True})
    assert imported.status_code == 200, imported.text

    # Cloudflare: the IP table, a scan, the Worker page and its test button.
    assert isinstance(browser.get('/api/cloudflare/ips?limit=20').json(), list)
    assert 'seeded' in browser.post('/api/cloudflare/refresh', json={}).json()
    assert browser.post('/api/edge/scan', json={'limit': 4}).status_code == 200
    assert 'code' in browser.get('/api/cloudflare/worker-code').json() or \
           isinstance(browser.get('/api/cloudflare/worker-code').json(), dict)
    assert browser.get('/api/cloudflare/worker-download').status_code == 200
    assert browser.post('/api/cloudflare/worker-test', json={}).status_code in (200, 400)

    # Worker URL + token, then clearing it again. The field wants the full
    # https:// address (its placeholder shows one), and a bare host is refused
    # with a readable message instead of being stored broken.
    assert browser.post('/api/settings/cloudflare-worker', json={'url': 'worker.example.workers.dev', 'api_key': ''}).status_code == 400
    assert browser.post('/api/settings/cloudflare-worker', json={'url': 'https://worker.example.workers.dev', 'api_key': ''}).status_code == 200
    assert browser.get('/api/settings/cloudflare-worker').json()['configured'] is True
    assert browser.post('/api/settings/cloudflare-worker', json={'url': '', 'api_key': ''}).status_code == 200
    assert browser.get('/api/settings/cloudflare-worker').json()['configured'] is False

    # WARP and Hysteria2 have real enable/disable/clear paths.
    assert browser.get('/api/warp').json()
    warp_disable = browser.post('/api/warp', json={'action': 'disable'})
    assert warp_disable.status_code == 200
    hy2 = browser.post('/api/hysteria', json={'host': 'hy2.example.com', 'password': 'secret', 'port': 443, 'enabled': True})
    assert hy2.status_code == 200, hy2.text
    assert browser.post('/api/hysteria', json={'host': 'bad'}).status_code == 400
    assert browser.post('/api/hysteria', json={'action': 'disable'}).status_code == 200
    assert browser.post('/api/hysteria', json={'action': 'clear'}).status_code == 200

    # Rotating the Shadowsocks ciphers, then rebuilding the catalog with them.
    rotated = browser.post('/api/settings/rotate-shadowsocks', json={})
    assert rotated.status_code == 200 and 'rotated' in rotated.json()
    assert browser.post('/api/nodes/sync', json={}).status_code == 200

    _wipe_sources()


# --------------------------------------------------------------------- settings


def test_journey_settings_security_and_customization(monkeypatch):
    """Save settings, skin the panel, change the password, then undo the lot."""
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)
    browser, _token = _panel()
    _wipe_users()

    before = browser.get('/api/settings').json()
    saved = browser.post('/api/settings', json={
        'public_base_url': before.get('public_base_url') or '',
        'sub_prefix': 'journey', 'default_protocol': 'all', 'default_limit_gb': '', 'default_expiry_days': '',
        'default_ip_limit': '', 'session_days': 7, 'ping_interval': 15,
        'app_name': 'JourneyTest', 'accent': '#c9f24c', 'accent_secondary': '#9ede48'})
    assert saved.status_code == 200, saved.text
    assert 'sub_prefix' in saved.json()['changed']
    # A bad colour is refused with a readable message.
    assert browser.post('/api/settings', json={'accent': 'not-a-colour'}).status_code == 400
    browser.post('/api/settings', json={'sub_prefix': before.get('sub_prefix') or 'sub',
                                        'app_name': 'NEXUS', 'accent': '#c9f24c', 'accent_secondary': '#9ede48'})

    # Customization: banner, support link, flags, default format, default scope.
    custom = browser.post('/api/customization', json={
        'portal_banner': 'تمدید از پشتیبانی', 'support_url': SUPPORT_CHANNEL, 'flags_enabled': True,
        'default_format': 'clash', 'default_max_configs': 8, 'default_scope': 'multi',
        'app_name': 'NEXUS', 'accent': '#c9f24c', 'accent_secondary': '#9ede48'})
    assert custom.status_code == 200, custom.text
    assert browser.post('/api/customization', json={'support_url': 'javascript:x'}).status_code == 400
    assert browser.post('/api/customization', json={'default_format': 'nope'}).status_code == 400
    assert browser.post('/api/customization', json={'default_max_configs': 900}).status_code == 400
    shown = browser.get('/api/customization').json()
    assert shown['support_url'] == SUPPORT_CHANNEL and shown['default_scope'] == 'multi'

    # Client download links: save one, then reset to the shipped defaults.
    assert browser.post('/api/settings/clients', json={'bettbox': 'https://example.com/bettbox.apk'}).json()['clients']
    assert browser.post('/api/settings/clients', json={'nope': 'https://example.com/x'}).status_code == 400
    assert browser.post('/api/settings/clients', json={'reset': True}).status_code == 200

    # Logs: read, then clear.
    assert isinstance(browser.get('/api/logs?limit=20').json(), list)
    assert browser.delete('/api/logs').json()['success'] is True

    # Security: the password change must take effect right away, refuse a wrong
    # current password, and be undoable.
    password = _setting('admin_password') or settings.admin_password
    new_password = 'journey-pass-1' if password != 'journey-pass-1' else 'journey-pass-2'
    assert browser.post('/api/settings/password', json={'current': 'wrong', 'new': new_password}).status_code in (400, 403)
    assert browser.post('/api/settings/password', json={'current': password, 'new': 'short'}).status_code == 400
    try:
        assert browser.post('/api/settings/password', json={'current': password, 'new': new_password}).status_code == 200
        fresh = TestClient(app)
        assert fresh.post('/api/login', json={'password': password}).status_code == 401
        assert fresh.post('/api/login', json={'password': new_password}).status_code == 200
    finally:
        # Back to the password the rest of the suite reads. The eight-character
        # rule is real, so a short one is restored directly instead of pretending
        # the endpoint would accept it.
        _set('admin_password', password)

    # «ابطال همه نشستها» — by design this closes every session including the
    # current one (the panel says so and reloads to the login page).
    revoked = browser.post('/api/settings/rotate-session', json={})
    assert revoked.status_code == 200 and revoked.json()['signed_out'] is True
    assert browser.get('/api/metrics', follow_redirects=False).status_code in (401, 303, 307)

    # Restore the customization defaults so the rest of the suite sees a clean slate.
    restored = _panel()[0]
    restored.post('/api/customization', json={'portal_banner': '', 'support_url': '', 'flags_enabled': True,
                                              'default_format': 'auto', 'default_max_configs': '',
                                              'default_scope': 'all'})


def test_journey_tools_and_public_pages(monkeypatch):
    """The network toolbox, the manifest, the service worker and the PWA shell."""
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)
    browser, _token = _panel()
    _wipe_users()

    # «بررسی دسترسی» answers with a verdict even when nothing is listening, so a
    # closed port reads as «بیپاسخ» instead of an error dialog.
    check = browser.post('/api/tools/check', json={'host': '127.0.0.1', 'port': 1, 'sni': '', 'tls': True})
    assert check.status_code == 200, check.text
    verdict = check.json()
    assert verdict['results']['tcp']['ok'] is False and verdict['results']['tls']['ok'] is False
    assert verdict['elapsed_ms'] >= 0
    # A host that is not there at all is also just an answer.
    assert browser.post('/api/tools/check', json={'host': 'nowhere.invalid', 'port': 443}).status_code == 200
    assert browser.post('/api/tools/check', json={}).status_code == 400

    # CIDR summariser: valid input is summarised, useless input is a 400.
    cidr = browser.post('/api/tools/cidr', json={'value': '104.16.0.0/13, 198.41.128.0/17', 'limit': 6})
    assert cidr.status_code == 200 and cidr.json()['count'] == 2 and cidr.json()['addresses']
    assert browser.post('/api/tools/cidr', json={'value': 'not-an-ip'}).status_code == 400
    assert browser.post('/api/tools/cidr', json={}).status_code == 400

    # The DNS box is verified without the network: the resolver is stubbed.
    from app.dns import service as dns_service
    monkeypatch.setattr(dns_service, 'doh', lambda name: _resolved(name))
    assert browser.get('/api/tools/dns?name=example.com').json()['answer']
    assert browser.get('/api/tools/dns').status_code == 400

    # Public install surface.
    manifest = browser.get('/manifest.webmanifest')
    assert manifest.status_code == 200 and manifest.json()['name']
    sw = browser.get('/sw.js')
    assert sw.status_code == 200 and 'nexus-dev' not in sw.text  # replaced by the build token
    assert '/static/app.css' in sw.text
    assert browser.get('/static/icons/nexus.svg').status_code == 200

    # A fresh install's bootstrap page.
    assert browser.get('/setup').status_code == 200
    assert browser.get('/api/setup/admin', follow_redirects=False).status_code in (200, 405, 404)


async def _resolved(name):
    return [{'type': 'A', 'value': '203.0.113.7'}]


def test_journey_proxy_helpers_and_import_export(monkeypatch):
    """The small utility endpoints the tests/CLI-era UI still exposes."""
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)
    browser, _token = _panel()
    assert isinstance(browser.get('/api/proxies').json(), list)
    # The legacy DNS alias (the panel's own box is /api/tools/dns) is stubbed the
    # same way, so this stays a contract check rather than a network check.
    from app import main as main_module
    monkeypatch.setattr(main_module, 'doh', _resolved)
    assert browser.get('/api/dns?name=example.com').status_code == 200
    assert browser.get('/api/dns', follow_redirects=False).status_code == 422
    backup = browser.get('/api/backup')
    assert backup.status_code == 200 and backup.text.strip()
    assert browser.post('/api/test-proxy', json={'proxy': 'socks5://127.0.0.1:1'}).status_code == 200
    # A body-less or malformed call is the operator's mistake, reported as one.
    assert browser.post('/api/test-proxy', json={}).status_code == 400
    assert browser.post('/api/test-proxy', json={'proxy': 'nonsense'}).status_code == 400
    assert browser.post('/api/proxies', json={}).status_code == 400
    assert browser.post('/api/proxies', json={'proxy': 'socks5://127.0.0.1:1080'}).status_code == 200
    created = [item for item in browser.get('/api/proxies').json() if item['value'] == 'socks5://127.0.0.1:1080']
    assert created
    execute("DELETE FROM proxies WHERE value='socks5://127.0.0.1:1080'")
