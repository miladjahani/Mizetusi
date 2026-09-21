# NEXUS · Xray control plane v9

NEXUS is a Python/FastAPI + Xray-core control plane that runs on Railway. It creates real
Xray users, publishes subscribable nodes (Railway direct + healthy Cloudflare clean IPs),
measures every node with a real probe, and gives each end user a public status window with
a dedicated subscription per client.

## What this release changes

- **Shadowsocks finally pings in every client, not only Happ.** The 2022 ciphers are what
  sing-box and mihomo parse best, but v2rayNG, NekoBox, Shadowrocket and every device that
  only learned SIP022 showed the Shadowsocks node greyed out. The classic AEAD method
  `aes-256-gcm` now ships first (`ss-classic`, `/ws/ss-classic` + `/cdn/ss-classic`), reached
  through `v2ray-plugin` exactly the way the working reference link does it —
  `mode=websocket;path=…;mux=0;host=…;tls` — and the 2022 cipher families stay right behind
  it. `tests/test_v9_features.py` decodes a real `ss://` link and asserts the method, the
  key and the plugin options.
- **Every node names its country.** `app/subscriptions/flags.py` maps a location slug, an
  English or Persian country name, a provider hostname — or a flag emoji read out of
  someone else's remark — to a flag, so a subscription entry reads `🇩🇪 آلمان · de-cdn-01`
  instead of a bare slug. `transports.node_flag`/`node_label` are the single place a link
  name is composed, so the «پرچم کشور روی نام نودها» switch in the new **شخصی‌سازی** tab
  governs links, panel lists and the status window together.
- **A Hysteria2 node, published only when it is real.** Hysteria2 is QUIC and has no Xray
  inbound, so the new **پیشرفته** tab stores *your* hysteria2 endpoint (host, port,
  password, SNI, `salamander` obfs, insecure) and publishes it as one extra entry — with its
  flag — across the line formats, sing-box and Clash, never into the Xray config (which has
  no such outbound) and never into a per-node subscription. A half-configured endpoint is
  refused rather than handed to users.
- **The status window keeps its shape, but the client list is split by engine.** Every client
  belongs to a family (`app/subscriptions/clients.py`) and each family is one collapsed
  dropdown: Xray clients (v2rayNG, Exclusive, Streisand, Shadowrocket, V2Box, FoXray) get
  Base64/text, sing-box clients (Hiddify, NekoBox, Karing, sing-box) get JSON, and
  Clash/Mihomo clients (Bettbox, Clash Verge) get **YAML only** — a client can never be handed
  a format its engine cannot import. Alternate formats stay hidden behind a second collapsed
  list, the admin's preferred family is marked recommended, and the window still carries the
  smart link, the banner, the support link and the config counter (`config_count` /
  `max_configs`).
- **How many configs a user gets is now a number you set.** The create/edit form has a
  **تعداد کانفیگ** field (and «شخصی‌سازی» holds the default quick-create uses). The cap is
  applied in exactly one place (`entry_pairs`), so the line formats, Base64, sing-box,
  Clash and Xray all hand out the same set — a client importing two of them sees one list.
- **Real multi-location, from packs or from any subscription.** Edge locations are no longer
  Cloudflare-only: the provider catalog grew to nineteen (Akamai, Google CDN, Azure Front
  Door, Edgio, CDN77, StackPath, CacheFly, KeyCDN, Imperva, Sucuri, Verizon/Edgecast,
  CDNetworks, QUANTIL, ChinaCache…), the **پیشرفته** tab installs ready-made location packs
  (one clean domain per country, or your own domains), and «ورود از سابلینک» turns any
  `subs.bikara.net`-style subscription into locations — host, port and country read from the
  entries themselves. Every location publishes the whole protocol matrix with its own
  Host/SNI, and one location can be handed out on its own (`/sub/<uuid>?location=de`).
