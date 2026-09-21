/* =============================================================================
   NEXUS · ui — modals, hand-rolled SVG charts and the shared status vocabulary.
   No chart library: the panel must render inside a Railway container with zero
   external requests, and an SVG string is smaller than any dependency.
   ========================================================================== */
import { $, $$, esc, ico, chev, Fmt } from './core.js';

/* ---------------------------------------------------------------- accordion */
/**
 * One collapsible card: a summary line with an icon, a title, a hint and an
 * optional right-hand figure, and a body that stays folded until it is needed.
 * Every long tab is built from these, so a page is a short stack of titled rows
 * instead of a wall of panels nobody scrolls to the bottom of.
 */
export const accordion = ({ id = '', icon = '', title, subtitle = '', meta = '', body = '', open = false, klass = '' }) => {
  const classes = ['acc', klass].filter(Boolean).join(' ');
  return `<details class="${classes}"${open ? ' open' : ''}${id ? ` id="${esc(id)}"` : ''}>`
    + `<summary>${icon ? `<span class="acc-ico">${ico(icon, 15)}</span>` : ''}`
    + `<span class="acc-t"><b>${esc(title)}</b>${subtitle ? `<span>${esc(subtitle)}</span>` : ''}</span>`
    + `${meta ? `<span class="acc-meta">${esc(meta)}</span>` : ''}${chev(15)}</summary>`
    + `<div class="acc-body">${body}</div></details>`;
};

/* ---------------------------------------------------------------------- modal */
export class Modal {
  constructor(backdrop, onClose) {
    this.el = backdrop;
    this.body = $('.modal-body', backdrop);
    this.onClose = onClose;
  }

  close() {
    this.el.style.animation = 'fadeIn .2s reverse';
    setTimeout(() => this.el.remove(), 180);
    document.removeEventListener('keydown', this.keyHandler);
    this.onClose?.();
  }

  bindKey(handler) { this.keyHandler = handler; }
}

export class ModalManager {
  constructor(root) { this.root = root; }

