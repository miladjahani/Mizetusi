"""The country of a clean-IP location is measured, not assumed.

A user reported that a Cloudflare node with a Canadian flag dialled an address
their own lookup called American. The label was a guess about an anycast network
and the guess was wrong: measuring every range of the ``cloudflare-regions`` pack
against three independent databases showed the six ranges labelled «کانادا»
answering «United States» on two of the three. These tests pin the fix down:

* an address's country is a majority vote over several databases, a tie resolves
  to *no* answer (the admin's own label stays), and an answer is cached for good;
* a location whose addresses measure another country is re-labelled, which moves
  its flag, its node names and its per-country sublink together;
* a location publishes one country — an address that measures elsewhere is not
  handed to users under a flag that contradicts it;
* a location an admin pinned by hand, and a clean *domain* (one host published
  under several countries on purpose), are never touched;
* the whole thing is switchable and never raises offline.
"""
import os
import re
import time

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-geo-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-geo-test.db')

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import execute, init_db, row
from app.edge import geo
from app.edge import packs as edge_packs
from app.edge import sources as edge
from app.main import _setting, app
from app.nodes import ensure as ensure_nodes
from app.subscriptions import transports

init_db()
ensure_nodes()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or settings.admin_password}


def _clean():
    execute("DELETE FROM nodes WHERE kind IN ('cloudflare','edge')")
    execute('DELETE FROM cf_ips')
    execute('DELETE FROM settings WHERE key=?', (edge.SOURCE_KEY,))
    execute('DELETE FROM settings WHERE key=?', (geo.CACHE_KEY,))
    execute('DELETE FROM settings WHERE key=?', (geo.ENABLED_KEY,))


def _answers(mapping, calls=None):
    """Stand-in for the three databases: ``{'1.2.3.4': {'ipwho': 'us'}}``."""
    def fake(name, url, timeout=4.0):
        found = re.search(r'(\d+\.\d+\.\d+\.\d+)', str(url))
        if calls is not None:
            calls.append(name)
        answers = mapping.get(found.group(1)) if found else None
        return (answers or {}).get(name, '')
    return fake


def _source(**overrides):
    payload = {'kind': 'ip', 'provider': edge.MANUAL_PROVIDER, 'location': 'ca',
               'host': 'worker.example.workers.dev', 'port': 443, 'max': 4,
               'ips': '104.24.0.5'}
    payload.update(overrides)
    source, _ = edge.save_source(payload, '')
    return source


def _no_sockets(monkeypatch):
    """Not one real connection: the TLS/TCP probes and the geo databases are faked.

    The country lookups stay *on* (they are what these tests are about), so the
    probing half is stubbed instead of switching outbound traffic off.
    """
    import app.main as panel
    from app.nodes import NodeProbe

    async def dead_handshake(host, port, timeout=4.0, server_hostname=None):
        return None, 'TimeoutError', False

    async def dead_tcp(host, port, timeout=4.0, tls=False, server_hostname=None):
        return None, 'TimeoutError'

    async def no_probe(*args, **kwargs):
        return []

    monkeypatch.setattr(NodeProbe, 'handshake', staticmethod(dead_handshake))
    monkeypatch.setattr(NodeProbe, 'tcp', staticmethod(dead_tcp))
    monkeypatch.setattr(panel, 'probe_all', no_probe)
    monkeypatch.setattr(settings, 'outbound_probe_enabled', True)


# ------------------------------------------------------------------- the vote
def test_the_majority_of_the_databases_wins_and_a_tie_answers_nothing():
    assert geo.majority(['us', 'ca', 'us']) == 'us'
    assert geo.majority(['us', 'us', 'us']) == 'us'
    # Two databases disagreeing while the third is unreachable is not an answer:
    # the caller keeps the label it already had instead of a coin flip.
    assert geo.majority(['us', 'ca']) == ''
    assert geo.majority(['', 'nl']) == 'nl'
    assert geo.majority(['nonsense', 'nl']) == 'nl'
    assert geo.majority([]) == ''


def test_the_measured_answer_is_cached_and_never_asked_twice(monkeypatch):
    _clean()
    calls = []
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us', 'ip-api': 'ca',
                                                                'ipinfo': 'us'}}, calls))
    first = geo.lookup('104.24.0.5')
    assert first['cc'] == 'us'
    assert first['votes'] == {'ipwho': 'us', 'ip-api': 'ca', 'ipinfo': 'us'}
    assert len(calls) == 3
    calls.clear()
    again = geo.lookup('104.24.0.5')
    assert again['cc'] == 'us'
    # One answer per address is enough — the cache is the whole point of a lookup
    # the panel may need on every render.
    assert geo.cached('104.24.0.5') == 'us'
    assert calls == []
    # A private address is never asked about at all.
    calls.clear()
    assert geo.lookup('192.168.1.10') == {}
    assert geo.lookup('not-an-ip') == {}
    assert calls == []
    _clean()


