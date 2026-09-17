/* Headless smoke test: import the whole ES-module graph with a tiny DOM stub.
   Node links the modules for real, so a missing/misnamed export fails here
   instead of in the browser. Run:  node tests/js_smoke.mjs            */
const noop = () => {};
const fakeEl = () => ({
  style: {}, dataset: {}, classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
  children: [], firstElementChild: null, innerHTML: '', textContent: '', value: '',
  appendChild: noop, remove: noop, setAttribute: noop, addEventListener: noop,
  querySelector: () => null, querySelectorAll: () => [], closest: () => null, focus: noop,
});

globalThis.document = {
  readyState: 'complete',
  title: '',
  documentElement: { style: { setProperty: noop } },
  body: fakeEl(),
  querySelector: () => null,
  querySelectorAll: () => [],
  getElementById: () => null,
  createElement: fakeEl,
  addEventListener: noop,
  removeEventListener: noop,
  hidden: false,
};
globalThis.window = {
  location: { href: 'http://127.0.0.1/', protocol: 'http:', hash: '', search: '', pathname: '/', origin: 'http://127.0.0.1' },
  navigator: { userAgent: 'node-smoke', standalone: false },
  matchMedia: () => ({ matches: false }),
  addEventListener: noop,
  removeEventListener: noop,
  scrollTo: noop,
  isSecureContext: false,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  performance,
  fetch: () => Promise.reject(new Error('offline smoke test')),
  history: { replaceState: noop },
};
// location/navigator are getter-only globals in Node 22, so they are defined
// (not assigned) and only where the runtime has not already provided them.
const defineGlobal = (name, value) => {
  if (globalThis[name] === undefined) Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
};
defineGlobal('location', window.location);
defineGlobal('navigator', window.navigator);
defineGlobal('fetch', window.fetch);
defineGlobal('requestAnimationFrame', window.requestAnimationFrame);
if (globalThis.location !== window.location) globalThis.window.location = globalThis.location;

const failures = [];
process.on('unhandledRejection', (error) => failures.push(`unhandled: ${error.message}`));

// The panel modules are imported straight from the repository (Node detects the
// ES-module syntax in .js files), so this needs no build step and no copy.
const BASE = new URL('../static/js/', import.meta.url);
const mods = ['core', 'ui', 'session', 'api', 'store', 'pwa', 'views/dashboard', 'views/nodes', 'views/users', 'views/system'];
const loaded = {};
for (const name of mods) {
  loaded[name] = await import(new URL(`${name}.js`, BASE));
}
await import(new URL('app.js', BASE));

const checks = [
  [typeof loaded.core.Fmt.num(1234) === 'string', 'Fmt.num'],
  [loaded.core.Fmt.sizeText(1.5).includes('GB'), 'Fmt.sizeText'],
  [loaded.core.Fmt.until(90).length > 3, 'Fmt.until'],
  [loaded.ui.StatusKit.of({ is_active: 1 }) === loaded.ui.STATUS.active, 'StatusKit.of'],
  [loaded.ui.StatusKit.latencyText(-1) === 'قطع', 'latencyText failed node'],
  [loaded.ui.Charts.bars !== undefined && loaded.ui.Charts.area !== undefined, 'Charts API'],
  [typeof loaded.session.SessionManager === 'function', 'SessionManager'],
  [typeof loaded.api.ApiClient === 'function', 'ApiClient'],
  [loaded.store.SECTIONS.length === 5, 'SECTIONS'],
  [typeof loaded.pwa.PwaManager === 'function', 'PwaManager'],
  [typeof loaded['views/dashboard'].DashboardView === 'function', 'DashboardView'],
  [typeof loaded['views/nodes'].NodesView === 'function', 'NodesView'],
  [typeof loaded['views/users'].UsersView === 'function', 'UsersView'],
  [typeof loaded['views/system'].CloudflareView === 'function', 'CloudflareView'],
  [typeof loaded['views/system'].SettingsView === 'function', 'SettingsView'],
  [!!window.nexus, 'app bootstrapped'],
  [window.nexus?.store?.get('section') === 'dashboard', 'router default section'],
  [typeof window.nexus?.handleSessionLost === 'function', 'session recovery hook'],
];
for (const [ok, label] of checks) {
  if (!ok) failures.push(`check failed: ${label}`);
}

// Exercise the paths that used to crash: a clock tick with no DOM, an error
// report, an expired session and a view render with empty data.
try {
  window.nexus.tickClock();
  window.nexus.report(new Error('smoke'));
  window.nexus.dashboard.render();
  window.nexus.nodes.render();
  window.nexus.nodes.renderCoverage();
  window.nexus.users.render();
  window.nexus.settings.render();
  window.nexus.cloudflare.render();
  window.nexus.updateBadges();
  window.nexus.setLive(true);
  window.nexus.applyBrand();
} catch (error) {
  failures.push(`render path threw: ${error.message}`);
}

await new Promise((resolve) => setTimeout(resolve, 50));

if (failures.length) {
  console.error('FAILED'); failures.forEach((line) => console.error(' -', line));
  process.exit(1);
}
console.log(`OK — ${checks.length} checks passed, ${mods.length + 1} modules linked, render paths clean`);
// The shell starts a 1s clock interval, so the process is ended explicitly.
process.exit(0);
