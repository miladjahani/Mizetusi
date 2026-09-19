"""Ready-made node samples: different settings, one click.

Assembling a clean-IP node by hand means knowing a provider's anycast addresses,
which Cloudflare ports also answer HTTPS, and what SNI the CDN expects. The
samples ship those combinations — and they must be *safe*: never overwriting an
existing node, and never being wiped by the automatic catalog rebuild.
"""
import json
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-samples-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-samples-test.db')

import pytest

from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.db import execute, rows, init_db
from app.edge import samples as edge_samples
from app.main import _setting, app
from app.nodes import catalog, ensure as ensure_nodes

init_db()
ensure_nodes()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


@pytest.fixture(autouse=True)
def clean_sample_nodes():
    execute("DELETE FROM nodes WHERE source='sample'")
    yield
    execute("DELETE FROM nodes WHERE source='sample'")


# ------------------------------------------------------------------- definitions
def test_every_sample_is_a_valid_node():
    items = edge_samples.catalog('panel.example.com')
    ids = [item['id'] for item in items]
    assert len(ids) == len(set(ids))
    for item in items:
        assert item['label'] and item['note']
        for node in item['nodes']:
            assert node['name'].startswith('sample-')
            assert node['kind'] in ('railway', 'cloudflare', 'edge')
            assert 1 <= int(node['port']) <= 65535
            assert node['server'] and '{host}' not in node['server']
            assert int(node['tls']) in (0, 1)
            assert '{host}' not in str(node.get('sni') or '')


def test_each_sample_defaults_to_an_sni_that_can_actually_validate():
    # Behind a Worker the Cloudflare samples must carry the Worker hostname (that
    # is the domain the CDN serves the origin on); the origin node and the other
    # CDNs must not, because they would never serve a *.workers.dev certificate.
    items = {item['id']: item for item in
             edge_samples.catalog('panel.example.com', 'https://edge.example.workers.dev')}
    assert items['cf-worker']['node']['sni'] == 'edge.example.workers.dev'
    assert items['clean-domain']['node']['sni'] == 'edge.example.workers.dev'
    assert items['origin']['node']['sni'] == 'panel.example.com'
    for key in ('fastly', 'gcore', 'arvan'):
        assert items[key]['node']['sni'] == 'panel.example.com'
    # Without a Worker every sample falls back to the deployment's own hostname.
    for item in edge_samples.catalog('panel.example.com'):
        if item['node']['kind'] == 'railway':
            continue
        assert item['node']['sni'] in ('panel.example.com', '')


def test_the_samples_cover_the_settings_that_matter():
    catalog_ = {item['id']: item for item in edge_samples.catalog('panel.example.com')}
    assert set(catalog_) >= {'clean-domain', 'cf-worker', 'cf-alt-ports', 'fastly', 'gcore',
                             'arvan', 'plain-ws', 'origin'}
    # Alternative Cloudflare ports in one entry, one node each.
    ports = [node['port'] for node in catalog_['cf-alt-ports']['nodes']]
    assert ports == [2053, 2087, 2096, 8443]
    # The plain-WS fallback really has TLS off, the clean domain is a domain node.
    assert catalog_['plain-ws']['node']['tls'] == 0
    assert catalog_['clean-domain']['node']['kind'] == 'edge'
    # Different providers, so the locations really are distinct.
    assert {catalog_[key]['node']['provider'] for key in ('cf-worker', 'fastly', 'gcore', 'arvan')} == {
        'cloudflare', 'fastly', 'gcore', 'arvancloud'}


def test_an_unknown_sample_is_reported_not_guessed():
    assert edge_samples.nodes_for('nope', 'panel.example.com') is None
    assert edge_samples.nodes_for('cf-worker', 'panel.example.com')[0]['sni'] == 'panel.example.com'


def test_sample_nodes_are_recognised_including_legacy_metadata():
    assert edge_samples.is_sample({'metadata': json.dumps({'role': 'sample', 'sample': 'cf-worker'})})
    assert edge_samples.is_sample({'metadata': "{'role': 'sample', 'sample': 'cf-worker'}"})
    assert not edge_samples.is_sample({'metadata': json.dumps({'role': 'clean-ip'})})
    assert not edge_samples.is_sample({})


# ---------------------------------------------------------------------- the API
def test_the_panel_lists_adds_and_clears_the_samples():
    listing = client.get('/api/nodes/samples', headers=h()).json()
    assert listing['samples'] and all(item['added'] is False for item in listing['samples'])
    assert listing['host']

    created = client.post('/api/nodes/samples', headers=h(), json={'ids': ['cf-alt-ports', 'clean-domain']})
    assert created.status_code == 200, created.text
    body = created.json()
    assert len(body['created']) == 5  # four alternative ports + the clean domain
    assert body['probed'] == 5

    nodes = {node['name']: node for node in client.get('/api/nodes', headers=h()).json()}
    assert nodes['sample-cf-2053']['enabled'] == 1
    assert nodes['sample-cf-2053']['port'] == 2053
    assert nodes['sample-clean-domain']['kind'] == 'edge'

    again = client.get('/api/nodes/samples', headers=h()).json()
    marked = {item['id']: item for item in again['samples']}
    assert marked['cf-alt-ports']['added'] is True and marked['fastly']['added'] is False

    duplicate = client.post('/api/nodes/samples', headers=h(), json={'ids': ['cf-alt-ports']})
    assert duplicate.status_code == 400

    everything = client.post('/api/nodes/samples', headers=h(), json={'all': True}).json()
    assert everything['success'] is True and everything['created']
    assert everything['skipped']  # the ones that already existed are not recreated

    cleared = client.delete('/api/nodes/samples', headers=h()).json()
    remaining = {node['name'] for node in cleared['nodes']}
    assert 'sample-cf-worker' in cleared['removed'] and 'sample-cf-worker' not in remaining
    assert not any(name.startswith('sample-') for name in remaining)


def test_a_hand_made_node_survives_clearing_the_samples():
    client.post('/api/nodes', headers=h(), json={'name': 'mine', 'server': 'mine.example.com',
                                                 'kind': 'edge', 'port': 443, 'tls': 1,
                                                 'sni': 'mine.example.com', 'host': 'mine.example.com'})
    client.post('/api/nodes/samples', headers=h(), json={'ids': ['origin']})
    names = {node['name'] for node in client.delete('/api/nodes/samples', headers=h()).json()['nodes']}
    assert 'mine' in names and 'sample-origin' not in names
    execute('DELETE FROM nodes WHERE name=?', ('mine',))


def test_a_catalog_sync_never_erases_a_sample_node():
    """The rebuild owns its own rows: a node the admin added must not vanish."""
    client.post('/api/nodes/samples', headers=h(), json={'ids': ['cf-worker']})
    before = catalog.get('sample-cf-worker')
    assert before and before['enabled'] == 1
    catalog.sync('https://panel.example.com', None)
    after = catalog.get('sample-cf-worker')
    assert after and after['enabled'] == 1, 'a sample node must survive the automatic rebuild'
    execute("DELETE FROM nodes WHERE kind IN ('cloudflare','edge') AND source != 'sample'")


def test_the_created_node_carries_its_sample_identity():
    client.post('/api/nodes/samples', headers=h(), json={'ids': ['gcore']})
    row_ = rows("SELECT metadata FROM nodes WHERE name='sample-gcore'")[0]
    meta = json.loads(row_['metadata'])
    assert meta['role'] == 'sample' and meta['sample'] == 'gcore'
    assert meta['provider'] == 'gcore'
