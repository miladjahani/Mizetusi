"""A location must publish addresses, and «پینگ» must mean what a client means.

The complaint this module locks down: locations that never ping, including the
ones added by hand. Two separate defects produced it, and both are easy to
reintroduce:

* a location is built from the clean-IP pool of its provider, and a deployment
  that never ran a scan has an empty pool — so the location published **zero
  nodes** and there was literally nothing to ping;
* the probe treated a bare TCP connect as health, so a location whose Host/SNI
  its addresses cannot serve was reported healthy in the panel while every
  client timed out on it.

The tests keep the suite offline: the provider fetch and the TLS handshake are
patched, never the code under test.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-locping-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-locping-test.db')

from fastapi.testclient import TestClient

from app.config import settings
from app.db import execute, init_db
from app.edge import sources as edge
from app.main import _setting, app
from app.nodes import NodeProbe, catalog, ensure as ensure_nodes, upsert
from app.subscriptions.generator import active_nodes

init_db()
ensure_nodes()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or settings.admin_password}


def _clean():
    execute('DELETE FROM nodes')
    execute('DELETE FROM cf_ips')
    execute('DELETE FROM settings WHERE key=?', (edge.SOURCE_KEY,))
    edge._POOL_FETCH.clear()


def _offline_pool(monkeypatch, addresses=('104.16.1.1', '104.16.1.2')):
    """A provider list that needs no network, so the on-demand fill is testable."""
    monkeypatch.setattr(edge, 'fetch',
                        lambda provider_id, limit=64, per_net=4: (list(addresses), None))


def test_a_hand_added_location_publishes_addresses_without_a_scan(monkeypatch):
    """The pool is filled on demand instead of leaving the location empty.

    ``scan_on_boot`` is off by default, so this is the state of every fresh
    deployment: without the on-demand fill the location published no nodes at
    all — the reason «لوکیشن‌ها پینگ نمی‌دهند» had no ping to show.
    """
    _clean()
    _offline_pool(monkeypatch)
    response = client.post('/api/edge/sources', headers=h(), json={
        'kind': 'ip', 'location': 'de', 'provider': 'cloudflare',
        'host': 'worker.example.workers.dev', 'max': 2})
    assert response.status_code == 200, response.text
    source = response.json()['source']
    assert source['addresses'] >= 2, source
    nodes = [n for n in catalog.list() if n['kind'] == 'cloudflare']
    assert {n['server'] for n in nodes} == {'104.16.1.1', '104.16.1.2'}
    assert all(n['sni'] == 'worker.example.workers.dev' for n in nodes)
    assert all(n['enabled'] for n in nodes)
    _clean()


def test_a_location_with_no_address_at_all_says_so(monkeypatch):
    """«آی‌پی دستی» with an empty list is a reason, not a silent empty location."""
    _clean()
    monkeypatch.setattr(edge, 'fetch', lambda *a, **k: ([], 'empty seed'))
    response = client.post('/api/edge/sources', headers=h(), json={
        'kind': 'ip', 'location': 'ir', 'provider': 'custom',
        'host': 'cdn.example.com', 'max': 2})
    assert response.status_code == 200, response.text
    source = response.json()['sources'][0]
    assert source['addresses'] == 0 and source['nodes'] == 0
    assert 'آی‌پی دستی وارد نشده' in source['reason']
    _clean()


def test_health_is_the_clients_own_handshake_not_a_tcp_connect(monkeypatch):
    """A node that answers on the port but refuses the handshake is *broken*.

    This is the whole point of the stricter probe: a client pings by completing
    TLS with the node's Host/SNI, so a clean IP that only accepts a TCP connect
    must never be advertised as healthy (and must be dropped from subscriptions).
    """
    import asyncio

    from app.nodes import sync_from_sources

    _clean()
    _offline_pool(monkeypatch)
    edge.save_source({'kind': 'ip', 'location': 'de', 'provider': 'cloudflare',
                      'host': 'worker.example.workers.dev', 'max': 1})
    sync_from_sources('https://panel.example.com')
    name = [n['name'] for n in catalog.list() if n['kind'] == 'cloudflare'][0]

    async def refuses_the_handshake(host, port, timeout=4.0, server_hostname=None):
        return None, 'SSLError', False

    async def answers_on_the_port(host, port, timeout=4.0, tls=False, server_hostname=None):
        return 11.0, None

    monkeypatch.setattr(NodeProbe, 'handshake', staticmethod(refuses_the_handshake))
    monkeypatch.setattr(NodeProbe, 'tcp', staticmethod(answers_on_the_port))
    node = catalog.get(name)
    result = asyncio.run(NodeProbe(catalog).ping(node, timeout=1.0))
    assert result['ok'] is False and result['tcp_ok'] is True
    assert 'Host/SNI' in result['hint']
    after = catalog.get(name)
    assert after['latency_ms'] < 0
    # Measured and broken: the clean IP is held back from every subscription, so
    # a client is never handed an entry that cannot even be pinged.
    assert name not in {n['name'] for n in active_nodes()}
    _clean()


def test_the_handshake_decides_health_and_records_the_certificate(monkeypatch):
    """A completed handshake is a ping, and the certificate verdict comes with it."""
    import asyncio

    _clean()
    upsert('de-cdn-01', 'cloudflare', '104.16.9.9', 443, True, 'worker.example.workers.dev',
           'worker.example.workers.dev', 'cloudflare-source',
           {'source_id': 'de', 'provider': 'cloudflare', 'location': 'de'})

    async def completes(host, port, timeout=4.0, server_hostname=None):
        return 24.5, None, False  # handshake completed, certificate did not verify

    monkeypatch.setattr(NodeProbe, 'handshake', staticmethod(completes))
    result = asyncio.run(NodeProbe(catalog).ping(catalog.get('de-cdn-01'), timeout=1.0))
    assert result['ok'] is True and result['latency_ms'] == 24.5
    assert result['tls_ok'] is True and result['tls_verified'] is False
    assert result['hint'] == ''
    assert catalog.get('de-cdn-01')['latency_ms'] == 24.5
    _clean()


def test_every_location_reports_its_own_health_and_can_be_pinged(monkeypatch):
    """The locations card answers «چرا پینگ نمی‌دهد؟» and offers the ping itself."""
    import asyncio

    _clean()
    _offline_pool(monkeypatch, addresses=('104.16.2.1', '104.16.2.2'))
    created = client.post('/api/edge/sources', headers=h(), json={
        'kind': 'ip', 'location': 'nl', 'provider': 'cloudflare',
        'host': 'worker.example.workers.dev', 'max': 2}).json()
    source_id = created['source']['id']

    async def completes(host, port, timeout=4.0, server_hostname=None):
        return (18.0, None, True) if host == '104.16.2.1' else (None, 'TimeoutError', False)

    async def tcp(host, port, timeout=4.0, tls=False, server_hostname=None):
        return 3.0, None  # both answer on the port; only one completes the handshake

    monkeypatch.setattr(NodeProbe, 'handshake', staticmethod(completes))
    monkeypatch.setattr(NodeProbe, 'tcp', staticmethod(tcp))

    data = client.post(f'/api/edge/sources/{source_id}/ping', headers=h(), json={}).json()
    assert data['probed'] == 2 and data['healthy'] == 1 and data['failed'] == 1
    assert data['source']['healthy'] == 1 and data['source']['nodes'] == 2
    assert data['source']['fastest_ms'] == 18.0
    assert 'کنار گذاشته شد' in data['source']['reason']
    served = [item for item in data['sources'] if item['id'] == source_id][0]
    assert served['addresses'] == 2 and served['healthy'] == 1

    # The verdict travels with each node, so the node list can explain itself.
    node = [n for n in data['nodes'] if n['server'] == '104.16.2.2'][0]
    assert node['probe']['ok'] is False and node['probe']['tcp'] is True
    assert node['probe']['hint']
    # A node without an address is a failure with a reason, never a crash.
    empty = asyncio.run(NodeProbe(catalog).ping(
        {'name': 'de-empty', 'kind': 'edge', 'server': '', 'port': 443, 'tls': 1}, timeout=1.0))
    assert empty['ok'] is False and empty['error'] == 'no server address'
    _clean()
