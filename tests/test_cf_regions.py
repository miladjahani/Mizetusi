"""Cloudflare multi-location: one anycast network, several labelled locations.

A Cloudflare edge answers on one anycast network, so 'the location' of a clean-IP
node used to be whatever single label the admin typed — every entry dialled the
same handful of addresses and a client showed one country for all of them. The
``cloudflare-regions`` pack fixes that by giving every location its own *slice of
Cloudflare's published ranges*, which is what a client's geo database reports and
what decides which routes are open to it. These tests pin that contract down:

* the pack exists, needs a Host/SNI, and refuses to install without one instead
  of publishing locations that could never answer;
* each installed location is a real ``ip`` source whose addresses stay inside its
  own ranges (so two locations never publish the same address);
* the catalog keeps working without the pack, and the range filter never invents
  addresses outside the group it claims.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-cfregions-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-cfregions-test.db')

import ipaddress

from fastapi.testclient import TestClient

from app.config import settings
from app.db import execute, init_db, rows
from app.edge import packs as edge_packs
from app.edge import sources as edge
from app.main import _setting, app
from app.nodes import NodeProbe, ensure as ensure_nodes

init_db()
ensure_nodes()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or settings.admin_password}


def _offline(monkeypatch):
    """No socket is opened: the pack/ping paths are exercised without the network."""
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)

    async def dead_handshake(host, port, timeout=4.0, server_hostname=None):
        return None, 'TimeoutError', False

    async def dead_tcp(host, port, timeout=4.0, tls=False, server_hostname=None):
        return None, 'TimeoutError'

    monkeypatch.setattr(NodeProbe, 'handshake', staticmethod(dead_handshake))
    monkeypatch.setattr(NodeProbe, 'tcp', staticmethod(dead_tcp))


def _clean():
    execute("DELETE FROM nodes WHERE kind IN ('cloudflare','edge')")
    execute('DELETE FROM cf_ips')
    execute('DELETE FROM settings WHERE key=?', (edge.SOURCE_KEY,))
    execute('DELETE FROM settings WHERE key=?', ('cloudflare_worker_url',))
    edge._POOL_FETCH.clear()


def test_the_cf_regions_pack_is_listed_with_its_ranges():
    listing = client.get('/api/edge/packs', headers=h()).json()
    packs = {item['id']: item for item in listing['packs']}
    assert 'cloudflare-regions' in packs
    pack = packs['cloudflare-regions']
    assert pack['kind'] == 'ip' and pack['host_required'] is True
    assert pack['locations'] >= 6
    # Every entry names a country and owns real Cloudflare ranges — that is what
    # makes the location more than a cosmetic label.
    for entry in pack['entries']:
        assert entry['location'] and entry['ranges']
        for cidr in entry['ranges']:
            assert ipaddress.ip_network(cidr).version == 4


def test_installing_the_pack_needs_a_host_and_creates_ip_locations(monkeypatch):
    _clean()
    _offline(monkeypatch)
    missing = client.post('/api/edge/packs', headers=h(), json={'id': 'cloudflare-regions'})
    # No Worker URL, no saved base URL and no override: refuse the whole install.
    assert missing.status_code == 400
    assert 'کلودفلر' in missing.json()['detail']
    assert edge.sources() == []

    installed = client.post('/api/edge/packs', headers=h(), json={
        'id': 'cloudflare-regions', 'hosts': 'worker.example.workers.dev'})
    assert installed.status_code == 200, installed.text
    body = installed.json()
    assert len(body['created']) == len(edge_packs.pack('cloudflare-regions')['locations'])
    for source in body['sources']:
        assert source['kind'] == 'ip' and source['provider'] == 'cloudflare'
        assert source['host'] == 'worker.example.workers.dev'
        assert source['ranges'] and source['pack'] == 'cloudflare-regions'
        assert source['location']
    _clean()


def test_every_cf_location_publishes_only_its_own_ranges(monkeypatch):
    """The whole point: addresses differ per location, and stay inside its group."""
    _clean()
    _offline(monkeypatch)
    # The provider pool is shared; the region filter is what keeps them apart.
    monkeypatch.setattr(edge, 'fetch', lambda provider_id, limit=64, per_net=4: (list((
        '103.22.200.10', '197.234.240.10', '141.101.64.10', '103.21.244.10')), None))
    created = client.post('/api/edge/packs', headers=h(), json={
        'id': 'cloudflare-regions', 'hosts': 'worker.example.workers.dev'}).json()
    assert created['success'] is True
    pools = {}
    for source in edge.sources():
        addresses = edge._ordered_ips(source)  # seeds the pool on demand
        assert addresses, source['id']
        for address in addresses:
            assert any(ipaddress.ip_address(address) in ipaddress.ip_network(cidr)
                       for cidr in source['ranges']), (source['id'], address)
        pools[source['id']] = set(addresses)
    # Two locations of the same anycast network no longer hand out one address set.
    assert len(pools) == len(edge.sources())
    assert not (pools['nl'] & pools['sg'])
    assert not (pools['za'] & pools['sg'])
    _clean()


def test_a_cf_location_reports_health_like_any_other(monkeypatch):
    """Ranges do not bypass the honest probe: the panel still says what answers."""
    import asyncio

    from app.nodes import NodeProbe, catalog

    _clean()
    _offline(monkeypatch)
    monkeypatch.setattr(edge, 'fetch', lambda provider_id, limit=64, per_net=4: (
        ['103.22.200.11'], None))
    client.post('/api/edge/packs', headers=h(), json={
        'id': 'cloudflare-regions', 'hosts': 'worker.example.workers.dev',
        'location': 'sg'})

    async def handshake(host, port, timeout=4.0, server_hostname=None):
        return 21.0, None, True

    monkeypatch.setattr(NodeProbe, 'handshake', staticmethod(handshake))
    data = client.post('/api/edge/sources/sg/ping', headers=h(), json={}).json()
    assert data['source']['addresses'] and data['source']['nodes']
    assert data['healthy'] == data['probed'] and data['failed'] == 0
    assert data['source']['fastest_ms'] == 21.0
    assert data['source']['reason'] == ''
    assert catalog.get([n['name'] for n in data['nodes'] if n['kind'] == 'cloudflare'][0])
    _clean()


def test_uninstall_removes_only_that_pack(monkeypatch):
    _clean()
    _offline(monkeypatch)
    client.post('/api/edge/packs', headers=h(), json={
        'id': 'cloudflare-regions', 'hosts': 'worker.example.workers.dev'})
    client.post('/api/edge/packs', headers=h(), json={'id': 'multi-cdn'})
    assert {item['pack'] for item in edge.sources()} == {'multi-cdn', 'cloudflare-regions'}
    removed = client.post('/api/edge/packs', headers=h(), json={
        'id': 'cloudflare-regions', 'action': 'uninstall'}).json()
    assert removed['success'] is True and len(removed['removed']) >= 6
    left = {item['pack'] for item in edge.sources()}
    assert left == {'multi-cdn'}
    assert not [row for row in rows("SELECT name FROM nodes")
                if str(row['name']).endswith('-cloudflare-01') and row['name'].startswith('sg-')]
    client.post('/api/edge/packs', headers=h(), json={'id': 'multi-cdn', 'action': 'uninstall'})
    _clean()
