/* `npm run dev` — the local development server, with one guard.
 *
 * `wrangler dev` starts a Worker (and its container) on localhost and then serves
 * it forever. That is exactly right on a laptop and exactly wrong in a build
 * environment, where the first "build" step is whatever command a person — or the
 * provider's autoconfig — put in the Build command field.
 *
 * Cloudflare's Workers Builds reads a project's package.json, and a `dev` script
 * is the one thing it is most likely to pick up by mistake: the build then runs
 * `bun run dev`, the log ends on
 *
 *     Ready on http://localhost:8787
 *
 * and the build never finishes. Everything before it succeeded — the container
 * image was built and pushed — but the deploy step is never reached, so the
 * deployment quietly is not there.
 *
 * So in a CI/build environment (`WORKERS_CI=1` is injected by Workers Builds,
 * `CI=true` by everything else) this script explains the situation and exits
 * successfully, letting the deploy command run. Correcting the Build command to
 * be empty is still the right fix — see docs/CLOUDFLARE-DEPLOY-FA.md, section 0 —
 * but a wrong field must never be able to swallow a deployment.
 *
 * Locally nothing changes: it hands over to `wrangler dev` with the same output.
 */
import { spawnSync } from 'node:child_process';

const inBuild = process.env.WORKERS_CI === '1' || Boolean(process.env.WORKERS_CI) || Boolean(process.env.CI);

if (inBuild) {
  console.error(
    '`npm run dev` is a long-running local server and is not a build step.\n'
    + 'This looks like a build environment (CI/WORKERS_CI is set), so it is being skipped\n'
    + 'instead of holding the build open forever — the deploy command runs right after it.\n'
    + 'Fix it properly by clearing the Build command in the Worker\'s Settings → Builds\n'
    + '(docs/CLOUDFLARE-DEPLOY-FA.md, section 0).',
  );
  process.exit(0);
}

const result = spawnSync('npx', ['wrangler', 'dev', ...process.argv.slice(2)], { stdio: 'inherit' });
process.exit(typeof result.status === 'number' ? result.status : 1);
