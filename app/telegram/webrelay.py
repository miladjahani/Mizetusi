"""Telegram Desktop's **WEB** proxy, served from this deployment's own domain.

Telegram Desktop 7.1 added a fourth proxy type, ``WEB``. It is *not* a transport
in the VPN sense and it is the only Telegram proxy that needs no raw TCP port at
all — which is exactly why it belongs on a forwarder like Railway:

```
Telegram Desktop → hidden WebView → https://<our-domain>/?bridge=<capability>
                 → same-origin WebSocket → relay → MTProxy → Telegram
```

The client opens no MTProto socket. A hidden native WebView loads a page **from
this deployment**, and that page shuttles multiplexed MTProto frames over a
same-origin WebSocket. What a censor sees is a genuine browser TLS handshake
with a CA-chained certificate and then HTTP — because it is one.

Two consequences shape everything below:

* **the page and the WebSocket must come from this origin.** Telegram treats
  ``https://<server>/`` and its socket as one site, so the public Host has to be
  restored when the request is handed to the loopback relay;
* **the link carries a ``dd`` secret, not ``ee``.** The relay is a raw byte pipe
  and adds no TLS-emulation record, so the client reports a FakeTLS secret as
  unsupported for a WEB proxy. Port 443 is implicit and never appears in a WEB
  link.

The relay itself is mtproto.zig's ``mtproto-proxy web-relay``, pinned in the
image like every other third-party binary this deployment ships. Its data plane
is a *second* instance of the same binary bound to ``127.0.0.1``: the relay is
what dials it (with a PROXY-v2 header, so the real browser address survives), no
client ever does, and nothing on this host has to expose another port.

The panel's job is therefore narrow and honest: render the config, keep the two
processes alive, serve the bridge on our own domain, and hand out
``tg://webproxy`` links only while a WebView would really connect.
"""
import asyncio
import base64
import hashlib
import hmac
import os
import re as _re
import secrets as _secrets
import socket
import urllib.parse

import httpx
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import HTMLResponse

from app import runtime
from app.config import settings
from app.db import execute, row

# Settings keys. The secret is stored, not regenerated: it lives *inside* every
# link a user already added, so a redeploy must not reissue it.
ENABLED = 'tg_web_relay_enabled'
SECRET = 'tg_web_relay_secret'
DOMAIN = 'tg_web_relay_domain'
SESSIONS = 'tg_web_relay_sessions'
STREAMS = 'tg_web_relay_streams'

# Where the bridge page opens its socket. Kept in step with ``[web].ws_path`` in
# the rendered config, so the page the relay serves and the route this module
# proxies cannot drift apart.
WS_PATH = settings.webrelay_ws_path

router = APIRouter()

# One entry per process: ``core`` is the loopback data plane, ``relay`` is the
# half that speaks the browser's WebSocket. Both are the same binary.
_procs = {'core': None, 'relay': None}
_hash = None
_state = {'running': False, 'error': '', 'warning': ''}


# ------------------------------------------------------------------- settings
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


def binary():
    return settings.webrelay_binary


def available():
    """Whether the relay binary is really installed in this image."""
    return os.path.exists(binary())


def config_path():
    return settings.webrelay_config


def enabled():
    """Whether an admin switched the WEB proxy on (off by default)."""
    return (_setting(ENABLED) or '0') == '1'


def domain():
    """The public host the WebView loads, without a scheme.

    This is both the ``server`` of the link and the Host the relay selects the
    bridge page by, so it is the deployment's own public hostname — never the
    loopback address the panel talks to.
    """
    raw = str(_setting(DOMAIN) or '').strip().lower()
    if raw:
        raw = raw.split('://')[-1].split('/')[0]
        raw = raw.rsplit(':', 1)[0] if raw.count(':') == 1 else raw
    return raw or (runtime.host() or '')


def port():
    return int(settings.webrelay_port)


def backend_port():
    return int(settings.webrelay_backend_port)