- **Three new panel tabs.** **شخصی‌سازی** (the end-user experience: banner, support link,
  flags, recommended format, branding, default config count), **ابزار شبکه** (a multi-CDN
  scanner console, TCP/TLS reachability from the server, DoH lookup that bypasses a poisoned
  resolver, CIDR maths, subscription analysis and one-click "لوكيشن بساز از این سابلینک"),
  and **پیشرفته** (Hysteria2, location packs, subscription import, the transport truth — what
  the running engine really serves and what it withheld — plus Shadowsocks key rotation and
  a sync+ping button). All of it is wired in `static/js/app.js` with its own views.
- **Ready-made node examples.** The Nodes section has a **«نمونه‌های آماده»** button that
  creates nodes with genuinely different settings in one click: clean IPs of Cloudflare,
  Fastly, Gcore and آروان‌کلود, Cloudflare's alternative HTTPS ports (2053/2087/2096/8443), a
  clean domain, an IP/SNI pair from different ranges, a plain-WS fallback and the origin node
  itself (`app/edge/samples.py`). Each entry explains what it is for, defaults its Host/SNI to
  the domain the CDN really serves this deployment on (the Worker URL when configured), never
  overwrites an existing node, and is pinged the moment it is created.
- **It runs anywhere, not only on Railway.** The origin hostname, the raw TCP endpoint and
  the database directory are now resolved generically (`app/runtime.py`): Railway, Render,
  Fly.io, Koyeb, Heroku, Vercel, Replit and a plain VPS/Docker host, each with its own
  injected variables, plus generic `NEXUS_PUBLIC_DOMAIN` / `NEXUS_DIRECT_HOST` / `NEXUS_DIRECT_PORT`
  overrides. `render.yaml` and `docker-compose.yml` ship as ready blueprints. On a host that owns
  a public port (VPS/Docker/Fly) Reality switches itself on with no configuration at all; where
  no raw port exists (Render, Heroku) it stays unpublished instead of handing users a dead link.
- **Clean IPs and clean domains from anywhere — real multi-location.** The catalog is no longer
  Cloudflare-only: `app/edge/sources.py` knows Cloudflare, Fastly, Gcore, آروان‌کلود, Bunny and
  AWS CloudFront, plus hand-written IP/CIDR lists and clean domains. Every **source** is one
  location with its own Host/SNI, so `de-cloudflare-01`, `nl` or `ir-arvan` all publish the full
  protocol matrix, the panel groups them by location, and a user can subscribe to a single one
  (`/sub/<uuid>?location=de`). With no source configured the previous automatic behaviour is
  unchanged.
- **The protocol multi-select is never empty again.** The chips in the create/edit user
  form render from the settings catalog, and only the Settings section fetched it — so
  opening «ساخت/ویرایش کاربر» first (a very normal thing to do) showed an empty protocol
  box and the form refused to save. The users section now loads the catalog up front, the
  form waits for it before opening, and the chips fall back to the built-in
  VLESS/VMess/Trojan/Shadowsocks list if that request ever fails.
- **A user really does get every protocol.** The Xray inbounds used to filter users by the
  single `protocol` column, so a VLESS user's VMess/Trojan/Shadowsocks links were published
  but rejected by the engine. One credential is now registered on every inbound, and the
  panel's protocol field became a multi-select with all protocols on by default.
- **Shadowsocks is four ciphers, each a real client link.** AES-128-GCM, AES-256-GCM,
  ChaCha20-Poly1305 and the broadly compatible `chacha20-ietf-poly1305`, each with its own
  listener, its own key and its own subscription target across both edge path shapes, now
  published as SIP002 `ss://` links (with the `v2ray-plugin` WebSocket edge) instead of
  being visible only in the JSON formats.
- **Shadowsocks is served single-user, on purpose.** A multi-user Shadowsocks-2022 inbound is
  rejected by Xray for every method but `blake3-aes-*-gcm`, and a single rejected inbound
  used to abort startup and take *every* protocol down; clients also disagree about which key
  a user has to present. One key per cipher is accepted by every client, so the panel rotates
  keys per cipher (Settings → «چرخش کلید شادوساکس») to revoke a leaked link.
