/* Headless smoke test for the Worker-only panel (`worker-panel/`).

   The Worker cannot be started here — the relay needs `cloudflare:sockets` and
   a real socket — so this test drives the parts that decide whether a deployment
   works at all, and which are *pure* enough to run in Node:

   * the VLESS header parser, with frames built byte by byte (a real client's
     opening frame is the one thing that must never be misread);
   * the link/subscription builders, because a link with the wrong Host is a
     link that connects nowhere;
   * the session signing, including the tampered and expired cases;
   * the panel document itself, whose script is compiled here exactly as a
     browser would compile it — an escaped backtick that slipped through the
     template literal would otherwise only be found by loading the page;
   * the wrangler bindings, which is the one place the KV/D1 wiring can silently
     disagree with the code.

   Run:  node tests/worker_panel_smoke.mjs */

import { readFileSync } from 'node:fs';

import {
  ADDR_DOMAIN, ADDR_IPV4, ADDR_IPV6, CMD_TCP, addressAllowed, bytesFromUuid, concatBytes,
  parseVlessHeader, uuidFromBytes,
} from '../worker-panel/src/vless-core.js';
import { buildLinks, normalizeUuid, subscriptionBody, vlessLink } from '../worker-panel/src/links.js';
import { createSession, hashPassword, safeEqual, verifySession } from '../worker-panel/src/auth.js';
import { panelHtml } from '../worker-panel/src/panel.js';

const failures = [];
const check = (ok, label) => { if (!ok) failures.push(label); };

const UUID = '3f2a1c48-9d6b-4e7a-8f10-2b5c7e9a1d44';
const OTHER_UUID = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d';

/* ----------------------------------------------------------- frame building */

const encodeDomain = (address) => {
  const bytes = new TextEncoder().encode(address);
  return Uint8Array.from([bytes.length, ...bytes]);
};
const encodeIpv4 = (address) => Uint8Array.from(address.split('.').map(Number));
const encodeIpv6 = (address) => {
  const out = [];
  for (const group of address.split(':')) {
    const value = parseInt(group, 16);
    out.push((value >> 8) & 255, value & 255);
  }
  return Uint8Array.from(out);
};

/** The opening frame a VLESS client sends, built the way the client builds it. */
function frame({ uuid = UUID, address = 'example.com', port = 443, atyp = ADDR_DOMAIN, command = CMD_TCP, payload = new Uint8Array(0), version = 0 }) {
  const body = atyp === ADDR_DOMAIN ? encodeDomain(address)
    : atyp === ADDR_IPV4 ? encodeIpv4(address)
      : encodeIpv6(address);
  const head = Uint8Array.from([
    version,
    ...bytesFromUuid(uuid),
    0,                                                       // addon length
    command,
    (port >> 8) & 255, port & 255,
    atyp,
    ...body,
  ]);
  return concatBytes(head, payload);
}

/* ------------------------------------------------------------ the VLESS frame */

const domainFrame = frame({ payload: Uint8Array.from([9, 8, 7]) });
const domain = parseVlessHeader(domainFrame);
check(!!domain, 'a domain-addressed frame must parse');
check(domain?.uuid === UUID, 'the parsed UUID must be the one the client sent');
check(domain?.address === 'example.com', 'a domain destination must be decoded as text');
check(domain?.port === 443, 'the port is big endian');
check(domain?.command === CMD_TCP, 'command 1 is TCP');
check(domain?.headLength === domainFrame.length - 3, 'the header length must stop where the stream starts');
check(Array.from(domain?.payload || []).join(',') === '9,8,7',
  'the bytes after the header are stream data, not header');

const ipv4 = parseVlessHeader(frame({ address: '104.16.132.229', atyp: ADDR_IPV4 }));
check(ipv4?.address === '104.16.132.229', 'an IPv4 destination must be dotted-quad, not bytes');

const ipv6 = parseVlessHeader(frame({ address: '2606:4700:4700:0:0:0:0:1111', atyp: ADDR_IPV6 }));
check(ipv6?.address === '2606:4700:4700:0:0:0:0:1111', 'an IPv6 destination must round-trip');

