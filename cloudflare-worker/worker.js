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
 *   GET /health   → JSON status (the panel's "تست ورکر" button calls this)
 *   GET /ws/vless → VLESS  over WebSocket  (path must match the subscription)
 *   GET /ws/trojan→ Trojan over WebSocket
 *   GET /ws       → legacy alias of /ws/vless
 */

// The panel serves this file with ORIGIN_FALLBACK prefilled with your Railway
// URL, so a copy/paste deployment works without touching the dashboard.
const ORIGIN_FALLBACK = '';

const WS_PATHS = ['/ws', '/ws/vless', '/ws/trojan'];

// Edge/Cloudflare internals must not leak into the origin request: they would
// confuse Host/SNI handling and let a client spoof its own country or scheme.
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

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', ...NO_CACHE },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = normalizeOrigin((env && env.NEXUS_ORIGIN) || (env && env.ZEUS_ORIGIN) || ORIGIN_FALLBACK);

    // Health/diagnostics: the panel uses this to prove the Worker is live, and
    // it doubles as a self-check that ORIGIN is configured.
    if (url.pathname === '/health' || url.pathname === '/diag') {
      return json({
        ok: Boolean(origin),
        worker: 'nexus-ws',
        origin: origin || null,
        paths: WS_PATHS,
        colo: (request.cf && request.cf.colo) || null,
        country: (request.cf && request.cf.country) || null,
        time: new Date().toISOString(),
      }, origin ? 200 : 503);
    }

    if (!WS_PATHS.includes(url.pathname)) {
      return new Response(
        'NEXUS WebSocket edge.\nConnect with VLESS/Trojan over /ws/vless or /ws/trojan.\n',
        { status: 404, headers: { 'content-type': 'text/plain; charset=utf-8', ...NO_CACHE } },
      );
    }

    if (!origin) return json({ ok: false, error: 'NEXUS_ORIGIN is not configured' }, 503);

    if ((request.headers.get('Upgrade') || '').toLowerCase() !== 'websocket') {
      return json({ ok: false, error: 'WebSocket upgrade required' }, 426);
    }

    // Optional allow-list so the edge is never an open relay.
    const allowed = String((env && env.ALLOWED_HOSTS) || '')
      .split(',')
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean);
    const host = (request.headers.get('Host') || url.hostname).toLowerCase();
    if (allowed.length && !allowed.includes(host)) {
      return json({ ok: false, error: 'host not allowed' }, 403);
    }

    const headers = new Headers(request.headers);
    for (const name of STRIP_HEADERS) headers.delete(name);
    // The origin builds absolute subscription/status URLs from the forwarded
    // host, and Xray always terminates TLS here, so pin the scheme to https.
    headers.set('X-Forwarded-Proto', 'https');
    headers.set('X-Forwarded-Host', host);
    headers.set('Host', new URL(origin).host);

    const upstream = await fetch(new Request(origin + url.pathname + url.search, {
      method: 'GET',
      headers,
      redirect: 'manual',
    }));

    // 101 Switching Protocols must be returned untouched: rebuilding the
    // Response would drop `webSocket` and the client would hang.
    if (upstream.webSocket) return upstream;

    const response = new Response(upstream.body, upstream);
    response.headers.set('cache-control', NO_CACHE['cache-control']);
    return response;
  },
};
