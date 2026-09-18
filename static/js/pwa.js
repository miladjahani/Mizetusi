/* =============================================================================
   NEXUS · pwa — install it on a phone or desktop like a native app.

   The panel ships a manifest, a service worker and its own NEXUS icon set, so
   Chrome/Edge/Safari offer a real install (standalone window, home-screen icon,
   app shell cached for a flaky connection).
   ========================================================================== */
import { $, $$ } from './core.js';

export class PwaManager {
  static REGISTERED = false;

  constructor({ store, toasts, bus }) {
    this.store = store;
    this.toasts = toasts;
    this.bus = bus;
    this.deferredPrompt = null;
    this.updateReady = false;
  }

  get standalone() {
    return window.matchMedia('(display-mode: standalone)').matches
      || window.matchMedia('(display-mode: minimal-ui)').matches
      || window.navigator.standalone === true;
  }

  get iOS() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent);
  }

  init() {
    this.watchInstallPrompt();
    this.registerServiceWorker();
    this.renderButtons();
    this.store.on('pwa', () => this.renderButtons());
    this.store.set('pwa', {
      installable: !!this.deferredPrompt,
      installed: this.standalone,
      platform: this.iOS ? 'ios' : 'default',
    });
  }

  watchInstallPrompt() {
    window.addEventListener('beforeinstallprompt', (event) => {
      // Keep the event so the install button can trigger the browser dialog.
      event.preventDefault();
      this.deferredPrompt = event;
      this.store.set('pwa', { installable: true, installed: false, platform: this.iOS ? 'ios' : 'default' });
    });
    window.addEventListener('appinstalled', () => {
      this.deferredPrompt = null;
      this.store.set('pwa', { installable: false, installed: true, platform: 'default' });
      this.toasts.ok('NEXUS روی این دستگاه نصب شد', 5200);
    });
  }

  registerServiceWorker() {
    if (!('serviceWorker' in navigator) || location.protocol === 'file:') return;
    if (PwaManager.REGISTERED) return;
    PwaManager.REGISTERED = true;
    // The worker re-runs the panel from its cached shell. Whenever a newer
    // build takes control the page reloads exactly once, so a phone that was
    // showing the previous CSS/JS picks up the new one without a manual
    // hard-refresh. The flag doubles as a loop guard.
    let reloading = false;
    const reloadOnce = (reason) => {
      if (reloading || !navigator.serviceWorker.controller) return;
      reloading = true;
      this.toasts?.info(reason, 3600);
      setTimeout(() => location.reload(), 420);
    };
    navigator.serviceWorker.addEventListener('controllerchange', () => reloadOnce('نسخه جدید نصب شد — بارگذاری مجدد…'));
    navigator.serviceWorker.addEventListener('message', (event) => {
      if (event.data?.type === 'NEXUS_ACTIVATED' && this.build && event.data.version !== this.build) {
        reloadOnce('نسخه جدید پنل بارگذاری می‌شود…');
      }
    });
    window.addEventListener('load', async () => {
      try {
        // updateViaCache:'none' keeps the worker script itself out of the HTTP
        // cache, so the deployed build token is always the one that is read.
        const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' });
        this.registration = registration;
        this.build = await this.currentBuild();
        this.checkDeployedBuild();
        registration.addEventListener('updatefound', () => {
          const installing = registration.installing;
          installing?.addEventListener('statechange', () => {
            if (installing.state === 'installed' && navigator.serviceWorker.controller) {
              this.updateReady = true;
              this.bus?.emit('pwa:update', { registration });
            }
          });
        });
        // A control panel is long-lived in a phone tab: poll for new builds so
        // a deploy shows up without closing the app.
        const checkForUpdate = () => this.safeUpdate();
        setInterval(checkForUpdate, 15 * 60 * 1000);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) checkForUpdate(); });
        window.addEventListener('online', checkForUpdate);
      } catch (error) {
        // A service worker is a nice-to-have: never let it break the panel.
        console.warn('[nexus] service worker registration failed', error);
      }
    });
  }

  /* A safety net for the case where the new worker took control before this
     script attached its listeners (a cached old worker reloading the page).
     The last seen build is remembered; a different one means the deployed
     panel changed, so the page reloads exactly once onto the new assets. */
  checkDeployedBuild() {
    if (!this.build) return;
    try {
      const seen = localStorage.getItem('nexus.build');
      localStorage.setItem('nexus.build', this.build);
      if (seen && seen !== this.build && navigator.serviceWorker.controller) {
        this.toasts?.info('نسخه جدید پنل نصب شد — بارگذاری مجدد…', 3600);
        setTimeout(() => location.reload(), 420);
      }
    } catch {
      // Storage can be blocked (private mode); the SW update path still works.
    }
  }

  async currentBuild() {
    try {
      const response = await fetch('/api/version', { cache: 'no-store' });
      return (await response.json())?.build || '';
    } catch { return ''; }
  }

  async safeUpdate() {
    try {
      const registration = this.registration || await navigator.serviceWorker.getRegistration();
      await registration?.update();
    } catch (error) {
      console.warn('[nexus] service worker update check failed', error);
    }
  }

  async promptInstall() {
    if (this.standalone) {
      this.toasts.info('NEXUS از قبل نصب شده است');
      return false;
    }
    if (!this.deferredPrompt) {
      this.toasts.info(this.iOS
        ? 'در Safari روی «اشتراک‌گذاری» بزنید و «افزودن به صفحه اصلی» را انتخاب کنید'
        : 'برای نصب، از منوی مرورگر گزینه «نصب برنامه» را انتخاب کنید', 6200);
      return false;
    }
    this.deferredPrompt.prompt();
    const choice = await this.deferredPrompt.userChoice.catch(() => ({ outcome: 'dismissed' }));
    this.deferredPrompt = null;
    this.store.set('pwa', { installable: false, installed: choice?.outcome === 'accepted', platform: 'default' });
    if (choice?.outcome === 'accepted') this.toasts.ok('در حال نصب NEXUS…');
    return choice?.outcome === 'accepted';
  }

  renderButtons() {
    const pwa = this.store.get('pwa') || {};
    const label = pwa.installed ? 'نصب‌شده' : (pwa.installable ? 'نصب برنامه' : 'راهنمای نصب');
    $$('[data-pwa-install]').forEach((button) => {
      button.innerHTML = button.dataset.pwaInstall === 'compact'
        ? `<svg width="13" height="13"><use href="#i-download"/></svg> ${label}`
        : label;
      button.classList.toggle('done', !!pwa.installed);
      button.disabled = !!pwa.installed;
    });
    const hint = $('#pwaHint');
    if (hint) {
      hint.textContent = pwa.installed
        ? 'NEXUS در حالت برنامه (standalone) اجرا می‌شود.'
        : pwa.installable
          ? 'مرورگر آماده نصب است — دکمه «نصب برنامه» را بزنید.'
          : this.iOS
            ? 'روی iOS: Safari → دکمه اشتراک‌گذاری → «افزودن به صفحه اصلی».'
            : 'اگر دکمه نصب فعال نیست، از منوی مرورگر (⋮) گزینه Install app را انتخاب کنید.';
    }
  }

  static manifestLink() {
    const link = $('link[rel="manifest"]');
    return link ? link.href : '/manifest.webmanifest';
  }
}
