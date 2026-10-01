#!/usr/bin/env bash
# NEXUS on a fresh Linux host — one command.
#
# Run this on any host you control: it installs Docker, fetches this repository,
# writes a `.env` with a generated admin password and session secret, and starts
# the stack. There are two shapes, and the difference is where the public address
# comes from:
#
#   * a host with its own public IP (a VPS) serves every transport — Reality,
#     AnyTLS, TUIC, MTProto and the HTTP/SOCKS5 web proxies — because the host
#     owns its ports instead of asking a platform for a TCP proxy. Pass no flags
#     and open the ports it prints.
#   * a host behind NAT — a home connection, a Raspberry Pi, an old laptop — needs
#     `--tunnel <token>`, which adds a Cloudflare Tunnel. `cloudflared` dials
#     *out*, so nothing needs a public IP, a forwarded port or a firewall change,
#     and it costs nothing (`docs/FREE-DEPLOY-FA.md`). Only HTTP(S) and WebSocket
#     upgrades cross a tunnel, so the raw-TCP transports stay unpublished — that
#     is not a misconfiguration, and `--tunnel` tells the panel so by setting
#     NEXUS_PLATFORM=tunnel, which is what stops it publishing links a client
#     could never dial.
#
# This script does the four things that otherwise have to be done by hand:
#
#   1. install Docker + the compose plugin (Debian/Ubuntu, via Docker's own
#      convenience script — nothing else is assumed to be present);
#   2. fetch this repository (or use the checkout it is already run from);
#   3. write a `.env` with a generated admin password and session secret, so a
#      fresh host never boots on the shipped guessable default;
#   4. build and start the stack, then print exactly which ports to open.
#
# Usage (as root, or through sudo):
#
#   curl -fsSL https://raw.githubusercontent.com/miladjahani/Mizetusi/main/scripts/install-vps.sh | sudo bash
#   # …or, on an existing checkout:
#   sudo bash scripts/install-vps.sh
#   sudo bash scripts/install-vps.sh --domain panel.example.com --open-firewall
#   sudo bash scripts/install-vps.sh --tunnel <token> --domain panel.example.com
#
# Flags:
#   --domain <host>   the domain clients should use (default: the host's IP)
#   --dir <path>      where the checkout lives / is cloned (default /opt/nexus)
#   --repo <url>      repository to clone (default: this project)
#   --tunnel <token>  publish through a Cloudflare Tunnel instead of opening ports
#                     (the free path for a host with no public IP)
#   --open-firewall   run the ufw rules for the published ports (default: print them)
#   --no-build        skip `docker compose build` (reuse existing images)
set -euo pipefail

REPO_URL="${NEXUS_REPO_URL:-https://github.com/miladjahani/Mizetusi.git}"
TARGET_DIR="${NEXUS_DIR:-/opt/nexus}"
DOMAIN=""
TUNNEL_TOKEN=""
OPEN_FIREWALL=0
DO_BUILD=1

# The ports the stack publishes. Kept in one place so the summary, the ufw rules
# and docker-compose.yml can never drift apart silently. The 101xx block is the
# advanced transports (one port each) — published, but nothing listens on one
# until an admin switches that transport on in «مبهم‌سازی پیشرفته».
PORTS_TCP=(8080 8443 8446 8448 8449 10101 10102 10104 10105 10106)

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m%s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx \033[0m%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --dir) TARGET_DIR="${2:-}"; shift 2 ;;
    --repo) REPO_URL="${2:-}"; shift 2 ;;
    --tunnel) TUNNEL_TOKEN="${2:-}"; shift 2 ;;
    --open-firewall) OPEN_FIREWALL=1; shift ;;
    --no-build) DO_BUILD=0; shift ;;
    -h|--help) sed -n '2,/^set -euo pipefail/p' "$0"; exit 0 ;;
    *) die "unknown flag: $1 (try --help)" ;;
  esac
done

