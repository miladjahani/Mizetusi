"""Edge sources — the clean IPs and clean domains a subscription is built from.

This is what makes NEXUS multi-location. Each **source** is one way clients can
reach the Xray origin:

* ``ip``   — a pool of clean edge IPs from a CDN (Cloudflare, Fastly, Gcore,
  آروان, Bunny, CloudFront) or a hand-written list, dialled by IP with the
  source's own Host/SNI (the classic anti-blocking setup);
* ``domain`` — a clean domain that already fronts the origin (a custom domain
  behind Cloudflare, a CDN hostname, or another server of yours). It is published
  as its own node, so a user can subscribe to *just that location*.

Every source carries a ``location`` label, and every node it produces is named
after it (``de-cf-01``, ``nl-vps``). Because one subscription entry is a
*(node × transport profile)* pair, a new source automatically gets the entire
matrix — VLESS, VMess, Trojan and all four Shadowsocks ciphers, in both edge path
shapes — with no extra configuration, and the labels tell the user where they are
dialling.
"""

import ipaddress
import json
import re
import time
import urllib.request

from app.config import settings
from app.db import rows, row, execute
from app import runtime

# --------------------------------------------------------------------- providers
# ``format`` is how a published list is parsed:
#   text-lines  one CIDR/IP per line
#   json        a JSON object with the addresses under ``field``
#   json-list   a JSON array of addresses
#   aws         AWS ip-ranges.json: ``field`` entries filtered by ``match``
#   static      the provider ships its known edge addresses here (``seed``):
#               plenty of CDNs publish no machine-readable list at all, so a
#               curated seed is the only honest way to offer the location — the
#               probe still decides which addresses are actually usable.
# ``live`` False means ``urls`` is documentation only (there is nothing to parse).
PROVIDERS = (
    {'id': 'cloudflare', 'label': 'Cloudflare', 'format': 'text-lines', 'port': 443, 'tls': 1,
     'urls': ['https://www.cloudflare.com/ips-v4'],
     'note': 'رنج‌های رسمی کلودفلر؛ مناسب آی‌پی تمیز با Worker یا دامنهٔ پشت کلودفلر.'},
    {'id': 'fastly', 'label': 'Fastly', 'format': 'json', 'field': 'addresses', 'port': 443, 'tls': 1,
     'urls': ['https://api.fastly.com/public-ip-list'],
     'note': 'شبکهٔ anycast فستلی — برای ایران معمولاً پینگ پایین‌تری از کلودفلر دارد.'},
    {'id': 'gcore', 'label': 'Gcore CDN', 'format': 'json', 'field': 'addresses', 'port': 443, 'tls': 1,
     'urls': ['https://api.gcore.com/cdn/public-ip-list'],
     'note': 'آی‌پی‌های لبهٔ Gcore (تک‌آدرسی)؛ نقطهٔ ورود تمیز و متنوع جغرافیایی.'},
    {'id': 'arvancloud', 'label': 'ابر آروان', 'format': 'text-lines', 'port': 443, 'tls': 1,
     'urls': ['https://www.arvancloud.ir/fa/ips.txt'],
     'note': 'آی‌پی‌های آروان‌کلود؛ داخل ایران و بدون نیاز به تغییر DNS.'},
    {'id': 'bunny', 'label': 'Bunny CDN', 'format': 'json-list', 'port': 443, 'tls': 1,
     'urls': ['https://api.bunny.net/system/edgeserverlist'],
     'note': 'لیست سرورهای لبهٔ Bunny (متنوع از نظر کشور).'},
    {'id': 'cloudfront', 'label': 'AWS CloudFront', 'format': 'aws', 'field': 'prefixes',
     'ip_field': 'ip_prefix', 'match': {'service': 'CLOUDFRONT'}, 'port': 443, 'tls': 1,
     'urls': ['https://ip-ranges.amazonaws.com/ip-ranges.json'],
     'note': 'پیشوندهای CloudFront از فهرست رسمی AWS.'},
    # --- the wider CDN edge -------------------------------------------------
    # Every one of these is a real anycast edge in front of a huge share of the
    # web; a client that dials it with this deployment's Host/SNI reaches the
    # origin through a network path that may be far better than the panel's own.
    {'id': 'akamai', 'label': 'Akamai', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://techdocs.akamai.com/origin-ip-acl/docs/update-your-origin-ip-acl'],
     'seed': ['23.32.0.1', '23.192.0.1', '184.24.0.1', '2.16.0.1', '104.64.0.1'],
     'note': 'لبهٔ Akamai (بزرگ‌ترین شبکهٔ توزیع محتوا)؛ در ایران معمولاً پایدار و پرسرعت.'},
    {'id': 'google', 'label': 'Google CDN', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://www.gstatic.com/ipranges/goog.json'],
     'seed': ['142.250.0.1', '172.217.0.1', '216.58.192.1', '34.96.0.1'],
     'note': 'لبهٔ گوگل؛ مسیر خیلی خوب برای ایرانسل/همراه اول در ساعات شلوغی.'},
    {'id': 'azure', 'label': 'Azure Front Door', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://learn.microsoft.com/en-us/azure/frontdoor/front-door-faq'],
     'seed': ['152.199.0.1', '13.107.0.1', '204.79.197.1'],
     'note': 'Azure Front Door؛ نقطهٔ ورود مایکروسافت با پوشش خوب در خاورمیانه.'},
    {'id': 'edgio', 'label': 'Edgio (Limelight)', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://docs.edg.io/guides/configuration'],
     'seed': ['192.229.128.1', '68.142.64.1', '209.197.0.1'],
     'note': 'شبکهٔ Edgio/Limelight — هم‌اکنون بخش بزرگی از ترافیک مایکروسافت را حمل می‌کند.'},
    {'id': 'cdn77', 'label': 'CDN77', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://www.cdn77.com/network'],
     'seed': ['185.59.220.1', '185.93.0.1', '37.48.0.1'],
     'note': 'CDN77 (DataCamp) — لبهٔ اروپایی ارزان با PoP های متعدد.'},
    {'id': 'stackpath', 'label': 'StackPath', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://www.stackpath.com/products/edge-compute/'],
     'seed': ['151.139.0.1', '68.232.32.1', '209.235.0.1'],
     'note': 'StackPath؛ برای بعضی شبکه‌های ایران مسیر متفاوتی از کلودفلر می‌دهد.'},
    {'id': 'cachefly', 'label': 'CacheFly', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://www.cachefly.com/network/'],
     'seed': ['205.234.175.1', '66.204.0.1'],
     'note': 'CacheFly — لبهٔ کوچک ولی پایدار با پینگ کم در اروپا.'},
    {'id': 'keycdn', 'label': 'KeyCDN', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://www.keycdn.com/network'],
     'seed': ['185.146.168.1', '193.203.0.1'],
     'note': 'KeyCDN؛ PoP های اروپایی و خاورمیانه.'},
    {'id': 'imperva', 'label': 'Imperva / Incapsula', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://docs.imperva.com/bundle/cloud-application-security/page/more/ips.htm'],
     'seed': ['199.83.128.1', '198.143.32.1', '149.126.72.1', '45.60.0.1', '107.154.0.1'],
     'note': 'Imperva/Incapsula؛ رنج‌های ثابت و قابل‌اعتماد (مناسب فیلترشکن‌های سازمانی).'},
    {'id': 'sucuri', 'label': 'Sucuri', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://docs.sucuri.net/website-firewall/configuration/operational-questions/'],
     'seed': ['192.124.249.1', '185.93.228.1'],
     'note': 'Sucuri Firewall؛ لبهٔ کوچک با رنج‌های ثابت.'},
    {'id': 'edgecast', 'label': 'Verizon / Edgecast', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://docs.edgecast.com/cdn/Content/About/CDN_IP_Blocks.htm'],
     'seed': ['72.21.80.1', '152.195.0.1', '192.16.0.1', '68.232.32.1'],
     'note': 'Verizon Edgecast — لبهٔ قدیمی و پرقدرت سیسکو/ورایزن.'},
    {'id': 'cdnnetworks', 'label': 'CDNetworks', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://www.cdnetworks.com/'],
     'seed': ['117.18.0.1', '119.31.0.1'],
     'note': 'CDNetworks؛ پوشش خوب در آسیا و خاورمیانه.'},
    {'id': 'quantil', 'label': 'QUANTIL (ChinaNetCenter)', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://www.quantil.com/network/'],
     'seed': ['117.18.232.1', '124.108.0.1'],
     'note': 'QUANTIL؛ لبهٔ چینی‌-آمریکایی برای مسیرهای جایگزین.'},
    {'id': 'chinacache', 'label': 'ChinaCache', 'format': 'static', 'live': False, 'port': 443, 'tls': 1,
     'urls': ['https://en.chinacache.com/'],
     'seed': ['220.181.0.1', '118.26.0.1'],
     'note': 'ChinaCache؛ آخرین گزینهٔ مسیر آسیایی.'},
    {'id': 'custom', 'label': 'آی‌پی دستی', 'format': 'manual', 'port': 443, 'tls': 1, 'urls': [],
     'note': 'هر آی‌پی تمیزی که خودتان دارید را اینجا وارد کنید.'},
)