def sessions():
    try:
        value = int(_setting(SESSIONS) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if 1 <= value <= 64 else int(settings.webrelay_sessions)


def streams():
    try:
        value = int(_setting(STREAMS) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if 1 <= value <= 256 else int(settings.webrelay_streams)


def max_connections():
    """The data plane's ceiling, derived from the relay's own caps.

    mtproto.zig refuses to pretend: one WEB client holds a masked connection plus
    one connection per logical stream, and the binary warns when the total does
    not fit. Half of the ceiling is left to everything else.
    """
    return max(256, 2 * sessions() * (streams() + 1))


# --------------------------------------------------------------------- secret
def secret(create=True):
    """The 32-hex MTProto secret this proxy authenticates with.

    One secret for the deployment, like the MTProto card: it is stored in the
    database so a redeploy keeps every link that was already handed out, and
    rotating it revokes them all at once.
    """
    value = str(_setting(SECRET) or '').strip()
    if len(value) == 32 and all(char in '0123456789abcdef' for char in value):
        return value
    if not create:
        return value
    value = _secrets.token_hex(16)
    _store(SECRET, value)
    return value


def link_secret():
    """The ``dd…`` form a WEB link carries (never the ``ee`` FakeTLS one)."""
    raw = secret()
    return ('dd' + raw) if raw else ''


def rotate_secret():
    """Issue a new secret — every WEB link handed out before it stops working."""
    value = _secrets.token_hex(16)
    _store(SECRET, value)
    return value


# ---------------------------------------------------------------------- state
def reachable():
    """``(ok, reason)`` — could a WebView outside really reach the bridge?"""
    if not available():
        return False, f'باینری mtproto-proxy نصب نیست ({binary()})'
    if not domain():
        return False, 'دامنهٔ عمومی پیدا نشد؛ PUBLIC_BASE_URL یا NEXUS_PUBLIC_DOMAIN را ست کنید'
    return True, ''


def running():
    """Whether both halves are up (the data plane alone serves nobody)."""
    return all(proc is not None and proc.returncode is None for proc in _procs.values())


def published():
    """Whether the ``tg://webproxy`` link may be handed to a user right now."""
    if not enabled():
        return False
    if not reachable()[0]:
        return False
    return running()


def links():
    """The links a user adds, or ``None`` while nothing is published.

    Port 443 is implicit: Telegram Desktop refuses a WEB link that names a port,
    because the carrier *is* an ordinary HTTPS site.
    """
    host, value = domain(), link_secret()
    if not published() or not (host and value):
        return None
    query = urllib.parse.urlencode({'server': host, 'secret': value})
    return {
        'server': host, 'secret': value,
        'tg': 'tg://webproxy?' + query,
        'tme': 'https://t.me/webproxy?' + query,
    }


# --------------------------------------------------------------------- config
def config_text():
    """The ``mtproto-proxy`` config both processes read.

    One file for both modes on purpose — ``mtproto-proxy <path>`` runs the data
    plane and ``mtproto-proxy web-relay <path>`` runs the relay — so there is a
    single place a port, a secret or a domain can be wrong.
    """
    front = str(settings.telegram_mtproto_domain or '').strip() or 'www.cloudflare.com'
    lines = [
        '# NEXUS-generated mtproto-proxy config — see app/telegram/webrelay.py',
        '#',
        '# Two processes read this file: the data plane (loopback only, dialled by',
        '# the relay) and the WEB relay itself (dialled by the panel, which owns the',
        '# public HTTPS name). Nothing here needs a public raw TCP port.',
        '',
        '[general]',
        '# MiddleProxy is what makes media (and promotion tags) work for accounts',
        '# without Telegram Premium; without it photos and videos do not load.',
        'use_middle_proxy = true',
        '',
        '[server]',
        'port = %d' % backend_port(),
        'bind_address = "127.0.0.1"',
        'public_ip = "%s"' % domain(),
        'public_port = 443',
        '# The relay prefixes every backend connection with a PROXY v2 header so the',
        '# real browser address reaches Telegram; the listener is loopback-only, so',
        '# nothing untrusted can forge it.',
        'accept_proxy_protocol = true',
        'max_connections = %d' % max_connections(),
        '# The default 2048 KiB is what a single 1 MiB media part plus its framing',
        '# needs; halving it saves memory and breaks Stories and video downloads, so',
        '# the connection ceiling is traded instead (buffers are allocated lazily).',
        'middleproxy_buffer_kb = 2048',
        'log_level = "warn"',
        '',
        '[censorship]',
        'tls_domain = "%s"' % front,
        '# Only the relay may use the direct-obfuscated transport, and it may only',
        '# because it connects from loopback — see [web] below.',
        'fake_tls_only = true',
        'mask = false',
        'drs = true',
        'fast_mode = true',
        '',
        '[web]',
        'enabled = true',
        'domain = "%s"' % domain(),
        'listen = "127.0.0.1"',
        'port = %d' % port(),
        'backend = "127.0.0.1:%d"' % backend_port(),
        'ws_path = "%s"' % WS_PATH,
        'check_origin = true',
        'max_sessions = %d' % sessions(),
        'max_streams = %d' % streams(),
        '',
        '[access.users]',
        '# One secret, stored in the database: it is inside every link already',
        '# handed out, so it survives a redeploy and rotation revokes them all.',
        'nexus = "%s"' % (secret() or '0' * 32),
        '',
    ]
    return '\n'.join(lines)


def _write(text):
    path = config_path()
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)
    return path


def validate_argv(path):
    """How to make the binary parse the config without binding anything."""
    return [binary(), '--check-config', path]


def core_argv(path):
    return [binary(), path]


def relay_argv(path):
    return [binary(), 'web-relay', path]


async def _validate(path):
    try:
        proc = await asyncio.create_subprocess_exec(
            *validate_argv(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
    except Exception as exc:
        return f'mtproto-proxy اجرا نشد ({type(exc).__name__})'
    if proc.returncode != 0:
        return ((err or out) or b'').decode(errors='ignore').strip()[-400:] or 'invalid config'
    return None


# ------------------------------------------------------------------ lifecycle
async def _stop():
    for name, proc in list(_procs.items()):
        _procs[name] = None
        if not proc or proc.returncode is not None:
            continue
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


async def _spawn(name, argv, label):
    """Start one half and fail loudly if it exits straight away."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _procs[name] = proc
    await asyncio.sleep(0.3)
    if proc.returncode is not None:
        detail = (await proc.stderr.read()).decode(errors='ignore').strip()[-400:]
        raise RuntimeError(detail or f'{label} بلافاصله بسته شد')
    return proc


async def start_or_reload(force=False):
    """Run both halves, reload them when the config changed, stop when switched off."""
    global _hash
    if not settings.cores_enabled or not available():
        await _stop()
        _state.update({'running': False, 'error': '' if settings.cores_enabled else 'غیرفعال است',
                       'warning': '' if available() else f'باینری mtproto-proxy نصب نیست ({binary()})'})
        return {'running': False, 'reason': _state.get('warning') or None}
    if not enabled():
        await _stop()
        _state.update({'running': False, 'error': '', 'warning': ''})
        return {'running': False, 'reason': None}
    ok, reason = reachable()
    if not ok:
        # Switched on but unreachable: a relay nobody can load is a process for
        # its own sake, so it is stopped and the card shows the reason.
        await _stop()
        _state.update({'running': False, 'error': '', 'warning': reason})
        return {'running': False, 'reason': reason}
    text = config_text()
    digest = hashlib.sha256(text.encode()).hexdigest()
    if running() and _hash == digest and not force:
        _state.update({'running': True, 'error': ''})
        return {'running': True, 'pid': _procs['core'].pid, 'reloaded': False}
    path = _write(text)
    problem = await _validate(path)
    if problem:
        _state.update({'error': problem, 'running': running()})
        return {'running': running(), 'reason': problem}
    await _stop()
    try:
        core = await _spawn('core', core_argv(path), 'data plane')
        relay = await _spawn('relay', relay_argv(path), 'relay')
    except Exception as exc:
        await _stop()
        _state.update({'running': False, 'error': f'اجرای relay ناموفق بود ({exc})'})
        return {'running': False, 'reason': _state['error']}
    _hash = digest
    _state.update({'running': True, 'error': ''})
    return {'running': True, 'pid': core.pid, 'relay_pid': relay.pid, 'reloaded': True}


async def sync(force=False):
    if not settings.cores_enabled:
        return {'running': False, 'reason': 'غیرفعال است'}
    result = await start_or_reload(force=force)
    if result.get('running'):
        # A process that still carries the previous config is the one failure a
        # card cannot show: the link keeps working, the admin's change silently
        # did not. force makes the next pass converge on the rendered file.
        _state.update({'warning': ''})
    return result


async def stop_all():
    await _stop()


# -------------------------------------------------------------------- linking
def status():
    """What the panel card reads: the switch, both processes, the links, why not."""
    ok, reason = reachable()
    if not reason and enabled() and not running():
        reason = _state.get('error') or _state.get('warning') or ''
    return {
        'enabled': enabled(),
        'installed': available(),
        'binary': binary(),
        'engine': 'mtproto-proxy (mtproto.zig) · web-relay',
        'domain': domain(),
        'host': domain(),
        'port': port(),
        'backend_port': backend_port(),
        'sessions': sessions(),
        'streams': streams(),
        'max_connections': max_connections(),
        'http': bool(ok),
        'reachable': ok,
        'reason': reason,
        'running': running(),
        'core_pid': _procs['core'].pid if _procs['core'] and _procs['core'].returncode is None else None,
        'relay_pid': _procs['relay'].pid if _procs['relay'] and _procs['relay'].returncode is None else None,
        'published': published(),
        'secret': secret() or '',
        'link_secret': link_secret(),
        'links': links(),
        'ws_path': WS_PATH,
        'url': f'https://{domain()}/' if domain() else '',
        'config': config_text(),
    }


def notes():
    """The one-liners that turn a refusal into something an admin can act on."""
    out = [
        'این تنها پروکسی تلگرامی است که پورت خام TCP نمی‌خواهد؛ روی دامنهٔ همین پنل و پورت ۴۴۳ کار می‌کند '
        '(روی Railway بدون هیچ TCP Proxy).',
        'فقط تلگرام دسکتاپ ۷.۱ به بعد و فقط برای پیام‌ها؛ تماس صوتی پشتیبانی نمی‌شود.',
    ]
    if not available():
        out.append(f'باینری relay در این ایمیج نیست ({binary()}); بدون آن لینکی ساخته نمی‌شود.')
    if not domain():
        out.append('دامنهٔ عمومی پنل معلوم نیست؛ آدرس پایه را در تنظیمات ست کنید.')
    out.append('کاربر لینک tg://webproxy را در تلگرام دسکتاپ اضافه می‌کند (تنظیمات → پیشرفته → نوع اتصال → '
               'افزودن پروکسی → نوع WEB). بقیهٔ ترافیک دست‌نخورده می‌ماند.')
    return out


def save(updates):
    """Store the switch, the domain and the relay's caps. Returns the changed keys.

    Validation happens here, before anything is written, so a rejected save never
    leaves the card showing «روشن» for a relay that was never started.
    """
    plan = []
    if 'enabled' in updates:
        value = str(updates['enabled']).strip().lower()
        plan.append((ENABLED, '0' if value in ('0', 'false', 'off', 'no') else '1'))
    if 'domain' in updates:
        name = str(updates['domain'] or '').strip().lower()
        name = name.split('://')[-1].split('/')[0]
        if name and ('.' not in name or ' ' in name):
            raise ValueError('دامنهٔ پروکسی WEB معتبر نیست (مثال: nexus.up.railway.app)')
        plan.append((DOMAIN, name))
    if 'sessions' in updates:
        try:
            value = int(float(str(updates['sessions']).strip()))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('تعداد نشست‌های همزمان باید یک عدد باشد')
        if not 1 <= value <= 64:
            raise ValueError('تعداد نشست‌های همزمان باید بین ۱ و ۶۴ باشد')
        plan.append((SESSIONS, str(value)))
    if 'streams' in updates:
        try:
            value = int(float(str(updates['streams']).strip()))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('تعداد استریم هر نشست باید یک عدد باشد')
        if not 1 <= value <= 256:
            raise ValueError('تعداد استریم هر نشست باید بین ۱ و ۲۵۶ باشد')
        plan.append((STREAMS, str(value)))
    for key, value in plan:
        _store(key, value)
    return [key for key, _ in plan]


# ------------------------------------------------------------- bridge + socket
def bridge_capability():
    """The capability a bridge URL carries, derived the way Telegram derives it.

    Telegram Desktop builds ``https://<host>/?bridge=<capability>`` from the
    ``dd…`` secret and the host the link names, with the derivation the relay
    implements (``base64url(hmac_sha256(\xdd || secret, "tdesktop-web-proxy-bridge-v1\n" || host))``).
    The panel needs the same value for *this* deployment for one reason only: so
    its own check can load the page and open the socket the way the client does,
    instead of reporting that a port is bound. Nothing is handed out from here —
    a link still carries the secret and Telegram still makes its own capability.
    """
    raw = secret(create=False)
    host = domain()
    if not host or len(raw) != 32:
        return ''
    try:
        key = b'\xdd' + bytes.fromhex(raw)
    except ValueError:
        return ''
    digest = hmac.new(key, b'tdesktop-web-proxy-bridge-v1\n' + host.encode(),
                      hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()


def _page_token(html):
    """The carrier token the relay puts in the bridge page it just served."""
    found = _re.search(r'TOKEN="([^"]+)"', html or '')
    return found.group(1) if found else ''


async def _dial_relay(subprotocols=None, forwarded=None, query='', timeout=12.0):
    """Open the relay's socket half with **one** ``Host`` header — ours.

    The relay serves one site and picks it by ``Host``, compared verbatim. The
    WebSocket client always writes the host from the URI into that header and
    *appends* any header handed to it separately, so passing the public host as an
    extra header reaches the relay as two ``Host`` values whose first one is
    ``127.0.0.1:<port>`` — the page loads, the socket is answered with ``404``, and
    the WEB proxy connects nothing. The socket is therefore dialled here and handed
    over already connected, while the URI names the public host *without* a port:
    the URI only builds that header, the bytes still go down the loopback socket
    this function opened.
    """
    host = domain()
    if not host:
        raise RuntimeError('دامنهٔ عمومی پروکسی WEB پیدا نشد')
    sock = socket.create_connection(('127.0.0.1', port()), timeout=min(float(timeout), 5.0))
    try:
        from websockets.asyncio.client import connect
        return await connect(f'ws://{host}{WS_PATH}{query}', sock=sock,
                             additional_headers=dict(forwarded or {}),
                             subprotocols=list(subprotocols) if subprotocols else None,
                             open_timeout=timeout, ping_interval=None, max_size=None)
    except BaseException:
        try:
            sock.close()
        except Exception:
            pass
        raise


def _client_address(request):
    """The browser's real address, so it survives the hop to the relay.

    The relay reads it (right-most entry of the header it was told to trust) and
    puts it in the PROXY-v2 header it sends to the data plane, which is how
    Telegram sees the client rather than this container.
    """
    try:
        from app.core import clientip
        value = clientip.client_ip(request, extra_trusted=_setting('trusted_proxy_cidrs') or '')
        if value:
            return value
    except Exception:
        pass
    return getattr(getattr(request, 'client', None), 'host', '') or ''


def _off_page():
    return HTMLResponse(
        '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">'
        '<title>پروکسی WEB خاموش است</title>'
        '<body style="font-family:system-ui;background:#0b120a;color:#dbe7cf;padding:28px">'
        '<h2>پروکسی WEB تلگرام خاموش است</h2>'
        '<p>برای فعال کردن آن، در پنل به تب «پروکسی تلگرام» بروید و بخش '
        '<b>WEB — تلگرام دسکتاپ ۷.۱+</b> را روشن کنید.</p></body></html>', status_code=403)


async def bridge(request: Request):
    """Serve the page the WebView loads, straight from the loopback relay.

    Telegram Desktop opens ``https://<our-domain>/?bridge=<capability>`` and then
    talks to a same-origin socket, so this has to answer on *our* host: the public
    Host is restored explicitly (the relay would otherwise see 127.0.0.1 and
    refuse to serve the bridge at all), and nothing about the query is touched —
    the capability in it is what the relay verifies.
    """
    if not published():
        return _off_page()
    target = f'http://127.0.0.1:{port()}/'
    if request.url.query:
        target += '?' + str(request.url.query)
    headers = {
        'host': domain(),
        'accept': request.headers.get('accept') or '*/*',
        'accept-encoding': 'identity',
        'user-agent': request.headers.get('user-agent') or 'nexus-webrelay',
        'x-forwarded-proto': 'https',
        'x-forwarded-host': domain(),
    }
    address = _client_address(request)
    if address:
        headers['x-forwarded-for'] = address
    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False)
    try:
        upstream = await client.get(target, headers=headers)
    except Exception as exc:
        await client.aclose()
        return HTMLResponse(
            '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">'
            '<title>relay پاسخ نداد</title>'
            '<body style="font-family:system-ui;background:#0b120a;color:#dbe7cf;padding:28px">'
            '<h2>relay پروکسی WEB پاسخ نداد</h2>'
            f'<p style="direction:ltr;color:#ffc3cc">{type(exc).__name__}: {exc}</p>'
            '</body></html>', status_code=502)
    body = await upstream.aread()
    await upstream.aclose()
    await client.aclose()
    pairs = [(key, value) for key, value in upstream.headers.multi_items()
             if key.lower() not in ('content-length', 'transfer-encoding', 'connection',
                                    'keep-alive', 'content-encoding')]
    from app.telegram.webapp import build_response
    return build_response(upstream.status_code, pairs, body=body)


@router.websocket(WS_PATH)
async def web_relay_socket(websocket: WebSocket):
    """Tunnel the bridge's own WebSocket to the relay, subprotocol included.

    This is the half a plain HTTP reverse proxy cannot do, and without it the page
    loads and then never connects. The carrier token travels in
    ``Sec-WebSocket-Protocol`` (``tproxy-v1.<token>``), so that header has to be
    offered upstream and echoed back to the client — Telegram's WebView refuses
    the socket otherwise.

    The dial itself goes through :func:`_dial_relay`, because the ``Host`` header
    is what the relay selects the bridge by and a second copy of it is a proxy that
    silently serves the page and nothing else.
    """
    if not published():
        await websocket.close(code=1013)
        return
    offered = [part.strip() for part in
               (websocket.headers.get('sec-websocket-protocol') or '').split(',') if part.strip()]
    forwarded = {'x-forwarded-proto': 'wss', 'x-forwarded-host': domain()}
    origin = websocket.headers.get('origin')
    if origin:
        forwarded['origin'] = origin
    address = _client_address(websocket)
    if address:
        forwarded['x-forwarded-for'] = address
    query = ('?' + str(websocket.url.query)) if websocket.url.query else ''
    try:
        upstream = await _dial_relay(subprotocols=offered, forwarded=forwarded, query=query)
    except Exception:
        await websocket.close(code=1011)
        return
    from app.telegram.webapp import _pump

    await websocket.accept(subprotocol=upstream.subprotocol)
    try:
        async with upstream:
            await _pump(websocket, upstream)
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------- probe
async def probe(timeout=6.0):
    """Can a WebView outside really connect *here*? Both halves, for real.

    The question «does the WEB proxy work» is not «is the port bound»: a page that
    loads while its socket is refused looks healthy in every cheap check and
    connects nothing. So this does exactly what Telegram Desktop does — loads
    ``/?bridge=<capability>`` for our own host, takes the token the page carries,
    and opens the same-origin socket with ``tproxy-v1.<token>`` — and reports the
    subprotocol the relay echoed back as the proof that the carrier handshake
    completed. «The port is bound» is the question that let a broken carrier look
    healthy, so this never asks it.
    """
    host = domain()
    capability = bridge_capability()
    url = f'http://127.0.0.1:{port()}/?bridge={capability}' if capability else ''
    result = {'ok': False, 'error': '', 'host': host, 'url': url, 'status': 0, 'bytes': 0,
              'page': {'ok': False, 'status': 0, 'bytes': 0, 'error': ''},
              'socket': {'ok': False, 'protocol': '', 'error': ''}}
    if not enabled():
        result['error'] = 'پروکسی WEB خاموش است'
        return result
    if not (host and capability):
        result['error'] = 'دامنه یا secret پروکسی WEB آماده نیست'
        return result
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers={'host': host, 'accept': 'text/html',
                                                      'accept-encoding': 'identity'})
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        result['page']['error'] = result['error']
        return result
    result['status'] = response.status_code
    result['bytes'] = len(response.content)
    result['page'] = {'ok': response.status_code == 200, 'status': response.status_code,
                      'bytes': len(response.content),
                      'error': '' if response.status_code == 200 else
                               f'صفحهٔ bridge با کد {response.status_code} پاسخ داد'}
    token = _page_token(response.text) if result['page']['ok'] else ''
    if not token:
        result['socket']['error'] = (result['page']['error'] or
                                     'صفحهٔ bridge توکن سوکت را برنگرداند')
        result['error'] = result['socket']['error']
        return result
    try:
        upstream = await _dial_relay(subprotocols=['tproxy-v1.' + token], timeout=timeout)
    except Exception as exc:
        result['socket']['error'] = f'{type(exc).__name__}: {exc}'
        result['error'] = result['socket']['error']
        return result
    result['socket'] = {'ok': True, 'protocol': upstream.subprotocol or '', 'error': ''}
    try:
        await upstream.close()
    except Exception:
        pass
    result['ok'] = True
    return result