- **A transport the engine refuses can no longer kill the deployment.** The config is tried
  richest-first (full → without WARP/Reality → Shadowsocks reduced cipher by cipher), and only
  the profiles the running engine really contains are published, so a subscription can never
  point at a listener that does not exist.
- **The Cloudflare Worker proxies the whole matrix.** It only knew `/ws`, `/ws/vless` and
  `/ws/trojan`, so every VMess, Shadowsocks, CDN and WARP node 404'd behind Cloudflare while
  working on the Railway origin. It also forwards the real client IP, keeps the handshake
  headers the origin needs, and answers an unreachable origin with a JSON `502`.
- **A local end-to-end proof.** `scripts/e2e_tunnel_check.py` boots the generated server
  config, the FastAPI edge and one client outbound per published transport, then pushes a real
  HTTP request through every tunnel on a host that has Xray installed.
- **Freebuff-side hardening.** Edge routes and the Worker's path table are now generated from
  (and asserted against) the transport profile table, and two new headless probes cover them:
  `tests/worker_smoke.mjs` and the protocol/Shadowsocks cases in `tests/test_panel_api.py`.

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
| `app/subscriptions/flags.py` | Country → flag lookup for node names (slug, name, provider or emoji) |
| `app/api_extra.py` | The newer admin APIs: customization, Hysteria2, location packs, network tools |
| `app/edge/packs.py` | Multi-location packs + the subscription importer |
| `app/subscriptions/transports.py` | Transport profiles and the Shadowsocks cipher table |
| `app/subscriptions/clients.py` | Client catalog + the Iran-ready quick-create presets |
| `app/xray.py` | Xray-core supervisor: config generation, reload, StatsService sync |

Xray is the protocol engine while FastAPI stays the public HTTPS/WebSocket edge on whatever
host this runs on: the edge terminates TLS and bridges WebSocket streams to the loopback
Xray listeners. `app/runtime.py` answers "where am I, what is my public hostname, is there a
raw TCP port, which directory is writable", and `app/edge/sources.py` owns the clean-IP
providers, the clean domains and the locations they form.

### Front end (`static/js/`)

| Module | Class(es) |
| --- | --- |
| `core.js` | DOM/format helpers, `SafeStorage`, `Fmt`, `ToastCenter`, `EventBus` |
| `ui.js` | `ModalManager`/`Modal`, hand-rolled SVG `Charts`, `StatusKit` |
| `session.js` | `SessionManager` — cookie/token/session recovery |
| `api.js` | `ApiClient`/`ApiError` — timeouts, JSON, single-flight 401 |
| `store.js` | `PanelStore` state container, `Router` |
| `pwa.js` | `PwaManager` — service worker + install prompt |
| `views/*.js` | `DashboardView`, `NodesView`, `UsersView`, `CloudflareView`, `CustomizeView`, `ToolsView`, `AdvancedView`, `SettingsView` |
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

- The origin node (`railway-direct`, `render-direct`, `vps-direct`… depending on the platform)
  is created automatically from `PUBLIC_BASE_URL`, the platform's own variable, or the saved
  base URL. It is the one node that is never dropped after a failed probe, so a subscription
  can never come back empty.
- Edge nodes are rebuilt automatically too. When locations are configured they define the
  catalog; otherwise the Worker host wins, and failing that, if the panel's own domain is
  served through Cloudflare (custom-domain setups), NEXUS detects that with one TLS handshake
  against a healthy clean IP (SNI = panel host) and publishes the CF nodes with no user input.
- The catalog rebuild runs at startup, on every panel open, on «Sync نودها», and on a
  ~10-minute background loop; the ping loop keeps every latency fresh.
- Latency convention: `NULL` never probed, `>= 0` measured, `-1` last probe failed.
  Subscriptions are ordered fastest-first; a failed clean-IP node is dropped, but the
  Railway origin always stays published (DNS still resolves during a transient failure),
  so a subscription can never come back empty.
- Probes run on demand («پینگ همه نودها», per node, or «پینگ» on any location) and on a
  background loop whose interval is the `ping_interval` setting.
