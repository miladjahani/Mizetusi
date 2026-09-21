"""Client catalog + Iran-ready creation presets.

Everything here is data, not logic:

* ``CLIENTS`` tells the panel which subscription format each client imports and
  where the end user downloads it. Every client also belongs to a ``family``
  (core / Xray / sing-box / Clash-Mihomo / tool cores), which is what the sublink
  lists are grouped by: a Clash client is only ever handed YAML, an Xray client
  only Base64 or plain text, so nobody has to guess which of a dozen links to
  paste.
* ``PRESETS`` describes the "best settings" bundles the quick-create button
  applies (fragment tuning, fingerprint, limits, expiry...).

Download links can be overridden from the panel (settings key ``client_links``
holding ``{"<client id>": "https://..."}``) because app stores and GitHub
release pages move over time.
"""
import json, urllib.parse
from app.db import row

# Subscription formats the generator can render. The last group filters by
# transport instead of by protocol, so a client can subscribe to just the
# Reality node set or just the plain WebSocket paths.
FORMATS = ('auto', 'all', 'vless', 'trojan', 'vmess', 'ss', 'base64', 'singbox', 'clash', 'xray', 'json',
           'ws', 'cdn', 'reality', 'warp')

FORMAT_LABELS = {
    'auto': 'اتصال هوشمند (همه نودها × همه پروتکل‌ها)',
    'all': 'همه ترکیب‌ها (VLESS + VMess + Trojan)',
    'vless': 'VLESS (WS · CDN · Reality)',
    'trojan': 'Trojan',
    'vmess': 'VMess',
    'ss': 'Shadowsocks',
    'base64': 'Base64 (V2Ray)',
    'singbox': 'sing-box JSON',
    'clash': 'Clash / Mihomo',
    'xray': 'Xray outbound JSON',
    'json': 'کاتالوگ نودها و پروتکل‌ها JSON',
    'ws': 'فقط WebSocket',
    'cdn': 'مسیرهای CDN',
    'reality': 'Reality (TCP)',
    'warp': 'WARP',
    'grpc': 'gRPC (نیازمند پورت اختصاصی)',
    'xhttp': 'XHTTP (نیازمند پورت اختصاصی)',
    'httpupgrade': 'HTTPUpgrade (نیازمند پورت اختصاصی)',
}

# Client engines, in the order the sublink sections are listed. ``order`` drives
# the grouping in the panel and in the public status window; ``label``/``hint``
# explain which link belongs to which family so an end user never mixes a YAML
# link into an Xray client (or the other way around).
FAMILIES = [
    {'id': 'core', 'order': 0, 'label': 'شروع سریع', 'hint': 'یک لینک برای همه‌چیز؛ سریع‌ترین نود بر اساس پینگ، مناسب هر کلاینتی.'},
    {'id': 'xray', 'order': 1, 'label': 'خانواده Xray — Base64 / متن ساده',
     'hint': 'v2rayNG، Exclusive، Amnezia، Streisand، Shadowrocket، V2Box و FoXray سابلینک Base64 (یا متن ساده) می‌خوانند.'},
    {'id': 'singbox', 'order': 2, 'label': 'خانواده sing-box — JSON',
     'hint': 'Hiddify، NekoBox، NekoBoxPlus، Karing و خود sing-box خروجی sing-box JSON را مستقیم ایمپورت می‌کنند.'},
    {'id': 'mihomo', 'order': 3, 'label': 'خانواده Clash / Mihomo — فقط YAML',
     'hint': 'Bettbox، Clash Verge، FlClash، Mihomo و Stash فقط فایل YAML می‌گیرند؛ لینک Base64 روی این کلاینت‌ها کار نمی‌کند.'},
    {'id': 'tools', 'order': 4, 'label': 'هسته‌ها و ابزارها', 'hint': 'خروجی خام برای هسته‌ها، ربات‌ها و ست‌کردن دستی.'},
]

FAMILY_IDS = [item['id'] for item in FAMILIES]

# The format a family publishes first — a client can never drift away from it.
FAMILY_FORMAT = {'core': 'auto', 'xray': 'base64', 'singbox': 'singbox', 'mihomo': 'clash', 'tools': 'xray'}


