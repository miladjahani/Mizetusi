# NEXUS Cloudflare WebSocket Front

This Worker is a narrow WebSocket reverse proxy for the NEXUS edge. It never exposes a generic URL-fetch or open-proxy endpoint: only the exact paths the panel publishes are proxied, everything else is a 404.

## Deploy

1. Workers & Pages → Create Worker → paste `worker.js` → Deploy.
2. Settings → Variables:
   - `NEXUS_ORIGIN` — your Railway HTTPS origin, e.g. `https://nexus-production.up.railway.app` (the legacy `ZEUS_ORIGIN` name is still accepted).
   - `ALLOWED_HOSTS` — optional comma-separated Host allow-list, so the edge is never an open relay.
3. Enter the Worker URL in NEXUS → Cloudflare → Cloudflare Worker and press «ذخیره و Sync».

You rarely need to edit the file by hand: the panel serves the same source with your Railway origin already written into `ORIGIN_FALLBACK`, one click to copy or download (NEXUS → Cloudflare → «کد ورکر Cloudflare (بهینه)»).

## Endpoints

- `/health` (or `/diag`) — JSON status: `{ ok, worker, origin, paths, colo, country, time }`.
  Add `?probe=1` and the Worker also dials `<origin>/health`, so one call proves the
  Worker, the origin URL and the request route at once (`origin_probe`). The panel's
  «تست ورکر» button uses the probing form. It answers `503` when `NEXUS_ORIGIN` is
  missing or the origin is unreachable.
- Every published WebSocket path, proxied 1:1 to the same path on the origin:

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
  "the Worker is broken".

## What the Worker does for you

- Proxies the whole published transport matrix, not a hand-picked subset: VLESS, VMess, Trojan, all four Shadowsocks ciphers, the CDN path shapes and WARP.
- Strips Cloudflare/hop-by-hop headers (`cf-connecting-ip`, `cf-ray`, …) so the origin never sees spoofed client metadata — but keeps `Connection: upgrade` / `Upgrade: websocket`, which the origin's WebSocket handshake requires.
- Pins `X-Forwarded-Proto: https`, forwards the original `Host`/`X-Forwarded-Host` (the origin builds absolute subscription and status-window URLs from it) and forwards the real client IP as `X-Forwarded-For`, so IP limits and quota attribution stay correct through Cloudflare.
- Marks its own requests with `X-Nexus-Edge`, so the origin can tell a Worker-fronted request apart.
- Returns the `101 Switching Protocols` response untouched (rebuilding it would drop the WebSocket and hang the client), caches nothing, and answers an unreachable origin with a JSON `502` instead of letting the rejection escape into Cloudflare's opaque error page.

After the Worker is live, NEXUS health-probes Cloudflare IPs from Railway, keeps the Node Catalog up to date, and subscriptions use the healthy Cloudflare fronts — fastest first — as soon as you press «پینگ همه نودها».
