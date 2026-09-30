"""The half of a fresh deployment's setup that carries no judgement.

Everywhere else the panel's rule is «off until an admin turns it on deliberately»,
and that rule is now the rule here too. This module used to carve out one exception:
**Telegram Desktop's WEB proxy**, whose carrier is the deployment's own HTTPS name
and a same-origin WebSocket (``app/telegram/webrelay.py``), so it needs no port and
no credential and every fresh deploy switched it on.

That exception is gone, and the reason is measured rather than theoretical: a domain
that answers ``/?bridge=<capability>`` and serves a same-origin ``tproxy-v1`` socket
is *recognisable* to a network that blocks Telegram, and an address that gets
recognised that way stops answering for everyone — the panel included. A deployment
whose own URL only opens through a VPN is a worse failure than one Telegram proxy
staying off, and it is the failure this pass was quietly causing. So the WEB proxy is
an admin decision like every other switch, and **the pass switches nothing on**.

What is left is the pass's real, judgement-free half. On Railway a raw TCP port is a
**TCP proxy**, its public port is random, and only an API call can create it (see
:mod:`app.railway`). So when a Railway API token is present the pass creates one
proxy per capability an admin has *already* switched on, records the forwarded host
and port (:mod:`app.ports`), and redeploys the service once so those proxies are
active — which is what turns «the link points at a port nothing forwards» into a link
that works, with no dashboard visit at all. Nothing else is implied: a capability
whose switch is off gets no proxy, because a public port in front of a listener
nobody binds is a port that answers nothing. Without a token the pass creates nothing
and says exactly which variable is missing.

The pass runs **once**: the marker is a setting, so a proxy an admin deletes stays
deleted across every later boot. It can also be re-run by hand from the panel.

The TCP-proxy half has one Railway-specific hazard it closes by itself: creating a
TCP proxy makes Railway hand the service that proxy's *application* port as ``PORT``,
and that port is one Xray already listens on — so the pass pins the port the HTTP
edge is really on (``railway.pin_http_port``) before it redeploys, instead of leaving
behind a panel that crash-loops on a port it does not own.

A marker is written only once the pass has nothing left to do, and that is now true
on the very first boot: the pass itself has no switch to flip. So the marker goes
down at once, every switch an admin sets — in either direction — is never touched
again, and the WEB proxy stays available as a candidate the admin can turn on.
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
    """The capabilities an admin could switch on here, with the reason they are not.

    A candidate is only ever «enable the WEB proxy and let it follow this
    deployment's own hostname»: the domain is left empty on purpose, so a redeploy
    that changes the Railway domain keeps publishing working links.

    It is a **candidate** and not a step of the pass for one reason worth naming on
    the card and in the code: the carrier *is* the panel's own address, so a network
    that blocks Telegram can recognise this deployment by it. Turning it on is the
    admin's call, and the reason to leave it off is real rather than theoretical.
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
        # The HTTP edge first. On Railway, the moment a TCP proxy exists the service
        # is handed that proxy's *application* port as ``PORT`` — and that port is
        # the one Xray is already listening on for Reality, AnyTLS or MTProto, so the
        # panel would come back up on a port somebody else owns and die on «address
        # already in use». That is unrecoverable from inside the app (a crash-looping
        # panel has no card to show the reason), so the pass pins the port while this
        # container is still on it — before the redeploy that would move it.
        pinned, reason = railway.pin_http_port()
        if not pinned:
            failed.append({'id': 'http-port', 'port': 0, 'reason': reason})
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
    """Retire the pass. Returns the capability ids it enabled — always empty now.

    Nothing is switched on by itself. The one capability this pass used to enable is
    carried by the panel's own domain, and a domain that carries Telegram-proxy
    traffic is the domain a network blocks — so switching it on with no admin in the
    loop is exactly how a deployment's own address stops answering and the panel
    starts needing a VPN. ``force`` is kept for the panel's own button and for tests;
    it only re-runs the Railway TCP-proxy half.
    """
    if not allowed():
        return []
    if not force and (settings.environment or '').lower() == 'test':
        return []
    changed = []
    if not _setting(DONE):
        # «Nothing left to do» is true immediately now: the pass flips no switch,
        # and the WEB proxy below is the admin's decision rather than a step of ours.
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