def family_by_id(family_id):
    for item in FAMILIES:
        if item['id'] == family_id:
            return item
    return FAMILIES[0]


# One row per client. ``format`` is what the client imports best, ``alt`` lists
# the extra formats it also accepts (offered behind «سایر فرمت‌ها»), and
# ``family`` is the engine that format belongs to — the key the sublink lists are
# grouped by in the panel and in the public status window.
CLIENTS = [
    {
        'id': 'smart', 'name': 'اتصال هوشمند', 'platform': 'همه پلتفرم‌ها', 'format': 'auto', 'alt': [], 'family': 'core',
        'download': '',
        'note': 'سریع‌ترین نود بر اساس پینگ واقعی؛ مناسب شروع سریع.',
    },
    {
        'id': 'v2rayng', 'name': 'v2rayNG', 'platform': 'Android', 'format': 'base64', 'alt': ['all', 'vless'], 'family': 'xray',
        'download': 'https://github.com/2dust/v2rayNG/releases/latest',
        'note': 'پرکاربردترین کلاینت اندروید؛ فرگمنت، sni و vless را کامل پشتیبانی می‌کند.',
    },
    {
        'id': 'bettbox', 'name': 'Bettbox', 'platform': 'Android', 'format': 'clash', 'alt': [], 'family': 'mihomo',
        'download': 'https://play.google.com/store/search?q=bettbox&c=apps',
        'note': 'کلاینت فارسی اندروید روی هسته Clash/Mihomo؛ فقط سابلینک YAML را ایمپورت می‌کند.',
    },
    {
        'id': 'exclusive', 'name': 'Exclusive', 'platform': 'Android', 'format': 'base64', 'alt': ['all', 'vless'], 'family': 'xray',
        'download': 'https://play.google.com/store/search?q=exclusive%20vpn%20v2ray&c=apps',
        'note': 'کلاینت اندروید؛ از سابلینک استاندارد Base64 استفاده می‌کند.',
    },
    {
        'id': 'nekoboxplus', 'name': 'NekoBoxPlus', 'platform': 'Android', 'format': 'singbox', 'alt': ['base64', 'all'], 'family': 'singbox',
        'download': 'https://github.com/search?q=nekoboxplus&type=repositories',
        'note': 'هسته sing-box؛ خروجی sing-box JSON یا سابلینک Base64.',
    },
    {
        'id': 'amnezia', 'name': 'Amnezia VPN', 'platform': 'Android · iOS · دسکتاپ', 'format': 'base64', 'alt': ['all', 'reality'], 'family': 'xray',
        'download': 'https://github.com/amnezia-vpn/amnezia-client/releases/latest',
        'note': 'کلاینت Amnezia؛ پروفایل VLESS/Reality و سابلینک Base64 را ایمپورت می‌کند.',
    },
    {
        'id': 'nekobox', 'name': 'NekoBox', 'platform': 'Android', 'format': 'singbox', 'alt': ['base64'], 'family': 'singbox',
        'download': 'https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest',
        'note': 'خانواده sing-box؛ بهترین نتیجه با خروجی sing-box.',
    },
    {
        'id': 'hiddify', 'name': 'Hiddify', 'platform': 'Android · iOS · دسکتاپ', 'format': 'singbox', 'alt': ['base64'], 'family': 'singbox',
        'download': 'https://github.com/hiddify/hiddify-next/releases/latest',
        'note': 'چندسکویی؛ sing-box JSON را مستقیم ایمپورت می‌کند.',
    },
    {
        'id': 'karing', 'name': 'Karing', 'platform': 'Android · iOS · دسکتاپ', 'format': 'singbox', 'alt': ['base64'], 'family': 'singbox',
        'download': 'https://github.com/KaringX/karing/releases/latest',
        'note': 'رابط ساده و مناسب موبایل؛ خروجی sing-box.',
    },
    {
        'id': 'streisand', 'name': 'Streisand', 'platform': 'iOS · macOS', 'format': 'base64', 'alt': ['all', 'vless'], 'family': 'xray',
        'download': 'https://apps.apple.com/app/streisand/id6450534064',
        'note': 'رایگان روی iOS؛ سابلینک Base64 و فرگمنت پشتیبانی می‌شود.',
    },
    {
        'id': 'shadowrocket', 'name': 'Shadowrocket', 'platform': 'iOS · macOS', 'format': 'base64', 'alt': ['all', 'vless'], 'family': 'xray',
        'download': 'https://apps.apple.com/app/shadowrocket/id932747118',
        'note': 'کلاینت حرفه‌ای iOS؛ سابلینک Base64.',
    },
    {
        'id': 'v2box', 'name': 'V2Box', 'platform': 'iOS · macOS', 'format': 'base64', 'alt': ['all'], 'family': 'xray',
        'download': 'https://apps.apple.com/app/v2box-v2ray-client/id6446814690',
        'note': 'گزینه جایگزین روی iOS.',
    },
    {
        'id': 'foxray', 'name': 'FoXray', 'platform': 'iOS', 'format': 'base64', 'alt': ['all'], 'family': 'xray',
        'download': 'https://apps.apple.com/app/foxray/id6448898396',
        'note': 'کلاینت سبک iOS با پشتیبانی از fragment.',
    },
    {
        'id': 'clash', 'name': 'Clash Verge / Mihomo', 'platform': 'Windows · macOS · Linux', 'format': 'clash', 'alt': [], 'family': 'mihomo',
        'download': 'https://github.com/clash-verge-rev/clash-verge-rev/releases/latest',
        'note': 'روی دسکتاپ؛ فقط خروجی Clash/Mihomo (YAML proxies).',
    },
    {
        'id': 'singbox', 'name': 'sing-box', 'platform': 'دسکتاپ · سرور', 'format': 'singbox', 'alt': ['base64'], 'family': 'singbox',
        'download': 'https://github.com/SagerNet/sing-box/releases/latest',
        'note': 'هسته رسمی sing-box؛ خروجی outbounds.',
    },
    {
        'id': 'xray', 'name': 'Xray-core', 'platform': 'دسکتاپ · سرور', 'format': 'xray', 'alt': [], 'family': 'tools',
        'download': 'https://github.com/XTLS/Xray-core/releases/latest',
        'note': 'برای ست‌کردن کلاینت‌های دستی و ربات‌ها.',
    },
]

