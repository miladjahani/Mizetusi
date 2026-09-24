"""``web.telegram.org`` through this deployment's own domain.

The web version of Telegram is a website, so the only way to unblock it is to
serve it from a host that is not blocked. This module is a **host-allowlisted**
reverse proxy for exactly that: the panel's own domain answers on ``/tg/…``, and
the Cloudflare Worker that already fronts the VPN transports forwards the same
prefix — so a Worker URL the admin already deployed serves Telegram Web too.

Three things make it work, and all three are deliberate:

* **The allowlist is tight.** Only ``web.telegram.org`` (and its ``*.`` subdomains,
  which is where the API and the WebSocket live), ``telegram.org`` and ``t.me``
  may be proxied — everything else is refused. An open proxy is an SSRF hole and
  an abuse magnet; this one can only reach Telegram.
* **A shim rewrites the app's own outbound calls.** The web app opens a
  WebSocket to ``<dc>.web.telegram.org`` and fetches Telegram-hosted resources by
  absolute URL. The injected script routes both through this proxy
  (``/tg/__ws/…`` and ``/tg/__p/…``), so the page keeps working from our domain.
* **The prefix is forwarded by the Worker**, so the same path works behind
  Cloudflare clean IPs when the panel's own address is blocked.

The proxy is off until an admin switches it on (``tg_web_enabled``), and the
status is *measured*: :func:`probe` really fetches the app shell from here and
reports what it got, because «the proxy is configured» is not the same answer as
«this server can reach Telegram».
"""
import asyncio
import re
import urllib.parse

import httpx
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from app.config import settings
from app.db import execute, row

# Hosts the proxy may reach. ``telegram.org`` and ``t.me`` are needed by the login
# flow; the WebSocket/API hosts are ``*.web.telegram.org``.
EXTRA_HOSTS = ('telegram.org', 'www.telegram.org', 't.me')

# A public Telegram channel shown as a real row at the top of Telegram Web's
# chat list. It is deliberately injected by the proxy shim rather than added to
# an account server-side: NEXUS never receives a Telegram API hash or a user's
# MTProto credentials, so pretending the server could join a chat for them would
# be both impossible and unsafe.
SPONSOR_URL = 'https://t.me/milonfig'
SPONSOR_HANDLE = 'milonfig'
SPONSOR_TITLE = 'کانال اسپانسر'

# Hop-by-hop headers are per-connection: passing them on corrupts the response.
DROP_REQUEST = ('host', 'connection', 'content-length', 'transfer-encoding', 'accept-encoding',
                'keep-alive', 'te', 'upgrade', 'proxy-connection', 'expect')
DROP_RESPONSE = ('content-length', 'transfer-encoding', 'connection', 'keep-alive',
                 'content-encoding', 'content-security-policy', 'content-security-policy-report-only',
                 'x-frame-options', 'strict-transport-security', 'alt-svc', 'report-to', 'nel',
                 'cross-origin-opener-policy', 'cross-origin-embedder-policy',
                 # Cookies travel in their own list: they have to be re-scoped to
                 # this domain and there can be several of them.
                 'set-cookie')

ENABLED = 'tg_web_enabled'

router = APIRouter()


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


def enabled():
    """Whether an admin switched the Telegram Web proxy on (off by default)."""
    return (_setting(ENABLED) or '0') == '1'


def path_prefix():
    raw = str(settings.telegram_web_path or '/tg').strip()
    if not raw.startswith('/'):
        raw = '/' + raw
    return raw.rstrip('/') or '/tg'


def origin():
    """The upstream the bare prefix proxies to (``https://web.telegram.org``)."""
    raw = str(settings.telegram_web_origin or 'https://web.telegram.org').strip().rstrip('/')
    return raw or 'https://web.telegram.org'


def origin_parts():
    parsed = urllib.parse.urlparse(origin())
    return {'scheme': parsed.scheme or 'https', 'host': parsed.hostname or 'web.telegram.org',
            'port': parsed.port}


