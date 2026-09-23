/* Headless smoke test: import the whole ES-module graph with a tiny DOM stub.
   Node links the modules for real, so a missing/misnamed export fails here
   instead of in the browser. Run:  node tests/js_smoke.mjs            */
import { readFileSync } from 'node:fs';

const noop = () => {};
const fakeEl = () => ({
  style: {}, dataset: {}, classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
  children: [], firstElementChild: null, innerHTML: '', textContent: '', value: '',
  appendChild: noop, remove: noop, setAttribute: noop, addEventListener: noop,
  querySelector: () => null, querySelectorAll: () => [], closest: () => null, focus: noop,
});

/* The render paths look up ids the real template defines. Without them every
   `$('#x')` returns null, a view returns early and a crash inside it is never
   seen — which is how an unguarded `data.nodes.map` shipped. Ids that exist in
   templates/index.html resolve to a stub element, so a render really runs. */
const templateIds = new Set([...readFileSync(new URL('../templates/index.html', import.meta.url), 'utf8')
  .matchAll(/id="([^"]+)"/g)].map((match) => match[1]));
const elements = new Map();
const elementFor = (id) => {
  if (!elements.has(id)) elements.set(id, fakeEl());
  return elements.get(id);
};
const byIdSelector = (selector) => (typeof selector === 'string' && /^#[^ .>#[]+$/.test(selector)
  && templateIds.has(selector.slice(1)) ? elementFor(selector.slice(1)) : null);

globalThis.document = {
  readyState: 'complete',
  title: '',
  documentElement: { style: { setProperty: noop } },
  body: fakeEl(),
  activeElement: null,
  querySelector: byIdSelector,
  querySelectorAll: () => [],
  getElementById: (id) => (templateIds.has(id) ? elementFor(id) : null),
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
  'views/system', 'views/customize', 'views/tools', 'views/telegram', 'views/advanced', 'views/guide'];
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
  [loaded.store.SECTIONS.length === 9, 'SECTIONS'],
  [loaded.store.SECTIONS.map((item) => item.id).join(',') ===
    'dashboard,users,nodes,cloudflare,tools,telegram,customize,advanced,settings', 'SECTIONS order'],
  // The sidebar is five collapsible groups, and every section belongs to one of
  // them — a section left out of a group would be unreachable in the UI.
  [loaded.store.NAV_GROUPS.length === 5, 'nav groups'],
  [loaded.store.SECTIONS.every((item) => loaded.store.NAV_GROUPS.some((group) => group.items.includes(item.id))),
    'every section belongs to a nav group'],
  [loaded.store.NAV_GROUPS.flatMap((group) => group.items).length === loaded.store.SECTIONS.length,
    'nav groups must list every section exactly once'],
  [loaded.store.groupOf('cloudflare').id === 'network' && loaded.store.groupOf('nope').id === 'overview',
    'groupOf resolves a section to its group'],
  [typeof loaded.pwa.PwaManager === 'function', 'PwaManager'],
  [typeof loaded['views/dashboard'].DashboardView === 'function', 'DashboardView'],
  [typeof loaded['views/nodes'].NodesView === 'function', 'NodesView'],
  [typeof loaded['views/nodes'].NodesView.prototype.samplesModal === 'function', 'node samples modal'],
  [typeof loaded['views/nodes'].NodesView.prototype.addSamples === 'function', 'node samples add'],
  [typeof loaded['views/users'].UsersView === 'function', 'UsersView'],
  [typeof loaded['views/system'].CloudflareView === 'function', 'CloudflareView'],
  [typeof loaded['views/system'].CloudflareView.prototype.pingCell === 'function', 'location ping verdict'],
  [typeof loaded['views/system'].CloudflareView.prototype.geoCell === 'function', 'measured-country column'],
  [typeof loaded['views/system'].CloudflareView.prototype.detectGeo === 'function', 'country detection action'],
  [typeof loaded['views/nodes'].NodesView.prototype.geoBadge === 'function', 'node country badge'],
  [typeof loaded['views/system'].SettingsView === 'function', 'SettingsView'],
  [typeof loaded['views/customize'].CustomizeView === 'function', 'CustomizeView'],
  [typeof loaded['views/customize'].CustomizeView.prototype.load === 'function', 'customization loader'],
  [typeof loaded['views/tools'].ToolsView === 'function', 'ToolsView'],
  [typeof loaded['views/tools'].ToolsView.prototype.scan === 'function', 'CDN scanner action'],
  [typeof loaded['views/tools'].ToolsView.prototype.renderClientIp === 'function', 'real client-ip card'],
  [typeof loaded['views/tools'].ToolsView.prototype.saveClientIp === 'function', 'trusted-proxy save'],
  [typeof loaded['views/telegram'].TelegramView === 'function', 'TelegramView'],
  [typeof loaded['views/telegram'].TelegramView.prototype.load === 'function', 'telegram loader'],
  [typeof loaded['views/telegram'].TelegramView.prototype.rotate === 'function', 'mtproto secret rotation'],
  [typeof loaded['views/telegram'].TelegramView.prototype.probe === 'function', 'telegram web probe'],
  [typeof loaded['views/telegram'].TelegramView.prototype.payload === 'function', 'telegram form payload'],
  [typeof loaded['views/advanced'].AdvancedView === 'function', 'AdvancedView'],
  [typeof loaded['views/advanced'].AdvancedView.prototype.saveHysteria === 'function', 'hysteria2 form'],
  [typeof loaded['views/guide'].GuideView === 'function', 'GuideView'],
  [typeof loaded['views/guide'].GuideView.prototype.load === 'function', 'guide loader'],
  [typeof loaded['views/guide'].GuideView.prototype.toggle === 'function', 'guide drawer'],
  [typeof loaded.ui.accordion === 'function', 'accordion helper'],
  [!!window.nexus, 'app bootstrapped'],
  [window.nexus?.store?.get('section') === 'dashboard', 'router default section'],
  // The grouped navigation must really paint. It reads NAV_GROUPS off the router;
  // reading it off the store threw «Cannot read properties of undefined
  // (reading 'map')» during boot and left the sidebar — and the phone bottom
  // bar — empty on every load.
  [(elements.get('navGroups')?.innerHTML.match(/class="nav-group[ "]/g) || []).length === 5,
    'the grouped navigation must render five groups'],
  [(elements.get('navGroups')?.innerHTML.match(/data-section=/g) || []).length === 9,
    'the grouped navigation must offer every section'],
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

