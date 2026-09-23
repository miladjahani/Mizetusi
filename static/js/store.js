/* =============================================================================
   NEXUS · store — one state container and one router.

   Views never keep their own copies of server data: everything the panel shows
   lives here, so a refresh from any view updates all of them and there is a
   single place to see what a render depends on.
   ========================================================================== */

// The nine sections, each belonging to one navigation group. Groups are what
// keep the sidebar (and the phone's bottom bar) short: five rows instead of a
// single long list where every tab competes for attention.
export const SECTIONS = [
  { id: 'dashboard', label: 'داشبورد', icon: 'dash', crumb: 'NEXUS CONTROL CENTER', title: 'داشبورد', group: 'overview' },
  { id: 'users', label: 'کاربران', icon: 'users', crumb: 'USER MANAGEMENT', title: 'کاربران', group: 'access' },
  { id: 'nodes', label: 'نودها', icon: 'nodes', crumb: 'NODE CATALOG', title: 'نودها', group: 'access' },
  { id: 'cloudflare', label: 'Cloudflare', icon: 'cloud', crumb: 'EDGE NETWORK', title: 'Cloudflare', group: 'network' },
  { id: 'tools', label: 'ابزار شبکه', icon: 'globe', crumb: 'NETWORK TOOLS', title: 'ابزار شبکه', group: 'network' },
  { id: 'telegram', label: 'پروکسی تلگرام', icon: 'link', crumb: 'TELEGRAM PROXY', title: 'پروکسی تلگرام', group: 'network' },
  { id: 'customize', label: 'شخصی‌سازی', icon: 'edit', crumb: 'CUSTOMIZATION', title: 'شخصی‌سازی', group: 'look' },
  { id: 'advanced', label: 'پیشرفته', icon: 'server', crumb: 'ADVANCED', title: 'پیشرفته', group: 'look' },
  { id: 'settings', label: 'تنظیمات', icon: 'cog', crumb: 'SYSTEM SETTINGS', title: 'تنظیمات', group: 'system' },
];

// ``items`` is the render order inside a group; ``quick`` is the one tab an
// admin lands on from that group most of the time (used by the phone bar).
export const NAV_GROUPS = [
  { id: 'overview', label: 'نمای کلی', icon: 'dash', items: ['dashboard'] },
  { id: 'access', label: 'کاربران و نودها', icon: 'users', items: ['users', 'nodes'] },
  { id: 'network', label: 'شبکه و لبه', icon: 'cloud', items: ['cloudflare', 'tools', 'telegram'] },
  { id: 'look', label: 'پنل و ظاهر', icon: 'edit', items: ['customize', 'advanced'] },
  { id: 'system', label: 'سیستم', icon: 'cog', items: ['settings'] },
];

export const groupOf = (sectionId) => NAV_GROUPS.find((group) => group.items.includes(sectionId)) || NAV_GROUPS[0];

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
      telegram: null,
      telegramProbe: '',
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
      // Paging: a list is rendered a screenful at a time, so a tab with 300
      // clean IPs or 80 users stays one short page instead of a long scroll.
      userLimit: 24,
      nodeLimit: 24,
      exploreUser: '',
      // Navigation: which group is unfolded (the one holding the open tab).
      navGroup: 'overview',
      // Live guide: the setup steps the server just reported, and the drawer.
      guide: null,
      guideOpen: false,
      // Quick-create: the selected mode, the node scope it will publish, and
      // whether the panel's own default scope should override the mode's.
      scopes: [],
      quickMode: '',
      quickScope: '',
      quickUseDefaultScope: false,
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

  get groups() { return NAV_GROUPS; }

  sectionsOf(groupId) { return SECTIONS.filter((section) => section.group === groupId); }

  groupMeta(groupId) { return NAV_GROUPS.find((group) => group.id === groupId) || NAV_GROUPS[0]; }

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
    // Navigating into a tab always unfolds the group that holds it, so the
    // active row is never hidden behind a collapsed section.
    this.store.set('navGroup', this.meta(target).group || 'overview');
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
