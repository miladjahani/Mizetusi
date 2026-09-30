"""The Clash/Mihomo subscription is a whole YAML profile, not a JSON fragment.

Clash-family clients (Bettbox, Clash Verge, FlClash, Mihomo, Stash) import a
*config*: a top-level selector, one latency group per country, rules, a resolver
setup. The panel used to hand them ``{"proxies": [...]}`` — valid JSON, and not
something any of those clients can load — so what is asserted here is the shape
every working provider publishes:

* the body is YAML (``proxies:`` / ``proxy-groups:`` / ``rules:`` …), never JSON;
* it parses back into exactly the document the generator built, with no duplicate
  proxy name (Clash silently keeps the last one) and no group member that does
  not resolve;
* the groups follow the same countries the node *names* carry, and the panel's
  «پرچم کشور روی نام نودها» switch governs both;
* local and Iranian traffic goes direct, the rest through the selector, and
  filtering rules appear only when the user asked for them;
* the per-user config cap and the per-node subscription hold here too.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-clash-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-clash-test.db')

import pytest

from fastapi.testclient import TestClient

from app.config import settings
from app.core.models import UserCreate
from app.core.settings_store import store
from app.db import execute, init_db
from app.main import _setting, app
from app.nodes import catalog, upsert
from app.subscriptions import yamlout
from app.subscriptions.generator import (AUTO_GROUP, ORIGIN_GROUP, PROBE_URL, SELECT_GROUP,
                                         clash_document, render)
from app.users.service import create_user

init_db()
client = TestClient(app)

SHARED_OPTIONS = ('flags_enabled',)
COUNTRY_GROUPS = ('🇩🇪 آلمان', '🇳🇱 هلند')


def h():
    return {'X-Admin-Password': _setting('admin_password') or settings.admin_password}


def _seed_nodes():
    """Two nodes in two countries, so grouping has something to group."""
    execute('DELETE FROM nodes')
    upsert('de-edge', 'edge', 'de.example.com', 443, True, 'de.example.com', 'de.example.com',
           'domain', {'location': 'de', 'provider': 'domain'})
    upsert('nl-edge', 'edge', 'nl.example.com', 443, True, 'nl.example.com', 'nl.example.com',
           'domain', {'location': 'nl', 'provider': 'domain'})


@pytest.fixture(autouse=True)
def clean_state():
    saved = {key: _setting(key) for key in SHARED_OPTIONS}
    for key in SHARED_OPTIONS:
        store.delete(key)
    _seed_nodes()
    yield
    for key, value in saved.items():
        if value is None:
            store.delete(key)
        else:
            store.set(key, value)
    _seed_nodes()


_seed_nodes()


def _user(username, **kwargs):
    execute('DELETE FROM users WHERE username=?', (username,))
    return create_user(UserCreate(username=username, protocol='all', **kwargs))


def _groups(document):
    return {group['name']: group for group in document['proxy-groups']}


# --------------------------------------------------------------------- the body
def test_the_clash_subscription_is_yaml_and_never_json():
    user = _user('clashuser')
    response = client.get(f"/sub/{user['uuid']}?target=clash")
    assert response.status_code == 200, response.text
    text = response.text
    assert response.headers['x-nexus-format'] == 'clash'
    assert not text.lstrip().startswith('{')
    assert '"proxies"' not in text and 'outbounds' not in text
    for key in ('mixed-port:', 'mode: rule', 'dns:', 'proxies:', 'proxy-groups:', 'rules:'):
        assert key in text, key
    # The body is the document, serialised once — one code path, one answer.
    prefix = _setting('sub_prefix') or ''
    assert text == yamlout.dump(clash_document(user, prefix=prefix))


def test_the_profile_parses_back_into_the_document_that_was_built():
    yaml = pytest.importorskip('yaml')
    user = _user('clashyaml')
    document = clash_document(user)
    parsed = yaml.safe_load(yamlout.dump(document))
    assert parsed == document

    names = [proxy['name'] for proxy in parsed['proxies']]
    # A repeated name would silently drop a node, so the generator disambiguates.
    assert len(names) == len(set(names))
    known = set(names) | set(_groups(parsed)) | {'DIRECT'}
    for group in parsed['proxy-groups']:
        assert group['proxies']
        assert set(group['proxies']) <= known
    # Every group a client can land on is reachable from the selector.
    selector = _groups(parsed)[SELECT_GROUP]
    assert selector['type'] == 'select' and 'DIRECT' in selector['proxies']
    assert set(_groups(parsed)) - {SELECT_GROUP} <= set(selector['proxies'])


# ------------------------------------------------------------------- the groups
def test_every_node_is_grouped_under_its_own_country():
    user = _user('clashgroups')
    document = clash_document(user)
    groups = _groups(document)
    assert set(COUNTRY_GROUPS) <= set(groups)
    names = {proxy['name'] for proxy in document['proxies']}
    for label in COUNTRY_GROUPS:
        assert groups[label]['type'] == 'url-test'
        assert groups[label]['url'] == PROBE_URL
        assert set(groups[label]['proxies']) <= names
        # The country group holds that country's nodes and nothing else: a group
        # name is composed exactly like the node names inside it.
        flag = label.split(' ', 1)[0]
        assert all(member.startswith(flag + ' ') for member in groups[label]['proxies'])

    # The panel switch governs the group names the same way it governs links.
    assert client.post('/api/customization', headers=h(), json={'flags_enabled': '0'}).status_code == 200
    plain = set(_groups(clash_document(user)))
    assert {'آلمان', 'هلند'} <= plain
    assert '🇩🇪 آلمان' not in plain
    assert ORIGIN_GROUP not in plain and AUTO_GROUP in plain


# -------------------------------------------------------------------- the rules
def test_iranian_traffic_goes_direct_and_filtering_is_opt_in():
    user = _user('clashrules')
    rules = clash_document(user)['rules']
    assert rules[-1] == f'MATCH,{SELECT_GROUP}'
    assert 'GEOIP,LAN,DIRECT' in rules and 'GEOIP,IR,DIRECT' in rules
    assert 'DOMAIN-SUFFIX,ir,DIRECT' in rules
    # Nothing was asked for, so nothing is rejected on the user's behalf.
    assert not [rule for rule in rules if rule.endswith(',REJECT')]

    filtered = clash_document(_user('clashads', block_ads=True, block_porn=True))['rules']
    assert 'DOMAIN-SUFFIX,doubleclick.net,REJECT' in filtered
    assert 'DOMAIN-SUFFIX,pornhub.com,REJECT' in filtered
    assert filtered[-1] == f'MATCH,{SELECT_GROUP}'


# --------------------------------------------------------------- caps and nodes
def test_the_config_cap_holds_for_the_clash_profile_too():
    user = _user('clashcap', max_configs=5)
    document = clash_document(user)
    assert len(document['proxies']) == 5
    names = {proxy['name'] for proxy in document['proxies']}
    for group in document['proxy-groups']:
        assert set(group['proxies']) <= names | set(_groups(document)) | {'DIRECT'}


def test_a_per_node_clash_subscription_carries_only_that_node():
    user = _user('clashnode')
    node = catalog.get('de-edge')
    document = clash_document(user, nodes=[node], include_extras=False)
    assert document['proxies']
    assert all(proxy['name'].startswith('🇩🇪 de-edge') for proxy in document['proxies'])
    assert set(_groups(document)) == {SELECT_GROUP, AUTO_GROUP, '🇩🇪 آلمان'}
    # The whole profile travels for one node too: a bare proxy list is not a config.
    text = render(user, 'https://panel.example.com', 'clash', nodes=[node], include_extras=False)
    assert 'proxy-groups:' in text and 'rules:' in text


def test_published_proxies_carry_the_users_own_credential_and_a_real_probe():
    user = _user('clashcred')
    document = clash_document(user)
    # A published proxy never carries an empty or shared credential.
    for proxy in document['proxies']:
        assert proxy.get('uuid') or proxy.get('password'), proxy.get('name')
    latency = [group for group in document['proxy-groups'] if group.get('type') == 'url-test']
    assert latency and all(group['url'] == PROBE_URL for group in latency)
    assert all(isinstance(group['interval'], int) and group['interval'] > 0 for group in latency)
