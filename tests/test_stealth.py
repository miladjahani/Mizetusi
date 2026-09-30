"""The low-fingerprint half: what the service stops announcing about itself.

A panel is a fingerprint even when only its owner knows the URL, and the cheap
signals are the ones that cost nothing to remove: a ``Server: uvicorn`` header
on every response, an ``/openapi.json`` that is a map of the whole product, and
a ``/health`` that names its service.

So the tests here are the four ways a deployment can talk about itself by
accident, and the one thing that must keep working while it goes quiet: the
platform's own health check (a loopback caller) still gets the detailed body, or
a container that hides too much ends up hiding from its own operator.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-stealth-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-stealth-test.db')

from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.core import stealth
from app.db import execute, init_db
from app.main import _setting, app

init_db()
client = TestClient(app)
# The same app seen from *inside* the container, which is how a Docker/Render
# health check reaches it: a loopback peer cannot be a scanner outside.
inside = TestClient(app, client=('127.0.0.1', 40000))

KEYS = (stealth.HEADERS, stealth.DOCS, stealth.HEALTH, stealth.SUB_HEADERS,
        stealth.SERVER_HEADER)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


def setup_function(_item):
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))


def teardown_function(_item):
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))


# ----------------------------------------------------------------- the headers
def test_no_response_names_the_stack():
    """``Server: uvicorn`` is the cheapest fingerprint there is, and it is on
    every single response by default — the panel, a static asset, a redirect."""
    for path in ('/health', '/login', '/static/app.css', '/api/stats'):
        response = client.get(path, headers=h() if path == '/api/stats' else {})
        assert 'server' not in response.headers, path
        assert 'x-powered-by' not in response.headers, path
        assert response.headers['x-content-type-options'] == 'nosniff'   # still hardened


def test_an_admin_can_put_a_plausible_server_value_back():
    """Silence is the default, not the only option: a name that matches what a
    CDN in front of this host would send is a better answer than none."""
    assert client.get('/health').headers.get('server') is None
    saved = client.post('/api/stealth', headers=h(), json={stealth.SERVER_HEADER: 'cloudflare'})
    assert saved.status_code == 200
    assert client.get('/health').headers['server'] == 'cloudflare'
    client.post('/api/stealth', headers=h(), json={stealth.SERVER_HEADER: ''})
    assert client.get('/health').headers.get('server') is None


def test_a_header_value_that_could_split_the_response_is_refused():
    """One line, short. A newline here is a response-splitting primitive, not a
    cosmetic problem."""
    bad = client.post('/api/stealth', headers=h(), json={stealth.SERVER_HEADER: 'ok\r\nX-Injected: 1'})
    assert bad.status_code == 400
    assert client.get('/health').headers.get('server') is None


# ------------------------------------------------------------------- the schema
def test_the_api_schema_is_not_published_until_it_is_asked_for():
    """``/openapi.json`` names every route this product has — free
    reconnaissance for anything that can reach the address."""
    assert client.get('/openapi.json').status_code == 404
    assert client.get('/docs').status_code == 404
    # A scan that guesses the path learns nothing at all.
    assert 'nexus' not in client.get('/openapi.json').text.lower()

    client.post('/api/stealth', headers=h(), json={stealth.DOCS: '1'})
    document = client.get('/openapi.json')
    assert document.status_code == 200 and 'paths' in document.json()
    # Even switched on, the document does not carry the product's own name.
    assert document.json()['info']['title'] == 'API'
    assert client.get('/docs').status_code == 200


def test_turning_the_scrub_off_is_itself_a_decision_somebody_made():
    """The default is silence, and the switch is readable, so a deployment that
    suddenly announces itself is a switch somebody flipped — not a mystery."""
    body = client.get('/api/stealth', headers=h()).json()
    states = {item['key']: item['on'] for item in body['switches']}
    assert states[stealth.HEADERS] is True and states[stealth.DOCS] is False
    assert states[stealth.HEALTH] is True and states[stealth.SUB_HEADERS] is True
    client.post('/api/stealth', headers=h(), json={stealth.HEADERS: '0'})
    assert client.get('/api/stealth', headers=h()).json()['switches'][0]['on'] is False


# -------------------------------------------------------------------- the probe
def test_a_public_health_check_answers_one_byte_of_truth():
    """The body names the service, its database and its uptime — a fingerprint
    for anything that can reach the route."""
    outside = client.get('/health').json()
    assert outside == {'ok': True}
    assert 'service' not in outside and 'uptime_seconds' not in outside


def test_the_inside_still_gets_the_whole_story():
    """A Docker or Render health check runs *in* the container, so the detailed
    body is still there for it — and a forged ``X-Forwarded-For`` does not buy
    anyone the same thing."""
    detailed = inside.get('/health').json()
    assert detailed['ok'] is True and detailed['database'] == 'ok'
    assert detailed['service'] == 'nexus-python' and detailed['uptime_seconds'] >= 0
    outside = client.get('/health', headers={'X-Forwarded-For': '127.0.0.1'}).json()
    assert outside == {'ok': True}


# ------------------------------------------------------- the subscription headers
def test_the_diagnostic_headers_are_kept_until_an_admin_drops_them():
    """They are useful to the panel and to a human debugging a client, and they
    only ever appear on a route that needs the subscription token. The switch
    exists for the deployment that wants none of it."""
    from app.nodes import upsert
    execute('DELETE FROM users')
    upsert('stealth-origin', 'railway', 'railway.example.com', 443, True,
           'railway.example.com', 'railway.example.com', 'railway', {})
    created = client.post('/api/users', headers=h(), json={'username': 'stealth-user'}).json()
    sub = client.get(f"/sub/{created['uuid']}?target=vless")
    assert sub.status_code == 200
    assert sub.headers['x-nexus-target'] == 'vless'

    client.post('/api/stealth', headers=h(), json={stealth.SUB_HEADERS: '0'})
    quiet = client.get(f"/sub/{created['uuid']}?target=vless")
    assert 'x-nexus-target' not in quiet.headers
    assert 'x-nexus-node-count' not in quiet.headers
    # The client's own three headers are not ours to hide: a client reads them
    # to build its subscription screen.
    assert quiet.headers['profile-update-interval'] == '12'
    assert 'subscription-userinfo' in quiet.headers
    execute('DELETE FROM users')
    execute('DELETE FROM nodes')


# ------------------------------------------------------------------ the API
def test_the_switches_need_the_admin_session():
    assert client.get('/api/stealth').status_code == 401
    assert client.post('/api/stealth', json={stealth.HEADERS: '0'}).status_code == 401