# The profile is chosen by the flag, and an empty token would silently start a
# tunnel that authenticates as nobody — so it is refused here rather than leaving
# a container in a restart loop.
if [ "${TUNNEL_TOKEN:-}" != "" ] && [ -z "${TUNNEL_TOKEN// /}" ]; then
  die "--tunnel needs the token, not empty space"
fi

[ "$(id -u)" -eq 0 ] || die "run this as root (or: sudo bash $0)"

# ------------------------------------------------------------------ 1. docker
if ! command -v docker >/dev/null 2>&1; then
  log "installing Docker (and the compose plugin)"
  command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl ca-certificates; }
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
else
  log "Docker already present ($(docker --version))"
fi
# `docker compose` (plugin) is what this project uses; the old `docker-compose`
# binary is a different, unmaintained tool and is not what the compose file expects.
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is missing (install docker-compose-plugin)"

# ------------------------------------------------------------------ 2. checkout
# Running from an existing checkout (this script is inside it) must not re-clone:
# the operator's own edits and database volume live here.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$SELF_DIR/docker-compose.yml" ] && [ "$SELF_DIR" != "$TARGET_DIR" ]; then
  log "using the current checkout: $SELF_DIR"
  TARGET_DIR="$SELF_DIR"
elif [ -f "$TARGET_DIR/docker-compose.yml" ]; then
  log "using the existing checkout: $TARGET_DIR"
else
  command -v git >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq git; }
  log "cloning $REPO_URL → $TARGET_DIR"
  mkdir -p "$(dirname "$TARGET_DIR")"
  git clone --depth 1 "$REPO_URL" "$TARGET_DIR"
fi
cd "$TARGET_DIR"

# ------------------------------------------------------------------ 3. secrets
random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "$1"
  else
    head -c 96 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "$1"
  fi
}

if [ -f .env ]; then
  log ".env already exists — left untouched (your password and secret stay)"
  grep -q '^ADMIN_PASSWORD=' .env || warn "ADMIN_PASSWORD is not set in .env; add it before exposing the panel"
  # The tunnel flag is the one thing that *does* update an existing file: it is a
  # new decision about how the host is published, not a secret to preserve. Both
  # lines are rewritten in place so a re-run cannot leave the old platform (which
  # would promise ports the tunnel does not carry) next to a fresh token.
  if [ -n "$TUNNEL_TOKEN" ]; then
    log "updating .env for the Cloudflare Tunnel"
    cp .env .env.backup
    sed -i '/^NEXUS_PLATFORM=/d;/^CLOUDFLARE_TUNNEL_TOKEN=/d' .env
    printf 'NEXUS_PLATFORM=tunnel\nCLOUDFLARE_TUNNEL_TOKEN=%s\n' "$TUNNEL_TOKEN" >> .env
    chmod 600 .env
  fi
else
  log "writing .env with a generated admin password and session secret"
  ADMIN_PASS="$(random_secret 24)"
  JWT="$(random_secret 48)"
  cat > .env <<EOF
