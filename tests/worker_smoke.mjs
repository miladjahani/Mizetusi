/* Cloudflare Worker smoke test — no network, no `wrangler deploy`.

   It loads the real cloudflare-worker/worker.js as an ES module with a stubbed
   fetch() and drives every published edge path through it, so a path the panel
   publishes but the Worker refuses to proxy (the classic "Worker gives an
   error" report) fails here instead of in a user's client.

   Run:  node tests/worker_smoke.mjs                                      */
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const SOURCE = readFileSync(new URL('../cloudflare-worker/worker.js', import.meta.url), 'utf8');
// The Worker's own path table, so a path added to the deployment but forgotten
// here (or vice versa) shows up as a failure rather than as an untested route.
const ADVERTISED = SOURCE.split('const EDGE_PATHS = [', 2)[1].split('];', 1)[0]
  .match(/'([^']+)'/g).map((quoted) => quoted.slice(1, -1));

// Node treats a bare .js as CommonJS, so the ESM source is imported from a copy
// with an .mjs extension — this is the same text Cloudflare would deploy.
const dir = mkdtempSync(join(tmpdir(), 'nexus-worker-'));
const file = join(dir, 'worker.mjs');
writeFileSync(file, SOURCE);
const worker = (await import(pathToFileURL(file).href)).default;

// Every path the panel publishes (ws + cdn shapes, all Shadowsocks ciphers).
const EDGE_PATHS = [
  '/ws', '/ws/vless', '/ws/vmess', '/ws/trojan',
  '/ws/ss-classic', '/ws/ss', '/ws/ss-aes256', '/ws/ss-chacha', '/ws/ss-legacy',
  '/ws/warp',
  '/cdn/vless', '/cdn/vmess', '/cdn/trojan',
  '/cdn/ss-classic', '/cdn/ss', '/cdn/ss-aes256', '/cdn/ss-chacha', '/cdn/ss-legacy',
];
const ORIGIN = 'https://nexus-production.up.railway.app';
const EDGE = 'https://nexus-edge.example.workers.dev';
const failures = [];

// A fake upstream: records every request the Worker makes and answers with a
// 101-shaped object for WebSocket upgrades (Node's Response cannot carry one).
const seen = [];
let upstreamFails = false;
globalThis.fetch = async (input) => {
  const request = input instanceof Request ? input : new Request(input);
  seen.push(request);
  if (upstreamFails) throw new TypeError('network unreachable');
  const upgrade = (request.headers.get('upgrade') || '').toLowerCase() === 'websocket';
  if (upgrade) return { status: 101, webSocket: { fake: true }, headers: new Headers() };
  return new Response('{"ok":true,"service":"nexus-python"}', { status: 200 });
};

const call = (path, { env = { NEXUS_ORIGIN: ORIGIN }, headers = {}, origin } = {}) => {
  seen.length = 0;
  const request = new Request((origin || EDGE) + path, { headers });
  return worker.fetch(request, env, {});
};

const ws = { Upgrade: 'websocket', Connection: 'Upgrade' };
const check = (ok, label) => { if (!ok) failures.push(label); };

// ---------------------------------------------------------------- /health
{
  const response = await call('/health');
  const body = await response.json();
  check(response.status === 200, 'health should be 200 when the origin is set');
  check(body.ok === true && body.origin === ORIGIN, 'health should report the origin');
  check(Array.isArray(body.paths) && body.paths.length >= EDGE_PATHS.length, 'health should list the paths');
  check(EDGE_PATHS.every((path) => body.paths.includes(path)), 'health must list every published path');
  check((response.headers.get('cache-control') || '').includes('no-store'), 'health must not be cached');
}

// ------------------------------------------------------ unconfigured origin
{
  const missing = await call('/health', { env: {} });
  check(missing.status === 503, 'health should be 503 without NEXUS_ORIGIN');
  check((await missing.json()).ok === false, 'health should say ok:false without an origin');
  const noOrigin = await call('/ws/vless', { env: {}, headers: ws });
  check(noOrigin.status === 503, 'a WS path without an origin should be 503');
}

// ------------------------------------------------- every published WS path
check(ADVERTISED.length === EDGE_PATHS.length
  && ADVERTISED.every((path) => EDGE_PATHS.includes(path))
  && EDGE_PATHS.every((path) => ADVERTISED.includes(path)),
  `path list drifted from worker.js: ${ADVERTISED.join(' ')}`);
for (const path of ADVERTISED) {
  const response = await call(path, { headers: ws });
  const proxied = seen.find((request) => new URL(request.url).pathname === path);
  check(response.status === 101, `${path} should be proxied (got ${response.status})`);
  check(Boolean(proxied), `${path} was never forwarded to the origin`);
  if (proxied) {
    check(proxied.url.startsWith(ORIGIN + path), `${path} must reach the origin at the same path`);
    check(proxied.method === 'GET', `${path} must be proxied as GET`);
  }
}

// Query strings survive the hop (clients append ?ed=2048 and similar).
{
  await call('/ws/vless?ed=2048&x=1', { headers: ws });
  check(seen[0] && new URL(seen[0].url).search === '?ed=2048&x=1', 'query string must be preserved');
}

