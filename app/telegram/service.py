"""One place the panel, the API and the status window read Telegram proxies from.

The features in this package answer different questions and are published under
different rules, but they are shown together (one card in the panel, one section
in the status window), so the shape of that answer lives here rather than being
reassembled by each caller:

* :func:`payload` — the admin view: every switch, listener, link and reason;
* :func:`save` / :func:`reconcile` — store what the card sent, then bring the
  listeners to exactly that state and report what really came up;
* :func:`portal_payload` — the end-user view: *their* proxy links, and nothing
  about the other users.

**One proxy type by default.** A user is handed *one kind of Telegram proxy* —
the WEB one (``tg://webproxy``) — because it is the only kind that needs no raw
TCP port and therefore the only kind that works on every host this image runs on,
including a forwarder like Railway with no TCP proxy at all. The other three (a
``tg://`` MTProto link, the HTTP/SOCKS5 web proxies, the web.telegram.org path)
are still here and still maintained, but they are published only after an admin
switches the panel to «همهٔ انواع» (:func:`mode`), and never handed to a user in
the default mode.

Everything is derived from live state on every request, so a card can never show
a link the deployment is not actually serving.
"""
from app import runtime
from app.config import settings
from app.telegram import mtproto, webapp, webrelay, webproxy

# Which Telegram proxy types this deployment publishes. «web» is the default and
# the one an automatic deploy lands on; «all» restores the other three for an
# admin who arranged a raw TCP port (a VPS, or a Railway TCP proxy).
MODE = 'tg_proxy_mode'
WEB_ONLY = 'web'
ALL = 'all'


def _setting(key, default=None):
    from app.db import row
    try:
        found = row('SELECT value FROM settings WHERE key=?', (key,))
    except Exception:
        found = None
    value = (found or {}).get('value') if found else None
    return value if value not in (None, '') else default


def mode():
    """``'web'`` (the default) or ``'all'``."""
    return ALL if (_setting(MODE) or '').strip().lower() == ALL else WEB_ONLY


def web_only():
    """Whether only the WEB proxy may be published — the default."""
    return mode() == WEB_ONLY


def enabled():
    """Whether any Telegram proxy this deployment publishes is switched on."""
    if web_only():
        return bool(webrelay.enabled())
    return bool(mtproto.enabled() or webapp.enabled() or webrelay.enabled()
                or any(item['enabled'] for item in webproxy.chosen()))


def host():
    """The address clients dial for the raw-port proxies."""
    return mtproto.host() or webproxy.host()


def notes():
    """The one-liners that turn a refusal into something an admin can act on."""
    out = []
    if not runtime.has_tcp():
        out.append('این پلتفرم پورت خام نمی‌دهد؛ پروکسی MTProto و وب‌پروکسی به یک پورت TCP نیاز دارند '
                   '(روی VPS آزاد است، روی Railway هر پورت یک TCP Proxy می‌خواهد).')
    if not mtproto.available():
        out.append(f'باینری mtg در این ایمیج نیست ({mtproto.binary()}); پروکسی MTProto بدون آن منتشر نمی‌شود.')
    out.append('پروکسی MTProto فقط تلگرام را عبور می‌دهد؛ برای بقیهٔ ترافیک همان سابلینک‌های VPN را بدهید.')
    out.append('وب‌پروکسی HTTP/SOCKS5 با همان اعتبار کاربر کار می‌کند؛ غیرفعال کردن کاربر، خط پروکسی او را هم قطع می‌کند.')
    if not webapp.enabled():
        out.append('پروکسی وب تلگرام خاموش است؛ با روشن کردنش، web.telegram.org از همین دامنه باز می‌شود.')
    out.extend(webrelay.notes())
    if web_only():
        out.append('حالت فعلی «فقط پروکسی WEB» است: به کاربران فقط لینک tg://webproxy داده می‌شود '
                   'و خطوط MTProto و وب‌پروکسی منتشر نمی‌شوند. برای برگرداندن آن‌ها حالت را روی '
                   '«همهٔ انواع» بگذارید.')
    else:
        out.append('حالت «همهٔ انواع» روشن است؛ هر سه نوع پروکسی به کاربران داده می‌شود.')
    return out


def counts():
    web = webproxy.status()
    only = web_only()
    return {
        'mtproto': 0 if only else (1 if mtproto.status()['published'] else 0),
        'webproxy': 0 if only else web['counts']['published'],
        'webapp': 0 if only else (1 if webapp.enabled() else 0),
        'webrelay': 1 if webrelay.published() else 0,
        'enabled': sum([0 if only else (1 if mtproto.enabled() else 0),
                        0 if only else (1 if webapp.enabled() else 0),
                        1 if webrelay.enabled() else 0,
                        0 if only else web['counts']['enabled']]),
        'published': sum([0 if only else (1 if mtproto.status()['published'] else 0),
                          0 if only else web['counts']['published'],
                          0 if only else (1 if webapp.enabled() else 0),
                          1 if webrelay.published() else 0]),
        'mode': mode(),
    }


