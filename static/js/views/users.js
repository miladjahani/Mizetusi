/* =============================================================================
   NEXUS · view — users: cards with quota rings, the advanced create/edit form,
   the per-client subscription drawer and one-click Iran-tuned creation.
   ========================================================================== */
import { $, $$, ico, esc, Fmt, bindCopyButtons } from '../core.js';
import { Charts, StatusKit, STATUS } from '../ui.js';

const FILTERS = [      { id: 'all', label: 'همه' }, { id: 'active', label: 'فعال' }, { id: 'off', label: 'غیرفعال' },
  { id: 'quota', label: 'اتمام حجم' }, { id: 'expired', label: 'منقضی' },
];

// Client ids are listed in the order an Iranian user is most likely to need
// them; anything the backend adds later still renders, just after these.
const CLIENT_ORDER = ['smart', 'bettbox', 'exclusive', 'v2rayng', 'nekoboxplus', 'nekobox', 'hiddify',
  'karing', 'streisand', 'shadowrocket', 'v2box', 'foxray', 'clash', 'singbox', 'xray'];

// The protocol multi-select normally renders from the catalog in /api/settings.
// These values are the fallback, so the create/edit form is always usable: it
// used to show an empty box (and refuse to save) whenever that catalog had not
// been fetched yet — e.g. opening the user form before visiting Settings.
const FALLBACK_PROTOCOLS = [
  { id: 'vless', label: 'VLESS' }, { id: 'vmess', label: 'VMess' },
  { id: 'trojan', label: 'Trojan' }, { id: 'ss', label: 'Shadowsocks' },
];
const FALLBACK_CIPHERS = 'AES-128 · AES-256 · ChaCha20-Poly1305';

// Users render a screenful at a time; the rest sits behind one «نمایش بیشتر»
// button, so a workspace with a hundred accounts is still one tidy page.
const PAGE = 24;

// The node-scope picker usually renders from /api/presets (which carries a live
// node count per scope); this is the fallback so the user form is never empty.
const FALLBACK_SCOPES = [
  { id: 'all', label: 'همه نودها', hint: 'سرور اصلی + همه لوکیشن‌ها' },
  { id: 'multi', label: 'فقط نودهای مولتی‌لوکیشن', hint: 'فقط لوکیشن‌های لبه، بدون نود خود سرور' },
  { id: 'origin', label: 'فقط سرور اصلی', hint: 'فقط نود خود همین سرور' },
];

const sortedClients = (clients) => (clients || []).slice().sort((a, b) => {
  const index = (item) => (CLIENT_ORDER.indexOf(item.id) === -1 ? 99 : CLIENT_ORDER.indexOf(item.id));
  return index(a) - index(b);
});

// Clients are published in engine families: Xray reads Base64/text, sing-box reads
// JSON and Clash/Mihomo (Bettbox, Clash Verge…) reads YAML only. Grouping them is
// what stops a Clash user from being handed a Base64 link — and vice versa — so
// every family is rendered as its own collapsed dropdown.
const groupClients = (clients) => {
  const list = (clients || []).slice().sort((a, b) => {
    const order = (item) => (Number.isFinite(item.family_order) ? item.family_order : 99);
    return order(a) - order(b) || CLIENT_ORDER.indexOf(a.id) - CLIENT_ORDER.indexOf(b.id);
  });
  const groups = [];
  for (const client of list) {
    const id = client.family || 'core';
    let group = groups.find((item) => item.id === id);
    if (!group) {
      group = { id, label: client.family_label || id, hint: client.family_hint || '', clients: [] };
      groups.push(group);
    }
    group.clients.push(client);
  }
  return groups;
};