def allowed_host(host):
    """Whether a hostname may be proxied (the allowlist, in one place)."""
    name = str(host or '').strip().lower().rstrip('.')
    if not name or '/' in name or ' ' in name or ':' in name:
        return False
    base = origin_parts()['host'].lower()
    if name == base or name.endswith('.' + base):
        return True
    return any(name == extra or name.endswith('.' + extra) for extra in EXTRA_HOSTS)


def default_host():
    return origin_parts()['host']


def origin_hostport():
    """The origin's host and, when it has one, its port (dev/test deployments)."""
    parts = origin_parts()
    return f"{parts['host']}:{parts['port']}" if parts['port'] else str(parts['host'])


# ----------------------------------------------------------------------- urls
def upstream_url(host, path, query=''):
    """The URL a proxied request really goes to."""
    name = str(host or default_host()).strip().lower()
    parsed = origin_parts()
    if name == parsed['host']:
        base = f"{parsed['scheme']}://{parsed['host']}"
        if parsed['port']:
            base += f":{parsed['port']}"
    else:
        base = 'https://' + name
    url = base + '/' + str(path or '').lstrip('/')
    if query:
        url += ('&' if '?' in url else '?') + str(query)
    return url


def ws_upstream_url(host, path, query=''):
    """The WebSocket the app's own socket is tunnelled to."""
    url = upstream_url(host, path, query)
    parts = origin_parts()
    scheme = 'wss' if (url.startswith('https') or parts['scheme'] == 'https') else 'ws'
    return re.sub(r'^https?', scheme, url, count=1)


def local_url(host, path, query=''):
    """The same request, addressed at this deployment instead."""
    prefix = path_prefix()
    name = str(host or '').strip().lower()
    base = f'{prefix}/__p/{name}' if name and name != default_host() else prefix
    url = base + '/' + str(path or '').lstrip('/')
    if query:
        url += ('&' if '?' in url else '?') + str(query)
    return url


def ws_local_url(host, path, query=''):
    prefix = path_prefix()
    name = str(host or default_host()).strip().lower()
    url = f'{prefix}/__ws/{name}/' + str(path or '').lstrip('/')
    if query:
        url += '?' + str(query)
    return url


