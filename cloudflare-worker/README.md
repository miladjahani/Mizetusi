# NEXUS Cloudflare Edge

This Worker is a reverse proxy for one NEXUS deployment. It fronts **the whole panel** — pages,
the API, subscription and portal links, static assets, uploads, redirects and the panel's own
WebSockets — and every published transport path, so a client that can only reach Cloudflare
keeps both the panel and its node links on one clean address. It is never a generic URL-fetch
or open proxy: the origin is fixed at deploy time (`NEXUS_ORIGIN`) and only the panel's own
routes and the published transport paths are forwarded.

That matters because the usual failure in a filtered network is the *address*, not the code:
the Railway domain and its certificate name stop resolving, so the panel will not open and the
node the subscription hands out cannot connect. Serving the same deployment through Cloudflare
gives the client an anycast IP it can reach.

## Which Cloudflare deployment do you want?

There are two, and this pair of files is the first one:

* **The app runs somewhere else** (Railway, a VPS, Render) and this Worker is a clean address in front of
  it — that is what the rest of this file describes. `NEXUS_ORIGIN` is a URL.
* **The app runs on Cloudflare itself**, in a Cloudflare **Container** built from this repository's own
  `Dockerfile`, with this same Worker in front of it. No Railway project, no VPS. A Worker cannot run
  Python or Xray, so that deployment is a Worker *plus* a container: see
  [`wrangler.jsonc`](../wrangler.jsonc), [`cloudflare-containers/`](../cloudflare-containers/) and
  **[docs/CLOUDFLARE-DEPLOY-FA.md](../docs/CLOUDFLARE-DEPLOY-FA.md)** (Persian). The proxy code is byte
  for byte the file below — it resolves its upstream once, and a bound `NEXUS_CONTAINER` wins over
  `NEXUS_ORIGIN`.

## Deploy

1. Workers & Pages → Create Worker → paste `worker.js` → Deploy.
2. Settings → Variables:
   - `NEXUS_ORIGIN` — your Railway HTTPS origin, e.g. `https://nexus-production.up.railway.app` (the legacy `ZEUS_ORIGIN` name is still accepted).
   - `ALLOWED_HOSTS` — optional comma-separated Host allow-list for the WebSocket edge, so the relay is never reachable under an unexpected name.
3. Settings → Domains & Routes → **Add custom domain**: attach a subdomain of a domain this
   Cloudflare account owns (e.g. `panel.example.com`). See the next section for why this is
   the step that decides reachability.
4. Enter the Worker URL — the **custom domain**, not the `workers.dev` one — in
   NEXUS → Cloudflare → Cloudflare Worker and press «ذخیره و Sync».
   From then on the Worker URL is also a working panel address: open it, log in, and every
   subscription link the panel builds points at that host. Saving a `workers.dev` address is
   accepted but answered with a warning, because that hostname is not reachable from a
   filtered network either.

### Deploying it with wrangler instead of pasting

The `wrangler.toml` beside this file describes the same Worker for the CLI:

```bash
cd cloudflare-worker
npx wrangler deploy --config wrangler.toml
```

The `--config` flag is **not optional**, and the reason is worth knowing. wrangler searches
*upward* from the working directory and prefers a `wrangler.jsonc` wherever it finds one, so from
inside this folder it picks up the repository root's `wrangler.jsonc` — the **container**
deployment — and tries to bundle `cloudflare-containers/worker.js` instead. The error it produces
is misleading in exactly the way that costs an afternoon:

```
✘ [ERROR] Could not resolve "@cloudflare/containers"
    cloudflare-containers/worker.js:50:26
```

It names a file most people deploying this edge Worker have never opened, and a package they
did not install. Passing `--config wrangler.toml` pins the local config and the upload is 9.75 KiB
with no build step at all.

Put `NEXUS_ORIGIN` in the `[vars]` block before deploying, or leave it empty and set it in the
dashboard afterwards — the variable there wins either way. Without it the Worker is deployed and
healthy but answers `503` on the panel routes, which `/health` reports as
`"origin": null, "panel": false`.

## The hostname is what decides reachability

A Worker answers on `https://<name>.<subdomain>.workers.dev` until it is given a custom domain,
and the whole `workers.dev` suffix is filtered in Iran. So a Worker left on its default hostname
is a **second** address that only opens through a VPN — the same trap as the Railway domain,
just with a different name. The fix is a hostname the filter does not already know:

1. Keep a domain in the same Cloudflare account (any cheap one; it does not have to be the
   domain the panel "should" live on).
2. Worker → Settings → Domains & Routes → **Add custom domain** → `panel.example.com`.
   Cloudflare issues the certificate and serves the Worker from its anycast IPs on that name.
3. Put **that** hostname in the panel, then press «پینگ همه نودها» so the node links are rebuilt
   on it too.

A Cloudflare-proxied record in front of the Railway origin can work as well, but it only carries
HTTP: the WebSocket transports are not proxied to Railway's edge that way, and the panel's own
link hosts are not rewritten. The Worker is the shape that does both.

