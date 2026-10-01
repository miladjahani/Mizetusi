/**
 * NEXUS · Cloudflare Worker — the whole panel and its WebSocket edge.
 *
 * Why this Worker exists
 * ----------------------
 * Iranian clients are usually blocked at the *IP* level: the Railway address
 * (and its certificate name) stops resolving or answering, so the panel and its
 * node links are unreachable without a VPN. Serving everything through
 * Cloudflare gives every client a clean Cloudflare IP instead, while all traffic
 * still ends in the Railway container.
 *
 * This Worker is therefore a full reverse proxy, not only a WebSocket relay:
 *
 *   https://<worker>/                 → the panel itself (login, dashboard, API,
 *                                       subscription and portal links, static
 *                                       assets, redirects, uploads)
 *   wss://<worker>/ws/vless  /cdn/…   → the Xray/Shadowsocks WebSocket edge
 *   wss://<worker>/api/v1/socket      → the panel's own WebSockets
 *   https://<worker>/health           → this Worker's status (the panel's
 *                                       "تست ورکر" button calls it)
 *
 * For that reason the panel's subscription and status-window URLs must be built
 * from *this* host, which is why every forwarded request carries
 * `X-Forwarded-Host: <worker host>`: the panel derives its absolute links from
 * it, so a link a user copies points at the clean Cloudflare address rather than
 * the blocked Railway one.
 *
 * Deploy in 4 steps
 * -----------------
 *   1. Workers & Pages → Create Worker → paste this file → Deploy.
 *   2. Settings → Domains & Routes → Add custom domain: attach a subdomain of a
 *      domain the Cloudflare account owns (e.g. panel.example.com). This step is
 *      not cosmetic — the default `*.workers.dev` hostname is filtered in Iran
 *      too, so a Worker left on it is just another address that only opens
 *      through a VPN. Cloudflare issues the certificate itself.
 *   3. Settings → Variables: NEXUS_ORIGIN = https://<your-app>.up.railway.app
 *      (the copy served by the NEXUS panel already has this value inline, so the
 *      paste-as-is version works too), optionally ALLOWED_HOSTS.
 *   4. Copy the **custom-domain** URL into the panel's Cloudflare section and run
 *      "پینگ همه نودها" — the healthy IPs become your Cloudflare nodes, and the
 *      panel's own links are rebuilt on that reachable host.
 *
 * Every WebSocket path below is mirrored by the FastAPI edge and by
 * `app/subscriptions/transports.py`; `tests/worker_smoke.mjs` drives each one
 * through this file, and the panel's test suite fails if the two lists drift.
 *
 * Two shapes, one file
 * --------------------
 * "Python behind this Worker" is a URL in one deployment and a binding in the
 * other, so the upstream is resolved in exactly one place:
 *
 *   NEXUS_ORIGIN set          → an HTTPS origin somewhere else (Railway, Render,
 *                               a VPS). This is the paste-into-the-dashboard
 *                               deployment, and it stays dependency-free.
 *   NEXUS_CONTAINER bound     → the app itself runs in a Cloudflare Container on
 *                               this account, and the Worker is its only address.
 *                               See `cloudflare-containers/` and
 *                               `docs/CLOUDFLARE-DEPLOY-FA.md`.
 *
 * A bound container wins, because that deployment has no other address at all —
 * which also means a stale NEXUS_ORIGIN left in the environment cannot make the
 * Worker call itself.
 */

// The panel serves this file with ORIGIN_FALLBACK prefilled with your Railway
// URL, so a copy/paste deployment works without touching the dashboard.
const ORIGIN_FALLBACK = '';