// The country a location really is: a Cloudflare range used to be labelled from
// the range it came from, so a client showed a Canadian flag on an American
// address. The panel must show the measured country next to the label, flag the
// disagreement and offer to align it — never hide it.
try {
  const view = window.nexus.cloudflare;
  window.nexus.store.set('edge', {
    sources: [
      { id: 'us', location: 'us', label: '🇺🇸 · آمریکا', kind: 'ip', provider: 'cloudflare',
        host: 'worker.example.workers.dev', port: 443, enabled: 1, addresses: 2, nodes: 2,
        healthy: 2, failed: 0, pending: 0, fastest_ms: 40, reason: '',
        geo: { country: 'us', counts: { us: 2 }, measured: 2, total: 2, agree: true, flag: '🇺🇸', name: 'آمریکا' } },
      { id: 'ca', location: 'ca', label: '🇨🇦 · کانادا', kind: 'ip', provider: 'cloudflare',
        host: 'worker.example.workers.dev', port: 443, enabled: 1, addresses: 2, nodes: 2,
        healthy: 2, failed: 0, pending: 0, fastest_ms: 55, reason: '',
        geo: { country: 'us', counts: { us: 2 }, measured: 2, total: 2, agree: true, flag: '🇺🇸', name: 'آمریکا' } },
      { id: 'unmeasured', location: 'nl', label: '🇳🇱 · هلند', kind: 'ip', provider: 'cloudflare',
        host: 'worker.example.workers.dev', port: 443, enabled: 1, addresses: 1, nodes: 1,
        healthy: 1, failed: 0, pending: 0, fastest_ms: 60, reason: '',
        geo: { country: '', counts: {}, measured: 0, total: 1, agree: false, flag: '', name: '' } },
      { id: 'split', location: 'au', label: '🇦🇺 · اقیانوسیه', kind: 'ip', provider: 'cloudflare',
        host: 'worker.example.workers.dev', port: 443, enabled: 1, addresses: 2, nodes: 2,
        healthy: 2, failed: 0, pending: 0, fastest_ms: 65, reason: '',
        geo: { country: '', counts: { au: 1, gt: 1 }, measured: 2, total: 2, agree: false, flag: '', name: '' } },
      { id: 'tunnel', location: 'de', label: '🇩🇪 · تانل آلمان', kind: 'domain', provider: 'domain',
        host: 'cdn16.qemitra.ir', port: 30524, enabled: 1, addresses: 1, nodes: 1,
        healthy: 1, failed: 0, pending: 0, fastest_ms: 70, reason: '',
        geo: { country: '', counts: {}, measured: 0, total: 0, agree: false, flag: '', name: '' } },
    ],
    nodes: [{ name: 'us-cloudflare-01', enabled: 1, source_id: 'us' }],
    locations: ['us', 'ca', 'nl', 'de'],
    geo: { enabled: true, known: 4, answered: 4, countries: ['us'], updated_at: 0, setting_on: true },
    runtime: {}, providers: [], probing: {},
  });
  const host = elementFor('edgeSources');
  host.innerHTML = '';
  view.renderEdge();
  const html = host.innerHTML;
  if (!html.includes('🇺🇸')) failures.push('the measured country must be shown in the locations table');
  if (!html.includes('اندازه‌گیری نشده')) failures.push('an unmeasured location must say so instead of guessing');
  // A range whose addresses really are in different countries has no country:
  // the label stays and the split is shown instead of a flag half of them would
  // contradict.
  if (!html.includes('بدون اکثریت') || !html.includes('AU×')) {
    failures.push('a split location must show «بدون اکثریت» with the countries it measured');
  }
  // Only the location whose label the data contradicts gets the align action.
  if ((html.match(/data-edge="align"/g) || []).length !== 1) failures.push('exactly the mismatched location must offer «هم‌تراز»');
  if (!html.includes('data-country="us"')) failures.push('the align action must carry the measured country');
  if (html.includes('undefined')) failures.push('the locations table rendered undefined');
  // And the tidy case (measured == label) offers no action at all.
  if ((html.match(/data-id="us"/g) || []).length !== 3) failures.push('a matching location keeps only ping/toggle/delete');
} catch (error) {
  failures.push(`the measured-country column threw: ${error.message}`);
}

