"""What a fresh deployment switches on by itself.

Everywhere else the panel's rule is «off until an admin turns it on deliberately»,
and that rule stays: a switch an admin has ever touched is never overwritten here.
What this module adds is the one decision that carries no judgement — a capability
that needs no port, no credential, no DNS record and no outside service, on a host
that can really serve it, should not sit switched off in a deployment nobody has
opened yet.

Exactly one capability qualifies today: **Telegram Desktop's WEB proxy**. Its
carrier is HTTPS on the deployment's own domain plus a same-origin WebSocket
(``app/telegram/webrelay.py``), so it works on every platform this image runs on —
Railway included, with no TCP proxy — the moment the panel's own domain resolves.

Everything else that *could* be switched on needs something first: a raw TCP port
(AnyTLS, MTProto, the HTTP/SOCKS5 web proxies), a UDP port (TUIC), an external
endpoint (Hysteria2), a Worker address (the Cloudflare locations) or an admin's own
secret. The panel withholds those until the missing half is real and names it on
the card, so enabling them here would only produce a page full of reasons.

…with one exception, which is the second half of the pass. On Railway that raw TCP
port is a **TCP proxy**, its public port is random, and only an API call can create
it (see :mod:`app.railway`). So when a Railway API token is present the pass creates
one proxy per capability an admin has already switched on, records the forwarded
host and port (:mod:`app.ports`), and redeploys the service once so those proxies
are active — which is what turns «the link points at a port nothing forwards» into
a link that works, with no dashboard visit at all. Without a token the pass creates
nothing and says exactly which variable is missing.

Each half runs **once**: the markers are settings, so an admin who turns the WEB
proxy off afterwards — or deletes a proxy — keeps that across every later boot.
Both can also be re-run by hand from the panel.
"""
import time

from app import ports, railway
from app.config import settings
from app.db import execute, row
from app.telegram import webrelay

DONE = 'auto_configure_done'
SWITCH = 'auto_configure'
LOG = 'auto_configure_log'


def _setting(key, default=None):
    try:
        found = row('SELECT value FROM settings WHERE key=?', (key,))
    except Exception:
        found = None
    value = (found or {}).get('value') if found else None
    return value if value not in (None, '') else default