CLIENT_IDS = [c['id'] for c in CLIENTS]

# client id -> subscription format
CLIENT_FORMATS = {c['id']: c['format'] for c in CLIENTS}

# client id -> engine family, so the panel can group links without a lookup table.
CLIENT_FAMILIES = {c['id']: c.get('family') for c in CLIENTS}

# A client can never publish a format its engine does not import (the reason the
# YAML link used to show up in Xray client cards): the pairing is asserted once,
# at import time, instead of being re-checked at every render.
for _client in CLIENTS:
    _expected = FAMILY_FORMAT.get(_client.get('family'))
    if _expected and _client['format'] != _expected:
        raise ValueError(f"client {_client['id']}: format {_client['format']} does not belong to family {_client['family']}")

# --------------------------------------------------------------------------- presets
# ``limit_gb``/``expiry_days``/``ip_limit`` are only used when the panel has no
# default configured for them (admin defaults always win).

# Every preset also declares the **node scope** it publishes (see
# ``app.subscriptions.scope``): a mode is a preset *plus* the nodes it hands out,
# so "Iran fast on the edge only" and "the server itself only" are two different,
# equally one-click modes instead of one preset with hidden assumptions.
PRESETS = [
    {
        'id': 'iran-fast',
        'name': 'ایران — پرسرعت (پیشنهادی)',
        'kind': 'iran',
        'scope': 'all',
        'best_for': 'همراه اول · ایرانسل · مخابرات',
        'note': 'VLESS + WS + TLS با فرگمنت ضد DPI روی نودهای Cloudflare و Railway؛ سبک و پایدار برای موبایل.',
        'highlights': ['فرگمنت 100-200 با فاصله 10-20', 'fingerprint chrome', 'بلاک تبلیغات', '۲ دستگاه همزمان', '۶۰ گیگ / ۳۰ روز'],
        'fields': {
            'protocol': 'vless',
            'frag_len': '100-200', 'frag_int': '10-20',
            'fingerprint': 'chrome', 'tls': 'on',
            'block_ads': True, 'block_porn': False,
            'ip_operator': 'all', 'ip_count': 5, 'rotate_time': 5, 'auto_rotate_ip': False,
            'start_on_first_connect': True,
            'ip_limit': 2, 'limit_gb': 60.0, 'expiry_days': 30,
        },
    },
    {
        'id': 'iran-unlimited',
        'name': 'ایران — مصرف سنگین (نامحدود)',
        'kind': 'iran',
        'scope': 'all',
        'best_for': 'استریم · بازی · دانلود',
        'note': 'بدون سقف حجم، فرگمنت فعال و سه دستگاه همزمان؛ برای مصرف بالا.',
        'highlights': ['حجم نامحدود', 'فرگمنت 100-200', '۳ دستگاه همزمان', '۹۰ روز اعتبار'],
        'fields': {
            'protocol': 'vless',
            'frag_len': '100-200', 'frag_int': '10-20',
            'fingerprint': 'chrome', 'tls': 'on',
            'block_ads': True, 'block_porn': False,
            'ip_operator': 'all', 'ip_count': 5, 'rotate_time': 5, 'auto_rotate_ip': False,
            'start_on_first_connect': True,
            'ip_limit': 3, 'limit_gb': None, 'expiry_days': 90,
        },
    },
    {
        'id': 'global-clean',
        'name': 'بین‌المللی — بدون فرگمنت',
        'kind': 'global',
        'scope': 'all',
        'best_for': 'اینترنت بدون محدودیت · سرعت حداکثری',
        'note': 'بدون تکه‌تکه‌سازی بسته‌ها؛ تمیزترین حالت برای اتصال‌های پایدار و پرسرعت.',
        'highlights': ['بدون فرگمنت', 'بدون محدودیت حجم', '۵ دستگاه همزمان', 'بدون انقضا'],
        'fields': {
            'protocol': 'vless',
            'frag_len': '', 'frag_int': '',
            'fingerprint': 'chrome', 'tls': 'on',
            'block_ads': False, 'block_porn': False,
            'ip_operator': 'all', 'ip_count': 5, 'rotate_time': 5, 'auto_rotate_ip': False,
            'start_on_first_connect': False,
            'ip_limit': 5, 'limit_gb': None, 'expiry_days': None,
        },
    },
]

