# NEXUS Cloudflare WebSocket Front

This Worker is a narrow WebSocket reverse proxy for the NEXUS `/ws/vless` and `/ws/trojan` endpoints. It does not expose a generic URL-fetch or open-proxy endpoint.

## Deploy

1. Workers & Pages → Create Worker → paste `worker.js` → Deploy.
2. Settings → Variables:
   - `NEXUS_ORIGIN` — your Railway HTTPS origin, e.g. `https://nexus-production.up.railway.app` (the legacy `ZEUS_ORIGIN` name is still accepted).
   - `ALLOWED_HOSTS` — optional comma-separated Host allow-list, so the edge is never an open relay.
3. Enter the Worker URL in NEXUS → Cloudflare → Cloudflare Worker and press «ذخیره و Sync».

You rarely need to edit the file by hand: the panel serves the same source with your Railway origin already written into `ORIGIN_FALLBACK`, one click to copy or download (NEXUS → Cloudflare → «کد ورکر Cloudflare (بهینه)»).

## Endpoints

- `/health` (or `/diag`) — JSON status: `{ ok, origin, paths, colo, country }`. The panel's «تست ورکر» button calls this, and it answers `503` when `NEXUS_ORIGIN` is missing.
- `/ws/vless`, `/ws/trojan`, `/ws` — WebSocket upgrade targets proxied to the origin.

## What the Worker does for you

- Strips Cloudflare/hop-by-hop headers (`cf-connecting-ip`, `cf-ray`, …) so the origin never sees spoofed client metadata.
- Pins `X-Forwarded-Proto: https` and forwards the original `Host`/`X-Forwarded-Host`, which is what the origin uses to build absolute subscription and status-window URLs.
- Returns the `101 Switching Protocols` response untouched (rebuilding it would drop the WebSocket and hang the client), and caches nothing.

After the Worker is live, NEXUS health-probes Cloudflare IPs from Railway, keeps the Node Catalog up to date, and subscriptions use the healthy Cloudflare fronts — fastest first — as soon as you press «پینگ همه نودها».
