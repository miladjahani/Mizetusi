/* =============================================================================
   NEXUS · app — the shell.

   It owns the singletons (session, api, store, views), the data loading, the
   clock, the auto-refresh loop and the two failure paths that used to hurt:

   * a crashed view was fatal — every render now goes through ``safe()`` and a
     single bad view can no longer take the panel down;
   * an expired session produced an endless toast storm plus a redirect loop on
     a phone — a 401 is now reported once, polling stops and an in-panel
     re-login overlay keeps the page alive.
   ========================================================================== */
import { $, $$, ico, esc, Fmt, EventBus, ToastCenter } from './core.js';
import { ModalManager } from './ui.js';
import { SessionManager } from './session.js';
import { ApiClient } from './api.js';
import { PanelStore, Router, SECTIONS } from './store.js';
import { PwaManager } from './pwa.js';
import { DashboardView } from './views/dashboard.js';
import { NodesView } from './views/nodes.js';
import { UsersView } from './views/users.js';
import { CloudflareView, SettingsView } from './views/system.js';

const POLL_SECONDS = 25;

export class NexusApp {
  constructor() {
    this.bus = new EventBus();
    this.store = new PanelStore();
    this.toasts = new ToastCenter($('#toasts'));
    this.modals = new ModalManager($('#modalRoot'));
    this.session = new SessionManager({ bus: this.bus });
    this.api = new ApiClient(this.session);
    this.router = new Router(this.store, { onNavigate: (section) => this.safe(() => this.showSection(section)) });
    this.pwa = new PwaManager({ store: this.store, toasts: this.toasts, bus: this.bus });
    this.dashboard = new DashboardView(this);
    this.nodes = new NodesView(this);
    this.users = new UsersView(this);
    this.cloudflare = new CloudflareView(this);
    this.settings = new SettingsView(this);
    this.clockTimer = null;
    this.polling = true;
    this.loginOverlay = null;
    this.resizeTimer = null;
  }

  /* ------------------------------------------------------------------ errors */
  safe(fn) {
    try {
      const result = fn();
      if (result && typeof result.then === 'function') return result.catch((error) => this.report(error));
      return result;
    } catch (error) {
      this.report(error);
      return undefined;
    }
  }

  report(error) {
    // The 401 path is owned by the re-login overlay, so it stays silent here.
    if (error?.unauthorized || error?.status === 401) return;
    console.warn('[nexus]', error);
    const message = error?.message || 'خطای نامشخص';
    if (/fetch|network|تایم‌اوت/i.test(message)) this.toasts.err('ارتباط با سرور برقرار نشد — اتصال را بررسی کنید');
    else this.toasts.err(message);
  }

  installErrorHandlers() {
    window.addEventListener('error', (event) => {
      if (event.message) this.toasts.err(`خطای برنامه: ${String(event.message).slice(0, 90)}`, 6000);
    });
    window.addEventListener('unhandledrejection', (event) => {
      const reason = event.reason;
      if (reason && !reason.unauthorized && reason.name !== 'AbortError') {
        this.toasts.err(String(reason.message || reason).slice(0, 90), 6000);
      }
      event.preventDefault();
    });
    this.bus.on('session:lost', () => this.handleSessionLost());
  }

  /* ------------------------------------------------------------------- boot */
  async start() {
    this.installErrorHandlers();
    this.session.boot();
    this.renderNav();
    this.nodes.renderFilters();
    this.users.renderFilters();
    this.bindEvents();
    this.pwa.init();
    this.router.boot();
    this.startClock();

    if (this.session.active) {
      const cookieOk = await this.session.cookieWorks();
      // A remembered token that the server still accepts is kept; a stale one is
      // dropped so the overlay shows up immediately instead of on the first 401.
      if (cookieOk && !this.session.expired) this.session.clear();
    }
    await this.refreshAll(true);
  }

  renderNav() {
    const nav = $('#nav');
    if (!nav) return;
    nav.innerHTML = SECTIONS.map((section) => `
      <button class="nav${section.id === this.store.get('section') ? ' active' : ''}" data-section="${section.id}">
        ${ico(section.icon)}
        <span class="nav-label">${esc(section.label)}</span>
        <span class="nav-badge" id="badge-${section.id}">—</span>
      </button>`).join('');
    $$('.nav', nav).forEach((button) => { button.onclick = () => this.safe(() => this.go(button.dataset.section)); });
    this.updateBadges();
  }

