"""Where a clean edge address really is.

A country label on a location is a claim about *what a client's geo database
says*, and for an anycast network that claim is frequently wrong. Measured on
the very ranges this deployment publishes:

* ``104.16.0.0/13``, ``104.24.0.0/14``, ``172.64.0.0/13``, ``198.41.128.0/17``,
  ``108.162.192.0/18`` and ``173.245.48.0/20`` answered **United States** on
  ipwho *and* ipinfo, while ip-api alone said Canada — which is exactly the
  complaint «پرچم کانادا زده، آی‌پی می‌زند آمریکا». Labelling that group
  «کانادا» was a guess, and the guess lost two votes to one.
* ``188.114.96.0/20`` (published as «اروپا (میلان/مادرید)») answered US for one
  address and ES for another.
* even inside a single range the answer moves per address: ``103.21.244.0/22``
  → US and MY, ``162.158.0.0/15`` → AU and GT, ``197.234.240.0/22`` → ZA and CI.

So the country of a node is **measured per address**, by majority vote over
several independent databases, instead of being inferred from the range the
address belongs to. Three details keep that honest and cheap:

* an address's allocation does not move, so an answer is cached for good;
* a tie (two databases disagreeing while the third is unreachable) resolves to
  *no* answer, which leaves the admin's own label in place rather than picking a
  database and calling it truth;
* nothing here ever raises, and a deployment with no outbound access — or an
  admin who switched the lookups off — simply keeps the labels it had.
"""
import ipaddress
import json
import time
import urllib.request

from app.config import settings
from app.db import row, execute

# ``geo_lookup`` is the panel switch (on by default); the addresses of every
# answer live in one bounded settings blob so no schema migration is needed.
ENABLED_KEY = 'geo_lookup'
CACHE_KEY = 'geo_cache'
MAX_ENTRIES = 2000
# A failed lookup is remembered for a few hours: an unreachable database must not
# turn every sync into the same three timeouts.
RETRY_AFTER = 6 * 3600

# Three keyless databases, deliberately different owners — the majority is only
# worth anything when the votes are independent. All three are plain GETs with no
# account, no key and no query the panel has to build.
LOOKUPS = (
    ('ipwho', 'https://ipwho.is/{ip}'),
    ('ip-api', 'http://ip-api.com/json/{ip}?fields=countryCode'),
    ('ipinfo', 'https://ipinfo.io/{ip}/country'),
)
USER_AGENT = 'NEXUS-panel/1.0 (+geo)'


def _setting(key, default=None):
    try:
        found = row('SELECT value FROM settings WHERE key=?', (key,))
    except Exception:
        found = None
    value = (found or {}).get('value') if found else None
    return value if value not in (None, '') else default


def enabled():
    """Whether the panel may ask a database about an address.

    Off when the admin switched it off in «شخصی‌سازی», and off when outbound
    probing is disabled altogether (``NEXUS_OUTBOUND_PROBE_ENABLED=0``): a
    deployment that must not make outbound connections must not make these
    either.
    """
    if not getattr(settings, 'outbound_probe_enabled', True):
        return False
    return (_setting(ENABLED_KEY) or '1') != '0'


def public(ip):
    """The canonical form of a *public* address, or ``''``.

    Private, loopback, link-local and reserved space never gets a country: a
    lookup for ``192.168.1.1`` would return the database's guess about a private
    range, which is worse than no answer.
    """
    try:
        address = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return ''
    if not address.is_global:
        return ''
    return str(address)


def _cache():
    raw = _setting(CACHE_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _save(data):
    """Persist the answers, dropping the oldest ones once the blob gets large."""
    if len(data) > MAX_ENTRIES:
        ordered = sorted(data.items(), key=lambda kv: float((kv[1] or {}).get('at') or 0), reverse=True)
        data = dict(ordered[:MAX_ENTRIES])
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (CACHE_KEY, json.dumps(data, ensure_ascii=False)))


def entry(ip):
    """The stored answer for one address (``None`` when it was never measured)."""
    code = public(ip)
    if not code:
        return None
    found = _cache().get(code)
    return found if isinstance(found, dict) else None


def cached(ip):
    """The measured country of an address, or ``''`` when we have no answer.

    This is the read-only half every render path uses: no network, no writes.
    """
    return str((entry(ip) or {}).get('cc') or '').lower()


