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
const mods = ['core', 'ui', 'session', 'api', 'store', 'pwa', 'views/dashboard', 'views/nodes', 'views/users',
  'views/system', 'views/customize', 'views/tools', 'views/advanced'];
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
  [loaded.store.SECTIONS.length === 8, 'SECTIONS'],
  [loaded.store.SECTIONS.map((item) => item.id).join(',') ===
    'dashboard,nodes,users,cloudflare,customize,tools,advanced,settings', 'SECTIONS order'],
  [typeof loaded.pwa.PwaManager === 'function', 'PwaManager'],
  [typeof loaded['views/dashboard'].DashboardView === 'function', 'DashboardView'],
  [typeof loaded['views/nodes'].NodesView === 'function', 'NodesView'],
  [typeof loaded['views/nodes'].NodesView.prototype.samplesModal === 'function', 'node samples modal'],
  [typeof loaded['views/nodes'].NodesView.prototype.addSamples === 'function', 'node samples add'],
  [typeof loaded['views/users'].UsersView === 'function', 'UsersView'],
  [typeof loaded['views/system'].CloudflareView === 'function', 'CloudflareView'],
  [typeof loaded['views/system'].SettingsView === 'function', 'SettingsView'],
  [typeof loaded['views/customize'].CustomizeView === 'function', 'CustomizeView'],
  [typeof loaded['views/customize'].CustomizeView.prototype.load === 'function', 'customization loader'],
  [typeof loaded['views/tools'].ToolsView === 'function', 'ToolsView'],
  [typeof loaded['views/tools'].ToolsView.prototype.scan === 'function', 'CDN scanner action'],
  [typeof loaded['views/advanced'].AdvancedView === 'function', 'AdvancedView'],
  [typeof loaded['views/advanced'].AdvancedView.prototype.saveHysteria === 'function', 'hysteria2 form'],
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

// The protocol multi-select must build a chip per catalog entry with no DOM, and
// default to every protocol on (so all paths are live for a new user).
try {
  const catalog = {
    protocols: {
      protocols: [{ id: 'vless', label: 'VLESS' }, { id: 'vmess', label: 'VMess' }, { id: 'ss', label: 'Shadowsocks' }],
      shadowsocks: [{ id: 'ss', method: '2022-blake3-aes-128-gcm' }, { id: 'ss-chacha', method: '2022-blake3-chacha20-poly1305' }],
    },
  };
  window.nexus.store.set('settings', catalog);
  const every = window.nexus.users.protocolChips({});
  const narrowed = window.nexus.users.protocolChips({ protocols: ['vless'], protocol_value: 'vless' });
  if ((every.html.match(/class="pick on"/g) || []).length !== 3) failures.push('protocol chips must default to all-on');
  if (!every.html.includes('data-proto="ss"') || !every.html.includes('chacha20-poly1305')) failures.push('protocol chips missing an SS cipher');
  if ((narrowed.html.match(/class="pick on"/g) || []).length !== 1) failures.push('protocol chips must honour a narrowed set');
  if (every.html.includes('undefined')) failures.push('protocol chips rendered undefined');

  // The catalog only arrives with /api/settings, which the Settings section used
  // to be the only one to fetch: opening the user form first left the select
  // empty. The form must never render that empty state any more.
  window.nexus.store.set('settings', null);
  const offline = window.nexus.users.protocolChips({});
  if (offline.html.includes('کاتالوگ پروتکل')) failures.push('protocol chips must never render the empty-catalog message');
  if ((offline.html.match(/class="pick on"/g) || []).length !== 4) failures.push('protocol chips must fall back to the built-in protocol list');
  if (!offline.html.includes('data-proto="ss"')) failures.push('protocol chips fallback is missing Shadowsocks');

  // ensureSettings() reuses a cached catalog (no request) and never rejects when
  // the fetch fails, and openForm() always waits for it before opening.
  window.nexus.store.set('settings', catalog);
  const cached = await window.nexus.ensureSettings();
  if (cached !== catalog) failures.push('ensureSettings must reuse the cached catalog');
  window.nexus.store.set('settings', null);
  const view = window.nexus.users;
  const originalModal = view.modal;
  let handed = 'never-called';
  view.modal = (user) => { handed = user; };
  await view.openForm({ username: 'probe' }).catch((error) => failures.push(`openForm threw: ${error.message}`));
  view.modal = originalModal;
  if (!handed || handed.username !== 'probe') failures.push('openForm must open the form with the same user');
  if (window.nexus.store.get('settings') !== null) failures.push('a failed catalog fetch must not cache an empty settings payload');
  window.nexus.store.set('settings', catalog);
} catch (error) {
  failures.push(`protocol chips threw: ${error.message}`);
}