  open({ title, subtitle = '', body = '', footer = '', size = '' }) {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-back';
    backdrop.innerHTML = `<div class="modal ${size}" role="dialog" aria-modal="true">
      <div class="modal-head">
        <div><h3>${esc(title)}</h3>${subtitle ? `<p>${esc(subtitle)}</p>` : ''}</div>
        <button class="icon-btn modal-close" data-close aria-label="بستن">${ico('x', 16)}</button>
      </div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-foot">${footer}</div>` : ''}
    </div>`;
    this.root.appendChild(backdrop);
    const modal = new Modal(backdrop);
    const keyHandler = (event) => { if (event.key === 'Escape') modal.close(); };
    modal.bindKey(keyHandler);
    document.addEventListener('keydown', keyHandler);
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop || event.target.closest('[data-close]')) modal.close();
    });
    return modal;
  }

  ask(title, message, { confirmLabel = 'تأیید و حذف', cancelLabel = 'انصراف', size = 'slim' } = {}) {
    return new Promise((resolve) => {
      let answered = false;
      const modal = this.open({
        title,
        size,
        body: `<p class="muted" style="line-height:1.9;margin:0">${esc(message)}</p>`,
        footer: `<div class="actions" style="margin:0">
          <button class="danger-btn" data-yes>${esc(confirmLabel)}</button>
          <button class="secondary" data-close>${esc(cancelLabel)}</button></div>`,
      });
      $('[data-yes]', modal.el).onclick = () => { answered = true; resolve(true); modal.close(); };
      modal.el.addEventListener('click', (event) => {
        if (event.target.closest('[data-close]') || event.target === modal.el) {
          setTimeout(() => { if (!answered) resolve(false); }, 0);
        }
      });
    });
  }
}

/* ---------------------------------------------------------------- collapse */
/**
 * Fold every panel marked `data-acc="عنوان|زیرعنوان|آیکون"` into a collapsed card.
 * The panel itself is untouched — its ids, buttons and rendered content all stay
 * where they are — so a tab stops being a wall of boxes and becomes a short,
 * scannable stack of titled rows that open on demand.
 */
export function collapsePanels(root = document, { forceOpen = false } = {}) {
  $$('[data-acc]', root).forEach((panel) => {
    const [title, subtitle = '', icon = 'list'] = String(panel.dataset.acc).split('|');
    const pill = $('.panel-head .pill', panel);
    const summary = panel.ownerDocument.createElement('summary');
    summary.innerHTML = `<span class="acc-ico">${ico(icon, 15)}</span>`
      + `<span class="acc-t"><b>${esc(title)}</b>${subtitle ? `<span>${esc(subtitle)}</span>` : ''}</span>`
      + `${pill && pill.textContent.trim() ? `<span class="acc-meta">${esc(pill.textContent.trim())}</span>` : ''}`
      + chev(15);
    const body = panel.ownerDocument.createElement('div');
    body.className = 'acc-body';
    const details = panel.ownerDocument.createElement('details');
    details.className = `acc${panel.dataset.accKlass ? ` ${panel.dataset.accKlass}` : ''}`;
    if (forceOpen || panel.dataset.accOpen === '1') details.setAttribute('open', '');
    panel.parentNode.insertBefore(details, panel);
    panel.classList.add('acc-plain');
    body.appendChild(panel);
    details.appendChild(summary);
    details.appendChild(body);
  });
}

/* --------------------------------------------------------------------- charts */
let gradientSeq = 0;

export class Charts {
  static niceMax(value) { return value <= 0 ? 1 : value < 1 ? 1 : Math.ceil(value * 1.18); }

  static sparkPath(values, width, height) {
    if (!values.length) return '';
    const max = Math.max(...values, 0.0001);
    const min = Math.min(...values, 0);
    const span = max - min || 1;
    const step = values.length > 1 ? width / (values.length - 1) : 0;
    return values.map((v, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)} ${(height - ((v - min) / span) * height).toFixed(1)}`).join(' ');
  }

  static sparkline(values, color = '#c9f24c') {
    if (!values?.length) return '';
    const w = 120, h = 26;
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:100%">
      <path d="${this.sparkPath(values, w, h - 4)}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linecap="round" transform="translate(0,2)"/>
    </svg>`;
  }

  static smoothPath(points) {
    if (points.length < 2) return points.length ? `M${points[0].x} ${points[0].y}` : '';
    let d = `M${points[0].x} ${points[0].y}`;
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = points[i - 1] || points[i], p1 = points[i], p2 = points[i + 1], p3 = points[i + 2] || p2;
      const c1x = p1.x + (p2.x - p0.x) / 6.4, c1y = p1.y + (p2.y - p0.y) / 6.4;
      const c2x = p2.x - (p3.x - p1.x) / 6.4, c2y = p2.y - (p3.y - p1.y) / 6.4;
      d += ` C${c1x.toFixed(1)} ${c1y.toFixed(1)},${c2x.toFixed(1)} ${c2y.toFixed(1)},${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
    }
    return d;
  }

  static area(host, points, options = {}) {
    if (!host) return;
    const W = Math.max(host.clientWidth || 520, 280);
    const H = options.height || 210;
    const padL = 46, padR = 12, padT = 14, padB = 28;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    host.innerHTML = '';

    if (!points.length) {
      host.innerHTML = `<div class="empty">${ico('chart', 34)}<div>هنوز ترافیکی ثبت نشده است</div>
        <div class="muted" style="margin-top:6px">با اتصال اولین کلاینت، نمودار زنده می‌شود.</div></div>`;
      return;
    }
    const values = points.map((p) => Number(p.v) || 0);
    const max = this.niceMax(Math.max(...values));
    const step = points.length > 1 ? plotW / (points.length - 1) : 0;
    const pts = points.map((p, i) => ({
      x: padL + i * step,
      y: padT + plotH - ((Number(p.v) || 0) / max) * plotH,
      raw: p,
    }));
    const line = this.smoothPath(pts);
    const area = `${line} L${pts[pts.length - 1].x.toFixed(1)} ${padT + plotH} L${pts[0].x.toFixed(1)} ${padT + plotH} Z`;
    const gid = `grad${++gradientSeq}`;
    const accent = options.color || '#c9f24c';
    const accent2 = options.color2 || '#5fce62';

    const grid = [0, 0.5, 1].map((f) => {
      const y = padT + plotH - f * plotH;
      return `<line class="gridline" x1="${padL}" y1="${y.toFixed(1)}" x2="${padL + plotW}" y2="${y.toFixed(1)}"/>
        <text class="axis" x="${padL - 8}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${esc(options.axisFmt ? options.axisFmt(max * f) : String(Math.round(max * f)))}</text>`;
    }).join('');

    const ticks = [0, Math.floor((points.length - 1) / 2), points.length - 1]
      .filter((v, i, all) => all.indexOf(v) === i)
      .map((i) => `<text class="axis" x="${Fmt.clamp(pts[i].x, padL, padL + plotW).toFixed(1)}" y="${H - 8}" text-anchor="${i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'}">${esc(points[i].label || '')}</text>`)
      .join('');

    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
      <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${accent}" stop-opacity="0.42"/>
        <stop offset="60%" stop-color="${accent2}" stop-opacity="0.13"/>
        <stop offset="100%" stop-color="${accent2}" stop-opacity="0"/>
      </linearGradient></defs>
      ${grid}${ticks}
      <path d="${area}" fill="url(#${gid})" opacity="0"><animate attributeName="opacity" to="1" dur="0.7s" fill="freeze"/></path>
      <path class="line" id="chartLine" d="${line}" stroke="${accent}" style="filter:drop-shadow(0 5px 14px ${accent}55)"/>
      ${pts.map((p) => `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="2.1" fill="#0b1307" stroke="${accent}" stroke-width="1.3" opacity="0"><animate attributeName="opacity" to="1" dur="0.6s" begin="0.45s" fill="freeze"/></circle>`).join('')}
      <rect id="chartHit" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor:crosshair"/>
      <line id="chartCross" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="${accent}" stroke-opacity="0.4" stroke-dasharray="3 4" opacity="0"/>
    </svg>`;

    const lineEl = $('#chartLine', host);
    if (lineEl?.getTotalLength) {
      const length = lineEl.getTotalLength();
      lineEl.style.strokeDasharray = `${length}`;
      lineEl.style.strokeDashoffset = `${length}`;
      requestAnimationFrame(() => {
        lineEl.style.transition = 'stroke-dashoffset 1.15s cubic-bezier(.22,.68,.24,1)';
        lineEl.style.strokeDashoffset = '0';
      });
    }

    const tip = document.createElement('div');
    tip.className = 'tooltip';
    host.appendChild(tip);
    const cross = $('#chartCross', host);
    const hit = $('#chartHit', host);
    if (hit) {
      hit.addEventListener('pointermove', (event) => {
        const rect = host.getBoundingClientRect();
        const scale = W / rect.width;
        const x = (event.clientX - rect.left) * scale;
        const index = Fmt.clamp(Math.round((x - padL) / (step || 1)), 0, pts.length - 1);
        const point = pts[index];
        cross.setAttribute('x1', point.x);
        cross.setAttribute('x2', point.x);
        cross.setAttribute('opacity', '1');
        tip.style.left = `${point.x / scale}px`;
        tip.style.top = `${point.y / scale}px`;
        tip.style.opacity = '1';
        tip.innerHTML = `<b>${esc(point.raw.title || point.raw.label || '')}</b>${esc(options.valueFmt ? options.valueFmt(Number(point.raw.v) || 0) : String(point.raw.v))}`;
      });
      hit.addEventListener('pointerleave', () => { tip.style.opacity = '0'; cross.setAttribute('opacity', '0'); });
    }
  }

  static donut(host, segments, options = {}) {
    if (!host) return '';
    const total = segments.reduce((acc, s) => acc + s.value, 0);
    const size = options.size || 180, stroke = 17, r = (size - stroke) / 2, circumference = 2 * Math.PI * r;
    let offset = 0;
    const arcs = segments.map((segment) => {
      const fraction = total ? segment.value / total : 0;
      const dash = fraction * circumference;
      const arc = `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${segment.color}" stroke-width="${stroke}"
        stroke-dasharray="${dash.toFixed(2)} ${(circumference - dash).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}" stroke-linecap="butt" transform="rotate(-90 ${size / 2} ${size / 2})">
        <animate attributeName="stroke-width" from="0" to="${stroke}" dur="0.7s" fill="freeze"/></circle>`;
      offset += dash;
      return arc;
    }).join('');
    host.innerHTML = `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="${stroke}"/>
      ${arcs}
      <text x="${size / 2}" y="${size / 2 - 2}" text-anchor="middle" fill="#eaf4dc" font-family="Vazirmatn" font-size="22" font-weight="800">${esc(options.centerValue ?? String(total))}</text>
      <text x="${size / 2}" y="${size / 2 + 18}" text-anchor="middle" fill="#93a885" font-family="Vazirmatn" font-size="10.5">${esc(options.centerLabel || '')}</text>
    </svg>`;
    return segments.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)} · ${Fmt.num(s.value)}</span>`).join('');
  }

  static ring(pct, color) {
    const size = 78, stroke = 7, r = (size - stroke) / 2, circumference = 2 * Math.PI * r;
    const value = Fmt.clamp(pct, 0, 100);
    const dash = (value / 100) * circumference;
    return `<svg viewBox="0 0 ${size} ${size}">
      <circle class="ring-track" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${stroke}"/>
      <circle class="ring-val" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke="${color}" stroke-width="${stroke}"
        stroke-dasharray="${dash.toFixed(2)} ${(circumference - dash).toFixed(2)}" stroke-dashoffset="0"/></svg>`;
  }

  static bars(host, items) {
    if (!host) return;
    if (!items.length) {
      host.innerHTML = `<div class="empty">${ico('server', 32)}<div>موردی برای نمایش نیست</div></div>`;
      return;
    }
    host.innerHTML = items.map((item) => `
      <div class="bar-row">
        <div class="name">${item.icon || ''}<span style="overflow:hidden;text-overflow:ellipsis">${esc(item.label)}</span></div>
        <div class="val">${esc(item.value)}</div>
        <div class="bar-track"><div class="bar-fill ${item.tone || ''}" data-w="${Fmt.clamp(item.pct || 0, 2, 100)}%"></div></div>
      </div>`).join('');
    requestAnimationFrame(() => $$('.bar-fill', host).forEach((bar) => { bar.style.width = bar.dataset.w; }));
  }

  static countUp(el, target, digits = 0, suffix = '') {
    if (!el) return;
    const from = Number(el.dataset.v || 0);
    const to = Number(target) || 0;
    el.dataset.v = to;
    if (from === to) {
      el.innerHTML = `${Fmt.num(to, digits)}${suffix ? `<small>${esc(suffix)}</small>` : ''}`;
      return;
    }
    const duration = 750, start = performance.now();
    const tick = (now) => {
      const progress = Fmt.clamp((now - start) / duration, 0, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      el.innerHTML = `${Fmt.num(from + (to - from) * eased, digits)}${suffix ? `<small>${esc(suffix)}</small>` : ''}`;
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
}

/* ------------------------------------------------------- shared vocabulary */
export const STATUS = {
  active: { label: 'فعال', cls: 'ok' },
  off: { label: 'غیرفعال', cls: 'bad' },
  expired: { label: 'منقضی', cls: 'warn' },
  quota: { label: 'اتمام حجم', cls: 'warn' },
  request: { label: 'اتمام درخواست', cls: 'warn' },
  bootstrap: { label: 'شروع با اولین اتصال', cls: 'info' },
};

export const LOG_LABELS = {
  'auth.login': 'ورود مدیر', 'auth.logout': 'خروج مدیر',
  'user.create': 'ساخت کاربر', 'user.quick': 'ساخت سریع کاربر', 'user.delete': 'حذف کاربر',
  'user.update': 'ویرایش کاربر', 'user.reset': 'صفر کردن مصرف', 'user.toggle': 'تغییر وضعیت کاربر',
  'node.update': 'ویرایش نود', 'node.delete': 'حذف نود', 'node.bootstrap': 'ساخت خودکار نود',
  'nodes.sync': 'همگام‌سازی نودها', 'nodes.ping': 'پینگ نودها',
  'settings.update': 'تغییر تنظیمات', 'settings.clients': 'تنظیمات کلاینت‌ها',
  'settings.password': 'تغییر رمز', 'settings.session': 'ابطال یا تمدید نشست',
  'cloudflare.worker': 'تنظیم Worker', 'logs.clear': 'پاک‌سازی گزارش',
};

export class StatusKit {
  static of(user) {
    if (!user) return STATUS.off;
    if (!user.is_active) return STATUS.off;
    const now = Date.now() / 1000;
    if (user.start_on_first_connect && !user.first_connection_time) return STATUS.bootstrap;
    if (user.expires_at && now >= user.expires_at) return STATUS.expired;
    if (user.limit_gb != null && Number(user.used_gb) >= Number(user.limit_gb)) return STATUS.quota;
    if (user.limit_req != null && Number(user.used_req) >= Number(user.limit_req)) return STATUS.request;
    return STATUS.active;
  }

  static quotaPct(user) {
    return user?.limit_gb ? Fmt.clamp((Number(user.used_gb) || 0) / Number(user.limit_gb) * 100, 0, 100) : 0;
  }

  static toneForPct(pct) { return pct > 90 ? '#ff6b81' : pct > 70 ? '#ffc85c' : '#3ee6a0'; }

  static latencyTone(ms) { return ms == null || Number(ms) < 0 ? 'off' : ms < 120 ? 'good' : ms < 320 ? 'mid' : 'bad'; }

  // A probe stores -1 for "failed", so every latency read-out goes through here.
  static latencyText(ms) {
    return ms == null ? '—' : Number(ms) < 0 ? 'قطع' : `${Fmt.lat().format(Number(ms))} ms`;
  }

  static barTone(ms) { return ms == null ? '' : ms < 120 ? 'good' : ms < 320 ? 'mid' : 'bad'; }

  static logLabel(action) { return LOG_LABELS[action] || action || 'رویداد'; }
}