- **A ping is the client's own handshake.** For a TLS node health means a completed TLS
  handshake with that node's own Host/SNI — the check a client performs when it pings an
  entry. A bare TCP connect is measured and reported too (`ping_tcp`), but it is never
  health, so a location whose Host/SNI its addresses cannot serve is marked broken (and
  dropped from subscriptions) instead of being advertised as healthy and timing out in
  every client. When a certificate does not verify but the handshake completes, the node
  stays published and the panel says so.

## Transports — one node, every protocol

A subscription entry is a **(node × transport profile)** pair, defined once in
`app/subscriptions/transports.py` and consumed by the Xray config, the generator and the
panel. Every node publishes all of these **by default**, with no admin action:

| Profile | Protocol | Transport | Where it runs |
|---|---|---|---|
| `vless-ws` / `vless-cdn` | VLESS | WebSocket + TLS | `/ws/vless`, `/cdn/vless` |
| `vmess-ws` / `vmess-cdn` | VMess | WebSocket + TLS | `/ws/vmess`, `/cdn/vmess` |
| `trojan-ws` / `trojan-cdn` | Trojan | WebSocket + TLS | `/ws/trojan`, `/cdn/trojan` |
| `ss-ws` / `ss-cdn` | Shadowsocks-2022 · AES-128-GCM | WebSocket + TLS | `/ws/ss`, `/cdn/ss` |
| `ss-aes256-ws` / `ss-aes256-cdn` | Shadowsocks-2022 · AES-256-GCM | WebSocket + TLS | `/ws/ss-aes256`, `/cdn/ss-aes256` |
| `ss-chacha-ws` / `ss-chacha-cdn` | Shadowsocks-2022 · ChaCha20-Poly1305 | WebSocket + TLS | `/ws/ss-chacha`, `/cdn/ss-chacha` |
| `ss-legacy-ws` / `ss-legacy-cdn` | Shadowsocks · ChaCha20-IETF | WebSocket + TLS | `/ws/ss-legacy`, `/cdn/ss-legacy` |
| `vless-reality` | VLESS | Reality (TCP) | the direct port, when one exists |
| `warp-ws` | VLESS | WebSocket → WARP exit | `/ws/warp`, once WARP is enabled |

The two path shapes per protocol exist so a blocked path never takes the service down, and
Shadowsocks ships one listener per cipher family (each cipher needs a key of its own length,
so a single profile cannot represent them all). Each profile gets its own local Xray
listener; the FastAPI edge bridges each WebSocket path to the matching listener
(`EDGE_ROUTES`), so one Railway HTTP port serves all of them. Those routes are *generated*
from the profile table rather than hand-written, and the Cloudflare Worker's copy of the same
path list is asserted against it by `tests/test_panel_api.py` — a transport can therefore
never be published without a route on both edges.

### One credential, every protocol

A user is not tied to a single protocol. The `protocol` column holds a **set**
(comma-separated), every user is registered on **every** inbound by default, and the panel
renders it as a multi-select with all protocols switched on — so VLESS, VMess, Trojan and
all four Shadowsocks ciphers work for the same UUID/password with no extra step. VLESS, VMess
and Trojan keep one credential **per user** (disabling the user revokes it everywhere);
Shadowsocks authenticates one key **per cipher**, because Xray refuses a multi-user
Shadowsocks-2022 inbound for every method but `blake3-aes-*-gcm` and a single rejected
listener used to abort startup for the whole engine. Rotating those keys is therefore the SS
revoke path (Settings → «امنیت پنل»), and the panel reports which profiles the running engine
really serves so no link ever points at a listener that was dropped. Narrowing
the set is optional and only removes the links that were turned off (a subscription target
for a disabled protocol answers `400` instead of returning an empty list). Rows written by
older releases carry a single value, which still means "all protocols", because that is what
those users have always received.

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

