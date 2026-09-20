/* =============================================================================
   NEXUS · store — one state container and one router.

   Views never keep their own copies of server data: everything the panel shows
   lives here, so a refresh from any view updates all of them and there is a
   single place to see what a render depends on.
   ========================================================================== */

export const SECTIONS = [
  { id: 'dashboard', label: 'داشبورد', icon: 'dash', crumb: 'NEXUS CONTROL CENTER', title: 'داشبورد' },
  { id: 'nodes', label: 'نودها', icon: 'nodes', crumb: 'NODE CATALOG', title: 'نودها' },
  { id: 'users', label: 'کاربران', icon: 'users', crumb: 'USER MANAGEMENT', title: 'کاربران' },
  { id: 'cloudflare', label: 'Cloudflare', icon: 'cloud', crumb: 'EDGE NETWORK', title: 'Cloudflare' },
  { id: 'customize', label: 'شخصی‌سازی', icon: 'edit', crumb: 'CUSTOMIZATION', title: 'شخصی‌سازی' },
  { id: 'tools', label: 'ابزار شبکه', icon: 'globe', crumb: 'NETWORK TOOLS', title: 'ابزار شبکه' },
  { id: 'advanced', label: 'پیشرفته', icon: 'server', crumb: 'ADVANCED', title: 'پیشرفته' },
  { id: 'settings', label: 'تنظیمات', icon: 'cog', crumb: 'SYSTEM SETTINGS', title: 'تنظیمات' },
];

export class PanelStore {
  constructor() {
    this.data = {
      metrics: null,
      users: [],
      nodes: [],
      settings: null,
      core: null,
      logs: [],
      cfIps: [],
      worker: null,
      edge: null,
      customization: null,
      hysteria: null,
      packs: null,
      transports: null,
      locations: [],
      presets: [],
      workerCode: '',
      uptimeBase: null,
      lastLoad: null,
      section: 'dashboard',
      trafficRange: 24,
      nodeFilter: 'all',
      nodeSearch: '',
      nodeSort: 'latency',
      userFilter: 'all',
      userSearch: '',
      userSort: 'new',
      exploreUser: '',
      autoRefresh: true,
      countdown: 25,
      loading: false,
      live: null,
      pwa: { installable: false, installed: false },
    };
    this.listeners = new Map();
  }

  get(key) { return this.data[key]; }

  set(key, value) {
    const changed = this.data[key] !== value;
    this.data[key] = value;
    if (changed) this.#emit(key, value);
    return value;
  }

  patch(values) {
    Object.entries(values).forEach(([key, value]) => this.set(key, value));
  }

  on(key, handler) {
    const list = this.listeners.get(key) || [];
    list.push(handler);
    this.listeners.set(key, list);
    return () => this.listeners.set(key, (this.listeners.get(key) || []).filter((h) => h !== handler));
  }

  #emit(key, value) {
    (this.listeners.get(key) || []).slice().forEach((handler) => {
      try { handler(value); } catch (error) { console.warn('[nexus] store listener failed', key, error); }
    });
  }
}

export class Router {
  constructor(store, { onNavigate } = {}) {
    this.store = store;
    this.onNavigate = onNavigate;
  }

  get sections() { return SECTIONS; }

  meta(id) { return SECTIONS.find((section) => section.id === id) || SECTIONS[0]; }

  boot() {
    const hash = (location.hash || '').replace('#', '');
    this.apply(SECTIONS.some((section) => section.id === hash) ? hash : 'dashboard');
    window.addEventListener('hashchange', () => this.apply((location.hash || '').replace('#', '')));
  }

  go(id, { scroll = true } = {}) {
    this.apply(id);
    if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' });
    this.onNavigate?.(this.store.get('section'));
  }

  apply(id) {
    const target = SECTIONS.some((section) => section.id === id) ? id : 'dashboard';
    this.store.set('section', target);
    this.store.set('countdown', 25);
    document.querySelectorAll('.section').forEach((el) => el.classList.toggle('active', el.id === `section-${target}`));
    document.querySelectorAll('.nav').forEach((el) => el.classList.toggle('active', el.dataset.section === target));
    const meta = this.meta(target);
    const title = document.getElementById('pageTitle');
    const crumb = document.getElementById('crumb');
    if (title) title.textContent = meta.title;
    if (crumb) crumb.textContent = meta.crumb;
    if (location.hash !== `#${target}`) {
      try { history.replaceState(null, '', `#${target}`); } catch (error) { /* ignore */ }
    }
    return target;
  }
}