const chev = (size = 14) => `<svg class="chev" width="${size}" height="${size}" aria-hidden="true"><use href="#i-chevron"/></svg>`;

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
        this.store.set('userLimit', PAGE);
        this.renderFilters();
        this.render();
        this.renderSubTable();
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
    const shown = list.slice(0, this.store.get('userLimit') || PAGE);
    host.innerHTML = shown.map((user, index) => {
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
            <div class="kv"><span>سقف IP / پروتکل</span><b>${user.ip_limit ? Fmt.num(user.ip_limit) : '—'} / ${esc(user.protocol_label || 'همه پروتکل‌ها')}</b></div>
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
    }).join('') + this.moreButton(list.length, shown.length, 'user');

    $$('#userList [data-u]').forEach((button) => {
      const user = this.store.get('users').find((item) => item.username === button.dataset.name);
      button.onclick = () => this.app.safe(async () => {
        const action = button.dataset.u;
        if (action === 'links') await this.linksModal(user);
        if (action === 'portal') await this.portal(user);
        if (action === 'edit') await this.openForm(user);
        if (action === 'toggle') await this.toggle(user);
        if (action === 'reset') await this.reset(user);
        if (action === 'del') await this.remove(user);
      });
    });
    const more = $('#userList [data-more]');
    if (more) more.onclick = () => {
      this.store.set('userLimit', (this.store.get('userLimit') || PAGE) + PAGE);
      this.render();
    };
  }

  /* The one control that replaces a long list: a titled row saying how much is
     left, instead of silently dropping the rest. */
  moreButton(total, shown, kind) {
    if (total <= shown) return '';
    return `<button class="secondary more-row" data-more="${esc(kind)}">`
      + `${ico('chevron', 13)} نمایش ${Fmt.num(Math.min(PAGE, total - shown))} مورد بعدی · ${Fmt.num(total - shown)} باقی‌مانده`
      + `</button>`;
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
    const shown = users.slice(0, this.store.get('userLimit') || PAGE);
    host.innerHTML = shown.map((user) => {
      const status = StatusKit.of(user);
      const url = `${base}/sub/${encodeURIComponent(user.uuid)}?target=auto`;
      return `<tr>
        <td><b>${esc(user.username)}</b><div class="muted mono" style="font-size:10px">${esc(String(user.uuid).slice(0, 13))}…</div></td>
        <td><span class="pill" style="padding:3px 9px">${esc(user.node_scope_label || 'همه نودها')}</span></td>
        <td><span class="pill ${status.cls}">${esc(status.label)}</span></td>
        <td class="mono" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(url)}</td>
        <td class="nowrap">
          <button class="copy-btn" data-copy="${esc(url)}" title="کپی">${ico('copy', 13)}</button>
          <button class="copy-btn" data-links="${esc(user.username)}" title="همه لینک‌ها">${ico('link', 13)}</button>
        </td>
      </tr>`;
    }).join('') + (users.length > shown.length
      ? `<tr><td colspan="5">${this.moreButton(users.length, shown.length, 'usertable')}</td></tr>` : '');
    bindCopyButtons(host, this.toasts);
    const more = $('[data-more="usertable"]', host);
    if (more) more.onclick = () => {
      this.store.set('userLimit', (this.store.get('userLimit') || PAGE) + PAGE);
      this.render();
      this.renderSubTable();
    };
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
  /* The server owns both lists: one card per *mode* (a preset plus the slice of
     the catalog it publishes) and one chip per *scope* with its live node count,
     so the panel never offers a combination the deployment cannot deliver. */
  async loadPresets() {
    try {
      const data = await this.api.get('/api/presets');
      const modes = data.modes?.length ? data.modes : (data.presets || []);
      this.store.set('presets', modes);
      this.store.set('scopes', data.scopes || []);
      const current = this.store.get('quickMode');
      const chosen = modes.find((mode) => mode.id === current) || modes.find((mode) => mode.id === data.default) || modes[0];
      this.store.set('quickMode', chosen?.id || '');
      if (!this.store.get('quickScope')) this.store.set('quickScope', chosen?.scope || data.scope || 'all');
      this.renderModes();
      this.renderScopes();
    } catch (error) {
      const host = $('#quickModes');
      if (host) host.innerHTML = '<div class="empty">حالت‌های ساخت سریع در دسترس نیست</div>';
    }
  }

  renderModes() {
    const host = $('#quickModes');
    if (!host) return;
    const modes = this.store.get('presets');
    const current = this.store.get('quickMode');
    if (!modes.length) {
      host.innerHTML = '<div class="empty">حالتی تعریف نشده است</div>';
      return;
    }
    host.innerHTML = modes.map((mode) => {
      const glyph = mode.kind === 'edge' ? 'globe' : mode.kind === 'origin' ? 'server' : 'zap';
      return `<button type="button" class="mode-card${mode.id === current ? ' on' : ''}" data-mode="${esc(mode.id)}" title="${esc(mode.note || '')}">
        <b>${ico(glyph, 14)} ${esc(mode.name)}</b>
        <span>${esc(mode.best_for || '')}</span>
        <span class="tags">${(mode.highlights || []).slice(0, 3).map((item) => `<i>${esc(item)}</i>`).join('')}</span>
      </button>`;
    }).join('');
    $$('#quickModes [data-mode]', host).forEach((button) => {
      button.onclick = () => {
        this.store.set('quickMode', button.dataset.mode);
        const mode = this.store.get('presets').find((item) => item.id === button.dataset.mode);
        // A mode publishes a specific slice of the catalog, so its scope comes
        // preselected — unless the admin asked for the panel default to win.
        if (mode?.scope && !this.store.get('quickUseDefaultScope')) this.store.set('quickScope', mode.scope);
        this.renderModes();
        this.renderScopes();
      };
    });
  }

  renderScopes() {
    const host = $('#quickScopes');
    if (!host) return;
    const options = this.store.get('scopes') || [];
    if (!options.length) {
      host.innerHTML = '';
      return;
    }
    const fallback = (options.find((option) => option.current) || options[0]).id;
    const current = this.store.get('quickScope') || fallback;
    host.innerHTML = options.map((option) => `
      <button type="button" class="pick${option.id === current ? ' on' : ''}" data-scope="${esc(option.id)}"${option.empty ? ' disabled' : ''} title="${esc(option.hint || '')}">
        <span class="tick"></span>${esc(option.label)}<small>${Fmt.num(option.count)} نود</small>
      </button>`).join('');
    $$('#quickScopes [data-scope]', host).forEach((button) => {
      button.onclick = () => { this.store.set('quickScope', button.dataset.scope); this.renderScopes(); };
    });
  }

  async quickCreate() {
    const button = $('#btnQuickUser');
    const original = button?.innerHTML || '';
    if (button) { button.disabled = true; button.innerHTML = '<span class="spin-inline"></span> در حال ساخت…'; }
    try {
      const username = $('#quickUsername')?.value.trim() || '';
      const data = await this.api.post('/api/users/quick', {
        preset: this.store.get('quickMode'), scope: this.store.get('quickScope'), username,
      });
      const input = $('#quickUsername');
      if (input) input.value = '';
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
            <div class="kv-line"><span>محدودهٔ نودها</span><b>${esc(data.node_scope_label || 'همه نودها')}</b></div>
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
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px"><b style="font-size:13px">سابلینک به تفکیک پروتکل و ترنسپورت</b><span class="pill info">${Fmt.num((data.transports || []).length)} ترکیب</span></div>
          ${this.transportRows(data.transports, 8)}
          <p class="muted" style="margin:11px 0 0;line-height:1.9">هر ترکیب نود × پروتکل یک سابلینک جدا دارد (مثلاً فقط Reality یا فقط مسیر CDN). حالت «هوشمند» همه‌ی نودها و پروتکل‌ها را یکجا می‌دهد.</p>
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

  /* --------------------------------------------------------------- node scope */
  /* Which slice of the catalog this user's subscription publishes: everything,
     only the multi-location nodes, only this server, or one country. The count on
     every chip is live, so the choice is never a guess. */
  scopeChips(user = {}) {
    const catalog = this.store.get('scopes') || [];
    let list = catalog.length ? catalog.slice() : FALLBACK_SCOPES.slice();
    const raw = String(user.node_scope || this.store.get('quickScope') || 'all');
    if (!list.some((item) => item.id === raw)) {
      // A scope the catalog no longer lists (a country whose nodes are gone, or
      // one saved by an older release) still has to be visible, or saving the
      // form would silently widen the user to every node.
      list.unshift({ id: raw, label: user.node_scope_label || raw.toUpperCase(), hint: 'محدودهٔ فعلی این کاربر' });
    }
    const html = list.map((item) => `<button type="button" class="pick${item.id === raw ? ' on' : ''}" data-scope="${esc(item.id)}" title="${esc(item.hint || '')}">
        <span class="tick"></span>${esc(item.label)}${Number.isFinite(item.count) ? `<small>${Fmt.num(item.count)} نود</small>` : ''}
      </button>`).join('');
    return { html, current: raw };
  }

  /* ------------------------------------------------------------ protocol chips */
  /* Every protocol is selected by default, so a created user works on all paths
     (WS, CDN, Reality, WARP) with no extra step; an admin only narrows it. */
  protocolChips(user = {}) {
    const catalog = this.store.get('settings')?.protocols || {};
    const list = catalog.protocols?.length ? catalog.protocols : FALLBACK_PROTOCOLS;
    const ciphers = (catalog.shadowsocks || []).map((c) => c.method.replace('2022-blake3-', '')).join(' · ')
      || FALLBACK_CIPHERS;
    const raw = String(user.protocol_value || user.protocol || 'all');
    const explicit = raw.includes(',') ? raw.split(',').map((id) => id.trim()).filter(Boolean) : [];
    const on = new Set(Array.isArray(user.protocols) && user.protocols.length ? user.protocols
      : (explicit.length ? explicit : list.map((item) => item.id)));
    const html = list.map((item) => `<button type="button" class="pick${on.has(item.id) ? ' on' : ''}" data-proto="${esc(item.id)}" title="${esc(item.label)}${item.id === 'ss' ? ` — ${esc(ciphers)}` : ''}">
        <span class="tick"></span>${esc(item.label)}${item.id === 'ss' && ciphers ? `<small>${esc(ciphers)}</small>` : ''}
      </button>`).join('');
    return { html, ciphers };
  }

  /* ------------------------------------------------------- advanced user form */
  /* The form needs the settings catalog (protocols + defaults), which is only
     fetched by the Settings section — load it first so the chips are never
     empty, then open the form. */
  async openForm(user = null) {
    await Promise.all([this.app.ensureSettings(), this.ensureCustomization()]);
    this.modal(user);
  }

  /* The default config count lives in the «شخصی‌سازی» payload, which is the one
     place an admin sets it. Fetch it before the form opens so the field is
     prefilled instead of always empty. */
  async ensureCustomization() {
    if (this.store.get('customization')) return;
    try {
      this.store.set('customization', await this.api.get('/api/customization'));
    } catch (error) {
      // The field falls back to "no cap", so a failed fetch must not block
      // creating a user.
      if (!error.unauthorized) console.warn('[nexus] customization unavailable', error);
    }
  }

  modal(user = null) {
    const isEdit = !!user;
    const defaults = this.store.get('settings')?.defaults || {};
    const capDefault = defaults.max_configs || this.store.get('customization')?.default_max_configs || '';
    const u = user || {};
    const pick = (value, fallback) => (value === null || value === undefined || value === '' ? (fallback ?? '') : value);
    const swClass = (value, fallback) => ((value === undefined ? fallback : !!value) ? ' on' : '');
    const protocols = this.protocolChips(u);
    const scopes = this.scopeChips(u);

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
          <div><label>تعداد کانفیگ <span class="hint">خالی = بدون سقف</span></label>
            <input id="ufMaxConfigs" type="number" min="1" max="500" dir="ltr" placeholder="${esc(capDefault || 'بدون سقف')}" value="${esc(pick(u.max_configs, capDefault))}"></div>
        </div>
        <label>پروتکل‌ها <span class="hint">هر تعداد را می‌توانید همزمان فعال کنید</span></label>
        <div class="picks" id="ufProtocols">${protocols.html}</div>
        <div class="picks" style="gap:6px;margin-top:8px">
          <button type="button" class="fchip" id="ufAllProtocols">انتخاب همه / هیچ‌کدام</button>
        </div>
        <label>محدودهٔ نودها <span class="hint">این کاربر فقط همین نودها را در سابلینکش می‌بیند</span></label>
        <div class="picks" id="ufScopes">${scopes.html}</div>
        <p class="muted" style="margin:11px 0 0;line-height:1.9">
          <b>تعداد کانفیگ</b> یعنی همین کاربر حداکثر چند ورودی (نود × پروتکل) در سابلینکش می‌بیند؛ خالی بگذارید تا همه ترکیب‌های منتشرشده بیاید.
          کاربر روی همه اینباندهای Xray این سرور ساخته می‌شود و هر پروتکل انتخابی یک سابلینک واقعی می‌گیرد.
          ShadowSocks در سه نوع عرضه می‌شود (${esc(protocols.ciphers)}) و در خروجی sing-box و Clash می‌آید.
        </p>
        ${isEdit ? '<p class="muted" style="margin-top:10px">نام کاربری پس از ساخت قابل تغییر نیست؛ بقیه تنظیمات قابل ویرایش است.</p>' : ''}
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
    // One credential, every inbound: a chip toggles a protocol and "انتخاب همه"
    // turns the whole set back on.
    const chosenProtocols = () => $$('#ufProtocols .pick.on', modal.el).map((chip) => chip.dataset.proto);
    $$('#ufProtocols .pick', modal.el).forEach((chip) => {
      chip.onclick = () => chip.classList.toggle('on');
    });
    const allChip = field('#ufAllProtocols');
    if (allChip) allChip.onclick = () => {
      const chips = $$('#ufProtocols .pick', modal.el);
      const every = chips.length > 0 && chips.every((chip) => chip.classList.contains('on'));
      chips.forEach((chip) => chip.classList.toggle('on', !every));
      allChip.classList.toggle('on', !every);
    };
    $$('.tab', modal.el).forEach((tab) => {
      tab.onclick = () => {
        $$('.tab', modal.el).forEach((other) => other.classList.toggle('on', other === tab));
        $$('.tab-panel', modal.el).forEach((panel) => panel.classList.toggle('on', panel.dataset.panel === tab.dataset.tab));
      };
    });
    ['#ufActive', '#ufStartFirst', '#ufBlockAds', '#ufBlockPorn']
      .forEach((selector) => { const el = field(selector); if (el) el.onclick = () => el.classList.toggle('on'); });
    // One scope at a time: picking one clears the rest, like a radio group.
    const scopeButtons = () => $$('#ufScopes .pick', modal.el);
    scopeButtons().forEach((chip) => {
      chip.onclick = () => scopeButtons().forEach((other) => other.classList.toggle('on', other === chip));
    });
    const switchOn = (selector) => !!field(selector)?.classList.contains('on');

    field('#ufSave').onclick = () => this.app.safe(async () => {
      const button = field('#ufSave');
      const number = (selector) => {
        const value = field(selector).value.trim();
        return value === '' ? null : Number(value);
      };
      const payload = {
        limit_gb: number('#ufLimit'), expiry_days: number('#ufExpiry'), limit_req: number('#ufReq'), ip_limit: number('#ufIpLimit'),
        max_configs: number('#ufMaxConfigs'),
        node_scope: (scopeButtons().find((chip) => chip.classList.contains('on')) || {}).dataset?.scope || 'all',
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
      const selected = chosenProtocols();
      if (!selected.length) {
        this.toasts.err('حداقل یک پروتکل را انتخاب کنید');
        return;
      }
      payload.protocol = selected;
      if (!isEdit) payload.username = username;
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
      // The scope catalog comes with the links: the drawer then shows what this
      // user publishes *and* every other slice as a ready link.
      const [data, scopeData] = await Promise.all([
        this.api.get(`/api/users/${encodeURIComponent(user.username)}/links`),
        this.api.get(`/api/scopes?username=${encodeURIComponent(user.username)}`),
      ]);
      // Nothing downstream may assume the shape: a proxy page or an empty body
      // would otherwise crash the drawer on its first ``.map``.
      if (!data || typeof data !== 'object') throw new Error('پاسخ سرور خوانا نبود — صفحه را بازخوانی کنید');
      data.subscriptions = Array.isArray(data.subscriptions) ? data.subscriptions : [];
      data.nodes = Array.isArray(data.nodes) ? data.nodes : [];
      $('#linkSummary', modal.el).innerHTML = `
        <div class="kv-line"><span>UUID / رمز</span><b>${esc(data.uuid)}</b></div>
        <div class="kv-line"><span>پروتکل‌های فعال</span><b>${esc(data.protocol_label || 'همه پروتکل‌ها')}</b></div>
        <div class="kv-line"><span>وضعیت</span><b>${esc(data.allowed ? 'قابل اتصال' : `غیرفعال (${data.reason})`)}</b></div>
        <div class="kv-line"><span>نودهای موجود</span><b>${Fmt.num(data.node_count)} نود</b></div>
        <div class="kv-line"><span>مصرف</span><b>${esc(Fmt.sizeText(data.used_gb))}${data.limit_gb ? ` از ${esc(Fmt.sizeText(data.limit_gb))}` : ''}</b></div>`;

      const globalLinks = data.subscriptions.map((sub) => [sub.label, sub.url]);
      body.innerHTML = `
        ${this.scopePanel(scopeData, data.node_scope_label)}
        <div class="sub-card" style="margin-top:11px">
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
                ${node.in_scope === false ? '<span class="pill bad" title="این نود در محدودهٔ کاربر نیست و در سابلینکش منتشر نمی‌شود">خارج از محدوده</span>' : ''}
                <span class="lat ${StatusKit.latencyTone(node.latency_ms)}">${StatusKit.latencyText(node.latency_ms)}</span>
              </div>
              <div class="link-box"><div class="lb-main"><b>${esc(data.protocol_label || 'همه پروتکل‌ها')} مستقیم</b><code>${esc(node.links.primary)}</code></div>
                <button class="copy-btn" data-copy="${esc(node.links.primary)}">${ico('copy', 14)}</button></div>
              <div class="link-box"><div class="lb-main"><b>سابلینک این نود</b><code>${esc(node.subscription)}</code></div>
                <button class="copy-btn" data-copy="${esc(node.subscription)}">${ico('copy', 14)}</button></div>
              <div class="link-box" style="margin-top:6px"><div class="lb-main"><b>سابلینک همه ترکیب‌ها روی این نود</b><code>${esc(node.subscription_all || node.subscription)}</code></div>
                <button class="copy-btn" data-copy="${esc(node.subscription_all || node.subscription)}">${ico('copy', 14)}</button></div>
              ${(node.transport_subscriptions || []).slice(0, 4).map((item) => `<div class="link-box" style="margin-top:6px"><div class="lb-main"><b>${esc(item.label)} روی همین نود</b><code>${esc(item.url)}</code></div>
                <button class="copy-btn" data-copy="${esc(item.url)}">${ico('copy', 14)}</button></div>`).join('')}
              ${this.clientChips(node.clients)}
            </div>`).join('') || '<div class="empty">نود فعالی برای انتشار وجود ندارد</div>'}
        </div>`;
      bindCopyButtons(body, this.toasts);
      $$('[data-set-scope]', body).forEach((button) => {
        button.onclick = () => this.app.safe(async () => {
          await this.api.put(`/api/users/${encodeURIComponent(user.username)}`, { node_scope: button.dataset.setScope });
          this.toasts.ok('محدودهٔ نودهای این کاربر تغییر کرد');
          await this.app.reloadUsers();
          modal.close();
          await this.linksModal(user);
        });
      });
    } catch (error) {
      body.innerHTML = `<div class="empty">${ico('alert', 28)}<div>${esc(error.message)}</div></div>`;
    }
  }

  /* Which nodes this user publishes, and every other slice as a one-tap link.
     A scope is the answer to «only the multi-location nodes» / «only the US» /
     «only the server itself», so it belongs at the top of the link drawer. */
  scopePanel(scopeData, label = '') {
    if (!scopeData || !(scopeData.options || []).length) return '';
    return `<div class="sub-card">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <b style="font-size:13px">محدودهٔ نودهای این کاربر</b>
        <span class="pill info">${esc(scopeData.label || label || 'همه نودها')}</span>
        <span class="pill">${Fmt.num(scopeData.catalog_total)} نود در کاتالوگ</span>
      </div>
      <p class="hint" style="margin:0 0 4px">${esc(scopeData.hint || '')}</p>
      ${scopeData.options.map((option) => `
        <div class="link-box">
          <div class="lb-main"><b>${esc(option.label)} <span class="muted" style="font-weight:400">· ${Fmt.num(option.count)} نود</span></b><code>${esc(option.url || option.hint || '')}</code></div>
          ${option.current ? '<span class="pill ok">فعلی</span>' : `<button class="tbtn" data-set-scope="${esc(option.id)}" title="محدودهٔ این کاربر را روی همین بگذار">${ico('check', 12)} همین</button>`}
          ${option.url ? `<button class="copy-btn" data-copy="${esc(option.url)}" title="کپی">${ico('copy', 14)}</button>` : ''}
        </div>`).join('')}
      <p class="muted" style="margin:11px 0 0;line-height:1.9">«همین» محدودهٔ کاربر را دائمی می‌کند؛ کپی فقط همین لینک را با آن محدوده می‌دهد (بدون تغییر تنظیمات کاربر).</p>
    </div>`;
  }

  /* One copy-ready subscription per protocol/transport pair. */
  transportRows(transports, limit = 0) {
    const list = limit ? (transports || []).slice(0, limit) : (transports || []);
    if (!list.length) return '<div class="empty">ترنسپورتی برای انتشار نیست</div>';
    return list.map((item) => `<div class="link-box">
        <div class="lb-main"><b>${esc(item.label)} <span class="muted" style="font-weight:400">· ${esc((item.protocol || '').toUpperCase())} / ${esc(item.network || '')}</span></b><code>${esc(item.url)}</code></div>
        <button class="copy-btn" data-copy="${esc(item.url)}" title="کپی سابلینک ${esc(item.label)}">${ico('copy', 14)}</button>
      </div>`).join('');
  }

  /* One collapsed dropdown per engine family; every client shows the single format
     it imports, with its other accepted formats tucked behind «سایر فرمت‌ها». */
  clientLinkRows(clients) {
    const groups = groupClients(clients);
    if (!groups.length) return '<div class="empty">کلاینتی برای انتشار وجود ندارد</div>';
    return groups.map((group) => `
      <details class="sub-group">
        <summary>
          <span class="t">${esc(group.label)}</span>
          <span class="m">${Fmt.num(group.clients.length)} کلاینت</span>
          ${chev(14)}
        </summary>
        <div class="body">
          ${group.hint ? `<p class="hint">${esc(group.hint)}</p>` : ''}
          ${group.clients.map((client) => `
            <div class="link-box">
              <div class="lb-main"><b>${esc(client.name)} <span class="muted" style="font-weight:400">· ${esc(client.platform || '')}</span>
                <span class="pill" style="padding:2px 8px;font-size:9.5px">${esc(client.format_label || client.format)}</span></b><code>${esc(client.url)}</code></div>
              <button class="copy-btn" data-copy="${esc(client.url)}" title="کپی سابلینک ${esc(client.name)}">${ico('copy', 14)}</button>
              ${client.download ? `<a class="copy-btn" href="${esc(client.download)}" target="_blank" rel="noopener" title="دانلود ${esc(client.name)}">${ico('download', 14)}</a>` : ''}
            </div>
            ${(client.alternatives || []).length ? `<details class="sub-alt">
              <summary>${chev(12)} سایر فرمت‌های همین کلاینت (${Fmt.num(client.alternatives.length)})</summary>
              <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
                ${client.alternatives.map((alt) => `<button class="tbtn" data-copy="${esc(alt.url)}" title="کپی ${esc(alt.label)}">${ico('copy', 12)} ${esc(alt.label)}</button>`).join('')}
              </div>
            </details>` : ''}
          `).join('')}
        </div>
      </details>`).join('');
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
    if (search) search.oninput = (event) => { this.store.set('userSearch', event.target.value); this.store.set('userLimit', PAGE); this.render(); };
    const sort = $('#userSort');
    if (sort) sort.onchange = (event) => { this.store.set('userSort', event.target.value); this.store.set('userLimit', PAGE); this.render(); };
    const add = $('#btnAddUser');
    if (add) add.onclick = () => this.app.safe(() => this.openForm(null));
    const quick = $('#btnQuickUser');
    if (quick) quick.onclick = () => this.app.safe(() => this.quickCreate());
    const useDefault = $('#quickUseDefaultScope');
    if (useDefault) useDefault.onchange = () => this.store.set('quickUseDefaultScope', useDefault.checked);
    this.app.safe(() => this.loadPresets());
  }
}