# ----------------------------------------------------------------------- shim
def shim():
    """The script that keeps the app inside this proxy.

    Telegram Web computes its API and WebSocket hostnames at runtime
    (``<dc>.web.telegram.org``), so a path prefix alone is not enough: without
    this, the page loads from our domain and then dials Telegram directly — which
    is the one thing the user cannot do. Everything the app opens is rewritten to
    the tunnel, and only Telegram hosts are touched.
    """
    hosts = sorted({default_host(), *(extra for extra in EXTRA_HOSTS)})
    return """
<script>
/* NEXUS · Telegram Web proxy shim — see app/telegram/webapp.py */
(() => {
  const PREFIX = __NEXUS_PREFIX__;
  const HOSTS = __NEXUS_HOSTS__;
  const allowed = (name) => HOSTS.some((host) => name === host || name.endsWith('.' + host));
  const rewrite = (value, socket) => {
    let url;
    try { url = new URL(value, location.href); } catch (error) { return value; }
    if (!allowed(url.hostname)) return value;
    const path = PREFIX + (socket ? '/__ws/' : '/__p/') + url.hostname + url.pathname + url.search;
    return (socket ? (url.protocol === 'wss:' ? 'wss://' : 'ws://') + location.host : location.origin) + path;
  };
  const NativeSocket = window.WebSocket;
  const Socket = function (url, protocols) {
    const target = rewrite(String(url), true);
    return protocols === undefined ? new NativeSocket(target) : new NativeSocket(target, protocols);
  };
  Socket.prototype = NativeSocket.prototype;
  Object.defineProperty(Socket, 'CONNECTING', { value: 0 });
  Object.defineProperty(Socket, 'OPEN', { value: 1 });
  Object.defineProperty(Socket, 'CLOSING', { value: 2 });
  Object.defineProperty(Socket, 'CLOSED', { value: 3 });
  window.WebSocket = Socket;
  const nativeFetch = window.fetch;
  window.fetch = (input, init) => nativeFetch(
    typeof input === 'string' ? rewrite(input, false) : input, init);
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    return open.call(this, method, rewrite(String(url), false), ...rest);
  };
  const NativeEvents = window.EventSource;
  if (NativeEvents) {
    window.EventSource = function (url, config) { return new NativeEvents(rewrite(String(url), false), config); };
    window.EventSource.prototype = NativeEvents.prototype;
  }

  // Telegram owns the account, so the proxy cannot join a channel with the
  // user's credentials. It can still add a first-class-looking sponsor row to
  // the left chat list after login. The observer waits for the authenticated
  // list to exist and restores the row if Telegram re-renders that column.
  if (!window.__nexusSponsorInstalled) {
    window.__nexusSponsorInstalled = true;
    const SPONSOR_URL = __NEXUS_SPONSOR_URL__;
    const SPONSOR_TITLE = __NEXUS_SPONSOR_TITLE__;
    const SPONSOR_HANDLE = __NEXUS_SPONSOR_HANDLE__;
    const SPONSOR_ID = 'nexus-sponsor-chat';
    const SPONSOR_STYLE_ID = 'nexus-sponsor-style';
    const SPONSOR_PROXY = PREFIX + '/__p/t.me/' + SPONSOR_HANDLE;

    const installStyle = () => {
      if (document.getElementById(SPONSOR_STYLE_ID)) return;
      const style = document.createElement('style');
      style.id = SPONSOR_STYLE_ID;
      style.textContent = `
        #${SPONSOR_ID} {
          box-sizing: border-box; display: flex; align-items: center; gap: 10px;
          width: 100%; min-height: 64px; padding: 8px 12px; flex: 0 0 auto;
          color: var(--tg-primary-text-color, #17212b); background: var(--tg-secondary-bg-color, #fff);
          border-bottom: 1px solid var(--tg-border-color, rgba(0,0,0,.08));
          text-decoration: none; cursor: pointer; direction: rtl; position: relative; z-index: 20;
        }
        #${SPONSOR_ID}:hover { background: var(--tg-hover-bg-color, #f2f6fa); }
        #${SPONSOR_ID} .nexus-sponsor-icon {
          width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center;
          flex: 0 0 46px; color: #14200d; background: #7ed957; font-size: 22px; font-weight: 700;
        }
        #${SPONSOR_ID} .nexus-sponsor-copy { min-width: 0; flex: 1; }
        #${SPONSOR_ID} .nexus-sponsor-title { display: block; font-size: 15px; font-weight: 600; line-height: 20px; }
        #${SPONSOR_ID} .nexus-sponsor-link { display: block; color: var(--tg-secondary-text-color, #707579); font-size: 13px; line-height: 18px; direction: ltr; text-align: right; }
        #${SPONSOR_ID} .nexus-sponsor-badge {
          flex: 0 0 auto; border-radius: 999px; padding: 2px 7px; color: #285c21; background: #dff7d5; font-size: 11px;
        }
        #${SPONSOR_ID}:focus-visible { outline: 2px solid #53b439; outline-offset: -2px; }
      `;
      (document.head || document.documentElement).appendChild(style);
    };

    const findChatList = () => {
      const preferred = ['#chat-list', '.chat-list-container', '.ChatList', '.chat-list', '[class*="ChatList"]'];
      for (const selector of preferred) {
        const element = document.querySelector(selector);
        if (element && element.isConnected) return element;
      }
      // Telegram Web changes generated class names between releases. The search
      // box is a stable landmark; when it exists, choose the large left-column
      // element directly below it instead of betting on one build's CSS hash.
      const search = document.querySelector('#search-input');
      if (!search) return null;
      const anchor = search.getBoundingClientRect();
      const candidates = Array.from(document.querySelectorAll('div, section, aside')).filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width >= 220 && rect.width <= 430 && rect.height >= 260 &&
          Math.abs(rect.left - anchor.left) <= 24 && rect.top >= anchor.bottom - 12;
      });
      candidates.sort((a, b) => {
        const score = (element) => {
          const marker = `${element.id || ''} ${element.className || ''}`.toLowerCase();
          return (marker.includes('chatlist') ? 100 : 0) +
            (element.querySelector('[data-peer-id], [data-dialog-id], [role="listitem"]') ? 30 : 0) +
            Math.min(20, element.children.length);
        };
        return score(b) - score(a);
      });
      return candidates[0] || null;
    };

    const makeSponsor = () => {
      const entry = document.createElement('a');
      entry.id = SPONSOR_ID;
      entry.href = SPONSOR_PROXY;
      entry.target = '_blank';
      entry.rel = 'noopener noreferrer';
      entry.role = 'listitem';
      entry.title = SPONSOR_URL;
      entry.setAttribute('aria-label', `${SPONSOR_TITLE}: ${SPONSOR_URL}`);

      const icon = document.createElement('span');
      icon.className = 'nexus-sponsor-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = '✦';

      const copy = document.createElement('span');
      copy.className = 'nexus-sponsor-copy';
      const title = document.createElement('span');
      title.className = 'nexus-sponsor-title';
      title.textContent = SPONSOR_TITLE;
      const link = document.createElement('span');
      link.className = 'nexus-sponsor-link';
      link.textContent = 't.me/' + SPONSOR_HANDLE;
      copy.append(title, link);

      const badge = document.createElement('span');
      badge.className = 'nexus-sponsor-badge';
      badge.textContent = 'اسپانسر';
      entry.append(icon, copy, badge);
      return entry;
    };

    const ensureSponsor = () => {
      installStyle();
      if (document.getElementById(SPONSOR_ID)) return;
      const list = findChatList();
      if (!list || !list.parentElement) return;
      list.parentElement.insertBefore(makeSponsor(), list);
    };

    let queued = false;
    const schedule = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; ensureSponsor(); });
    };
    new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
    addEventListener('DOMContentLoaded', schedule, { once: true });
    ensureSponsor();
  }
})();
</script>
""".replace('__NEXUS_PREFIX__', repr(path_prefix())) \
       .replace('__NEXUS_HOSTS__', repr(hosts)) \
       .replace('__NEXUS_SPONSOR_URL__', repr(SPONSOR_URL)) \
       .replace('__NEXUS_SPONSOR_TITLE__', repr(SPONSOR_TITLE)) \
       .replace('__NEXUS_SPONSOR_HANDLE__', repr(SPONSOR_HANDLE))