Two habits keep the new domain alive: leave the Telegram **WEB proxy off** (its carrier is the
panel's own address, so serving it is a signature a network can block the whole domain on), and
keep `ADMIN_PASSWORD` set.

You rarely need to edit the file by hand: the panel serves the same source with your Railway
origin already written into `ORIGIN_FALLBACK`, one click to copy or download (NEXUS →
Cloudflare → «کد ورکر Cloudflare (بهینه)»).

## Endpoints

- `/health` (or `/diag`) — JSON status: `{ ok, worker, origin, paths, panel, colo, country, time }`.
  Add `?probe=1` and the Worker also dials `<origin>/health`, so one call proves the
  Worker, the origin URL and the request route at once (`origin_probe`). The panel's
  «تست ورکر» button uses the probing form. It answers `503` when `NEXUS_ORIGIN` is
  missing or the origin is unreachable.
- **Everything else is the panel**, proxied 1:1 (method, query, body and WebSocket upgrades
  included): `/`, `/login`, `/api/…`, `/sub/…`, `/portal/…`, `/static/…`, `/tg/…` and the
  panel's own sockets such as `/api/v1/socket`.
- Every published WebSocket transport path, proxied to the same path on the origin:

  | Path | Protocol |
  | --- | --- |
  | `/ws/vless`, `/cdn/vless` | VLESS over WebSocket |
  | `/ws/vmess`, `/cdn/vmess` | VMess over WebSocket |
  | `/ws/trojan`, `/cdn/trojan` | Trojan over WebSocket |
  | `/ws/ss`, `/cdn/ss` | Shadowsocks-2022 · AES-128-GCM |
  | `/ws/ss-aes256`, `/cdn/ss-aes256` | Shadowsocks-2022 · AES-256-GCM |
  | `/ws/ss-chacha`, `/cdn/ss-chacha` | Shadowsocks-2022 · ChaCha20-Poly1305 |
  | `/ws/ss-legacy`, `/cdn/ss-legacy` | Shadowsocks · ChaCha20-IETF (widest client support) |
  | `/ws/warp` | VLESS over WebSocket, exiting through WARP |
  | `/ws` | legacy VLESS alias |

  The list is mirrored from `app/subscriptions/transports.py`; `tests/worker_smoke.mjs`
  drives each path through this file and `tests/test_panel_api.py` fails if the two
  lists ever drift. A path that is missing here is a Cloudflare node that works on the
  Railway origin but 404s behind Cloudflare — which is exactly what a user reports as
  "the Worker is broken". An unknown `/ws/…` or `/cdn/…` path stays a `404` with the path
  list, so a typo'd node path is never mistaken for a panel route.

## What the Worker does for you

- **Fronts the panel and the relays on one address.** A filtered network needs one reachable
  domain: this is it. Pages, the API, subscription/portal links, uploads, redirects and the
  panel's own WebSockets are proxied to the same origin as the transports.
- **Keeps the panel's links on the reachable host.** Every forwarded request carries
  `X-Forwarded-Proto: https` and `X-Forwarded-Host: <worker host>`, which is what the origin
  builds its absolute subscription and status-window URLs from — otherwise a link a user
  copies would point back at the blocked address. The saved Worker URL itself is authoritative
  too (`public_url`), so a link copied while the admin is on the Railway domain still names the
  Worker, and even `PUBLIC_BASE_URL` does not pull the links back to a filtered host.
- **Rewrites origin redirects to the Worker host.** A redirect that names the origin
  (`Location: https://<origin>/login`) is rewritten so a browser is never bounced back to the
  address that is filtered; relative redirects are handed to the browser untouched instead of
  being followed here, so the address bar stays correct.
- Strips Cloudflare/hop-by-hop headers (`cf-connecting-ip`, `cf-ray`, …) so the origin never sees spoofed client metadata — but keeps `Connection: upgrade` / `Upgrade: websocket`, which the origin's WebSocket handshake requires.
- Forwards the real client IP as `X-Forwarded-For`, so IP limits and quota attribution stay correct through Cloudflare.
- Marks its own requests with `X-Nexus-Edge`, so the origin can tell a Worker-fronted request apart.
- Returns the `101 Switching Protocols` response untouched (rebuilding it would drop the WebSocket and hang the client), caches nothing, and answers an unreachable origin with a JSON `502` instead of letting the rejection escape into Cloudflare's opaque error page.

## Trust model

- The origin is not configurable per request: it comes from `NEXUS_ORIGIN` / `ZEUS_ORIGIN` /
  `ORIGIN_FALLBACK` only, and the outgoing path is always this request's path on that origin.
  A path shaped like an absolute URL stays a `404`.
- The Worker adds no authentication of its own — it is as public as the panel behind it is.
  Keep `ADMIN_PASSWORD` set, and keep the panel's own session and IP rules in force.
- `ALLOWED_HOSTS` applies to the WebSocket edge (`/ws/…`, `/cdn/…`). It is deliberately not
  applied to the panel routes: an allow-list that accidentally excludes the new panel hostname
  would lock the admin out of the very panel they just moved.

After the Worker is live, NEXUS health-probes Cloudflare IPs from Railway, keeps the Node
Catalog up to date, and subscriptions use the healthy Cloudflare fronts — fastest first — as
soon as you press «پینگ همه نودها».
