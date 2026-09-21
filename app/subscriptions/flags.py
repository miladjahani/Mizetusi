"""Country flags for node labels.

A subscription that mixes locations is only readable when every entry says where
it terminates — ``de-cloudflare-03`` means little on a phone while
``🇩🇪 آلمان · de-cloudflare-03`` is obvious. This module turns whatever a node
carries (a location slug like ``de``, a Persian or English country name, a
provider hostname) into the matching flag emoji, and is the single place that
knows the spelling variants an admin may type.

Nothing here needs the network: the table is small, curated and covers the
locations a proxy deployment actually uses (Europe, the Middle East, the US and
the usual Asian relay points). An unknown location simply yields no flag, which
is better than a wrong one.
"""

# country code -> (English name, Persian name)
COUNTRIES = {
    'ae': ('UAE', 'امارات'),
    'am': ('Armenia', 'ارمنستان'),
    'at': ('Austria', 'اتریش'),
    'au': ('Australia', 'استرالیا'),
    'az': ('Azerbaijan', 'آذربایجان'),
    'be': ('Belgium', 'بلژیک'),
    'bg': ('Bulgaria', 'بلغارستان'),
    'br': ('Brazil', 'برزیل'),
    'ca': ('Canada', 'کانادا'),
    'ch': ('Switzerland', 'سوئیس'),
    'cl': ('Chile', 'شیلی'),
    'cn': ('China', 'چین'),
    'cr': ('Costa Rica', 'کاستاریکا'),
    'cy': ('Cyprus', 'قبرس'),
    'cz': ('Czechia', 'چک'),
    'de': ('Germany', 'آلمان'),
    'dk': ('Denmark', 'دانمارک'),
    'ee': ('Estonia', 'استونی'),
    'eg': ('Egypt', 'مصر'),
    'es': ('Spain', 'اسپانیا'),
    'fi': ('Finland', 'فنلاند'),
    'fr': ('France', 'فرانسه'),
    'gb': ('United Kingdom', 'انگلیس'),
    'ge': ('Georgia', 'گرجستان'),
    'gr': ('Greece', 'یونان'),
    'hk': ('Hong Kong', 'هنگ‌کنگ'),
    'hr': ('Croatia', 'کرواسی'),
    'hu': ('Hungary', 'مجارستان'),
    'id': ('Indonesia', 'اندونزی'),
    'ie': ('Ireland', 'ایرلند'),
    'il': ('Israel', 'اسرائیل'),
    'in': ('India', 'هند'),
    'ir': ('Iran', 'ایران'),
    'is': ('Iceland', 'ایسلند'),
    'it': ('Italy', 'ایتالیا'),
    'jp': ('Japan', 'ژاپن'),
    'kr': ('South Korea', 'کره جنوبی'),
    'kw': ('Kuwait', 'کویت'),
    'kz': ('Kazakhstan', 'قزاقستان'),
    'lt': ('Lithuania', 'لیتوانی'),
    'lu': ('Luxembourg', 'لوکزامبورگ'),
    'lv': ('Latvia', 'لتونی'),
    'md': ('Moldova', 'مولداوی'),
    'mx': ('Mexico', 'مکزیک'),
    'my': ('Malaysia', 'مالزی'),
    'nl': ('Netherlands', 'هلند'),
    'no': ('Norway', 'نروژ'),
    'nz': ('New Zealand', 'نیوزلند'),
    'om': ('Oman', 'عمان'),
    'pl': ('Poland', 'لهستان'),
    'pt': ('Portugal', 'پرتغال'),
    'qa': ('Qatar', 'قطر'),
    'ro': ('Romania', 'رومانی'),
    'rs': ('Serbia', 'صربستان'),
    'ru': ('Russia', 'روسیه'),
    'sa': ('Saudi Arabia', 'عربستان'),
    'se': ('Sweden', 'سوئد'),
    'sg': ('Singapore', 'سنگاپور'),
    'si': ('Slovenia', 'اسلوونی'),
    'sk': ('Slovakia', 'اسلواکی'),
    'th': ('Thailand', 'تایلند'),
    'tr': ('Turkey', 'ترکیه'),
    'ua': ('Ukraine', 'اوکراین'),
    'us': ('United States', 'آمریکا'),
    'vn': ('Vietnam', 'ویتنام'),
    'za': ('South Africa', 'آفریقای جنوبی'),
}