def payload(base=''):
    """The panel's one payload for the Telegram tab."""
    web = webproxy.status()
    return {
        'success': True,
        'enabled': enabled(),
        'mode': mode(),
        'web_only': web_only(),
        'tcp': runtime.has_tcp(),
        'platform': runtime.label(),
        'host': host(),
        'sync_interval': int(settings.telegram_sync_interval),
        'mtproto': mtproto.status(),
        'webrelay': webrelay.status(),
        'webproxy': web,
        'webapp': webapp.status(base),
        'notes': notes(),
        'counts': counts(),
    }


def save(body):
    """Store the switches, ports and fronting values from the card.

    Validation happens inside each module *before* anything is written, and the
    whole body is validated before the first write — a rejected save leaves the
    card exactly as it was instead of half-applied.
    """
    changed = []
    if 'mode' in body:
        value = str(body.get('mode') or '').strip().lower()
        if value not in (WEB_ONLY, ALL):
            raise ValueError('حالت پروکسی تلگرام باید web یا all باشد')
        from app.db import execute
        execute('INSERT INTO settings(key,value) VALUES(?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (MODE, value))
        changed.append(MODE)
    for key, module in (('mtproto', mtproto), ('webrelay', webrelay),
                        ('webproxy', webproxy), ('webapp', webapp)):
        section = body.get(key)
        if isinstance(section, dict):
            changed.extend(module.save(section))
    return changed


async def loop():
    """Keep the MTProto proxy in step with the settings without a restart.

    The web proxies need no loop of their own: they are inbounds of the Xray
    config, which the Xray supervisor already reconciles on its own interval.
    """
    import asyncio
    while True:
        try:
            await mtproto.sync()
            await webrelay.sync()
        except Exception:
            pass
        await asyncio.sleep(max(5, settings.telegram_sync_interval))


async def stop_all():
    await mtproto.stop_all()
    await webrelay.stop_all()


async def reconcile(force=True):
    """Bring the listeners to the state the settings ask for, and report it.

    The Xray engine is restarted here because the web-proxy inbounds live in *its*
    config: enabling the HTTP proxy has to reach the engine that serves it, not
    just the settings table. The MTProto proxy owns its own process.
    """
    from app import xray
    result = {'mtproto': await mtproto.sync(force=force),
              'webrelay': await webrelay.sync(force=force)}
    engine = await xray.start_or_reload(force=force)
    result['xray'] = {'running': bool(engine.get('running')), 'pid': engine.get('pid'),
                      'reloaded': bool(engine.get('reloaded')), 'reason': engine.get('reason') or ''}
    return result


async def probe():
    """Really fetch Telegram Web from here (the card's «تست» button)."""
    return await webapp.probe()


async def probe_webrelay():
    """Load the WEB bridge and open its carrier socket — the WEB card's «تست».

    A different question from :func:`probe`: that one asks whether this host can
    reach web.telegram.org, this one asks whether a WebView outside can reach
    *us*, over the page and the same-origin socket the WEB proxy is made of.
    """
    return await webrelay.probe()


# ------------------------------------------------------------------- portal
def portal_payload(base, user):
    """What one end user gets: their own proxy links, and nothing else.

    ``base`` is the deployment's public base URL, so a link that needs one is
    absolute and copyable. An empty dict means «nothing to show», which is the
    same answer as before this feature existed — the status window simply does
    not grow a section that would be empty.

    In the default mode a user is handed **only the WEB link**. The other proxy
    types are withheld here rather than merely hidden in the panel: what matters
    is not what the status window lists, it is which credential a user ends up
    configuring, and one kind of proxy is one kind of support question.
    """
    if not user:
        return {}
    only = web_only()
    lines = [] if only else webproxy.lines(user.get('username'), user.get('uuid'))
    link = mtproto.links() if (not only and mtproto.published()) else None
    relay = webrelay.links() if webrelay.published() else None
    web = webapp.status(base) if (not only and webapp.enabled()) else None
    if not (lines or link or relay or web):
        return {}
    hint = ('این بخش فقط برای تلگرام است؛ پروکسی WEB را در تلگرام دسکتاپ ۷.۱+ اضافه کنید '
            '(تنظیمات → پیشرفته → نوع اتصال → افزودن پروکسی → نوع WEB) و همین لینک را بچسبانید.')
    if not only:
        hint = ('این بخش فقط برای تلگرام است؛ پروکسی WEB را در تلگرام دسکتاپ ۷.۱+ اضافه کنید '
                '(نوع پروکسی: WEB)، لینک tg:// را روی MTProto برنامه بگذارید، و وب‌پروکسی را در '
                'تنظیمات پروکسی تلگرام دسکتاپ یا مرورگر.')
    return {
        'host': host(),
        'mtproto': ({'url': link['tg'], 'tme': link['tme'], 'secret': link['secret'],
                     'label': f"{link['server']}:{link['port']}"} if link else None),
        'webrel': ({'url': relay['tg'], 'tme': relay['tme'], 'secret': relay['secret'],
                    'label': f"WEB · {relay['server']}"} if relay else None),
        'lines': lines,
        'web': ({'url': web['url'], 'label': 'نسخهٔ وب تلگرام'} if web else None),
        'hint': hint,
    }