def _store(key, value):
    try:
        execute('INSERT INTO settings(key,value) VALUES(?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
    except Exception:
        pass


def allowed():
    """Whether automatic configuration is on (an admin can switch it off)."""
    return (_setting(SWITCH) or '1') != '0'


def candidates():
    """The capabilities that could be switched on here, with the reason they are not.

    A candidate is only ever «enable the WEB proxy and let it follow this
    deployment's own hostname»: the domain is left empty on purpose, so a redeploy
    that changes the Railway domain keeps publishing working links.
    """
    out = []
    if _setting(webrelay.ENABLED) is None:
        out.append({
            'id': 'webrelay',
            'title': 'پروکسی WEB تلگرام (tg://webproxy)',
            'ready': bool(webrelay.available()),
            'reason': '' if webrelay.available() else f'باینری relay در این ایمیج نیست ({webrelay.binary()})',
        })
    return out


def port_candidates():
    """The raw-TCP capabilities this deployment would forward, and what is missing.

    This is the second half of the pass and it is deliberately separate: it needs a
    Railway API token and it ends by restarting the service once, so it carries its
    own marker and an admin can run it again from the panel without re-running the
    switch pass above.
    """
    rows = []
    for item in ports.catalog():
        found = ports.entry(item['port'])
        rows.append({**item, 'proxied': bool(found),
                     'public_host': str((found or {}).get('host') or ''),
                     'public_port': int((found or {}).get('port') or 0)})
    return rows


def railway_ports(force=False):
    """Create one TCP proxy per enabled capability, then learn the public ports.

    Returns ``{'ok', 'created', 'known', 'reason'}``. Nothing is created for a
    capability whose switch is off (a public port in front of a listener nobody
    binds is exactly the unrequested change the rest of the panel refuses to
    make), and nothing is ever invented: an API failure is reported verbatim so
    the card can show it.
    """
    if not force and (settings.environment or '').lower() == 'test':
        return {'ok': False, 'created': [], 'known': [], 'reason': 'محیط تست'}
    if _setting(ports.DONE) and not force:
        return {'ok': True, 'created': [], 'known': [], 'reason': '', 'skipped': True}
    if not railway.configured():
        return {'ok': False, 'created': [], 'known': [],
                'reason': 'برای ساخت خودکار پورت‌های TCP این‌ها لازم است: ' + '، '.join(railway.missing())}
    known, error = railway.proxies()
    if error:
        return {'ok': False, 'created': [], 'known': [], 'reason': error}
    ports.merge(known)
    created, failed = [], []
    for item in ports.catalog():
        if not item['enabled'] or ports.proxied(item['port']):
            continue
        proxy, reason = railway.create(item['port'])
        if reason:
            failed.append({'id': item['id'], 'port': item['port'], 'reason': reason})
            continue
        ports.merge([proxy])
        created.append(item['port'])
    _store(ports.DONE, str(int(time.time())))
    if created:
        # A TCP proxy created through the API is only active after the service is
        # redeployed (the API says so itself), and the container is where this
        # pass runs — so the pass ends by asking for exactly that one restart.
        ok, reason = railway.redeploy()
        if not ok:
            failed.append({'id': 'redeploy', 'port': 0, 'reason': reason})
    # An admin who enables another capability later creates its proxy in a *second*
    # run, so the log is folded in rather than replaced — the card is the only place
    # that says which ports this deployment ever had created for it.
    known_log = [part for part in (_setting(ports.LOG) or '').split(',') if part.isdigit()]
    _store(ports.LOG, ','.join(dict.fromkeys(known_log + [str(port) for port in created])))
    return {'ok': not failed, 'created': created, 'known': [int(key) for key in ports.load()],
            'failed': failed, 'reason': '' if not failed else failed[0]['reason']}


def apply(force=False):
    """Switch on what needs no decision, once. Returns the capability ids enabled.

    ``force`` is for tests and for an admin who wants the pass re-run: the normal
    call skips the test environment so the suite never depends on it.
    """
    if not allowed():
        return []
    if not force and (settings.environment or '').lower() == 'test':
        return []
    changed = []
    if not _setting(DONE):
        for item in candidates():
            if item['id'] == 'webrelay' and item['ready']:
                # The switch only: the domain stays empty until an admin names one,
                # so the link follows whatever hostname this deployment is served on.
                webrelay.save({'enabled': '1'})
                changed.append(item['id'])
        _store(DONE, str(int(time.time())))
    try:
        outcome = railway_ports(force=force)
    except Exception as exc:
        outcome = {'created': [], 'reason': f'{type(exc).__name__}: {exc}'}
    if outcome.get('created'):
        changed.append('railway')
    if changed:
        _store(LOG, ','.join(dict.fromkeys(changed)))
    return changed


def railway_state():
    """What the panel shows about the TCP-proxy half of the pass."""
    return {
        'api': railway.info(),
        'done': bool(_setting(ports.DONE)),
        'ran_at': int(_setting(ports.DONE) or 0),
        'created': [int(part) for part in (_setting(ports.LOG) or '').split(',') if part.isdigit()],
        'candidates': port_candidates(),
        'proxies': ports.status(),
    }


def state():
    """What the panel shows about the pass: when it ran, what it did, what is left."""
    return {
        'allowed': allowed(),
        'done': bool(_setting(DONE)),
        'ran_at': int(_setting(DONE) or 0),
        'applied': [part for part in (_setting(LOG) or '').split(',') if part],
        'candidates': candidates(),
        'railway': railway_state(),
    }