PRESETS += [
    {
        'id': 'multi-location',
        'name': 'چند لوکیشن — فقط نودهای لبه',
        'kind': 'edge',
        'scope': 'multi',
        'best_for': 'می‌خواهد فقط لوکیشن‌های CDN را ببیند',
        'note': 'همان تنظیمات ایران، اما فقط روی لوکیشن‌های لبه (کلودفلر و دامنه‌های تمیز). نود خود سرور منتشر نمی‌شود.',
        'highlights': ['فقط نودهای مولتی‌لوکیشن', 'پرچم کشور روی هر نود', 'فرگمنت فعال', '۴۵ گیگ / ۳۰ روز'],
        'fields': {
            'protocol': 'vless',
            'frag_len': '100-200', 'frag_int': '10-20',
            'fingerprint': 'chrome', 'tls': 'on',
            'block_ads': True, 'block_porn': False,
            'ip_operator': 'all', 'ip_count': 5, 'rotate_time': 5, 'auto_rotate_ip': False,
            'start_on_first_connect': True,
            'ip_limit': 2, 'limit_gb': 45.0, 'expiry_days': 30,
        },
    },
    {
        'id': 'origin-only',
        'name': 'فقط سرور اصلی (بدون CDN)',
        'kind': 'origin',
        'scope': 'origin',
        'best_for': 'وقتی مسیر CDN مشکل دارد یا تست خود سرور',
        'note': 'فقط نود خود همین سرور منتشر می‌شود؛ بدون لوکیشن و بدون IP تمیز، برای عیب‌یابی سریع.',
        'highlights': ['فقط نود سرور اصلی', 'بدون لوکیشن', 'بدون فرگمنت', 'تست/عیب‌یابی'],
        'fields': {
            'protocol': 'all',
            'frag_len': '', 'frag_int': '',
            'fingerprint': 'chrome', 'tls': 'on',
            'block_ads': False, 'block_porn': False,
            'ip_operator': 'all', 'ip_count': 5, 'rotate_time': 5, 'auto_rotate_ip': False,
            'start_on_first_connect': False,
            'ip_limit': 10, 'limit_gb': None, 'expiry_days': 7,
        },
    },
]

