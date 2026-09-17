/* =============================================================================
   NEXUS · core — DOM helpers, formatting, toasts and the event bus.

   The panel is ES-module object-oriented code: every module exports classes,
   nothing writes to a shared global scope. That removes the whole class of
   "duplicate declaration" crashes the old five-script bundle could hit, and it
   keeps each file small enough to reason about.
   ========================================================================== */
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export const ico = (name, size = 16) =>
  `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/** Safe storage: private mode and blocked third-party storage throw on access. */
export class SafeStorage {
  constructor(kind = 'localStorage') {
    this.kind = kind;
    this.memory = new Map();
    this.available = this._probe();
  }

  _probe() {
    try {
      const store = window[this.kind];
      const probe = '__nexus_probe__';
      store.setItem(probe, '1');
      store.removeItem(probe);
      return true;
    } catch (error) {
      return false;
    }
  }

  get(key) {
    if (!this.available) return this.memory.get(key) ?? null;
    try { return window[this.kind].getItem(key); } catch (error) { return this.memory.get(key) ?? null; }
  }

  set(key, value) {
    this.memory.set(key, value);
    if (!this.available) return false;
    try { window[this.kind].setItem(key, value); return true; } catch (error) { return false; }
  }

  remove(key) {
    this.memory.delete(key);
    if (!this.available) return;
    try { window[this.kind].removeItem(key); } catch (error) { /* ignore */ }
  }
}

/** Persian-aware number, size, time and duration formatting. */
export class Fmt {
  static fa(digits = 0) {
    this._cache = this._cache || {};
    if (!this._cache[digits]) {
      this._cache[digits] = new Intl.NumberFormat('fa-IR', { maximumFractionDigits: digits });
    }
    return this._cache[digits];
  }

  static lat(digits = 1) {
    this._lat = this._lat || new Intl.NumberFormat('en-US', { maximumFractionDigits: digits });
    return this._lat;
  }

  static num(value, digits = 0) {
    const n = Number(value);
    return this.fa(digits).format(Number.isFinite(n) ? n : 0);
  }

  static pad(n) { return String(n).padStart(2, '0'); }

  static clamp(value, low, high) { return Math.min(Math.max(value, low), high); }

  static size(gb) {
    const v = Number(gb) || 0;
    if (v >= 1024) return { v: this.num(v / 1024, 2), u: 'TB' };
    if (v >= 1) return { v: this.num(v, 2), u: 'GB' };
    if (v > 0) return { v: this.num(v * 1024, 1), u: 'MB' };
    return { v: this.num(0), u: 'GB' };
  }

  static sizeText(gb) { const s = this.size(gb); return `${s.v} ${s.u}`; }

  static until(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    if (s === 0) return 'پایان یافته';
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${this.num(d)} روز و ${this.num(h)} ساعت`;
    if (h > 0) return `${this.num(h)} ساعت و ${this.num(m)} دقیقه`;
    if (m > 0) return `${this.num(m)} دقیقه`;
    return `${this.num(s)} ثانیه`;
  }

  static ago(ts) {
    const s = Math.floor(Date.now() / 1000) - Number(ts || 0);
    if (!Number.isFinite(s) || s < 0) return '—';
    if (s < 60) return 'لحظه‌ای پیش';
    if (s < 3600) return `${this.num(Math.floor(s / 60))} دقیقه پیش`;
    if (s < 86400) return `${this.num(Math.floor(s / 3600))} ساعت پیش`;
    return `${this.num(Math.floor(s / 86400))} روز پیش`;
  }

  static #short() {
    this._short = this._short || {
      t: new Intl.DateTimeFormat('fa-IR-u-nu-latn', { hour: '2-digit', minute: '2-digit', hour12: false }),
      d: new Intl.DateTimeFormat('fa-IR', { day: 'numeric', month: 'short' }),
    };
    return this._short;
  }

  static dateTime(ts) {
    if (!ts) return '—';
    const { t, d } = this.#short();
    const date = new Date(ts * 1000);
    return `${t.format(date)} · ${d.format(date)}`;
  }

  static shortTime(ts) { return this.#short().t.format(new Date(ts * 1000)); }

  static shortDate(ts) { return this.#short().d.format(new Date(ts * 1000)); }

  static #iran() {
    this._iran = this._iran || {
      time: new Intl.DateTimeFormat('fa-IR-u-nu-latn', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Tehran' }),
      date: new Intl.DateTimeFormat('fa-IR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Asia/Tehran' }),
    };
    return this._iran;
  }

  static clock(date = new Date()) { return this.#iran().time.format(date); }

  static clockDate(date = new Date()) { return this.#iran().date.format(date); }

  static utc(date = new Date()) { return `${this.pad(date.getUTCHours())}:${this.pad(date.getUTCMinutes())}`; }

  static truncate(value, length) {
    const text = String(value ?? '');
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  }
}

/** Toast notifications with de-duplication (an offline server must not flood). */
export class ToastCenter {
  constructor(host, { dedupeWindow = 6000, max = 4 } = {}) {
    this.host = host;
    this.dedupeWindow = dedupeWindow;
    this.max = max;
    this.seen = new Map();
  }

  show(message, kind = 'info', ttl = 4200) {
    if (!this.host) return;
    const text = String(message ?? '').trim();
    if (!text) return;
    const fingerprint = `${kind}:${text}`;
    const now = Date.now();
    if (now - (this.seen.get(fingerprint) || 0) < this.dedupeWindow) return;
    this.seen.set(fingerprint, now);
    if (this.seen.size > 60) this.seen.clear();

    const glyph = kind === 'ok' ? 'check' : kind === 'err' ? 'alert' : 'activity';
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `<span class="ic">${ico(glyph, 12)}</span><span>${esc(text)}</span>`;
    this.host.appendChild(el);
    while (this.host.children.length > this.max) this.host.firstElementChild.remove();
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 320); }, ttl);
  }

  ok(message, ttl) { this.show(message, 'ok', ttl); }

  err(message, ttl) { this.show(message, 'err', ttl); }

  info(message, ttl) { this.show(message, 'info', ttl); }
}

/** Minimal typed event bus so views never reach into each other. */
export class EventBus {
  constructor() { this.listeners = new Map(); }

  on(event, handler) {
    const list = this.listeners.get(event) || [];
    list.push(handler);
    this.listeners.set(event, list);
    return () => this.off(event, handler);
  }

  off(event, handler) {
    const list = (this.listeners.get(event) || []).filter((h) => h !== handler);
    this.listeners.set(event, list);
  }

  emit(event, payload) {
    (this.listeners.get(event) || []).slice().forEach((handler) => {
      try { handler(payload); } catch (error) { console.warn('[nexus] listener failed', event, error); }
    });
  }
}

export async function copyText(value, button, toasts) {
  const text = String(value ?? '');
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    if (button) {
      button.classList.add('done');
      button.innerHTML = ico('check', 14);
      setTimeout(() => { button.classList.remove('done'); button.innerHTML = ico('copy', 14); }, 1500);
    }
    toasts?.ok('لینک در کلیپ‌بورد کپی شد', 2200);
    return true;
  } catch (error) {
    toasts?.err('کپی نشد؛ لینک را دستی انتخاب کنید');
    return false;
  }
}

export function bindCopyButtons(root, toasts) {
  $$('[data-copy]', root).forEach((button) => {
    button.onclick = () => copyText(button.dataset.copy, button, toasts);
  });
}