# Generated by scripts/install-vps.sh — keep this file private.
ENVIRONMENT=production
ADMIN_PASSWORD=${ADMIN_PASS}
JWT_SECRET=${JWT}
# The domain clients should use; empty falls back to this host's public IP.
NEXUS_PUBLIC_DOMAIN=${DOMAIN}
PUBLIC_BASE_URL=${DOMAIN:+https://${DOMAIN}}
SQLITE_PATH=/data/nexus.db
XRAY_ENABLED=true
EOF
  if [ -n "$TUNNEL_TOKEN" ]; then
    cat >> .env <<EOF
# Cloudflare Tunnel: this host is published through Cloudflare, not on its own
# ports, so the panel must not advertise a raw TCP endpoint it cannot serve.
NEXUS_PLATFORM=tunnel
CLOUDFLARE_TUNNEL_TOKEN=${TUNNEL_TOKEN}
EOF
  fi
  chmod 600 .env
  printf '\n    \033[1;36mADMIN_PASSWORD = %s\033[0m\n' "$ADMIN_PASS"
  printf '    (also saved in %s/.env — change it in the panel any time)\n\n' "$TARGET_DIR"
fi

# ------------------------------------------------------------------ 4. start
# An empty array under `set -u` is a bash-version trap, so the profile list is
# expanded through the ${arr[@]+...} form.
PROFILE=()
[ -n "$TUNNEL_TOKEN" ] && PROFILE=(--profile tunnel)

log "building and starting the stack${TUNNEL_TOKEN:+ (with the Cloudflare Tunnel profile)}"
if [ "$DO_BUILD" -eq 1 ]; then
  docker compose ${PROFILE[@]+"${PROFILE[@]}"} up -d --build
else
  docker compose ${PROFILE[@]+"${PROFILE[@]}"} up -d
fi

# ------------------------------------------------------------------ 5. summary
HOST="${DOMAIN:-$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<this-host>')}"

if [ -n "$TUNNEL_TOKEN" ]; then
cat <<EOF

  NEXUS is up, behind a Cloudflare Tunnel.

    panel : https://${DOMAIN:-<your-tunnel-hostname>}/admin
              (the Public Hostname you set in Zero Trust → Networks → Tunnels →
               your tunnel, with the service URL http://nexus:8080)

  Nothing was forwarded and no port was opened: cloudflared keeps an outbound
  connection to Cloudflare, so this host needs no public IP and can sit behind
  NAT on a normal home connection.

    live here : the panel, subscription and portal links, every WebSocket
                transport (VLESS/VMess/Trojan/Shadowsocks/WARP) and the Telegram
                WEB proxy
    not here  : Reality, AnyTLS, TUIC, MTProto and the HTTP/SOCKS5 web proxies —
                they need a raw TCP/UDP port, which a tunnel does not carry. The
                panel knows (NEXUS_PLATFORM=tunnel) and withholds them rather
                than handing users dead links. A machine that really has a public
                IP and a forwarded port can serve them: set NEXUS_DIRECT_HOST
                and NEXUS_DIRECT_PORT in .env.

  Log in with the ADMIN_PASSWORD printed above (or the one already in .env).

EOF
fi

if [ -z "$TUNNEL_TOKEN" ]; then
cat <<EOF

  NEXUS is up.

    panel   : http://${HOST}:8080/admin
              (the same panel also answers at the root; /admin is the address
               to bookmark, and the one a front proxy should open)
    reality : ${HOST}:8443   (raw TCP — the direct transport)
    MTProto : ${HOST}:8446   (enable it in the panel first)
    web     : ${HOST}:8448 and :8449 (HTTP / SOCKS5, per-user)
    advanced: ${HOST}:10101, :10102, :10104, :10105, :10106
               (XHTTP / gRPC / HTTPUpgrade — open them only for the transports
                you switch on under «مبهم‌سازی پیشرفته»)

  Log in with the ADMIN_PASSWORD printed above.
  Put a domain (ideally behind Cloudflare) in front of 8080 for TLS — the panel
  itself is at /admin on that domain, and behind TLS the port above can be bound
  to loopback ('127.0.0.1:8080:8080') so nothing reaches it directly.
  `docker compose --profile tls up -d` starts a bundled TLS edge for it
  (Caddyfile), or use deploy/nginx.conf.example if nginx already fronts the host.

EOF

if [ "$OPEN_FIREWALL" -eq 1 ]; then
  if command -v ufw >/dev/null 2>&1; then
    for port in "${PORTS_TCP[@]}"; do ufw allow "${port}/tcp" >/dev/null && log "ufw: allowed ${port}/tcp"; done
  else
    warn "ufw is not installed; open the ports in your cloud security group instead"
  fi
else
  echo "  Open these TCP ports in your firewall / security group:"
  echo "    ${PORTS_TCP[*]}"
  echo "  (or re-run with --open-firewall to add the ufw rules now)"
  echo
fi
fi