DEFAULT_PRESET = 'iran-fast'


def preset(preset_id=None):
    wanted = (preset_id or DEFAULT_PRESET).strip().lower()
    for item in PRESETS:
        if item['id'] == wanted:
            return item
    if wanted not in {'', 'default'}:
        raise ValueError('unknown preset: ' + str(preset_id))
    return PRESETS[0]


def client(client_id):
    for item in CLIENTS:
        if item['id'] == client_id:
            return item
    return None


def download_overrides():
    """Admin-supplied download links (settings key ``client_links``)."""
    try:
        raw = row('SELECT value FROM settings WHERE key=?', ('client_links',))
    except Exception:
        return {}
    if not raw or not raw.get('value'):
        return {}
    try:
        data = json.loads(raw['value'])
    except Exception:
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()} if isinstance(data, dict) else {}


def catalog(overrides=None):
    """Client list with download links resolved, formats flattened and the
    engine family spelled out (label/hint/order) for the grouped link lists."""
    links = download_overrides() if overrides is None else overrides
    items = []
    for item in CLIENTS:
        entry = dict(item)
        entry['download'] = links.get(item['id']) or item['download']
        entry['targets'] = [item['format']] + [f for f in item['alt'] if f != item['format']]
        meta = family_by_id(item.get('family'))
        entry['family_label'] = meta['label']
        entry['family_hint'] = meta['hint']
        entry['family_order'] = meta['order']
        items.append(entry)
    return items


def subscription_url(base, token, target, node=''):
    url = f"{base}/sub/{urllib.parse.quote(str(token), safe='')}?target={urllib.parse.quote(target)}"
    if node:
        url += '&node=' + urllib.parse.quote(str(node))
    return url


def client_links(base, token, node='', overrides=None):
    """Subscription URL per client, ready to render in the panel or the portal."""
    rows_out = []
    for item in catalog(overrides):
        alts = []
        for fmt in item['targets'][1:]:
            alts.append({'target': fmt, 'label': FORMAT_LABELS.get(fmt, fmt), 'url': subscription_url(base, token, fmt, node)})
        rows_out.append({
            'id': item['id'],
            'name': item['name'],
            'platform': item['platform'],
            'format': item['format'],
            'format_label': FORMAT_LABELS.get(item['format'], item['format']),
            'note': item['note'],
            'download': item['download'],
            'family': item.get('family'),
            'family_label': item['family_label'],
            'family_hint': item['family_hint'],
            'family_order': item['family_order'],
            'url': subscription_url(base, token, item['id'], node),
            'alternatives': alts,
        })
    return rows_out


def client_groups(base, token, node='', overrides=None):
    """The client links split by engine family, in ``FAMILIES`` order.

    This is what both the status window and the panel drawer render: one collapsed
    section per family (Xray, sing-box, Clash/Mihomo, tool cores) instead of one
    flat list where every client was offered every format.
    """
    by_id = {item['id']: item for item in client_links(base, token, node, overrides)}
    groups = []
    for meta in FAMILIES:
        members = [by_id[item['id']] for item in CLIENTS
                   if item.get('family') == meta['id'] and item['id'] in by_id]
        if not members:
            continue
        groups.append({
            'id': meta['id'], 'label': meta['label'], 'hint': meta['hint'], 'order': meta['order'],
            'format': FAMILY_FORMAT.get(meta['id'], 'auto'),
            'formats': sorted({member['format'] for member in members}),
            'clients': members,
        })
    return groups