// Every WebSocket path the origin publishes. A path that is missing here is a
// node that 404s behind Cloudflare while working on the Railway origin, which
// reads as "the Worker is broken" in a client — so the list is exhaustive.
const EDGE_PATHS = [
  // VLESS / VMess / Trojan, in both edge path shapes.
  '/ws/vless', '/cdn/vless',
  '/ws/vmess', '/cdn/vmess',
  '/ws/trojan', '/cdn/trojan',
  // Shadowsocks, one listener per cipher family. The first pair is the classic
  // aes-256-gcm profile every client implements; the rest are the 2022 ciphers
  // plus the widely compatible chacha20-ietf-poly1305 one.
  '/ws/ss-classic', '/cdn/ss-classic',
  '/ws/ss', '/cdn/ss',
  '/ws/ss-aes256', '/cdn/ss-aes256',
  '/ws/ss-chacha', '/cdn/ss-chacha',
  '/ws/ss-legacy', '/cdn/ss-legacy',
  // The WARP exit node, once it is enabled in the panel.
  '/ws/warp',
  // Legacy VLESS path kept for clients subscribed before the split.
  '/ws',
];

// Anything under these prefixes that is *not* a published edge path is a client
// mistake (a typo'd node path), never a panel page — so it stays a 404 with the
// path list instead of being proxied to the app.
const EDGE_PREFIXES = ['/ws/', '/cdn/'];

const HEALTH_PATHS = ['/health', '/diag'];
const WORKER_VERSION = 'nexus-edge-6';

// The Durable Object / Container instance this Worker fronts when the app runs on
// Cloudflare itself. One name, so every request reaches the same panel (and the
// same database) instead of a fresh container per path.
const CONTAINER_NAME = 'nexus-panel';

// Edge/Cloudflare internals must not leak into the origin request: they would
// confuse Host/SNI handling and let a client spoof its own country or scheme.
// ``connection`` is deliberately NOT listed: uvicorn and the websockets library
// both require ``Connection: upgrade`` *and* ``Upgrade: websocket`` to accept a
// session, so stripping it would make every handshake fail at the origin.
const STRIP_HEADERS = [
  'cf-connecting-ip', 'cf-ipcountry', 'cf-ray', 'cf-visitor', 'cf-worker',
  'cf-ew-via', 'cdn-loop', 'x-real-ip', 'x-forwarded-proto', 'x-forwarded-host',
  'x-forwarded-for', 'content-length',
];

const NO_CACHE = { 'cache-control': 'no-store, max-age=0' };

function normalizeOrigin(value) {
  const raw = String(value || '').trim().replace(/\/+$/, '');
  if (!raw) return '';
  try {
    return new URL(/^https?:\/\//i.test(raw) ? raw : 'https://' + raw).origin;
  } catch (error) {
    return '';
  }
}

// The bound Container, or null when this Worker points at an HTTPS origin. A
// missing binding is the normal edge deployment, not an error.
function containerStub(env) {
  const binding = env && env.NEXUS_CONTAINER;
  if (!binding) return null;
  if (typeof binding.getByName === 'function') return binding.getByName(CONTAINER_NAME);
  // The same thing spelled with the older Durable Object API, so a binding from
  // any wrangler version resolves to the one instance instead of no origin at all.
  if (typeof binding.idFromName === 'function' && typeof binding.get === 'function') {
    return binding.get(binding.idFromName(CONTAINER_NAME));
  }
  return null;
}

// One function that performs an upstream request, so the WebSocket edge, the
// panel hop and the health probe share a single definition of "where the app
// is". ``null`` means nothing is configured and every route answers 503.
function upstreamFor(env, origin) {
  const container = containerStub(env);
  if (container) return (request) => container.fetch(request);
  if (!origin) return null;
  return (request) => fetch(request);
}

// Where a relative path is resolved from. A container has no public name of its
// own, so the Worker's own host is the only base that exists there.
function upstreamBase(origin, host) {
  return origin || 'https://' + host;
}

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', ...NO_CACHE, ...extra },
  });
}

function text(body, status = 200) {
  return new Response(body, {
    status,
    headers: { 'content-type': 'text/plain; charset=utf-8', ...NO_CACHE },
  });
}