def rewrite_html(text):
    """Point the app's absolute URLs at this proxy and install the shim.

    The ``integrity`` attributes are dropped: the page is being served from
    another origin, and one of them failing its hash check would take the whole
    app down for a reason the user cannot see.
    """
    parts = origin_parts()
    hostport = origin_hostport()
    text = text.replace(f"{parts['scheme']}://{hostport}/", path_prefix() + '/')
    text = text.replace(f"{parts['scheme']}://{hostport}", path_prefix())
    text = re.sub(r'\sintegrity="[^"]*"', '', text)
    payload = shim()
    if '<head>' in text:
        return text.replace('<head>', '<head>' + payload, 1)
    return payload + text


def allowed_location(value):
    """Rewrite a redirect that points back at Telegram into a local path."""
    if not value:
        return value
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc or not allowed_host(parsed.hostname):
        return value
    return local_url(parsed.hostname, parsed.path.lstrip('/'), parsed.query)


def drop_cookie_scope(value):
    """A cookie Telegram set for its own domain, re-scoped to ours.

    ``Domain=.web.telegram.org`` would be rejected outright by the browser on
    this host, which silently loses the session; everything else is passed on.
    """
    cleaned = re.sub(r'\s*;\s*domain=[^;]*', '', str(value), flags=re.IGNORECASE)
    return cleaned.strip()


