#!/usr/bin/env bash
# NEXUS on Cloudflare — one command.
#
# This is the deployment where the app lives on Cloudflare itself: `wrangler
# deploy` builds this repository's Dockerfile (Xray, sing-box, mihomo, mtg,
# mtproto-proxy and the FastAPI panel), pushes it to Cloudflare's registry, starts
# it as a Container, and puts the Worker in front of it as the only public
# address. Nothing else is needed — no Railway project, no VPS.
#
# The script does the four things that otherwise have to be done by hand:
#
#   1. check the two tools this needs (Docker, because the image is built locally,
#      and Node/npm for wrangler);
#   2. install the Worker's dependencies and make sure you are logged in;
#   3. set the panel's secrets — a generated ADMIN_PASSWORD and JWT_SECRET, and the
#      DATABASE_URL you pass — without ever rotating a password that is already
#      live (a secret is write-only, so an existing one is left alone);
#   4. deploy, then print the two steps that are done in a browser.
#
# Usage (from the repository root):
#
#   sh cloudflare-containers/deploy.sh --db 'postgresql://…'
#   sh cloudflare-containers/deploy.sh --domain panel.example.com --db 'postgresql://…'
#   sh cloudflare-containers/deploy.sh --secrets-only      # set secrets, do not deploy
#   sh cloudflare-containers/deploy.sh --silent            # non-interactive (CI)
#
# Flags:
#   --db <url>        Postgres URL for DATABASE_URL (strongly recommended: a
#                     container's own disk is thrown away when it sleeps)
#   --domain <host>   the Worker's custom domain, written into PUBLIC_BASE_URL
#   --secrets-only    write the secrets and stop (no Docker build)
#   --silent          do not print prompts (for a scripted run)
set -euo pipefail

DB_URL=""
DOMAIN=""
SECRETS_ONLY=0
SILENT=0

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m%s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx \033[0m%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --db) DB_URL="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --secrets-only) SECRETS_ONLY=1; shift ;;
    --silent) SILENT=1; shift ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) die "unknown flag: $1 (try --help)" ;;
  esac
done

# Run from the repository root wherever the script was invoked from, because
# wrangler.jsonc, the Dockerfile and `main` are all resolved from there.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f wrangler.jsonc ] || die "wrangler.jsonc is missing — run this from a NEXUS checkout"

# ------------------------------------------------------------------ 1. tools
command -v npm >/dev/null 2>&1 || die "npm is not installed (wrangler needs Node 18+)"
if [ "$SECRETS_ONLY" -eq 0 ]; then
  command -v docker >/dev/null 2>&1 || die "Docker is not installed — it is what builds the image"
  docker info >/dev/null 2>&1 || die "the Docker daemon is not running (start Docker Desktop, or colima)"
fi

# ------------------------------------------------------------------ 2. wrangler
if [ ! -d node_modules ]; then
  log "installing the Worker's dependencies"
  npm install --no-fund --no-audit
fi
if ! npx --no-install wrangler whoami >/dev/null 2>&1; then
  log "logging in to Cloudflare"
  npx wrangler login
fi
npx wrangler whoami >/dev/null 2>&1 || die "not logged in — run: npx wrangler login"

# ------------------------------------------------------------------ 3. secrets
# A secret can be written and never read back, so the only safe rule is: set it
# once, and never touch a value that is already there.
existing_secrets() {
  npx wrangler secret list 2>/dev/null | grep -o '"[A-Za-z_][A-Za-z0-9_]*"' | tr -d '"' || true
}

random_secret() {
  # No early-closing pipe here: `head -c` in the middle would make this fail
  # under `set -o pipefail` about half the time, which is exactly the kind of
  # bug that only shows up on someone else's machine.
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

EXISTING="$(existing_secrets)"

put_secret() {
  name="$1"
  value="$2"
  if printf '%s\n' "$EXISTING" | grep -qx "$name"; then
    log "secret $name already set — left untouched"
    return 0
  fi
  printf '%s' "$value" | npx wrangler secret put "$name" >/dev/null
  log "secret $name set"
}

ADMIN_PASS="$(random_secret 16)"
JWT="$(random_secret 48)"

if [ -n "$DB_URL" ]; then
  put_secret DATABASE_URL "$DB_URL"
elif printf '%s\n' "$EXISTING" | grep -qx 'DATABASE_URL'; then
  log "DATABASE_URL already set — left untouched"
else
  warn "no --db given: the panel will use its local SQLite file, and a container's"
  warn "disk is wiped every time it sleeps or redeploys. Pass --db 'postgresql://…'"
  warn "once you have a Postgres (see docs/CLOUDFLARE-DEPLOY-FA.md)."
fi
put_secret ADMIN_PASSWORD "$ADMIN_PASS"
put_secret JWT_SECRET "$JWT"

cat <<EOF

  If any of those secrets were NEW, this is the password to log in with:

      ADMIN_PASSWORD = ${ADMIN_PASS}

EOF
if [ "$SILENT" -eq 0 ]; then
  printf '  (write it down now — it is never shown again)\n\n'
fi

if [ "$SECRETS_ONLY" -eq 1 ]; then
  log "secrets written; stopping before the deploy (--secrets-only)"
  exit 0
fi

# The address clients really use — the Worker's custom domain, which is what the
# panel builds the origin node and every subscription link from. It is not a
# secret, but a Worker secret is an ordinary env binding here and this reuses the
# one path that never overwrites a value that is already set.
if [ -n "$DOMAIN" ]; then
  put_secret PUBLIC_BASE_URL "https://${DOMAIN}"
fi

# ------------------------------------------------------------------ 4. deploy
log "building the image and deploying (the first build takes a few minutes)"
npx wrangler deploy

cat <<EOF

  Deployed. Two steps are left, and both are in a browser:

    1. Workers & Pages → this Worker → Settings → Domains & Routes →
       Add custom domain → e.g. ${DOMAIN:-panel.example.com}
       This is not cosmetic: the default *.workers.dev hostname is filtered in
       Iran, so a Worker left on it only opens through a VPN.

    2. Open that custom domain, log in with the ADMIN_PASSWORD above, and in
       «کلودفلر» save the same custom-domain URL as the Worker address, then
       press «پینگ همه نودها» so every subscription link is built on it.

  Billed by Cloudflare as Containers + Workers (Workers Paid plan required).

  From now on this deployment can keep itself up to date: connect the repository to
  this Worker (Workers & Pages → the Worker → Settings → Builds → Connect) and every
  push to main builds the image and rolls the container out — no token, no secret.
  See docs/CLOUDFLARE-DEPLOY-FA.md, section 0.

EOF
