"""Multi-location packs and a subscription importer.

Configs used to carry the relay's own location for every entry, and the
Cloudflare ones shared a single Host, so a user in Iran saw a dozen entries that
all terminated in the same place. Two things fix that here:

* **packs** — a ready-made bundle of *locations*, one per country, each with its
  own clean domain (and port). Installing a pack creates one edge source per
  location, so the whole protocol matrix is published once per country and every
  entry carries that country's flag.
* **the importer** — the same thing built from someone else's subscription: paste
  any ``/sub/...`` link, and every host it contains becomes a location, with the
  flag read from the entry's own remark. That is what makes the pack *yours*
  instead of a hard-coded list.

Both are deliberately explicit: a location is only published after it exists as a
source, and the probe decides which of its addresses is healthy. Nothing here
opens a socket by itself except the async importer, which downloads exactly the
one URL it was given.
"""
import base64
import json
import re
import urllib.parse

from app.edge import sources as edge
from app.subscriptions import flags

# Ready-made locations taken from the reference subscription the operator
# supplied (`subs.bikara.net`). Each entry is one country: flag, a clean domain
# that already answers on TLS, and the port it answers on.
MULTI_LOCATIONS = (
    {'location': 'us', 'host': 'cdn101.melqora.ir', 'port': 443},
    {'location': 'us', 'host': 'cdn1.zarveto.ir', 'port': 443},
    {'location': 'us', 'host': 'cdn9.velnaro.ir', 'port': 443},
    {'location': 'gb', 'host': 'cdn1.novrisa.ir', 'port': 443},
    {'location': 'nl', 'host': 'cdn101.novrisa.ir', 'port': 443},
    {'location': 'fi', 'host': 'cdn1.velnaro.ir', 'port': 443},
    {'location': 'fi', 'host': 'cdn1.kafshora.ir', 'port': 443},
    {'location': 'de', 'host': 'cdn1.torvixa.ir', 'port': 443},
    {'location': 'pl', 'host': 'cdn1.melqora.ir', 'port': 443},
    {'location': 'tr', 'host': 'cdn1.nelqora.ir', 'port': 443},
    {'location': 'ae', 'host': 'cdn7.velnaro.ir', 'port': 443},
)

# The Iranian relay endpoints of the same subscription: one host, one port per
# destination country. These are the "ایرانسل" tunnels — the ones that work when
# the direct route is throttled.
IRAN_TUNNELS = (
    {'location': 'fi', 'host': 'cdn16.qemitra.ir', 'port': 30521},
    {'location': 'de', 'host': 'cdn16.qemitra.ir', 'port': 30524},
    {'location': 'tr', 'host': 'cdn16.qemitra.ir', 'port': 30528},
    {'location': 'us', 'host': 'cdn16.qemitra.ir', 'port': 30529},
    {'location': 'pl', 'host': 'cdn16.qemitra.ir', 'port': 30530},
    {'location': 'ae', 'host': 'cdn16.qemitra.ir', 'port': 30602},
)

PACKS = (
    {
        'id': 'multi-cdn',
        'label': 'چند لوکیشن — دامنه‌های تمیز CDN',
        'note': ('برای هر کشور یک لوکیشن با دامنهٔ تمیز همان لوکیشن ساخته می‌شود؛ '
                 'کل ماتریس پروتکل‌ها روی هر کشور منتشر و با پرچم همان کشور برچسب می‌خورد. '
                 'دامنه‌ها باید اوریجین همین سرویس را سرو کنند — اگر دامنهٔ دیگری دارید، '
                 'همین بسته را با دامنه‌های خودتان وارد کنید (بخش «ورود از سابلینک»).'),
        'provider': 'cloudflare',
        'max': 3,
        'locations': MULTI_LOCATIONS,
    },
    {
        'id': 'iran-tunnels',
        'label': 'تانل‌های ایران (ایرانسل)',
        'note': ('پورت‌های ایران‌سل روی یک دامنهٔ تمیز؛ برای شبکه‌هایی که مسیر مستقیم را '
                 'محدود می‌کنند. هر پورت یک لوکیشن جدا با پرچم کشور مقصد است.'),
        'provider': 'cloudflare',
        'max': 2,
        'locations': IRAN_TUNNELS,
    },
)