def majority(values):
    """The most frequent country code, or ``''`` when the votes are tied.

    A tie means the databases disagree and the third did not answer: picking one
    of them would be a coin flip, so the caller keeps the label it already has.
    """
    tally = {}
    for value in values:
        code = str(value or '').strip().lower()
        if len(code) == 2 and code.isalpha():
            tally[code] = tally.get(code, 0) + 1
    if not tally:
        return ''
    ranked = sorted(tally.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return ''
    return ranked[0][0]


def _parse(name, body):
    """Pull the two-letter code out of one database's answer."""
    text = str(body or '').strip()
    if not text:
        return ''
    if name == 'ipinfo':
        # Plain text: ``US`` (or ``Error``).
        return text[:2].lower() if len(text) >= 2 and text[:2].isalpha() else ''
    try:
        data = json.loads(text)
    except Exception:
        return ''
    if not isinstance(data, dict):
        return ''
    value = data.get('country_code') or data.get('countryCode') or ''
    value = str(value).strip().lower()
    return value if len(value) == 2 and value.isalpha() else ''


def _query(name, url, timeout):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                                  'Accept': 'application/json, text/plain'})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return _parse(name, answer.read(2048).decode('utf-8', 'replace'))


def lookup(ip, timeout=4.0):
    """The majority answer for one address, measured once and then remembered.

    Cache first: an address's allocation does not move, so a second call must not
    become three more requests to the databases.
    """
    code = public(ip)
    if not code or not enabled():
        return entry(code) or {}
    stored = (_cache().get(code) or {})
    if stored.get('cc'):
        return stored
    votes = {}
    for name, template in LOOKUPS:
        try:
            found = _query(name, template.format(ip=code), timeout)
        except Exception:
            found = ''
        if found:
            votes[name] = found
    answer = {'cc': majority(votes.values()), 'votes': votes, 'at': int(time.time())}
    data = _cache()
    data[code] = answer
    _save(data)
    return answer


def resolve(ips, limit=24, timeout=3.0, budget=8.0):
    """Measure the addresses we have never seen; bounded, best effort.

    ``limit`` is how many *new* addresses one call may look up and ``budget`` how
    many seconds it may spend doing it, so a panel request or a monitor pass never
    fans out into dozens of outbound requests and cannot stall behind a database
    that is slow or unreachable — a blocked host would otherwise cost three
    timeouts per address. Answers are persisted even when the pass is cut short,
    so the work done is never lost and the next pass continues where this one
    stopped.
    """
    wanted = []
    for ip in ips or []:
        code = public(ip)
        if code and code not in wanted:
            wanted.append(code)
    if not enabled() or not wanted:
        return {'checked': 0, 'resolved': 0, 'known': len(wanted)}
    data = _cache()
    now = time.time()
    started = time.monotonic()
    checked = resolved = known = 0
    try:
        for code in wanted:
            stored = data.get(code) or {}
            fresh_failure = bool(stored) and not stored.get('cc') and (now - float(stored.get('at') or 0)) < RETRY_AFTER
            if stored.get('cc'):
                known += 1
                continue
            if fresh_failure or checked >= max(0, int(limit)):
                continue
            if checked and time.monotonic() - started > max(0.0, float(budget)):
                break
            checked += 1
            votes = {}
            for name, template in LOOKUPS:
                try:
                    found = _query(name, template.format(ip=code), timeout)
                except Exception:
                    found = ''
                if found:
                    votes[name] = found
            data[code] = {'cc': majority(votes.values()), 'votes': votes,
                          'at': int(now)}
            if data[code]['cc']:
                resolved += 1
    finally:
        if checked:
            _save(data)
    return {'checked': checked, 'resolved': resolved, 'known': known}


async def resolve_async(ips, limit=24, timeout=3.0, budget=8.0):
    """``resolve`` off the event loop: the lookups are blocking HTTP calls."""
    import asyncio
    return await asyncio.to_thread(resolve, ips, limit, timeout, budget)


def summary(ips):
    """What the databases say about a set of addresses.

    ``country`` is the majority answer (what the location should be labelled),
    ``counts`` the full split so a disagreement is visible instead of smoothed
    over, and ``measured`` how many of the addresses have an answer at all.
    """
    counts = {}
    answers = []
    for ip in ips or []:
        code = cached(ip)
        if code:
            counts[code] = counts.get(code, 0) + 1
            answers.append(code)
    total = len(list(ips or []))
    return {'country': majority(answers), 'counts': counts, 'measured': len(answers),
            'total': total, 'agree': bool(answers) and len(counts) == 1}


def stats():
    """Panel view of the cache: how many addresses are known and when."""
    data = _cache()
    answered = [item for item in data.values() if isinstance(item, dict) and item.get('cc')]
    return {'enabled': enabled(), 'known': len(data), 'answered': len(answered),
            'countries': sorted({str(item.get('cc')) for item in answered if item.get('cc')}),
            'updated_at': max((int(item.get('at') or 0) for item in data.values()
                               if isinstance(item, dict)), default=0)}


def forget():
    """Drop every stored answer (the panel's «پاک کردن حافظهٔ جغرافیایی»)."""
    execute('DELETE FROM settings WHERE key=?', (CACHE_KEY,))
    return True