// -------------------------------------------------- headers the origin sees
{
  await call('/ws/vless', {
    headers: { ...ws, 'cf-connecting-ip': '203.0.113.9', 'cf-ray': 'abc123', 'cf-ipcountry': 'IR', Host: 'edge.example' },
  });
  const upstream = seen[0];
  const headers = upstream.headers;
  check(!headers.get('cf-connecting-ip') && !headers.get('cf-ray') && !headers.get('cf-ipcountry'),
    'Cloudflare internals must not reach the origin');
  check(headers.get('x-forwarded-proto') === 'https', 'x-forwarded-proto must be pinned to https');
  check(headers.get('x-forwarded-host') === 'edge.example', 'x-forwarded-host must carry the client host');
  check(headers.get('x-forwarded-for') === '203.0.113.9', 'the real client IP must be forwarded');
  check(headers.get('upgrade') === 'websocket', 'the upgrade header must be forwarded');
  // uvicorn and the websockets library reject a handshake without this pair.
  check((headers.get('connection') || '').toLowerCase().includes('upgrade'),
    'Connection: Upgrade must be forwarded or the origin refuses the handshake');
}

// ---------------------------------------------------------------- refusals
{
  const plain = await call('/ws/vless');
  check(plain.status === 426, 'a non-upgrade request on a WS path should be 426');
  const unknown = await call('/ws/nope', { headers: ws });
  check(unknown.status === 404, 'an unknown path should be 404');
  const openProxy = await call('/https://example.com', { headers: ws });
  check(openProxy.status === 404, 'the Worker must never act as an open relay');
}

// ---------------------------------------------------------- host allow-list
{
  const blocked = await call('/ws/vless', { env: { NEXUS_ORIGIN: ORIGIN, ALLOWED_HOSTS: 'other.example' }, headers: { ...ws, Host: 'edge.example' } });
  check(blocked.status === 403, 'ALLOWED_HOSTS must block an unlisted Host');
  const permitted = await call('/ws/vless', { env: { NEXUS_ORIGIN: ORIGIN, ALLOWED_HOSTS: 'edge.example, other.example' }, headers: { ...ws, Host: 'edge.example' } });
  check(permitted.status === 101, 'ALLOWED_HOSTS must allow a listed Host');
}

// ------------------------------------------------------------ origin down
// An unreachable origin must become a clean JSON 502. Today the Worker lets the
// rejection escape, which is what Cloudflare paints as its own error page.
{
  upstreamFails = true;
  let down = null;
  try {
    down = await call('/ws/vless', { headers: ws });
  } catch (error) {
    failures.push(`an unreachable origin threw instead of returning 502 (${error.message})`);
  }
  upstreamFails = false;
  if (down) {
    check(down.status === 502, 'an unreachable origin should be 502');
    const body = await down.json().catch(() => ({}));
    check(body.ok === false && body.error, 'a 502 must carry a JSON error');
  }
}

// ------------------------------------------------- legacy ZEUS_ORIGIN alias
{
  const legacy = await call('/health', { env: { ZEUS_ORIGIN: ORIGIN } });
  check(legacy.status === 200, 'the legacy ZEUS_ORIGIN name must still work');
}

// ------------------------------------------------------- Telegram Web prefix
// The panel proxies web.telegram.org itself; the Worker only has to carry the
// prefix, because the whole reason a user needs the Worker is the address, not
// the proxying. Every part of the hop has to survive: the path (including the
// API-host form), the query, the method and the client address.
{
  const page = await call('/tg/k/', { headers: { 'cf-connecting-ip': '203.0.113.7' } });
  check(page.status === 200, 'the Telegram Web prefix should be forwarded');
  const forwarded = seen.find((request) => new URL(request.url).pathname === '/tg/k/');
  check(Boolean(forwarded), '/tg/k/ was never forwarded to the origin');
  if (forwarded) {
    check(forwarded.url.startsWith(ORIGIN + '/tg/k/'), 'the Telegram path must reach the origin unchanged');
    check(forwarded.headers.get('X-Forwarded-Proto') === 'https', 'the Telegram hop must be forwarded as https');
    check(forwarded.headers.get('X-Forwarded-For') === '203.0.113.7', 'the client address must reach the origin');
    check(!forwarded.headers.get('cf-connecting-ip'), 'Cloudflare internals must not leak to the origin');
  }
  const api = await call('/tg/__p/pluto.web.telegram.org/apiws?dc=2');
  check(api.status === 200, 'a nested Telegram API host should be forwarded');
  const nested = seen.find((request) => new URL(request.url).pathname === '/tg/__p/pluto.web.telegram.org/apiws');
  check(Boolean(nested) && new URL(nested.url).search === '?dc=2', 'the Telegram API path and query must survive');
  seen.length = 0;
  await worker.fetch(new Request(EDGE + '/tg/', {
    method: 'POST',
    body: 'login=1',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
  }), { NEXUS_ORIGIN: ORIGIN }, {});
  check(Boolean(seen.find((request) => request.method === 'POST')), 'a POST to the Telegram prefix must stay a POST');
  const without = await call('/tg/k/', { env: {} });
  check(without.status === 503, 'the Telegram prefix without an origin must be 503');
}

if (failures.length) {
  console.error('FAILED');
  failures.forEach((line) => console.error(' -', line));
  process.exit(1);
}
console.log(`OK — worker routed ${ADVERTISED.length} edge paths, ${failures.length} failures`);