  updateBadges() {
    const totals = this.store.get('metrics')?.totals || null;
    const set = (id, value) => {
      const el = $(`#badge-${id}`);
      if (!el) return;
      if (value === null) { el.style.display = 'none'; return; }
      el.style.display = '';
      el.textContent = value;
    };
    set('dashboard', null);
    set('settings', null);
    if (!totals) return;
    set('users', Fmt.num(totals.active_users));
    set('nodes', Fmt.num(totals.nodes_enabled));
    set('cloudflare', Fmt.num(totals.cf_ips_ok));
  }

  /* -------------------------------------------------------------------- data */
  async loadMetrics() {
    const metrics = await this.api.get('/api/metrics?hours=168');
    this.store.patch({ metrics, uptimeBase: { server: metrics.uptime_seconds, at: Date.now() } });
    this.updateBadges();
    this.dashboard.render();
  }

  async loadUsers() {
    this.store.set('users', await this.api.get('/api/users') || []);
  }

  async loadNodes() {
    this.store.set('nodes', await this.api.get('/api/nodes') || []);
  }

  async loadLogs() {
    try {
      this.store.set('logs', await this.api.get('/api/logs?limit=60') || []);
    } catch (error) {
      this.store.set('logs', []);
      if (!error.unauthorized) throw error;
    }
  }

  async loadCore() {
    try {
      this.store.set('core', await this.api.get('/api/core/status'));
    } catch (error) {
      this.store.set('core', null);
      if (!error.unauthorized) throw error;
    }
  }

  async loadSettings() {
    try {
      this.store.set('settings', await this.api.get('/api/settings'));
    } catch (error) {
      this.store.set('settings', null);
      if (!error.unauthorized) throw error;
    }
    await this.loadCore();
    await this.loadLogs();
    this.applyBrand();
    this.settings.render();
    this.cloudflare.render();
  }

  async loadCfIps() {
    const limit = $('#cfLimit') ? $('#cfLimit').value : 50;
    try {
      this.store.set('cfIps', await this.api.get(`/api/cloudflare/ips?limit=${limit}`) || []);
    } catch (error) {
      this.store.set('cfIps', []);
      if (!error.unauthorized) throw error;
    }
  }

  async loadWorkerSettings() {
    try {
      this.store.set('worker', await this.api.get('/api/settings/cloudflare-worker'));
    } catch (error) {
      this.store.set('worker', null);
      if (!error.unauthorized) throw error;
    }
  }

  async loadCloudflare() {
    await Promise.all([this.loadCfIps(), this.loadWorkerSettings()]);
    this.cloudflare.render();
    this.dashboard.cloudflareSummary();
  }

  workerSettings() {
    const worker = this.store.get('worker') || {};
    const settings = this.store.get('settings');
    return { url: worker.url || settings?.worker?.url || '', configured: !!(worker.configured || settings?.worker?.configured) };
  }

  async reloadNodes() {
    await this.loadNodes();
    this.nodes.render();
    this.nodes.renderCoverage();
  }

  async reloadUsers() {
    await this.loadUsers();
    this.users.render();
    this.users.renderSubTable();
    this.nodes.renderExplorerUsers();
    this.loadMetrics().catch(() => {});
  }

  /* --------------------------------------------------------------- sections */
  go(section) { this.router.go(section); }

  async showSection(section) {
    if (section === 'dashboard') {
      await Promise.all([this.loadCore(), this.loadWorkerSettings(), this.loadLogs()]);
      this.dashboard.render();
      this.setLive(true);
      return;
    }
    if (section === 'nodes') {
      if (!this.store.get('users').length) await this.loadUsers();
      await this.loadNodes();
      this.nodes.render();
      this.nodes.renderCoverage();
      this.nodes.renderExplorerUsers();
      return;
    }
    if (section === 'users') {
      await this.loadUsers();
      this.users.render();
      this.users.renderSubTable();
      this.nodes.renderExplorerUsers();
      return;
    }
    if (section === 'cloudflare') {
      await this.loadCloudflare();
      await this.cloudflare.loadWorkerCode(false);
      return;
    }
    if (section === 'settings') await this.loadSettings();
  }

