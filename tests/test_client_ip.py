"""Who the panel believes it is talking to.

``X-Forwarded-For`` is written by the client, so the header alone can never be an
identity. These tests pin the three rules ``app/core/clientip.py`` applies — the
same ones nginx's ``real_ip`` module applies — and the two consequences that are
actually user-visible: what the audit log records, and whether an attacker can
evade the login throttle by rotating a forged hop.
"""
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-clientip-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-clientip-test.db')

from fastapi.testclient import TestClient

from app.config import settings as cfg
from app.core import clientip
from app.db import init_db, rows
from app.main import _setting, app

init_db()
client = TestClient(app)

CLOUDFLARE_PEER = '104.16.0.1'
PUBLIC_PEER = '198.51.100.7'
TRUSTED_PEER = '10.0.0.9'
REAL_CLIENT = '198.51.100.7'


class _Peer:
    def __init__(self, host):
        self.host = host


class _Request:
    """Only the two attributes the resolver reads."""

    def __init__(self, peer, headers):
        self.client = _Peer(peer) if peer is not None else None
        self.headers = dict(headers or {})


def req(peer, **headers):
    return _Request(peer, {name.replace('_', '-'): value for name, value in headers.items()})


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


# --------------------------------------------------------------------- the rules
def test_an_untrusted_peer_cannot_claim_an_identity():
    """A public peer talking straight to the panel: its header is ignored."""
    state = clientip.resolve(req(PUBLIC_PEER, x_forwarded_for='203.0.113.9'))
    assert state['ip'] == PUBLIC_PEER
    assert state['source'] == 'peer'
    assert state['trusted_peer'] is False
    # The panel says so out loud instead of silently believing the hop.
    assert state['spoofed'] is True


def test_a_trusted_platform_proxy_is_followed_right_to_left():
    state = clientip.resolve(req(TRUSTED_PEER, x_forwarded_for=REAL_CLIENT + ', 10.0.0.1'))
    assert state['ip'] == REAL_CLIENT
    assert state['source'] == 'forwarded'
    assert state['trusted_peer'] is True
    assert state['spoofed'] is False


def test_a_forged_first_hop_behind_a_proxy_is_skipped():
    """The leftmost entry is always whatever the client sent — never the answer."""
    state = clientip.resolve(req(TRUSTED_PEER, x_forwarded_for='9.9.9.9, ' + REAL_CLIENT + ', 10.0.0.1'))
    assert state['ip'] == REAL_CLIENT
    assert state['spoofed'] is True


def test_cloudflares_own_header_beats_an_appended_hop():
    """Cloudflare appends the real address; the client's own hop lands first."""
    state = clientip.resolve(req(CLOUDFLARE_PEER, x_forwarded_for='9.9.9.9, ' + REAL_CLIENT,
                                 cf_connecting_ip=REAL_CLIENT))
    assert state['ip'] == REAL_CLIENT
    assert state['source'] == 'cloudflare'
    assert state['cloudflare'] is True
    assert state['spoofed'] is True


def test_the_cloudflare_header_is_only_believed_from_cloudflare():
    """Anyone can send ``CF-Connecting-IP``; only Cloudflare's own peers may claim it."""
    state = clientip.resolve(req(PUBLIC_PEER, x_forwarded_for='9.9.9.9',
                                 cf_connecting_ip='203.0.113.9'))
    assert state['ip'] == PUBLIC_PEER
    assert state['source'] == 'peer'
    assert state['cloudflare'] is False


def test_an_operator_trusted_range_is_honoured():
    """A same-host nginx on a public address is opt-in, exactly like set_real_ip_from."""
    state = clientip.resolve(req(PUBLIC_PEER, x_forwarded_for=REAL_CLIENT),
                             extra_trusted='198.51.100.0/24')
    assert state['ip'] == REAL_CLIENT
    assert state['source'] == 'forwarded'


def test_an_unparseable_peer_is_passed_through_untouched():
    state = clientip.resolve(req('unix-socket', x_forwarded_for=REAL_CLIENT))
    assert state['ip'] == 'unix-socket'
    assert state['trusted_peer'] is False


def test_a_private_ipv6_peer_is_trusted():
    state = clientip.resolve(req('fd00::2', x_forwarded_for='2001:db8::1'))
    assert state['ip'] == '2001:db8::1'
    assert state['source'] == 'forwarded'


def test_no_headers_means_the_peer():
    state = clientip.resolve(req(TRUSTED_PEER))
    assert state['ip'] == TRUSTED_PEER
    assert state['source'] == 'peer'
    assert state['chain'] == []
    assert state['spoofed'] is False


def test_every_hop_trusted_falls_back_to_the_origin_hop():
    state = clientip.resolve(req(TRUSTED_PEER, x_forwarded_for='10.1.1.1, 10.0.0.1'))
    assert state['ip'] == '10.1.1.1'
    assert state['source'] == 'forwarded'


