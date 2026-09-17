/* =============================================================================
   NEXUS · session — who is signed in, and what happens when that ends.

   A panel admin got signed out constantly on a phone: the session cookie is
   third-party-blocked inside an embedded preview pane and the tab-scoped token
   died with the tab. This class therefore keeps three fallbacks in order
   (cookie → tab token → remembered token), and when the session really is over
   it raises one event instead of letting every poll throw its own error.
   ========================================================================== */
import { SafeStorage } from './core.js';

export class SessionManager {
  static KEY = 'nexus_session';

  constructor({ bus, rememberDays = 30 } = {}) {
    this.bus = bus;
    this.local = new SafeStorage('localStorage');
    this.tab = new SafeStorage('sessionStorage');
    this.rememberDays = rememberDays;
    this.token = '';
    this.expired = false;
    this.remembered = false;
    this.lastError = null;
  }

  /** Pick up a token handed over in the URL, then any stored token. */
  boot() {
    const incoming = this.#fromUrl();
    if (incoming) this.save(incoming, { remember: true, silent: true });
    else this.token = this.tab.get(SessionManager.KEY) || this.local.get(SessionManager.KEY) || '';
    return this.token;
  }

  #fromUrl() {
    try {
      const url = new URL(location.href);
      const token = url.searchParams.get('token');
      if (!token) return '';
      url.searchParams.delete('token');
      url.searchParams.delete('source');
      history.replaceState(null, '', url.pathname + (url.search || '') + url.hash);
      return token;
    } catch (error) {
      return '';
    }
  }

  save(token, { remember = false, silent = false } = {}) {
    this.token = String(token || '');
    this.remembered = !!remember;
    this.expired = false;
    this.lastError = null;
    if (!this.token) return;
    // The tab copy keeps working even when persistent storage is blocked;
    // the durable copy is what survives a phone reload.
    this.tab.set(SessionManager.KEY, this.token);
    if (remember) this.local.set(SessionManager.KEY, this.token);
    else this.local.remove(SessionManager.KEY);
    if (!silent) this.bus?.emit('session:changed', { token: this.token });
  }

  clear() {
    this.token = '';
    this.remembered = false;
    this.tab.remove(SessionManager.KEY);
    this.local.remove(SessionManager.KEY);
    this.bus?.emit('session:changed', { token: '' });
  }

  get active() { return !!this.token; }

  headers() { return this.token ? { 'X-Nexus-Session': this.token } : {}; }

  /** Called by the API client exactly once per expiry. */
  markExpired(reason = 'expired') {
    const first = !this.expired;
    this.expired = true;
    this.lastError = reason;
    if (first) {
      this.bus?.emit('session:lost', { reason });
    }
    return first;
  }

  /** Is the HttpOnly cookie enough on its own (no header fallback needed)? */
  async cookieWorks() {
    try {
      const response = await fetch('/api/nodes', { credentials: 'same-origin', headers: {} });
      return response.status !== 401;
    } catch (error) {
      return false;
    }
  }
}