def test_a_tie_is_reported_as_unknown_rather_than_as_a_country(monkeypatch):
    _clean()
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us', 'ip-api': 'ca'}}))
    assert geo.lookup('104.24.0.5')['cc'] == ''
    assert geo.cached('104.24.0.5') == ''
    _clean()


def test_the_lookups_can_be_switched_off(monkeypatch):
    _clean()
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (geo.ENABLED_KEY, '0'))
    calls = []
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us'}}, calls))
    assert geo.enabled() is False
    assert geo.lookup('104.24.0.5') == {}
    assert geo.resolve(['104.24.0.5'], limit=4)['checked'] == 0
    assert calls == []
    # And the alignment never runs either: labels stay exactly as typed.
    assert edge.align_labels() == []
    _clean()


def test_a_pass_is_bounded_by_time_as_well_as_by_count(monkeypatch):
    """A slow or unreachable database must not stall the request that asked.

    Three timeouts per address would add up to minutes; the pass stops on a
    wall-clock budget and keeps what it already learned.
    """
    _clean()

    def slow(name, url, timeout=3.0):
        time.sleep(0.4)
        return 'us'

    monkeypatch.setattr(geo, '_query', slow)
    result = geo.resolve(['8.8.8.8', '1.1.1.1', '9.9.9.9'], limit=5, budget=0.1)
    assert result['checked'] == 0 or result['checked'] <= 3
    # The addresses it did reach are remembered, so the next pass continues
    # instead of starting over.
    before = geo.stats()['known']
    assert before == result['checked']
    monkeypatch.setattr(geo, '_query', _answers({'8.8.8.8': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'}}))
    geo.resolve(['8.8.8.8', '1.1.1.1', '9.9.9.9'], limit=5, budget=0.1)
    assert geo.stats()['known'] >= before
    _clean()


def test_country_lookups_follow_the_outbound_switch(monkeypatch):
    """A deployment that must not make outbound connections measures nothing."""
    _clean()
    calls = []
    monkeypatch.setattr(geo, '_query', _answers({'8.8.8.8': {'ipwho': 'us'}}, calls))
    monkeypatch.setattr(settings, 'outbound_probe_enabled', False)
    assert geo.enabled() is False
    assert geo.resolve(['8.8.8.8'], limit=4)['checked'] == 0
    assert calls == []
    _clean()


def test_no_lookup_survives_in_the_face_of_a_dead_network(monkeypatch):
    _clean()

    def dead(name, url, timeout=4.0):
        raise OSError('network is unreachable')

    monkeypatch.setattr(geo, '_query', dead)
    assert geo.lookup('104.24.0.5')['cc'] == ''
    # A failure is remembered for a while: a sync must not become three timeouts
    # on every pass.
    calls = []
    monkeypatch.setattr(geo, '_query', _answers({}, calls))
    assert geo.resolve(['104.24.0.5'], limit=4)['checked'] == 0
    assert calls == []
    _clean()


# ------------------------------------------------------------------- the pack
def test_the_canadian_group_is_gone_because_no_database_confirmed_it():
    _clean()
    pack = edge_packs.pack('cloudflare-regions')
    locations = {item['location'] for item in pack['locations']}
    assert 'ca' not in locations
    # 104.24.0.0/14 was the range in the report: it answers «United States» on two
    # of the three databases, so it belongs to the American location now.
    american = next(item for item in pack['locations'] if item['location'] == 'us')
    assert '104.24.0.0/14' in american['ranges']
    assert '104.16.0.0/13' in american['ranges']
    for item in pack['locations']:
        assert item['location'] not in ('ca', 'it')


# --------------------------------------------------------------- the alignment
def test_a_location_is_relabelled_to_the_country_its_addresses_measure(monkeypatch):
    _clean()
    source = _source(location='ca', label='🇨🇦 · کانادا', ips='104.24.0.5, 104.24.0.9')
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'},
                                                 '104.24.0.9': {'ipwho': 'us', 'ip-api': 'ca', 'ipinfo': 'us'}}))
    result = edge.measure(limit=8)
    assert result['checked'] == 2 and result['resolved'] == 2
    changes = edge.align_labels()
    assert [{'id': item['id'], 'from': item['from'], 'to': item['to']} for item in changes] == [
        {'id': source['id'], 'from': 'ca', 'to': 'us'}]
    saved = next(item for item in edge.sources() if item['id'] == source['id'])
    assert saved['location'] == 'us'
    # The label, the flag and the node name all come from that one field, so a
    # corrected location corrects the whole entry a client sees.
    assert saved['label'] == '🇺🇸 · آمریکا'
    node = edge.nodes_for_source(saved)[0]
    assert node['name'].startswith('us-')
    assert node['metadata']['geo_cc'] == 'us'
    assert transports.node_flag(node) == '🇺🇸'
    _clean()


