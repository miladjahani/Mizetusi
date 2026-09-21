/* =============================================================================
   NEXUS · view — Cloudflare (worker + edge IPs) and Settings (security,
   defaults, client links, PWA, system information and the audit log).
   ========================================================================== */
import { $, $$, ico, esc, Fmt, bindCopyButtons, copyText } from '../core.js';
import { Charts, StatusKit } from '../ui.js';

export class CloudflareView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  get modals() { return this.app.modals; }  render() {
    const worker = this.app.workerSettings();
    const nodes = this.store.get('nodes') || [];
    const cfNodes = nodes.filter((node) => node.kind === 'cloudflare' && node.enabled).length;
    // A Cloudflare-fronted panel domain feeds the catalog automatically, so the
    // section must not claim "Railway-only" while CF nodes are live.
    const runtime = this.store.get('edge')?.runtime || this.store.get('settings')?.runtime || {};
    const native = runtime.label || 'میزبان';
    const mode = worker.configured ? 'worker' : cfNodes > 0 ? 'edge' : 'origin';
    const url = $('#workerUrl');
    if (url && document.activeElement !== url) url.value = worker.url || '';
    const tag = $('#cfTag');
    if (tag) {
      tag.className = `pill ${mode === 'origin' ? 'warn' : 'ok'}`;
      tag.innerHTML = mode === 'worker' ? '<i class="dot"></i> Worker فعال'
        : mode === 'edge' ? '<i class="dot"></i> حالت خودکار (Edge)'
        : 'فقط نود مستقیم';
    }
    const stateBox = $('#workerState');
    if (stateBox) {
      const copy = {
        worker: ['Worker فعال است', 'Node Catalog از آی‌پی‌های سالم لوکیشن کلودفلر و از طریق Worker منتشر می‌شود.'],
        edge: ['حالت خودکار — بدون Worker', `دامنه پنل از طریق Cloudflare جلوه‌گذاری شده است؛ ${Fmt.num(cfNodes)} نود تمیز به‌صورت خودکار ساخته و پینگ شدند. اگر Worker هم مستقر کنید، همان آدرس اینجا ذخیره می‌شود.`],
        origin: ['لوکیشنی تنظیم نشده', `فقط نود مستقیم همین سرویس (${native}) منتشر می‌شود؛ از «منابع لبه و لوکیشن‌ها» می‌توانید آی‌پی تمیز یا دامنهٔ تمیز اضافه کنید.`],
      }[mode];
      stateBox.innerHTML = `<div class="txt"><b>${copy[0]}</b><p>${copy[1]}</p></div>
        <span class="pill ${mode === 'origin' ? 'warn' : 'ok'}">${esc((worker.url || '').replace(/^https?:\/\//, '') || (mode === 'edge' ? 'auto-detect' : native))}</span>`;
    }

    const ips = this.store.get('cfIps') || [];
    const metrics = this.store.get('metrics');
    const totals = metrics ? metrics.totals : null;
    const healthy = ips.filter((item) => item.ok).length;
    const stats = $('#cfStats');
    if (stats) {
      stats.innerHTML = `
        <div class="stat glass"><div class="top"><span class="lbl">IPهای جدول</span><span class="ico">${ico('globe', 15)}</span></div>
          <div class="val">${Fmt.num(totals ? totals.cf_ips_total : ips.length)}</div><div class="foot">ذخیره‌شده در دیتابیس</div></div>
        <div class="stat glass a-ok"><div class="top"><span class="lbl">سالم</span><span class="ico">${ico('check', 15)}</span></div>
          <div class="val">${Fmt.num(totals ? totals.cf_ips_ok : healthy)}</div><div class="foot">قابل استفاده در سابلینک</div></div>
        <div class="stat glass a-warn"><div class="top"><span class="lbl">نود CF منتشرشده</span><span class="ico">${ico('cloud', 15)}</span></div>
          <div class="val">${Fmt.num(totals ? totals.cloudflare_nodes : 0)}</div><div class="foot">در Node Catalog فعال</div></div>`;
    }

    const measured = ips.filter((item) => item.latency_ms != null);
    const worst = Math.max(...measured.map((item) => item.latency_ms), 60);
    Charts.bars($('#cfBars'), ips.slice(0, 8).map((item) => ({
      label: item.ip,
      value: item.latency_ms != null ? `${Fmt.lat().format(item.latency_ms)} ms` : 'تایم‌اوت',
      pct: item.latency_ms != null ? (item.latency_ms / worst) * 100 : 4,
      tone: item.ok ? StatusKit.barTone(item.latency_ms) : 'bad',
    })));

