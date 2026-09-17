/* =============================================================================
   NEXUS · view — users: cards with quota rings, the advanced create/edit form,
   the per-client subscription drawer and one-click Iran-tuned creation.
   ========================================================================== */
import { $, $$, ico, esc, Fmt, bindCopyButtons } from '../core.js';
import { Charts, StatusKit, STATUS } from '../ui.js';

const FILTERS = [
  { id: 'all', label: 'همه' }, { id: 'active', label: 'فعال' }, { id: 'off', label: 'غیرفعال' },
  { id: 'quota', label: 'اتمام حجم' }, { id: 'expired', label: 'منقضی' },
];

// Client ids are listed in the order an Iranian user is most likely to need
// them; anything the backend adds later still renders, just after these.
const CLIENT_ORDER = ['smart', 'bettbox', 'exclusive', 'v2rayng', 'nekoboxplus', 'nekobox', 'hiddify',
  'karing', 'streisand', 'shadowrocket', 'v2box', 'foxray', 'clash', 'singbox', 'xray'];

const sortedClients = (clients) => (clients || []).slice().sort((a, b) => {
  const index = (item) => (CLIENT_ORDER.indexOf(item.id) === -1 ? 99 : CLIENT_ORDER.indexOf(item.id));
  return index(a) - index(b);
});