  async refreshAll(silent = false) {
    if (this.store.get('loading')) return;
    this.store.set('loading', true);
    const button = $('#btnRefresh');
    button?.classList.add('spin');
    try {
      await this.loadMetrics();
      const section = this.store.get('section');
      if (section === 'dashboard') {
        await Promise.all([this.loadCore(), this.loadWorkerSettings(), this.loadLogs()]);
        this.dashboard.render();
      }
      if (section === 'nodes') {
        await this.loadNodes();
        this.nodes.render();
        this.nodes.renderCoverage();
      }
      if (section === 'users') {
        await this.loadUsers();
        this.users.render();
        this.users.renderSubTable();
        this.nodes.renderExplorerUsers();
      }
      if (section === 'cloudflare') await this.loadCloudflare();
      if (section === 'settings') await this.loadSettings();
      this.setLive(true);
    } catch (error) {
      this.setLive(false);
      if (!silent && !error?.unauthorized) this.report(error);
    } finally {
      this.store.set('loading', false);
      this.store.set('countdown', POLL_SECONDS);
      button?.classList.remove('spin');
    }
  }

  setLive(ok) {
    if (this.store.get('live') === ok) return;
    this.store.set('live', ok);
    const pill = $('#livePill');
    const dot = $('#sysDot');
    if (pill) {
      pill.className = `pill ${ok ? 'ok' : 'bad'}`;
      pill.innerHTML = `<i class="dot${ok ? '' : ' bad'}"></i> ${ok ? 'زنده' : 'قطع'}`;
    }
    if (dot) dot.className = `dot${ok ? '' : ' bad'}`;
    const text = $('#sysText');
    if (text) text.textContent = ok ? 'سیستم آنلاین است' : 'اتصال به سرور قطع شد';
  }

  /* ------------------------------------------------------------------ clock */
  startClock() {
    const tick = () => this.safe(() => this.tickClock());
    this.clockTimer = setInterval(tick, 1000);
    tick();
  }

  tickClock() {
    const main = $('#clockMain');
    if (main) main.textContent = Fmt.clock();
    const date = $('#clockDate');
    if (date) date.textContent = Fmt.clockDate();
    const utc = $('#clockUtc');
    if (utc) utc.textContent = `UTC ${Fmt.utc()}`;
    const base = this.store.get('uptimeBase');
    if (base) {
      const seconds = base.server + (Date.now() - base.at) / 1000;
      const uptime = $('#clockUptime');
      if (uptime) uptime.textContent = `UP ${Fmt.until(seconds)}`;
    }
    $$('[data-expiry]').forEach((el) => {
      const ts = Number(el.dataset.expiry);
      if (!ts) return;
      el.textContent = Fmt.until(ts - Date.now() / 1000);
    });
    const auto = $('#autoPill');
    if (!auto) return;
    if (!this.store.get('autoRefresh') || !this.polling) {
      auto.textContent = '⏸ متوقف';
      return;
    }
    const remaining = this.store.get('countdown') - 1;
    this.store.set('countdown', remaining <= 0 ? POLL_SECONDS : remaining);
    if (remaining <= 0) this.safe(() => this.refreshAll(true));
    auto.textContent = `⏱ ${Fmt.num(Fmt.clamp(this.store.get('countdown'), 0, 99))}s`;
  }

  /* ------------------------------------------------------------------ brand */
  applyBrand() {
    const settings = this.store.get('settings');
    const brand = settings?.brand || {};
    const root = document.documentElement;
    if (brand.accent) root.style.setProperty('--accent', brand.accent);
    if (brand.accent_secondary) root.style.setProperty('--accent-2', brand.accent_secondary);
    const name = brand.app_name || 'NEXUS';
    document.title = `${name} • مرکز کنترل`;
    const label = $('#brandName');
    if (label) label.textContent = name;
    const footer = $('#brandVersion');
    if (footer) footer.textContent = `${name} 7.0`;
    const apple = $('meta[name="apple-mobile-web-app-title"]');
    if (apple) apple.setAttribute('content', name);
  }