// A relay sits in this state every time a client splits its header — it must
// come back null (buffer and wait), never throw and never guess.
const whole = frame({ address: 'split.example.com' });
let allIncomplete = true;
for (let cut = 0; cut < whole.length; cut += 1) {
  if (parseVlessHeader(whole.subarray(0, cut)) !== null) allIncomplete = false;
}
check(allIncomplete, 'an incomplete header must never parse');
const joined = concatBytes(whole.subarray(0, 11), whole.subarray(11));
check(parseVlessHeader(joined)?.address === 'split.example.com', 'a split header must parse once joined');

check(parseVlessHeader(frame({ version: 1 })) === null, 'a version this protocol never defined must be refused');
check(parseVlessHeader(Uint8Array.from([0, 1, 2])) === null, 'a stub frame must not parse');
check(parseVlessHeader(Uint8Array.from([0, ...new Array(16).fill(0), 0, 1, 0, 443, 9, 0])) === null,
  'an unknown address type must be refused rather than dialled');
// A UDP command parses (it is a valid frame) and is refused by the relay, which
// is where the decision belongs — the parser must not hide it from the caller.
check(parseVlessHeader(frame({ command: 2 }))?.command === 2, 'a UDP command must still parse');

/* ------------------------------------------------------------------ deny list */

check(addressAllowed('example.com') && addressAllowed('104.16.132.229'), 'public destinations must be allowed');
check(addressAllowed('2606:4700::1111'), 'a public IPv6 destination must be allowed');
check(!addressAllowed('169.254.169.254'), 'the metadata address must be refused');
check(!addressAllowed('localhost') && !addressAllowed('box.internal') && !addressAllowed('printer.local'),
  'this deployment own neighbourhood must be refused');
check(!addressAllowed('::1') && !addressAllowed('fe80::1'), 'IPv6 loopback and link-local must be refused');
check(!addressAllowed(''), 'an empty destination must be refused');

/* --------------------------------------------------------------------- UUIDs */

check(uuidFromBytes(bytesFromUuid(UUID)) === UUID, 'a UUID must survive the bytes round trip');
check(bytesFromUuid('not-a-uuid') === null, 'a non-UUID must not become bytes');
check(normalizeUuid(UUID.toUpperCase()) === UUID, 'a UUID is normalised, not rejected, when it is upper case');
check(normalizeUuid('  ') === null, 'blank input is not a UUID');

/* --------------------------------------------------------------------- links */

const link = vlessLink({ uuid: UUID, address: '104.16.132.229', host: 'panel.example.com', name: 'ali · 1' });
check(link.startsWith(`vless://${UUID}@104.16.132.229:443?`), 'the link must dial the address it was given');
const params = new URLSearchParams(link.split('?')[1].split('#')[0]);
check(params.get('security') === 'tls' && params.get('type') === 'ws', 'a Worker link is TLS over WebSocket');
check(params.get('host') === 'panel.example.com' && params.get('sni') === 'panel.example.com',
  'the Host header and SNI must be the name Cloudflare routes, not the clean IP');
check(params.get('path') === '/ws', 'the path is the relay path');
check(decodeURIComponent(link.split('#')[1]).includes('ali'), 'the remark must survive the link');

const built = buildLinks({
  uuid: UUID, name: 'ali', workerHost: 'panel.example.com',
  hosts: ['104.16.132.229', 'panel.example.com', '  '], path: '/ws',
});
check(built.length === 2, 'the Worker host plus one clean IP is two links, duplicates dropped');
check(built[0].includes('@panel.example.com:443'), 'the Worker own hostname must always come first');
check(built[1].includes('@104.16.132.229:443'), 'a clean IP is an extra way in, never a replacement');
check(decodeURIComponent(built[1].split('#')[1]).includes('2'), 'each target is numbered in the remark');

const body = subscriptionBody(built);
const decoded = Buffer.from(body, 'base64').toString('utf8');
check(decoded.split('\n').length === built.length, 'the subscription body must carry every link');
check(decoded.split('\n')[0] === built[0], 'the subscription body must not reorder or re-encode links');

/* -------------------------------------------------------------------- session */

const secret = 'secret-one';
const salt = 'salt-one';
const hash = await hashPassword('hunter2', salt);
check(hash === await hashPassword('hunter2', salt), 'the same password and salt must hash the same');
check(hash !== await hashPassword('hunter2', 'salt-two'), 'a different salt must not collide');
check(hash.length === 64, 'the stored value is a 256-bit digest');
const altered = hash.slice(0, -1) + (hash.endsWith('0') ? '1' : '0');
check(safeEqual(hash, hash) && !safeEqual(hash, altered), 'comparison must be exact');
check(!safeEqual(hash, hash.slice(0, -1)), 'a shorter value must not compare equal');