PACK_IDS = {pack['id'] for pack in PACKS}


def pack(pack_id):
    wanted = str(pack_id or '').strip().lower()
    for item in PACKS:
        if item['id'] == wanted:
            return item
    return None


def installed(pack_id):
    """Sources that already belong to this pack (so install is idempotent)."""
    return [item for item in edge.sources() if str(item.get('pack') or '') == str(pack_id)]


def catalog():
    """Panel view: every pack with how much of it is already installed."""
    out = []
    for item in PACKS:
        present = installed(item['id'])
        out.append({
            'id': item['id'], 'label': item['label'], 'note': item['note'],
            'provider': item['provider'], 'max': item['max'],
            'locations': len(item['locations']), 'installed': len(present),
            'entries': [{'location': loc['location'], 'host': loc['host'], 'port': loc['port']}
                        for loc in item['locations']],
        })
    return out


def install(pack_id, override_hosts=None):
    """Create one edge source per location of a pack. Returns ``(created, sources)``."""
    item = pack(pack_id)
    if not item:
        raise ValueError('بستهٔ لوکیشن ناشناخته است')
    created = []
    for index, location in enumerate(item['locations']):
        host = str(location['host'])
        if override_hosts:
            host = str(override_hosts[index % len(override_hosts)])
        payload = {
            'kind': 'domain', 'host': host, 'port': int(location.get('port') or 443),
            'location': location['location'], 'provider': item['provider'],
            'max': int(item.get('max') or 3), 'pack': item['id'],
            'label': f"{flags.flag_for(location['location'])} · {location['location'].upper()}"
        }
        try:
            source, _ = edge.save_source(payload, '')
        except ValueError:
            continue
        created.append(source)
    return created, edge.sources()


def uninstall(pack_id):
    """Remove every source a pack created (its nodes go with them)."""
    removed = []
    for source in installed(pack_id):
        edge.delete_source(source['id'])
        removed.append(source['id'])
    return removed


# --------------------------------------------------------------- sub import
_LINK_RE = re.compile(r'^(?P<scheme>[a-z0-9]{2,12})://(?P<rest>.+)$', re.IGNORECASE)


def decode_subscription(text):
    """Return the plain lines of a subscription body.

    Panels answer either with the links themselves or with the whole list
    base64-encoded (the classic V2Ray form), so both are accepted — and a body
    that is HTML (a panel login page, a landing page) is rejected rather than
    silently parsed into nothing.
    """
    raw = str(text or '').strip()
    if not raw:
        return []
    if raw.lower().startswith(('<!doctype', '<html')):
        return []
    looks_like_links = '://' in raw
    if not looks_like_links:
        padded = raw.replace('-', '+').replace('_', '/')
        padded += '=' * (-len(padded) % 4)
        try:
            raw = base64.b64decode(padded).decode('utf-8', 'replace')
        except Exception:
            return []
    return [line.strip() for line in raw.splitlines() if line.strip() and '://' in line]


def _vmess_host(line):
    """``vmess://`` carries a base64 JSON blob instead of a URL."""
    body = line.split('://', 1)[1].strip()
    body += '=' * (-len(body) % 4)
    try:
        payload = json.loads(base64.b64decode(body).decode('utf-8', 'replace'))
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get('add'):
        return None
    try:
        port = int(payload.get('port') or 443)
    except (TypeError, ValueError):
        port = 443
    return {'host': str(payload['add']).strip(), 'port': port,
            'label': str(payload.get('ps') or '').strip()}


