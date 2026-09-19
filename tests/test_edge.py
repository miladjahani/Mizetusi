"""Edge sources: clean IPs from several providers, manual IPs and clean domains.

The catalog used to be Cloudflare-only and every node shared one Host. It is now
built from *sources* — one per location — and each source publishes the whole
protocol matrix with its own Host/SNI. These tests cover the parsing of every
provider's published list, the manual-IP path, source validation, the node plan
(including the compatible fallback when no source is defined) and the location
filter a user can subscribe to.
"""
import json
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-edge-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-edge-test.db')

import pytest

from fastapi.testclient import TestClient

from app.config import settings
from app.db import execute, rows, init_db
from app.edge import sources as edge
from app.main import _setting, app
from app.nodes import catalog, ensure as ensure_nodes
from app.subscriptions.generator import active_nodes, render
from app.users.service import create_user
from app.core.models import UserCreate

init_db()
ensure_nodes()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or settings.admin_password}


@pytest.fixture(autouse=True)
def clean_sources():
    """Sources are stored in the shared settings table: never leak one to a peer."""
    edge._write_sources([])
    execute("DELETE FROM nodes WHERE kind IN ('cloudflare','edge')")
    yield
    edge._write_sources([])
    execute("DELETE FROM nodes WHERE kind IN ('cloudflare','edge')")


# --------------------------------------------------------------------- providers
def test_every_provider_publishes_a_real_source():
    assert edge.DEFAULT_PROVIDER in edge.provider_ids()
    assert edge.provider('custom')['format'] == 'manual'
    for spec in edge.PROVIDERS:
        if spec['format'] == 'manual':
            continue
        assert spec['urls'], spec['id']
        assert all(url.startswith('https://') for url in spec['urls']), spec['id']
    assert not edge.provider('nope')


def test_each_published_format_parses():
    fastly = json.dumps({'addresses': ['151.101.0.0/16', 'bogus', '2001:db8::/32'], 'ipv6_addresses': []})
    assert edge.parse_list('fastly', fastly) == ['151.101.0.0/16']
    arvan = '185.143.232.0/22\n\n# comment\n188.229.116.16/30\n'
    assert edge.parse_list('arvancloud', arvan) == ['185.143.232.0/22', '188.229.116.16/30']
    bunny = json.dumps(['89.187.188.227', 'not-an-ip'])
    assert edge.parse_list('bunny', bunny) == ['89.187.188.227/32']
    aws = json.dumps({'prefixes': [{'service': 'CLOUDFRONT', 'ip_prefix': '13.32.0.0/15'},
                                   {'service': 'S3', 'ip_prefix': '52.0.0.0/8'}]})
    assert edge.parse_list('cloudfront', aws) == ['13.32.0.0/15']
    assert edge.parse_list('cloudflare', '104.16.0.0/13\n') == ['104.16.0.0/13']


def test_a_failed_download_reports_an_error_instead_of_raising(monkeypatch):
    monkeypatch.setattr(edge, '_http_text', lambda *a, **k: (_ for _ in ()).throw(OSError('no dns')))
    result = edge.scan('cloudflare', limit=4)
    assert result['ok'] is False and result['added'] == 0 and result['error']


def test_sample_is_bounded_and_deterministic():
    cidrs = ['104.16.0.0/13', '151.101.0.0/16']
    first = edge.sample(cidrs, limit=6, per_net=3)
    assert first == edge.sample(cidrs, limit=6, per_net=3)
    assert len(first) == 6 and len(set(first)) == 6


def test_manual_ips_are_added_and_removed():
    result = edge.add_ips('203.0.113.7, 203.0.113.8\n nonsense 198.51.100.0/30')
    assert result['skipped'] == ['nonsense']
    assert '203.0.113.7' in result['added']
    # A hand-written CIDR is sampled (bounded), never expanded wholesale.
    assert '198.51.100.1' in result['added'] and len(result['added']) <= 10
    mine = [item['ip'] for item in edge.ips('custom')]
    assert '203.0.113.7' in mine
    edge.remove_ip('203.0.113.7')
    assert '203.0.113.7' not in [item['ip'] for item in edge.ips('custom')]


def test_outbound_scanning_is_quiet_by_default():
    """A deploy must not look like a port scan.

    Hundreds of outbound TCP/TLS connects within seconds of a boot is what got a
    workspace flagged for "suspicious activity", so seeding is opt-in, the batch
    and its concurrency are small, and a master switch can stop every probe.
    """
    import asyncio

    from app.cloudflare.monitor import probe_all

    assert settings.scan_on_boot is False
    assert settings.cf_probe_limit <= 64 and settings.cf_probe_concurrency <= 8
    settings.outbound_probe_enabled = False
    try:
        assert asyncio.run(probe_all()) == []  # short-circuits without opening a socket
    finally:
        settings.outbound_probe_enabled = True