- VLESS: `/ws/vless` · VMess: `/ws/vmess` · Trojan: `/ws/trojan` · WARP: `/ws/warp`
- Shadowsocks: `/ws/ss-classic` (AES-256-GCM, every client) · `/ws/ss` (2022 · AES-128-GCM) · `/ws/ss-aes256` · `/ws/ss-chacha` · `/ws/ss-legacy`
- CDN path shapes: `/cdn/vless`, `/cdn/vmess`, `/cdn/trojan`, `/cdn/ss-classic`, `/cdn/ss`, `/cdn/ss-aes256`, `/cdn/ss-chacha`, `/cdn/ss-legacy` · legacy `/ws`
- Subscription: `/sub/<UUID>?target=auto|all|vless|trojan|vmess|ss|base64|singbox|clash|xray|json`
- By transport: `?target=ws|cdn|reality|warp` or one exact profile, e.g. `?target=vless-cdn`
- Per-client: `/sub/<UUID>?target=bettbox|exclusive|nekoboxplus|v2rayng|hiddify|karing|streisand|shadowrocket|v2box|foxray|nekobox|amnezia|smart`
- Per-node: `/sub/<UUID>?target=vless&node=<node-name>` or `/sub/<UUID>/<node-name>`
- Status window: `/portal/<UUID>` (also `/portal/<username>` and legacy `/status/<username>`), data at `/portal/<UUID>/json`
- Panel API: `/api/metrics`, `/api/users`, `/api/users/<name>/links`, `/api/users/quick`,
  `/api/presets`, `/api/clients`, `/api/transports`, `/api/warp`, `/api/nodes`,
  `/api/nodes/ping`, `/api/nodes/sync`, `/api/settings`, `/api/logs`, `/api/backup`,
  `/api/core/status`, `/api/settings/rotate-shadowsocks`
- Newer admin API: `/api/customization`, `/api/hysteria`, `/api/edge/packs`,
  `/api/edge/import`, `/api/tools/check`, `/api/tools/dns`, `/api/tools/cidr`, `/api/tools/parse`
- Cloudflare Worker: `/api/cloudflare/worker-code`, `/api/cloudflare/worker-download`, `/api/cloudflare/worker-test`
- PWA: `/manifest.webmanifest`, `/sw.js`, `/static/icons/*`

`target=auto` (and `all`, and the `smart` client) returns **every node × every transport**:
the default subscription already carries VLESS, VMess, Trojan and Shadowsocks for each node,
ordered fastest-node-first. `base64` is the same matrix encoded for the clients that only
read base64 blobs. Every response carries `X-NEXUS-Format`, `X-NEXUS-Node-Count`,
`X-NEXUS-Transports` and `X-NEXUS-Target`.

Shadowsocks is published as a **SIP002 link**: the method and key travel as base64 userinfo
and the WebSocket edge as the `v2ray-plugin` SIP003 plugin, which is what v2rayNG, NekoBox,
sing-box, mihomo and Shadowrocket all implement — so SS nodes appear in a client's config
list next to the other protocols and `?target=ss` returns links. A profile set with no link
form at all (Reality alone, on an install with no raw TCP port) still answers with the
sing-box JSON instead of a `400` that reads as "broken".

Because a Shadowsocks listener authenticates one key per cipher, that key is what a link
carries — disabling a user revokes VLESS/VMess/Trojan immediately, while Shadowsocks access
is revoked by rotating its keys (`POST /api/settings/rotate-shadowsocks`, or the button in
Settings → «امنیت پنل»), which reloads the engine so old links stop authenticating at once.

## The Cloudflare Worker