function allowList(env) {
  return String((env && env.ALLOWED_HOSTS) || '')
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

function edgeOriginOf(request) {
  return (request.headers.get('cf-connecting-ip') || request.headers.get('x-real-ip') || '').trim();
}

function isUpgrade(request) {
  return (request.headers.get('Upgrade') || '').toLowerCase() === 'websocket';
}

function hostOf(request) {
  return (request.headers.get('Host') || new URL(request.url).hostname).toLowerCase();
}

function hostNotAllowed(request, env) {
  const allowed = allowList(env);
  return allowed.length > 0 && !allowed.includes(hostOf(request));
}

// Probing the upstream proves the Worker, the upstream and the WebSocket route
// in one call: this is what the panel's "تست ورکر" button shows. The probe goes
// through the same function the real routes use, so a container deployment is
// probed through its binding rather than through a URL it does not have.
async function probeUpstream(upstream, base) {
  const started = Date.now();
  try {
    const response = await upstream(new Request(base + '/health', {
      headers: { 'user-agent': 'NEXUS-Worker-Probe', accept: 'application/json' },
      redirect: 'manual',
    }));
    let body = null;
    try { body = await response.json(); } catch (error) { body = null; }
    return { reachable: true, status: response.status, latency_ms: Date.now() - started, health: body };
  } catch (error) {
    return { reachable: false, status: 0, latency_ms: Date.now() - started, error: String(error && error.message || error) };
  }
}

async function handleHealth(request, env, origin, upstream) {
  const url = new URL(request.url);
  const wantsProbe = ['1', 'true', 'yes'].includes((url.searchParams.get('probe') || '').toLowerCase());
  const container = Boolean(containerStub(env));
  const body = {
    ok: Boolean(upstream),
    worker: WORKER_VERSION,
    mode: container ? 'container' : 'origin',
    container,
    origin: origin || null,
    paths: EDGE_PATHS,
    panel: Boolean(upstream),
    colo: (request.cf && request.cf.colo) || null,
    country: (request.cf && request.cf.country) || null,
    time: new Date().toISOString(),
  };
  if (!upstream) return json({ ...body, error: 'NEXUS_ORIGIN is not configured and no NEXUS_CONTAINER is bound' }, 503);
  if (wantsProbe) {
    body.origin_probe = await probeUpstream(upstream, upstreamBase(origin, hostOf(request)));
    if (!body.origin_probe.reachable) body.ok = false;
  }
  return json(body, body.ok ? 200 : 503);
}

function unknownEdgePath() {
  return text(
    'NEXUS edge. Published WebSocket paths:\n'
      + EDGE_PATHS.map((path) => '  ' + path).join('\n')
      + '\n\nEvery other path is the panel itself.\n',
    404,
  );
}

// A path shaped like a relay (an absolute URL pasted after the Worker address)
// must never be forwarded: the origin below is fixed, so the Worker can only
// ever reach the app it was deployed for.
function isRelayShaped(pathname) {
  return pathname.startsWith('//') || pathname.indexOf('://') !== -1;
}

// An origin redirect must reach the browser instead of being followed here:
// following it would render /login at the root URL and leave the address bar on
// the wrong page. An absolute redirect that names the origin is rewritten to
// this host, so a client never gets bounced back to the blocked address.
function rewriteLocation(value, origin, host) {
  try {
    const target = new URL(value, origin + '/');
    if (target.origin !== origin) return value;
    return 'https://' + host + target.pathname + target.search + target.hash;
  } catch (error) {
    return value;
  }
}

function originUnreachable(target, error) {
  // Without this the rejection escapes the handler and Cloudflare paints an
  // opaque error page, which is impossible to diagnose from a client.
  return json({
    ok: false,
    error: 'origin unreachable',
    origin: target || null,
    detail: String(error && error.message || error),
  }, 502);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = normalizeOrigin((env && env.NEXUS_ORIGIN) || (env && env.ZEUS_ORIGIN) || ORIGIN_FALLBACK);
    const edgeHost = hostOf(request);
    // Where the app really is, resolved once: a Container bound to this Worker,
    // or an HTTPS origin somewhere else. Every route below uses this one value.
    const sendUpstream = upstreamFor(env, origin);
    const target = origin || (containerStub(env) ? 'container:' + CONTAINER_NAME : '');

    if (HEALTH_PATHS.includes(url.pathname)) return handleHealth(request, env, origin, sendUpstream);

    // ---------------------------------------------------------- WebSocket edge
    if (EDGE_PATHS.includes(url.pathname)) {
      if (!sendUpstream) return json({ ok: false, error: 'NEXUS_ORIGIN is not configured' }, 503);
      if (request.method !== 'GET') {
        return json({ ok: false, error: 'method not allowed', allow: 'GET' }, 405, { allow: 'GET' });
      }
      if (!isUpgrade(request)) {
        return json({ ok: false, error: 'WebSocket upgrade required' }, 426);
      }
      if (hostNotAllowed(request, env)) {
        return json({ ok: false, error: 'host not allowed' }, 403);
      }

      const clientIp = edgeOriginOf(request);
      const headers = new Headers(request.headers);
      for (const name of STRIP_HEADERS) headers.delete(name);
      // The origin builds absolute subscription/status URLs from the forwarded
      // host, Xray always terminates TLS here, and the backend needs the real
      // client IP to enforce IP limits and quota attribution.
      headers.set('X-Forwarded-Proto', 'https');
      headers.set('X-Forwarded-Host', edgeHost);
      if (clientIp) headers.set('X-Forwarded-For', clientIp);
      headers.set('X-Nexus-Edge', WORKER_VERSION);
      if ((headers.get('Connection') || '').toLowerCase().indexOf('upgrade') === -1) {
        headers.set('Connection', 'Upgrade');
      }

      let upstream;
      try {
        upstream = await sendUpstream(new Request(upstreamBase(origin, edgeHost) + url.pathname + url.search, {
          method: 'GET',
          headers,
          redirect: 'manual',
        }));
      } catch (error) {
        return originUnreachable(target, error);
      }

      // 101 Switching Protocols must be returned untouched: rebuilding the
      // Response would drop `webSocket` and the client would hang.
      if (upstream.webSocket) return upstream;

      const response = new Response(upstream.body, upstream);
      response.headers.set('cache-control', NO_CACHE['cache-control']);
      return response;
    }

    if (EDGE_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) return unknownEdgePath();
    if (isRelayShaped(url.pathname)) return unknownEdgePath();

    // ------------------------------------------------------------------ panel
    // Everything else is the panel: pages, the API, subscription and portal
    // links, static assets, its own WebSockets, uploads and redirects.
    if (!sendUpstream) return json({ ok: false, error: 'NEXUS_ORIGIN is not configured' }, 503);

    // Built *from* the incoming request, so method, query, body and a WebSocket
    // upgrade all ride through untouched (an upload must not be buffered here);
    // only the edge headers are replaced.
    const upstreamBaseUrl = upstreamBase(origin, edgeHost);
    const forward = new Request(upstreamBaseUrl + url.pathname + url.search, request);
    forward.headers.delete('host');
    for (const name of STRIP_HEADERS) forward.headers.delete(name);
    forward.headers.set('X-Forwarded-Proto', 'https');
    forward.headers.set('X-Forwarded-Host', edgeHost);
    const seenFrom = edgeOriginOf(request);
    if (seenFrom) forward.headers.set('X-Forwarded-For', seenFrom);
    forward.headers.set('X-Nexus-Edge', WORKER_VERSION);
    if (isUpgrade(request) && (forward.headers.get('Connection') || '').toLowerCase().indexOf('upgrade') === -1) {
      forward.headers.set('Connection', 'Upgrade');
    }
    const manual = new Request(forward, { redirect: 'manual' });

    let upstream;
    try {
      upstream = await sendUpstream(manual);
    } catch (error) {
      return originUnreachable(target, error);
    }

    // A panel WebSocket (the WEB proxy's same-origin socket, live status).
    if (upstream.webSocket) return upstream;

    const location = upstream.headers.get('location');
    if (location) {
      const rewritten = rewriteLocation(location, upstreamBaseUrl, edgeHost);
      if (rewritten !== location) {
        const moved = new Response(upstream.body, upstream);
        moved.headers.set('location', rewritten);
        return moved;
      }
    }
    return upstream;
  },
};