def parse_link(line):
    """One subscription entry -> scheme/host/port/remark/flag."""
    text = str(line or '').strip()
    match = _LINK_RE.match(text)
    if not match:
        return None
    scheme = match.group('scheme').lower()
    remark = urllib.parse.unquote(text.split('#', 1)[1]) if '#' in text else ''
    remark = remark.strip()
    if scheme == 'vmess':
        parsed = _vmess_host(text)
        if not parsed:
            return None
        parsed.update({'scheme': scheme, 'label': parsed['label'] or remark})
    else:
        # scheme://userinfo@host:port?query — the userinfo may itself contain an
        # encoded host (Shadowsocks SIP002), so the authority is read after @.
        body = match.group('rest')
        authority = body.split('#', 1)[0].split('?', 1)[0]
        if '@' in authority:
            authority = authority.rsplit('@', 1)[1]
        authority = authority.split('/', 1)[0]
        if authority.startswith('['):  # IPv6 literal
            host, _, port = authority.partition(']')
            host = host.lstrip('[')
            port = port.lstrip(':')
        else:
            host, _, port = authority.partition(':')
        host = host.strip().lower()
        if not host or '.' not in host:
            return None
        try:
            port = int(port or 443)
        except (TypeError, ValueError):
            port = 443
        parsed = {'host': host, 'port': port, 'label': remark}
        parsed['scheme'] = scheme
    parsed['flag'] = flags.flag_for(parsed.get('label') or '')
    parsed['location'] = flags.country_code(parsed.get('label') or '') or ''
    return parsed


def entries(text):
    """Every parsable entry of a subscription, in order, de-duplicated by host:port."""
    seen = {}
    for line in decode_subscription(text):
        parsed = parse_link(line)
        if not parsed:
            continue
        key = f"{parsed['host']}:{parsed['port']}"
        if key in seen:
            continue
        seen[key] = parsed
    return list(seen.values())


def plan(text, provider='cloudflare', max_nodes=3, port_override=None):
    """Subscription body -> the location sources it would create.

    Entries without a recognisable country are kept under their own slug (so
    nothing is silently dropped), and a host that appears on several ports becomes
    one location per port — which is exactly how a relay publishes its tunnels.
    """
    created = []
    for item in entries(text):
        location = item.get('location') or ''
        if not location:
            location = re.sub(r'[^a-z0-9]+', '-', item['host'].split('.')[0]).strip('-')[:12]
        created.append({
            'kind': 'domain', 'host': item['host'],
            'port': int(port_override or item['port'] or 443),
            'location': location, 'provider': provider, 'max': int(max_nodes or 3),
            'pack': 'imported',
            'label': f"{item.get('flag') or ''} · {item['host']}".strip(),
        })
    return created


def import_plan(text, provider='cloudflare', max_nodes=3, port_override=None, apply=False):
    """Preview (or apply) a subscription import; raises on an unreadable body."""
    candidates = plan(text, provider=provider, max_nodes=max_nodes, port_override=port_override)
    if not candidates and not decode_subscription(text):
        raise ValueError('سابلینک خوانده نشد — لینک اشتراک یا متن کانفیگ‌ها را وارد کنید')
    created = []
    if apply:
        for payload in candidates:
            try:
                source, _ = edge.save_source(payload, '')
            except ValueError:
                continue
            created.append(source)
    return {'found': len(candidates), 'created': created,
            'locations': sorted({item['location'] for item in candidates}),
            'preview': [{'location': item['location'], 'host': item['host'], 'port': item['port']}
                        for item in candidates]}


def import_url(url, provider='cloudflare', max_nodes=3, port_override=None, apply=False,
               timeout=15):
    """Download a subscription URL and import the locations it publishes."""
    address = str(url or '').strip()
    if not address.startswith(('http://', 'https://')):
        raise ValueError('آدرس سابلینک باید http(s) باشد')
    try:
        text = edge._http_text(address, timeout=timeout)
    except Exception as exc:
        raise ValueError(f'دانلود سابلینک ناموفق بود ({type(exc).__name__})')
    result = import_plan(text, provider=provider, max_nodes=max_nodes,
                         port_override=port_override, apply=apply)
    result['url'] = address
    return result