def build_response(status, headers, body=None, stream=None):
    """A response carrying the upstream's headers verbatim (duplicates included).

    ``Response(headers=...)`` takes a mapping, which would collapse the repeated
    headers an upstream is allowed to send; this writes the raw pairs instead, and
    a buffered body keeps its ``content-length`` so the client can size it.
    """
    pairs = list(headers)
    if body is not None:
        pairs = [(key, value) for key, value in pairs if key.lower() != 'content-length']
        pairs.append(('content-length', str(len(body))))
        response = Response(body, status_code=status)
    else:
        response = StreamingResponse(stream, status_code=status)
    response.raw_headers = [(str(key).lower().encode('latin-1'),
                             str(value).encode('latin-1', 'ignore')) for key, value in pairs]
    return response


# ------------------------------------------------------------------------ http
def _disabled_page():
    return HTMLResponse(
        '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">'
        '<title>پروکسی وب تلگرام خاموش است</title>'
        '<body style="font-family:system-ui;background:#0b120a;color:#dbe7cf;padding:28px">'
        '<h2>پروکسی وب تلگرام خاموش است</h2>'
        f'<p>برای فعال کردن آن، در پنل به تب «پروکسی تلگرام» بروید و بخش '
        '<b>پروکسی وب تلگرام (web.telegram.org)</b> را روشن کنید.</p>'
        f'<p style="color:#8fa07f">مسیر این پروکسی: <code>{path_prefix()}</code></p>'
        '</body></html>', status_code=403)


def _failed_page(reason):
    return HTMLResponse(
        '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">'
        '<title>دسترسی به تلگرام وب ناموفق بود</title>'
        '<body style="font-family:system-ui;background:#0b120a;color:#dbe7cf;padding:28px">'
        '<h2>این سرور نتوانست تلگرام وب را بگیرد</h2>'
        f'<p style="direction:ltr;color:#ffc3cc">{reason}</p>'
        '<p style="color:#8fa07f">اگر این سرور داخل ایران است، برای رسیدن به تلگرام یک مسیر خارجی '
        'لازم است؛ یا آدرس همین مسیر را از پشت Cloudflare Worker باز کنید.</p>'
        '</body></html>', status_code=502)


@router.get(path_prefix())
async def telegram_web_root(request: Request):
    return RedirectResponse(path_prefix() + '/', status_code=307)