def test_the_panel_reports_how_much_it_is_allowed_to_scan():
    probing = client.get('/api/edge', headers=h()).json()['probing']
    assert probing['scan_on_boot'] is False
    assert probing['limit'] <= 64 and probing['concurrency'] <= 8
    assert probing['enabled'] is True


def test_provider_summary_counts_the_pool():
    edge.add_ips('203.0.113.20', 'fastly')
    summary = {item['id']: item for item in edge.provider_summary()}
    assert summary['fastly']['total'] >= 1
    assert summary['custom']['scannable'] is False


# ----------------------------------------------------------------- edge sources
def test_source_validation_rejects_incomplete_definitions():
    with pytest.raises(ValueError):
        edge.save_source({'kind': 'ip', 'host': ''}, '')
    with pytest.raises(ValueError):
        edge.save_source({'kind': 'domain', 'host': 'nodots'}, '')
    with pytest.raises(ValueError):
        edge.save_source({'kind': 'ip', 'host': 'cdn.example.com', 'provider': 'does-not-exist'}, '')
    with pytest.raises(ValueError):
        edge.save_source({'kind': 'nonsense', 'host': 'cdn.example.com'}, '')


def test_a_location_is_saved_with_a_readable_id():
    source, items = edge.save_source({'kind': 'ip', 'provider': 'cloudflare', 'location': 'de',
                                      'host': 'cdn.example.com', 'max': 3}, '')
    assert source['id'] == 'de'
    assert source['location'] == 'de'
    assert source['max'] == 3 and source['enabled'] == 1
    assert [item['id'] for item in items] == ['de']
    updated, _ = edge.save_source({'label': 'آلمان · کلودفلر'}, 'de')
    assert updated['id'] == 'de' and 'آلمان' in updated['label']
    assert edge.delete_source('de') == []
    assert edge.sources() == []


def test_a_location_publishes_its_own_nodes_with_its_own_host():
    edge.add_ips('203.0.113.31 203.0.113.32', 'cloudflare')
    source, _ = edge.save_source({'kind': 'ip', 'provider': 'cloudflare', 'location': 'de',
                                  'host': 'de.example.com', 'max': 2}, '')
    nodes = edge.nodes_for_source(source)
    assert [node['name'] for node in nodes] == ['de-cloudflare-01', 'de-cloudflare-02']
    assert all(node['host'] == 'de.example.com' and node['sni'] == 'de.example.com' for node in nodes)
    assert all(node['kind'] == 'cloudflare' for node in nodes)
    assert {node['metadata']['location'] for node in nodes} == {'de'}


def test_a_clean_domain_is_one_node_of_its_own_kind():
    source, _ = edge.save_source({'kind': 'domain', 'host': 'nl.example.com', 'location': 'nl'}, '')
    nodes = edge.nodes_for_source(source)
    assert len(nodes) == 1
    assert nodes[0]['kind'] == 'edge' and nodes[0]['server'] == 'nl.example.com'
    assert nodes[0]['metadata']['provider'] == 'domain'


def test_the_catalog_keeps_working_with_no_source_configured():
    # Compatibility: with nothing configured, the Worker host (or a detected
    # Cloudflare-fronted domain) still fronts the healthiest clean IPs.
    edge.add_ips('203.0.113.41', 'cloudflare')
    assert edge.plan(None, None) == []
    fallback = edge.plan('https://edge.example.workers.dev', None)
    assert fallback and fallback[0]['name'] == 'cloudflare-01'
    assert fallback[0]['sni'] == 'edge.example.workers.dev'


def test_plan_uses_the_configured_locations_instead():
    edge.add_ips('203.0.113.51', 'cloudflare')
    edge.save_source({'kind': 'ip', 'provider': 'cloudflare', 'location': 'de', 'host': 'de.example.com', 'max': 2}, '')
    edge.save_source({'kind': 'domain', 'host': 'nl.example.com', 'location': 'nl'}, '')
    plan = edge.plan('https://edge.example.workers.dev', None)
    names = {node['name'] for node in plan}
    # One group per location, and the Worker host is no longer used as SNI.
    assert 'nl' in names
    assert sum(1 for name in names if name.startswith('de-cloudflare-')) == 2
    assert all(node['sni'] != 'edge.example.workers.dev' for node in plan)