def test_turning_header_trust_off_reports_only_the_socket_peer():
    state = clientip.resolve(req(TRUSTED_PEER, x_forwarded_for=REAL_CLIENT), trust_headers=False)
    assert state['ip'] == TRUSTED_PEER
    assert state['source'] == 'peer'
    assert state['trust_headers'] is False
    # The evidence is still reported, so the operator sees what was ignored.
    assert state['chain'] == [REAL_CLIENT]
    assert state['spoofed'] is True


# ------------------------------------------------------------------- the wiring
def test_the_audit_log_records_what_the_resolver_decided():
    """The route must use the resolver, not ``request.client.host`` or the header."""
    client.post('/api/login', json={'password': 'definitely-not-the-password'},
                headers={'X-Forwarded-For': '203.0.113.9'})
    latest = rows("SELECT detail FROM audit_logs WHERE action='auth.login' ORDER BY id DESC LIMIT 1")
    assert latest, 'a failed login must be audited'
    detail = str(latest[0]['detail'])
    assert '203.0.113.9' not in detail, 'a header the client wrote reached the audit log'
    assert 'testclient' in detail


def test_a_request_from_cloudflares_edge_is_attributed_to_cf_connecting_ip():
    """End to end, over HTTP, the way the deployment really receives it."""
    edge = TestClient(app, client=(CLOUDFLARE_PEER, 443))
    body = edge.get('/api/net/client-ip', headers={**h(), 'X-Forwarded-For': '9.9.9.9',
                                                  'CF-Connecting-IP': REAL_CLIENT}).json()
    assert body['ip'] == REAL_CLIENT
    assert body['source'] == 'cloudflare'
    assert body['cloudflare'] is True
    assert body['spoofed'] is True
    # …and the forged hop reaches neither the audit trail nor the throttle key.
    edge.post('/api/login', json={'password': 'definitely-not-the-password'},
              headers={'X-Forwarded-For': '9.9.9.9', 'CF-Connecting-IP': REAL_CLIENT})
    latest = rows("SELECT detail FROM audit_logs WHERE action='auth.login' ORDER BY id DESC LIMIT 1")
    assert '9.9.9.9' not in str(latest[0]['detail'])
    assert REAL_CLIENT in str(latest[0]['detail'])


def test_a_direct_connection_cannot_impersonate_cloudflare():
    """A VPS panel on the open internet: the CF header is just a client header."""
    direct = TestClient(app, client=(PUBLIC_PEER, 51234))
    body = direct.get('/api/net/client-ip', headers={**h(), 'X-Forwarded-For': '203.0.113.9',
                                                    'CF-Connecting-IP': '203.0.113.9'}).json()
    assert body['ip'] == PUBLIC_PEER
    assert body['source'] == 'peer'
    assert body['cloudflare'] is False


def test_the_endpoint_reports_the_evidence():
    response = client.get('/api/net/client-ip', headers=h())
    assert response.status_code == 200
    body = response.json()
    assert body['success'] is True
    assert body['ip']
    assert body['source'] in ('peer', 'forwarded', 'cloudflare')
    assert body['trust_cdn_headers'] is True
    assert body['default_trusted'] and '127.0.0.0/8' in body['default_trusted']
    assert body['cloudflare_ranges'] >= 15


def test_the_endpoint_needs_a_session():
    assert client.get('/api/net/client-ip').status_code == 401


def test_the_trust_switch_and_ranges_are_stored():
    saved = client.post('/api/net/client-ip',
                        json={'trust_client_ip': '0', 'trusted_proxy_cidrs': '203.0.113.0/24'},
                        headers=h())
    assert saved.status_code == 200
    assert set(saved.json()['changed']) == {'trust_client_ip', 'trusted_proxy_cidrs'}
    assert saved.json()['trust_cdn_headers'] is False
    assert saved.json()['trusted_proxy_cidrs'] == '203.0.113.0/24'
    assert client.get('/api/settings', headers=h()).json()['trust_client_ip'] == '0'
    # Back to the default so the rest of the suite sees the shipped behaviour.
    client.post('/api/net/client-ip', json={'trust_client_ip': '1', 'trusted_proxy_cidrs': ''},
                headers=h())


def test_an_unusable_range_is_rejected_instead_of_dropped():
    response = client.post('/api/net/client-ip', json={'trusted_proxy_cidrs': 'not-a-cidr'},
                           headers=h())
    assert response.status_code == 400
    assert 'not-a-cidr' in response.json()['detail']


def test_the_container_never_lets_uvicorn_overwrite_the_peer():
    """With ``--forwarded-allow-ips='*'`` the raw peer is gone before any handler runs,
    so the resolver would have nothing to check the headers against."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'Dockerfile'), encoding='utf-8') as handle:
        dockerfile = handle.read()
    assert "--forwarded-allow-ips='*'" not in dockerfile
    assert "--forwarded-allow-ips='127.0.0.1'" in dockerfile


def test_the_panel_ships_the_card_that_shows_all_this():
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'templates', 'index.html'), encoding='utf-8') as handle:
        html = handle.read()
    for element in ('tlIpStats', 'tlIpTrust', 'tlIpCidrs', 'tlIpSave', 'tlIpRefresh', 'tlIpOut'):
        assert f'id="{element}"' in html, element