// «شخصی‌سازی» carries the switch that decides whether a location's country is
// measured from its addresses or kept as typed.
try {
  const view = window.nexus.customize;
  window.nexus.store.set('customization', {
    portal_banner: '', support_url: 'https://t.me/miliconfig', app_name: 'NEXUS', accent: '#c9f24c',
    accent_secondary: '#5fce62', flags: true, geo_lookup: true, default_format: 'auto',
    default_max_configs: '', default_scope: 'all', scopes: [], core_formats: [],
  });
  view.render();
  if (!String(elements.get('czTag').innerHTML).includes('اندازه‌گیری‌شده')) {
    failures.push('the customization tag must name the source of the country labels');
  }
  if (!String(elements.get('czPreview').innerHTML).includes('اندازه‌گیری از آی‌پی')) {
    failures.push('the customization preview must show where the country labels come from');
  }
  window.nexus.store.set('customization', {
    portal_banner: '', support_url: '', app_name: 'NEXUS', accent: '', accent_secondary: '',
    flags: true, geo_lookup: false, default_format: 'auto', default_max_configs: '',
    default_scope: 'all', scopes: [], core_formats: [],
  });
  view.render();
  if (!String(elements.get('czTag').innerHTML).includes('برچسب دستی')) {
    failures.push('a manual label must be visible in the customization tag');
  }
  if (!String(elements.get('czPreview').innerHTML).includes('برچسب دستی')) {
    failures.push('a manual label must be visible in the customization preview');
  }
} catch (error) {
  failures.push(`the geo switch threw: ${error.message}`);
}

