"""Node scope + the live guide.

A multi-location install publishes far more nodes than one user should see, and
"which nodes do I get" is now an explicit, stored choice — a **scope**:

* ``all``     — every node (the default, so nothing changes for existing users);
* ``multi``   — only the multi-location (edge) nodes;
* ``origin``  — only this server's own relay;
* ``cc:us``   — only one country (several may be combined).

These tests lock the whole path, because a scope that is accepted but not applied
(or applied in one format only) is worse than no scope at all: it looks right in
the panel and hands the user a node the admin explicitly excluded.

The live guide is the same kind of contract: every step it reports must come from
real state, so a deployment that has a user must not be told to create one.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-scope-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-scope-test.db')

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.settings_store import store
from app.db import execute, init_db
from app.main import _setting, app
from app.nodes import ensure as ensure_nodes, upsert
from app.subscriptions import scope as scopes
from app.subscriptions.generator import active_nodes, render
from app.users.service import create_user, get_user
from app.core.models import UserCreate

init_db()
ensure_nodes()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or settings.admin_password}


ORIGIN = 'railway-origin.test'
DE = 'de-edge-01'
US = 'us-edge-01'


def _seed():
    """One origin node plus two locations, each with its own measured health."""
    execute('DELETE FROM nodes')
    upsert(ORIGIN, 'railway', 'railway.example.com', 443, True, 'railway.example.com',
           'railway.example.com', 'railway', {})
    upsert(DE, 'cloudflare', '104.16.1.1', 443, True, 'worker.example.workers.dev',
           'worker.example.workers.dev', 'cloudflare', {'location': 'de', 'provider': 'cloudflare'})
    upsert(US, 'edge', 'cdn.us.example.com', 443, True, 'cdn.us.example.com',
           'cdn.us.example.com', 'domain', {'location': 'us', 'provider': 'domain'})
    execute(f"UPDATE nodes SET latency_ms=30.0 WHERE name='{DE}'")
    execute(f"UPDATE nodes SET latency_ms=40.0 WHERE name='{US}'")
    execute(f"UPDATE nodes SET latency_ms=20.0 WHERE name='{ORIGIN}'")


def _user(name='scope-user', scope=None, **extra):
    execute('DELETE FROM users WHERE username=?', (name,))
    fields = {'protocol': 'vless', 'limit_gb': None, 'expiry_days': None}
    fields.update(extra)
    return create_user(UserCreate(username=name, node_scope=scope, **fields))


@pytest.fixture(autouse=True)
def _fixture():
    _seed()
    yield
    execute('DELETE FROM users')


# ------------------------------------------------------------------- the model
def test_scope_spellings_resolve_to_one_canonical_value():
    """Every spelling an admin (or an imported config) may use lands on one id."""
    for value in ('us', 'US', 'usa', 'آمریکا', '🇺🇸', 'cc:us'):
        assert scopes.normalize(value) == 'cc:us'
    for value in ('multi', 'edge', 'locations', 'mولتی'.replace('م', 'م')):
        assert scopes.normalize(value) in ('multi', 'all')
    assert scopes.normalize('') == 'all'
    assert scopes.normalize(None) == 'all'
    # An unknown word must widen to the full catalog, never publish an empty one.
    assert scopes.normalize('some-nonsense') == 'all'
    assert scopes.normalize('multi') == 'multi'
    assert scopes.normalize('origin') == 'origin'
    assert scopes.normalize('cc:us,de') == 'cc:us,cc:de'


def test_every_node_belongs_to_exactly_one_of_origin_or_multi():
    nodes = active_nodes()
    origin = scopes.filter_nodes(nodes, 'origin')
    multi = scopes.filter_nodes(nodes, 'multi')
    assert {n['name'] for n in origin} == {ORIGIN}
    assert {n['name'] for n in multi} == {DE, US}
    assert len(origin) + len(multi) == len(nodes)


def test_country_scope_matches_only_that_country():
    nodes = active_nodes()
    assert {n['name'] for n in scopes.filter_nodes(nodes, 'us')} == {US}
    assert {n['name'] for n in scopes.filter_nodes(nodes, 'cc:de')} == {DE}
    # A country nobody publishes is honest: nothing, not everything.
    assert scopes.filter_nodes(nodes, 'jp') == []
    assert scopes.normalize('all') == 'all' and len(scopes.filter_nodes(nodes, 'all')) == 3


def test_render_is_scoped_in_every_format():
    """Line, Base64 and sing-box subscriptions must carry the same node set."""
    user = _user(scope='multi')
    for target in ('vless', 'base64', 'singbox'):
        body = render(user, 'https://panel.example.com', target)
        assert ORIGIN not in body, target
        if target == 'base64':
            body = base64.b64decode(body).decode()
        assert DE in body or '104.16.1.1' in body
        assert US in body or 'cdn.us.example.com' in body


# --------------------------------------------------------------- the endpoints
def test_subscription_url_honours_the_scope():
    user = _user(scope='origin')
    response = client.get(f"/sub/{user['uuid']}")
    assert response.status_code == 200
    assert response.headers['x-nexus-scope'] == 'origin'
    assert 'railway.example.com' in response.text
    assert 'cdn.us.example.com' not in response.text

    # ``?scope=`` overrides the stored choice for that one link.
    only_us = client.get(f"/sub/{user['uuid']}?scope=us")
    assert only_us.status_code == 200
    assert only_us.headers['x-nexus-scope'] == 'cc:us'
    assert 'cdn.us.example.com' in only_us.text
    assert 'railway.example.com' not in only_us.text


def test_a_scope_with_no_nodes_explains_itself():
    user = _user(scope='all')
    empty = client.get(f"/sub/{user['uuid']}?scope=jp")
    assert empty.status_code == 404
    assert 'ژاپن' in empty.json()['detail'] or 'no' in empty.json()['detail']


def test_feature_feed_route_accepts_the_scope():
    user = _user(scope='all')
    response = client.get(f"/feed/{user['uuid']}?scope=multi")
    assert response.status_code == 200
    assert 'railway.example.com' not in response.text


def test_user_payload_and_update_carry_the_scope():
    user = _user(scope='multi')
    listed = [item for item in client.get('/api/users', headers=h()).json()
              if item['username'] == user['username']][0]
    assert listed['node_scope'] == 'multi'
    assert 'مولتی‌لوکیشن' in listed['node_scope_label']

    updated = client.put(f"/api/users/{user['username']}", headers=h(),
                         json={'node_scope': 'usa'}).json()
    assert updated['node_scope'] == 'cc:us'
    assert 'آمریکا' in updated['node_scope_label']
    assert json.loads(get_user(user['username'])['metadata'])['node_scope'] == 'cc:us'

    # An empty value means "no narrowing", and it is removed rather than stored.
    cleared = client.put(f"/api/users/{user['username']}", headers=h(),
                         json={'node_scope': ''}).json()
    assert cleared['node_scope'] == 'all'


def test_quick_create_applies_the_mode_scope():
    execute('DELETE FROM users')
    created = client.post('/api/users/quick', headers=h(),
                          json={'preset': 'multi-location'}).json()
    assert created['user']['node_scope'] == 'multi'
    # The response is ready to hand over: the scope and its alternatives travel
    # with the links, so the admin never has to build ``?scope=`` by hand.
    assert created['node_scope_label']
    assert {item['id'] for item in created['scopes']} >= {'all', 'multi', 'origin', 'cc:de', 'cc:us'}
    assert any(item['current'] and item['id'] == 'multi' for item in created['scopes'])
    assert any(item['url'].endswith('&scope=cc:us') for item in created['scopes'])
    assert created['scope'] if 'scope' in created else True

    # An explicit scope in the request wins over the mode's own default.
    other = client.post('/api/users/quick', headers=h(),
                        json={'preset': 'iran-fast', 'scope': 'origin'}).json()
    assert other['user']['node_scope'] == 'origin'


def test_scopes_endpoint_answers_for_one_user():
    user = _user(scope='multi')
    payload = client.get('/api/scopes', headers=h(), params={'username': user['username']}).json()
    assert payload['scope'] == 'multi'
    assert payload['catalog_total'] == 3
    assert payload['locations'] == ['de', 'us']
    counts = {item['id']: item['count'] for item in payload['options']}
    assert counts == {'all': 3, 'multi': 2, 'origin': 1, 'cc:de': 1, 'cc:us': 1}
    assert all(item['url'].startswith('https://') or item['url'].startswith('http') for item in payload['options'])
    assert [item['id'] for item in payload['options'] if item['current']] == ['multi']


def test_customization_and_presets_expose_the_scope_default():
    saved = client.post('/api/customization', headers=h(), json={'default_scope': 'multi'}).json()
    assert saved['customization']['default_scope'] == 'multi'
    assert saved['customization']['default_scope'] in {item['id'] for item in saved['customization']['scopes']}
    presets = client.get('/api/presets', headers=h()).json()
    assert presets['defaults']['node_scope'] == 'multi'
    assert presets['scopes'] and presets['scope'] == 'multi'
    modes = [item['id'] for item in presets['modes']]
    assert 'multi-location' in modes and 'origin-only' in modes
    client.post('/api/customization', headers=h(), json={'default_scope': 'all'})


# ------------------------------------------------------------------ live guide
def test_guide_reports_real_state_and_a_next_step():
    execute('DELETE FROM users')
    store.set('cloudflare_worker_url', '')
    state = client.get('/api/guide', headers=h()).json()
    steps = {item['id']: item for item in state['steps']}
    assert set(steps) == {'worker', 'locations', 'nodes', 'ping', 'users', 'scope', 'share', 'brand'}
    assert steps['nodes']['done'] is True          # nodes were seeded
    assert steps['ping']['done'] is True           # and all three carry a latency
    assert steps['users']['done'] is False         # no user exists yet
    assert steps['worker']['done'] is False        # nor a Worker URL
    assert state['next'] == 'worker'               # the first thing still missing
    assert state['tips']['users']['items']
    assert state['catalog_total'] == 3

    # The guide advances by itself as the deployment changes: no checklist state
    # is stored anywhere, every answer is read from the settings and the catalog.
    store.set('cloudflare_worker_url', 'https://edge.example.workers.dev')
    advanced = client.get('/api/guide', headers=h()).json()
    assert {item['id']: item['done'] for item in advanced['steps']}['worker'] is True
    assert advanced['next'] == 'locations'

    client.post('/api/users/quick', headers=h(), json={'preset': 'multi-location', 'scope': 'multi'})
    after = client.get('/api/guide', headers=h()).json()
    steps = {item['id']: item for item in after['steps']}
    assert steps['users']['done'] is True
    assert steps['scope']['done'] is True          # a scoped user now exists
    assert steps['share']['done'] is True
    assert '/sub/' in after['links']['smart'] and after['links']['username']
    assert after['score'] > advanced['score']


def test_guide_is_authenticated():
    assert client.get('/api/guide').status_code in (401, 403)


# ------------------------------------------------------------ the compact shell
def test_panel_ships_the_grouped_compact_shell():
    """The panel is a short stack of collapsible rows, not one long scroll."""
    html = client.get('/', headers=h()).text
    # Grouped navigation + the live-guide drawer + quick-create modes and scope.
    for anchor in ('id="navGroups"', 'id="guideDrawer"', 'id="guideBody"', 'id="guidePill"',
                   'id="quickModes"', 'id="quickScopes"', 'id="czScopes"'):
        assert anchor in html, anchor
    # Every heavy panel of the newer tabs is folded into a collapsible card.
    assert html.count('data-acc=') >= 12
    # The old blue/violet palette is gone from the shipped theme: the stylesheet
    # carries the brand lime and nothing from the previous accent.
    css = client.get('/static/app.css').text
    assert '--accent:#c9f24c' in css and '--accent-2:#5fce62' in css
    assert '#5ad1ff' not in css and '#8b7bff' not in css
    assert '#060a05' in client.get('/login').text  # theme-color of the login page


def test_portal_shows_the_scope_picker_and_the_new_theme():
    user = _user(scope='multi')
    html = client.get(f"/portal/{user['uuid']}").text
    assert 'محدودهٔ نودها' in html
    assert 'فقط نودهای مولتی‌لوکیشن' in html
    # Every alternative scope is one tap away, including each country.
    assert 'scope=cc:de' in html and 'scope=cc:us' in html and 'scope=origin' in html
    # A scope with no nodes at all is explained instead of rendering an empty list.
    empty = _user('scope-empty', scope='all')
    page = client.get(f"/portal/{empty['uuid']}").text
    assert 'محدودهٔ نودها' in page
    assert '#5ad1ff' not in html
