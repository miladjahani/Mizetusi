# ZEUS Railway Python Auto v9

ZEUS v9 is an independent Python/FastAPI control plane for a Railway deployment, with the official Xray-core binary used as the protocol engine. It keeps the glass/Telegram-style panel and automatic Node Catalog from v8, but replaces the lightweight custom TCP parser with Xray-core.

## What v9 adds

- Official Xray-core 26.9.9 in the Docker image.
- Automatic Xray config generation from ZEUS users.
- VLESS + WebSocket and Trojan + WebSocket public endpoints through Railway's HTTPS/WebSocket edge.
- Xray handles the actual VLESS/Trojan protocol parsing and forwarding, including UDP tunnelling supported by those transports.
- Per-user traffic statistics are collected through Xray StatsService and synchronized into ZEUS quota counters.
- When a GB quota is reached, the user is disabled and the Xray configuration is automatically rebuilt.
- User add/edit/delete/disable is reflected automatically without manually editing an Xray config.
- One live subscription continues to use the current Node Catalog.
- Railway direct and healthy Cloudflare-front nodes can be included automatically.
- Cloudflare Worker forwards `/ws/vless` and `/ws/trojan` to the Railway service.
- Admin password defaults to `admin` for the first login.

## Important Railway transport limitation

Railway normally exposes an HTTPS service and terminates TLS at its edge. Therefore the default v9 path is: `Client -> Railway/Cloudflare HTTPS+WebSocket -> FastAPI WebSocket bridge -> local Xray -> Internet`. REALITY and other raw TCP/TLS modes require a direct TCP listener with TLS/REALITY termination; they are not enabled on the normal Railway HTTPS edge. The Xray binary itself supports those transports, but this deployment does not pretend that Railway's HTTP edge is raw TCP passthrough.

## Public endpoints

- VLESS: `/ws/vless`
- Trojan: `/ws/trojan`
- Legacy `/ws` remains as a VLESS compatibility route.
- Subscription: `/sub/<UUID>?target=vless|trojan|base64|singbox|clash|json`

## Deploy

Deploy the repository to Railway using the included Dockerfile. The container starts FastAPI and automatically starts/reconciles Xray-core. A PostgreSQL database is recommended for production; SQLite remains available for local testing. Railway supplies `PORT` automatically.

Initial panel password: `admin`.

## Security

Change the initial admin password after first login. Do not expose the Xray API port publicly; v9 binds it to loopback.

## Verification

The project includes parser/config/subscription tests. Local Python compilation and the automated test suite should pass before deployment. The Docker build itself depends on Railway's ability to pull the official Xray image from GHCR.
