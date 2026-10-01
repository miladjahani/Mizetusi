# NEXUS Worker Panel — a complete panel on Cloudflare's free plan

One Worker. No container, no server, no origin, no paid plan. It serves a
graphical panel, keeps its state in **KV** (settings) and **D1** (users), and
**proxies** — VLESS over WebSocket, terminated inside the Worker itself.

That last part is the point. The other two Cloudflare shapes in this repository
need something else to exist:

| shape | needs | why it is not this |
|---|---|---|
| `cloudflare-worker/` | an origin | it is a proxy with no app behind it, so its every path answers `503` |
| root `wrangler.jsonc` | **Workers Paid** | the panel is Python and lives in a Cloudflare Container; on Free the deploy dies on `/containers/me` |
| **this** | nothing | the panel *is* the Worker, and the proxy is JavaScript |

A Worker runs JavaScript only, so this deployment serves what JavaScript can
speak: **VLESS over WebSocket + TLS**, reached through any Cloudflare address.
It cannot terminate what needs its own raw TCP or UDP port — no Reality, no
TUIC, no MTProto, no HTTP/SOCKS5 web proxy. That is the trade, and it is the
only shape of this product that runs free.

## Deploy

```bash
# 1. the two bindings (once)
npx wrangler kv namespace create nexus-panel-kv      # → put the id in wrangler.jsonc
npx wrangler d1 create nexus-panel                   # → put the id in wrangler.jsonc

# 2. the table
npx wrangler d1 execute <database> --remote --file=worker-panel/schema.sql

# 3. the Worker
npx wrangler deploy --config worker-panel/wrangler.jsonc
```

Always pass `--config worker-panel/wrangler.jsonc`. Wrangler searches *upward*
for a config file, so a bare `wrangler deploy` from here finds the repository
root's `wrangler.jsonc` instead — the container deployment — and fails on
`@cloudflare/containers`. The root file is why this directory's config is
explicitly named everywhere.

The app also self-heals: `ensureSchema()` creates the table on the first request
of every isolate, so a deployment that skipped step 2 works anyway.

## Using it

1. Open the Worker's address. The password starts as **`admin`** — the panel says
   so until you change it, and you should change it immediately: the address is
   public the moment it exists.
2. Add a **custom domain** (`Workers & Pages → your Worker → Settings → Domains
   & Routes`). A `*.workers.dev` hostname is filtered in Iran, so a deployment
   left on it only opens through a VPN.
3. Create a user, copy its subscription URL into v2rayNG / Hiddify / Streisand.
4. Add **clean Cloudflare IPs** under «میزبان‌های اضافه» — one per line. Each one
   becomes an extra link in the same subscription, dialled instead of the Worker
   hostname, with the hostname still used as Host/SNI. That is what makes the
   subscription survive one Cloudflare address being blocked.

`POST /api/diag` answers the one question the panel cannot answer about itself:
can this deployment reach the internet? It says exactly what the platform said,
verbatim, which is the difference between «your client is misconfigured» and
«this deployment cannot dial out».

## What is measured

`used_bytes` is real. The relay adds up the bytes as they cross and flushes them
to D1 in batches (and once more on close), so the panel's usage column is
measured rather than estimated. The subscription response then carries
`subscription-userinfo` (plus `profile-update-interval: 12`), which is what makes
the client's own «subscription» screen show the usage and auto-update.

## Why the dial can fail

`connect()` is the Workers runtime API for outbound TCP, and it refuses some
destinations outright:

* **Cloudflare's own network** — `1.1.1.1`, `cloudflare.com` — is refused, so the
  Worker cannot be used to reach the platform's own services.
* Some addresses answer `proxy request failed, cannot connect to the specified
  address. It looks like you might be trying to connect to a HTTP-based service`.

Everything else dials normally (verified: `github.com:80` and `github.com:443`
both connect and proxy HTTP through the relay). The relay reports the failure
verbatim in the WebSocket close reason rather than closing silently, so a client
that "connects" and loads nothing always has an explanation on the server side.

## Layout

```
wrangler.jsonc    bindings: NEXUS_KV, NEXUS_DB
schema.sql        the one table (nexus_users)
src/index.js      router: /, /api/*, /sub/<uuid>, /ws*
src/panel.js      the panel document (one string, no build step)
src/store.js      KV settings + D1 users
src/auth.js       PBKDF2 password, HMAC session cookie
src/links.js      vless:// links and the subscription body   (pure)
src/vless-core.js VLESS header parsing, the dial deny-list   (pure)
src/vless.js      the relay: WebSocket ↔ cloudflare:sockets
```

The two `*-core`/`links` modules import nothing, which is what lets
`tests/worker_panel_smoke.mjs` drive them in Node — including compiling the panel
document's script exactly as a browser would.
