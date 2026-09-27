"""Manual node selection, per-tab reset, and the advanced subscription surface.

Three newest admin workflows, and the tests are about the specific ways each can
go wrong:

* **scanned nodes** — an automatic scan must never publish an address on its own,
  the deployment's own node must always stay published, and an admin's explicit
  pick must survive a re-scan;
* **reset to defaults** — a tab's reset deletes exactly that tab's keys, and a
  reset can never be reached without a session;
* **advanced subscription** — the alert thresholds grade the report, rotating a
  token really invalidates the old URL, and per-client overrides are validated.
"""
import itertools
import os
import time

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-extras-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-extras-test.db')

import pytest
from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.core.models import UserCreate
from app.db import execute, init_db
from app.edge import sources as edge_sources
from app.main import _setting, app
from app.nodes import catalog, sync_from_sources
from app.users import service as users_service

init_db()
client = TestClient(app)
_counter = itertools.count(1)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        execute('DELETE FROM nodes')
        execute('DELETE FROM cf_ips')
        execute('DELETE FROM settings WHERE key=?', (edge_sources.SOURCE_KEY,))
        execute('DELETE FROM settings WHERE key IN (?,?,?,?)',
                ('edge_auto_publish', 'alert_expiry_days', 'alert_quota_percent',
                 'client_target_overrides'))
        execute("DELETE FROM users WHERE username LIKE 'sbtest%'")
    wipe()
    yield
    wipe()


def _seed_automatic_ip():
    execute("INSERT INTO cf_ips(ip,source,enabled,ok,latency_ms) VALUES('104.16.9.9','cloudflare',1,1,20.0)")


# --------------------------------------------------------- manual node selection
def test_an_automatic_scan_parks_nodes_as_candidates():
    _seed_automatic_ip()
    sync_from_sources('https://panel.example.com', 'https://worker.example.com')
    scanned = [n for n in catalog.list() if n['name'].startswith('cloudflare-')]
    assert scanned, 'the automatic scan should still discover nodes'
    assert all(not n['enabled'] for n in scanned), 'a scan must not publish on its own'
    assert {n['name'] for n in catalog.candidates()} == {n['name'] for n in scanned}
    # The panel's own payload carries the published flag the candidate list keys off.
    payload = {n['name']: n for n in client.get('/api/nodes', headers=h()).json()}
    assert all(not payload[n['name']]['enabled'] for n in scanned)
    assert all(payload[n['name']]['kind'] in ('cloudflare', 'edge') for n in scanned)
    # The deployment's own node is the guaranteed baseline and is never a candidate.
    origin = [n for n in catalog.list() if n['kind'] == 'railway']
    assert origin and origin[0]['enabled']


def test_even_a_hand_configured_location_waits_for_the_same_pick():
    """Strict manual: a scanned location is a candidate like any other."""
    edge_sources.save_source({'kind': 'domain', 'host': 'cdn.example.com', 'location': 'de'})
    sync_from_sources('https://panel.example.com')
    nodes = [n for n in catalog.list() if n['kind'] == 'edge']
    assert nodes and all(not n['enabled'] for n in nodes)
    assert {n['name'] for n in nodes} <= {c['name'] for c in catalog.candidates()}
    client.post('/api/nodes/select', headers=h(), json={'names': [n['name'] for n in nodes]})
    assert all(catalog.get(n['name'])['enabled'] for n in nodes)


def test_an_admin_pick_publishes_and_survives_a_rescan():
    _seed_automatic_ip()
    sync_from_sources('https://panel.example.com', 'https://worker.example.com')
    name = catalog.candidates()[0]['name']
    response = client.post('/api/nodes/select', headers=h(), json={'names': [name], 'enabled': True})
    assert response.status_code == 200, response.text
    assert catalog.get(name)['enabled']
    # A rebuild must not quietly re-park a node the admin approved by hand.
    sync_from_sources('https://panel.example.com', 'https://worker.example.com')
    assert catalog.get(name)['enabled']


def test_manual_selection_is_the_default_and_can_be_switched_off():
    _seed_automatic_ip()
    assert client.post('/api/nodes/selection', headers=h(), json={}).json()['auto'] is False
    # Turning auto on publishes what is already waiting and keeps publishing.
    sync_from_sources('https://panel.example.com', 'https://worker.example.com')
    assert catalog.candidates()
    body = client.post('/api/nodes/selection', headers=h(), json={'auto': True}).json()
    assert body['auto'] is True and body['candidates'] == 0
    assert all(n['enabled'] for n in catalog.list() if n['kind'] == 'cloudflare')
    # And it survives the switch being read back.
    assert client.post('/api/nodes/selection', headers=h(), json={}).json()['auto'] is True


def test_manual_selection_needs_a_session():
    assert client.post('/api/nodes/select', json={'names': ['x']}).status_code == 401
    assert client.post('/api/nodes/selection', json={'auto': True}).status_code == 401


