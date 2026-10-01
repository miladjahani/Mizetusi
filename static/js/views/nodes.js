/* =============================================================================
   NEXUS · view — node catalog: filtering, CRUD, coverage matrix, per-node
   subscription explorer and the real ping actions.
   ========================================================================== */
import { $, $$, ico, esc, Fmt, bindCopyButtons } from '../core.js';
import { Charts, StatusKit, flagOf } from '../ui.js';

// Node kinds are about *where* an entry comes from, independent of the platform:
// 'railway' is this deployment's own hostname (the origin, on any provider),
// 'cloudflare' is a clean IP of an edge source and 'edge' is a clean domain.
const FILTERS = [
  { id: 'all', label: 'همه' }, { id: 'railway', label: 'مستقیم (Origin)' },
  { id: 'cloudflare', label: 'آی‌پی تمیز' }, { id: 'edge', label: 'دامنهٔ تمیز' },
  { id: 'disabled', label: 'غیرفعال' },
];

// A catalog can hold hundreds of clean IPs; only a screenful is painted at once
// and the rest waits behind one «نمایش بیشتر» button.
const PAGE = 24;

export class NodesView {
  constructor(app) {
    this.app = app;
    // Names a scan produced but has not published, ticked in «نودهای اسکن‌شده».
    this.candidateSelection = new Set();
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
        this.store.set('nodeLimit', PAGE);
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
      list = list.filter((node) => [node.name, node.server, node.sni, node.host, node.kind, node.location, node.provider]
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

  /* Why a node has (or has not) a latency.

     The probe records the TLS handshake a client itself performs, so a location
     whose Host/SNI its addresses cannot serve is described here instead of only
     showing a red dash. */
  probeTitle(node) {
    const probe = node.probe || {};
    if (probe.at && probe.ok) {
      const kind = probe.tls ? 'TLS' : 'TCP';
      return `${kind} دست‌داد · ${probe.verified ? 'گواهی معتبر' : 'گواهی تأیید نشد'}`;
    }
    if (probe.hint) return probe.hint;
    if (probe.error) return probe.error;
    if (node.latency_ms == null) return 'هنوز پینگ نشده';
    return Number(node.latency_ms) < 0 ? 'آخرین پینگ ناموفق بود' : 'تأخیر اندازه‌گیری‌شده';
  }

  /* --------------------------------------------------------------------- list */
  /**
   * A warning when the address of a node really sits in another country.
   *
   * The flag a client shows comes from `node.location`, so a node whose address
   * measures elsewhere would carry a flag its own IP contradicts — the exact
   * report that started this. It cannot normally happen (locations are
   * re-labelled, and a foreign address is not published in one), so this is the
   * smoke alarm for the cases the panel cannot fix by itself: a clean domain, or
   * a location an admin pinned.
   */
  /* A location as its own flag chip rather than a bare slug: it is the same
     flag the user's client shows beside the node this one generates, so a row
     here and a row there agree. A label that is not a country code stays text. */
  locPill(node) {
    const code = String(node.location || '').trim().toLowerCase();
    const country = String(node.geo?.country || '').trim().toLowerCase();
    if (!code && !country) return '';
    const flag = flagOf(code) || flagOf(country);
    const label = /^[a-z]{2}$/.test(code) ? code.toUpperCase() : (code || country).toUpperCase();
    return ` <span class="pill info" style="padding:2px 8px;font-size:9.5px;gap:5px">${flag ? `<span class="fl" style="font-size:12px;line-height:1">${esc(flag)}</span>` : ''}${esc(label)}</span>`;
  }

  geoBadge(node) {
    const info = node.geo || {};
    if (!info.measured || !info.country || info.country === (node.location || '').toLowerCase()) return '';
    return `<span class="pill warn" style="padding:2px 8px;font-size:9.5px" dir="ltr" title="کشور این آدرس از خود آدرس پرسیده شده: ${esc(info.country.toUpperCase())} — با برچسب لوکیشن (${esc((node.location || '').toUpperCase())}) نمی‌خواند">IP: ${esc(info.country.toUpperCase())}</span>`;
  }

  /* ------------------------------------------------- scanned-node candidates
     A scan never publishes an address by itself: every freshly discovered edge
     node waits in this list until an admin ticks it. The kept selection survives
     a re-render so ticking a dozen nodes and then syncing does not lose the
     ticks. */
  renderCandidates() {
    const host = $('#candidateList');
    if (!host) return;
    const auto = this.store.get('settings')?.edge_auto_publish === '1';
    const toggle = $('#nodeAutoSwitch');
    if (toggle) toggle.classList.toggle('on', auto);
    const candidates = this.candidateList();
    const pill = $('#candidatePill');
    if (pill) {
      pill.className = candidates.length ? 'pill warn' : 'pill ok';
      pill.textContent = candidates.length ? `${Fmt.num(candidates.length)} نود تازه` : 'بدون نود تازه';
    }
    if (!candidates.length) {
      host.innerHTML = `<div class="empty">${ico('check', 26)}<div>نود اسکن‌شدهٔ تأییدنشده‌ای نیست</div>
        <div class="muted" style="margin-top:6px">هر اسکن، نودهای تازه را همین‌جا نگه می‌دارد تا خودتان انتخاب کنید.</div></div>`;
      return;
    }
    host.innerHTML = candidates.map((node, index) => `
      <label class="node-row" style="--i:${index};cursor:pointer">
        <input type="checkbox" data-candidate="${esc(node.name)}" ${this.candidateSelection.has(node.name) ? 'checked' : ''} style="width:auto;margin:0 6px 0 0">
        <span class="node-icon ${node.kind === 'cloudflare' ? 'cf' : ''}">${node.kind === 'cloudflare' ? '☁' : 'R'}</span>
        <div class="node-main">
          <b>${esc(node.name)}${this.locPill(node)}${node.provider ? ` <span class="pill" style="padding:2px 8px;font-size:9.5px">${esc(node.provider)}</span>` : ''}</b>
          <span>${esc(node.kind)} · ${esc(node.server)}:${esc(node.port)}${node.sni ? ` · SNI ${esc(node.sni)}` : ''}</span>
        </div>
        <span class="lat ${StatusKit.latencyTone(node.latency_ms)}" title="${esc(this.probeTitle(node))}">${StatusKit.latencyText(node.latency_ms)}</span>
      </label>`).join('');
    $$('[data-candidate]', host).forEach((box) => {
      box.onchange = () => {
        if (box.checked) this.candidateSelection.add(box.dataset.candidate);
        else this.candidateSelection.delete(box.dataset.candidate);
      };
    });
  }

  /*
   * The fleet as a picture: one bar per node (tallest = fastest) drawn with the
   * same latency tone the node rows carry, plus one chip per location showing
   * the flag a client will show for the same node.
   *
   * It deliberately reads the *whole* catalog, not the filtered, paged list —
   * the shape of everything the deployment publishes is the point, and a filter
   * that hides the sick half would hide exactly what this card exists to show.
   */
  renderPulse() {
    const host = $('#nodePulse');
    if (!host) return;
    const nodes = this.store.get('nodes') || [];
    const enabled = nodes.filter((node) => node.enabled).length;
    const pill = $('#pulsePill');
    if (pill) {
      pill.className = `pill ${enabled ? 'ok' : 'warn'}`;
      pill.innerHTML = `<i class="dot${enabled ? '' : ' warn'}"></i> ${Fmt.num(enabled)} از ${Fmt.num(nodes.length)} فعال`;
    }
    if (!nodes.length) {
      host.innerHTML = `<div class="empty">${ico('server', 30)}<div>هنوز نودی در کاتالوگ نیست</div>
        <div class="muted" style="margin-top:6px">از «نمونه‌های آماده» شروع کنید یا Sync بزنید.</div></div>`;
      return;
    }
    const ordered = nodes.slice().sort((a, b) => (a.latency_ms ?? 1e9) - (b.latency_ms ?? 1e9));
    const spectrum = Charts.spectrum(ordered.slice(0, 48).map((node) => ({
      value: node.latency_ms != null && Number(node.latency_ms) >= 0 ? Number(node.latency_ms) : 0,
      tone: StatusKit.latencyTone(node.latency_ms),
      title: `${node.name} · ${StatusKit.latencyText(node.latency_ms)}`,
    })));
    const groups = new Map();
    nodes.forEach((node) => {
      const key = String(node.location || node.geo?.country || '').trim().toLowerCase() || 'other';
      const entry = groups.get(key) || { key, count: 0, latency: [] };
      entry.count += 1;
      if (node.latency_ms != null && Number(node.latency_ms) >= 0) entry.latency.push(Number(node.latency_ms));
      groups.set(key, entry);
    });
    const average = (values) => (values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null);
    const tones = { good: '#3ee6a0', mid: '#c9f24c', bad: '#ffc85c', off: 'rgba(255,255,255,.28)' };
    const chips = [...groups.values()]
      .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key))
      .map((group) => {
        const ms = average(group.latency);
        const tone = StatusKit.latencyTone(ms);
        const flag = flagOf(group.key);
        const label = /^[a-z]{2}$/.test(group.key) ? group.key.toUpperCase() : group.key;
        return `<span class="loc-chip" title="${esc(`${label} · ${Fmt.num(group.count)} نود · میانگین تاخیر ${StatusKit.latencyText(ms)}`)}">
          ${flag ? `<span class="fl">${esc(flag)}</span>` : ''}${esc(label)}
          <span class="ld" style="background:${tones[tone]};color:${tones[tone]}"></span>
          <b>${Fmt.num(group.count)}</b></span>`;
      }).join('');
    host.innerHTML = `${spectrum}<div class="loc-strip">${chips}</div>`;
  }

