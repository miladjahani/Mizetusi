"""What the *client app* reads off a subscription response.

A subscription URL is not only a body. Clients (v2rayNG, Streisand, Hiddify,
NekoBox…) read three response headers to build their own «subscription» screen,
and all three were missing, so a user had to remember to press «update» and had
no way to see their usage or reach the status window from inside the app:

* ``profile-update-interval`` — how many hours until the client re-fetches;
* ``subscription-userinfo`` — the traffic line (download/total/expiry);
* ``profile-web-page-url`` — the «open web page» button.

These tests pin the values, not just their presence, because a wrong ``total``
is worse than none: it makes a client paint a full bar and lock a working user
out of their own profile.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')

import pytest
from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.db import execute, init_db
from app.main import _setting, app
from app.nodes import ensure as ensure_nodes, upsert

init_db()
ensure_nodes()
client = TestClient(app)

GIB = 1024 ** 3


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


@pytest.fixture(autouse=True)
def clean():
    """One shared file-backed database, so every row this module writes is removed."""
    def wipe():
        execute('DELETE FROM nodes')
        execute("DELETE FROM users WHERE username LIKE 'cl%'")
    wipe()
    yield
    wipe()


def _seed_nodes():
    upsert('railway-direct', 'railway', 'railway.example.com', 443, True,
           'railway.example.com', 'railway.example.com', 'railway', {})
    upsert('cloudflare-01', 'cloudflare', '104.16.1.1', 443, True,
           'worker.example.workers.dev', 'worker.example.workers.dev', 'cloudflare-probe', {})
    execute("UPDATE nodes SET latency_ms=25.0 WHERE name='cloudflare-01'")


def _user(**fields):
    _seed_nodes()
    fields.setdefault('username', 'cluser')
    response = client.post('/api/users', headers=h(), json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_unlimited_user_reports_no_cap_and_no_expiry():
    user = _user(username='clunlimited')
    response = client.get(f"/sub/{user['uuid']}?target=vless")
    assert response.status_code == 200, response.text
    assert response.headers['profile-update-interval'] == '12'
    info = response.headers['subscription-userinfo']
    # total=0 / expire=0 is exactly how a client renders «unlimited» / «no expiry»,
    # so it must not be sent as a real number here.
    assert 'download=0' in info and 'total=0' in info and 'expire=0' in info
    assert response.headers['profile-web-page-url'].endswith(f"/portal/{user['uuid']}")


def test_a_metered_user_reports_the_usage_the_cap_and_the_expiry():
    user = _user(username='clmetered', limit_gb=10, expiry_days=30)
    # Record real traffic so the download side is not trivially zero.
    tracked = client.post(f"/api/traffic/{user['username']}",
                          headers=h(), json={'bytes': int(2.5 * GIB), 'requests': 1})
    assert tracked.status_code == 200, tracked.text
    response = client.get(f"/sub/{user['uuid']}?target=vless")
    info = response.headers['subscription-userinfo']
    fields = dict(piece.strip().split('=') for piece in info.split(';'))
    # The meter stores whole gigabytes, so the byte count is close, not exact.
    assert abs(int(fields['download']) - 2.5 * GIB) < GIB
    assert int(fields['total']) == 10 * GIB
    assert int(fields['expire']) > 0                   # a real expiry is carried


def test_a_per_node_link_carries_them_too():
    """A user who was handed one node's link gets the same client screen."""
    user = _user(username='clnode')
    response = client.get(f"/sub/{user['uuid']}/cloudflare-01?target=vless")
    assert response.status_code == 200, response.text
    assert response.headers['profile-update-interval'] == '12'
    assert 'subscription-userinfo' in response.headers
    assert response.headers['profile-web-page-url'].endswith(f"/portal/{user['uuid']}")


def test_a_client_format_still_gets_them():
    """Clash/sing-box bodies are YAML/JSON, but the client screen is the same."""
    user = _user(username='clclash')
    response = client.get(f"/sub/{user['uuid']}?target=clash")
    assert response.status_code == 200, response.text
    assert response.headers['profile-update-interval'] == '12'
    assert 'subscription-userinfo' in response.headers