# ---------------------------------------------------------- reset to defaults
def test_the_sections_endpoint_names_each_tabs_keys():
    body = client.get('/api/settings/sections', headers=h()).json()
    assert body['success'] is True
    assert {'customize', 'subs', 'nodes', 'advanced', 'settings'} <= set(body['sections'])
    assert body['sections']['nodes']['keys'] == ['edge_auto_publish']


def test_resetting_a_tab_deletes_exactly_its_keys():
    execute("INSERT INTO settings(key,value) VALUES('accent','#123456') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    execute("INSERT INTO settings(key,value) VALUES('hy2_label','other-tab') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    try:
        response = client.post('/api/settings/reset', headers=h(), json={'section': 'customize'})
        assert response.status_code == 200 and response.json()['section'] == 'customize'
        # accent belonged to customize and is gone; hy2_label is another tab's.
        assert not _exists('accent')
        assert _exists('hy2_label')
    finally:
        execute("DELETE FROM settings WHERE key IN ('accent','hy2_label')")


def test_a_reset_refuses_an_unknown_tab_and_needs_a_session():
    assert client.post('/api/settings/reset', headers=h(), json={'section': 'nope'}).status_code == 400
    assert client.post('/api/settings/reset', json={'section': 'customize'}).status_code == 401


def _exists(key):
    from app.db import row
    return row('SELECT value FROM settings WHERE key=?', (key,)) is not None


# ------------------------------------------------------- advanced subscription
def _user(limit_gb=None, used_gb=0.0, expires_in=None):
    username = f'sbtest{next(_counter)}'
    account = users_service.create_user(UserCreate(username=username, protocol='all'))
    sets, values = [], []
    if limit_gb is not None:
        sets.append('limit_gb=?'); values.append(limit_gb)
    if used_gb:
        sets.append('used_gb=?'); values.append(used_gb)
    if expires_in is not None:
        sets.append('expires_at=?'); values.append(int(time.time()) + expires_in)
    if sets:
        values.append(username)
        execute(f"UPDATE users SET {','.join(sets)} WHERE username=?", values)
    return account, users_service.get_user(username)


def test_the_report_flags_expiring_and_over_quota_users():
    _user(limit_gb=1.0, used_gb=0.95, expires_in=3600)
    response = client.get('/api/subscription/report', headers=h())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['success'] is True
    alerted = {item['username'] for item in body['alerts']}
    assert body['usage'] and any(item['username'] in alerted for item in body['usage'])
    # The two alert kinds are counted apart so the card can badge each.
    assert body['totals']['over_quota'] >= 1 and body['totals']['expiring'] >= 1


def test_the_alert_thresholds_grade_the_report():
    _user(limit_gb=10.0, used_gb=1.0)  # 10% of quota
    assert client.post('/api/subscription/alerts', headers=h(),
                       json={'expiry_days': 5, 'quota_percent': 5}).json()['thresholds']['quota_percent'] == 5
    body = client.get('/api/subscription/report', headers=h()).json()
    assert body['thresholds'] == {'expiry_days': 5, 'quota_percent': 5}
    assert any('quota' in item['alerts'] for item in body['alerts'])
    # A threshold outside its range is refused rather than stored.
    assert client.post('/api/subscription/alerts', headers=h(),
                       json={'quota_percent': 900}).status_code == 400


def test_rotating_a_token_invalidates_the_old_url_and_mints_a_new_one():
    account, _ = _user()
    old = account['uuid']
    body = client.post(f"/api/users/{account['username']}/rotate", headers=h(), json={}).json()
    assert body['success'] is True and body['uuid'] != old
    assert users_service.get_by_token(old) is None
    fresh = users_service.get_by_token(body['uuid'])
    assert fresh and fresh['username'] == account['username']
    assert body['subscription'].endswith('?target=auto')
    assert client.post('/api/users/nobody/rotate', headers=h(), json={}).status_code == 404


def test_per_client_overrides_are_validated_and_reported():
    account, _ = _user()
    bad = client.post('/api/subscription/clients', headers=h(),
                      json={'overrides': {'nope': 'base64'}})
    assert bad.status_code == 400
    saved = client.post('/api/subscription/clients', headers=h(),
                        json={'overrides': {'nekoboxplus': 'clash'}})
    assert saved.status_code == 200 and saved.json()['overrides'] == {'nekoboxplus': 'clash'}
    body = client.get(f"/api/subscription/report?user={account['username']}", headers=h()).json()
    tuned = [c for c in body['clients'] if c['id'] == 'nekoboxplus'][0]
    assert tuned['override'] == 'clash' and tuned['target'] == 'clash'
    assert tuned['url'], 'the per-client link is built for the chosen user'


def test_the_advanced_surface_needs_a_session():
    assert client.get('/api/subscription/report').status_code == 401
    assert client.post('/api/subscription/alerts', json={}).status_code == 401
    assert client.post('/api/subscription/clients', json={}).status_code == 401