`cloudflare-worker/worker.js` is a narrow WebSocket reverse proxy (never a generic fetch or
open relay) so Iranian users can dial a clean Cloudflare IP while the traffic still ends in
the Railway container. It proxies **every** published path, not a hand-picked subset: a path
the panel hands out but the Worker refuses 404s behind Cloudflare while working on the
Railway origin, which reads as "the Worker is broken". It also forwards the real client IP as
`X-Forwarded-For` (so IP limits and quota attribution survive Cloudflare), keeps
`Connection: upgrade`/`Upgrade: websocket` (the origin's handshake requires both), and turns
an unreachable origin into a JSON `502` instead of letting the rejection escape into
Cloudflare's opaque error page. `/health?probe=1` makes the Worker dial the origin itself, so
the panel's «تست ورکر» button proves the Worker, the origin URL and the route in one call.

## Clients, presets and the quick-create button

`app/subscriptions/clients.py` holds the client catalog (import format, platform, download
link, notes) and the presets used by quick create. Client ids are accepted as subscription
targets, so every client gets its own URL — `/sub/<uuid>?target=bettbox` returns the Clash
YAML (Bettbox is a Mihomo client), `?target=v2rayng` returns the Base64 (V2Ray) list and
`?target=nekoboxplus` returns sing-box JSON — and `&node=<name>` narrows it to a single node.

Quick create precedence is request values, then the panel defaults, then the preset. Each
created user gets a status window at `/portal/<uuid>` listing the smart link, the per-client
subscriptions, the client download buttons and every node with its measured ping. Download
links are editable in Settings → «لینک دانلود کلاینتها» because stores and release pages move.

## Deploy anywhere — Railway, Render, VPS

Deploy the repository with the included Dockerfile; the container starts FastAPI and
reconciles Xray-core automatically. A PostgreSQL database is recommended for production
(SQLite is used otherwise, on the mounted volume or in a local `data/` directory when the
volume is missing). Every platform supplies `PORT`.

The runtime layer detects where it is and adapts (`GET /api/system/runtime` shows what was
detected):

| Platform | Public hostname from | Raw TCP (Reality) |
| --- | --- | --- |
| Railway | `RAILWAY_PUBLIC_DOMAIN` / `RAILWAY_STATIC_URL` | only after a TCP Proxy is added |
| Render | `RENDER_EXTERNAL_URL` / `RENDER_EXTERNAL_HOSTNAME` | not on the free plan |
| Fly.io | `FLY_APP_NAME` (`.fly.dev`) | yes (TCP services are free) |
| Koyeb / Heroku / Azure | `KOYEB_PUBLIC_DOMAIN` / `HEROKU_APP_DOMAIN` / `WEBSITE_HOSTNAME` | no |
| VPS / Docker | `NEXUS_PUBLIC_DOMAIN`, else the machine's public IP | yes — auto-detected |

* **Render** — Render → New → Blueprint, then pick this repository (`render.yaml`). The disk
  needs a paid instance; without it the app stores sqlite in a local directory and still boots.
* **VPS** — `NEXUS_PUBLIC_DOMAIN=panel.example.com docker compose up -d --build`, then open
  8080 (panel + WebSocket edge) and 8443 (Reality) in the firewall. `NET_ADMIN` is only needed
  by the optional WARP exit.
* **Anywhere else** — set `NEXUS_PUBLIC_DOMAIN` (and `NEXUS_DIRECT_HOST`/`NEXUS_DIRECT_PORT` if
  a raw port is reachable) and run the image; nothing else is provider-specific.

> **Choosing a host.** NEXUS is a proxy/VPN control plane, and most PaaS acceptable-use
> policies forbid running one — Railway, Render and friends also watch for the outbound
> patterns a scanner produces, which is how a workspace can be restricted for "suspicious
> activity" minutes after a deploy. A VPS or dedicated server you control (Hetzner, Netcup,
> a provider in Iran) is the honest home for this software: one raw TCP port for Reality,
> no fair-use limits, and no third party inspecting your traffic. The defaults above
> (`scan_on_boot=0`, small batches, `NEXUS_OUTBOUND_PROBE_ENABLED=0` if the host must never
> see outbound measurement traffic) keep the footprint small wherever it runs.

### Ready-made nodes

The Nodes section can create a set of example nodes with one click:

