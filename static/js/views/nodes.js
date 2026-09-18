/* =============================================================================
   NEXUS · view — node catalog: filtering, CRUD, coverage matrix, per-node
   subscription explorer and the real ping actions.
   ========================================================================== */
import { $, $$, ico, esc, Fmt, bindCopyButtons } from '../core.js';
import { StatusKit } from '../ui.js';

const FILTERS = [
  { id: 'all', label: 'همه' }, { id: 'railway', label: 'Railway' },
  { id: 'cloudflare', label: 'Cloudflare' }, { id: 'disabled', label: 'غیرفعال' },
];

export class NodesView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  get modals() { return this.app.modals; }

  /* ------------------------------------------------------------------ filters */
  renderFilters() {
    const host = $('#nodeFilters');
    if (!host) return;
    host.innerHTML = FILTERS.map((filter) => `<button class="fchip${this.store.get('nodeFilter') === filter.id ? ' on' : ''}" data-filter="${filter.id}">${esc(filter.label)}</button>`).join('');
    $$('#nodeFilters .fchip').forEach((button) => {
      button.onclick = () => {
        this.store.set('nodeFilter', button.dataset.filter);
        this.renderFilters();
        this.render();
      };
    });
  }

  visible() {
    let list = this.store.get('nodes').slice();
    const filter = this.store.get('nodeFilter');
    if (filter === 'disabled') list = list.filter((node) => !node.enabled);
    else if (filter !== 'all') list = list.filter((node) => node.kind === filter);
    const query = this.store.get('nodeSearch').trim().toLowerCase();
    if (query) {
      list = list.filter((node) => [node.name, node.server, node.sni, node.host, node.kind]
        .filter(Boolean).some((value) => String(value).toLowerCase().includes(query)));
    }
    const sort = this.store.get('nodeSort');
    if (sort === 'latency') list.sort((a, b) => (a.latency_ms ?? 1e9) - (b.latency_ms ?? 1e9));
    if (sort === 'name') list.sort((a, b) => String(a.name).localeCompare(String(b.name)));
    if (sort === 'kind') {
      list.sort((a, b) => String(a.kind).localeCompare(String(b.kind)) || (a.latency_ms ?? 1e9) - (b.latency_ms ?? 1e9));
    }
    return list;
  }

  /* --------------------------------------------------------------------- list */
  render() {
    const host = $('#nodeList');
    if (!host) return;
    const list = this.visible();
    if (!list.length) {
      host.innerHTML = `<div class="empty">${ico('server', 34)}<div>نودی با این فیلتر پیدا نشد</div>
        <div class="muted" style="margin-top:6px">از «نود جدید» استفاده کنید یا Sync کنید.</div></div>`;
      return;
    }
    host.innerHTML = list.map((node, index) => `
      <div class="node-row" style="--i:${index};border-color:${node.enabled ? 'transparent' : 'rgba(255,107,129,.18)'}">
        <span class="node-icon ${node.kind === 'cloudflare' ? 'cf' : ''}">${node.kind === 'cloudflare' ? '☁' : 'R'}</span>
        <div class="node-main">
          <b>${esc(node.name)} ${node.enabled ? '' : '<span class="pill bad" style="padding:2px 8px;font-size:9.5px">غیرفعال</span>'}</b>
          <span>${esc(node.kind)} · ${esc(node.server)}:${esc(node.port)}${node.sni ? ` · SNI ${esc(node.sni)}` : ''}${node.host && node.host !== node.server ? ` · HOST ${esc(node.host)}` : ''}</span>
        </div>
        <span class="lat ${StatusKit.latencyTone(node.latency_ms)}" title="${Number(node.latency_ms) < 0 ? 'آخرین پینگ ناموفق بود' : 'تأخیر اندازه‌گیری‌شده'}">${StatusKit.latencyText(node.latency_ms)}</span>
        <div class="acts">
          <button class="tbtn info" data-act="ping" data-name="${esc(node.name)}" title="پینگ همین نود">${ico('activity', 13)}</button>
          <button class="tbtn info" data-act="links" data-name="${esc(node.name)}" title="سابلینک این نود">${ico('link', 13)}</button>
          <button class="tbtn ${node.enabled ? 'ok' : ''}" data-act="toggle" data-name="${esc(node.name)}" title="${node.enabled ? 'غیرفعال کردن' : 'فعال کردن'}">${ico('power', 13)}</button>
          <button class="tbtn" data-act="edit" data-name="${esc(node.name)}" title="ویرایش">${ico('edit', 13)}</button>
          <button class="tbtn bad" data-act="del" data-name="${esc(node.name)}" title="حذف">${ico('trash', 13)}</button>
        </div>
      </div>`).join('');

    $$('#nodeList [data-act]').forEach((button) => {
      const node = this.store.get('nodes').find((item) => item.name === button.dataset.name);
      button.onclick = () => this.app.safe(async () => {
        const action = button.dataset.act;
        if (action === 'ping') await this.pingOne(node, button);
        if (action === 'toggle') await this.toggle(node);
        if (action === 'edit') this.modal(node);
        if (action === 'del') await this.remove(node);
        if (action === 'links') await this.linksModal(node);
      });
    });
  }

  async loadTransports() {
    const data = await this.api.get('/api/transports');
    this.store.set('transports', data);
    this.renderCoverage();
  }

  renderCoverage() {
    const host = $('#coverage');
    if (!host) return;
    // The transport matrix comes from the engine, so this card can never claim a
    // protocol the server would not actually serve.
    if (!this.store.get('transports')) this.app.safe(() => this.loadTransports());
    const nodes = this.store.get('nodes');
    const matrix = this.store.get('transports') || {};
    const profiles = matrix.profiles || [];
    const planned = matrix.planned || [];
    if (!nodes.length) {
      host.innerHTML = `<div class="empty">${ico('nodes', 32)}<div>نودی برای بررسی پوشش نیست</div></div>`;
      return;
    }
    const badge = (ok, label, title) => `<span class="chip" style="padding:3px 9px;font-size:10px;${ok ? 'border-color:rgba(62,230,160,.3);color:#8ef0c0;background:rgba(62,230,160,.07)' : 'opacity:.45'}" title="${esc(title)}">${ok ? '✓' : '×'} ${esc(label)}</span>`;
    const users = this.store.get('users');
    const vless = users.filter((user) => (user.protocol || 'vless') === 'vless').length;
    const trojan = users.filter((user) => user.protocol === 'trojan').length;
    const enabled = nodes.filter((node) => node.enabled).length;
    const clients = this.store.get('settings')?.clients?.length || this.store.get('clientCount') || 16;
    host.innerHTML = `
      <div class="kv-list" style="margin-bottom:12px">
        <div class="kv-line"><span>ترکیب‌های قابل انتشار</span><b>${Fmt.num(enabled * profiles.length)} لینک (${Fmt.num(enabled)} نود × ${Fmt.num(profiles.length)} پروتکل/ترنسپورت)</b></div>
        <div class="kv-line"><span>سابلینک اختصاصی هر کلاینت</span><b>${Fmt.num(clients)} کلاینت × ${Fmt.num(enabled)} نود</b></div>
        <div class="kv-line"><span>کاربرهای VLESS / Trojan</span><b>${Fmt.num(vless)} / ${Fmt.num(trojan)}</b></div>
        <div class="kv-line"><span>وضعیت هسته</span><b>${matrix.xray?.running ? 'Running' : 'متوقف'}${matrix.xray?.warning ? ' · هشدار کانفیگ' : ''}</b></div>
      </div>
      ${nodes.slice(0, 12).map((node) => `
        <div class="node-row" style="padding:9px 11px">
          <span class="node-icon ${node.kind === 'cloudflare' ? 'cf' : ''}" style="width:28px;height:28px;flex:0 0 28px;font-size:11px">${node.kind === 'cloudflare' ? '☁' : 'R'}</span>
          <div class="node-main"><b style="font-size:12px">${esc(node.name)}</b></div>
          <div class="acts">
            ${profiles.slice(0, 8).map((profile) => badge(true, profile.tag, `${profile.protocol.toUpperCase()} روی ${profile.network}`)).join('')}
            ${badge(!!node.tls, 'TLS', 'رمزنگاری TLS لبه')}
            ${badge(!!node.host, 'Host', node.host || 'بدون هدر Host')}
          </div>
        </div>`).join('')}
      ${planned.length ? `<div class="kv-line" style="margin-top:10px"><span>در انتظار پورت اختصاصی</span><b style="font-size:11px">${planned.map((item) => esc(item.tag)).join(' · ')}</b></div>` : ''}`;
  }

  /* ------------------------------------------------------------- explorer */
  renderExplorerUsers() {
    const select = $('#exploreUser');
    if (!select) return;
    const users = this.store.get('users');
    const current = this.store.get('exploreUser') || (users[0] ? users[0].username : '');
    this.store.set('exploreUser', current);
    select.innerHTML = users.length
      ? users.map((user) => `<option value="${esc(user.username)}"${user.username === current ? ' selected' : ''}>${esc(user.username)} · ${esc((user.protocol || 'vless').toUpperCase())}</option>`).join('')
      : '<option value="">کاربری وجود ندارد</option>';
    select.onchange = () => {
      this.store.set('exploreUser', select.value);
      this.app.safe(() => this.renderExplorer());
    };
    this.app.safe(() => this.renderExplorer());
  }

  async renderExplorer() {
    const host = $('#exploreLinks');
    if (!host) return;
    const username = this.store.get('exploreUser');
    if (!username) {
      host.innerHTML = `<div class="empty">${ico('link', 30)}<div>ابتدا یک کاربر بسازید</div></div>`;
      return;
    }
    host.innerHTML = '<div class="skel" style="height:90px"></div>';
    const data = await this.api.get(`/api/users/${encodeURIComponent(username)}/links`);
    host.innerHTML = data.nodes.map((node) => `
      <div class="sub-card" style="margin-top:9px">
        <div style="display:flex;align-items:center;gap:9px">
          <span class="node-icon ${node.kind === 'cloudflare' ? 'cf' : ''}" style="width:26px;height:26px;flex:0 0 26px;font-size:10px">${node.kind === 'cloudflare' ? '☁' : 'R'}</span>
          <b style="font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(node.name)}</b>
          <span class="lat ${StatusKit.latencyTone(node.latency_ms)}">${StatusKit.latencyText(node.latency_ms)}</span>
        </div>
        <div class="link-box">
          <div class="lb-main"><b>لینک مستقیم ${esc((data.protocol || 'vless').toUpperCase())}</b><code>${esc(node.links.primary)}</code></div>
          <button class="copy-btn" data-copy="${esc(node.links.primary)}">${ico('copy', 14)}</button>
        </div>
        <div class="link-box" style="margin-top:6px">
          <div class="lb-main"><b>سابلینک اختصاصی این نود</b><code>${esc(node.subscription)}</code></div>
          <button class="copy-btn" data-copy="${esc(node.subscription)}">${ico('copy', 14)}</button>
        </div>
      </div>`).join('');
    bindCopyButtons(host, this.toasts);
  }

  /* ---------------------------------------------------------------- actions */
  async pingOne(node, button) {
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spin-inline"></span>';
    try {
      const result = await this.api.post('/api/nodes/ping', { nodes: [node.name] });
      const probe = (result.results || [])[0];
      this.toasts[probe?.ok ? 'ok' : 'err'](probe?.ok
        ? `${node.name} پاسخ داد · ${Fmt.lat().format(probe.latency_ms)} ms`
        : `${node.name} پاسخ نداد (${probe?.error || 'timeout'})`, 4200);
      await this.app.reloadNodes();
      await this.app.loadMetrics();
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  async pingAll(button) {
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spin-inline"></span> در حال پینگ…';
    try {
      const data = await this.api.post('/api/nodes/ping', {});
      this.store.set('nodes', data.nodes || []);
      this.render();
      this.renderCoverage();
      const average = data.avg_latency_ms != null ? ` · میانگین ${Fmt.lat().format(data.avg_latency_ms)} ms` : '';
      this.toasts.show(`${Fmt.num(data.healthy)} از ${Fmt.num(data.probed)} نود پاسخ دادند${average}`, data.failed ? 'info' : 'ok', 5200);
      await this.app.loadMetrics();
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  async sync(button) {
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spin-inline"></span> Sync…';
    try {
      const result = await this.api.post('/api/nodes/sync', {});
      this.toasts.ok(`${Fmt.num(result.synced)} نود همگام شد`);
      await this.app.reloadNodes();
      await this.app.loadMetrics();
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  async toggle(node) {
    await this.api.put(`/api/nodes/${encodeURIComponent(node.name)}`, { enabled: node.enabled ? 0 : 1 });
    this.toasts.ok(`نود ${node.name} ${node.enabled ? 'غیرفعال' : 'فعال'} شد`);
    await this.app.reloadNodes();
    await this.app.loadMetrics();
  }

  async remove(node) {
    const confirmed = await this.modals.ask('حذف نود', `نود «${node.name}» حذف شود؟ در Sync بعدی ممکن است دوباره ساخته شود.`);
    if (!confirmed) return;
    await this.api.delete(`/api/nodes/${encodeURIComponent(node.name)}`);
    this.toasts.ok('نود حذف شد');
    await this.app.reloadNodes();
  }

  /* ----------------------------------------------------------------- modals */
  modal(node = null) {
    const isEdit = !!node;
    const modal = this.modals.open({
      title: isEdit ? `ویرایش نود ${node.name}` : 'نود جدید',
      subtitle: 'نود دستی در کاتالوگ ذخیره می‌شود و در همه سابلینک‌ها منتشر می‌شود.',
      body: `<div class="field-grid">
          <div><label>نام نود</label><input id="ndName" dir="ltr" ${isEdit ? 'disabled' : ''} placeholder="railway-eu"></div>
          <div><label>نوع</label><select id="ndKind"><option value="railway">Railway</option><option value="cloudflare">Cloudflare</option></select></div>
        </div>
        <div class="field-grid">
          <div><label>سرور / IP</label><input id="ndServer" dir="ltr" placeholder="nexus.up.railway.app"></div>
          <div><label>پورت</label><input id="ndPort" type="number" min="1" max="65535" value="443" dir="ltr"></div>
        </div>
        <div class="field-grid">
          <div><label>SNI <span class="hint">اختیاری</span></label><input id="ndSni" dir="ltr" placeholder="nexus.up.railway.app"></div>
          <div><label>Host header <span class="hint">اختیاری</span></label><input id="ndHost" dir="ltr" placeholder="worker.example.workers.dev"></div>
        </div>
        <div class="switch-row"><div class="txt"><b>TLS</b><span>لبه Railway/Cloudflare روی HTTPS است</span></div><div class="switch on" id="ndTls"></div></div>
        <div class="switch-row"><div class="txt"><b>فعال</b><span>در سابلینک‌ها منتشر شود</span></div><div class="switch on" id="ndEnabled"></div></div>`,
      footer: `<div class="actions" style="margin:0"><button class="primary" id="ndSave">${isEdit ? 'ذخیره تغییرات' : 'افزودن نود'}</button><button class="secondary" data-close>انصراف</button></div>`,
    });
    const field = (selector) => $(selector, modal.el);
    [field('#ndTls'), field('#ndEnabled')].forEach((sw) => { sw.onclick = () => sw.classList.toggle('on'); });
    if (isEdit) {
      field('#ndName').value = node.name;
      field('#ndKind').value = node.kind;
      field('#ndServer').value = node.server;
      field('#ndPort').value = node.port;
      field('#ndSni').value = node.sni || '';
      field('#ndHost').value = node.host || '';
      field('#ndTls').classList.toggle('on', !!node.tls);
      field('#ndEnabled').classList.toggle('on', !!node.enabled);
    }
    field('#ndSave').onclick = () => this.app.safe(async () => {
      const save = field('#ndSave');
      const payload = {
        name: field('#ndName').value.trim(),
        kind: field('#ndKind').value,
        server: field('#ndServer').value.trim(),
        port: Number(field('#ndPort').value) || 443,
        sni: field('#ndSni').value.trim() || null,
        host: field('#ndHost').value.trim() || null,
        tls: field('#ndTls').classList.contains('on') ? 1 : 0,
        enabled: field('#ndEnabled').classList.contains('on') ? 1 : 0,
      };
      if (!payload.name || !payload.server) { this.toasts.err('نام و سرور الزامی است'); return; }
      save.disabled = true;
      save.innerHTML = '<span class="spin-inline"></span> ذخیره…';
      try {
        if (isEdit) {
          const { name, ...rest } = payload;
          await this.api.put(`/api/nodes/${encodeURIComponent(node.name)}`, rest);
        } else {
          await this.api.post('/api/nodes', payload);
        }
        modal.close();
        this.toasts.ok(isEdit ? 'نود بروزرسانی شد' : 'نود افزوده شد');
        await this.app.reloadNodes();
      } catch (error) {
        this.app.report(error);
        save.disabled = false;
        save.textContent = isEdit ? 'ذخیره تغییرات' : 'افزودن نود';
      }
    });
  }

  async linksModal(node) {
    const users = this.store.get('users');
    const modal = this.modals.open({
      title: `لینک‌های نود ${node.name}`,
      subtitle: `${node.kind} · ${node.server}:${node.port}`,
      size: 'wide',
      body: `<label>کاربر</label><select id="nodeLinkUser">${users.map((user) => `<option value="${esc(user.username)}">${esc(user.username)}</option>`).join('') || '<option value="">—</option>'}</select>
             <div id="nodeLinkBody" style="margin-top:14px"></div>`,
    });
    const render = () => this.app.safe(async () => {
      const body = $('#nodeLinkBody', modal.el);
      const username = $('#nodeLinkUser', modal.el).value;
      if (!username) { body.innerHTML = '<div class="empty">کاربری وجود ندارد</div>'; return; }
      body.innerHTML = '<div class="skel" style="height:120px"></div>';
      const data = await this.api.get(`/api/users/${encodeURIComponent(username)}/links?node=${encodeURIComponent(node.name)}`);
      const item = (data.nodes || [])[0];
      if (!item) {
        body.innerHTML = '<div class="empty">این نود در کاتالوگ فعال نیست (ممکن است غیرفعال یا بدون Probe باشد)</div>';
        return;
      }
      const rows = [
        ['لینک مستقیم پروتکل کاربر', item.links.primary],
        ['VLESS', item.links.vless],
        ['Trojan', item.links.trojan],
        ['VMess', item.links.vmess],
        // One subscription per protocol/transport pair on this single node.
        ...(item.transport_subscriptions || []).map((sub) => [`سابلینک ترنسپورت — ${sub.label}`, sub.url]),
        ...item.subscriptions.map((sub) => [`سابلینک فرمت — ${sub.label}`, sub.url]),
        ...(item.clients || []).map((client) => [`سابلینک ${client.name} — همین نود`, client.url]),
      ].filter(([, value]) => value);
      body.innerHTML = rows.map(([label, value]) => `
        <div class="link-box">
          <div class="lb-main"><b>${esc(label)}</b><code>${esc(value)}</code></div>
          <button class="copy-btn" data-copy="${esc(value)}">${ico('copy', 14)}</button>
        </div>`).join('');
      bindCopyButtons(body, this.toasts);
    });
    $('#nodeLinkUser', modal.el).onchange = render;
    render();
  }

  bindEvents() {
    const search = $('#nodeSearch');
    if (search) search.oninput = (event) => { this.store.set('nodeSearch', event.target.value); this.render(); };
    const sort = $('#nodeSort');
    if (sort) sort.onchange = (event) => { this.store.set('nodeSort', event.target.value); this.render(); };
    const pingAll = $('#btnPingNodes');
    if (pingAll) pingAll.onclick = () => this.app.safe(() => this.pingAll(pingAll));
    const add = $('#btnAddNode');
    if (add) add.onclick = () => this.modal(null);
    const sync = $('#btnSync');
    if (sync) sync.onclick = () => this.app.safe(() => this.sync(sync));
  }
}
