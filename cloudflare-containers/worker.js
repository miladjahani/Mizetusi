/**
 * NEXUS on Cloudflare — the Worker that *is* the deployment.
 *
 * Why this file exists
 * --------------------
 * A Worker runs JavaScript/WASM only: no Python, no processes, no Xray — so the
 * panel can never execute inside one. What Cloudflare does offer is a **Container**:
 * a real Linux VM, booted from this repository's own `Dockerfile`, sitting behind
 * this Worker. That is the difference between the two Cloudflare deployments:
 *
 *   `cloudflare-worker/worker.js` alone   the app lives somewhere else (Railway, a
 *                                         VPS) and the Worker is a clean address
 *                                         in front of it. NEXUS_ORIGIN is a URL.
 *   this file + a `containers` block      the app itself runs on Cloudflare, in a
 *                                         container this Worker reaches through a
 *                                         binding. Nothing else is needed: no
 *                                         Railway project, no VPS, no origin URL.
 *
 * The proxy itself is not reimplemented here — `cloudflare-worker/worker.js` is
 * re-exported unchanged, and it resolves its upstream once: a bound
 * `NEXUS_CONTAINER` wins, otherwise `NEXUS_ORIGIN`. So the panel, the subscription
 * and portal links, the panel's own sockets and all eighteen published transport
 * paths go through exactly one code path, covered by one test suite
 * (`tests/worker_smoke.mjs` drives both shapes).
 *
 * One instance on purpose
 * -----------------------
 * A panel is stateful: users, quotas, subscription tokens and settings live in one
 * database. `max_instances: 1` in `wrangler.jsonc` and one fixed name here
 * (`nexus-panel`) mean every request lands on the same container, instead of a
 * fresh one per path — a container per path would hand a user a subscription that
 * the next request cannot find.
 *
 * The database is the one thing a container cannot keep
 * ----------------------------------------------------
 * A container's disk is ephemeral: it is thrown away when the instance sleeps and
 * on every deploy. So the shipped default (`SQLITE_PATH`, one file) is a *demo*
 * database here, and the panel's data only survives if `DATABASE_URL` points at a
 * Postgres. `docs/CLOUDFLARE-DEPLOY-FA.md` walks through that, and the panel's
 * own «ذخیرهسازی» card shows which one it is really using.
 */
import { Container } from '@cloudflare/containers';
import edge from '../cloudflare-worker/worker.js';

// The port the panel listens on inside the container. `uvicorn` binds
// `${NEXUS_HTTP_PORT:-${PORT:-8080}}` (see the Dockerfile), so PORT is pinned to
// the same number here — a container whose app listens somewhere else is a
// container the Worker can never reach.
const PANEL_PORT = 8080;

// Environment the panel reads. Everything else the container gets is set below.
const PASSTHROUGH = [
  'ADMIN_PASSWORD',   // without it the panel boots on the shipped default
  'JWT_SECRET',       // session signing; rotate to log every device out
  'DATABASE_URL',     // Postgres — the only storage that survives a restart
  'PUBLIC_BASE_URL',  // optional: the Worker's custom domain (see the guide)
  'NEXUS_PUBLIC_DOMAIN',
  'SESSION_TTL',
  'LOG_LEVEL',
];

function containerEnv(env) {
  const out = {
    ENVIRONMENT: 'production',
    // Named rather than detected: the panel reports where it runs, and this is
    // the one place that knows it is a container behind a Worker.
    NEXUS_PLATFORM: 'cloudflare',
    XRAY_ENABLED: 'true',
    PORT: String(PANEL_PORT),
    // The container's own disk, and it is thrown away with the instance — see the
    // module comment. Only used when DATABASE_URL is empty.
    SQLITE_PATH: '/tmp/nexus.db',
    NEXUS_DATA_DIR: '/tmp',
  };
  for (const key of PASSTHROUGH) {
    const value = env && env[key];
    if (typeof value === 'string' && value.trim()) out[key] = value;
  }
  return out;
}

export class NexusContainer extends Container {
  defaultPort = PANEL_PORT;
  requiredPorts = [PANEL_PORT];
  // Idle for an hour and the container stops. A live tunnel renews this on every
  // message, so a session is never cut off mid-transfer; a quiet deployment stops
  // paying for an idle VM.
  sleepAfter = '1h';
  // Xray dials the internet from inside the container, so egress has to stay on.
  enableInternet = true;

  constructor(ctx, env) {
    super(ctx, env);
    this.envVars = containerEnv(env || {});
  }

  onStart() {
    console.log('NEXUS panel container started');
  }

  onStop() {
    console.log('NEXUS panel container stopped');
  }

  onError(error) {
    console.error('NEXUS panel container error:', error && error.message ? error.message : error);
    throw error;
  }
}

// The panel proxy, exactly as the paste-into-the-dashboard deployment uses it.
export default edge;
