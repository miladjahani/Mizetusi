/**
 * NEXUS · Cloudflare Worker — WebSocket edge in front of a Railway origin.
 *
 * Why this Worker exists
 * ----------------------
 * Iranian clients are usually blocked at the *IP* level. Serving the same
 * Railway Xray origin through Cloudflare gives every user a clean Cloudflare IP
 * (the panel probes them and only ships the healthy ones, fastest first), while
 * all traffic still ends in the Railway container.
 *
 * Deploy in 3 steps
 * -----------------
 *   1. Workers & Pages → Create Worker → paste this file → Deploy.
 *   2. Settings → Variables: NEXUS_ORIGIN = https://<your-app>.up.railway.app
 *      (the copy served by the NEXUS panel already has this value inline, so the
 *      paste-as-is version works too), optionally ALLOWED_HOSTS.
 *   3. Copy the Worker URL into the panel's Cloudflare section and run
 *      "پینگ همه نودها" — the healthy IPs become your Cloudflare nodes.
 *
 * Endpoints
 * ---------
 *   GET /health          → JSON status (the panel's "تست ورکر" button calls it)
 *   GET /health?probe=1  → same, plus a live round-trip to the origin's /health
 *   GET /ws/…  /cdn/…    → WebSocket reverse proxy to the matching origin path
 *
 * Every path below is mirrored by the FastAPI edge and by
 * `app/subscriptions/transports.py`; `tests/worker_smoke.mjs` drives each one
 * through this file, and the panel's test suite fails if the two lists drift.
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
  // Shadowsocks-2022, one listener per cipher family.
  '/ws/ss', '/cdn/ss',
  '/ws/ss-aes256', '/cdn/ss-aes256',
  '/ws/ss-chacha', '/cdn/ss-chacha',
  // The WARP exit node, once it is enabled in the panel.
  '/ws/warp',
  // Legacy VLESS path kept for clients subscribed before the split.
  '/ws',
];

const HEALTH_PATHS = ['/health', '/diag'];
const WORKER_VERSION = 'nexus-ws-2';

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

// Probing the origin proves the Worker, the origin URL and the WebSocket route
// in one call: this is what the panel's "تست ورکر" button shows.
async function probeOrigin(origin) {
  const started = Date.now();
  try {
    const response = await fetch(origin + '/health', {
      headers: { 'user-agent': 'NEXUS-Worker-Probe', accept: 'application/json' },
      redirect: 'manual',
    });
    let body = null;
    try { body = await response.json(); } catch (error) { body = null; }
    return { reachable: true, status: response.status, latency_ms: Date.now() - started, health: body };
  } catch (error) {
    return { reachable: false, status: 0, latency_ms: Date.now() - started, error: String(error && error.message || error) };
  }
}

async function handleHealth(request, env, origin) {
  const url = new URL(request.url);
  const wantsProbe = ['1', 'true', 'yes'].includes((url.searchParams.get('probe') || '').toLowerCase());
  const body = {
    ok: Boolean(origin),
    worker: WORKER_VERSION,
    origin: origin || null,
    paths: EDGE_PATHS,
    colo: (request.cf && request.cf.colo) || null,
    country: (request.cf && request.cf.country) || null,
    time: new Date().toISOString(),
  };
  if (!origin) return json({ ...body, error: 'NEXUS_ORIGIN is not configured' }, 503);
  if (wantsProbe) {
    body.origin_probe = await probeOrigin(origin);
    if (!body.origin_probe.reachable) body.ok = false;
  }
  return json(body, body.ok ? 200 : 503);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = normalizeOrigin((env && env.NEXUS_ORIGIN) || (env && env.ZEUS_ORIGIN) || ORIGIN_FALLBACK);

    if (HEALTH_PATHS.includes(url.pathname)) return handleHealth(request, env, origin);

    if (!EDGE_PATHS.includes(url.pathname)) {
      return text(
        'NEXUS WebSocket edge.\nConnect with VLESS/VMess/Trojan/Shadowsocks over one of:\n'
          + EDGE_PATHS.map((path) => '  ' + path).join('\n') + '\n',
        404,
      );
    }

    if (!origin) return json({ ok: false, error: 'NEXUS_ORIGIN is not configured' }, 503);

    if (request.method !== 'GET') {
      return json({ ok: false, error: 'method not allowed', allow: 'GET' }, 405, { allow: 'GET' });
    }

    if ((request.headers.get('Upgrade') || '').toLowerCase() !== 'websocket') {
      return json({ ok: false, error: 'WebSocket upgrade required' }, 426);
    }

    const allowed = allowList(env);
    const host = (request.headers.get('Host') || url.hostname).toLowerCase();
    if (allowed.length && !allowed.includes(host)) {
      return json({ ok: false, error: 'host not allowed' }, 403);
    }

    const clientIp = edgeOriginOf(request);
    const headers = new Headers(request.headers);
    for (const name of STRIP_HEADERS) headers.delete(name);
    // The origin builds absolute subscription/status URLs from the forwarded
    // host, Xray always terminates TLS here, and the backend needs the real
    // client IP to enforce IP limits and quota attribution.
    headers.set('X-Forwarded-Proto', 'https');
    headers.set('X-Forwarded-Host', host);
    if (clientIp) headers.set('X-Forwarded-For', clientIp);
    headers.set('X-Nexus-Edge', WORKER_VERSION);
    if ((headers.get('Connection') || '').toLowerCase().indexOf('upgrade') === -1) {
      headers.set('Connection', 'Upgrade');
    }

    let upstream;
    try {
      upstream = await fetch(new Request(origin + url.pathname + url.search, {
        method: 'GET',
        headers,
        redirect: 'manual',
      }));
    } catch (error) {
      // Without this the rejection escapes the handler and Cloudflare paints an
      // opaque error page, which is impossible to diagnose from a client.
      return json({
        ok: false,
        error: 'origin unreachable',
        origin,
        detail: String(error && error.message || error),
      }, 502);
    }

    // 101 Switching Protocols must be returned untouched: rebuilding the
    // Response would drop `webSocket` and the client would hang.
    if (upstream.webSocket) return upstream;

    const response = new Response(upstream.body, upstream);
    response.headers.set('cache-control', NO_CACHE['cache-control']);
    return response;
  },
};
