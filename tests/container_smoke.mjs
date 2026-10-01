/* Cloudflare Container deployment smoke test — no network, no Docker, no wrangler.

   The deployment where the app runs on Cloudflare itself is spread over four
   files that have to agree with each other, and every way they can disagree is a
   failure a user sees rather than a build error:

     * `wrangler.jsonc` declares the container, the Durable Object binding and the
       class export. A class name that does not match the JS export, or a binding
       name the Worker does not look for, deploys a Worker with no origin at all —
       which answers 503 on every route.
     * The container's port has to be the port the app really binds, which is the
       one the `Dockerfile` hands uvicorn. A container listening somewhere else is
       a Worker that can never reach it.
     * The panel is one database, so `max_instances` has to be 1 and the container
       has to be told the secrets a panel needs (ADMIN_PASSWORD, JWT_SECRET and —
       because a container's disk is ephemeral — DATABASE_URL).

   It reads the files rather than importing them, because the container entry
   imports `cloudflare:workers`, a runtime only workerd provides.

   Run:  node tests/container_smoke.mjs                                    */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel) => readFileSync(join(ROOT, rel), 'utf8');
const failures = [];
const check = (ok, label) => { if (!ok) failures.push(label); };

// ---------------------------------------------------------------- wrangler.jsonc
// Comments are stripped rather than parsed as JSONC so this file needs no
// dependency; the deployment config is the one place a stray comment cannot hide.
const configSource = read('wrangler.jsonc');
let config = null;
try {
  config = JSON.parse(
    configSource
      .replace(/"(?:[^"\\]|\\.)*"|\/\/[^\n]*|\/\*[\s\S]*?\*\//g, (match) => (match.startsWith('"') ? match : '')),
  );
} catch (error) {
  failures.push(`wrangler.jsonc is not valid JSONC: ${error.message}`);
}

const containerSource = read('cloudflare-containers/worker.js');
const workerSource = read('cloudflare-worker/worker.js');
const dockerfile = read('Dockerfile');