DEFAULT_PROVIDER = 'cloudflare'
MANUAL_PROVIDER = 'custom'
SOURCE_KEY = 'edge_sources'
MAX_SOURCE_NODES = 30


def provider(provider_id):
    wanted = str(provider_id or '').strip().lower()
    for item in PROVIDERS:
        if item['id'] == wanted:
            return item
    return None


def provider_ids():
    return [item['id'] for item in PROVIDERS]


# ------------------------------------------------------------------ list parsing
def _http_text(url, timeout=15):
    request = urllib.request.Request(url, headers={'User-Agent': 'NEXUS-Edge/1.0',
                                                   'Accept': 'application/json, text/plain, */*'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8', 'replace')


def parse_list(provider_id, text):
    """Turn a provider's published list into CIDR strings."""
    spec = provider(provider_id) or {}
    kind = spec.get('format') or 'text-lines'
    items = []
    if kind == 'text-lines':
        items = [line.strip() for line in (text or '').splitlines()]
    else:
        try:
            payload = json.loads(text)
        except Exception:
            return []
        if kind == 'json-list':
            items = payload if isinstance(payload, list) else []
        elif kind == 'json':
            items = (payload or {}).get(spec.get('field') or 'addresses') or []
        elif kind == 'aws':
            entries = (payload or {}).get(spec.get('field') or 'prefixes') or []
            field = spec.get('ip_field') or 'ip_prefix'
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if any(str(entry.get(key, '')).upper() != str(value).upper()
                       for key, value in (spec.get('match') or {}).items()):
                    continue
                if entry.get(field):
                    items.append(entry[field])
    out = []
    for item in items:
        value = str(item or '').strip()
        if not value or value.startswith('#'):
            continue
        try:
            net = ipaddress.ip_network(value, strict=False)
        except ValueError:
            continue
        if net.version == 4:
            out.append(str(net))
    return out


def sample(cidrs, limit=64, per_net=4):
    """Deterministic, bounded set of IPv4 addresses inside the given networks.

    A CDN fronting thousands of anycast addresses answers on *any* address of its
    published range, so probing a spread of addresses per range is more useful
    than expanding the first network completely (the old behaviour, which only
    ever looked at ranges smaller than /22). The sample is deterministic so a
    re-scan keeps the same well-known IPs instead of churning the catalog.
    """
    picked = []
    for cidr in cidrs:
        if len(picked) >= limit:
            break
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if net.version != 4:
            continue
        size = net.num_addresses
        steps = min(max(1, per_net), limit - len(picked), max(1, size - 2))
        span = max(1, (size - 3) // steps) if size > 4 else 1
        for index in range(steps):
            try:
                address = net.network_address + 1 + index * span
            except Exception:
                break
            if address not in net:
                continue
            value = str(address)
            if value not in picked:
                picked.append(value)
    return picked[:limit]


def fetch(provider_id, limit=64, per_net=4):
    """The clean addresses one provider can offer right now.

    Providers with a published, machine-readable list are downloaded (and the
    answer is the real sample of their ranges). Providers that publish nothing
    parsable answer from their curated seed instead — the probe still decides
    which of those addresses is actually usable, so a stale seed degrades into a
    failed ping rather than into a dead link.
    """
    spec = provider(provider_id)
    if not spec or spec.get('format') == 'manual':
        return [], 'manual provider'
    if spec.get('format') == 'static':
        seed = [str(item) for item in (spec.get('seed') or [])]
        return sample(seed, limit=limit, per_net=per_net), None if seed else 'empty seed'
    errors = []
    for url in spec.get('urls') or []:
        try:
            cidrs = parse_list(provider_id, _http_text(url))
        except Exception as exc:
            errors.append(f'{url}: {type(exc).__name__}')
            continue
        addresses = sample(cidrs, limit=limit, per_net=per_net)
        if addresses:
            return addresses, None
        errors.append(f'{url}: empty list')
    return [], '; '.join(errors) or 'no source urls'


# ----------------------------------------------------------------------- storage
def parse_metadata(raw):
    """Read a node's metadata whether it is JSON or an old Python repr.

    Earlier releases stored ``str(dict)`` in the metadata column, so a strict
    ``json.loads`` would silently lose the location/provider of every node created
    before this upgrade; both spellings are accepted.
    """
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or '').strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return dict(data)
    except Exception:
        pass
    try:
        import ast
        data = ast.literal_eval(text)
        if isinstance(data, dict):
            return dict(data)
    except Exception:
        pass
    return {}


def _save_ip(ip, source, latitude=None):
    now = int(time.time())
    try:
        execute('INSERT INTO cf_ips(ip,source,enabled,last_seen) VALUES(?,?,1,?) '
                'ON CONFLICT(ip) DO UPDATE SET source=excluded.source, enabled=1, last_seen=excluded.last_seen',
                (ip, source, now))
    except Exception:
        try:
            execute('INSERT OR IGNORE INTO cf_ips(ip,source,enabled,last_seen) VALUES(?,?,1,?)', (ip, source, now))
        except Exception:
            return False
    return True


def scan(provider_id, limit=64, per_net=4):
    """Seed the clean-IP pool from one provider's published ranges."""
    spec = provider(provider_id)
    if not spec:
        return {'provider': provider_id, 'ok': False, 'error': 'unknown provider', 'added': 0, 'found': 0}
    addresses, error = fetch(provider_id, limit=limit, per_net=per_net)
    added = sum(1 for ip in addresses if _save_ip(ip, spec['id']))
    return {'provider': spec['id'], 'ok': bool(addresses), 'found': len(addresses),
            'added': added, 'error': error}


def scan_all(limit=None):
    """Refresh every configured provider (the panel's «اسکن منابع» button)."""
    limit = int(limit or settings.cf_probe_limit)
    per = max(4, limit // max(1, len([p for p in PROVIDERS if p['format'] != 'manual'])))
    return [scan(item['id'], limit=per) for item in PROVIDERS if item['format'] != 'manual']


def add_ips(values, source=MANUAL_PROVIDER):
    """Add hand-written clean IPs (one per line, or comma separated)."""
    text = values if isinstance(values, str) else '\n'.join(str(v) for v in (values or []))
    added, skipped = [], []
    for token in re.split(r'[\s,;]+', text or ''):
        value = token.strip()
        if not value:
            continue
        try:
            net = ipaddress.ip_network(value, strict=False)
        except ValueError:
            skipped.append(value)
            continue
        candidates = [str(net.network_address)] if net.num_addresses == 1 else sample([str(net)], limit=8)
        for ip in candidates:
            if _save_ip(ip, source):
                added.append(ip)
    return {'added': added, 'skipped': skipped}


def remove_ip(ip):
    execute('DELETE FROM cf_ips WHERE ip=?', (str(ip).strip(),))
    return True


def ips(provider_id='', limit=500):
    """Clean IPs from the pool, healthiest first."""
    if provider_id:
        return rows('SELECT * FROM cf_ips WHERE source=? ORDER BY COALESCE(latency_ms,999999) ASC, ip LIMIT ?',
                    (provider_id, int(limit)))
    return rows('SELECT * FROM cf_ips ORDER BY COALESCE(latency_ms,999999) ASC, ip LIMIT ?', (int(limit),))


def provider_summary():
    """Panel view: every provider with how many of its IPs are known/healthy."""
    counts = {item['source']: item for item in rows(
        'SELECT source, COUNT(*) AS total, SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) AS healthy FROM cf_ips GROUP BY source')}
    out = []
    for spec in PROVIDERS:
        stats = counts.get(spec['id']) or {}
        out.append({**{k: spec[k] for k in ('id', 'label', 'note', 'format', 'port')},
                    'total': int(stats.get('total') or 0), 'healthy': int(stats.get('healthy') or 0),
                    # ``seeded`` providers ship their own known edge addresses (the
                    # CDNs that publish no parsable list); ``scannable`` is what the
                    # panel uses to decide whether the provider can be dialled at all.
                    'seeded': spec.get('format') == 'static',
                    'seed_count': len(spec.get('seed') or []),
                    'docs': (spec.get('urls') or [''])[0],
                    'scannable': spec.get('format') != 'manual'})
    return out


# ----------------------------------------------------------------- edge sources
def _slug(value, fallback='edge'):
    text = re.sub(r'[^A-Za-z0-9._-]+', '-', str(value or '').strip()).strip('-_.').lower()
    return (text or fallback)[:24]


def sources():
    """The configured sources (locations), in creation order."""
    raw = (row('SELECT value FROM settings WHERE key=?', (SOURCE_KEY,)) or {}).get('value')
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return [item for item in data if isinstance(item, dict) and item.get('id')] if isinstance(data, list) else []


def _write_sources(items):
    payload = json.dumps(items, ensure_ascii=False)
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (SOURCE_KEY, payload))
    return items


def normalize_source(payload, existing=None):
    """Validate one source definition; raises ``ValueError`` with a usable message."""
    data = dict(existing or {})
    data.update({k: v for k, v in (payload or {}).items() if v is not None})
    kind = str(data.get('kind') or 'ip').strip().lower()
    if kind not in ('ip', 'domain'):
        raise ValueError('kind must be ip or domain')
    label = str(data.get('label') or '').strip()[:48]
    location = _slug(data.get('location') or '', '') or ''
    host = str(data.get('host') or '').strip()
    provider_id = str(data.get('provider') or DEFAULT_PROVIDER).strip().lower()
    if kind == 'domain':
        if not host or '.' not in host:
            raise ValueError('دامنهٔ تمیز معتبر نیست (مثال: nl.example.com)')
        provider_id = 'domain'
    else:
        if provider_id not in provider_ids() or provider_id == 'domain':
            raise ValueError('provider ناشناخته است')
        if not host or '.' not in host:
            raise ValueError('برای منبع آی‌پی، Host/SNI لازم است (دامنه یا زیردامنهٔ پشت CDN)')
    try:
        port = int(data.get('port') or 443)
    except (TypeError, ValueError):
        port = 443
    if not 1 <= port <= 65535:
        raise ValueError('پورت نامعتبر است')
    try:
        maximum = int(data.get('max') or 5)
    except (TypeError, ValueError):
        maximum = 5
    literal = data.get('ips') or []
    if isinstance(literal, str):
        literal = [token for token in re.split(r'[\s,;]+', literal) if token]
    cleaned = []
    for value in literal[:64]:
        try:
            net = ipaddress.ip_network(str(value).strip(), strict=False)
        except ValueError:
            continue
        cleaned.append(str(net))
    source_id = _slug(data.get('id') or location or label or provider_id, provider_id)
    base = source_id
    index = 2
    taken = {item['id'] for item in sources() if item['id'] != (existing or {}).get('id')}
    while source_id in taken:
        source_id = f'{base}-{index}'
        index += 1
    return {
        'id': source_id,
        'label': label or (location.upper() or provider_id) + (
            f' · {host}' if kind == 'domain' else f' · {provider_id}'),
        'location': location,
        'kind': kind,
        'provider': provider_id,
        'host': host.lower(),
        'port': port,
        'tls': int(bool(data.get('tls', 1))),
        'ips': cleaned,
        'max': max(1, min(maximum, MAX_SOURCE_NODES)),
        'enabled': int(bool(data.get('enabled', 1))),
        # Which pack (or import) created this location, so a pack can be listed,
        # re-installed idempotently or removed as a whole.
        'pack': _slug(data.get('pack') or '', '') or '',
        'created_at': int(data.get('created_at') or time.time()),
    }


def save_source(payload=None, source_id=''):
    """Create or update one source (a location). Returns ``(source, all_sources)``."""
    existing = None
    if source_id:
        existing = next((item for item in sources() if item['id'] == str(source_id)), None)
        if not existing:
            raise ValueError('منبع پیدا نشد')
    merged = normalize_source(payload or {}, existing)
    if existing:
        merged['id'] = existing['id']
    items = [item for item in sources() if item['id'] != merged['id']]
    items.append(merged)
    _write_sources(items)
    return merged, items


def delete_source(source_id):
    target = str(source_id or '')
    items = sources()
    kept = [item for item in items if item['id'] != target]
    if len(kept) != len(items):
        _write_sources(kept)
        # Nodes of a removed location disappear with it.
        for node in rows("SELECT name, metadata FROM nodes WHERE kind IN ('cloudflare','edge')"):
            meta = parse_metadata(node.get('metadata'))
            if str(meta.get('source_id') or '') == target:
                execute('DELETE FROM nodes WHERE name=?', (node['name'],))
    return kept


def _ordered_ips(source):
    """Addresses for one source: measured-healthy pool first, literals after."""
    provider_id = source.get('provider')
    limit = int(source.get('max') or 5)
    literal = [str(ipaddress.ip_network(item, strict=False).network_address)
               for item in (source.get('ips') or [])]
    pool = [item['ip'] for item in rows(
        'SELECT ip FROM cf_ips WHERE source=? AND enabled=1 '
        'ORDER BY CASE WHEN ok=1 THEN 0 ELSE 1 END, COALESCE(latency_ms,999999) ASC, ip ASC LIMIT ?',
        (provider_id, max(limit * 4, 16)))]
    ordered = []
    for value in pool + literal:
        if value not in ordered:
            ordered.append(value)
    return ordered[:limit]


def nodes_for_source(source, known_latency=None):
    """The node descriptors one source publishes (as used by ``nodes.sync``)."""
    known_latency = known_latency or {}
    location = str(source.get('location') or '').strip()
    if source.get('kind') == 'domain':
        host = source['host']
        name = _slug(source.get('id') or location or host)
        return [{
            'name': name, 'kind': 'edge', 'server': host, 'port': int(source.get('port') or 443),
            'tls': int(source.get('tls', 1)), 'sni': host, 'host': host,
            'source': 'edge-domain', 'latency_ms': known_latency.get(name),
            'metadata': {'source_id': source['id'], 'provider': 'domain', 'location': location,
                         'edge_host': host, 'role': 'clean-domain'},
        }]
    provider_id = str(source.get('provider') or DEFAULT_PROVIDER)
    prefix = _slug('-'.join(part for part in (location, provider_id if provider_id != MANUAL_PROVIDER else 'custom')
                            if part), provider_id)
    nodes = []
    for index, ip in enumerate(_ordered_ips(source), 1):
        name = f'{prefix}-{index:02d}'
        nodes.append({
            'name': name, 'kind': 'cloudflare', 'server': ip, 'port': int(source.get('port') or 443),
            'tls': int(source.get('tls', 1)), 'sni': source['host'], 'host': source['host'],
            'source': f'{provider_id}-source', 'latency_ms': known_latency.get(name),
            'metadata': {'source_id': source['id'], 'provider': provider_id, 'location': location,
                         'edge_host': source['host'], 'role': 'clean-ip'},
        })
    return nodes


def plan(worker_url=None, edge_host=None):
    """Every node the catalog should contain right now.

    With no source configured the historical behaviour is kept intact: the
    Worker host (or the auto-detected Cloudflare-fronted domain) fronts the
    healthiest clean IPs, named ``cloudflare-NN``. As soon as the admin defines
    sources, those define the catalog instead — one group of nodes per location.
    """
    configured = [item for item in sources() if item.get('enabled', 1)]
    known = {}
    for item in rows('SELECT name, latency_ms FROM nodes'):
        known[item['name']] = item.get('latency_ms')
    nodes = []
    for source in configured:
        nodes.extend(nodes_for_source(source, known))
    if nodes:
        return nodes
    whost = (urllib_url_host(worker_url) if worker_url else None) or edge_host
    if not whost:
        return []
    source = 'cloudflare-probe' if worker_url else 'cloudflare-edge'
    for index, item in enumerate(_default_candidates(20), 1):
        name = f'cloudflare-{index:02d}'
        nodes.append({
            'name': name, 'kind': 'cloudflare', 'server': item['ip'], 'port': 443, 'tls': 1,
            'sni': whost, 'host': whost, 'source': source, 'latency_ms': item.get('latency_ms'),
            'metadata': {'provider': item.get('provider') or DEFAULT_PROVIDER, 'location': '',
                         'edge_host': whost, 'probe_latency_ms': item.get('latency_ms'), 'role': 'clean-ip'},
        })
    return nodes


def urllib_url_host(value):
    from urllib.parse import urlparse
    raw = str(value or '').strip()
    if not raw:
        return None
    if '://' not in raw:
        raw = 'https://' + raw
    return urlparse(raw).hostname


def _default_candidates(limit):
    """Healthiest measured IPs, topped up with unprobed ones (fresh install)."""
    chosen = rows('SELECT * FROM cf_ips WHERE enabled=1 AND ok=1 ORDER BY latency_ms ASC LIMIT ?', (limit,))
    if len(chosen) < limit:
        seen = {item['ip'] for item in chosen}
        extra = rows('SELECT * FROM cf_ips WHERE enabled=1 ORDER BY COALESCE(latency_ms,999999) ASC LIMIT ?',
                     (limit,))
        chosen += [item for item in extra if item['ip'] not in seen]
    return chosen[:limit]


def cleanup_orphans(keep_names):
    """Drop nodes whose source no longer exists (called after a sync)."""
    removed = []
    for node in rows("SELECT name, metadata FROM nodes WHERE kind IN ('cloudflare','edge')"):
        if node['name'] in keep_names:
            continue
        meta = parse_metadata(node.get('metadata'))
        if not meta.get('source_id'):
            continue  # default (source-less) catalog, owned by the sync itself
        execute('DELETE FROM nodes WHERE name=?', (node['name'],))
        removed.append(node['name'])
    return removed


def status():
    """Single payload for the panel's edge/locations card."""
    return {
        'providers': provider_summary(),
        'sources': sources(),
        'locations': sorted({item.get('location') for item in sources() if item.get('location')}),
        'default_provider': DEFAULT_PROVIDER,
        'max_nodes': MAX_SOURCE_NODES,
        'runtime': runtime.info(),
    }