const token = await createSession(secret);
check(await verifySession(secret, token), 'a freshly signed session must verify');
check(!(await verifySession('another-secret', token)), 'a session signed with another secret must not verify');
check(!(await verifySession(secret, token.split('.')[0] + '.deadbeef')), 'a tampered signature must not verify');
check(!(await verifySession(secret, '')), 'a missing token must not verify');
const expired = await createSession(secret, -10);
check(!(await verifySession(secret, expired)), 'an expired session must not verify');

/* ------------------------------------------------------------ the panel page */

const html = panelHtml({ host: 'panel.example.com', version: '1.0.0' });
check(html.startsWith('<!DOCTYPE html>'), 'the panel is a whole document');
check(html.includes('dir="rtl"'), 'the panel is RTL, like the rest of the product');
check(html.includes('#c9f24c'), 'the panel wears the repository own palette');
for (const id of ['loginView', 'appView', 'userRows', 'stats', 'addUser', 'saveSettings', 'savePw', 'subHook']) {
  check(html.includes(`id="${id}"`), `the panel must define #${id}`);
}
check(html.includes('panel.example.com') && html.includes('1.0.0'), 'the panel names its host and version');
check(html.includes('رمز پیش‌فرض'), 'the panel must tell the admin the password starts as admin');
check(!html.includes('__HOST__') && !html.includes('__VERSION__'), 'no placeholder may reach the browser');

// The panel's script is written inside a template literal, so a single unescaped
// backtick or `${` would produce a page that *looks* fine and does nothing. This
// compiles it the way the browser will, which is the only cheap way to catch it.
const script = html.slice(html.indexOf('<script>') + '<script>'.length, html.lastIndexOf('</script>'));
check(script.length > 1000, 'the panel must carry its own script');
try {
  // eslint-disable-next-line no-new-func
  new Function(script);
} catch (error) {
  failures.push(`the panel script does not parse: ${error.message}`);
}

/* ------------------------------------------------------------------- bindings */

const wrangler = readFileSync(new URL('../worker-panel/wrangler.jsonc', import.meta.url), 'utf8');
check(/"name"\s*:\s*"[a-z0-9-]+"/.test(wrangler), 'the Worker name must be a valid script name');
check(/kv_namespaces/.test(wrangler) && /NEXUS_KV/.test(wrangler), 'the KV namespace must be bound');
check(/d1_databases/.test(wrangler) && /NEXUS_DB/.test(wrangler), 'the D1 database must be bound');
check(!/containers/.test(wrangler) && !/durable_objects/.test(wrangler),
  'this Worker must not need a container or a durable object — that is the whole point of it');

const store = readFileSync(new URL('../worker-panel/src/store.js', import.meta.url), 'utf8');
const schema = readFileSync(new URL('../worker-panel/schema.sql', import.meta.url), 'utf8');
check(/nexus_users/.test(schema) && /nexus_users/.test(store),
  'the schema and the data layer must agree on the table name');
check(/env\.NEXUS_DB/.test(store) && /env\.NEXUS_KV/.test(store),
  'the data layer must use the bindings the config declares');

const router = readFileSync(new URL('../worker-panel/src/index.js', import.meta.url), 'utf8');
for (const route of ["'/api/'", "'/sub/'", "'/ws'", 'vlessUpgrade']) {
  check(router.includes(route), `the router must serve ${route}`);
}
check(/vlessUpgrade\(request, env, ctx\)/.test(router), 'the relay needs the request context (it meters usage after the response)');

const relay = readFileSync(new URL('../worker-panel/src/vless.js', import.meta.url), 'utf8');
check(/from 'cloudflare:sockets'/.test(relay), 'the relay dials out through cloudflare:sockets');
check(/userUsable\(found\)/.test(relay), 'the relay must refuse a user D1 does not vouch for');
check(/addressAllowed\(header\.address\)/.test(relay), 'the relay must refuse a hostile destination');

if (failures.length) {
  console.error('FAILED');
  for (const failure of failures) console.error(' -', failure);
  process.exit(1);
}
console.log('OK — the Worker panel parses VLESS, builds links, signs sessions and serves a compiling panel');
