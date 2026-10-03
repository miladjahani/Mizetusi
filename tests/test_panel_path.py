"""The panel is also a complete address of its own: ``/admin``.

The whole application is mounted a second time there, which is what lets a
deployment's main address stop being ``/`` — a panel whose admin page is the
front door of its own domain is one every fingerprinting probe already knows,
while ``/admin`` is a path nothing has reason to guess.

So the tests are the four things that could go wrong with a second address:

* the prefix is **complete** — login, panel, API, assets, portal — not a page
  that dead-ends three links in;
* a **session hop stays on the prefix** (an anonymous ``/admin`` must land on
  ``/admin/login``, not be bounced to the root and look like a different site);
* the **root keeps working**, because it is not ours to take away: the Telegram
  WEB proxy loads ``/?bridge=…`` in a hidden WebView, and every subscription
  link an admin has already handed to a user points at the root;
* the two addresses are the **same app**, not a second surface with its own
  rules: the same session cookie opens both, and the same admin header
  authenticates both.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-admin-path-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-admin-path-test.db')

from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.core import stealth
from app.db import execute, init_db
from app.main import PANEL_PREFIX, _setting, app

init_db()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


def test_the_prefix_is_the_admin_address_and_not_just_a_page():
    """One mount, the whole app: the panel answers at /admin, not only at /."""
    assert PANEL_PREFIX == '/admin'
    assert client.get(PANEL_PREFIX, headers=h()).status_code == 200
    # The trailing-slash form is the mount itself and must not 404.
    assert client.get(PANEL_PREFIX + '/', headers=h()).status_code == 200
    assert client.get(PANEL_PREFIX + '/login').status_code == 200
    # Its API, and the assets the page asks for, are on the same address.
    assert client.get(PANEL_PREFIX + '/api/settings', headers=h()).status_code == 200
    assert client.get(PANEL_PREFIX + '/static/app.css').status_code == 200
    # The API still needs a session there: the mount is not a way around auth.
    assert client.get(PANEL_PREFIX + '/api/settings').status_code == 401


def test_an_anonymous_visit_lands_on_the_prefixed_login():
    """A redirect to the root's /login would drop the browser on a page that
    looks like a different site, and would make the address bar lie.

    The default face for an anonymous visit is now the decoy web app, but its
    way in must stay on whichever address was opened — and with the face
    switched off, the old bounce has to come back unchanged.
    """
    for path in (PANEL_PREFIX, PANEL_PREFIX + '/'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200
        # The public face, with a sign-in link that stays on this prefix.
        assert f'href="{PANEL_PREFIX}/login"' in response.text
        assert 'id="loginForm"' not in response.text
    # …and that login page is the real one, not a 404 from the mount.
    login_page = client.get(PANEL_PREFIX + '/login').text
    assert 'id="loginForm"' in login_page and 'id="password"' in login_page
    # The face is a switch: off brings the original redirect back.
    client.post('/api/stealth', headers=h(), json={stealth.DECOY: '0'})
    try:
        for path in (PANEL_PREFIX, PANEL_PREFIX + '/'):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers['location'] == PANEL_PREFIX + '/login'
    finally:
        execute('DELETE FROM settings WHERE key=?', (stealth.DECOY,))


def test_the_root_still_answers_because_it_is_not_ours_to_take_away():
    """Two things live there: the Telegram WEB proxy's carrier
    (``/?bridge=…``) and every subscription link already handed to a user."""
    assert client.get('/', headers=h()).status_code == 200
    # Anonymous, the root shows the public face instead of a redirect whose
    # Location header announces where the door is.
    face = client.get('/', follow_redirects=False)
    assert face.status_code == 200 and 'href="/login"' in face.text
    assert client.get('/login').status_code == 200
    # The carrier is answered before the session check, at the root, exactly as
    # it was before the second address existed.
    assert client.get('/?bridge=nope', follow_redirects=False).status_code in (403, 404, 400)
    assert client.get('/health').status_code == 200
    assert client.get('/api/settings', headers=h()).status_code == 200
    # And with the face switched off, the old bounce is back.
    client.post('/api/stealth', headers=h(), json={stealth.DECOY: '0'})
    try:
        assert client.get('/', follow_redirects=False).headers['location'] == '/login'
    finally:
        execute('DELETE FROM settings WHERE key=?', (stealth.DECOY,))


def test_both_addresses_are_the_same_panel():
    """One session, one set of rules: the cookie the root login sets opens
    /admin, and the admin header works the same on both."""
    execute('DELETE FROM users')
    login = client.post('/api/login', json={'password': cfg.admin_password})
    assert login.status_code == 200
    cookie = login.cookies.get('nexus_session') or login.cookies.get('nexus-admin-session')
    assert cookie
    prefixed = client.get(PANEL_PREFIX, cookies=login.cookies, follow_redirects=False)
    assert prefixed.status_code == 200
    # And the session the prefixed address logs in with opens the root too.
    again = client.post(PANEL_PREFIX + '/api/login', json={'password': cfg.admin_password})
    assert again.status_code == 200
    assert client.get('/', cookies=again.cookies, follow_redirects=False).status_code == 200
    execute('DELETE FROM users')