export class UsersView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  get modals() { return this.app.modals; }

  /* ------------------------------------------------------------------ filters */
  renderFilters() {
    const host = $('#userFilters');
    if (!host) return;
    host.innerHTML = FILTERS.map((filter) => `<button class="fchip${this.store.get('userFilter') === filter.id ? ' on' : ''}" data-filter="${filter.id}">${esc(filter.label)}</button>`).join('');
    $$('#userFilters .fchip').forEach((button) => {
      button.onclick = () => {
        this.store.set('userFilter', button.dataset.filter);
        this.renderFilters();
        this.render();
      };
    });
  }

  visible() {
    let list = this.store.get('users').slice();
    const filter = this.store.get('userFilter');
    if (filter !== 'all') {
      list = list.filter((user) => {
        const status = StatusKit.of(user);
        if (filter === 'active') return status === STATUS.active || status === STATUS.bootstrap;
        if (filter === 'off') return status === STATUS.off;
        if (filter === 'quota') return status === STATUS.quota || status === STATUS.request;
        if (filter === 'expired') return status === STATUS.expired;
        return true;
      });
    }
    const query = this.store.get('userSearch').trim().toLowerCase();
    if (query) {
      list = list.filter((user) => String(user.username).toLowerCase().includes(query) || String(user.uuid).toLowerCase().includes(query));
    }
    const sort = this.store.get('userSort');
    if (sort === 'usage') list.sort((a, b) => (b.used_gb || 0) - (a.used_gb || 0));
    if (sort === 'name') list.sort((a, b) => String(a.username).localeCompare(String(b.username)));
    if (sort === 'expiry') list.sort((a, b) => (a.expires_at ?? Infinity) - (b.expires_at ?? Infinity));
    return list;
  }

  /* --------------------------------------------------------------------- list */
  render() {
    const host = $('#userList');
    if (!host) return;
    const list = this.visible();
    if (!list.length) {
      host.innerHTML = `<div class="empty">${ico('users', 36)}<div>کاربری با این فیلتر وجود ندارد</div>
        <div class="muted" style="margin-top:6px">با «کاربر جدید» اولین کاربر را بسازید.</div></div>`;
      return;
    }
    host.innerHTML = list.map((user, index) => {
      const status = StatusKit.of(user);
      const pct = StatusKit.quotaPct(user);
      const remain = user.expires_at ? user.expires_at - Date.now() / 1000 : null;
      return `<div class="ucard glass" style="--i:${index}">
        <div class="head">
          <span class="avatar">${esc((user.username || '?')[0].toUpperCase())}</span>
          <div class="meta"><b>${esc(user.username)}</b><span>${esc(String(user.uuid).slice(0, 18))}…</span></div>
          <span class="pill ${status.cls}">${esc(status.label)}</span>
        </div>
        <div class="body">
          <div class="ring-wrap">
            ${Charts.ring(pct, StatusKit.toneForPct(pct))}
            <div style="text-align:center">
              <div class="ring-txt">${user.limit_gb ? `${Fmt.num(pct, 0)}%` : '∞'}</div>
              <div class="ring-sub">${user.limit_gb ? `از ${esc(Fmt.sizeText(user.limit_gb))}` : 'بی‌نهایت'}</div>
            </div>
          </div>
          <div class="usage">
            <div class="kv"><span>مصرف</span><b>${esc(Fmt.sizeText(user.used_gb))}</b></div>
            <div class="kv"><span>عمر کل</span><b>${esc(Fmt.sizeText(user.lifetime_used_gb))}</b></div>
            <div class="kv"><span>درخواست‌ها</span><b>${Fmt.num(user.used_req)}</b></div>
            <div class="kv"><span>انقضا</span><b data-expiry="${user.expires_at || ''}">${user.expires_at ? esc(Fmt.until(remain)) : 'بدون انقضا'}</b></div>
            <div class="kv"><span>سقف IP / پروتکل</span><b>${user.ip_limit ? Fmt.num(user.ip_limit) : '—'} / ${esc((user.protocol || 'vless').toUpperCase())}</b></div>
          </div>
        </div>
        <div class="acts">
          <button class="tbtn ${user.is_active ? 'ok' : ''}" data-u="toggle" data-name="${esc(user.username)}">${ico('power', 13)} ${user.is_active ? 'فعال' : 'خاموش'}</button>
          <button class="tbtn info" data-u="portal" data-name="${esc(user.username)}">${ico('eye', 13)} پنجره وضعیت</button>
          <button class="tbtn info" data-u="links" data-name="${esc(user.username)}">${ico('link', 13)} لینک‌ها</button>
          <button class="tbtn" data-u="edit" data-name="${esc(user.username)}">${ico('edit', 13)} ویرایش</button>
          <button class="tbtn" data-u="reset" data-name="${esc(user.username)}">${ico('refresh', 13)} صفر کردن</button>
          <button class="tbtn bad" data-u="del" data-name="${esc(user.username)}">${ico('trash', 13)}</button>
        </div>
      </div>`;
    }).join('');

    $$('#userList [data-u]').forEach((button) => {
      const user = this.store.get('users').find((item) => item.username === button.dataset.name);
      button.onclick = () => this.app.safe(async () => {
        const action = button.dataset.u;
        if (action === 'links') await this.linksModal(user);
        if (action === 'portal') await this.portal(user);
        if (action === 'edit') this.modal(user);
        if (action === 'toggle') await this.toggle(user);
        if (action === 'reset') await this.reset(user);
        if (action === 'del') await this.remove(user);
      });
    });
  }

  renderSubTable() {
    const host = $('#subTable');
    if (!host) return;
    const users = this.store.get('users');
    const pill = $('#subCountPill');
    if (pill) pill.textContent = `${Fmt.num(users.length)} کاربر`;
    if (!users.length) {
      host.innerHTML = '<tr><td colspan="5"><div class="empty">کاربری ثبت نشده</div></td></tr>';
      return;
    }
    const base = this.store.get('settings')?.resolved_base_url || location.origin;
    host.innerHTML = users.map((user) => {
      const status = StatusKit.of(user);
      const url = `${base}/sub/${encodeURIComponent(user.uuid)}?target=auto`;
      return `<tr>
        <td><b>${esc(user.username)}</b><div class="muted mono" style="font-size:10px">${esc(String(user.uuid).slice(0, 13))}…</div></td>
        <td>${esc((user.protocol || 'vless').toUpperCase())}</td>
        <td><span class="pill ${status.cls}">${esc(status.label)}</span></td>
        <td class="mono" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(url)}</td>
        <td class="nowrap">
          <button class="copy-btn" data-copy="${esc(url)}" title="کپی">${ico('copy', 13)}</button>
          <button class="copy-btn" data-links="${esc(user.username)}" title="همه لینک‌ها">${ico('link', 13)}</button>
        </td>
      </tr>`;
    }).join('');
    bindCopyButtons(host, this.toasts);
    $$('[data-links]', host).forEach((button) => {
      button.onclick = () => this.app.safe(() => this.linksModal(this.store.get('users').find((item) => item.username === button.dataset.links)));
    });
  }

  /* ---------------------------------------------------------------- actions */
  async portal(user) {
    const data = await this.api.get(`/api/users/${encodeURIComponent(user.username)}/links`);
    window.open(data.portal_url, '_blank', 'noopener');
  }

  async toggle(user) {
    await this.api.put(`/api/users/${encodeURIComponent(user.username)}`, { toggle_only: true });
    this.toasts.ok('وضعیت کاربر تغییر کرد');
    await this.app.reloadUsers();
  }

  async reset(user) {
    const choice = await new Promise((resolve) => {
      const modal = this.modals.open({
        title: 'صفر کردن مصرف', size: 'slim',
        body: `<p class="muted" style="margin:0 0 6px">چه چیزی برای «${esc(user.username)}» صفر شود؟</p>`,
        footer: `<div class="actions" style="margin:0">
          <button class="secondary" data-vol>حجم مصرفی</button>
          <button class="secondary" data-req>تعداد درخواست</button>
          <button class="secondary" data-close>انصراف</button></div>`,
      });
      $('[data-vol]', modal.el).onclick = () => { resolve('volume'); modal.close(); };
      $('[data-req]', modal.el).onclick = () => { resolve('req'); modal.close(); };
    });
    if (!choice) return;
    await this.api.put(`/api/users/${encodeURIComponent(user.username)}`, { reset_action: choice });
    this.toasts.ok('مصرف صفر شد');
    await this.app.reloadUsers();
  }

  async remove(user) {
    const confirmed = await this.modals.ask('حذف کاربر', `کاربر «${user.username}» و تمام لینک‌هایش حذف می‌شود. ادامه می‌دهید؟`);
    if (!confirmed) return;
    await this.api.delete(`/api/users/${encodeURIComponent(user.username)}`);
    this.toasts.ok('کاربر حذف شد');
    await this.app.reloadUsers();
  }

  /* ------------------------------------------------------------- quick create */
  async loadPresets() {
    const host = $('#quickPreset');
    if (!host) return;
    try {
      const data = await this.api.get('/api/presets');
      this.store.set('presets', data.presets || []);
      host.innerHTML = (data.presets || []).map((preset) => `<option value="${esc(preset.id)}"${preset.id === data.default ? ' selected' : ''}>${esc(preset.name)}</option>`).join('')
        || '<option value="">پیش‌فرض سرور</option>';
      const describe = () => {
        const chosen = this.store.get('presets').find((preset) => preset.id === host.value);
        host.title = chosen ? `${chosen.note}${chosen.highlights ? ` — ${chosen.highlights.join(' · ')}` : ''}` : '';
      };
      host.onchange = describe;
      describe();
    } catch (error) {
      host.innerHTML = '<option value="">پیش‌فرض سرور</option>';
    }
  }

  async quickCreate() {
    const button = $('#btnQuickUser');
    const presetHost = $('#quickPreset');
    const original = button?.innerHTML || '';
    if (button) { button.disabled = true; button.innerHTML = '<span class="spin-inline"></span> در حال ساخت…'; }
    try {
      const data = await this.api.post('/api/users/quick', { preset: presetHost ? presetHost.value : '' });
      this.toasts.ok(`کاربر ${data.user.username} ساخته شد · ${data.preset_name}`, 5200);
      await this.app.reloadUsers();
      this.quickResultModal(data);
    } finally {
      if (button) { button.disabled = false; button.innerHTML = original; }
    }
  }

  quickResultModal(data) {
    const modal = this.modals.open({
      title: `کاربر ${data.user.username} آماده است`,
      subtitle: `${data.preset_name} — کاربر روی موتور Xray ساخته و لینک همه کلاینت‌ها آماده شد`,
      size: 'wide',
      body: `
        <div class="sub-card">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px"><b style="font-size:13px">تنظیمات اعمال‌شده</b><span class="pill ok">${esc(data.preset_name)}</span></div>
          <div style="display:flex;gap:7px;flex-wrap:wrap">${(data.applied || []).map((item) => `<span class="pill">${esc(item)}</span>`).join('')}</div>
          <div class="kv-list" style="margin-top:11px">
            <div class="kv-line"><span>نام کاربری</span><b>${esc(data.user.username)}</b></div>
            <div class="kv-line"><span>UUID / رمز</span><b>${esc(data.user.uuid)}</b></div>
            <div class="kv-line"><span>انقضا</span><b>${data.user.expires_at ? esc(Fmt.until(data.user.expires_at - Date.now() / 1000)) : 'بدون انقضا'}</b></div>
          </div>
        </div>
        <div class="sub-card" style="margin-top:11px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px"><b style="font-size:13px">لینک اشتراک هوشمند</b><span class="pill info">سریع‌ترین نود بر اساس پینگ</span></div>
          <div class="link-box"><div class="lb-main"><b>اتصال هوشمند (auto)</b><code>${esc(data.smart_url)}</code></div>
            <button class="copy-btn" data-copy="${esc(data.smart_url)}">${ico('copy', 14)}</button></div>
          ${this.portalBox(data.portal_url)}
        </div>
        <div class="sub-card" style="margin-top:11px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px"><b style="font-size:13px">سابلینک اختصاصی هر کلاینت</b><span class="pill info">${Fmt.num((data.clients || []).length)} کلاینت</span></div>
          ${this.clientLinkRows(data.clients)}
        </div>`,
      footer: `<div class="actions" style="margin:0">
        <button class="secondary" data-close>بستن</button>
        <button class="secondary" id="quickOpenPortal">${ico('eye', 13)} مشاهده پنجره وضعیت</button></div>`,
    });
    bindCopyButtons(modal.el, this.toasts);
    const open = $('#quickOpenPortal', modal.el);
    if (open && data.portal_url) open.onclick = () => window.open(data.portal_url, '_blank', 'noopener');
  }

  /* ------------------------------------------------------- advanced user form */
  modal(user = null) {
    const isEdit = !!user;
    const defaults = this.store.get('settings')?.defaults || {};
    const u = user || {};
    const pick = (value, fallback) => (value === null || value === undefined || value === '' ? (fallback ?? '') : value);
    const swClass = (value, fallback) => ((value === undefined ? fallback : !!value) ? ' on' : '');

    const modal = this.modals.open({
      title: isEdit ? `ویرایش ${user.username}` : 'ساخت کاربر جدید',
      subtitle: 'تنظیمات پیشرفته: سهمیه، انقضا، شبکه، فرگمنت و محدودیت‌ها',
      size: 'wide',
      body: `
      <div class="tabs" id="userTabs">
        <button class="tab on" data-tab="basic">پایه</button>
        <button class="tab" data-tab="quota">سهمیه و زمان</button>
        <button class="tab" data-tab="net">شبکه</button>
        <button class="tab" data-tab="adv">پیشرفته</button>
      </div>

      <div class="tab-panel on" data-panel="basic">
        <div class="field-grid">
          <div><label>نام کاربری <span class="hint">حروف لاتین، عدد، _ . -</span></label>
            <input id="ufUsername" dir="ltr" placeholder="nexus-user1" value="${esc(pick(u.username, ''))}" ${isEdit ? 'disabled' : ''}></div>
          <div><label>پروتکل</label>
            <select id="ufProtocol" ${isEdit ? 'disabled' : ''}>
              <option value="vless"${(u.protocol || defaults.protocol || 'vless') === 'vless' ? ' selected' : ''}>VLESS + WebSocket</option>
              <option value="trojan"${u.protocol === 'trojan' ? ' selected' : ''}>Trojan + WebSocket</option>
            </select></div>
        </div>
        ${isEdit ? '<p class="muted" style="margin-top:10px">نام کاربری و پروتکل پس از ساخت قابل تغییر نیستند.</p>' : ''}
        <div class="switch-row"><div class="txt"><b>فعال باشد</b><span>در صورت خاموش بودن، کاربر از Xray حذف می‌شود</span></div>
          <div class="switch${swClass(u.is_active, true)}" id="ufActive"></div></div>
        <div class="switch-row"><div class="txt"><b>شروع شمارش از اولین اتصال</b><span>تا اولین اتصال، تاریخ انقضا محاسبه نمی‌شود</span></div>
          <div class="switch${swClass(u.start_on_first_connect, false)}" id="ufStartFirst"></div></div>
      </div>

      <div class="tab-panel" data-panel="quota">
        <div class="field-grid">
          <div><label>سقف حجم (GB) <span class="hint">خالی = بی‌نهایت</span></label><input id="ufLimit" type="number" min="0" step="0.5" dir="ltr" value="${esc(pick(u.limit_gb, defaults.limit_gb))}"></div>
          <div><label>مدت اعتبار (روز) <span class="hint">خالی = بدون انقضا</span></label><input id="ufExpiry" type="number" min="0" step="1" dir="ltr" value="${esc(pick(u.expiry_days, defaults.expiry_days))}"></div>
          <div><label>سقف درخواست <span class="hint">خالی = بی‌نهایت</span></label><input id="ufReq" type="number" min="0" step="1" dir="ltr" value="${esc(pick(u.limit_req, ''))}"></div>
          <div><label>محدودیت IP همزمان</label><input id="ufIpLimit" type="number" min="1" step="1" dir="ltr" value="${esc(pick(u.ip_limit, defaults.ip_limit))}"></div>
        </div>
        <div class="field-grid">
          <div><label>چرخش خودکار IP</label>
            <select id="ufRotateEnabled"><option value="0"${u.auto_rotate_ip ? '' : ' selected'}>غیرفعال</option><option value="1"${u.auto_rotate_ip ? ' selected' : ''}>فعال</option></select></div>
          <div><label>بازه چرخش (دقیقه)</label><input id="ufRotateTime" type="number" min="1" dir="ltr" value="${esc(pick(u.rotate_time, 5))}"></div>
          <div><label>اپراتور IP</label>
            <select id="ufIpOperator">
              ${['all', 'iran', 'foreign'].map((option) => `<option value="${option}"${(u.ip_operator || 'all') === option ? ' selected' : ''}>${option === 'all' ? 'همه' : option === 'iran' ? 'ایران' : 'خارج'}</option>`).join('')}
            </select></div>
          <div><label>تعداد IP مجاز</label><input id="ufIpCount" type="number" min="1" dir="ltr" value="${esc(pick(u.ip_count, 5))}"></div>
        </div>
      </div>

      <div class="tab-panel" data-panel="net">
        <div class="field-grid">
          <div><label>پورت اتصال</label><input id="ufPort" type="number" min="1" max="65535" dir="ltr" value="${esc(pick(u.port, 443))}"></div>
          <div><label>SNI <span class="hint">اختیاری</span></label><input id="ufSni" dir="ltr" value="${esc(pick(u.sni, ''))}"></div>
          <div><label>Host header <span class="hint">اختیاری</span></label><input id="ufHost" dir="ltr" value="${esc(pick(u.host, ''))}"></div>
          <div><label>اثر انگشت TLS</label>
            <select id="ufFingerprint">
              ${['chrome', 'firefox', 'safari', 'ios', 'android', 'edge', 'random', 'randomized'].map((fp) => `<option value="${fp}"${(u.fingerprint || 'chrome') === fp ? ' selected' : ''}>${fp}</option>`).join('')}
            </select></div>
        </div>
        <div class="field-grid">
          <div><label>وضعیت TLS</label><select id="ufTls"><option value="on"${(u.tls || 'on') === 'on' ? ' selected' : ''}>فعال</option><option value="off"${u.tls === 'off' ? ' selected' : ''}>غیرفعال</option></select></div>
          <div><label>IPهای مجاز <span class="hint">با کاما جدا کنید</span></label><input id="ufIps" dir="ltr" value="${esc(pick(u.ips, ''))}" placeholder="1.2.3.4,5.6.7.8"></div>
          <div><label>پروکسی کاربر (خروجی)</label><input id="ufUserProxy" dir="ltr" value="${esc(pick(u.user_proxy, ''))}" placeholder="socks5://user:pass@host:1080"></div>
        </div>
      </div>

      <div class="tab-panel" data-panel="adv">
        <div class="field-grid">
          <div><label>Fragment طول</label><input id="ufFragLen" dir="ltr" value="${esc(pick(u.frag_len, ''))}" placeholder="100-200"></div>
          <div><label>Fragment فاصله</label><input id="ufFragInt" dir="ltr" value="${esc(pick(u.frag_int, ''))}" placeholder="10-20"></div>
          <div><label>Advanced fragment</label><input id="ufAdvFrag" dir="ltr" value="${esc(pick(u.advanced_frag, ''))}" placeholder="tlshello"></div>
          <div><label>Cipher suites</label><input id="ufCipher" dir="ltr" value="${esc(pick(u.cipher_suites, ''))}" placeholder="TLS_AES_128_GCM_SHA256"></div>
          <div><label>TLS mask</label><input id="ufTlsMask" dir="ltr" value="${esc(pick(u.tls_mask, ''))}" placeholder="1.1.1.1"></div>
        </div>
        <div class="switch-row"><div class="txt"><b>مسدودسازی تبلیغات</b><span>قواعد routing برای دامنه‌های تبلیغاتی</span></div>
          <div class="switch${swClass(u.block_ads, false)}" id="ufBlockAds"></div></div>
        <div class="switch-row"><div class="txt"><b>مسدودسازی محتوای بزرگسال</b><span>قواعد routing برای دامنه‌های غیرمجاز</span></div>
          <div class="switch${swClass(u.block_porn, false)}" id="ufBlockPorn"></div></div>
      </div>`,
      footer: `<div class="actions" style="margin:0">
        <button class="primary" id="ufSave">${isEdit ? 'ذخیره تغییرات' : 'ساخت کاربر و نمایش لینک‌ها'}</button>
        <button class="secondary" data-close>انصراف</button></div>`,
    });

    const field = (selector) => $(selector, modal.el);
    $$('.tab', modal.el).forEach((tab) => {
      tab.onclick = () => {
        $$('.tab', modal.el).forEach((other) => other.classList.toggle('on', other === tab));
        $$('.tab-panel', modal.el).forEach((panel) => panel.classList.toggle('on', panel.dataset.panel === tab.dataset.tab));
      };
    });
    ['#ufActive', '#ufStartFirst', '#ufBlockAds', '#ufBlockPorn']
      .forEach((selector) => { const el = field(selector); if (el) el.onclick = () => el.classList.toggle('on'); });
    const switchOn = (selector) => !!field(selector)?.classList.contains('on');

    field('#ufSave').onclick = () => this.app.safe(async () => {
      const button = field('#ufSave');
      const number = (selector) => {
        const value = field(selector).value.trim();
        return value === '' ? null : Number(value);
      };
      const payload = {
        limit_gb: number('#ufLimit'), expiry_days: number('#ufExpiry'), limit_req: number('#ufReq'), ip_limit: number('#ufIpLimit'),
        start_on_first_connect: switchOn('#ufStartFirst'), is_active: switchOn('#ufActive') ? 1 : 0,
        auto_rotate_ip: field('#ufRotateEnabled').value === '1' ? 1 : 0, rotate_time: Number(field('#ufRotateTime').value) || 5,
        ip_operator: field('#ufIpOperator').value, ip_count: Number(field('#ufIpCount').value) || 5,
        port: Number(field('#ufPort').value) || 443, sni: field('#ufSni').value.trim() || null, host: field('#ufHost').value.trim() || null,
        fingerprint: field('#ufFingerprint').value, tls: field('#ufTls').value, ips: field('#ufIps').value.trim(),
        user_proxy: field('#ufUserProxy').value.trim() || null,
        frag_len: field('#ufFragLen').value.trim(), frag_int: field('#ufFragInt').value.trim(),
        advanced_frag: field('#ufAdvFrag').value.trim() || null, cipher_suites: field('#ufCipher').value.trim() || null,
        tls_mask: field('#ufTlsMask').value.trim() || null,
        block_ads: switchOn('#ufBlockAds') ? 1 : 0, block_porn: switchOn('#ufBlockPorn') ? 1 : 0,
      };
      const username = field('#ufUsername').value.trim();
      if (!isEdit) { payload.username = username; payload.protocol = field('#ufProtocol').value; }
      if (!isEdit && !/^[A-Za-z0-9_.-]{1,80}$/.test(username)) {
        this.toasts.err('نام کاربری فقط با حروف لاتین، عدد، _ و . و - (حداکثر ۸۰ کاراکتر)');
        return;
      }
      button.disabled = true;
      button.innerHTML = '<span class="spin-inline"></span> در حال ذخیره…';
      try {
        if (isEdit) await this.api.put(`/api/users/${encodeURIComponent(user.username)}`, payload);
        else await this.api.post('/api/users', payload);
        modal.close();
        this.toasts.ok(isEdit ? 'کاربر بروزرسانی شد' : `کاربر ${username} ساخته شد`);
        await this.app.reloadUsers();
        if (!isEdit) await this.linksModal({ username });
      } catch (error) {
        this.app.report(error);
        button.disabled = false;
        button.innerHTML = isEdit ? 'ذخیره تغییرات' : 'ساخت کاربر و نمایش لینک‌ها';
      }
    });
  }

  /* --------------------------------------------------------- links drawer */
  async linksModal(user) {
    if (!user) return;
    const modal = this.modals.open({
      title: `سابلینک‌های ${user.username}`,
      subtitle: 'همه فرمت‌ها، همه کلاینت‌ها و همه ترکیب‌های نود برای این کاربر',
      size: 'wide',
      body: '<div id="linkSummary" class="kv-list"></div><div id="linkBody" style="margin-top:14px"><div class="skel" style="height:150px"></div></div>',
    });
    const body = $('#linkBody', modal.el);
    try {
      const data = await this.api.get(`/api/users/${encodeURIComponent(user.username)}/links`);
      $('#linkSummary', modal.el).innerHTML = `
        <div class="kv-line"><span>UUID / رمز</span><b>${esc(data.uuid)}</b></div>
        <div class="kv-line"><span>پروتکل</span><b>${esc((data.protocol || 'vless').toUpperCase())}</b></div>
        <div class="kv-line"><span>وضعیت</span><b>${esc(data.allowed ? 'قابل اتصال' : `غیرفعال (${data.reason})`)}</b></div>
        <div class="kv-line"><span>نودهای موجود</span><b>${Fmt.num(data.node_count)} نود</b></div>
        <div class="kv-line"><span>مصرف</span><b>${esc(Fmt.sizeText(data.used_gb))}${data.limit_gb ? ` از ${esc(Fmt.sizeText(data.limit_gb))}` : ''}</b></div>`;

      const globalLinks = data.subscriptions.map((sub) => [sub.label, sub.url]);
      body.innerHTML = `
        <div class="sub-card">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <b style="font-size:13px">سابلینک‌های سراسری</b>
            <span class="pill info">${Fmt.num(globalLinks.length)} فرمت</span>
          </div>
          ${this.portalBox(data.portal_url)}
          ${globalLinks.map(([label, url]) => `
            <div class="link-box"><div class="lb-main"><b>${esc(label)}</b><code>${esc(url)}</code></div>
              <button class="copy-btn" data-copy="${esc(url)}">${ico('copy', 14)}</button></div>`).join('')}
        </div>
        <div class="sub-card" style="margin-top:11px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <b style="font-size:13px">سابلینک اختصاصی هر کلاینت</b>
            <span class="pill info">${Fmt.num((data.clients || []).length)} کلاینت</span>
          </div>
          ${this.clientLinkRows(data.clients)}
          <p class="muted" style="margin:11px 0 0;line-height:1.9">هر کلاینت آدرس مخصوص خودش را دارد. با افزودن <span class="mono" dir="ltr">&amp;node=نام‌نود</span> فقط همان نود منتشر می‌شود و با <span class="mono" dir="ltr">&amp;target=all</span> هر دو پروتکل روی همه نودها می‌آید.</p>
        </div>
        <div class="sub-card" style="margin-top:11px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <b style="font-size:13px">لینک مستقیم هر نود</b>
            <span class="pill">${Fmt.num((data.nodes || []).length)} نود</span>
          </div>
          ${(data.nodes || []).map((node) => `
            <div style="padding:10px;border-radius:14px;border:1px solid var(--line);margin-top:9px">
              <div style="display:flex;align-items:center;gap:9px">
                <span class="node-icon ${node.kind === 'cloudflare' ? 'cf' : ''}" style="width:26px;height:26px;flex:0 0 26px;font-size:10px">${node.kind === 'cloudflare' ? '☁' : 'R'}</span>
                <b style="font-size:12px;flex:1">${esc(node.name)}</b>
                <span class="lat ${StatusKit.latencyTone(node.latency_ms)}">${StatusKit.latencyText(node.latency_ms)}</span>
              </div>
              <div class="link-box"><div class="lb-main"><b>${esc((data.protocol || 'vless').toUpperCase())} مستقیم</b><code>${esc(node.links.primary)}</code></div>
                <button class="copy-btn" data-copy="${esc(node.links.primary)}">${ico('copy', 14)}</button></div>
              <div class="link-box"><div class="lb-main"><b>سابلینک این نود</b><code>${esc(node.subscription)}</code></div>
                <button class="copy-btn" data-copy="${esc(node.subscription)}">${ico('copy', 14)}</button></div>
              <div class="link-box" style="margin-top:6px"><div class="lb-main"><b>سابلینک همه ترکیب‌ها روی این نود</b><code>${esc(node.subscription_all || node.subscription)}</code></div>
                <button class="copy-btn" data-copy="${esc(node.subscription_all || node.subscription)}">${ico('copy', 14)}</button></div>
              ${this.clientChips(node.clients)}
            </div>`).join('') || '<div class="empty">نود فعالی برای انتشار وجود ندارد</div>'}
        </div>`;
      bindCopyButtons(body, this.toasts);
    } catch (error) {
      body.innerHTML = `<div class="empty">${ico('alert', 28)}<div>${esc(error.message)}</div></div>`;
    }
  }

  clientLinkRows(clients) {
    const list = sortedClients(clients);
    if (!list.length) return '<div class="empty">کلاینتی برای انتشار وجود ندارد</div>';
    return list.map((client) => `<div class="link-box">
        <div class="lb-main"><b>${esc(client.name)} <span class="muted" style="font-weight:400">· ${esc(client.platform || '')}</span>
          <span class="pill" style="padding:2px 8px;font-size:9.5px">${esc(client.format_label || client.format)}</span></b><code>${esc(client.url)}</code></div>
        <button class="copy-btn" data-copy="${esc(client.url)}" title="کپی سابلینک ${esc(client.name)}">${ico('copy', 14)}</button>
        ${client.download ? `<a class="copy-btn" href="${esc(client.download)}" target="_blank" rel="noopener" title="دانلود ${esc(client.name)}">${ico('download', 14)}</a>` : ''}
      </div>`).join('');
  }

  clientChips(clients, limit = 6) {
    const list = sortedClients(clients).slice(0, limit);
    if (!list.length) return '';
    return `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:9px">
      ${list.map((client) => `<button class="tbtn" data-copy="${esc(client.url)}" title="سابلینک ${esc(client.name)}">${ico('copy', 12)} ${esc(client.name)}</button>`).join('')}
    </div>`;
  }

  portalBox(url, label = 'پنجره وضعیت سابلینک') {
    if (!url) return '';
    return `<div class="link-box"><div class="lb-main"><b>${esc(label)} — قابل دادن به کاربر نهایی</b><code>${esc(url)}</code></div>
      <button class="copy-btn" data-copy="${esc(url)}" title="کپی">${ico('copy', 14)}</button>
      <a class="copy-btn" href="${esc(url)}" target="_blank" rel="noopener" title="باز کردن">${ico('eye', 14)}</a></div>`;
  }

  bindEvents() {
    const search = $('#userSearch');
    if (search) search.oninput = (event) => { this.store.set('userSearch', event.target.value); this.render(); };
    const sort = $('#userSort');
    if (sort) sort.onchange = (event) => { this.store.set('userSort', event.target.value); this.render(); };
    const add = $('#btnAddUser');
    if (add) add.onclick = () => this.modal(null);
    const quick = $('#btnQuickUser');
    if (quick) quick.onclick = () => this.app.safe(() => this.quickCreate());
    this.app.safe(() => this.loadPresets());
  }
}