// The address the panel acts on must never be a header the client wrote. The
// tools tab renders the resolver's own verdict — the address it settled on, the
// raw TCP peer, the chain it did not believe, and why — so a wrong trust list is
// visible instead of being an invisible decision nobody can correct.
try {
  const view = window.nexus.tools;
  view.renderClientIp({
    ip: '198.51.100.7', peer: '10.0.0.9', source: 'forwarded', cloudflare: false,
    trusted_peer: true, chain: ['9.9.9.9', '198.51.100.7', '10.0.0.1'], spoofed: true,
    trust_cdn_headers: true, trusted_proxy_cidrs: '', default_trusted: ['127.0.0.0/8'],
    cloudflare_ranges: 22,
  });
  const out = String(elements.get('tlIpOut')?.innerHTML || '');
  const stats = String(elements.get('tlIpStats')?.innerHTML || '');
  if (!out.includes('198.51.100.7')) failures.push('the client-ip card must show the address the panel acts on');
  if (!out.includes('10.0.0.9')) failures.push('the client-ip card must show the raw TCP peer');
  if (!out.includes('9.9.9.9')) failures.push('the client-ip card must show the hop it refused to believe');
  if (!stats.includes('198.51.100.7')) failures.push('the client-ip summary must show the resolved address');
  if (!String(elements.get('tlIpTag')?.innerHTML || '').includes('جعلی')) {
    failures.push('an ignored forged hop must be labelled in the panel');
  }
  if (out.includes('undefined')) failures.push('the client-ip card rendered undefined');
  // Turning header trust off is reported as the socket peer, not as a blank.
  view.renderClientIp({
    ip: '10.0.0.9', peer: '10.0.0.9', source: 'peer', cloudflare: false, trusted_peer: true,
    chain: ['203.0.113.9'], spoofed: true, trust_cdn_headers: false,
    trusted_proxy_cidrs: '203.0.113.0/24', default_trusted: [], cloudflare_ranges: 22,
  });
  if (!String(elements.get('tlIpOut')?.innerHTML || '').includes('خاموش')) {
    failures.push('the client-ip card must report the trust switch as off');
  }
} catch (error) {
  failures.push(`the client-ip card threw: ${error.message}`);
}