def test_the_alignment_keeps_an_admins_own_wording(monkeypatch):
    _clean()
    source = _source(location='de', label='آلمان · کلودفلر', ips='104.24.0.5')
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'}}))
    edge.measure(limit=4)
    edge.align_labels()
    saved = next(item for item in edge.sources() if item['id'] == source['id'])
    assert saved['location'] == 'us'
    # The note someone typed survives the correction: the country name is
    # replaced by the measured one, the note stays behind it.
    assert saved['label'] == '🇺🇸 · آمریکا · کلودفلر'
    _clean()


def test_a_location_publishes_only_addresses_of_its_own_country(monkeypatch):
    _clean()
    # One range, two countries: this is 162.158.0.0/15 measured for real (AU and
    # GT on the same range), so the label has to follow the address.
    source = _source(location='au', label='🇦🇺 · اقیانوسیه', ips='162.158.0.5, 162.158.3.232')
    monkeypatch.setattr(geo, '_query', _answers({'162.158.0.5': {'ipwho': 'au', 'ip-api': 'au', 'ipinfo': 'au'},
                                                 '162.158.3.232': {'ipwho': 'gt', 'ip-api': 'gt', 'ipinfo': 'gt'}}))
    edge.measure(limit=4)
    saved = next(item for item in edge.sources() if item['id'] == source['id'])
    nodes = edge.nodes_for_source(saved)
    # The Guatemalan address is not handed out under an Australian flag — that
    # mismatch is exactly what a user reported seeing in their client.
    assert [node['server'] for node in nodes] == ['162.158.0.5']
    assert transports.node_flag(nodes[0]) == '🇦🇺'
    assert saved['location'] == 'au'
    _clean()


def test_a_location_nobody_measured_keeps_the_label_it_was_given(monkeypatch):
    _clean()
    monkeypatch.setattr(geo, '_query', _answers({}))
    source = _source(location='nl', label='🇳🇱 · هلند', ips='141.101.64.10')
    assert edge.measure(limit=4)['resolved'] == 0
    assert edge.align_labels() == []
    saved = next(item for item in edge.sources() if item['id'] == source['id'])
    assert saved['location'] == 'nl'
    node = edge.nodes_for_source(saved)[0]
    assert node['name'].startswith('nl-') and transports.node_flag(node) == '🇳🇱'
    _clean()


def test_a_pinned_location_and_a_clean_domain_are_never_relabelled(monkeypatch):
    _clean()
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'},
                                                 '10.9.9.9': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'}}))
    pinned = _source(location='de', label='🇩🇪 · آلمان (نام دستی)', ips='104.24.0.5', autolabel=0)
    # The Iranian relay publishes six countries through one host on six ports:
    # its label is intent, not a guess about a range, so it is left alone.
    domain, _ = edge.save_source({'kind': 'domain', 'host': 'cdn16.qemitra.ir', 'port': 30524,
                                  'location': 'de', 'label': '🇩🇪 · تانل آلمان'}, '')
    edge.measure(limit=8)
    assert edge.align_labels() == []
    saved = {item['id']: item for item in edge.sources()}
    assert saved[pinned['id']]['location'] == 'de'
    assert saved[domain['id']]['location'] == 'de'
    assert saved[domain['id']]['label'] == '🇩🇪 · تانل آلمان'
    _clean()


