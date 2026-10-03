"""The public face: what somebody (or something) with no session sees.

A redirect to a login page whose title said what this is, a manifest that
advertised subscriptions, a service worker that narrated the panel, a JSON
``{"detail": "Not Found"}`` that named the framework — each individually small,
together a picture. The tests here are the whole anonymous surface at once:
the front door on both addresses, the login copy, the manifest, the worker,
the three routes a curious crawler always asks for, the shape of a 404, and
the panel's own source behind the two switches that keep it quiet.

Every test that flips a switch puts it back, so the suite that runs *after*
this file still sees the shipped defaults.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-decoy-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-decoy-test.db')

from fastapi.testclient import TestClient

from app.config import SUPPORT_CHANNEL, settings as cfg
from app.core import stealth
from app.db import execute, init_db
from app.main import _brand, _setting, app

init_db()
client = TestClient(app)

KEYS = (stealth.DECOY, stealth.ASSETS)

# What no public page may carry, in either script: the vocabulary that names
# this product for what it is, whatever a probe reads it in.
BANNED = ('پروکسی', 'سابلینک', 'زیرسافت', 'زیرساخت', 'مرکز کنترل', 'تونل',
          'کلاینت', 'کانفیگ', 'نود',
          'proxy', 'vpn', 'xray', 'vless', 'vmess', 'reality', 'trojan',
          'subscription', 'railway', 'cloudflare', 'bridge')


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


def flip(key, value):
    client.post('/api/stealth', headers=h(), json={key: value})


def setup_function(_item):
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))


def teardown_function(_item):
    for key in KEYS:
        execute('DELETE FROM settings WHERE key=?', (key,))


def assert_clean(text):
    """Nothing on a public page may name the product."""
    lowered = text.lower()
    for word in BANNED:
        assert word.lower() not in lowered, f'the public face leaks: {word}'


# ------------------------------------------------------------- the front door
def test_the_front_door_is_an_ordinary_web_app():
    """An anonymous ``/`` used to answer 303 whose Location named the login
    page — the first thing any probe reads. Now it is a landing page a crawler
    can index and a human can use, and it carries no vocabulary of ours."""
    anonymous = TestClient(app)
    response = anonymous.get('/', follow_redirects=False)
    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert 'location' not in response.headers
    # The signals a normal public page has: a title, a description, an
    # indexable robots directive, Open Graph tags for link previews.
    assert '<title>' in response.text
    assert 'name="description"' in response.text
    assert 'index,follow' in response.text
    assert 'property="og:title"' in response.text
    assert 'property="og:image"' in response.text
    # The way in, and nothing of the panel itself.
    assert 'href="/login"' in response.text
    assert 'id="loginForm"' not in response.text
    assert '/static/js/app.js' not in response.text
    assert_clean(response.text)


def test_the_second_address_has_the_same_face():
    """``/admin`` is the address a scanner brute-forces; it must show the same
    ordinary face, with the sign-in link staying on its own prefix."""
    anonymous = TestClient(app)
    for path in ('/admin', '/admin/'):
        response = anonymous.get(path, follow_redirects=False)
        assert response.status_code == 200
        assert 'href="/admin/login"' in response.text
        assert 'id="loginForm"' not in response.text
        assert_clean(response.text)


def test_switching_the_face_off_restores_the_old_bounce():
    """The disguise is a switch, not a rewrite of history: off, the panel goes
    back to redirecting anonymous visitors to its login page."""
    flip(stealth.DECOY, '0')
    anonymous = TestClient(app)
    assert anonymous.get('/', follow_redirects=False).headers['location'] == '/login'
    prefixed = anonymous.get('/admin', follow_redirects=False)
    assert prefixed.status_code == 303
    assert prefixed.headers['location'] == '/admin/login'
    # And the odd answers go back to their old shapes too.
    assert anonymous.get('/robots.txt').status_code == 404
    page = anonymous.get('/definitely-not-a-page')
    assert page.status_code == 404 and page.json()['detail'] == 'Not Found'


# ----------------------------------------------------------------- the login
def test_the_login_page_says_nothing_about_what_this_is():
    """The login page is reachable from the public face, so while the face is
    on it never describes the deployment — and it no longer prints the default
    password for whoever wanders in."""
    page = client.get('/login')
    assert page.status_code == 200
    assert 'id="loginForm"' in page.text and 'id="password"' in page.text
    # The things that must stay: the way in, the support link, the theme.
    assert f'href="{SUPPORT_CHANNEL}"' in page.text
    assert '#060a05' in page.text
    assert 'name="robots" content="noindex,nofollow"' in page.text
    assert_clean(page.text)
    assert 'رمز اولیه' not in page.text, 'the default password must not be advertised'
    # Off: the old copy, the old title, the old hint — the admin's own choice.
    flip(stealth.DECOY, '0')
    old = client.get('/login')
    assert 'مرکز کنترل' in old.text and 'پروکسی' in old.text
    assert 'رمز اولیه' in old.text


# ------------------------------------------------------- the install surface
def test_the_manifest_no_longer_maps_the_panel():
    """Fetched before any session exists — by the login page, by any crawler —
    so with the face on it carries a name and nothing else about the product."""
    manifest = client.get('/manifest.webmanifest')
    assert manifest.status_code == 200
    assert 'manifest' in manifest.headers['content-type']
    body = manifest.json()
    assert body['name'] == _brand()['app_name']
    assert body['description'].startswith(_brand()['app_name'])
    assert 'shortcuts' not in body, 'shortcuts would map the panel sections'
    # The install contract itself is untouched.
    assert body['start_url'].startswith('/') and body['display'] == 'standalone'
    assert any(icon.get('purpose') == 'maskable' for icon in body['icons'])
    assert_clean(manifest.text)
    # Off: the descriptive manifest with its section shortcuts comes back.
    flip(stealth.DECOY, '0')
    old = client.get('/manifest.webmanifest').json()
    assert 'مرکز کنترل' in old['name'] and 'سابلینک' in old['description']
    assert 'shortcuts' in old


def test_the_service_worker_ships_without_the_narration():
    """``/sw.js`` is fetched from the login page — with no session — so with
    the face on it is the code and the build token, minus the comments that
    narrate what kind of app runs behind it."""
    worker = client.get('/sw.js')
    assert worker.status_code == 200
    lines = worker.text.splitlines()
    assert not any(line.strip().startswith(('//', '/*')) for line in lines), \
        'a comment line survived the strip'
    assert 'service worker' not in worker.text.lower()
    # The behaviour is byte-for-byte the same: same version token, same cache.
    assert "const VERSION = 'nexus-" in worker.text
    assert 'nexus-dev' not in worker.text
    assert '/static/app.css' in worker.text
    assert worker.headers.get('service-worker-allowed') == '/'
    # Off: the narration is back.
    flip(stealth.DECOY, '0')
    assert 'service worker' in client.get('/sw.js').text.lower()


# --------------------------------------------------------- the crawler routes
def test_a_curious_robot_gets_the_usual_answers():
    """robots.txt, a sitemap and a favicon are what an ordinary site serves;
    404 on all three is its own tell — and none of them may point at /admin."""
    robots = client.get('/robots.txt')
    assert robots.status_code == 200 and 'text/plain' in robots.headers['content-type']
    assert 'User-agent: *' in robots.text and 'Allow: /' in robots.text
    assert 'Sitemap:' in robots.text
    assert '/admin' not in robots.text

    sitemap = client.get('/sitemap.xml')
    assert sitemap.status_code == 200 and 'xml' in sitemap.headers['content-type']
    assert '<loc>' in sitemap.text and sitemap.text.rstrip().endswith('</urlset>')

    favicon = client.get('/favicon.ico')
    assert favicon.status_code == 200
    assert 'image/png' in favicon.headers['content-type']
    assert favicon.content[:8] == b'\x89PNG\r\n\x1a\n'

    # Off: back to silence.
    flip(stealth.DECOY, '0')
    for path in ('/robots.txt', '/sitemap.xml', '/favicon.ico'):
        assert client.get(path).status_code == 404, path


# --------------------------------------------------------------- the 404s
def test_an_unknown_path_is_a_web_404_and_an_unknown_api_stays_json():
    """``{"detail": "Not Found"}`` on every miss is the framework introducing
    itself; an ordinary website answers a missing *page* with an HTML 404 —
    while /api/ keeps speaking JSON, because that is what an API does."""
    anonymous = TestClient(app)
    page = anonymous.get('/definitely-not-a-page')
    assert page.status_code == 404
    assert 'text/html' in page.headers['content-type']
    assert 'detail' not in page.text and 'Not Found' not in page.text
    assert '۴۰۴' in page.text
    assert_clean(page.text)
    # The same for a miss under the second address, and for a method without
    # a route of its own.
    assert anonymous.get('/admin/no-such-page').status_code == 404
    assert anonymous.post('/definitely-not-a-page').status_code == 404
    # /api/ answers in JSON either way — a client (or a test) parses it.
    for path in ('/api/no-such-endpoint', '/api/setup/admin/nope'):
        miss = anonymous.get(path)
        assert miss.status_code == 404 and miss.json()['detail'] == 'Not Found', path
    # Off: every miss is JSON again, as it always was.
    flip(stealth.DECOY, '0')
    old = anonymous.get('/definitely-not-a-page')
    assert old.status_code == 404 and old.json()['detail'] == 'Not Found'


# ----------------------------------------------------------- the panel source
def test_the_panels_own_source_needs_a_reason_to_be_read():
    """``/static/js/*`` is the whole interface in one download. With the guard
    on, three kinds of caller still get it: a browser loading its own page's
    scripts, a signed-in session, and the admin header — and a bare request
    gets the same 404 as a file that does not exist."""
    anonymous = TestClient(app)
    assert anonymous.get('/static/js/app.js').status_code == 404
    assert anonymous.get('/static/sw.js').status_code == 404
    # A browser fetching a module for a page on this origin.
    fetched = anonymous.get('/static/js/app.js', headers={'Sec-Fetch-Site': 'same-origin'})
    assert fetched.status_code == 200
    # A browser navigating to the file directly is not a page needing it.
    direct = anonymous.get('/static/js/app.js', headers={'Sec-Fetch-Site': 'none'})
    assert direct.status_code == 404
    # A signed-in session, by cookie…
    member = TestClient(app)
    assert member.post('/api/login', json={'password': _setting('admin_password') or cfg.admin_password}).status_code == 200
    assert member.get('/static/js/app.js').status_code == 200
    # …or by header.
    assert client.get('/static/js/app.js', headers=h()).status_code == 200
    # The stylesheet and the icons stay public: they name nothing.
    assert anonymous.get('/static/app.css').status_code == 200
    assert anonymous.get('/static/icons/favicon-32.png').status_code == 200
    # The guard is a switch, and off it is the old world.
    flip(stealth.ASSETS, '0')
    assert anonymous.get('/static/js/app.js').status_code == 200


# ------------------------------------------------------------------- the card
def test_the_switches_are_on_the_low_fingerprint_card():
    """Both switches live where every other quieting decision already lives,
    on by default, and only a session may move them."""
    body = client.get('/api/stealth', headers=h()).json()
    states = {item['key']: item for item in body['switches']}
    for key in (stealth.DECOY, stealth.ASSETS):
        assert key in states, key
        assert states[key]['on'] is True
        assert states[key]['default'] == '1'
        assert states[key]['label']
    assert client.get('/api/stealth').status_code == 401
    assert client.post('/api/stealth', json={stealth.DECOY: '0'}).status_code == 401
    # A settings reset puts both back with everything else in that section.
    from app.panel_extras import SETTINGS_SECTIONS
    assert stealth.DECOY in SETTINGS_SECTIONS['advanced']
    assert stealth.ASSETS in SETTINGS_SECTIONS['advanced']