    const table = $('#cfTable');
    if (table) {
      table.innerHTML = ips.length ? ips.map((item) => `<tr>
        <td class="mono">${esc(item.ip)}</td><td>${esc(item.source || '—')}</td>
        <td><span class="lat ${StatusKit.latencyTone(item.latency_ms)}">${item.latency_ms != null ? `${Fmt.lat().format(item.latency_ms)} ms` : '—'}</span></td>
        <td><span class="pill ${item.ok ? 'ok' : 'bad'}">${item.ok ? 'سالم' : 'ناموفق'}</span></td>
        <td class="mono">${esc(item.last_probe ? Fmt.ago(item.last_probe) : '—')}</td></tr>`).join('')
        : '<tr><td colspan="5"><div class="empty">هنوز IP Probe نشده — دکمه «اجرای Probe» را بزنید (اسکن خودکار در بوت خاموش است تا تعداد اتصال‌های خروجی کم و قابل‌توضیح بماند)</div></td></tr>';
    }
  }

  /* ------------------------------------------------ edge sources + locations */
  /* The catalog is no longer Cloudflare-only: every location (a CDN's clean IPs,
     a hand-written list, a clean domain) is one source, and each one publishes
     the whole protocol matrix. */
  async loadEdge() {
    try {
      this.store.set('edge', await this.api.get('/api/edge'));
    } catch (error) {
      this.store.set('edge', null);
      if (!error.unauthorized) throw error;
    }
    this.fillProviders();
    this.renderEdge();
  }

  fillProviders() {
    const select = $('#edgeProvider');
    if (!select) return;
    const providers = (this.store.get('edge')?.providers || this.store.get('settings')?.edge?.providers || [])
      .filter((item) => item.scannable !== false && item.id !== 'domain');
    const current = select.value;
    select.innerHTML = providers.map((item) => `<option value="${esc(item.id)}">${esc(item.label)} · ${Fmt.num(item.total)} IP</option>`).join('')
      || '<option value="custom">آی‌پی دستی</option>';
    if (current && providers.some((item) => item.id === current)) select.value = current;
  }

  /* Whether a location really answers.

     The counts come from the last probe of that location's *own* nodes — the
     same TLS handshake with its Host/SNI that a client performs — so a location
     that nothing can reach is visible in the panel instead of only in a client.
     ``reason`` is the server's one-line explanation. */
  pingCell(source) {
    const healthy = Number(source.healthy || 0);
    const failed = Number(source.failed || 0);
    const pending = Number(source.pending || 0);
    const total = healthy + failed + pending;
    const title = source.reason || '';
    if (!total) return `<span class="lat off" title="${esc(title)}">—</span>`;
    if (healthy) {
      const ms = source.fastest_ms != null ? ` · ${Fmt.lat().format(source.fastest_ms)} ms` : '';
      return `<span class="lat good" title="${esc(title)}">${Fmt.num(healthy)}/${Fmt.num(total)}${ms}</span>`;
    }
    if (failed) return `<span class="lat bad" title="${esc(title)}">ناموفق · ${Fmt.num(failed)}</span>`;
    return `<span class="lat off" title="${esc(title)}">پینگ نشده</span>`;
  }

  renderEdge() {
    const data = this.store.get('edge');
    const runtime = $('#runtimeBox');
    if (runtime) {
      const info = data?.runtime || {};
      const direct = info.direct ? `${info.direct.host}:${info.direct.port}` : 'در دسترس نیست';
      const probing = data?.probing || {};
      const scanMode = probing.enabled === false ? 'خاموش (NEXUS_OUTBOUND_PROBE_ENABLED=0)'
        : probing.scan_on_boot ? 'خودکار در بوت + دستی'
        : 'فقط دستی — اسکن در بوت خاموش است';
      runtime.innerHTML = `
        <div class="kv-line"><span>محل اجرا</span><b>${esc(info.label || '—')}</b></div>
        <div class="kv-line"><span>دامنهٔ عمومی</span><b dir="ltr">${esc(info.host || '—')}</b></div>
        <div class="kv-line"><span>پورت مستقیم (Reality)</span><b dir="ltr">${esc(direct)}</b></div>
        <div class="kv-line"><span>مسیر دیتابیس</span><b dir="ltr">${esc(info.data_dir || '—')}</b></div>
        <div class="kv-line"><span>حالت اسکن خروجی</span><b>${esc(scanMode)}</b></div>
        ${probing.limit ? `<div class="kv-line"><span>اندازهٔ هر اسکن</span><b dir="ltr">${Fmt.num(probing.limit)} IP · ${Fmt.num(probing.concurrency || 8)} همزمان</b></div>` : ''}
        ${(info.notes || []).map((note) => `<div class="kv-line"><span>یادداشت</span><b style="font-family:Vazirmatn;direction:rtl;max-width:78%;white-space:normal">${esc(note)}</b></div>`).join('')}`;
    }
    const host = $('#edgeSources');
    if (host) {
      const sources = data?.sources || [];
      const nodes = data?.nodes || [];
      host.innerHTML = sources.length ? `<div class="table-wrap" style="margin-top:12px"><table>
        <thead><tr><th>لوکیشن</th><th>برچسب</th><th>نوع</th><th>Host / SNI</th><th>آی‌پی</th><th>نود</th><th>پینگ</th><th>وضعیت</th><th></th></tr></thead>
        <tbody>${sources.map((source) => {
          const count = nodes.filter((node) => node.enabled && node.source_id === source.id).length;
          return `<tr>
            <td><b>${esc(source.location || '—')}</b></td>
            <td>${esc(source.label || '')}</td>
            <td>${source.kind === 'domain' ? 'دامنهٔ تمیز' : esc(source.provider)}</td>
            <td class="mono" dir="ltr">${esc(source.host)}:${esc(String(source.port))}</td>
            <td>${Fmt.num(source.addresses != null ? source.addresses : '—')}</td>
            <td>${Fmt.num(count || source.nodes || 0)}</td>
            <td>${this.pingCell(source)}</td>
            <td><span class="pill ${source.enabled ? 'ok' : 'warn'}">${source.enabled ? 'فعال' : 'خاموش'}</span></td>
            <td class="nowrap">
              <button class="tbtn info" data-edge="ping" data-id="${esc(source.id)}" title="پینگ همین لوکیشن">${ico('activity', 12)} پینگ</button>
              <button class="tbtn" data-edge="toggle" data-id="${esc(source.id)}">${source.enabled ? 'خاموش' : 'روشن'}</button>
              <button class="tbtn bad" data-edge="del" data-id="${esc(source.id)}">حذف</button></td></tr>`;
        }).join('')}</tbody></table>
        ${sources.filter((source) => source.reason).map((source) => `<p class="note">⚠ ${esc(source.location || source.id)}: ${esc(source.reason)}</p>`).join('')}
        <p class="note">پینگ هر لوکیشن همان کاری را می‌کند که کلاینت می‌کند: دست‌دادن TLS با Host/SNI خودِ آن لوکیشن. آی‌پی‌ای که پاسخ ندهد از سابلینک کنار گذاشته می‌شود؛ ستون «آی‌پی» صفر یعنی این لوکیشن هیچ آدرسی ندارد (اسکن منابع یا آی‌پی دستی لازم است).</p></div>`
        : '<div class="empty" style="margin-top:12px">هنوز لوکیشنی ساخته نشده — با فرم بالا یکی اضافه کنید (یا فقط دامنهٔ پنل را پشت Cloudflare ببرید تا خودکار ساخته شود).</div>';
      $$('#edgeSources [data-edge]').forEach((button) => {
        button.onclick = () => this.app.safe(async () => {
          const id = button.dataset.id;
          if (button.dataset.edge === 'del') {
            const confirmed = await this.modals.ask('حذف لوکیشن', 'نودهای این لوکیشن از سابلینک همه کاربران حذف می‌شوند.', { confirmLabel: 'حذف' });
            if (!confirmed) return;
            await this.api.delete(`/api/edge/sources/${encodeURIComponent(id)}`);
            this.toasts.ok('لوکیشن حذف شد');
          } else if (button.dataset.edge === 'ping') {
            const original = button.innerHTML;
            button.disabled = true;
            button.innerHTML = '<span class="spin-inline"></span>';
            try {
              const result = await this.api.post(`/api/edge/sources/${encodeURIComponent(id)}/ping`, {});
              if (!result.probed) {
                this.toasts.err('این لوکیشن نودی ندارد — آی‌پی یا دامنه‌اش را بررسی کنید', 6000);
              } else if (result.healthy) {
                this.toasts.ok(`${Fmt.num(result.healthy)} از ${Fmt.num(result.probed)} آدرس این لوکیشن پینگ داد`, 5200);
              } else {
                this.toasts.err(`${Fmt.num(result.probed)} آدرس پینگ نشد — Host/SNI با آی‌پی‌ها سازگار نیست یا پورت بسته است`, 7000);
              }
            } finally {
              button.disabled = false;
              button.innerHTML = original;
            }
          } else {
            const result = await this.api.post(`/api/edge/sources/${encodeURIComponent(id)}/toggle`, {});
            if (result.ping && result.ping.probed) {
              this.toasts.ok(`${Fmt.num(result.ping.healthy)} از ${Fmt.num(result.ping.probed)} آدرس پینگ داد`, 4800);
            }
          }
          await this.loadEdge();
          await this.app.reloadNodes();
          await this.app.loadMetrics();
        });
      });
    }
    const stats = $('#edgeProviderStats');
    if (stats) {
      const providers = data?.providers || [];
      stats.innerHTML = providers.length ? `<div class="picks" style="gap:7px;margin-top:12px">${providers.map((item) => `
        <span class="pill ${item.healthy ? 'ok' : item.total ? 'warn' : ''}" title="${esc(item.note || '')}">${esc(item.label)} · ${Fmt.num(item.healthy)}/${Fmt.num(item.total)}</span>`).join('')}</div>`
        : '';
    }
  }

  async scanEdge(provider = '', button = null) {
    const original = button ? button.innerHTML : '';
    if (button) { button.disabled = true; button.innerHTML = '<span class="spin-inline"></span> در حال اسکن…'; }
    try {
      const result = await this.api.post('/api/edge/scan', provider ? { provider } : {});
      await this.loadEdge();
      await this.app.loadCfIps();
      await this.app.reloadNodes();
      await this.app.loadMetrics();
      this.toasts.ok(`${Fmt.num(result.found)} آی‌پی تازه · ${Fmt.num(result.probed)} Probe · ${Fmt.num(result.synced)} نود`, 5200);
    } finally {
      if (button) { button.disabled = false; button.innerHTML = original; }
    }
  }

  async loadWorkerCode(show = true, force = false) {
    const host = $('#workerCode');
    if (!host) return '';
    let code = this.store.get('workerCode');
    if (!code || force) {
      if (show) host.innerHTML = '<code>در حال دریافت کد…</code>';
      try {
        const data = await this.api.get('/api/cloudflare/worker-code');
        code = data.code || '';
        this.store.set('workerCode', code);
        const guide = $('#workerGuide');
        if (guide) {
          guide.innerHTML = (data.steps || []).map((step, index) => `<div class="kv-line"><span>مرحله ${Fmt.num(index + 1)}</span>
            <b style="font-family:Vazirmatn;direction:rtl;max-width:78%;white-space:normal">${esc(step)}</b></div>`).join('')
            + `<div class="kv-line"><span>آدرس سرویس داخل کد</span><b>${esc(data.origin)}</b></div>`;
        }
      } catch (error) {
        this.store.set('workerCode', '');
        if (show) host.innerHTML = `<code>${esc(error.message)}</code>`;
        return '';
      }
    }
    if (show) host.innerHTML = `<code>${esc(code)}</code>`;
    return code;
  }

  async probe(button) {
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spin-inline"></span> در حال Probe…';
    const progress = $('#cfProgress');
    if (progress) {
      progress.innerHTML = `<div class="setting-row"><div class="txt"><b>اسکن IPهای لبه</b><p>Probe با نرخ کم (${Fmt.num(this.store.get('edge')?.probing?.concurrency || 8)} همزمان) و فقط با درخواست شما انجام می‌شود؛ چند ثانیه طول می‌کشد.</p></div><span class="pill info">در جریان</span></div>`;
    }
    try {
      const result = await this.api.post('/api/cloudflare/refresh', {});
      this.toasts.ok(`${Fmt.num(result.probed)} IP بررسی شد · ${Fmt.num((result.best || []).length)} سالم`);
      await this.app.loadCloudflare();
      await this.app.loadMetrics();
    } finally {
      button.disabled = false;
      button.innerHTML = original;
      if (progress) progress.innerHTML = '';
    }
  }

  bindEvents() {
    const save = $('#saveWorker');
    if (save) {
      save.onclick = () => this.app.safe(async () => {
        const original = save.textContent;
        save.disabled = true;
        save.innerHTML = '<span class="spin-inline"></span> ذخیره…';
        try {
          await this.api.post('/api/settings/cloudflare-worker', {
            url: $('#workerUrl').value.trim(), api_key: $('#workerKey')?.value.trim() || '',
          });
          this.toasts.ok('تنظیمات Worker ذخیره و نودها Sync شد');
          await this.app.loadCloudflare();
          await this.app.reloadNodes();
          await this.app.loadMetrics();
        } finally {
          save.disabled = false;
          save.textContent = original;
        }
      });
    }
    const clear = $('#clearWorker');
    if (clear) {
      clear.onclick = () => this.app.safe(async () => {
        const confirmed = await this.modals.ask('حذف Worker', 'نودهای Cloudflare حذف و پنل در حالت Railway-only ادامه می‌دهد.', { confirmLabel: 'حذف Worker' });
        if (!confirmed) return;
        await this.api.post('/api/settings/cloudflare-worker', { url: '', api_key: '' });
        this.toasts.ok('Worker حذف شد');
        await this.app.loadCloudflare();
        await this.app.reloadNodes();
      });
    }
    const probe = $('#btnProbe');
    if (probe) probe.onclick = () => this.app.safe(() => this.probe(probe));
    const edgeScan = $('#btnEdgeScan');
    if (edgeScan) edgeScan.onclick = () => this.app.safe(() => this.scanEdge('', edgeScan));
    const edgeReload = $('#btnEdgeReload');
    if (edgeReload) edgeReload.onclick = () => this.app.safe(async () => { await this.loadEdge(); this.toasts.ok('منابع لبه بروزرسانی شد', 1600); });
    const edgeSave = $('#edgeSave');
    if (edgeSave) {
      edgeSave.onclick = () => this.app.safe(async () => {
        const payload = {
          kind: $('#edgeKind')?.value || 'ip',
          provider: $('#edgeProvider')?.value || 'custom',
          location: $('#edgeLocation')?.value.trim() || '',
          label: $('#edgeLabel')?.value.trim() || '',
          host: $('#edgeHost')?.value.trim() || '',
          port: Number($('#edgePort')?.value) || 443,
          max: Number($('#edgeMax')?.value) || 5,
          ips: $('#edgeIps')?.value.trim() || '',
        };
        let ping = null;
        try {
          ping = (await this.api.post('/api/edge/sources', payload))?.ping || null;
        } catch (error) {
          this.toasts.err(error.message || 'ذخیرهٔ لوکیشن ناموفق بود');
          return;
        }
        ['#edgeLabel', '#edgeHost', '#edgeIps'].forEach((selector) => { const el = $(selector); if (el) el.value = ''; });
        // The location is measured in the same request, so the answer is here
        // instead of in a client the admin has to go and check.
        if (ping && ping.probed) {
          (ping.healthy ? this.toasts.ok : this.toasts.err)(
            `لوکیشن ذخیره شد · ${Fmt.num(ping.healthy)} از ${Fmt.num(ping.probed)} آدرس پینگ داد`, 6000);
        } else if (ping) {
          this.toasts.err('لوکیشن ذخیره شد اما هیچ نودی ندارد — آی‌پی/دامنه را بررسی کنید', 7000);
        } else {
          this.toasts.ok('لوکیشن ساخته و Sync شد');
        }
        await this.loadEdge();
        await this.app.reloadNodes();
        await this.app.loadMetrics();
      });
    }
    const edgeKind = $('#edgeKind');
    if (edgeKind) edgeKind.onchange = () => {
      const domain = edgeKind.value === 'domain';
      const provider = $('#edgeProvider');
      const host = $('#edgeHost');
      if (provider) provider.disabled = domain;
      if (host) host.placeholder = domain ? 'nl.example.com' : 'cdn.example.com';
    };
    const refresh = $('#btnCfIps');
    if (refresh) {
      refresh.onclick = () => this.app.safe(async () => {
        await this.app.loadCfIps();
        this.render();
        this.toasts.ok('جدول IP بروزرسانی شد', 1800);
      });
    }
    const limit = $('#cfLimit');
    if (limit) {
      limit.onchange = () => this.app.safe(async () => {
        await this.app.loadCfIps();
        this.render();
      });
    }
    const code = $('#btnWorkerCode');
    if (code) code.onclick = () => this.app.safe(() => this.loadWorkerCode(true));
    const copy = $('#btnWorkerCopy');
    if (copy) {
      copy.onclick = () => this.app.safe(async () => {
        const source = await this.loadWorkerCode(false);
        if (!source) { this.toasts.err('کد ورکر دریافت نشد'); return; }
        await copyText(source, copy, this.toasts);
        this.toasts.ok('کد ورکر کپی شد', 1900);
      });
    }
    const download = $('#btnWorkerDownload');
    if (download) download.onclick = () => { location.href = '/api/cloudflare/worker-download'; };
    const test = $('#btnWorkerTest');
    if (test) {
      test.onclick = () => this.app.safe(async () => {
        const original = test.innerHTML;
        test.disabled = true;
        test.innerHTML = '<span class="spin-inline"></span> در حال تست…';
        try {
          const result = await this.api.post('/api/cloudflare/worker-test', {});
          if (result.ok) this.toasts.ok(`ورکر سالم است · ${Fmt.lat().format(result.latency_ms)} ms · ${result.health?.colo || 'CF'}`, 5200);
          else this.toasts.err(`ورکر پاسخ درست نداد (${result.detail || result.status || 'نامشخص'})`, 5200);
        } finally {
          test.disabled = false;
          test.innerHTML = original;
        }
      });
    }
  }
}

