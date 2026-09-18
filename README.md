# NEXUS Railway Python Auto v7

NEXUS is a Python/FastAPI + Xray-core control plane that runs on Railway. It creates real
Xray users, publishes subscribable nodes (Railway direct + healthy Cloudflare clean IPs),
measures every node with a real probe, and gives each end user a public status window with
a dedicated subscription per client.

## What v7 changes

- **Object-oriented structure.** The backend is a small service layer
  (`SettingsStore`, `SessionManager`, `LoginThrottle`, `AuditLog`, `NodeCatalog`,
  `NodeProbe`) instead of loose module functions, and every route delegates to it.
- **The front end is real ES modules.** `static/js/app.js` is the single entry point and
  imports the core, API client, session manager, router, PWA manager and the four view
  classes. No bundler, no CDN, and no cross-file globals — the class of
  duplicate-declaration crashes the old five-script bundle could hit is gone.
- **A crashed view is no longer fatal.** Every render and every handler runs through
  `NexusApp.safe()`; window `error`/`unhandledrejection` are captured and surfaced as a
  toast, and the shell keeps working.
- **Installable app (PWA).** Dynamic manifest, service worker, and a generated NEXUS icon
  set (192/512/maskable/apple/favicon + vector logo). Install it on a phone or desktop with
  the «نصب برنامه» button in the topbar or in Settings.
- **A deploy can never leave a phone on the old build.** `/sw.js` is served with a build
  token hashed from the shipped assets, so a new deploy installs a new worker, drops the old
  shell cache and reloads the open panel exactly once. The Settings → سیستم card shows the
  running `version · build` so it is obvious which copy a device is painting.
- **Sessions no longer log you out.** The lifetime is an admin setting (default 7 days,
  up to 365), remember-me extends it to 30 days, the token is kept in durable storage so a
  phone reload survives a blocked cookie, and a 401 raises **one** in-panel re-login overlay
  instead of a toast storm plus a redirect loop.
- **The Node Catalog self-heals.** Railway's injected public domain (or the configured base
  URL) creates the `railway-direct` node at boot and whenever the panel is opened, so a
  fresh deployment never publishes an empty catalog or 404s a subscription.
- **Cloudflare nodes build themselves, with no admin action.** Boot seeds Cloudflare's
  published CIDRs, then the edge is detected by dialling a clean IP with the panel's own host
  as SNI; a configured Worker URL simply wins over that. Detection and the catalog rebuild
  tolerate a deployment where nothing has been probed yet, so the clean-IP nodes (and their
  real ping) exist before anyone opens the Node Catalog.
- **The layout fits every screen.** Grid/flex children can shrink (`min-width:0`), long
  subscription URLs wrap over two clamped lines instead of pushing the panel sideways, node
  rows move their action buttons to a second line, and tables reflow cell-by-cell under
  700px — all of it asserted by the stylesheet test.
- **Settings grew up.** Session length, automatic ping interval, app name and both theme
  accent colours are validated settings, and the client download links stay editable at
  runtime.

## Architecture

### Backend

| Module | Responsibility |
| --- | --- |
| `app/main.py` | FastAPI routes; delegates to the services below |
| `app/core/settings_store.py` | `SettingsStore` — typed access to the `settings` table |
| `app/core/security.py` | `SessionManager` (signed sessions + cookie policy), `LoginThrottle` |
| `app/services/audit.py` | `AuditLog` — append-only admin trail with Persian labels |
| `app/nodes.py` | `NodeCatalog` (CRUD/sync/bootstrap) and `NodeProbe` (real latency) |
| `app/subscriptions/generator.py` | Subscription rendering for every target/format |
| `app/subscriptions/clients.py` | Client catalog + the Iran-ready quick-create presets |
| `app/xray.py` | Xray-core supervisor: config generation, reload, StatsService sync |

Xray is the protocol engine while FastAPI stays the public HTTPS/WebSocket edge on Railway:
the edge terminates TLS and bridges WebSocket streams to the loopback Xray listeners.

### Front end (`static/js/`)

| Module | Class(es) |
| --- | --- |
| `core.js` | DOM/format helpers, `SafeStorage`, `Fmt`, `ToastCenter`, `EventBus` |
| `ui.js` | `ModalManager`/`Modal`, hand-rolled SVG `Charts`, `StatusKit` |
| `session.js` | `SessionManager` — cookie/token/session recovery |
| `api.js` | `ApiClient`/`ApiError` — timeouts, JSON, single-flight 401 |
| `store.js` | `PanelStore` state container, `Router` |
| `pwa.js` | `PwaManager` — service worker + install prompt |
| `views/*.js` | `DashboardView`, `NodesView`, `UsersView`, `CloudflareView`, `SettingsView` |
| `app.js` | `NexusApp` — wiring, loaders, clock, polling, auth recovery |

