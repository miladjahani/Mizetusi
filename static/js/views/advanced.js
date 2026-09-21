/* =============================================================================
   NEXUS · view — advanced.

   The three things that are powerful but need explaining, grouped in one place:

   * the Hysteria2 node — a QUIC protocol Xray does not implement, so it is an
     *external* endpoint the operator owns and publishes only when they say so;
   * multi-location — a ready-made pack of country locations (the imports from the
     reference subscription), or locations built from any subscription URL;
   * the transport truth — what the running engine really serves, what it had to
     withhold, and the Shadowsocks key rotation.
   ========================================================================== */
import { $, $$, ico, esc, Fmt } from '../core.js';

export class AdvancedView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  async load() {
    const results = await Promise.allSettled([
      this.api.get('/api/hysteria'),
      this.api.get('/api/edge/packs'),
      this.api.get('/api/transports'),
      this.api.get('/api/edge'),
    ]);
    const [hy2, packs, transports, edge] = results.map((item) => (item.status === 'fulfilled' ? item.value : null));
    if (hy2) this.store.set('hysteria', hy2);
    if (packs) this.store.set('packs', packs);
    if (transports) this.store.set('transports', transports);
    if (edge) {
      this.store.set('edge', edge);
      const select = $('#adImportProvider');
      if (select) {
        const providers = (edge.providers || []).filter((item) => item.scannable !== false && item.id !== 'domain');
        const current = select.value;
        select.innerHTML = providers.map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('');
        if (current && providers.some((item) => item.id === current)) select.value = current;
      }
    }
    this.render();
  }

  render() {
    this.renderHy2();
    this.renderPacks();
    this.renderProfiles();
  }

  renderHy2() {
    const data = this.store.get('hysteria')?.hysteria || { configured: false };
    const set = (selector, value) => {
      const el = $(selector);
      if (el && document.activeElement !== el) el.value = value ?? '';
    };
    const info = this.store.get('hysteria') || {};
    const profile = info.profile || {};
    set('#adHy2Host', data.host || '');
    set('#adHy2Port', data.port || 443);
    set('#adHy2Sni', data.sni || '');
    set('#adHy2Label', data.label || profile.tag ? (data.label || '') : '');
    const enabled = $('#adHy2Enabled');
    if (enabled) enabled.classList.toggle('on', !!data.configured);
    const obfs = $('#adHy2Obfs');
    if (obfs) obfs.value = data.obfs ? 'salamander' : '';
    const tag = $('#adHy2Tag');
    if (tag) {
      tag.className = `pill ${data.configured ? 'ok' : 'warn'}`;
      tag.innerHTML = data.configured
        ? `<i class="dot"></i> ${esc(data.host)}:${Fmt.num(data.port)}`
        : 'غیرفعال';
    }
    const host = $('#adHy2Info');
    if (host) {
      host.innerHTML = data.configured ? `
        <div class="kv-line"><span>هاست</span><b dir="ltr">${esc(data.host)}:${Fmt.num(data.port)}</b></div>
        <div class="kv-line"><span>SNI</span><b dir="ltr">${esc(data.sni || '—')}</b></div>
        <div class="kv-line"><span>Obfs</span><b dir="ltr">${data.obfs ? 'salamander' : '—'}</b></div>
        <div class="kv-line"><span>در سابلینک</span><b>مانند یک نود جداگانه با پرچم لوکیشن</b></div>`
        : `<div class="kv-line"><span>وضعیت</span><b>تا هاست و رمز را ذخیره نکنید هیچ لینکی منتشر نمی‌شود</b></div>`;
    }
  }

  renderPacks() {
    const payload = this.store.get('packs') || {};
    const host = $('#adPacks');
    if (!host) return;
    const packs = payload.packs || [];
    const sources = payload.sources || [];
    host.innerHTML = packs.map((pack) => `
      <div class="setting-row${pack.installed ? ' ok' : ''}">
        <div class="txt">
          <b>${esc(pack.label)} ${pack.installed ? `· ${Fmt.num(pack.installed)}/${Fmt.num(pack.locations)} نصب‌شده` : ''}</b>
          <p>${esc(pack.note)}</p>
          <div class="chips" style="margin-top:6px">
            ${(pack.entries || []).map((entry) => `<span class="chip">${esc(entry.location.toUpperCase())}${entry.name ? ` ${esc(entry.name)}` : ''} · ${entry.host ? esc(entry.host) : `${Fmt.num((entry.ranges || []).length)} رنج کلودفلر`}${entry.port !== 443 ? `:${Fmt.num(entry.port)}` : ''}</span>`).join('')}
          </div>
          ${pack.host_required ? `<p class="muted" style="margin-top:4px">دامنهٔ Worker (یا دامنهٔ پنل پشت کلودفلر) را در فیلد بالای همین بخش بگذارید؛ لوکیشن‌های کلودفلر با همان Host/SNI منتشر می‌شوند.</p>` : ''}
        </div>
        <div class="actions" style="margin:0">
          <button class="secondary compact" data-pack-install="${esc(pack.id)}">${pack.installed ? 'به‌روزرسانی' : 'نصب'}</button>
          ${pack.installed ? `<button class="danger-btn compact" data-pack-remove="${esc(pack.id)}">حذف</button>` : ''}
        </div>
      </div>`).join('') || '<div class="empty">بسته‌ای موجود نیست.</div>';
    $$('[data-pack-install]', host).forEach((button) => {
      button.onclick = () => this.app.safe(() => this.installPack(button.dataset.packInstall));
    });
    $$('[data-pack-remove]', host).forEach((button) => {
      button.onclick = () => this.app.safe(() => this.removePack(button.dataset.packRemove));
    });
    const locations = sources.filter((item) => item.location).map((item) => item.location);
    this.store.set('locations', Array.from(new Set(locations)));
  }

  renderProfiles() {
    const data = this.store.get('transports');
    const host = $('#adProfiles');
    if (!host || !data) return;
    const profiles = data.profiles || [];
    const served = data.served;
    const withheld = data.withheld || [];
    host.innerHTML = `
      <div class="kv-line"><span>ترنسپورت‌های منتشرشده</span><b dir="ltr">${Fmt.num(profiles.length)}</b></div>
      <div class="kv-line"><span>پروتکل‌ها</span><b dir="ltr">${esc((data.protocols || []).join(' · '))}</b></div>
      <div class="kv-line"><span>روش‌های شادوساکس</span><b dir="ltr" style="white-space:normal">${esc((data.ss_methods || []).join(' · '))}</b></div>
      <div class="kv-line"><span>Reality</span><b dir="ltr">${data.direct ? `${esc(data.direct.host)}:${Fmt.num(data.direct.port)}` : 'در دسترس نیست'}</b></div>
      <div class="kv-line"><span>WARP</span><b>${data.warp ? 'فعال' : 'خاموش'}</b></div>
      <div class="kv-line"><span>Hysteria2</span><b>${data.hysteria2?.configured ? 'فعال' : 'خاموش'}</b></div>
      <div class="kv-line"><span>کنار گذاشته‌شده</span><b dir="ltr" style="white-space:normal">${withheld.length ? esc(withheld.join(' · ')) : (served === null ? 'نامعلوم' : 'هیچ')}</b></div>
      <div class="kv-line"><span>مسیرهای اجباری برنامه‌ریزی‌شده</span><b dir="ltr" style="white-space:normal">${esc((data.planned || []).map((item) => item.id).join(' · ') || '—')}</b></div>`;
  }

  async installPack(id) {
    const hosts = $('#adPackHosts')?.value.trim() || '';
    const payload = { id, action: 'install' };
    if (hosts) payload.hosts = hosts;
    const data = await this.api.post('/api/edge/packs', payload);
    // The created locations are pinged in the same request, so a pack whose
    // domains do not serve this deployment reads as broken right away instead of
    // as "installed" and silent.
    const ping = data.ping || {};
    if (ping.probed) {
      (ping.healthy ? this.toasts.ok : this.toasts.err)(
        `${Fmt.num(data.created?.length || 0)} لوکیشن نصب شد · ${Fmt.num(ping.healthy)} از ${Fmt.num(ping.probed)} آدرس پینگ داد`, 7000);
    } else {
      this.toasts.ok(`${Fmt.num(data.created?.length || 0)} لوکیشن نصب شد`);
    }
    await this.load();
    await this.app.reloadNodes();
  }

  async removePack(id) {
    const data = await this.api.post('/api/edge/packs', { id, action: 'uninstall' });
    this.toasts.info(`${Fmt.num(data.removed?.length || 0)} لوکیشن حذف شد`, 2600);
    await this.load();
    await this.app.reloadNodes();
  }

  async importSubscription(apply) {
    const payload = {
      url: $('#adImportUrl')?.value.trim() || '',
      provider: $('#adImportProvider')?.value || '',
      max_nodes: Number($('#adImportMax')?.value || 3),
      apply: !!apply,
    };
    if (!payload.url) { this.toasts.err('آدرس سابلینک را وارد کنید'); return; }
    const data = await this.api.post('/api/edge/import', payload);
    const out = $('#adImportOut');
    if (out) {
      out.innerHTML = `
        <div class="kv-list">
          <div class="kv-line"><span>پیدا شد</span><b dir="ltr">${Fmt.num(data.found)}</b></div>
          <div class="kv-line"><span>لوکیشن‌ها</span><b>${esc((data.locations || []).join(' · ') || '—')}</b></div>
        </div>
        <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>لوکیشن</th><th>هاست</th><th>پورت</th></tr></thead><tbody>
          ${(data.preview || []).slice(0, 40).map((item) => `<tr><td class="mono">${esc(item.location)}</td>
            <td class="mono">${esc(item.host)}</td><td class="mono">${Fmt.num(item.port)}</td></tr>`).join('')}
        </tbody></table></div>`;
    }
    if (apply) {
      const ping = data.ping || {};
      if (ping.probed) {
        (ping.healthy ? this.toasts.ok : this.toasts.err)(
          `${Fmt.num(data.created?.length || 0)} لوکیشن ساخته شد · ${Fmt.num(ping.healthy)} از ${Fmt.num(ping.probed)} آدرس پینگ داد`, 7000);
      } else {
        this.toasts.ok(`${Fmt.num(data.created?.length || 0)} لوکیشن ساخته شد`);
      }
      await this.load();
      await this.app.reloadNodes();
    }
  }

  async saveHysteria() {
    const payload = {
      host: $('#adHy2Host')?.value.trim() || '',
      port: Number($('#adHy2Port')?.value || 443),
      password: $('#adHy2Password')?.value.trim() || '',
      sni: $('#adHy2Sni')?.value.trim() || '',
      obfs: $('#adHy2Obfs')?.value || '',
      obfs_password: $('#adHy2ObfsPassword')?.value.trim() || '',
      insecure: $('#adHy2Insecure')?.value || '0',
      label: $('#adHy2Label')?.value.trim() || '',
      enabled: $('#adHy2Enabled')?.classList.contains('on') ? '1' : '0',
      action: 'save',
    };
    const data = await this.api.post('/api/hysteria', payload);
    this.store.set('hysteria', data);
    const password = $('#adHy2Password');
    if (password) password.value = '';
    this.renderHy2();
    this.toasts.ok('نود Hysteria2 ذخیره شد');
    await this.app.loadSettings();
  }

  bindEvents() {
    const toggle = $('#adHy2Enabled');
    if (toggle) toggle.onclick = () => toggle.classList.toggle('on');
    const save = $('#adHy2Save');
    if (save) save.onclick = () => this.app.safe(() => this.saveHysteria());
    const disable = $('#adHy2Disable');
    if (disable) disable.onclick = () => this.app.safe(async () => {
      const data = await this.api.post('/api/hysteria', { action: 'disable' });
      this.store.set('hysteria', data);
      this.renderHy2();
      this.toasts.info('نود Hysteria2 غیرفعال شد');
    });
    const clear = $('#adHy2Clear');
    if (clear) clear.onclick = () => this.app.safe(async () => {
      const data = await this.api.post('/api/hysteria', { action: 'clear' });
      this.store.set('hysteria', data);
      this.renderHy2();
      this.toasts.info('نود Hysteria2 حذف شد');
    });
    const preview = $('#adImportPreview');
    if (preview) preview.onclick = () => this.app.safe(() => this.importSubscription(false));
    const apply = $('#adImportApply');
    if (apply) apply.onclick = () => this.app.safe(() => this.importSubscription(true));
    const reload = $('#adProfilesReload');
    if (reload) reload.onclick = () => this.app.safe(async () => { await this.load(); this.toasts.info('بروزرسانی شد', 1600); });
    const rotate = $('#adRotateSs');
    if (rotate) rotate.onclick = () => this.app.safe(async () => {
      const data = await this.api.post('/api/settings/rotate-shadowsocks', {});
      this.toasts.ok(`کلید ${Fmt.num((data.rotated || []).length)} روش شادوساکس چرخید`);
      await this.load();
    });
    const sync = $('#adSyncNodes');
    if (sync) sync.onclick = () => this.app.safe(async () => {
      await this.api.post('/api/nodes/sync', {});
      await this.api.post('/api/nodes/ping', { timeout: 3 });
      this.toasts.ok('نودها همگام و پینگ شدند');
      await this.app.reloadNodes();
      await this.load();
    });
  }
}
