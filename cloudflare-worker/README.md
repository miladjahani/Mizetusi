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

## Deploy

1. Workers & Pages → Create Worker → paste `worker.js` → Deploy.
2. Settings → Variables:
   - `NEXUS_ORIGIN` — your Railway HTTPS origin, e.g. `https://nexus-production.up.railway.app` (the legacy `ZEUS_ORIGIN` name is still accepted).
   - `ALLOWED_HOSTS` — optional comma-separated Host allow-list for the WebSocket edge, so the relay is never reachable under an unexpected name.
3. Enter the Worker URL in NEXUS → Cloudflare → Cloudflare Worker and press «ذخیره و Sync».
   From then on the Worker URL is also a working panel address: open it, log in, and every
   subscription link the panel builds points at that host.

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
  copies would point back at the blocked address.
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