## Session model

1. The primary session is an HttpOnly cookie. On HTTPS same-site requests it is
   `SameSite=strict; Secure`; embedded cross-site panes get `SameSite=None; Secure`
   (the only policy a browser will send back there).
2. Login also returns the signed token, which the panel stores and sends as
   `X-Nexus-Session`. That keeps the panel authenticated when the browser refuses the
   cookie entirely.
3. A 401 stops polling, shows a re-login overlay (page state preserved) and never
   redirects in a loop. Rotating the secret in Settings logs every device out on purpose.

## Nodes and probing

The Node Catalog is fully self-building — no admin action is ever required:

- `railway-direct` is created automatically from `PUBLIC_BASE_URL`,
  `RAILWAY_PUBLIC_DOMAIN`/`RAILWAY_STATIC_URL`, or the saved base URL.
- Cloudflare clean-IP nodes are rebuilt automatically too. The Worker host wins when a
  Worker URL is configured; otherwise, if the panel's own domain is served through
  Cloudflare (custom-domain setups), NEXUS detects that with one TLS handshake against a
  healthy clean IP (SNI = panel host) and publishes the CF nodes with no user input.
- The catalog rebuild runs at startup, on every panel open, on «Sync نودها», and on a
  ~10-minute background loop; the ping loop keeps every latency fresh.
- Latency convention: `NULL` never probed, `>= 0` measured, `-1` last probe failed.
  Subscriptions are ordered fastest-first; a failed clean-IP node is dropped, but the
  Railway origin always stays published (DNS still resolves during a transient failure),
  so a subscription can never come back empty.
- Probes run on demand («پینگ همه نودها», or per node) and on a background loop whose
  interval is the `ping_interval` setting.

## Transports — one node, every protocol

A subscription entry is a **(node × transport profile)** pair, defined once in
`app/subscriptions/transports.py` and consumed by the Xray config, the generator and the
panel. Every node publishes all of these **by default**, with no admin action:

| Profile | Protocol | Transport | Where it runs |
|---|---|---|---|
| `vless-ws` / `vless-cdn` | VLESS | WebSocket + TLS | `/ws/vless`, `/cdn/vless` |
| `vmess-ws` / `vmess-cdn` | VMess | WebSocket + TLS | `/ws/vmess`, `/cdn/vmess` |
| `trojan-ws` / `trojan-cdn` | Trojan | WebSocket + TLS | `/ws/trojan`, `/cdn/trojan` |
| `ss-ws` | Shadowsocks-2022 | WebSocket + TLS | `/ws/ss` (sing-box/Clash/Xray output) |
| `vless-reality` | VLESS | Reality (TCP) | the direct port, when one exists |
| `warp-ws` | VLESS | WebSocket → WARP exit | `/ws/warp`, once WARP is enabled |

The two path shapes per protocol exist so a blocked path never takes the service down.
Each profile gets its own local Xray listener; the FastAPI edge bridges each WebSocket path
to the matching listener (`EDGE_ROUTES`), so one Railway HTTP port serves all of them.

**Reality** terminates TLS inside Xray with a real site's certificate, so it needs a raw
TCP endpoint: set `direct_host`/`direct_port` in the panel (or enable a Railway TCP proxy,
which Railway announces through `RAILWAY_TCP_PROXY_DOMAIN`/`RAILWAY_TCP_PROXY_PORT`). The
key pair is generated with the bundled binary and cached, and the profile appears in every
subscription the moment the endpoint exists.

**gRPC, XHTTP and HTTPUpgrade are deliberately not published.** Reality only accepts RAW,
XHTTP and gRPC clients, the h2-shaped transports need a dedicated port each (Reality
fallbacks are not honoured for them in Xray 26.9.9 — verified), and a Railway service can
expose exactly one raw TCP port. They are listed in the panel's transport view as
*planned* rather than shipped as links that dead-end.

**WARP** is opt-in on purpose. Register a peer in Settings → «نود WARP» (one anonymous
Cloudflare API call, from `app/warp.py`), then enable it: Xray gets a `wireguard` outbound
and the `/ws/warp` inbound is routed through it, so the WARP node is a real exit node. It
stays off until an admin turns it on because a WARP tunnel needs outbound WireGuard/UDP and
a node that silently dead-ends is worse than no node — `scripts/check_transports_e2e.py`
with `WARP_CONFIG` set proves the tunnel on the host that will run it.

**Amnezia** ships as a client profile (the Amnezia client imports the Base64 list and the
Reality profile). AmneziaWG itself is not an Xray protocol, so no Amnezia-specific inbound
is advertised.

