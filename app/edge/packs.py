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

# ------------------------------------------------- Cloudflare location groups
# Cloudflare answers on one anycast network, so a "country" cannot be forced by
# choosing an address: any of its addresses serves any Cloudflare hostname from
# wherever the client happens to be, and that is exactly what makes a clean-IP
# location work with nothing but the Worker/panel domain as Host/SNI.
#
# What *does* differ per address is which part of Cloudflare's published network
# owns it — its registry allocation and therefore the country a client's geo
# database reports — and which routes to it are open from a given network. So a
# Cloudflare location here is one **group of ranges** from CF's official list:
# the location publishes addresses of that group only, which is what turns one
# single "Netherlands" entry into a real multi-location Cloudflare catalog.
# The ranges below were grouped by geo-locating sampled addresses of each of CF's
# published ranges (``GET /api/edge/ips`` shows the same addresses the probe
# measures); the honest TLS probe still decides which one really answers.
CF_REGIONS = (
    {'location': 'us', 'name': 'آمریکا',
     'ranges': ('103.21.244.0/22', '103.31.4.0/22')},
    {'location': 'ca', 'name': 'کانادا',
     'ranges': ('104.16.0.0/13', '104.24.0.0/14', '172.64.0.0/13', '198.41.128.0/17',
                '108.162.192.0/18', '173.245.48.0/20')},
    {'location': 'nl', 'name': 'هلند (آمستردام)',
     'ranges': ('141.101.64.0/18',)},
    {'location': 'it', 'name': 'اروپا (میلان/مادرید)',
     'ranges': ('188.114.96.0/20',)},
    {'location': 'sg', 'name': 'سنگاپور و توکیو',
     'ranges': ('103.22.200.0/22',)},
    {'location': 'za', 'name': 'آفریقای جنوبی',
     'ranges': ('197.234.240.0/22',)},
    {'location': 'cr', 'name': 'آمریکای مرکزی',
     'ranges': ('190.93.240.0/20', '131.0.72.0/22')},
    {'location': 'au', 'name': 'اقیانوسیه',
     'ranges': ('162.158.0.0/15',)},
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
        'id': 'cloudflare-regions',
        'label': 'کلودفلر — لوکیشن‌های چندگانه (۸ منطقه)',
        'note': ('برای هر گروه از رنج‌های کلودفلر یک لوکیشن جدا ساخته می‌شود؛ هر لوکیشن '
                 'فقط آدرس‌های همان بخش را منتشر می‌کند، پس برچسب کشور و مسیر ورود به '
                 'کلودفلر بین نودها فرق می‌کند و کل ماتریس پروتکل‌ها روی هرکدام می‌آید. '
                 'همهٔ آن‌ها با Host/SNI دامنهٔ Worker (یا دامنهٔ خودِ پنل پشت کلودفلر) '
                 'منتشر می‌شوند، پس دامنه را در فیلد بالا وارد کنید. پینگ هر لوکیشن از '
                 'همین سرور اندازه‌گیری می‌شود؛ چون کلودفلر آدرس‌هایش را در شبکه‌های '
                 'مختلف متفاوت اعلام می‌کند، ممکن است منطقه‌ای که از سرور پنل جواب '
                 'ندهد از شبکهٔ کاربران جواب بدهد و برعکس — آدرس پاسخ‌نداده از سابلینک '
                 'کنار گذاشته می‌شود و می‌توانید در همان لوکیشن رنج یا آی‌پی خودتان را '
                 'جایگزین کنید. (خروج ترافیک همان سرور رله است؛ چیزی که عوض می‌شود '
                 'مسیر ورود به کلودفلر است.)'),
        'provider': 'cloudflare',
        'kind': 'ip',
        'host_required': True,
        # A few addresses per region on purpose: they are Cloudflare's own edge
        # space, not a curated list, so the probe has to decide which of them
        # really answers from this deployment's network.
        'max': 4,
        'locations': tuple(
            {'location': item['location'], 'name': item['name'], 'ranges': item['ranges']}
            for item in CF_REGIONS),
    },
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
            'kind': item.get('kind') or 'domain',
            'host_required': bool(item.get('host_required')),
            'locations': len(item['locations']), 'installed': len(present),
            'entries': [{'location': loc['location'], 'host': loc.get('host') or '',
                         'name': loc.get('name') or '', 'port': loc.get('port') or 443,
                         'ranges': list(loc.get('ranges') or [])}
                        for loc in item['locations']],
        })
    return out


def _host_of(value):
    """The hostname inside a URL or a bare host (the Worker/panel domain)."""
    raw = str(value or '').strip()
    if not raw:
        return ''
    if '://' not in raw:
        raw = 'https://' + raw
    return (urllib.parse.urlparse(raw).hostname or '').lower()


def install(pack_id, override_hosts=None, default_host=''):
    """Create one edge source per location of a pack. Returns ``(created, sources)``.

    A clean-domain pack ships its own hosts; a clean-IP pack (the Cloudflare
    regions) needs one Host/SNI for every location — the Worker or panel domain
    the origin already answers on — so the caller may pass the one it has saved.
    Without any host the install is refused instead of publishing locations that
    could never answer.
    """
    item = pack(pack_id)
    if not item:
        raise ValueError('بستهٔ لوکیشن ناشناخته است')
    kind = item.get('kind') or 'domain'
    fallback = _host_of(default_host)
    # Resolve every host first: a missing Host/SNI must refuse the whole install
    # instead of leaving half a pack behind.
    hosts = []
    for index, location in enumerate(item['locations']):
        host = _host_of(location.get('host') or '')
        if override_hosts:
            host = _host_of(override_hosts[index % len(override_hosts)])
        elif not host:
            host = fallback
        hosts.append(host)
    if any('.' not in host for host in hosts):
        raise ValueError('برای لوکیشن‌های کلودفلر دامنهٔ Worker (یا دامنهٔ پنل پشت کلودفلر) '
                         'لازم است — در فیلد بالای همین بخش وارد کنید')
    created = []
    for index, location in enumerate(item['locations']):
        host = hosts[index]
        label = location.get('name') or location['location'].upper()
        payload = {
            'kind': kind, 'host': host, 'port': int(location.get('port') or 443),
            'location': location['location'], 'provider': item['provider'],
            'max': int(item.get('max') or 3), 'pack': item['id'],
            'label': f"{flags.flag_for(location['location'])} · {label}".strip(),
        }
        if kind == 'ip' and location.get('ranges'):
            payload['ranges'] = list(location['ranges'])
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