export class SettingsView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  get modals() { return this.app.modals; }

  render() {
    const settings = this.store.get('settings');
    if (!settings) return;
    const set = (id, value) => {
      const el = $(id);
      if (el && document.activeElement !== el) el.value = value ?? '';
    };
    const defaults = settings.defaults || {};
    const brand = settings.brand || {};
    set('#setBaseUrl', settings.public_base_url || '');
    set('#setPrefix', settings.sub_prefix || '');
    set('#setProtocol', defaults.protocol || 'all');
    set('#setLimit', defaults.limit_gb);
    set('#setExpiry', defaults.expiry_days);
    set('#setIpLimit', defaults.ip_limit);
    set('#setSessionDays', settings.session_days || settings.security?.session_days || '');
    set('#setPingInterval', settings.ping_interval || '');
    set('#setAppName', brand.app_name || 'NEXUS');
    set('#setAccent', brand.accent || '#5ad1ff');
    set('#setAccent2', brand.accent_secondary || '#8b7bff');
    set('#workerUrl', settings.worker?.url || '');

    const security = settings.security || {};
    const pill = $('#secPill');
    if (pill) {
      pill.className = `pill ${security.bootstrap_complete ? 'ok' : 'warn'}`;
      pill.textContent = security.bootstrap_complete ? 'رمز اولیه تغییر کرده' : 'رمز پیش‌فرض فعال است';
    }

    const core = this.store.get('core') || {};
    const metrics = this.store.get('metrics');
    const version = this.store.get('version');
    // The build hash is what tells an admin whether a phone is showing the
    // freshly deployed panel or a service-worker copy of the previous one.
    if (!version) this.app.safe(() => this.loadVersion());
    const count = metrics ? `${Fmt.num(metrics.totals.users)} کل · ${Fmt.num(metrics.totals.active_users)} فعال` : '—';
    const rows = [
      ['نسخه پنل', version ? `NEXUS ${version.version} · build ${version.build}` : 'NEXUS 9.2.0'],
      ['آدرس پایه', settings.resolved_base_url || location.origin],
      ['کاربران', count],
      ['نودهای فعال', metrics ? `${Fmt.num(metrics.totals.nodes_enabled)} از ${Fmt.num(metrics.totals.nodes)}` : '—'],
      ['هسته Xray', core.running ? `Running${core.pid ? ` · PID ${core.pid}` : ''}` : 'متوقف'],
      ['ترنسپورت', core.transport || 'WebSocket + TLS'],
      ['پروتکل‌های منتشرشده', (core.protocols || []).join(' · ')],
      ['اندپوینت‌های WebSocket', (core.endpoints || []).join(' · ')],
      // What the running engine really contains: a transport it rejected at startup
      // is never published, so this is where an admin sees the difference.
      ['پروفایل‌های سروشده', Array.isArray(core.served) ? `${Fmt.num(core.served.length)} پروفایل` : 'در انتظار راه‌اندازی هسته'],
      ['پروفایل‌های کنارگذاشته', (core.withheld || []).length ? (core.withheld || []).join(' · ') : 'ندارد'],
      ['ترکیب‌های نود × پروتکل', (settings.subscription?.transports || []).map((item) => item.label).join(' · ')],
      ['حالت Reality', (core.transports || []).includes('vless-reality') ? 'فعال (پورت مستقیم)' : 'نیازمند پورت TCP اختصاصی'],
      ['فرمت‌های سابلینک', (settings.subscription?.targets || []).join(' · ')],
      ['مدت نشست', `${Fmt.num(security.session_days || 7)} روز`],
      ['برنامه نصب‌شدنی (PWA)', settings.pwa?.installable ? 'فعال' : 'غیرفعال'],
      ['آپ‌تایم', metrics ? Fmt.until(metrics.uptime_seconds) : '—'],
    ];
    const sysInfo = $('#sysInfo');
    if (sysInfo) sysInfo.innerHTML = rows.map(([key, value]) => `<div class="kv-line"><span>${esc(key)}</span><b>${esc(value)}</b></div>`).join('');

    this.renderClientLinks(settings.clients || []);
    this.renderLogs();
    this.app.safe(() => this.renderWarp());
  }

  async loadVersion() {
    const data = await this.api.get('/api/version');
    this.store.set('version', data);
    if (this.store.get('section') === 'settings') this.render();
  }

  /* ------------------------------------------------------------- WARP exit node */
  async renderWarp() {
    const tag = $('#warpTag');
    if (!tag) return;
    const data = await this.api.get('/api/warp');
    this.store.set('warp', data);
    tag.className = `pill ${data.enabled ? 'ok' : data.registered ? 'info' : 'warn'}`;
    tag.textContent = data.enabled ? 'روشن' : data.registered ? 'ثبت‌شده، خاموش' : 'ثبت نشده';
    const box = $('#warpState');
    if (box) {
      box.innerHTML = `<div class="txt"><b>${data.registered ? esc(data.endpoint || 'WARP') : 'هنوز ثبت نشده است'}</b>
        <p>${esc(data.note || '')}${data.address ? ` · آدرس داخلی ${esc(data.address)}` : ''}${data.key_ready ? '' : ' · باینری Xray در دسترس نیست'}</p></div>`;
    }
    const toggle = $('#btnWarpToggle');
    if (toggle) toggle.textContent = data.enabled ? 'غیرفعال‌سازی' : 'فعال‌سازی';
    this.bindWarp();
  }

  bindWarp() {
    const actions = [
      ['#btnWarpRegister', 'register', 'WARP ثبت شد؛ برای انتشار نود، فعالش کنید'],
      ['#btnWarpToggle', this.store.get('warp')?.enabled ? 'disable' : 'enable', 'حالت WARP تغییر کرد'],
      ['#btnWarpDiscard', 'discard', 'WARP حذف شد'],
    ];
    actions.forEach(([selector, action, message]) => {
      const button = $(selector);
      if (!button) return;
      button.onclick = () => this.app.safe(async () => {
        const original = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<span class="spin-inline"></span>';
        try {
          await this.api.post('/api/warp', { action });
          this.toasts.ok(message);
          await this.renderWarp();
          await this.app.loadMetrics();
        } finally {
          button.disabled = false;
          button.innerHTML = original;
        }
      });
    });
  }

  renderLogs() {
    const logs = this.store.get('logs') || [];
    const count = $('#logCount');
    if (count) count.textContent = `${Fmt.num(logs.length)} رویداد`;
    const table = $('#logTable');
    if (!table) return;
    table.innerHTML = logs.length ? logs.map((log) => `<tr>
      <td class="mono">${Fmt.num(log.id)}</td><td>${esc(StatusKit.logLabel(log.action))}</td>
      <td class="mono" style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(log.detail || '—')}</td>
      <td class="mono nowrap">${esc(Fmt.dateTime(log.created_at))}</td></tr>`).join('')
      : '<tr><td colspan="4"><div class="empty">رویدادی ثبت نشده</div></td></tr>';
  }

  // These links are what the public status window shows each user, so they are
  // editable at runtime instead of being frozen in the front-end bundle.
  renderClientLinks(clients) {
    const host = $('#clientLinkFields');
    if (!host) return;
    const list = (clients || []).filter((client) => client.id !== 'smart');
    host.innerHTML = list.map((client) => `<div>
      <label>${esc(client.name)} <span class="hint">${esc(client.platform || '')}</span></label>
      <input data-client="${esc(client.id)}" dir="ltr" value="${esc(client.download || '')}" placeholder="https://…">
    </div>`).join('') || '<div class="muted">کلاینتی ثبت نشده است.</div>';
  }

  passwordStrength(value) {
    let score = 0;
    if (value.length >= 8) score++;
    if (value.length >= 14) score++;
    if (/[A-Z]/.test(value) && /[a-z]/.test(value)) score++;
    if (/\d/.test(value)) score++;
    if (/[^A-Za-z0-9]/.test(value)) score++;
    return Fmt.clamp(Math.round((score / 5) * 100), 0, 100);
  }

  previewSubscriptions() {
    const settings = this.store.get('settings');
    const base = ($('#setBaseUrl').value.trim() || settings?.resolved_base_url || location.origin).replace(/\/$/, '');
    const sample = this.store.get('users')[0];
    const token = sample ? sample.uuid : '00000000-0000-0000-0000-000000000000';
    const rows = [
      ['اشتراک هوشمند', `${base}/sub/${token}?target=auto`],
      ['همه ترکیب‌ها', `${base}/sub/${token}?target=all`],
      ['سابلینک کلاینت Bettbox', `${base}/sub/${token}?target=bettbox`],
      ['سابلینک کلاینت NekoBoxPlus', `${base}/sub/${token}?target=nekoboxplus`],
      ['لینک نود مشخص', `${base}/sub/${token}?target=vless&node=railway-direct`],
      ['پنجره وضعیت کاربر', `${base}/portal/${token}`],
    ];
    const host = $('#subPreview');
    host.innerHTML = rows.map(([label, url]) => `<div class="link-box"><div class="lb-main"><b>${esc(label)}</b><code>${esc(url)}</code></div>
      <button class="copy-btn" data-copy="${esc(url)}">${ico('copy', 14)}</button></div>`).join('');
    bindCopyButtons(host, this.toasts);
  }

  bindEvents() {
    const pwNew = $('#pwNew');
    if (pwNew) {
      pwNew.oninput = (event) => {
        const score = this.passwordStrength(event.target.value);
        const meter = $('#pwMeter');
        meter.style.width = `${score}%`;
        meter.style.background = score > 75 ? 'linear-gradient(90deg,#34e0c0,#3ee6a0)'
          : score > 45 ? 'linear-gradient(90deg,#5ad1ff,#8b7bff)' : 'linear-gradient(90deg,#ffc85c,#ff6b81)';
      };
    }
    const password = $('#btnPassword');
    if (password) {
      password.onclick = () => this.app.safe(async () => {
        const current = $('#pwCurrent').value, next = $('#pwNew').value, confirm = $('#pwConfirm').value;
        if (next !== confirm) { this.toasts.err('تکرار رمز جدید مطابقت ندارد'); return; }
        if (next.length < 8) { this.toasts.err('رمز جدید حداقل ۸ کاراکتر باشد'); return; }
        await this.api.post('/api/settings/password', { current, new: next });
        this.toasts.ok('رمز مدیریت تغییر کرد');
        $('#pwCurrent').value = $('#pwNew').value = $('#pwConfirm').value = '';
        $('#pwMeter').style.width = '0%';
        await this.app.loadSettings();
      });
    }
    const rotate = $('#btnRotate');
    if (rotate) {
      rotate.onclick = () => this.app.safe(async () => {
        const confirmed = await this.modals.ask('ابطال نشست‌ها', 'همه نشست‌های فعال بسته می‌شود و باید دوباره وارد شوید.', { confirmLabel: 'ابطال کن' });
        if (!confirmed) return;
        await this.api.post('/api/settings/rotate-session', {});
        this.toasts.ok('نشست‌ها باطل شد');
        this.app.session.clear();
        setTimeout(() => location.reload(), 700);
      });
    }
    // Shadowsocks uses one key per cipher, so this is how a leaked SS link is
    // revoked: the old links stop authenticating the moment Xray reloads.
    const rotateSs = $('#btnRotateSs');
    if (rotateSs) {
      rotateSs.onclick = () => this.app.safe(async () => {
        const confirmed = await this.modals.ask('چرخش کلید شادوساکس',
          'کلید همه سیفرهای شادوساکس عوض می‌شود؛ لینک‌های شادوساکس قدیمی از کار می‌افتند و کاربران باید سابلینک را دوباره وارد کنند.',
          { confirmLabel: 'چرخش کن' });
        if (!confirmed) return;
        const original = rotateSs.textContent;
        rotateSs.disabled = true;
        rotateSs.innerHTML = '<span class="spin-inline"></span> چرخش…';
        try {
          const data = await this.api.post('/api/settings/rotate-shadowsocks', {});
          this.toasts.ok(`${(data.rotated || []).length} کلید شادوساکس چرخید`);
          await this.app.loadSettings();
        } finally {
          rotateSs.disabled = false;
          rotateSs.textContent = original;
        }
      });
    }
    const save = $('#btnSaveSettings');
    if (save) {
      save.onclick = () => this.app.safe(async () => {
        const original = save.textContent;
        save.disabled = true;
        save.innerHTML = '<span class="spin-inline"></span> ذخیره…';
        try {
          await this.api.post('/api/settings', {
            public_base_url: $('#setBaseUrl').value.trim(),
            sub_prefix: $('#setPrefix').value.trim(),
            default_protocol: $('#setProtocol').value,
            default_limit_gb: $('#setLimit').value.trim(),
            default_expiry_days: $('#setExpiry').value.trim(),
            default_ip_limit: $('#setIpLimit').value.trim(),
            session_days: $('#setSessionDays').value.trim(),
            ping_interval: $('#setPingInterval').value.trim(),
            app_name: $('#setAppName').value.trim(),
            accent: $('#setAccent').value.trim(),
            accent_secondary: $('#setAccent2').value.trim(),
          });
          this.toasts.ok('تنظیمات ذخیره شد — برای اعمال کامل، صفحه یک بار بازخوانی می‌شود', 4600);
          await this.app.loadSettings();
          this.app.loadMetrics().catch(() => {});
          setTimeout(() => location.reload(), 1200);
        } finally {
          save.disabled = false;
          save.textContent = original;
        }
      });
    }
    const preview = $('#btnPreviewSub');
    if (preview) preview.onclick = () => this.previewSubscriptions();
    const backup = $('#btnBackup');
    if (backup) {
      backup.onclick = () => this.app.safe(async () => {
        await this.api.download('/api/backup', `nexus-backup-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`);
        this.toasts.ok('فایل پشتیبان دانلود شد');
      });
    }
    const logsRefresh = $('#btnLogsRefresh');
    if (logsRefresh) {
      logsRefresh.onclick = () => this.app.safe(async () => {
        await this.app.loadLogs();
        this.render();
        this.toasts.ok('گزارش بروزرسانی شد', 1600);
      });
    }
    const clearLogs = $('#btnClearLogs');
    if (clearLogs) {
      clearLogs.onclick = () => this.app.safe(async () => {
        const confirmed = await this.modals.ask('پاک‌سازی گزارش', 'تمام رکوردهای رویداد حذف می‌شوند. این عمل برگشت‌پذیر نیست.', { confirmLabel: 'پاک کن' });
        if (!confirmed) return;
        await this.api.delete('/api/logs');
        this.toasts.ok('گزارش پاک شد');
        await this.app.loadLogs();
        this.render();
      });
    }
    const saveClients = $('#btnSaveClientLinks');
    if (saveClients) {
      saveClients.onclick = () => this.app.safe(async () => {
        const payload = {};
        $$('#clientLinkFields input[data-client]').forEach((input) => {
          if (input.value.trim()) payload[input.dataset.client] = input.value.trim();
        });
        const result = await this.api.post('/api/settings/clients', payload);
        this.renderClientLinks(result.clients);
        this.toasts.ok('لینک دانلود کلاینت‌ها ذخیره شد');
      });
    }
    const resetClients = $('#btnClientLinksReset');
    if (resetClients) {
      resetClients.onclick = () => this.app.safe(async () => {
        const result = await this.api.post('/api/settings/clients', { reset: true });
        this.renderClientLinks(result.clients);
        this.toasts.ok('لینک‌ها به پیش‌فرض برگشت');
      });
    }
    const install = $('#btnInstallPwa');
    if (install) install.onclick = () => this.app.pwa.promptInstall();
  }
}
