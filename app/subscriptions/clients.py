"""Client catalog + Iran-ready creation presets.

Everything here is data, not logic:

* ``CLIENTS`` tells the panel which subscription format each client imports and
  where the end user downloads it.
* ``PRESETS`` describes the "best settings" bundles the quick-create button
  applies (fragment tuning, fingerprint, limits, expiry...).

Download links can be overridden from the panel (settings key ``client_links``
holding ``{"<client id>": "https://..."}``) because app stores and GitHub
release pages move over time.
"""
import json, urllib.parse
from app.db import row

# Subscription formats the generator can render.
FORMATS = ('auto', 'all', 'vless', 'trojan', 'base64', 'singbox', 'clash', 'xray', 'json')

FORMAT_LABELS = {
    'auto': 'اتصال هوشمند',
    'all': 'همه ترکیب‌ها (VLESS + Trojan)',
    'vless': 'VLESS',
    'trojan': 'Trojan',
    'base64': 'Base64 (V2Ray)',
    'singbox': 'sing-box JSON',
    'clash': 'Clash / Mihomo',
    'xray': 'Xray outbound JSON',
    'json': 'Node Catalog JSON',
}

# One row per client. ``format`` is what the client imports best, ``alt`` lists
# the extra formats it also accepts (offered as secondary chips).
CLIENTS = [
    {
        'id': 'smart', 'name': 'اتصال هوشمند', 'platform': 'همه پلتفرم‌ها', 'format': 'auto', 'alt': [],
        'download': '',
        'note': 'سریع‌ترین نود بر اساس پینگ واقعی؛ مناسب شروع سریع.',
    },
    {
        'id': 'v2rayng', 'name': 'v2rayNG', 'platform': 'Android', 'format': 'base64', 'alt': ['all', 'vless'],
        'download': 'https://github.com/2dust/v2rayNG/releases/latest',
        'note': 'پرکاربردترین کلاینت اندروید؛ فرگمنت، sni و vless را کامل پشتیبانی می‌کند.',
    },
    {
        'id': 'bettbox', 'name': 'Bettbox', 'platform': 'Android', 'format': 'base64', 'alt': ['all', 'vless'],
        'download': 'https://play.google.com/store/search?q=bettbox&c=apps',
        'note': 'کلاینت فارسی اندروید؛ سابلینک Base64 و فرگمنت را می‌خواند.',
    },
    {
        'id': 'exclusive', 'name': 'Exclusive', 'platform': 'Android', 'format': 'base64', 'alt': ['all', 'vless'],
        'download': 'https://play.google.com/store/search?q=exclusive%20vpn%20v2ray&c=apps',
        'note': 'کلاینت اندروید؛ از سابلینک استاندارد Base64 استفاده می‌کند.',
    },
    {
        'id': 'nekoboxplus', 'name': 'NekoBoxPlus', 'platform': 'Android', 'format': 'singbox', 'alt': ['base64', 'all'],
        'download': 'https://github.com/search?q=nekoboxplus&type=repositories',
        'note': 'هسته sing-box؛ خروجی sing-box JSON یا سابلینک Base64.',
    },
    {
        'id': 'nekobox', 'name': 'NekoBox', 'platform': 'Android', 'format': 'singbox', 'alt': ['base64'],
        'download': 'https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest',
        'note': 'خانواده sing-box؛ بهترین نتیجه با خروجی sing-box.',
    },
    {
        'id': 'hiddify', 'name': 'Hiddify', 'platform': 'Android · iOS · دسکتاپ', 'format': 'singbox', 'alt': ['base64', 'clash'],
        'download': 'https://github.com/hiddify/hiddify-next/releases/latest',
        'note': 'چندسکویی؛ sing-box JSON را مستقیم ایمپورت می‌کند.',
    },
    {
        'id': 'karing', 'name': 'Karing', 'platform': 'Android · iOS · دسکتاپ', 'format': 'singbox', 'alt': ['base64'],
        'download': 'https://github.com/KaringX/karing/releases/latest',
        'note': 'رابط ساده و مناسب موبایل؛ خروجی sing-box.',
    },
    {
        'id': 'streisand', 'name': 'Streisand', 'platform': 'iOS · macOS', 'format': 'base64', 'alt': ['all', 'vless'],
        'download': 'https://apps.apple.com/app/streisand/id6450534064',
        'note': 'رایگان روی iOS؛ سابلینک Base64 و فرگمنت پشتیبانی می‌شود.',
    },
    {
        'id': 'shadowrocket', 'name': 'Shadowrocket', 'platform': 'iOS · macOS', 'format': 'base64', 'alt': ['all', 'vless'],
        'download': 'https://apps.apple.com/app/shadowrocket/id932747118',
        'note': 'کلاینت حرفه‌ای iOS؛ سابلینک Base64.',
    },
    {
        'id': 'v2box', 'name': 'V2Box', 'platform': 'iOS · macOS', 'format': 'base64', 'alt': ['all'],
        'download': 'https://apps.apple.com/app/v2box-v2ray-client/id6446814690',
        'note': 'گزینه جایگزین روی iOS.',
    },
    {
        'id': 'foxray', 'name': 'FoXray', 'platform': 'iOS', 'format': 'base64', 'alt': ['all'],
        'download': 'https://apps.apple.com/app/foxray/id6448898396',
        'note': 'کلاینت سبک iOS با پشتیبانی از fragment.',
    },
    {
        'id': 'clash', 'name': 'Clash Verge / Mihomo', 'platform': 'Windows · macOS · Linux', 'format': 'clash', 'alt': ['base64'],
        'download': 'https://github.com/clash-verge-rev/clash-verge-rev/releases/latest',
        'note': 'روی دسکتاپ؛ خروجی Clash/Mihomo (YAML proxies).',
    },
    {
        'id': 'singbox', 'name': 'sing-box', 'platform': 'دسکتاپ · سرور', 'format': 'singbox', 'alt': ['base64'],
        'download': 'https://github.com/SagerNet/sing-box/releases/latest',
        'note': 'هسته رسمی sing-box؛ خروجی outbounds.',
    },
    {
        'id': 'xray', 'name': 'Xray-core', 'platform': 'دسکتاپ · سرور', 'format': 'xray', 'alt': [],
        'download': 'https://github.com/XTLS/Xray-core/releases/latest',
        'note': 'برای ست‌کردن کلاینت‌های دستی و ربات‌ها.',
    },
]

CLIENT_IDS = [c['id'] for c in CLIENTS]

# client id -> subscription format
CLIENT_FORMATS = {c['id']: c['format'] for c in CLIENTS}

# --------------------------------------------------------------------------- presets
# ``limit_gb``/``expiry_days``/``ip_limit`` are only used when the panel has no
# default configured for them (admin defaults always win).

PRESETS = [
    {
        'id': 'iran-fast',
        'name': 'ایران — پرسرعت (پیشنهادی)',
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
    """Client list with download links resolved and formats flattened."""
    links = download_overrides() if overrides is None else overrides
    items = []
    for item in CLIENTS:
        entry = dict(item)
        entry['download'] = links.get(item['id']) or item['download']
        entry['targets'] = [item['format']] + [f for f in item['alt'] if f != item['format']]
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
            'url': subscription_url(base, token, item['id'], node),
            'alternatives': alts,
        })
    return rows_out