// The per-client sublink list groups by engine family: a Clash client (which
// imports YAML only) must never sit next to a Base64 link, and every family is a
// collapsed dropdown with its alternate formats hidden behind another one.
try {
  const rows = window.nexus.users.clientLinkRows([
    { id: 'bettbox', name: 'Bettbox', platform: 'Android', format: 'clash', format_label: 'Clash / Mihomo',
      family: 'mihomo', family_label: 'خانواده Clash / Mihomo — فقط YAML', family_hint: 'فقط YAML', family_order: 3,
      url: 'https://panel.example.com/sub/1?target=bettbox', download: '', alternatives: [] },
    { id: 'v2rayng', name: 'v2rayNG', platform: 'Android', format: 'base64', format_label: 'Base64 (V2Ray)',
      family: 'xray', family_label: 'خانواده Xray — Base64 / متن ساده', family_hint: '', family_order: 1,
      url: 'https://panel.example.com/sub/1?target=v2rayng', download: '',
      alternatives: [{ target: 'all', label: 'همه ترکیب‌ها', url: 'https://panel.example.com/sub/1?target=all' }] },
  ]);
  if ((rows.match(/class="sub-group"/g) || []).length !== 2) failures.push('client links must be grouped per engine family');
  if (!rows.includes('خانواده Xray') || !rows.includes('خانواده Clash')) failures.push('client groups must carry their family label');
  if (!rows.includes('سایر فرمت')) failures.push('alternate formats must stay in a collapsed dropdown');
  if (rows.includes('undefined')) failures.push('client groups rendered undefined');
  if (!/class="sub-group"[\s\S]*class="sub-group"/.test(rows) || rows.indexOf('خانواده Xray') > rows.indexOf('خانواده Clash')) {
    failures.push('client families must keep their configured order');
  }
  if (window.nexus.users.clientLinkRows([]) === '' || !window.nexus.users.clientLinkRows([]).includes('empty')) {
    failures.push('client groups must render an empty state');
  }
} catch (error) {
  failures.push(`client groups threw: ${error.message}`);
}

// The edge/locations card renders the runtime info, every configured location and
// the provider summary — it must work with data and when nothing is configured.
try {
  window.nexus.store.set('edge', null);
  window.nexus.cloudflare.fillProviders();
  window.nexus.cloudflare.renderEdge();
  window.nexus.store.set('edge', {
    runtime: { id: 'docker', label: 'VPS / Docker', host: 'nexus.example.com', has_tcp: true,
      direct: { host: '203.0.113.5', port: 8443 }, data_dir: '/data', notes: ['یادداشت'] },
    sources: [{ id: 'de-cf', location: 'de', label: 'آلمان · کلودفلر', kind: 'ip', provider: 'cloudflare',
      host: 'cdn.example.com', port: 443, enabled: 1 },
      { id: 'nl-vps', location: 'nl', label: 'هلند · دامنه تمیز', kind: 'domain', provider: 'domain',
        host: 'nl.example.com', port: 443, enabled: 0 }],
    providers: [{ id: 'cloudflare', label: 'Cloudflare', total: 8, healthy: 3, scannable: true },
      { id: 'custom', label: 'آی‌پی دستی', total: 1, healthy: 0, scannable: false }],
    nodes: [{ name: 'de-cf-01', location: 'de', provider: 'cloudflare', enabled: 1 }],
    locations: ['de', 'nl'],
  });
  window.nexus.cloudflare.renderEdge();
  try { await window.nexus.cloudflare.loadEdge(); } catch (error) { /* offline smoke test */ }
} catch (error) {
  failures.push(`edge card threw: ${error.message}`);
}

await new Promise((resolve) => setTimeout(resolve, 50));

if (failures.length) {
  console.error('FAILED'); failures.forEach((line) => console.error(' -', line));
  process.exit(1);
}
console.log(`OK — ${checks.length} checks passed, ${mods.length + 1} modules linked, render paths clean`);
// The shell starts a 1s clock interval, so the process is ended explicitly.
process.exit(0);
