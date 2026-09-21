"""Node scope — which slice of the catalog one subscription is allowed to carry.

A multi-location install publishes a lot of nodes: this server's own relay, plus
every clean IP / clean domain of every edge location. An end user rarely wants
all of them — one wants *only the multi-location nodes*, another *only the US
edge*, another *only the server itself*.

That choice is a **scope**: a single short string stored on the user (in the
``metadata`` JSON, exactly like the config cap) and understood by three callers —
the subscription route (``?scope=``), the panel's user form, and the public
status window:

* ``all``     — every published node (the default; nothing changes for anyone);
* ``multi``   — only the multi-location nodes (every edge node, origin excluded);
* ``origin``  — only this server's own relay node(s);
* ``cc:us``   — only one country; several may be combined (``cc:us,cc:de``).

Country spellings are resolved through :mod:`app.subscriptions.flags`, so ``us``,
``usa``, ``🇺🇸``, ``آمریکا`` and ``de-frankfurt-01`` all land on the same scope.
Anything unrecognised resolves to ``all`` rather than silently publishing an
empty subscription.
"""
import re

from app.subscriptions import flags

SCOPE_ALL = 'all'
SCOPE_MULTI = 'multi'
SCOPE_ORIGIN = 'origin'
COUNTRY_PREFIX = 'cc:'
SPLIT = re.compile(r'[,\s+]+')

# ``label`` is what the panel shows; ``hint`` is the sentence under it.
SCOPE_LABELS = {
    SCOPE_ALL: 'همه نودها',
    SCOPE_MULTI: 'فقط نودهای مولتی‌لوکیشن',
    SCOPE_ORIGIN: 'فقط سرور اصلی',
}

SCOPE_HINTS = {
    SCOPE_ALL: 'سرور اصلی + همه لوکیشن‌های لبه (پیش‌فرض)',
    SCOPE_MULTI: 'همه لوکیشن‌های لبه (CDN و دامنهٔ تمیز)، بدون نود خود سرور',
    SCOPE_ORIGIN: 'فقط نود خود همین سرور؛ مناسب وقتی مسیر CDN مشکل دارد',
}


def context(node):
    """The (location, provider, origin?) view of a node, however it was stored.

    A node is ``origin`` when it is this deployment's own relay and ``multi``
    when it belongs to an edge location — every node is exactly one of the two,
    so no scope can drop a node by accident.
    """
    from app.edge.sources import parse_metadata
    raw = parse_metadata((node or {}).get('metadata'))
    kind = str((node or {}).get('kind') or '').strip().lower()
    return {
        'location': str(raw.get('location') or '').strip().lower(),
        'provider': str(raw.get('provider') or (node or {}).get('source') or '').strip().lower(),
        'origin': kind == 'railway',
    }


def location_of(node):
    """The location slug of a node ('' for the deployment's own relay)."""
    return context(node)['location']


def is_origin(node):
    """True when this node is the deployment's own relay, not a CDN location."""
    return context(node)['origin']


def parse(value):
    """Resolve any scope spelling to ``{'mode', 'countries', 'raw'}``.

    ``mode`` is one of ``all`` / ``multi`` / ``origin`` / ``country``; a country
    scope carries the normalised two-letter codes it selected.
    """
    text = str(value or '').strip().lower()
    if text in ('', '*', 'all', 'every', 'همه', 'همه نودها'):
        return {'mode': SCOPE_ALL, 'countries': [], 'raw': SCOPE_ALL}
    if text in ('multi', 'multi-location', 'location', 'locations', 'edge', 'لوکیشن', 'مولتی'):
        return {'mode': SCOPE_MULTI, 'countries': [], 'raw': SCOPE_MULTI}
    if text in ('origin', 'own', 'own-server', 'server', 'relay', 'سرور', 'سرور اصلی'):
        return {'mode': SCOPE_ORIGIN, 'countries': [], 'raw': SCOPE_ORIGIN}
    countries = []
    for part in SPLIT.split(text):
        token = part[3:] if part.startswith(COUNTRY_PREFIX) else part
        code = flags.country_code(token)
        if code and code not in countries:
            countries.append(code)
    if countries:
        return {'mode': 'country', 'countries': countries,
                'raw': ','.join(f'{COUNTRY_PREFIX}{code}' for code in countries)}
    return {'mode': SCOPE_ALL, 'countries': [], 'raw': SCOPE_ALL}