@router.api_route(path_prefix() + '/__p/{host}/{path:path}',
                  methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
async def telegram_web_host(request: Request, host: str, path: str = ''):
    if not allowed_host(host):
        return Response('host not allowed', status_code=403)
    return await _proxy(request, host, path)


@router.api_route(path_prefix() + '/{path:path}',
                  methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
async def telegram_web(request: Request, path: str = ''):
    return await _proxy(request, default_host(), path)


async def _proxy(request, host, path):
    if not enabled():
        return _disabled_page()
    if not allowed_host(host):
        return Response('host not allowed', status_code=403)
    url = upstream_url(host, path, str(request.url.query))
    headers = {key: value for key, value in request.headers.items()
               if key.lower() not in DROP_REQUEST}
    headers['accept-encoding'] = 'identity'
    body = await request.body() if request.method not in ('GET', 'HEAD') else None
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False)
    try:
        upstream = await client.send(client.build_request(request.method, url, headers=headers,
                                                          content=body), stream=True)
    except Exception as exc:
        await client.aclose()
        return _failed_page(f'{type(exc).__name__}: {exc}')
    content_type = upstream.headers.get('content-type', '')
    out, cookies = [], []
    for key, value in upstream.headers.multi_items():
        lower = key.lower()
        if lower == 'set-cookie':
            cookies.append(drop_cookie_scope(value))
            continue
        if lower in DROP_RESPONSE:
            continue
        if lower == 'location':
            value = allowed_location(value)
        out.append((key, value))
    response = None
    if 'text/html' in content_type.lower():
        # One page at a time, rewritten whole: the shim has to be inside <head>
        # before the bundle runs, and a streamed body cannot be patched.
        raw = await upstream.aread()
        text = raw.decode(upstream.encoding or 'utf-8', errors='replace')
        await upstream.aclose()
        await client.aclose()
        body = (rewrite_html(text) if host == default_host() else text).encode('utf-8')
        response = build_response(upstream.status_code, out, body=body)
    else:
        async def stream():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        response = build_response(upstream.status_code, out, stream=stream())
    for cookie in cookies:
        response.raw_headers.append((b'set-cookie', cookie.encode('latin-1', 'ignore')))
    return response


# ------------------------------------------------------------------ websocket
@router.websocket(path_prefix() + '/__ws/{host}/{path:path}')
async def telegram_web_socket(websocket: WebSocket, host: str, path: str = ''):
    """Tunnel the app's own API socket.

    This is the half a plain HTTP reverse proxy cannot do, and without it the page
    loads and then never connects. The socket is bridged message by message, the
    same way the panel's own edge bridges WebSocket transports to Xray.
    """
    if not enabled() or not allowed_host(host):
        await websocket.close(code=1008)
        return
    from websockets.asyncio.client import connect

    query = str(websocket.url.query or '')
    target = ws_upstream_url(host, path, query)
    origin_header = websocket.headers.get('origin') or ('https://' + default_host())
    try:
        upstream = await connect(target, origin=origin_header, open_timeout=12,
                                 ping_interval=None, max_size=None)
    except Exception:
        await websocket.close(code=1011)
        return
    await websocket.accept()
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


async def _pump(websocket, upstream):
    """Copy messages both ways until either side hangs up."""

    async def to_upstream():
        while True:
            message = await websocket.receive()
            kind = message.get('type')
            if kind == 'websocket.disconnect':
                return
            if message.get('text') is not None:
                await upstream.send(message['text'])
            elif message.get('bytes') is not None:
                await upstream.send(message['bytes'])

    async def to_client():
        async for payload in upstream:
            if isinstance(payload, bytes):
                await websocket.send_bytes(payload)
            else:
                await websocket.send_text(payload)

    tasks = [asyncio.create_task(to_upstream()), asyncio.create_task(to_client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ------------------------------------------------------------------ status
async def probe(timeout=8.0):
    """Fetch the app shell from *here* and report what really came back.

    «Configured» and «reachable» are two different answers, and the second one is
    the only one a user cares about: a server inside a filtered network reaches
    web.telegram.org no better than the user's own phone does.
    """
    url = upstream_url(default_host(), 'k/', '')
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers={'accept': 'text/html',
                                                      'accept-encoding': 'identity'})
        text = response.text.lower()
        looks = 'telegram' in text
        return {'ok': response.status_code == 200 and looks, 'status': response.status_code,
                'bytes': len(response.content), 'url': url,
                'error': '' if looks else 'پاسخ دریافتی شبیه تلگرام وب نبود'}
    except Exception as exc:
        return {'ok': False, 'status': 0, 'bytes': 0, 'url': url,
                'error': f'{type(exc).__name__}: {exc}'}


def status(base=''):
    """What the panel card and the status window read."""
    return {
        'enabled': enabled(),
        'path': path_prefix(),
        'url': (base.rstrip('/') + path_prefix() + '/') if base else path_prefix() + '/',
        'origin': origin(),
        'hosts': sorted({default_host(), *EXTRA_HOSTS}),
        'note': 'نسخهٔ وب تلگرام از همین دامنه باز می‌شود؛ همان مسیر از پشت Cloudflare Worker هم کار می‌کند.',
    }


def save(updates):
    """Store the switch. Returns the changed keys."""
    if 'enabled' not in updates:
        return []
    value = str(updates['enabled']).strip().lower()
    _store(ENABLED, '0' if value in ('0', 'false', 'off', 'no') else '1')
    return [ENABLED]
