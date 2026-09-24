/* =============================================================================
   NEXUS · view — Telegram proxies.

   Three products on one tab, because a user who says «تلگرام کار نمی‌کند» wants
   one of exactly three things:

   * an **MTProto proxy** — the ``tg://`` link pasted straight into the app, so
     nothing has to be installed. Served by ``mtg`` (FakeTLS), one secret, and
     the link only appears here once the listener is really up;
   * an **HTTP/SOCKS5 web proxy** — what Telegram Desktop calls «custom proxy»,
     with one account per user, served by the Xray engine the panel already runs;
   * **web.telegram.org** from this deployment's own domain, so the web version
     opens where the site itself is blocked (the Cloudflare Worker in front of the
     panel carries the same path).

   Nothing here is a VPN node: the rows show state, the buttons store it, and a
   feature that cannot be reached says why instead of handing out a dead link.
   ========================================================================== */
import { $, $$, ico, esc, Fmt, bindCopyButtons } from '../core.js';

export class TelegramView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  async load() {
    this.store.set('telegram', await this.api.get('/api/telegram'));
    this.render();
  }

  render() {
    const data = this.store.get('telegram');
    if (!data) return;
    this.renderMode(data);
    this.renderMtproto(data.mtproto || {});
    this.renderWebRelay(data.webrelay || {});
    this.renderWeb(data.webproxy || {});
    this.renderApp(data.webapp || {});
    this.renderNotes(data);
  }

  /* -------------------------------------------------------------- the mode */
  /* A user is handed exactly one kind of Telegram proxy unless an admin asks for
     the rest: «one kind» is one thing to explain, one thing to rotate and one
     thing to support. The cards the mode withholds are hidden rather than removed,
     so switching back to «همهٔ انواع» loses nothing. */
  renderMode(data) {
    const only = (data.mode || 'web') !== 'all';
    const select = $('#tgMode');
    if (select && document.activeElement !== select) select.value = only ? 'web' : 'all';
    const tag = $('#tgModeTag');
    if (tag) {
      tag.className = `pill ${only ? 'ok' : 'warn'}`;
      tag.innerHTML = only ? '<i class="dot"></i> فقط WEB' : 'همهٔ انواع';
    }
    ['#tgOtherTypes', '#tgOtherWeb'].forEach((selector) => {
      const el = $(selector);
      if (el) el.style.display = only ? 'none' : '';
    });
    const info = $('#tgModeInfo');
    if (info) {
      info.textContent = only
        ? 'به هر کاربر فقط لینک tg://webproxy داده می‌شود؛ خطوط MTProto، وب‌پروکسی HTTP/SOCKS5 و نسخهٔ وب تلگرام منتشر نمی‌شوند. روی Railway همین حالت درست است، چون هیچ پورت خام لازم ندارد.'
        : 'هر سه نوع پروکسی منتشر می‌شود. برای MTProto و وب‌پروکسی، پورت خام باید واقعاً فوروارد شده باشد (روی Railway یک TCP Proxy، روی VPS خود پورت).';
    }
  }

  /* -------------------------------------------------------------- WEB proxy */
  /* Telegram Desktop 7.1+'s WEB type: MTProto inside an ordinary HTTPS page and
     a same-origin WebSocket, so it rides the panel's own 443 and needs no raw
     TCP port. The card is first because it is the one that works on a forwarder
     (Railway, a CDN, the Worker) without any extra setup. */
  renderWebRelay(data) {
    const set = (selector, value) => {
      const el = $(selector);
      if (el && document.activeElement !== el) el.value = value ?? '';
    };
    set('#tgRelayDomain', data.domain || '');
    set('#tgRelaySessions', data.sessions);
    set('#tgRelayStreams', data.streams);
    const toggle = $('#tgRelayEnabled');
    if (toggle) toggle.classList.toggle('on', !!data.enabled);
    const tag = $('#tgRelayTag');
    if (tag) {
      tag.className = `pill ${data.published ? 'ok' : (data.enabled ? 'warn' : '')}`;
      tag.innerHTML = data.published
        ? '<i class="dot"></i> منتشرشده'
        : (data.enabled ? 'منتشر نشده' : 'خاموش');
    }
    const info = $('#tgRelayInfo');
    if (!info) return;
    const state = data.installed
      ? (data.running ? `Running${data.relay_pid ? ` · relay PID ${Fmt.num(data.relay_pid)}` : ''}` : 'خاموش')
      : `باینری نصب نیست (${esc(data.binary)})`;
    info.innerHTML = `
      <div class="kv-line"><span>وضعیت سرویس</span><b dir="ltr">${esc(state)}</b></div>
      <div class="kv-line"><span>موتور</span><b dir="ltr">${esc(data.engine || '—')}</b></div>
      <div class="kv-line"><span>دامنه و مسیر</span><b dir="ltr">${data.domain ? `${esc(data.domain)} · ${esc(data.url || '')}` : '—'}</b></div>
      <div class="kv-line"><span>پورت‌های لوپ‌بک</span><b dir="ltr">relay ${Fmt.num(data.port)} → backend ${Fmt.num(data.backend_port)}</b></div>
      <div class="kv-line"><span>سوکت حمل</span><b dir="ltr">${esc(data.ws_path || '')}</b></div>
      <div class="kv-line"><span>سقف اتصال</span><b dir="ltr">${Fmt.num(data.max_connections)}</b></div>
      <div class="kv-line"><span>secret</span><b dir="ltr" style="white-space:normal;word-break:break-all">${esc(data.link_secret || '—')}</b></div>
      ${data.reason ? `<div class="kv-line"><span>دلیل منتشر نشدن</span><b style="white-space:normal">${esc(data.reason)}</b></div>` : ''}`;
    const links = $('#tgRelayLinks');
    if (!links) return;
    if (!data.links) {
      links.innerHTML = '<p class="muted">تا وقتی relay بالا نباشد لینکی ساخته نمی‌شود؛ دلیلش در ردیف‌های بالا نوشته شده است.</p>';
      return;
    }
    links.innerHTML = `
      <div class="link-box">
        <div class="lb-main"><b>لینک WEB (tg://webproxy)</b><code>${esc(data.links.tg)}</code></div>
        <button class="copy-btn" data-copy="${esc(data.links.tg)}" title="کپی لینک">${ico('copy', 14)}</button>
      </div>
      <div class="link-box">
        <div class="lb-main"><b>لینک قابل اشتراک (t.me/webproxy)</b><code>${esc(data.links.tme)}</code></div>
        <button class="copy-btn" data-copy="${esc(data.links.tme)}" title="کپی لینک">${ico('copy', 14)}</button>
        <a class="tbtn" href="${esc(data.links.tme)}" target="_blank" rel="noopener">باز کردن در تلگرام</a>
      </div>
      <p class="muted">کاربر در تلگرام دسکتاپ ۷.۱ یا بالاتر: تنظیمات → پیشرفته → نوع اتصال → افزودن پروکسی → نوع <b>WEB</b>، بعد همین لینک را اضافه می‌کند. پیام و مدیا از این سرور رد می‌شوند؛ بقیهٔ ترافیک دست‌نخورده می‌ماند.</p>`;
    bindCopyButtons(links, this.toasts);
  }

  /* ------------------------------------------------------------- MTProto */
  renderMtproto(data) {
    const set = (selector, value) => {
      const el = $(selector);
      if (el && document.activeElement !== el) el.value = value ?? '';
    };
    set('#tgMtPort', data.port);
    set('#tgMtDomain', data.domain || '');
    set('#tgMtConcurrency', data.concurrency);
    set('#tgMtDns', data.dns || '');
    set('#tgMtFrontIp', data.front_ip || '');
    const toggle = $('#tgMtEnabled');
    if (toggle) toggle.classList.toggle('on', !!data.enabled);
    const tag = $('#tgMtTag');
    if (tag) {
      tag.className = `pill ${data.published ? 'ok' : (data.enabled ? 'warn' : '')}`;
      tag.innerHTML = data.published
        ? '<i class="dot"></i> منتشرشده'
        : (data.enabled ? 'منتشر نشده' : 'خاموش');
    }
    const info = $('#tgMtInfo');
    if (info) {
      info.innerHTML = `
        <div class="kv-line"><span>وضعیت سرویس</span><b dir="ltr">${data.installed
          ? (data.running ? `Running${data.pid ? ` · PID ${Fmt.num(data.pid)}` : ''}` : 'خاموش')
          : `باینری نصب نیست (${esc(data.binary)})`}</b></div>
        <div class="kv-line"><span>آدرس انتشار</span><b dir="ltr">${data.host ? `${esc(data.host)}:${Fmt.num(data.port)}` : '—'}</b></div>
        <div class="kv-line"><span>FakeTLS / SNI</span><b dir="ltr">${esc(data.domain || '—')}</b></div>
        <div class="kv-line"><span>secret</span><b dir="ltr" style="white-space:normal;word-break:break-all">${esc(data.secret || '—')}</b></div>
        ${data.reason ? `<div class="kv-line"><span>دلیل منتشر نشدن</span><b style="white-space:normal">${esc(data.reason)}</b></div>` : ''}`;
    }
    const links = $('#tgMtLinks');
    if (!links) return;
    if (!data.links) {
      links.innerHTML = '<p class="muted">تا وقتی پروکسی منتشر نشده باشد، لینکی ساخته نمی‌شود — دلیلش در ردیف‌های بالا نوشته شده است.</p>';
      return;
    }
    links.innerHTML = `
      <div class="link-box">
        <div class="lb-main"><b>لینک تلگرام (tg://)</b><code>${esc(data.links.tg)}</code></div>
        <button class="copy-btn" data-copy="${esc(data.links.tg)}" title="کپی لینک">${ico('copy', 14)}</button>
      </div>
      <div class="link-box">
        <div class="lb-main"><b>لینک t.me (قابل اشتراک‌گذاری)</b><code>${esc(data.links.tme)}</code></div>
        <button class="copy-btn" data-copy="${esc(data.links.tme)}" title="کپی لینک">${ico('copy', 14)}</button>
        <a class="tbtn" href="${esc(data.links.tme)}" target="_blank" rel="noopener">باز کردن در تلگرام</a>
      </div>
      <p class="muted">کاربر این لینک را در تلگرام خودش اضافه می‌کند (تنظیمات → داده و حافظه → پروکسی، یا مستقیم روی لینک بزند). پیام‌ها و مدیا از این سرور رد می‌شوند؛ بقیهٔ ترافیک دست‌نخورده می‌ماند.</p>`;
    bindCopyButtons(links, this.toasts);
  }

  /* -------------------------------------------------------- web proxy */
  renderWeb(data) {
    const host = $('#tgWebHost');
    if (host) host.textContent = data.host ? `${data.host} · ${Fmt.num(data.counts?.accounts || 0)} کاربر` : 'آدرس عمومی پیدا نشد';
    const container = $('#tgWebProfiles');
    if (container) {
      container.innerHTML = (data.catalog || []).map((item) => `
        <div class="setting-row${item.published ? ' ok' : ''}">
          <div class="txt">
            <b>${esc(item.tag)}${item.published ? ' · منتشرشده' : (item.enabled ? ' · منتشر نشده' : '')}</b>
            <p>${esc(item.note)}</p>
            ${item.enabled && !item.reachable ? `<p class="muted" style="margin-top:4px">${esc(item.reason)}</p>` : ''}
          </div>
          <div class="actions" style="margin:0;gap:6px;flex-wrap:wrap">
            <input id="tgWebPort-${esc(item.id)}" type="number" min="1" max="65535" value="${Fmt.num(item.port)}" dir="ltr" style="width:98px" title="پورت عمومی">
            <div class="switch${item.enabled ? ' on' : ''}" id="tgWebToggle-${esc(item.id)}" data-tg-toggle="${esc(item.id)}"></div>
          </div>
        </div>`).join('') || '<div class="empty">وب‌پروکسی تعریف نشده است.</div>';
      $$('[data-tg-toggle]', container).forEach((element) => {
        element.onclick = () => element.classList.toggle('on');
      });
    }
    const table = $('#tgWebAccounts');
    if (!table) return;
    const lines = data.lines || [];
    if (!lines.length) {
      table.innerHTML = '<div class="empty">هنوز خط پروکسی منتشر نشده است. یکی از دو پروکسی بالا را روشن کنید و «ذخیره و انتشار» را بزنید.</div>';
      return;
    }
    table.innerHTML = `
      <div class="table-wrap"><table>
        <thead><tr><th>کاربر</th><th>نام کاربری</th><th>رمز</th><th>خط پروکسی</th><th></th></tr></thead>
        <tbody>${lines.map((item) => `
          <tr>
            <td>${esc(item.username)}</td>
            <td class="mono" dir="ltr">${esc(item.username)}</td>
            <td class="mono" dir="ltr">${esc(item.password.slice(0, 8))}…</td>
            <td class="mono" dir="ltr" style="white-space:normal;word-break:break-all">${esc(item.url)}</td>
            <td><button class="copy-btn" data-copy="${esc(item.url)}" title="کپی خط ${esc(item.label)}">${ico('copy', 14)}</button></td>
          </tr>`).join('')}
        </tbody>
      </table></div>
      <p class="muted">هر کاربر نام کاربری و رمز خودش را دارد؛ در تلگرام دسکتاپ: تنظیمات → پیشرفته → نوع اتصال → استفاده از پروکسی سفارشی (HTTP یا SOCKS5).</p>`;
    bindCopyButtons(table, this.toasts);
  }

  /* ---------------------------------------------------------- web app */
  renderApp(data) {
    const toggle = $('#tgAppEnabled');
    if (toggle) toggle.classList.toggle('on', !!data.enabled);
    const tag = $('#tgAppTag');
    if (tag) {
      tag.className = `pill ${data.enabled ? 'ok' : ''}`;
      tag.innerHTML = data.enabled ? '<i class="dot"></i> روشن' : 'خاموش';
    }
    const info = $('#tgAppInfo');
    if (info) {
      info.innerHTML = `
        <div class="kv-line"><span>مسیر روی همین دامنه</span><b dir="ltr">${esc(data.path || '/tg')}/</b></div>
        <div class="kv-line"><span>آدرس کامل</span><b dir="ltr" style="white-space:normal;word-break:break-all">${esc(data.url || '—')}</b></div>
        <div class="kv-line"><span>سرویس مبدأ</span><b dir="ltr">${esc(data.origin || '')}</b></div>
        <div class="kv-line"><span>هاست‌های مجاز</span><b dir="ltr" style="white-space:normal">${esc((data.hosts || []).join(' · '))}</b></div>
        ${this.store.get('telegramProbe') ? `<div class="kv-line"><span>آخرین تست</span><b style="white-space:normal">${esc(this.store.get('telegramProbe'))}</b></div>` : ''}`;
    }
    const open = $('#tgAppOpen');
    if (open) {
      if (data.enabled && data.url) {
        open.href = data.url;
        open.removeAttribute('aria-disabled');
      } else {
        open.removeAttribute('href');
        open.setAttribute('aria-disabled', 'true');
      }
    }
    const worker = $('#tgAppWorker');
    if (worker) {
      const url = this.app.workerSettings()?.url || '';
      worker.innerHTML = url
        ? `پشت ورکر کلودفلر هم همین مسیر کار می‌کند: <a class="tbtn" href="${esc(url.replace(/\/$/, ''))}${esc(data.path || '/tg')}/" target="_blank" rel="noopener">${esc(url.replace(/\/$/, ''))}${esc(data.path || '/tg')}/</a>`
        : 'اگر دامنهٔ پنل خودتان بسته است، آدرس Worker را در تب Cloudflare ثبت کنید؛ همان مسیر از دامنهٔ Worker هم باز می‌شود.';
    }
  }

  renderNotes(data) {
    const notes = $('#tgNotes');
    if (!notes) return;
    notes.innerHTML = (data.notes || []).map((note) =>
      `<div class="kv-line"><span>نکته</span><b style="white-space:normal">${esc(note)}</b></div>`).join('');
  }

  /* ------------------------------------------------------------ actions */
  payload() {
    const data = this.store.get('telegram') || {};
    const profiles = {};
    (data.webproxy?.catalog || []).forEach((item) => {
      profiles[item.id] = {
        enabled: $(`#tgWebToggle-${item.id}`)?.classList.contains('on') ? '1' : '0',
        port: Number($(`#tgWebPort-${item.id}`)?.value || item.port),
      };
    });
    return {
      mode: $('#tgMode')?.value || data.mode || 'web',
      mtproto: {
        enabled: $('#tgMtEnabled')?.classList.contains('on') ? '1' : '0',
        port: Number($('#tgMtPort')?.value || data.mtproto?.port || 8446),
        domain: $('#tgMtDomain')?.value.trim() || '',
        concurrency: Number($('#tgMtConcurrency')?.value || data.mtproto?.concurrency || 8192),
        dns: $('#tgMtDns')?.value.trim() || '',
        front_ip: $('#tgMtFrontIp')?.value.trim() || '',
      },
      webrelay: {
        enabled: $('#tgRelayEnabled')?.classList.contains('on') ? '1' : '0',
        domain: $('#tgRelayDomain')?.value.trim() || '',
        sessions: Number($('#tgRelaySessions')?.value || data.webrelay?.sessions || 6),
        streams: Number($('#tgRelayStreams')?.value || data.webrelay?.streams || 32),
      },
      webproxy: profiles,
      webapp: { enabled: $('#tgAppEnabled')?.classList.contains('on') ? '1' : '0' },
    };
  }

  async save(action = 'save') {
    const payload = this.payload();
    payload.action = action;
    const data = await this.api.post('/api/telegram', payload);
    this.store.set('telegram', data);
    this.render();
    const failure = Object.entries(data.sync || {})
      .map(([name, item]) => (item.reason ? `${name}: ${item.reason}` : ''))
      .find((line) => line);
    if (failure) this.toasts.err(failure, 9000);
    else this.toasts.ok(action === 'reload' ? 'پروکسی‌های تلگرام همگام شدند' : 'تنظیمات پروکسی تلگرام ذخیره شد');
    await this.app.reloadNodes();
  }

  async rotate() {
    const data = await this.api.post('/api/telegram', { action: 'rotate' });
    this.store.set('telegram', data);
    this.render();
    this.toasts.ok('secret تازه ساخته شد — لینک‌های قبلی از کار افتادند', 6000);
  }

  async rotateRelay() {
    const data = await this.api.post('/api/telegram', { action: 'rotate-webrelay' });
    this.store.set('telegram', data);
    this.render();
    this.toasts.ok('secret پروکسی WEB تازه شد — لینک‌های قبلی از کار افتادند', 6000);
  }

  async probe() {
    const button = $('#tgAppProbe');
    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="spin-inline"></span> در حال تست…';
    }
    try {
      const data = await this.api.post('/api/telegram/probe', {});
      const result = data.probe || {};
      this.store.set('telegramProbe', result.ok
        ? `موفق — ${Fmt.num(result.status)} · ${Fmt.num(result.bytes)} بایت از ${result.url}`
        : `ناموفق — ${result.error || 'پاسخ نامعتبر'}`);
      this.store.set('telegram', data);
      this.render();
      (result.ok ? this.toasts.ok : this.toasts.err)(
        result.ok ? 'این سرور می‌تواند تلگرام وب را بگیرد' : `تلگرام وب از این سرور در دسترس نیست (${result.error || result.status})`, 8000);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = 'تست دسترسی سرور';
      }
    }
  }

  bindEvents() {
    const modeSave = $('#tgModeSave');
    if (modeSave) modeSave.onclick = () => this.app.safe(() => this.save());
    const relay = $('#tgRelayEnabled');
    if (relay) relay.onclick = () => relay.classList.toggle('on');
    const relaySave = $('#tgRelaySave');
    if (relaySave) relaySave.onclick = () => this.app.safe(() => this.save());
    const relayRotate = $('#tgRelayRotate');
    if (relayRotate) relayRotate.onclick = () => this.app.safe(() => this.rotateRelay());
    const mtproto = $('#tgMtEnabled');
    if (mtproto) mtproto.onclick = () => mtproto.classList.toggle('on');
    const webapp = $('#tgAppEnabled');
    if (webapp) webapp.onclick = () => webapp.classList.toggle('on');
    const save = $('#tgSave');
    if (save) save.onclick = () => this.app.safe(() => this.save());
    const reload = $('#tgReload');
    if (reload) reload.onclick = () => this.app.safe(() => this.save('reload'));
    const rotate = $('#tgMtRotate');
    if (rotate) rotate.onclick = () => this.app.safe(() => this.rotate());
    const off = $('#tgMtOff');
    if (off) off.onclick = () => this.app.safe(async () => {
      $('#tgMtEnabled')?.classList.remove('on');
      await this.save();
    });
    const probe = $('#tgAppProbe');
    if (probe) probe.onclick = () => this.app.safe(() => this.probe());
  }
}