def normalize(value):
    """The canonical scope string for storage and URLs."""
    return parse(value)['raw']


def matches(node, scope=SCOPE_ALL):
    """Whether a node may be published under this scope."""
    parsed = parse(scope)
    mode = parsed['mode']
    if mode == SCOPE_ALL:
        return True
    if mode == SCOPE_MULTI:
        return not is_origin(node)
    if mode == SCOPE_ORIGIN:
        return is_origin(node)
    if mode == 'country':
        location = location_of(node)
        return bool(location) and location in parsed['countries']
    return True


def filter_nodes(nodes, scope=SCOPE_ALL):
    """The nodes a subscription with this scope may contain.

    An empty result is returned unchanged (not back-filled): the subscription
    route answers 404 with a readable reason, which is more honest than handing a
    client a node the scope explicitly excluded.
    """
    parsed = parse(scope)
    if parsed['mode'] == SCOPE_ALL:
        return list(nodes or [])
    return [node for node in (nodes or []) if matches(node, parsed['raw'])]


def label(scope):
    """Persian label of a scope, with the flag of every selected country."""
    parsed = parse(scope)
    if parsed['mode'] != 'country':
        return SCOPE_LABELS.get(parsed['mode'], SCOPE_LABELS[SCOPE_ALL])
    bits = []
    for code in parsed['countries']:
        flag = flags.flag(code)
        name = flags.name(code) or code.upper()
        bits.append(f'{flag} {name}'.strip())
    return ' + '.join(bits)


def hint(scope):
    parsed = parse(scope)
    if parsed['mode'] == 'country':
        return 'فقط نودهای همان کشور (در همهٔ پروتکل‌ها)'
    return SCOPE_HINTS.get(parsed['mode'], SCOPE_HINTS[SCOPE_ALL])


def options(nodes=None, scope=''):
    """Every scope worth offering for this catalog, with live node counts.

    The country entries are built from the nodes that actually exist, so the
    panel never offers a country it cannot publish. ``count`` is how many nodes a
    subscription with that scope would contain right now — the number that makes
    the choice obvious.
    """
    catalog = list(nodes or [])
    current = normalize(scope)
    items = [
        {'id': SCOPE_ALL, 'label': SCOPE_LABELS[SCOPE_ALL], 'hint': SCOPE_HINTS[SCOPE_ALL],
         'flag': '', 'count': len(catalog)},
        {'id': SCOPE_MULTI, 'label': SCOPE_LABELS[SCOPE_MULTI], 'hint': SCOPE_HINTS[SCOPE_MULTI],
         'flag': '', 'count': sum(1 for n in catalog if not is_origin(n))},
        {'id': SCOPE_ORIGIN, 'label': SCOPE_LABELS[SCOPE_ORIGIN], 'hint': SCOPE_HINTS[SCOPE_ORIGIN],
         'flag': '', 'count': sum(1 for n in catalog if is_origin(n))},
    ]
    seen = []
    for node in catalog:
        location = location_of(node)
        if location and location not in seen:
            seen.append(location)
    for location in sorted(seen, key=lambda code: (-sum(1 for n in catalog if location_of(n) == code), code)):
        identifier = f'{COUNTRY_PREFIX}{location}'
        items.append({
            'id': identifier,
            'label': (f'{flags.flag(location)} {flags.name(location) or location.upper()}').strip(),
            'hint': 'فقط نودهای همین کشور',
            'flag': flags.flag(location),
            'count': sum(1 for n in catalog if location_of(n) == location),
        })
    for item in items:
        item['current'] = item['id'] == current
        item['empty'] = item['count'] == 0
    return items


def catalog(nodes=None, scope=''):
    """The scope picker payload shared by the panel and the status window."""
    normalized = normalize(scope)
    return {
        'scope': normalized,
        'label': label(normalized),
        'hint': hint(normalized),
        'options': options(nodes, normalized),
    }