// A node whose address sits elsewhere shows a warning badge rather than a flag
// that contradicts its own IP.
try {
  const node = { name: 'ca-cloudflare-01', kind: 'cloudflare', server: '104.24.0.5', port: 443,
    location: 'ca', geo: { country: 'us', declared: 'ca', measured: true } };
  const badge = window.nexus.nodes.geoBadge(node);
  if (!badge.includes('IP: US')) failures.push('a node whose address is elsewhere must carry the country badge');
  if (window.nexus.nodes.geoBadge({ name: 'us-cf-01', location: 'us', geo: { country: 'us', measured: true } }) !== '') {
    failures.push('a matching node must not carry the badge');
  }
  if (window.nexus.nodes.geoBadge({ name: 'x', location: 'de', geo: {} }) !== '') {
    failures.push('an unmeasured node must not carry the badge');
  }
} catch (error) {
  failures.push(`the node country badge threw: ${error.message}`);
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

  // «بدون اسکرول‌های طولانی»: a list longer than one screen ends in exactly one
  // «نمایش بیشتر» row that says how much is left — and a short list has none.
  const users = window.nexus.users;
  const more = users.moreButton(60, 24, 'user');
  if (!more.includes('class="secondary more-row"') || !more.includes('data-more="user"')) {
    failures.push('a long user list must end in one «نمایش بیشتر» row');
  }
  if (!more.includes('۳۶ باقی‌مانده')) failures.push('the «نمایش بیشتر» row must say how much is left');
  if (users.moreButton(24, 24, 'user') !== '') failures.push('a fully shown list must not render a «نمایش بیشتر» row');
  // Both catalogs page from the store, so their limits are part of the defaults
  // (a missing key would render `undefined` rows per page).
  const defaults = new loaded.store.PanelStore();
  if (!(defaults.get('userLimit') > 0) || !(defaults.get('nodeLimit') > 0)) {
    failures.push('the store must default both list limits');
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

  // The ping column is the panel's own answer to «لوکیشن پینگ نمی‌دهد»: a
  // location is healthy only when its own hosts answered, broken when measured
  // and unreachable, and empty (with a reason) when it has no address at all.
  const pingCell = loaded['views/system'].CloudflareView.prototype.pingCell;
  const answered = pingCell({ healthy: 2, failed: 1, pending: 0, addresses: 3, fastest_ms: 34 });
  if (!answered.includes('class="lat good"')) failures.push('a location that answers must read as healthy');
  const unreachable = pingCell({ healthy: 0, failed: 2, pending: 0, addresses: 2 });
  if (!unreachable.includes('class="lat bad"') || !unreachable.includes('ناموفق')) {
    failures.push('a location nobody can reach must read as failed');
  }
  const unmeasured = pingCell({ healthy: 0, failed: 0, pending: 1, addresses: 1 });
  if (!unmeasured.includes('class="lat off"')) failures.push('an unmeasured location must not read as healthy');
  const empty = pingCell({ healthy: 0, failed: 0, pending: 0, addresses: 0 });
  if (!empty.includes('class="lat off"')) failures.push('a location without addresses must render as empty');
} catch (error) {
  failures.push(`edge card threw: ${error.message}`);
}

// The collapsible-card helper is what keeps every tab short: it must produce a
// closed <details> with a title, an optional figure and an escaped body.
try {
  const card = loaded.ui.accordion({ icon: 'link', title: 'سابلینک‌ها', subtitle: 'همه فرمت‌ها', meta: '۱۲', body: '<b>درون کارت</b>' });
  if (!card.startsWith('<details class="acc"')) failures.push('accordion must render a <details> card');
  if (card.includes(' open')) failures.push('accordion must start collapsed');
  if (!card.includes('سابلینک‌ها') || !card.includes('acc-body')) failures.push('accordion must carry its title and body');
  if (card.includes('undefined')) failures.push('accordion rendered undefined');
  const opened = loaded.ui.accordion({ title: 'x', body: 'y', open: true });
  if (!opened.includes('<details class="acc" open')) failures.push('accordion must honour open:true');
} catch (error) {
  failures.push(`accordion threw: ${error.message}`);
}

// The node-scope picker: one chip per scope the server offers, with the live node
// count, and a fallback that still carries all/multi/origin when the catalog has
// not been fetched yet (the form must never render an empty scope row).
try {
  window.nexus.store.set('scopes', [
    { id: 'all', label: 'همه نودها', hint: 'همه', count: 9, current: false },
    { id: 'multi', label: 'فقط نودهای مولتی‌لوکیشن', hint: 'لبه', count: 6, current: true },
    { id: 'cc:de', label: '🇩🇪 آلمان', hint: 'کشور', count: 2, current: false },
  ]);
  const chips = window.nexus.users.scopeChips({ node_scope: 'multi' });
  if ((chips.html.match(/class="pick on"/g) || []).length !== 1) failures.push('scope chips must mark exactly one choice');
  if (!chips.html.includes('data-scope="cc:de"') || !chips.html.includes('۹ نود')) failures.push('scope chips must list every scope with its node count');
  if (chips.html.includes('undefined')) failures.push('scope chips rendered undefined');

  // A scope the catalog no longer lists must still be selectable, or saving the
  // form would silently widen the user to the whole catalog.
  const stale = window.nexus.users.scopeChips({ node_scope: 'cc:jp', node_scope_label: '🇯🇵 ژاپن' });
  if (!stale.html.includes('data-scope="cc:jp"') || !stale.html.includes('class="pick on"')) {
    failures.push('an unlisted scope must stay visible and selected');
  }

  window.nexus.store.set('scopes', null);
  const offline = window.nexus.users.scopeChips({});
  if ((offline.html.match(/class="pick/g) || []).length !== 3) failures.push('scope chips must fall back to all/multi/origin');

  // The quick-create cards are built from the server's modes (a preset plus the
  // node scope it publishes).
  window.nexus.store.set('presets', [
    { id: 'iran-fast', name: 'ایران — پرسرعت', kind: 'iran', scope: 'all', best_for: 'موبایل', highlights: ['فرگمنت'] },
    { id: 'multi-location', name: 'چند لوکیشن — فقط لبه', kind: 'edge', scope: 'multi', best_for: 'CDN', highlights: ['لبه'] },
  ]);
  window.nexus.store.set('quickMode', 'multi-location');
  window.nexus.users.renderModes();
  window.nexus.users.renderScopes();
} catch (error) {
  failures.push(`scope chips threw: ${error.message}`);
}

// The live guide reads real state from /api/guide; with no payload it must render
// a skeleton instead of throwing, and its chip must never show a fake count.
try {
  window.nexus.store.set('guide', null);
  window.nexus.guide.renderChip();
  window.nexus.guide.render();
  window.nexus.store.set('guide', {
    score: 40, section: 'users',
    tip: { title: 'کاربران در سه کلیک', items: ['حالت را انتخاب کنید'] },
    steps: [
      { id: 'worker', title: 'آدرس Worker', hint: 'بی‌آن کار نمی‌کند', detail: 'ثبت نشده', done: false, section: 'cloudflare' },
      { id: 'nodes', title: 'کاتالوگ نود', hint: 'Sync بزنید', detail: '۳ نود فعال', done: true, section: 'nodes' },
    ],
    links: { smart: 'https://panel.example.com/sub/1?target=auto', portal: 'https://panel.example.com/portal/1', username: 'demo' },
    support: 'https://t.me/miliconfig',
  });
  window.nexus.guide.render();
  window.nexus.guide.renderChip();
  window.nexus.guide.toggle(false);
  const state = window.nexus.store.get('guide');
  if (!state || state.steps.length !== 2) failures.push('guide state must survive a render');
  // The support channel: on a phone this drawer is the only place the link is
  // reachable, so it must survive every guide render.
  const drawer = document.querySelector('#guideBody').innerHTML;
  if (!drawer.includes('href="https://t.me/miliconfig"')) failures.push('the guide must offer the support channel');
  window.nexus.store.set('guide', { score: 0, tip: {}, steps: [], support: 'javascript:alert(1)' });
  window.nexus.guide.render();
  if (document.querySelector('#guideBody').innerHTML.includes('javascript:')) failures.push('the guide must not link a non-http support value');
} catch (error) {
  failures.push(`guide threw: ${error.message}`);
}

// A response is not always the object a view expects: a proxy or a captive
// portal answers with its own page (a string), an empty body parses to null, a
// version skew ships the wrong shape. Any of those used to reach
// `data.nodes.map` and take the node section down with «Cannot read properties
// of undefined (reading 'map')». Every payload-consuming path must instead show
// an empty state — and the client must say why a body it cannot read is a
// problem, instead of handing HTML to a view.
try {
  const realApi = window.nexus.api;
  const host = elementFor('exploreLinks');
  window.nexus.store.set('users', [{ username: 'probe', protocol_label: 'همه پروتکل‌ها' }]);
  window.nexus.store.set('exploreUser', 'probe');
  for (const payload of ['<!doctype html><html>captive portal</html>', null, {}, 42]) {
    window.nexus.api = { get: async () => payload, post: async () => payload };
    host.innerHTML = '';
    await window.nexus.nodes.renderExplorer();
    if (!host.innerHTML.includes('class="empty"')) {
      failures.push('the node explorer must show an empty state when the links payload has no nodes');
    }
    if (host.innerHTML.includes('reading')) failures.push('the node explorer must not render an error message as content');
  }
  // …and the guard must not swallow a good payload either.
  window.nexus.api = {
    get: async () => ({ protocol_label: 'VLESS', nodes: [{ name: 'de-cf-01', kind: 'cloudflare', latency_ms: 42,
      links: { primary: 'vless://example' }, subscription: 'https://panel.example.com/sub/1?node=de-cf-01' }] }),
  };
  host.innerHTML = '';
  await window.nexus.nodes.renderExplorer();
  if (!host.innerHTML.includes('de-cf-01') || !host.innerHTML.includes('sub-card')) {
    failures.push('a readable links payload must still paint one card per node');
  }
  window.nexus.api = realApi;
} catch (error) {
  failures.push(`hostile payloads threw: ${error.message}`);
}

// A 200 that is not JSON never reached the API (proxy, captive portal, wrong
// host). It used to be returned as a string, which is what made views explode.
try {
  const realFetch = globalThis.fetch;
  const client = new loaded.api.ApiClient({ headers: () => ({}), markExpired: noop });
  globalThis.fetch = async () => new Response('<!doctype html><html>portal</html>', {
    status: 200, headers: { 'Content-Type': 'text/html' },
  });
  let message = '';
  await client.get('/api/users/probe/links')
    .then(() => failures.push('a non-JSON 200 must not resolve as a payload'))
    .catch((error) => { message = error.message; });
  if (!/JSON/.test(message)) failures.push('a non-JSON 200 must explain that the server did not answer with JSON');
  // A real JSON error body still reaches the view as a message.
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'کاربر پیدا نشد' }),
    { status: 404, headers: { 'Content-Type': 'application/json' } });
  await client.get('/api/users/nope').then(
    () => failures.push('a 404 must reject'),
    (error) => { if (!/پیدا نشد/.test(error.message)) failures.push('a JSON error body must keep its server message'); });
  globalThis.fetch = realFetch;
} catch (error) {
  failures.push(`api client threw: ${error.message}`);
}

// The second engines (AnyTLS / TUIC). One payload drives the whole card: the rows
// are built from `catalog`, the engines from `engines`, and the save button
// rebuilds its request from that same list. When the server nested that status
// one level down, the card painted «هیچ پروتکلی منتشر نشده» and the save posted an
// empty profile map — the switch looked live and did nothing.
try {
  const view = window.nexus.advanced;
  const payload = {
    success: true, enabled: true, host: '203.0.113.10', port_host: '203.0.113.10',
    tcp: true, udp: false, sni: 'www.cloudflare.com', published: ['anytls'],
    catalog: [
      { id: 'anytls', protocol: 'anytls', tag: 'AnyTLS · TLS', note: 'ضد DPI', needs: 'tcp',
        port: 8444, engine: 'singbox', enabled: true, reachable: true, running: true,
        published: true, unknown: false, reason: '' },
      { id: 'tuic', protocol: 'tuic', tag: 'TUIC v5 · QUIC', note: 'روی QUIC', needs: 'udp',
        port: 8445, engine: 'mihomo', enabled: true, reachable: false, running: false,
        published: false, unknown: false, reason: 'این پلتفرم پورت UDP نمی‌دهد' },
    ],
    engines: [
      { id: 'singbox', label: 'sing-box', binary: '/usr/local/bin/sing-box', installed: true,
        running: true, pid: 12, profiles: ['anytls'], error: '' },
      { id: 'mihomo', label: 'mihomo', binary: '/usr/local/bin/mihomo', installed: true,
        running: false, pid: null, profiles: [], error: '' },
    ],
    notes: ['پورت UDP در دسترس نیست؛ TUIC روی VPS منتشر می‌شود.'],
    profiles: [], counts: { enabled: 2, published: 1, engines: 1 },
  };
  window.nexus.store.set('cores', payload);
  view.renderCores();
  const rows = String(elements.get('coProfiles')?.innerHTML || '');
  if (!rows.includes('AnyTLS') || !rows.includes('TUIC')) {
    failures.push('the cores card must list every hosted protocol');
  }
  if ((rows.match(/data-co-toggle=/g) || []).length !== 2) {
    failures.push('every hosted protocol needs its own switch in the cores card');
  }
  if (!rows.includes('این پلتفرم پورت UDP نمی‌دهد')) failures.push('a withheld protocol must show why');
  if (!rows.includes('منتشرشده')) failures.push('a published protocol must be labelled as such');
  if (rows.includes('undefined')) failures.push('the cores card rendered undefined');
  const engineRows = String(elements.get('coEngines')?.innerHTML || '');
  if (!engineRows.includes('sing-box') || !engineRows.includes('mihomo')) {
    failures.push('the cores card must show both engines');
  }
  if (!engineRows.includes('Running')) failures.push('a running engine must say so');

  const realApi = window.nexus.api;
  let sent = null;
  window.nexus.api = { get: async () => payload, post: async (url, body) => { sent = { url, body }; return payload; } };
  // The reload that follows a save is offline in this test; the request itself is
  // what matters here.
  await view.saveCores().catch(() => {});
  window.nexus.api = realApi;
  if (!sent || sent.url !== '/api/cores') failures.push('the cores save must post to /api/cores');
  if (sent && Object.keys(sent.body.profiles || {}).join(',') !== 'anytls,tuic') {
    failures.push('the cores save must send one profile entry per hosted protocol');
  }
  if (sent && (sent.body.profiles.anytls.port !== 8444 || sent.body.profiles.anytls.engine !== 'singbox')) {
    failures.push('the cores save must carry the port and engine the card is showing');
  }
} catch (error) {
  failures.push(`the cores card threw: ${error.message}`);
}

await new Promise((resolve) => setTimeout(resolve, 50));

if (failures.length) {
  console.error('FAILED'); failures.forEach((line) => console.error(' -', line));
  process.exit(1);
}
console.log(`OK — ${checks.length} checks passed, ${mods.length + 1} modules linked, render paths clean`);
// The shell starts a 1s clock interval, so the process is ended explicitly.
process.exit(0);
