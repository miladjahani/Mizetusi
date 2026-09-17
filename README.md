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
- **Sessions no longer log you out.** The lifetime is an admin setting (default 7 days,
  up to 365), remember-me extends it to 30 days, the token is kept in durable storage so a
  phone reload survives a blocked cookie, and a 401 raises **one** in-panel re-login overlay
  instead of a toast storm plus a redirect loop.
- **The Node Catalog self-heals.** Railway's injected public domain (or the configured base
  URL) creates the `railway-direct` node at boot and whenever the panel is opened, so a
  fresh deployment never publishes an empty catalog or 404s a subscription.
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

## Public endpoints

- VLESS: `/ws/vless` · Trojan: `/ws/trojan` · legacy `/ws`
- Subscription: `/sub/<UUID>?target=auto|all|vless|trojan|base64|singbox|clash|xray|json`
- Per-client: `/sub/<UUID>?target=bettbox|exclusive|nekoboxplus|v2rayng|hiddify|karing|streisand|shadowrocket|v2box|foxray|nekobox|smart`
- Per-node: `/sub/<UUID>?target=vless&node=<node-name>` or `/sub/<UUID>/<node-name>`
- Status window: `/portal/<UUID>` (also `/portal/<username>` and legacy `/status/<username>`), data at `/portal/<UUID>/json`
- Panel API: `/api/metrics`, `/api/users`, `/api/users/<name>/links`, `/api/users/quick`,
  `/api/presets`, `/api/clients`, `/api/nodes`, `/api/nodes/ping`, `/api/nodes/sync`,
  `/api/settings`, `/api/logs`, `/api/backup`, `/api/core/status`
- Cloudflare Worker: `/api/cloudflare/worker-code`, `/api/cloudflare/worker-download`, `/api/cloudflare/worker-test`
- PWA: `/manifest.webmanifest`, `/sw.js`, `/static/icons/*`

`target=all` returns both the VLESS and Trojan link for every node, so a client can reach
each node over either protocol (`auto` picks the user's own protocol).

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
python -m pytest -q          # backend + panel API + PWA asset tests
node tests/js_smoke.mjs      # links the ES-module graph and exercises the render paths
```

The Python suite covers the parsers, subscription rendering, panel API, session/cookie
policy, presets, client catalog, per-node links, settings validation, node bootstrap and the
PWA assets. The Node smoke test does not need a browser or a build step.

Initial panel password: `admin` — change it right after the first login. Do not expose the
Xray API port publicly; it is bound to loopback.