# Spellings an admin (or an imported subscription) may use for a country that is
# not already the two-letter code. Longest first, so ``آمریکا۲`` still resolves.
ALIASES = {
    'uk': 'gb', 'u.k': 'gb', 'united kingdom': 'gb', 'england': 'gb', 'britain': 'gb',
    'انگلستان': 'gb', 'بریتانیا': 'gb', 'لندن': 'gb', 'انگلیس': 'gb',
    'usa': 'us', 'u.s': 'us', 'u.s.a': 'us', 'united states': 'us', 'america': 'us',
    'آمریکا': 'us', 'امریکا': 'us', 'یوتا': 'us', 'utah': 'us',
    'holland': 'nl', 'netherlands': 'nl', 'amsterdam': 'nl', 'هلند': 'nl',
    'germany': 'de', 'frankfurt': 'de', 'آلمان': 'de', 'فرانکفورت': 'de',
    'finland': 'fi', 'helsinki': 'fi', 'فنلاند': 'fi', 'فینلاند': 'fi',
    'poland': 'pl', 'warsaw': 'pl', 'لهستان': 'pl',
    'turkey': 'tr', 'turkiye': 'tr', 'istanbul': 'tr', 'ترکیه': 'tr',
    'emirates': 'ae', 'dubai': 'ae', 'امارات': 'ae', 'دبی': 'ae',
    'iran': 'ir', 'tehran': 'ir', 'ایران': 'ir', 'تهران': 'ir',
    'france': 'fr', 'paris': 'fr', 'فرانسه': 'fr',
    'sweden': 'se', 'stockholm': 'se', 'سوئد': 'se',
    'austria': 'at', 'ویَن': 'at', 'اتریش': 'at',
    'switzerland': 'ch', 'zurich': 'ch', 'سوئیس': 'ch',
    'italy': 'it', 'milan': 'it', 'ایتالیا': 'it',
    'spain': 'es', 'madrid': 'es', 'اسپانیا': 'es',
    'russia': 'ru', 'moscow': 'ru', 'روسیه': 'ru',
    'romania': 'ro', 'رومانی': 'ro',
    'ukraine': 'ua', 'اوکراین': 'ua',
    'canada': 'ca', 'کانادا': 'ca',
    'japan': 'jp', 'tokyo': 'jp', 'ژاپن': 'jp',
    'singapore': 'sg', 'سنگاپور': 'sg',
    'india': 'in', 'هند': 'in',
    'hong kong': 'hk', 'hongkong': 'hk', 'هنگ کنگ': 'hk',
    'south korea': 'kr', 'korea': 'kr', 'کره': 'kr',
    'australia': 'au', 'استرالیا': 'au',
    'brazil': 'br', 'برزیل': 'br',
    'armenia': 'am', 'ارمنستان': 'am',
    'azerbaijan': 'az', 'آذربایجان': 'az',
    'georgia': 'ge', 'گرجستان': 'ge',
    'kazakhstan': 'kz', 'قزاقستان': 'kz',
    'lithuania': 'lt', 'لیتوانی': 'lt',
    'latvia': 'lv', 'لتونی': 'lv',
    'estonia': 'ee', 'استونی': 'ee',
    'bulgaria': 'bg', 'بلغارستان': 'bg',
    'hungary': 'hu', 'مجارستان': 'hu',
    'czech': 'cz', 'czechia': 'cz', 'چک': 'cz',
    'ireland': 'ie', 'ایرلند': 'ie',
    'norway': 'no', 'نروژ': 'no',
    'denmark': 'dk', 'دانمارک': 'dk',
    'belgium': 'be', 'بلژیک': 'be',
    'greece': 'gr', 'یونان': 'gr',
    'qatar': 'qa', 'قطر': 'qa',
    'saudi': 'sa', 'عربستان': 'sa',
    'oman': 'om', 'عمان': 'om',
    'kuwait': 'kw', 'کویت': 'kw',
    'israel': 'il', 'اسرائیل': 'il',
    'egypt': 'eg', 'مصر': 'eg',
    'malaysia': 'my', 'مالزی': 'my',
    'indonesia': 'id', 'اندونزی': 'id',
    'thailand': 'th', 'تایلند': 'th',
    'vietnam': 'vn', 'ویتنام': 'vn',
    'mexico': 'mx', 'مکزیک': 'mx',
    'costa rica': 'cr', 'کاستاریکا': 'cr', 'san jose': 'cr',
}

