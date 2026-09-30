/* =============================================================================
   NEXUS · view — advanced subscriptions.

   The admin surface for a subscription fleet: a usage report, expiry/quota
   alerts, per-user link management (rotate a leaked token) and per-client
   fine-tuning (which subscription target each client receives). Every number is
   computed on the server (app/subscriptions/reports.py), so this file only
   paints it and wires the four actions.
   ========================================================================== */
import { $, $$, ico, esc, Fmt, bindCopyButtons } from '../core.js';

const SORTS = [
  { id: 'usage', label: 'بیشترین مصرف' },
  { id: 'expiry', label: 'نزدیک‌ترین انقضا' },
  { id: 'name', label: 'نام کاربر' },
];

const ALERT_LABELS = {
  quota: 'نزدیک/بالای سهمیه',
  expired: 'منقضی‌شده',
  expiring: 'در آستانهٔ انقضا',
};

const STATUS = {
  ok: { label: 'فعال', cls: 'ok' },
  quota: { label: 'سهمیه تمام', cls: 'warn' },
  expired: { label: 'منقضی', cls: 'bad' },
  expiring: { label: 'نزدیک انقضا', cls: 'warn' },
  disabled: { label: 'غیرفعال', cls: 'bad' },
};

export class SubscriptionsView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  async load() {
    const sort = this.store.get('subsSort') || 'usage';
    const user = this.store.get('subsUser') || '';
    const query = `sort=${encodeURIComponent(sort)}${user ? `&user=${encodeURIComponent(user)}` : ''}`;
    try {
      this.store.set('subscriptionReport', await this.api.get(`/api/subscription/report?${query}`));
    } catch (error) {
      this.store.set('subscriptionReport', null);
      if (!error.unauthorized) throw error;
    }
    this.render();
  }

  /* ------------------------------------------------------------------ render */
  render() {
    const report = this.store.get('subscriptionReport');
    if (!report) return;
    this.renderAlerts(report);
    this.renderTotals(report);
    this.renderThresholds(report);
    this.renderClients(report);
    this.renderTable(report);
  }

  renderAlerts(report) {
    const host = $('#subsAlerts');
    if (!host) return;
    const alerts = report.alerts || [];
    const pill = $('#subsAlertPill');
    if (pill) {
      pill.className = alerts.length ? 'pill warn' : 'pill ok';
      pill.textContent = alerts.length ? `${Fmt.num(alerts.length)} هشدار` : 'بدون هشدار';
    }
    if (!alerts.length) {
      host.innerHTML = `<div class="empty">${ico('check', 22)}<div>هیچ کاربری نزدیک انقضا یا بالای سهمیه نیست</div></div>`;
      return;
    }
    host.innerHTML = `<div class="panel-head" style="margin-bottom:8px"><div><h3>هشدارهای سابلینک</h3>
      <p class="sub">${Fmt.num(alerts.length)} کاربر نیاز به بررسی دارند — بر اساس آستانه‌های زیر</p></div></div>
      <div class="grid-2">${alerts.slice(0, 8).map((item) => `
        <div class="setting-row${item.alerts.includes('expired') || item.alerts.includes('quota') ? ' warn' : ''}">
          <div class="txt"><b>${esc(item.username)}</b>
            <p>${item.alerts.map((kind) => esc(ALERT_LABELS[kind] || kind)).join(' · ')}
              ${item.percent != null ? ` — ${Fmt.num(item.percent)}٪ سهمیه` : ''}
              ${item.days_left != null ? ` — ${item.days_left < 0 ? `${Fmt.num(-item.days_left)} روز گذشته` : `${Fmt.num(item.days_left)} روز مانده`}` : ''}</p></div>
          <button class="tbtn" data-goto-user="${esc(item.username)}">${ico('users', 12)} کاربر</button>
        </div>`).join('')}</div>`;
    $$('[data-goto-user]', host).forEach((button) => {
      button.onclick = () => this.app.go('users');
    });
  }

  renderTotals(report) {
    const host = $('#subsTotals');
    if (!host) return;
    const totals = report.totals || {};
    host.innerHTML = `<div class="kv-list">
      <div class="kv-line"><span>کاربران</span><b>${Fmt.num(totals.users)} — ${Fmt.num(totals.active)} فعال / ${Fmt.num(totals.disabled)} غیرفعال</b></div>
      <div class="kv-line"><span>مصرف کل</span><b>${Fmt.sizeText(totals.used_gb)}</b></div>
      <div class="kv-line"><span>مصرف تجمعی (lifetime)</span><b>${Fmt.sizeText(totals.lifetime_gb)}</b></div>
      <div class="kv-line"><span>کاربران سهمیه‌دار</span><b>${Fmt.num(totals.limited)}</b></div>
      <div class="kv-line"><span>در آستانهٔ انقضا / منقضی / بالای سهمیه</span><b>${Fmt.num(totals.expiring)} / ${Fmt.num(totals.expired)} / ${Fmt.num(totals.over_quota)}</b></div>
    </div>`;
  }

  renderThresholds(report) {
    const thresholds = report.thresholds || {};
    const expiry = $('#subsThresholdExpiry');
    if (expiry && document.activeElement !== expiry) expiry.value = thresholds.expiry_days ?? 3;
    const quota = $('#subsThresholdQuota');
    if (quota && document.activeElement !== quota) quota.value = thresholds.quota_percent ?? 80;
  }

  renderClients(report) {
    const host = $('#subsClients');
    const userSelect = $('#subsUser');
    if (userSelect) {
      const users = this.store.get('users') || [];
      const current = report.user || '';
      userSelect.innerHTML = `<option value="">— بدون کاربر (بدون لینک) —</option>`
        + users.map((user) => `<option value="${esc(user.username)}"${user.username === current ? ' selected' : ''}>${esc(user.username)}</option>`).join('');
    }
    if (!host) return;
    const clients = report.clients || [];
    const overridden = clients.filter((client) => client.override).length;
    const pill = $('#subsClientPill');
    if (pill) pill.textContent = overridden ? `${Fmt.num(overridden)} تنظیم‌شده` : `${Fmt.num(clients.length)} کلاینت`;
    const targets = report.targets || [];
    host.innerHTML = clients.map((client) => `
      <div class="setting-row" data-client="${esc(client.id)}">
        <div class="txt"><b>${esc(client.name)} <span class="muted" style="font-weight:400">· ${esc(client.platform || '')}</span></b>
          <p class="muted mono" dir="ltr" style="margin-top:5px">${esc(client.url || 'برای ساخت لینک یک کاربر انتخاب کنید')}</p></div>
        <div class="actions" style="margin:0">
          <select data-client-target="${esc(client.id)}" style="width:auto;min-width:150px">
            <option value="">پیش‌فرض (${esc(client.format)})</option>
            ${targets.map((item) => `<option value="${esc(item.target)}"${item.target === client.override ? ' selected' : ''}>${esc(item.label)}</option>`).join('')}
          </select>
          ${client.url ? `<button class="copy-btn" data-copy="${esc(client.url)}" title="کپی">${ico('copy', 14)}</button>` : ''}
        </div>
      </div>`).join('') || '<div class="empty">کلاینتی تعریف نشده</div>';
    bindCopyButtons(host, this.toasts);
  }

  renderTable(report) {
    const host = $('#subsTable');
    if (!host) return;
    const sort = this.store.get('subsSort') || 'usage';
    const sortHost = $('#subsSort');
    if (sortHost) {
      sortHost.innerHTML = SORTS.map((item) => `<button class="fchip${item.id === sort ? ' on' : ''}" data-sort="${item.id}">${esc(item.label)}</button>`).join('');
      $$('[data-sort]', sortHost).forEach((button) => {
        button.onclick = () => this.app.safe(async () => {
          this.store.set('subsSort', button.dataset.sort);
          await this.load();
        });
      });
    }
    const items = report.usage || [];
    if (!items.length) {
      host.innerHTML = '<tr><td colspan="6"><div class="empty">کاربری ثبت نشده</div></td></tr>';
      return;
    }
    const base = report.base_url || location.origin;
    host.innerHTML = items.map((item) => {
      const status = STATUS[item.status] || STATUS.ok;
      const url = `${base}/sub/${encodeURIComponent(item.uuid)}?target=auto`;
      const quota = item.limit_gb != null
        ? `<b>${Fmt.sizeText(item.used_gb)}</b> از ${Fmt.sizeText(item.limit_gb)} <span class="muted">(${Fmt.num(item.percent)}٪)</span>`
        : `<b>${Fmt.sizeText(item.used_gb)}</b> <span class="muted">/ بدون سقف</span>`;
      const expiry = item.days_left == null
        ? '<span class="muted">بدون انقضا</span>'
        : (item.days_left < 0 ? `<span class="pill bad">${Fmt.num(-item.days_left)} روز گذشته</span>`
          : `<b>${Fmt.num(item.days_left)}</b> روز مانده`);
      return `<tr>
        <td><b>${esc(item.username)}</b><div class="muted mono" style="font-size:10px">${esc(String(item.uuid).slice(0, 13))}…</div></td>
        <td>${quota}</td>
        <td>${item.limit_gb != null ? `${Fmt.num(item.percent)}٪` : '<span class="muted">—</span>'}</td>
        <td>${expiry}</td>
        <td><span class="pill ${status.cls}">${esc(status.label)}</span></td>
        <td class="nowrap">
          <button class="copy-btn" data-copy="${esc(url)}" title="کپی سابلینک">${ico('copy', 13)}</button>
          <button class="copy-btn" data-rotate="${esc(item.username)}" title="بازنشانی توکن سابلینک">${ico('sync', 13)}</button>
        </td>
      </tr>`;
    }).join('');
    bindCopyButtons(host, this.toasts);
    $$('[data-rotate]', host).forEach((button) => {
      button.onclick = () => this.app.safe(() => this.rotate(button.dataset.rotate, button));
    });
  }

  /* ----------------------------------------------------------------- actions */
  async saveAlerts() {
    const result = await this.api.post('/api/subscription/alerts', {
      expiry_days: $('#subsThresholdExpiry')?.value.trim(),
      quota_percent: $('#subsThresholdQuota')?.value.trim(),
    });
    this.toasts.ok(`آستانه‌ها ذخیره شد: ${Fmt.num(result.thresholds.expiry_days)} روز / ${Fmt.num(result.thresholds.quota_percent)}٪`);
    await this.load();
  }

  async saveClients() {
    const overrides = {};
    $$('[data-client-target]').forEach((select) => {
      const value = select.value.trim();
      if (value) overrides[select.dataset.clientTarget] = value;
    });
    await this.api.post('/api/subscription/clients', { overrides });
    this.toasts.ok('تنظیم کلاینت‌ها ذخیره شد');
    await this.load();
  }

  async rotate(username, button) {
    const confirmed = await this.app.modals.ask(
      'بازنشانی توکن سابلینک',
      `همهٔ لینک‌های «${username}» باطل و یک لینک تازه ساخته می‌شود. کاربر باید لینک جدید را دریافت کند.`,
      { confirmLabel: 'بازنشانی' },
    );
    if (!confirmed) return;
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spin-inline"></span>';
    try {
      const result = await this.api.post(`/api/users/${encodeURIComponent(username)}/rotate`, {});
      this.toasts.ok(`توکن «${username}» بازنشانی شد`);
      await this.load();
      if (result.subscription) this.toasts.info(result.subscription, 6000);
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  bindEvents() {
    const reload = $('#subsReload');
    if (reload) reload.onclick = () => this.app.safe(async () => {
      await this.load();
      this.toasts.info('گزارش سابلینک بازخوانی شد', 1600);
    });
    const saveAlerts = $('#subsSaveAlerts');
    if (saveAlerts) saveAlerts.onclick = () => this.app.safe(() => this.saveAlerts());
    const saveClients = $('#subsSaveClients');
    if (saveClients) saveClients.onclick = () => this.app.safe(() => this.saveClients());
    const user = $('#subsUser');
    if (user) user.onchange = () => this.app.safe(async () => {
      this.store.set('subsUser', user.value);
      await this.load();
    });
  }
}
