/* =============================================================================
   NEXUS · view — network tools.

   A scanner console plus the four checks an operator actually runs from a
   phone: can I reach this host:port, what does DNS say (through DoH, so a
   poisoned local resolver is bypassed), what does this CIDR contain, and what is
   inside this subscription? "ساخت لوکیشن از این سابلینک" hands the last answer
   straight back to the edge as new locations.
   ========================================================================== */
import { $, $$, ico, esc, Fmt, bindCopyButtons } from '../core.js';

export class ToolsView {
  constructor(app) {
    this.app = app;
    this.selected = new Set();
    this.search = '';
    this.scanning = false;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  async load() {
    try {
      const edge = await this.api.get('/api/edge');
      this.store.set('edge', edge);
    } catch (error) {
      if (!error.unauthorized) throw error;
    }
    this.render();
  }

  providers() {
    const list = this.store.get('edge')?.providers || [];
    return list.filter((item) => item.id !== 'domain');
  }

  visibleProviders() {
    const query = this.search.trim().toLowerCase();
    return this.providers().filter((item) => !query
      || String(item.label).toLowerCase().includes(query)
      || String(item.id).toLowerCase().includes(query));
  }

  render() {
    const providers = this.visibleProviders();
    const host = $('#tlProviders');
    const count = $('#tlProviderCount');
    if (count) count.textContent = `${Fmt.num(providers.length)} provider · ${Fmt.num(this.selected.size)} انتخاب‌شده`;
    if (host) {
      host.innerHTML = providers.length ? providers.map((item) => `
        <button class="fchip${this.selected.has(item.id) ? ' on' : ''}" data-provider="${esc(item.id)}" title="${esc(item.note || '')}">
          ${esc(item.label)} <small>${Fmt.num(item.total)}${item.healthy ? ` · ${Fmt.num(item.healthy)} سالم` : ''}${item.seeded ? ' · seed' : ''}</small>
        </button>`).join('')
        : '<div class="empty">providerی با این نام پیدا نشد.</div>';
      $$('#tlProviders .fchip').forEach((chip) => {
        chip.onclick = () => {
          const id = chip.dataset.provider;
          if (this.selected.has(id)) this.selected.delete(id); else this.selected.add(id);
          this.render();
        };
      });
    }
    const stats = $('#tlProviderStats');
    if (stats) {
      const known = this.providers();
      const pool = known.reduce((total, item) => total + Number(item.total || 0), 0);
      const healthy = known.reduce((total, item) => total + Number(item.healthy || 0), 0);
      const seeded = known.filter((item) => item.seeded).length;
      stats.innerHTML = `
        <div class="stat glass"><div class="top"><span class="lbl">providerها</span><span class="ico">${ico('globe', 15)}</span></div>
          <div class="val">${Fmt.num(known.length)}</div><div class="foot">${Fmt.num(seeded)} با seed آماده</div></div>
        <div class="stat glass a-violet"><div class="top"><span class="lbl">آی‌پی‌های شناخته‌شده</span><span class="ico">${ico('server', 15)}</span></div>
          <div class="val">${Fmt.num(pool)}</div><div class="foot">در استخر لبه</div></div>
        <div class="stat glass a-ok"><div class="top"><span class="lbl">سالم</span><span class="ico">${ico('check', 15)}</span></div>
          <div class="val">${Fmt.num(healthy)}</div><div class="foot">پینگ موفق</div></div>`;
    }
  }

  /* -------------------------------------------------------------------- scan */
  async scan() {
    if (this.scanning) return;
    const ids = Array.from(this.selected);
    const out = $('#tlScanResult');
    this.scanning = true;
    const results = [];
    const note = (html) => { if (out) out.innerHTML = `<div class="empty">${html}</div>`; };
    try {
      if (!ids.length) {
        note(`${ico('sync', 16)} در حال اسکن همهٔ providerها… (این کار چند ثانیه طول می‌کشد)`);
        const payload = await this.api.post('/api/edge/scan', { limit: 96 });
        results.push(...(payload.results || []));
      } else {
        for (let index = 0; index < ids.length; index += 1) {
          note(`${ico('sync', 16)} اسکن ${index + 1}/${ids.length} — ${esc(ids[index])}`);
          try {
            const payload = await this.api.post('/api/edge/scan', { provider: ids[index], limit: 48 });
            results.push(...(payload.results || []));
          } catch (error) {
            results.push({ provider: ids[index], ok: false, found: 0, error: error.message });
          }
        }
      }
      const found = results.reduce((total, item) => total + Number(item.found || 0), 0);
      if (out) {
        out.innerHTML = `<div class="table-wrap"><table><thead><tr><th>provider</th><th>پیدا شد</th><th>وضعیت</th><th>خطا</th></tr></thead><tbody>
          ${results.map((item) => `<tr>
            <td class="mono">${esc(item.provider)}</td>
            <td class="mono">${Fmt.num(item.found || 0)}</td>
            <td><span class="pill ${item.ok ? 'ok' : 'bad'}">${item.ok ? 'موفق' : 'ناموفق'}</span></td>
            <td class="muted">${esc(item.error || '—')}</td></tr>`).join('')}
          </tbody></table></div>
          <p class="muted">در مجموع ${Fmt.num(found)} آدرس تازه به استخر اضافه شد؛ آدرس‌های سالم پس از پینگ به کاتالوگ نودها منتشر می‌شوند.</p>`;
      }
      this.toasts.ok(`${Fmt.num(found)} آدرس تازه اضافه شد`);
      await this.app.reloadNodes();
      await this.load();
    } finally {
      this.scanning = false;
    }
  }

  /* ------------------------------------------------------------------- tools */
  async check() {
    const host = $('#tlHost')?.value.trim() || '';
    if (!host) { this.toasts.err('هاست را وارد کنید'); return; }
    const payload = {
      host,
      port: Number($('#tlPort')?.value || 443),
      sni: $('#tlSni')?.value.trim() || '',
      tls: ($('#tlTls')?.value || '1') === '1',
    };
    const out = $('#tlCheckOut');
    if (out) out.innerHTML = `<div class="kv-line"><span>وضعیت</span><b>در حال بررسی…</b></div>`;
    const data = await this.api.post('/api/tools/check', payload);
    // A completed handshake with an unverified certificate is still a reachable
    // edge (which is what a client needs), so it is reported as such instead of
    // as a failure the admin cannot act on.
    const row = (label, item) => item ? `<div class="kv-line"><span>${label}</span>
      <b dir="ltr" style="color:${item.ok ? '#79efbb' : '#ff9aa8'}">${item.ok ? `${Fmt.lat().format(item.latency_ms)} ms${item.verified === false ? ' · گواهی تأیید نشد' : ''}` : `ناموفق · ${esc(item.error || '')}`}</b></div>` : '';
    if (out) {
      out.innerHTML = `
        <div class="kv-line"><span>هدف</span><b dir="ltr">${esc(data.host)}:${Fmt.num(data.port)}</b></div>
        ${row('TCP', data.results?.tcp)}
        ${row('TLS', data.results?.tls)}
        <div class="kv-line"><span>زمان کل</span><b dir="ltr">${Fmt.lat().format(data.elapsed_ms)} ms</b></div>`;
    }
    const tag = $('#tlCheckTag');
    const reachable = Object.values(data.results || {}).some((item) => item.ok);
    if (tag) {
      tag.className = `pill ${reachable ? 'ok' : 'bad'}`;
      tag.innerHTML = reachable ? '<i class="dot"></i> پاسخ می‌دهد' : 'بی‌پاسخ';
    }
  }

  async dnsLookup() {
    const name = $('#tlDnsName')?.value.trim() || '';
    if (!name) { this.toasts.err('دامنه را وارد کنید'); return; }
    const out = $('#tlDnsOut');
    if (out) out.innerHTML = `<div class="kv-line"><span>وضعیت</span><b>در حال جست‌وجو…</b></div>`;
    const data = await this.api.get(`/api/tools/dns?name=${encodeURIComponent(name)}`);
    const answer = data.answer || {};
    const records = answer.Answer || answer.answer || [];
    const body = Array.isArray(records) && records.length
      ? records.map((item) => `<div class="kv-line"><span>${esc(item.name || name)}</span><b dir="ltr">${esc(item.data || item.address || '—')}</b></div>`).join('')
      : `<div class="kv-line"><span>پاسخ</span><b dir="ltr">${esc(JSON.stringify(answer).slice(0, 220))}</b></div>`;
    if (out) out.innerHTML = body;
  }

  async cidr() {
    const value = $('#tlCidr')?.value.trim() || '';
    if (!value) { this.toasts.err('مقدار CIDR را وارد کنید'); return; }
    const data = await this.api.post('/api/tools/cidr', { value, limit: 12 });
    const out = $('#tlCidrOut');
    if (out) {
      out.innerHTML = `
        <div class="kv-line"><span>شبکه‌های معتبر</span><b dir="ltr">${Fmt.num(data.count)}</b></div>
        <div class="kv-line"><span>مجموع آدرس‌ها</span><b dir="ltr">${Fmt.num(data.total_addresses)}</b></div>
        ${data.invalid?.length ? `<div class="kv-line"><span>نامعتبر</span><b dir="ltr">${esc(data.invalid.join(', '))}</b></div>` : ''}
        <div class="kv-line"><span>نمونهٔ اسکن</span><b dir="ltr" style="white-space:normal">${esc((data.addresses || []).join(' · '))}</b></div>`;
    }
  }

  subPayload() {
    return {
      url: $('#tlSubUrl')?.value.trim() || '',
      text: $('#tlSubText')?.value.trim() || '',
      max_nodes: Number($('#tlImportMax')?.value || 3),
    };
  }

  async parse() {
    const payload = this.subPayload();
    if (!payload.url && !payload.text) { this.toasts.err('آدرس سابلینک یا متن کانفیگ‌ها را وارد کنید'); return; }
    const out = $('#tlParseOut');
    if (out) out.innerHTML = `<div class="empty">${ico('sync', 16)} در حال تحلیل…</div>`;
    const data = await this.api.post('/api/tools/parse', payload);
    if (!out) return;
    out.innerHTML = `
      <div class="kv-list">
        <div class="kv-line"><span>تعداد کانفیگ</span><b dir="ltr">${Fmt.num(data.count)}</b></div>
        <div class="kv-line"><span>پروتکل‌ها</span><b dir="ltr">${esc((data.protocols || []).join(', ') || '—')}</b></div>
        <div class="kv-line"><span>لوکیشن‌ها</span><b>${esc((data.locations || []).join(' · ') || '—')}</b></div>
      </div>
      <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>پرچم</th><th>هاست</th><th>پورت</th><th>پروتکل</th><th>نام</th></tr></thead><tbody>
        ${(data.entries || []).slice(0, 60).map((item) => `<tr>
          <td>${esc(item.flag || '—')}</td>
          <td class="mono">${esc(item.host)}</td>
          <td class="mono">${Fmt.num(item.port)}</td>
          <td class="mono">${esc(item.scheme)}</td>
          <td class="muted">${esc(Fmt.truncate(item.label, 40))}</td></tr>`).join('')}
      </tbody></table></div>`;
  }

  async importLocations() {
    const payload = { ...this.subPayload(), apply: true };
    if (!payload.url && !payload.text) { this.toasts.err('آدرس سابلینک یا متن کانفیگ‌ها را وارد کنید'); return; }
    const data = await this.api.post('/api/edge/import', payload);
    const out = $('#tlParseOut');
    if (out) {
      out.innerHTML = `<div class="link-box"><div class="lb-main"><b>${Fmt.num(data.created?.length || 0)} لوکیشن ساخته شد</b>
        <code>${esc((data.locations || []).join(' · '))}</code></div></div>`;
    }
    this.toasts.ok(`${Fmt.num(data.created?.length || 0)} لوکیشن اضافه شد`);
    await this.app.reloadNodes();
    await this.load();
  }

  bindEvents() {
    bindCopyButtons(document, this.toasts);
    const search = $('#tlProviderSearch');
    if (search) search.oninput = () => { this.search = search.value; this.render(); };
    const all = $('#tlSelectAll');
    if (all) all.onclick = () => { this.providers().forEach((item) => this.selected.add(item.id)); this.render(); };
    const none = $('#tlSelectNone');
    if (none) none.onclick = () => { this.selected.clear(); this.render(); };
    const scan = $('#tlScan');
    if (scan) scan.onclick = () => this.app.safe(() => this.scan());
    const check = $('#tlCheck');
    if (check) check.onclick = () => this.app.safe(() => this.check());
    const dns = $('#tlDns');
    if (dns) dns.onclick = () => this.app.safe(() => this.dnsLookup());
    const cidr = $('#tlCidrRun');
    if (cidr) cidr.onclick = () => this.app.safe(() => this.cidr());
    const parse = $('#tlParseRun');
    if (parse) parse.onclick = () => this.app.safe(() => this.parse());
    const importBtn = $('#tlParseImport');
    if (importBtn) importBtn.onclick = () => this.app.safe(() => this.importLocations());
  }
}