def test_sync_creates_the_location_nodes_and_removes_deleted_ones():
    edge.add_ips('203.0.113.61', 'cloudflare')
    edge.save_source({'kind': 'ip', 'provider': 'cloudflare', 'location': 'de', 'host': 'de.example.com'}, '')
    edge.save_source({'kind': 'domain', 'host': 'nl.example.com', 'location': 'nl'}, '')
    catalog.sync('https://panel.example.com', None)
    produced = {node['name'] for node in rows("SELECT name FROM nodes WHERE kind IN ('cloudflare','edge')")}
    assert 'de-cloudflare-01' in produced and 'nl' in produced
    edge.delete_source('de')
    catalog.sync('https://panel.example.com', None)
    left = {node['name'] for node in rows("SELECT name FROM nodes WHERE kind IN ('cloudflare','edge')")}
    # A deleted location takes its nodes with it; the other one survives.
    assert not [name for name in left if name.startswith('de-cloudflare-')] and 'nl' in left


def test_the_origin_node_is_named_after_the_platform():
    from app.nodes import origin_node_name
    catalog.ensure_origin('https://panel.example.com')
    node = catalog.get(origin_node_name())
    assert node and node['server'] == 'panel.example.com'
    assert json.loads(node['metadata'])['role'] == 'direct'


def test_a_subscription_can_be_limited_to_one_location():
    execute('DELETE FROM users')
    execute('DELETE FROM nodes')
    from app.nodes import upsert
    edge.add_ips('203.0.113.71', 'cloudflare')
    edge.save_source({'kind': 'ip', 'provider': 'cloudflare', 'location': 'de', 'host': 'de.example.com'}, '')
    edge.save_source({'kind': 'domain', 'host': 'nl.example.com', 'location': 'nl'}, '')
    catalog.sync('https://panel.example.com', None)
    upsert('panel-direct', 'railway', 'panel.example.com', 443, True, 'panel.example.com', 'panel.example.com', 'local', {})
    user = create_user(UserCreate(username='geo', protocol='all'))
    everywhere = render(user, 'https://panel.example.com', 'all')
    assert 'de-cloudflare-01' in everywhere and 'nl' in everywhere
    only_de = render(user, 'https://panel.example.com', 'all', active_nodes(location='de'))
    assert 'de.example.com' in only_de and 'nl.example.com' not in only_de


# ------------------------------------------------------------------------ panel
def test_the_panel_can_manage_sources_end_to_end():
    response = client.post('/api/edge/sources', headers=h(), json={
        'kind': 'domain', 'host': 'nl.example.com', 'location': 'nl', 'label': 'هلند'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['success'] is True and body['source']['id'] == 'nl'

    status = client.get('/api/edge', headers=h()).json()
    assert any(item['id'] == 'nl' for item in status['sources'])
    assert 'nl' in status['locations']
    assert any(node['name'] == 'nl' and node['provider'] == 'domain' for node in status['nodes'])
    assert any(node['name'] == 'nl' for node in client.get('/api/nodes', headers=h()).json())

    invalid = client.post('/api/edge/sources', headers=h(), json={'kind': 'ip', 'host': ''})
    assert invalid.status_code == 400 and 'Host' in invalid.json()['detail']

    removed = client.delete('/api/edge/sources/nl', headers=h()).json()
    assert removed['sources'] == []
    assert client.get('/api/edge', headers=h()).json()['sources'] == []


def test_the_panel_exposes_the_runtime_and_the_provider_catalog():
    runtime = client.get('/api/system/runtime', headers=h()).json()
    assert set(('id', 'label', 'host', 'has_tcp', 'data_dir')) <= set(runtime)
    providers = client.get('/api/edge/providers', headers=h()).json()
    ids = {item['id'] for item in providers['providers']}
    assert {'cloudflare', 'fastly', 'gcore', 'arvancloud', 'bunny', 'cloudfront', 'custom'} <= ids
    assert providers['runtime']['label']


def test_manual_ips_are_accepted_and_rejected_over_the_api():
    good = client.post('/api/edge/ips', headers=h(), json={'ips': '203.0.113.99, junk'})
    assert good.status_code == 200, good.text
    assert good.json()['added'] == ['203.0.113.99']
    bad = client.post('/api/edge/ips', headers=h(), json={'ips': 'junk'})
    assert bad.status_code == 400
    assert client.delete('/api/edge/ips/203.0.113.99', headers=h()).json()['success'] is True