| Sample | What it publishes |
| --- | --- |
| دامنهٔ تمیز | the deployment's own hostname as a node (what a custom domain behind a CDN needs) |
| Cloudflare · IP تمیز + Worker | a Cloudflare anycast IP with the Worker hostname as Host/SNI |
| Cloudflare · پورت‌های جایگزین | the same edge on 2053/2087/2096/8443, for networks that drop 443 |
| Cloudflare · IP و SNI متفاوت | an address from one range carrying another range's SNI |
| Fastly / Gcore / آروان | clean IPs of three more CDNs, each as its own location |
| بدون TLS · پورت ۸۰ | the same host over plain WebSocket, as a last resort |
| نود مستقیم (Origin) | the always-present origin node, if it was deleted |

Adding a sample never overwrites an existing node, and a sample node survives the automatic
catalog rebuild (only the rows the rebuild owns are reset). `POST /api/nodes/samples` with
`ids` (or `all: true`), `GET /api/nodes/samples` and `DELETE /api/nodes/samples` are the same
actions over the API.

### Outbound probing is quiet by default

Filling and measuring the clean-IP pool means opening sockets to third-party CDNs,
and doing that the moment the container boots is what gets a deployment flagged for
"suspicious activity" by a hosting provider. The defaults are therefore small and
explicit:

| Setting | Default | Meaning |
| --- | --- | --- |
| `cf_probe_limit` | `64` | addresses kept (and probed) per pass |
| `cf_probe_concurrency` | `8` | probes in flight at once |
| `scan_on_boot` (`NEXUS_SCAN_ON_BOOT`) | `0` | never seed the pool automatically at boot |
| `outbound_probe_enabled` (`NEXUS_OUTBOUND_PROBE_ENABLED`) | `1` | `0` stops every external probe and ping |

With the defaults a fresh deploy publishes only the origin node — which is a complete
subscription — and the admin scans a provider on demand from the panel (or presses
«اسکن همه providerها»). The «منابع لبه و لوکیشن‌ها» card shows the active scan mode and the
per-pass size, so the outbound footprint is visible rather than implicit.

### Edge sources (clean IPs, clean domains, locations)

The Cloudflare section of the panel has a **«منابع لبه و لوکیشن‌ها»** card. One source = one
location:

* **IP source** — pick a provider and press «اسکن همه providerها»; the published ranges are
  downloaded, sampled, TCP-probed and turned into nodes named `<location>-<provider>-NN`. Your
  own clean IPs (or CIDRs) can be pasted instead, under any provider or as `custom`.
  A provider whose pool is still empty is **filled on demand** the first time a location needs
  it (bounded, cached for five minutes), so a location added by hand publishes real,
  pingable addresses on a deployment that never ran a scan — `scan_on_boot` is off by default,
  and without this a location published *nothing at all* and therefore could not ping.
* **Domain source** — paste a clean domain (a custom domain behind Cloudflare, a CDN hostname,
  another server of yours); it becomes a node of its own, and the whole protocol matrix is
  published through it.

Every source carries a `location`, its own Host/SNI and a node cap; disabling or deleting a
location stops its nodes from being published, and the leftover rows are removed on the next
sync. `POST /api/edge/scan`, `POST /api/edge/sources`, `POST /api/edge/ips` and the matching
`DELETE`s are the same actions over the API.

`GET /api/edge` enriches every source with its own health — `addresses`, `nodes`, `healthy`,
`failed`, `pending`, `fastest_ms` and a one-line `reason`. The locations table renders it as an
آی‌پی count, a `پینگ` verdict (`2/3 · 42 ms`, `ناموفق · 3`, `پینگ نشده`) and a `data-edge="ping"`
button per row, backed by **`POST /api/edge/sources/{id}/ping`**: it syncs, then probes exactly
that location's nodes the way a client would and returns the per-address results. Saving a
location, scanning a provider, adding clean IPs, toggling a location on, installing a location
pack and importing a subscription all measure the affected locations in the same request, so a
new location is never published unverified.

Icons are committed, but can be regenerated after a rebrand with:

```bash
python3 scripts/make_icons.py
```

## Verification

```bash
python -m pytest -q                        # backend + panel API + PWA asset tests
node tests/js_smoke.mjs                    # links the ES-module graph and exercises the render paths
node tests/worker_smoke.mjs                # routes every published path through the real Worker source
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