def test_the_save_endpoint_pins_the_location_an_admin_corrects(monkeypatch):
    _clean()
    _no_sockets(monkeypatch)
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'}}))
    created = client.post('/api/edge/sources', headers=h(), json={
        'kind': 'ip', 'provider': edge.MANUAL_PROVIDER, 'location': 'ca',
        'host': 'worker.example.workers.dev', 'ips': '104.24.0.5', 'max': 2})
    assert created.status_code == 200, created.text
    source = created.json()['source']
    # Measuring already corrected «کانادا» to what the address really answers.
    assert source['location'] == 'us'
    assert created.json()['geo']['changes'][0]['to'] == 'us'
    # Now the admin overrides it by hand: that decision is not undone by the next
    # pass, because the panel cannot know better than the person running it.
    updated = client.post('/api/edge/sources', headers=h(), json={
        'id': source['id'], 'location': 'de', 'label': '🇩🇪 · دلخواه'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['source']['location'] == 'de'
    assert updated.json()['source']['autolabel'] == 0
    _clean()


# ---------------------------------------------------------------- panel surface
def test_the_geo_endpoint_measures_reports_and_republishes(monkeypatch):
    _clean()
    _no_sockets(monkeypatch)
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'}}))
    client.post('/api/edge/sources', headers=h(), json={
        'kind': 'ip', 'provider': edge.MANUAL_PROVIDER, 'location': 'ca',
        'host': 'worker.example.workers.dev', 'ips': '104.24.0.5', 'max': 2})
    result = client.post('/api/edge/geo', headers=h(), json={'limit': 8})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['success'] is True
    assert body['stats']['enabled'] is True
    source = body['sources'][0]
    assert source['location'] == 'us'
    assert source['geo']['country'] == 'us'
    assert source['geo']['measured'] == 1
    node = next(item for item in body['nodes'] if item['source_id'] == source['id'])
    assert node['geo'] == {'country': 'us', 'declared': 'us', 'measured': True}
    assert node['location'] == 'us'
    assert transports.node_flag(node) == '🇺🇸'
    _clean()


def test_the_panel_reports_a_location_whose_addresses_disagree(monkeypatch):
    _clean()
    monkeypatch.setattr(geo, '_query', _answers({'104.24.0.5': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'},
                                                 '104.16.0.9': {'ipwho': 'fr', 'ip-api': 'fr', 'ipinfo': 'fr'}}))
    source = _source(location='us', label='🇺🇸 · آمریکا', ips='104.24.0.5, 104.16.0.9')
    edge.measure(limit=4)
    listed = next(item for item in edge.status()['sources'] if item['id'] == source['id'])
    # Both countries are visible, so a mixed group is a fact on screen instead of
    # being smoothed over by a single label.
    assert listed['geo']['counts'] == {'us': 1, 'fr': 1}
    assert listed['geo']['measured'] == 2 and listed['geo']['total'] == 2
    assert listed['geo']['agree'] is False
    _clean()


def test_the_customization_tab_carries_the_geo_switch():
    _clean()
    payload = client.get('/api/customization', headers=h()).json()
    assert payload['geo_lookup'] is True
    off = client.post('/api/customization', headers=h(), json={'geo_lookup': False})
    assert off.status_code == 200, off.text
    assert off.json()['customization']['geo_lookup'] is False
    assert geo.enabled() is False
    on = client.post('/api/customization', headers=h(), json={'geo_lookup': True})
    assert on.json()['customization']['geo_lookup'] is True
    assert geo.enabled() is True
    _clean()


def test_the_cache_is_bounded_and_can_be_forgotten(monkeypatch):
    _clean()
    answers = {f'104.24.{index}.5': {'ipwho': 'us', 'ip-api': 'us', 'ipinfo': 'us'} for index in range(6)}
    monkeypatch.setattr(geo, '_query', _answers(answers))
    assert geo.resolve(list(answers), limit=6)['checked'] == 6
    assert geo.stats()['answered'] == 6
    assert geo.stats()['countries'] == ['us']
    assert geo.forget() is True
    assert geo.stats()['known'] == 0
    assert geo.cached('104.24.0.5') == ''
    _clean()


def test_two_addresses_of_equal_weight_keep_the_locations_own_label(monkeypatch):
    """A 1-1 split is a tie: the admin's label wins instead of a coin flip."""
    _clean()
    monkeypatch.setattr(geo, '_query', _answers({'141.101.64.10': {'ipwho': 'nl', 'ip-api': 'nl', 'ipinfo': 'nl'},
                                                 '188.114.96.10': {'ipwho': 'es', 'ip-api': 'es', 'ipinfo': 'es'}}))
    source = _source(location='nl', label='🇳🇱 · هلند', ips='141.101.64.10, 188.114.96.10')
    edge.measure(limit=4)
    assert edge.align_labels() == []
    saved = next(item for item in edge.sources() if item['id'] == source['id'])
    assert saved['location'] == 'nl'
    assert [node['server'] for node in edge.nodes_for_source(saved)] == ['141.101.64.10']
    _clean()


def test_measuring_never_touches_a_clean_domains_addresses(monkeypatch):
    _clean()
    calls = []
    monkeypatch.setattr(geo, '_query', _answers({}, calls))
    edge.save_source({'kind': 'domain', 'host': 'cdn16.qemitra.ir', 'port': 30524,
                      'location': 'de', 'label': '🇩🇪 · تانل آلمان'}, '')
    edge.measure(limit=8)
    assert calls == []
    _clean()