## Public endpoints

- VLESS: `/ws/vless` · VMess: `/ws/vmess` · Trojan: `/ws/trojan` · Shadowsocks: `/ws/ss`
- CDN path shapes: `/cdn/vless`, `/cdn/vmess`, `/cdn/trojan` · WARP: `/ws/warp` · legacy `/ws`
- Subscription: `/sub/<UUID>?target=auto|all|vless|trojan|vmess|ss|base64|singbox|clash|xray|json`
- By transport: `?target=ws|cdn|reality|warp` or one exact profile, e.g. `?target=vless-cdn`
- Per-client: `/sub/<UUID>?target=bettbox|exclusive|nekoboxplus|v2rayng|hiddify|karing|streisand|shadowrocket|v2box|foxray|nekobox|amnezia|smart`
- Per-node: `/sub/<UUID>?target=vless&node=<node-name>` or `/sub/<UUID>/<node-name>`
- Status window: `/portal/<UUID>` (also `/portal/<username>` and legacy `/status/<username>`), data at `/portal/<UUID>/json`
- Panel API: `/api/metrics`, `/api/users`, `/api/users/<name>/links`, `/api/users/quick`,
  `/api/presets`, `/api/clients`, `/api/transports`, `/api/warp`, `/api/nodes`,
  `/api/nodes/ping`, `/api/nodes/sync`, `/api/settings`, `/api/logs`, `/api/backup`,
  `/api/core/status`
- Cloudflare Worker: `/api/cloudflare/worker-code`, `/api/cloudflare/worker-download`, `/api/cloudflare/worker-test`
- PWA: `/manifest.webmanifest`, `/sw.js`, `/static/icons/*`

`target=auto` (and `all`, and the `smart` client) returns **every node × every transport**:
the default subscription already carries VLESS, VMess and Trojan for each node, ordered
fastest-node-first. `base64` is the same matrix encoded for the clients that only read
base64 blobs. Shadowsocks-over-WebSocket has no sharing-URI form, so `?target=ss` returns
the sing-box JSON for exactly those profiles instead of a link that would dial the wrong
endpoint; the panel marks such rows. Every response carries `X-NEXUS-Format`,
`X-NEXUS-Node-Count`, `X-NEXUS-Transports` and `X-NEXUS-Target`.

## Clients, presets and the quick-create button

`app/subscriptions/clients.py` holds the client catalog (import format, platform, download
link, notes) and the presets used by quick create. Client ids are accepted as subscription
targets, so every client gets its own URL — `/sub/<uuid>?target=bettbox` returns the Base64
(V2Ray) list and `?target=nekoboxplus` returns sing-box JSON — and `&node=<name>` narrows it
to a single node.

Quick create precedence is request values, then the panel defaults, then the preset. Each
created user gets a status window at `/portal/<uuid>` listing the smart link, the per-client
subscriptions, the client download buttons and every node with its measured ping. Download
links are editable in Settings → «لینک دانلود کلاینتها» because stores and release pages move.

## Deploy on Railway

Deploy the repository with the included Dockerfile; the container starts FastAPI and
reconciles Xray-core automatically. A PostgreSQL database is recommended for production
(SQLite is used otherwise, on the mounted volume). Railway supplies `PORT`.

Icons are committed, but can be regenerated after a rebrand with:

```bash
python3 scripts/make_icons.py
```

## Verification

```bash
python -m pytest -q                        # backend + panel API + PWA asset tests
node tests/js_smoke.mjs                    # links the ES-module graph and exercises the render paths
python scripts/check_subscriptions.py      # prints the exact matrix one user receives
python scripts/check_xray_config.py        # builds the real Xray config and runs `xray run -test`
python scripts/check_transports_e2e.py     # drives real traffic through each direct transport
```

The three `check_*` scripts build their own throwaway database, so they never touch the
live one. `check_xray_config.py` needs the Xray binary (`XRAY_BIN=/usr/local/bin/xray`);
`check_transports_e2e.py` additionally proves Reality and (when `WARP_CONFIG` is supplied)
the WARP exit node actually carry traffic, and exits 0 without testing when the binary is
missing. `app/xray.py` runs the same `xray run -test` before every start and falls back to
the transports that need no raw TCP port, so a rejected optional inbound can never take the
transport every deployment already serves down.

The Python suite covers the parsers, subscription rendering, panel API, session/cookie
policy, presets, client catalog, per-node links, settings validation, node bootstrap and the
PWA assets. The Node smoke test does not need a browser or a build step.

Initial panel password: `admin` — change it right after the first login. Do not expose the
Xray API port publicly; it is bound to loopback.