if (config) {
  // ------------------------------------------------------- one class, one binding
  const declared = (config.containers || [])[0] || null;
  check(Boolean(declared), 'wrangler.jsonc must declare a container');
  const className = declared && declared.class_name;
  const bindings = (config.durable_objects && config.durable_objects.bindings) || [];
  const binding = bindings.find((item) => item.class_name === className);
  check(Boolean(binding), `no Durable Object binding for ${className}`);

  // The name the Worker looks up has to be the name this config binds, or the
  // Worker resolves no upstream and answers 503 with a perfectly valid deploy.
  check(binding && binding.name === 'NEXUS_CONTAINER',
    'the binding must be named NEXUS_CONTAINER — that is what worker.js looks for');
  check(workerSource.includes('env.NEXUS_CONTAINER'),
    'cloudflare-worker/worker.js must resolve the NEXUS_CONTAINER binding');

  // The exported class is what wrangler instantiates: a class_name that no module
  // exports fails at deploy, and a typo in one of the two is the whole failure.
  check(containerSource.includes(`export class ${className}`),
    `cloudflare-containers/worker.js must export ${className}`);

  // A Durable Object class needs storage, declared once — the current `exports`
  // form or the legacy `migrations` array, never both.
  const usesExports = Boolean(config.exports && config.exports[className]);
  const usesMigrations = Array.isArray(config.migrations);
  check(usesExports || usesMigrations, `${className} has no storage declaration`);
  check(!(usesExports && usesMigrations), 'exports and migrations cannot be combined');
  if (usesExports) {
    check(config.exports[className].storage === 'sqlite',
      `${className} must be declared with sqlite storage`);
  } else {
    check(config.migrations.some((m) => (m.new_sqlite_classes || []).includes(className)),
      `${className} must be registered in a new_sqlite_classes migration`);
  }

  // --------------------------------------------- the name Cloudflare deploys into
  // A connected build deploys into the Worker that owns the Git integration, so a
  // `name` that does not match the dashboard one is survivable — Cloudflare
  // overrides it — but it prints a warning and opens a pull request against this
  // file. The same name is what the guide tells the user to type into the
  // dashboard, so the two have to move together.
  const workerName = config.name || '';
  check(/^[a-z0-9][a-z0-9-]*$/.test(workerName),
    `wrangler.jsonc must declare a valid lowercase Worker name (got "${workerName}")`);
  check(read('docs/CLOUDFLARE-DEPLOY-FA.md').includes('`' + workerName + '`'),
    `docs/CLOUDFLARE-DEPLOY-FA.md must tell the user to name the Worker \`${workerName}\``);

  // --------------------------------------------------------- the plan that exists
  // Containers are not offered on the Free plan, and the failure that produces is
  // the `/containers/me` error at the very end of the deploy — after the image has
  // been built and the Worker uploaded — which reads like a token problem. The
  // guide has to warn about it before the user starts, not in the cost appendix.
  check(/Workers Paid/.test(read('docs/CLOUDFLARE-DEPLOY-FA.md')),
    'docs/CLOUDFLARE-DEPLOY-FA.md must lead with the Workers Paid plan requirement');

  // ------------------------------------------------------ the image really exists
  const image = (declared && declared.image) || '';
  const dockerfilePath = image.replace(/^\.\//, '');
  check(dockerfilePath === 'Dockerfile' && existsSync(join(ROOT, dockerfilePath)),
    `the container image must build from this repository's Dockerfile (got "${image}")`);

  // ------------------------------------------------------------------ one panel
  check(declared && declared.max_instances === 1,
    'the panel is one database, so it must be exactly one container instance');
  check(containerSource.includes('sleepAfter'), 'the container needs an idle timeout');
}

// ------------------------------------------------------------- the port they share
// `defaultPort` is where the Worker sends every request; the Dockerfile is where
// uvicorn really listens. These two numbers are the same number, or nothing works.
const dockerPort = (dockerfile.match(/\$\{PORT:-(\d+)\}/) || [])[1];
const panelPort = (containerSource.match(/const PANEL_PORT = (\d+)/) || [])[1];
check(Boolean(dockerPort), 'the Dockerfile must default the panel port');
check(Boolean(panelPort), 'cloudflare-containers/worker.js must define PANEL_PORT');
check(dockerPort === panelPort,
  `the container port (${panelPort}) must equal the port the Dockerfile binds (${dockerPort})`);
check(containerSource.includes('defaultPort = PANEL_PORT'),
  'PANEL_PORT must be the container defaultPort');
check(/PORT:\s*String\(PANEL_PORT\)/.test(containerSource),
  'the container must be told to listen on the same PORT — the app reads it from the environment');

// ------------------------------------------------------------------ its secrets
// Without ADMIN_PASSWORD the panel boots on the shipped default; without
// DATABASE_URL its database is thrown away with the container.
for (const name of ['ADMIN_PASSWORD', 'JWT_SECRET', 'DATABASE_URL']) {
  check(containerSource.includes(`'${name}'`), `the container must be given ${name}`);
}
check(/NEXUS_PLATFORM:\s*'cloudflare'/.test(containerSource),
  'the container must name its platform — the panel reports where it runs');

// --------------------------------------------------------- one proxy, two shapes
// The container deployment must not fork the proxy: it re-exports the same file
// the paste-into-the-dashboard deployment uses, so the panel, the subscription
// links and all eighteen transport paths stay one code path with one test.
check(/import edge from '\.\.\/cloudflare-worker\/worker\.js'/.test(containerSource),
  'the container entry must import the edge proxy from cloudflare-worker/worker.js');
check(/export default edge/.test(containerSource),
  'the container entry must re-export the edge proxy as the Worker handler');
check(/containerStub\(env\) \? 'container:' \+ CONTAINER_NAME/.test(workerSource),
  'worker.js must report which upstream it resolved');

// ------------------------------------------------------- the dev-script trap
// A `dev` script is what a provider's autoconfig is most likely to pick up as a
// *build* command, and `wrangler dev` never exits: the build then ends on
// «Ready on http://localhost:8787» and the deploy step is never reached, with the
// image already built. So the script has to refuse to be a build step.
{
  const pkg = JSON.parse(read('package.json'));
  const dev = (pkg.scripts && pkg.scripts.dev) || '';
  check(!/^\s*(npx\s+)?wrangler\s+dev\b/.test(dev),
    'the dev script must not be a bare `wrangler dev` — a build command that runs it never finishes');
  check(/dev\.mjs/.test(dev), 'the dev script must run the guard in cloudflare-containers/dev.mjs');
  check(existsSync(join(ROOT, 'cloudflare-containers/dev.mjs')),
    'cloudflare-containers/dev.mjs is missing');
  // The Worker's Build command cannot always be left empty, so the repository has
  // to offer something that is both meaningful and *finite*. This check is the
  // meaningful half: it fails the build before the deploy if the configuration
  // below stops agreeing with itself.
  const build = (pkg.scripts && pkg.scripts.build) || '';
  check(Boolean(build),
    'package.json needs a build script — a Worker build command cannot always be left empty');
  check(!/wrangler\s+dev|dev\.mjs/.test(build),
    'the build script must exit: a build command that starts a server never reaches the deploy step');
  check(/container_smoke\.mjs/.test(build),
    'the build script should run this deployment check, so a broken config fails before the deploy');

  const guard = read('cloudflare-containers/dev.mjs');
  check(/WORKERS_CI/.test(guard) && /process\.exit\(0\)/.test(guard),
    'the guard must exit successfully in a build environment so the deploy still runs');
  check(/spawnSync\(\s*'npx',\s*\[\s*'wrangler',\s*'dev'/.test(guard),
    'the guard must still start wrangler dev locally');
}

if (failures.length) {
  console.error('FAILED');
  failures.forEach((line) => console.error(' -', line));
  process.exit(1);
}
console.log('OK — the Cloudflare container deployment is consistent across wrangler.jsonc, the Dockerfile and both Worker entries');