# Providers whose edge really sits in one country. Cloudflare (and every other
# anycast CDN) is deliberately absent: its address answers from wherever the
# client is, so a flag there would be a guess, and a wrong flag is worse than
# none.
PROVIDER_FLAGS = {'arvancloud': 'ir'}

_SUPPLEMENTARY = 0x1F1E6  # regional indicator 'A'


def _emoji_code(text):
    """The country a flag emoji inside free text stands for.

    A subscription handed out by another panel usually names each entry
    ``ZEUS | 🇱🇷 | 66EF9OOY 30`` — the *only* place the location is written down is
    that flag, so the importer has to be able to read it back rather than
    guessing from a hostname. The pair is assembled from the two regional
    indicators, which cannot appear by accident in an ordinary label.
    """
    letters = ''
    for char in str(text or ''):
        point = ord(char)
        if _SUPPLEMENTARY <= point <= _SUPPLEMENTARY + 25:
            letters += chr(point - _SUPPLEMENTARY + ord('a'))
            if len(letters) == 2:
                break
        elif letters:
            break
    return letters


def _code(value):
    """Best-effort two-letter country code for a free-form label."""
    text = str(value or '').strip().lower()
    if not text:
        return ''
    emoji = _emoji_code(text)
    if emoji:
        return emoji
    text = text.replace('_', ' ').replace('-', ' ').strip()
    collapsed = ' '.join(text.split())
    if collapsed in COUNTRIES:
        return collapsed
    if collapsed in ALIASES:
        return ALIASES[collapsed]
    # A location slug such as ``de-cloudflare-01`` or ``nl``: the first token is
    # the country by convention, so it is checked before the whole string.
    parts = collapsed.split()
    for token in [collapsed] + parts:
        short = token.strip('·|/,.()[]')
        if short in COUNTRIES:
            return short
        if short in ALIASES:
            return ALIASES[short]
    # Last resort: a full country name embedded in a longer label. Only names of
    # four characters or more take part, so the short codes (``us``, ``uk``,
    # ``ae`` …) cannot fire on an unrelated word such as ``plus`` or ``usage``.
    for alias, code in ALIASES.items():
        if len(alias) >= 4 and alias in collapsed:
            return code
    return ''


def flag(code):
    """The emoji flag for a two-letter country code (``''`` when unknown)."""
    value = _code(code)
    if not value:
        return ''
    return ''.join(chr(_SUPPLEMENTARY + (ord(letter) - ord('a'))) for letter in value)


def flag_for(location='', label='', provider=''):
    """Flag for a node: its location, else its explicit label, else its provider.

    Ordering matters: an admin who types ``nl`` for a German node gets the Dutch
    flag only if the label is checked first, so the location slug (the panel's own
    structured field) wins over free text.
    """
    for candidate in (location, label):
        found = flag(candidate)
        if found:
            return found
    code = PROVIDER_FLAGS.get(str(provider or '').strip().lower())
    return flag(code) if code else ''


def name(location=''):
    """Persian name of a location, for labels (``''`` when unknown)."""
    entry = COUNTRIES.get(_code(location))
    if not entry:
        return ''
    return entry[1]


def country_code(value):
    """Public alias used by the panel and the importer."""
    return _code(value)