  /* ------------------------------------------------------- session recovery */
  handleSessionLost() {
    this.polling = false;
    this.setLive(false);
    if (this.loginOverlay) return;
    const modal = this.modals.open({
      title: 'نشست شما به پایان رسید',
      subtitle: 'برای ادامه کار، رمز مدیریت را دوباره وارد کنید — صفحه و تنظیمات شما حفظ می‌شود.',
      size: 'slim',
      body: `<label>رمز مدیریت</label>
        <input id="reloginPassword" type="password" autocomplete="current-password" placeholder="••••••••">
        <div class="switch-row" style="margin-top:12px"><div class="txt"><b>این دستگاه را به خاطر بسپار</b><span>ورود برای ۳۰ روز حفظ می‌شود</span></div>
          <div class="switch on" id="reloginRemember"></div></div>
        <p class="muted" id="reloginError" style="color:#ffb3bd;min-height:18px;margin:8px 0 0"></p>`,
      footer: '<div class="actions" style="margin:0"><button class="primary" id="reloginSubmit">ورود مجدد</button></div>',
    });
    this.loginOverlay = modal;
    const remember = $('#reloginRemember', modal.el);
    remember.onclick = () => remember.classList.toggle('on');
    const submit = $('#reloginSubmit', modal.el);
    const input = $('#reloginPassword', modal.el);
    const failure = $('#reloginError', modal.el);
    const attempt = async () => {
      const password = input.value;
      if (!password) { failure.textContent = 'رمز را وارد کنید'; return; }
      submit.disabled = true;
      submit.innerHTML = '<span class="spin-inline"></span> در حال ورود…';
      try {
        const response = await fetch('/api/login', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password, remember: remember.classList.contains('on') }),
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok) throw new Error(payload?.detail || 'ورود ناموفق بود');
        this.session.save(payload.token, { remember: remember.classList.contains('on') });
        modal.close();
        this.loginOverlay = null;
        this.polling = true;
        this.toasts.ok('دوباره وارد شدید');
        await this.refreshAll(true);
        await this.showSection(this.store.get('section'));
      } catch (error) {
        failure.textContent = /429/.test(String(error.message)) ? 'تلاش‌های زیادی انجام شده؛ کمی بعد امتحان کنید' : error.message;
        submit.disabled = false;
        submit.textContent = 'ورود مجدد';
        input.focus();
      }
    };
    submit.onclick = () => { void attempt(); };
    input.onkeydown = (event) => { if (event.key === 'Enter') void attempt(); };
    setTimeout(() => input.focus(), 120);
  }

  async logout() {
    try { await this.api.post('/api/logout', {}); } catch (error) { /* already gone */ }
    this.session.clear();
    location.href = '/login';
  }

  /* ----------------------------------------------------------------- events */
  bindEvents() {
    $$('[data-goto]').forEach((button) => { button.onclick = () => this.go(button.dataset.goto); });
    const refresh = $('#btnRefresh');
    if (refresh) {
      refresh.onclick = () => this.safe(async () => {
        await this.refreshAll();
        this.toasts.ok('بروزرسانی انجام شد', 1600);
      });
    }
    const logout = $('#logout');
    if (logout) logout.onclick = () => this.safe(() => this.logout());
    $$('[data-pwa-install]').forEach((button) => {
      button.onclick = () => this.safe(() => this.pwa.promptInstall());
    });
    this.bus.on('pwa:update', () => this.toasts.info('نسخه جدید NEXUS آماده است — صفحه را بازخوانی کنید', 7000));

    window.addEventListener('resize', () => {
      clearTimeout(this.resizeTimer);
      this.resizeTimer = setTimeout(() => {
        if (this.store.get('metrics')) {
          this.dashboard.traffic();
          this.dashboard.mix();
        }
      }, 160);
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) return;
      // Coming back to a phone that was asleep: refresh at once instead of
      // waiting for the rest of the countdown.
      this.store.set('countdown', 3);
    });

    this.dashboard.bindEvents();
    this.nodes.bindEvents();
    this.users.bindEvents();
    this.cloudflare.bindEvents();
    this.settings.bindEvents();
  }
}

const app = new NexusApp();
window.nexus = app; // debugging handle for the browser console

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => app.start());
else app.start();
