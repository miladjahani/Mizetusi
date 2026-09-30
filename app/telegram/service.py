"""One place the panel, the API and the status window read Telegram proxies from.

The three features in this package answer different questions and are published
under different rules, but they are shown together (one card in the panel, one
section in the status window), so the shape of that answer lives here rather than
being reassembled by each caller:

* :func:`payload` — the admin view: every switch, listener, link and reason;
* :func:`save` / :func:`reconcile` — store what the card sent, then bring the
  listeners to exactly that state and report what really came up;
* :func:`portal_payload` — the end-user view: *their* proxy lines (their own
  username and credential), the shared MTProto link and the web address, and
  nothing about the other users.

Everything is derived from live state on every request, so a card can never show
a link the deployment is not actually serving.
"""
from app import runtime
from app.config import settings
from app.telegram import mtproto, webapp, webproxy


def enabled():
    """Whether any of the three is switched on."""
    return bool(mtproto.enabled() or webapp.enabled()
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
    return out


def counts():
    web = webproxy.status()
    return {
        'mtproto': 1 if mtproto.status()['published'] else 0,
        'webproxy': web['counts']['published'],
        'webapp': 1 if webapp.enabled() else 0,
        'enabled': sum([1 if mtproto.enabled() else 0,
                        1 if webapp.enabled() else 0,
                        web['counts']['enabled']]),
        'published': sum([1 if mtproto.status()['published'] else 0,
                          web['counts']['published'],
                          1 if webapp.enabled() else 0]),
    }


def payload(base=''):
    """The panel's one payload for the Telegram tab."""
    web = webproxy.status()
    return {
        'success': True,
        'enabled': enabled(),
        'tcp': runtime.has_tcp(),
        'platform': runtime.label(),
        'host': host(),
        'sync_interval': int(settings.telegram_sync_interval),
        'mtproto': mtproto.status(),
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
    for key, module in (('mtproto', mtproto), ('webproxy', webproxy), ('webapp', webapp)):
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
        except Exception:
            pass
        await asyncio.sleep(max(5, settings.telegram_sync_interval))


async def stop_all():
    await mtproto.stop_all()


async def reconcile(force=True):
    """Bring the listeners to the state the settings ask for, and report it.

    The Xray engine is restarted here because the web-proxy inbounds live in *its*
    config: enabling the HTTP proxy has to reach the engine that serves it, not
    just the settings table. The MTProto proxy owns its own process.
    """
    from app import xray
    result = {'mtproto': await mtproto.sync(force=force)}
    engine = await xray.start_or_reload(force=force)
    result['xray'] = {'running': bool(engine.get('running')), 'pid': engine.get('pid'),
                      'reloaded': bool(engine.get('reloaded')), 'reason': engine.get('reason') or ''}
    return result


async def probe():
    """Really fetch Telegram Web from here (the card's «تست» button)."""
    return await webapp.probe()


# ------------------------------------------------------------------- portal
def portal_payload(base, user):
    """What one end user gets: their own lines, the shared link, the web address.

    ``base`` is the deployment's public base URL, so the web-proxy links are
    absolute and copyable. An empty dict means «nothing to show», which is the
    same answer as before this feature existed — the status window simply does
    not grow a section that would be empty.
    """
    if not user:
        return {}
    lines = webproxy.lines(user.get('username'), user.get('uuid'))
    link = mtproto.links() if mtproto.published() else None
    web = webapp.status(base) if webapp.enabled() else None
    if not (lines or link or web):
        return {}
    return {
        'host': host(),
        'mtproto': ({'url': link['tg'], 'tme': link['tme'], 'secret': link['secret'],
                     'label': f"{link['server']}:{link['port']}"} if link else None),
        'lines': lines,
        'web': ({'url': web['url'], 'label': 'نسخهٔ وب تلگرام'} if web else None),
        'hint': 'این بخش فقط برای تلگرام است؛ پروکسی MTProto را روی برنامهٔ تلگرام خودتان اضافه کنید و '
                'وب‌پروکسی را در تنظیمات پروکسی تلگرام دسکتاپ یا مرورگر بگذارید.',
    }