  candidateList() {
    return (this.store.get('nodes') || []).filter((node) => !node.enabled
      && (node.kind === 'cloudflare' || node.kind === 'edge'));
  }

  async publishCandidates(button) {
    const names = [...this.candidateSelection];
    if (!names.length) {
      this.toasts.err('هیچ نودی انتخاب نشده');
      return;
    }
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spin-inline"></span> در حال افزودن…';
    try {
      await this.api.post('/api/nodes/select', { names, enabled: true });
      this.candidateSelection.clear();
      this.toasts.ok(`${Fmt.num(names.length)} نود به کاتالوگ منتشر شد`);
      await this.app.reloadNodes();
      await this.app.loadMetrics();
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  async toggleAuto(button) {
    const next = !button.classList.contains('on');
    const result = await this.api.post('/api/nodes/selection', { auto: next });
    button.classList.toggle('on', !!result.auto);
    const settings = this.store.get('settings');
    if (settings) settings.edge_auto_publish = result.auto ? '1' : '0';
    this.toasts.ok(result.auto ? 'نودهای اسکن‌شده خودکار منتشر می‌شوند' : 'انتخاب دستی فعال شد');
    if (result.auto) {
      this.candidateSelection.clear();
      await this.app.reloadNodes();
    }
  }

  render() {
    const host = $('#nodeList');
    if (!host) return;
    this.renderCandidates();
    this.renderPulse();
    const list = this.visible();
    if (!list.length) {
      host.innerHTML = `<div class="empty">${ico('server', 34)}<div>نودی با این فیلتر پیدا نشد</div>
        <div class="muted" style="margin-top:6px">از «نود جدید» استفاده کنید یا Sync کنید.</div></div>`;
      return;
    }
    const shown = list.slice(0, this.store.get('nodeLimit') || PAGE);
    host.innerHTML = shown.map((node, index) => `
      <div class="node-row" style="--i:${index};border-color:${node.enabled ? 'transparent' : 'rgba(255,107,129,.18)'}">
        <span class="node-icon ${node.kind === 'cloudflare' ? 'cf' : ''}">${node.kind === 'cloudflare' ? '☁' : 'R'}</span>
        <div class="node-main">
          <b>${esc(node.name)} ${node.enabled ? '' : '<span class="pill bad" style="padding:2px 8px;font-size:9.5px">غیرفعال</span>'        }${this.locPill(node)}${this.geoBadge(node)}${node.provider ? ` <span class="pill" style="padding:2px 8px;font-size:9.5px">${esc(node.provider)}</span>` : ''}</b>
          <span>${esc(node.kind)} · ${esc(node.server)}:${esc(node.port)}${node.sni ? ` · SNI ${esc(node.sni)}` : ''}${node.host && node.host !== node.server ? ` · HOST ${esc(node.host)}` : ''}${node.probe && node.probe.hint ? ` · <span class="muted">${esc(node.probe.hint)}</span>` : ''}</span>
        </div>
        <span class="lat ${StatusKit.latencyTone(node.latency_ms)}" title="${esc(this.probeTitle(node))}">${StatusKit.latencyText(node.latency_ms)}</span>
        <div class="acts">
          <button class="tbtn info" data-act="ping" data-name="${esc(node.name)}" title="پینگ همین نود">${ico('activity', 13)}</button>
          <button class="tbtn info" data-act="links" data-name="${esc(node.name)}" title="سابلینک این نود">${ico('link', 13)}</button>
          <button class="tbtn ${node.enabled ? 'ok' : ''}" data-act="toggle" data-name="${esc(node.name)}" title="${node.enabled ? 'غیرفعال کردن' : 'فعال کردن'}">${ico('power', 13)}</button>
          <button class="tbtn" data-act="edit" data-name="${esc(node.name)}" title="ویرایش">${ico('edit', 13)}</button>
          <button class="tbtn bad" data-act="del" data-name="${esc(node.name)}" title="حذف">${ico('trash', 13)}</button>
        </div>
      </div>`).join('') + (list.length > shown.length
      ? `<button class="secondary more-row" data-more="node">${ico('chevron', 13)} نمایش ${Fmt.num(Math.min(PAGE, list.length - shown.length))} نود بعدی · ${Fmt.num(list.length - shown.length)} باقی‌مانده</button>` : '');

    const more = $('#nodeList [data-more]');
    if (more) more.onclick = () => {
      this.store.set('nodeLimit', (this.store.get('nodeLimit') || PAGE) + PAGE);
      this.render();
    };

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
    // The advanced transports that are switched on but whose own port is not
    // reachable yet — the one state that is neither published nor merely off.
    const pending = ((matrix.obfuscation || {}).profiles || [])
      .filter((item) => item.enabled && !item.reachable);
    if (!nodes.length) {
      host.innerHTML = `<div class="empty">${ico('nodes', 32)}<div>نودی برای بررسی پوشش نیست</div></div>`;
      return;
    }
    const badge = (ok, label, title) => `<span class="chip" style="padding:3px 9px;font-size:10px;${ok ? 'border-color:rgba(62,230,160,.3);color:#8ef0c0;background:rgba(62,230,160,.07)' : 'opacity:.45'}" title="${esc(title)}">${ok ? '✓' : '×'} ${esc(label)}</span>`;
    const users = this.store.get('users');
    // A user now carries a protocol *set*, so each protocol is counted by
    // membership - a user with every protocol on counts in every row.
    const count = (id) => users.filter((user) => (user.protocols || ['vless']).includes(id)).length;
    const enabled = nodes.filter((node) => node.enabled).length;
    const clients = this.store.get('settings')?.clients?.length || this.store.get('clientCount') || 16;
    const ciphers = (matrix.ss_methods || []).map((method) => method.replace('2022-blake3-', '')).join(' · ');
    host.innerHTML = `
      <div class="kv-list" style="margin-bottom:12px">
        <div class="kv-line"><span>ترکیب‌های قابل انتشار</span><b>${Fmt.num(enabled * profiles.length)} لینک (${Fmt.num(enabled)} نود × ${Fmt.num(profiles.length)} پروتکل/ترنسپورت)</b></div>
        <div class="kv-line"><span>سابلینک اختصاصی هر کلاینت</span><b>${Fmt.num(clients)} کلاینت × ${Fmt.num(enabled)} نود</b></div>
        <div class="kv-line"><span>کاربرهای هر پروتکل</span><b>VLESS ${Fmt.num(count('vless'))} · VMess ${Fmt.num(count('vmess'))} · Trojan ${Fmt.num(count('trojan'))} · SS ${Fmt.num(count('ss'))}</b></div>
        ${ciphers ? `<div class="kv-line"><span>ShadowSocks منتشر‌شده</span><b>${esc(ciphers)}</b></div>` : ''}
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
      ${pending.length ? `<div class="kv-line" style="margin-top:10px"><span>مبهم‌سازی پیشرفته در انتظار پورت</span><b style="font-size:11px">${pending.map((item) => esc(item.tag)).join(' · ')}</b></div>` : ''}`;
  }

  /* ------------------------------------------------------------- explorer */
  renderExplorerUsers() {
    const select = $('#exploreUser');
    if (!select) return;
    const users = this.store.get('users');
    const current = this.store.get('exploreUser') || (users[0] ? users[0].username : '');
    this.store.set('exploreUser', current);
    select.innerHTML = users.length
      ? users.map((user) => `<option value="${esc(user.username)}"${user.username === current ? ' selected' : ''}>${esc(user.username)} · ${esc(user.protocol_label || 'همه پروتکل‌ها')}</option>`).join('')
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
    let data;
    try {
      data = await this.api.get(`/api/users/${encodeURIComponent(username)}/links`);
    } catch (error) {
      host.innerHTML = `<div class="empty">${ico('alert', 28)}<div>${esc(error.message)}</div></div>`;
      return;
    }
    // The payload is the server's: if a proxy answered instead of the API (or the
    // node catalog is empty) there is no ``nodes`` array to paint, and an
    // unguarded ``data.nodes.map`` used to crash this whole section.
    const items = Array.isArray(data?.nodes) ? data.nodes : [];
    if (!items.length) {
      host.innerHTML = `<div class="empty">${ico('link', 30)}<div>نودی برای این کاربر منتشر نشده</div>
        <div class="muted" style="margin-top:6px">نودهای لبه را از بخش «شبکه و لبه» بسازید یا محدودهٔ نودی کاربر را بازتر کنید.</div></div>`;
      return;
    }
    host.innerHTML = items.map((node) => `
      <div class="sub-card" style="margin-top:9px">
        <div style="display:flex;align-items:center;gap:9px">
          <span class="node-icon ${node.kind === 'cloudflare' ? 'cf' : ''}" style="width:26px;height:26px;flex:0 0 26px;font-size:10px">${node.kind === 'cloudflare' ? '☁' : 'R'}</span>
          <b style="font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(node.name)}</b>
          <span class="lat ${StatusKit.latencyTone(node.latency_ms)}">${StatusKit.latencyText(node.latency_ms)}</span>
        </div>
        <div class="link-box">
          <div class="lb-main"><b>لینک مستقیم ${esc(data.protocol_label || 'همه پروتکل‌ها')}</b><code>${esc(node.links.primary)}</code></div>
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
          <div><label>نام نود</label><input id="ndName" dir="ltr" ${isEdit ? 'disabled' : ''} placeholder="direct-eu"></div>
          <div><label>نوع</label><select id="ndKind"><option value="railway">مستقیم (Origin)</option><option value="cloudflare">آی‌پی تمیز (CDN)</option><option value="edge">دامنهٔ تمیز</option></select></div>
        </div>
        <div class="field-grid">
          <div><label>سرور / IP</label><input id="ndServer" dir="ltr" placeholder="panel.example.com"></div>
          <div><label>پورت</label><input id="ndPort" type="number" min="1" max="65535" value="443" dir="ltr"></div>
        </div>
        <div class="field-grid">
          <div><label>SNI <span class="hint">اختیاری</span></label><input id="ndSni" dir="ltr" placeholder="panel.example.com"></div>
          <div><label>Host header <span class="hint">اختیاری</span></label><input id="ndHost" dir="ltr" placeholder="worker.example.workers.dev"></div>
        </div>
        <div class="switch-row"><div class="txt"><b>TLS</b><span>لبه (Worker/Cloudflare) روی HTTPS است</span></div><div class="switch on" id="ndTls"></div></div>
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

  /* ------------------------------------------------- ready-made node samples */
  /* Different settings, one click: clean IPs of several CDNs, the alternative
     Cloudflare ports, a clean domain, a plain-WS fallback and the origin. Each
     sample explains itself and never overwrites a node that already exists. */
  async samplesModal() {
    const data = await this.api.get('/api/nodes/samples');
    const samples = data.samples || [];
    const modal = this.modals.open({
      title: 'نمونه‌های آمادهٔ نود',
      subtitle: 'چند نود با تنظیمات مختلف — هر کدام را لازم داری اضافه کن (نودهای موجود دست‌نخورده می‌مانند)',
      size: 'wide',
      body: `<div class="kv-list" id="sampleHost">
          <div class="kv-line"><span>Host/SNI پیش‌فرض نمونه‌ها</span><b dir="ltr">${esc(data.host || '—')}</b></div>
          <div class="kv-line"><span>Worker</span><b dir="ltr">${esc(data.worker || 'تنظیم نشده')}</b></div>
        </div>
        <div id="sampleList" style="margin-top:12px"></div>`,
      footer: `<div class="actions" style="margin:0">
        <button class="primary" id="sampleAddAll">افزودن همه</button>
        <button class="secondary" id="sampleClear">حذف نمونه‌ها</button>
        <button class="secondary" data-close>بستن</button></div>`,
    });
    const paint = () => {
      $('#sampleList', modal.el).innerHTML = samples.map((sample) => {
        const node = sample.node;
        return `<div class="setting-row">
          <div class="txt">
            <b>${esc(sample.label)} ${sample.added ? '<span class="pill ok" style="padding:2px 8px;font-size:9.5px">افزوده شده</span>' : ''}</b>
            <p>${esc(sample.note)}</p>
            <p class="muted mono" dir="ltr" style="margin-top:5px">${esc(node.kind)} · ${esc(node.server)}:${esc(String(node.port))} · tls=${node.tls ? 'on' : 'off'} · sni=${esc(node.sni || '—')}${sample.nodes.length > 1 ? ` · ${Fmt.num(sample.nodes.length)} نود` : ''}</p>
            ${(sample.tags || []).length ? `<div class="picks" style="gap:6px;margin-top:7px">${sample.tags.map((tag) => `<span class="pill" style="padding:2px 8px;font-size:9.5px">${esc(tag)}</span>`).join('')}</div>` : ''}
          </div>
          <button class="secondary compact" data-sample="${esc(sample.id)}" ${sample.added ? 'disabled' : ''}>${sample.added ? 'موجود' : 'افزودن'}</button>
        </div>`;
      }).join('') || '<div class="empty">نمونه‌ای در دسترس نیست</div>';
      $$('[data-sample]', modal.el).forEach((button) => {
        button.onclick = () => this.app.safe(() => this.addSamples([button.dataset.sample], button, modal));
      });
    };
    paint();
    const all = $('#sampleAddAll', modal.el);
    if (all) all.onclick = () => this.app.safe(() => this.addSamples([], all, modal, true));
    const clear = $('#sampleClear', modal.el);
    if (clear) {
      clear.onclick = () => this.app.safe(async () => {
        const confirmed = await this.modals.ask('حذف نمونه‌ها', 'فقط نودهایی که از این نمونه‌ها ساخته شده‌اند حذف می‌شوند.', { confirmLabel: 'حذف' });
        if (!confirmed) return;
        const result = await this.api.delete('/api/nodes/samples');
        this.toasts.ok(`${Fmt.num((result.removed || []).length)} نود نمونه حذف شد`);
        await this.app.reloadNodes();
        await this.app.loadMetrics();
        samples.forEach((sample) => { sample.added = false; sample.present = []; });
        paint();
      });
    }
  }

  async addSamples(ids, button, modal, all = false) {
    const original = button ? button.innerHTML : '';
    if (button) { button.disabled = true; button.innerHTML = '<span class="spin-inline"></span> در حال ساخت…'; }
    try {
      const result = await this.api.post('/api/nodes/samples', all ? { all: true } : { ids });
      await this.app.reloadNodes();
      await this.app.loadMetrics();
      this.toasts.ok(`${Fmt.num((result.created || []).length)} نود ساخته و پینگ شد · ${Fmt.num(result.healthy)} سالم`, 5200);
      if (modal) await this.samplesRefreshInto(modal);
    } finally {
      if (button) { button.disabled = false; button.innerHTML = original; }
    }
  }

  /* Re-render the open samples modal after a change (keeps it in sync). */
  async samplesRefreshInto(modal) {
    const data = await this.api.get('/api/nodes/samples');
    const host = $('#sampleList', modal.el);
    if (!host || !data.samples) return;
    $('#sampleHost', modal.el).innerHTML = `
      <div class="kv-line"><span>Host/SNI پیش‌فرض نمونه‌ها</span><b dir="ltr">${esc(data.host || '—')}</b></div>
      <div class="kv-line"><span>Worker</span><b dir="ltr">${esc(data.worker || 'تنظیم نشده')}</b></div>`;
    host.innerHTML = data.samples.map((sample) => {
      const node = sample.node;
      return `<div class="setting-row">
        <div class="txt"><b>${esc(sample.label)} ${sample.added ? '<span class="pill ok" style="padding:2px 8px;font-size:9.5px">افزوده شده</span>' : ''}</b>
          <p>${esc(sample.note)}</p>
          <p class="muted mono" dir="ltr" style="margin-top:5px">${esc(node.kind)} · ${esc(node.server)}:${esc(String(node.port))} · tls=${node.tls ? 'on' : 'off'}</p></div>
        <button class="secondary compact" data-sample="${esc(sample.id)}" ${sample.added ? 'disabled' : ''}>${sample.added ? 'موجود' : 'افزودن'}</button>
      </div>`;
    }).join('');
    $$('[data-sample]', modal.el).forEach((button) => {
      button.onclick = () => this.app.safe(() => this.addSamples([button.dataset.sample], button, modal));
    });
  }

  bindEvents() {
    const samples = $('#btnNodeSamples');
    if (samples) samples.onclick = () => this.app.safe(() => this.samplesModal());
    const search = $('#nodeSearch');
    if (search) search.oninput = (event) => { this.store.set('nodeSearch', event.target.value); this.store.set('nodeLimit', PAGE); this.render(); };
    const sort = $('#nodeSort');
    if (sort) sort.onchange = (event) => { this.store.set('nodeSort', event.target.value); this.render(); };
    const pingAll = $('#btnPingNodes');
    if (pingAll) pingAll.onclick = () => this.app.safe(() => this.pingAll(pingAll));
    const add = $('#btnAddNode');
    if (add) add.onclick = () => this.modal(null);
    const sync = $('#btnSync');
    if (sync) sync.onclick = () => this.app.safe(() => this.sync(sync));
    const selectAll = $('#candidateSelectAll');
    if (selectAll) selectAll.onclick = () => {
      this.candidateList().forEach((node) => this.candidateSelection.add(node.name));
      this.renderCandidates();
    };
    const clear = $('#candidateClear');
    if (clear) clear.onclick = () => { this.candidateSelection.clear(); this.renderCandidates(); };
    const publish = $('#candidatePublish');
    if (publish) publish.onclick = () => this.app.safe(() => this.publishCandidates(publish));
    const auto = $('#nodeAutoSwitch');
    if (auto) auto.onclick = () => this.app.safe(() => this.toggleAuto(auto));
  }
}
