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
    window.addEventListener('load', async () => {
      try {
        const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        registration.addEventListener('updatefound', () => {
          const installing = registration.installing;
          installing?.addEventListener('statechange', () => {
            if (installing.state === 'installed' && navigator.serviceWorker.controller) {
              this.updateReady = true;
              this.bus?.emit('pwa:update', { registration });
            }
          });
        });
      } catch (error) {
        // A service worker is a nice-to-have: never let it break the panel.
        console.warn('[nexus] service worker registration failed', error);
      }
    });
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
