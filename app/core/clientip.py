"""The real client address behind the deployment's proxies.

nginx normally answers this with ``real_ip`` + ``set_real_ip_from``: an explicit
list of proxies whose forwarded hop may be believed. NEXUS ships no nginx —
Railway/Cloudflare terminate TLS and ``app/main.py`` is the single 443 edge that
serves the panel and every published transport — so the same rule lives here.
It is not log hygiene: the login throttle keys on this address (``app/main.py``),
so a forgeable ``X-Forwarded-For`` is a brute-force bypass, not a cosmetic detail.

The rules, in order:

* the direct TCP peer is the truth, and ``X-Forwarded-For`` is only read when
  that peer is a **trusted proxy** (loopback/private by default, plus any range
  the operator adds in «شبکه و لبه → IP واقعی کاربر»). A public peer is never
  trusted implicitly, because the address a client writes into that header is
  client-controlled by definition;
* ``CF-Connecting-IP`` is authoritative when the peer really is Cloudflare
  (checked against Cloudflare's published ranges): Cloudflare guarantees exactly
  one value there, while it only *appends* to ``X-Forwarded-For``;
* otherwise the chain is walked right-to-left and the first address that is not a
  trusted proxy is the client — the rule nginx's real_ip module applies.

``peer`` in the result is the raw socket peer. The container no longer starts
uvicorn with ``--forwarded-allow-ips='*'`` (see ``Dockerfile``): that rewrite is
what replaced the peer with an unverifiable header value in the first place.
"""
import functools
import ipaddress

# Trusted by default: the private/loopback ranges a platform proxy dials in from
# (Railway, Render, Fly, a Docker bridge, a same-host nginx). A public peer is
# never trusted implicitly — an operator behind a custom reverse proxy adds its
# range in the panel instead.
DEFAULT_TRUSTED = (
    '127.0.0.0/8', '::1/128',
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '100.64.0.0/10',  # carrier-grade NAT: the address Railway/Docker peers use
    '169.254.0.0/16', 'fe80::/10', 'fc00::/7',
)

# Cloudflare's published edge ranges (cloudflare.com/ips-v4 · ips-v6). Used only
# to decide whether the peer *is* Cloudflare, so its authoritative
# ``CF-Connecting-IP`` header can be believed.
CLOUDFLARE = (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
    '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32',
    '2405:8100::/32', '2a06:98c0::/29', '2c0f:f248::/32',
)

CLOUDFLARE_HEADER = 'cf-connecting-ip'
FORWARDED_HEADER = 'x-forwarded-for'


def _networks(values):
    out = []
    for value in values or ():
        try:
            out.append(ipaddress.ip_network(str(value).strip(), strict=False))
        except ValueError:
            continue
    return out


def address(value):
    """One IP address, or ``None`` for anything else (a hostname is not an IP)."""
    try:
        return ipaddress.ip_address(str(value or '').strip())
    except ValueError:
        return None


def _tokens(text):
    """One token per IP/CIDR — every separator an operator is likely to type."""
    raw = str(text or '').replace(';', ',').replace(' ', ',').replace('\n', ',')
    return [item.strip() for item in raw.split(',') if item.strip()]


def parse_cidrs(text):
    """Operator-supplied ranges, as networks (unusable tokens are dropped)."""
    return _networks(_tokens(text))


def invalid_cidrs(text):
    """The tokens of ``text`` that are not a usable IP or CIDR (for validation)."""
    return [token for token in _tokens(text) if not _networks([token])]


def _within(value, networks):
    return any(value in network for network in networks)


@functools.lru_cache(maxsize=32)
def trusted_networks(extra=''):
    """The default ranges plus the operator's, cached per configuration string."""
    return tuple(_networks(DEFAULT_TRUSTED)) + tuple(parse_cidrs(extra))


@functools.lru_cache(maxsize=1)
def cloudflare_networks():
    return tuple(_networks(CLOUDFLARE))


def forwarded_chain(header):
    """Every valid address in an ``X-Forwarded-For`` value, in wire order."""
    out = []
    for item in str(header or '').split(','):
        found = address(item)
        if found is not None:
            out.append(found)
    return out


def resolve(request, extra_trusted='', trust_headers=True):
    """The client address of one request, plus the evidence behind the answer.

    ``ip`` is what to log and key the throttle on; ``peer`` is the raw TCP peer;
    ``source`` names the rule that answered (``cloudflare`` / ``forwarded`` /
    ``peer``); ``chain`` is the forwarded hops as received; and ``spoofed`` is
    True when the first hop — the one a client writes itself — is not the address
    that was actually used. The panel shows those fields, so an operator can see
    *why* a given address was believed instead of trusting a black box.

    ``peer`` is reported exactly as the socket reports it, so an exotic peer (a
    unix socket, a test client) is passed through rather than replaced by a
    placeholder — but only a real IP can ever be *trusted*.
    """
    peer_raw = str(getattr(getattr(request, 'client', None), 'host', '') or '')
    peer = address(peer_raw)
    forwarded = forwarded_chain(request.headers.get(FORWARDED_HEADER))
    networks = trusted_networks(str(extra_trusted or ''))
    trusted_peer = peer is not None and _within(peer, networks)
    from_cloudflare = (bool(trust_headers) and peer is not None
                       and _within(peer, cloudflare_networks()))
    answer = None
    source = 'peer'
    if not trust_headers:
        # The operator turned CDN/proxy headers off: the socket peer is the only
        # address this deployment is willing to report.
        return {'ip': peer_raw, 'peer': peer_raw, 'source': 'peer', 'cloudflare': False,
                'trusted_peer': trusted_peer, 'chain': [str(item) for item in forwarded],
                'spoofed': bool(forwarded), 'trust_headers': False}
    if from_cloudflare:
        claimed = address(request.headers.get(CLOUDFLARE_HEADER))
        if claimed is not None:
            answer, source = claimed, 'cloudflare'
    if answer is None and trusted_peer:
        # Right-to-left: the first hop that is not one of our own proxies is the
        # caller; when every hop is a trusted proxy the leftmost is the origin.
        answer = next((item for item in reversed(forwarded) if not _within(item, networks)), None)
        if answer is not None:
            source = 'forwarded'
        elif forwarded:
            answer, source = forwarded[0], 'forwarded'
    if answer is None:
        answer, source = peer, 'peer'  # may be None: the raw peer is used as-is
    leftmost = forwarded[0] if forwarded else None
    resolved = str(answer) if answer is not None else peer_raw
    return {
        'ip': resolved,
        'peer': peer_raw,
        'source': source,
        'cloudflare': from_cloudflare,
        'trusted_peer': trusted_peer,
        'chain': [str(item) for item in forwarded],
        'spoofed': leftmost is not None and str(leftmost) != resolved,
        'trust_headers': True,
    }


def client_ip(request, extra_trusted='', trust_headers=True):
    """Just the address — what the login throttle and the audit log use."""
    return resolve(request, extra_trusted, trust_headers)['ip'] or 'unknown'
