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
      this.api.get('/api/cores'),
      this.api.get('/api/system/autoconfig'),
    ]);
    const [hy2, packs, transports, edge, cores, autoconfig] = results.map((item) => (item.status === 'fulfilled' ? item.value : null));
    if (hy2) this.store.set('hysteria', hy2);
    if (packs) this.store.set('packs', packs);
    if (transports) this.store.set('transports', transports);
    if (cores) this.store.set('cores', cores);
    if (autoconfig) this.store.set('autoconfig', autoconfig);
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
    this.renderAuto();
    this.renderHy2();
    this.renderPacks();
    this.renderProfiles();
    this.renderCores();
  }

  /* --------------------------------------- automatic configuration (boot) */
  /* What a deploy with no admin in the loop left behind: the capability the boot
     pass switches on by itself (Telegram Desktop's WEB proxy, whose carrier is
     this deployment's own 443) and, on Railway, the TCP proxies it creates for
     the raw-port capabilities — Railway allocates those public ports at random
     and only an API call can create them, which is why the mapping, not the
     environment, is what a link is built from (app/ports.py). Everything that
     stayed off names the one thing it would need first, and either half can be
     re-run from here. */
  renderAuto() {
    const data = this.store.get('autoconfig');
    const tag = $('#adAutoTag');
    if (!data) {
      if (tag) { tag.className = 'pill'; tag.textContent = '—'; }
      return;
    }
    const railway = data.railway || {};
    const api = railway.api || {};
    const catalog = (railway.proxies || {}).catalog || [];
    const forwarded = catalog.filter((item) => item.proxied);
    if (tag) {
      tag.className = `pill ${data.done ? 'ok' : 'warn'}`;
      tag.innerHTML = data.done
        ? `<i class="dot"></i> اجرا شده${(data.applied || []).length ? ` · ${esc((data.applied || []).join(' + '))}` : ''}`
        : 'اجرا نشده';
    }
    const info = $('#adAutoInfo');
    if (info) {
      info.innerHTML = `
        <div class="kv-line"><span>پاس خودکار</span><b>${data.allowed ? 'روشن' : 'خاموش (فقط دستی)'}</b></div>
        <div class="kv-line"><span>آخرین اجرا</span><b>${data.ran_at ? esc(Fmt.ago(data.ran_at)) : 'هنوز اجرا نشده'}</b></div>
        <div class="kv-line"><span>خودکار روشن شده</span><b dir="ltr" style="white-space:normal">${esc((data.applied || []).join(' · ') || '—')}</b></div>
        <div class="kv-line"><span>توکن API رِیلوی</span><b style="white-space:normal">${api.configured
          ? 'ست شده' : `ست نشده — ${esc((api.missing || []).join(' · '))}`}</b></div>
        <div class="kv-line"><span>پورت‌های عمومی فوروارد‌شده</span><b dir="ltr">${Fmt.num(forwarded.length)}</b></div>`;
    }
    const candidates = $('#adAutoCandidates');
    if (candidates) {
      candidates.innerHTML = catalog.length
        ? `<div class="table-wrap"><table><thead><tr><th>قابلیت</th><th>پورت داخلی</th><th>پورت عمومی</th><th>وضعیت</th></tr></thead><tbody>
            ${catalog.map((item) => `<tr>
              <td>${esc(item.label)}</td>
              <td class="mono" dir="ltr">${Fmt.num(item.port)}</td>
              <td class="mono" dir="ltr">${item.proxied ? `${esc(item.public_host)}:${Fmt.num(item.public_port)}` : '—'}</td>
              <td>${item.proxied ? '<span class="pill ok"><i class="dot"></i> فوروارد شده</span>'
                : (item.enabled ? '<span class="pill warn">بدون فوروارد</span>' : '<span class="pill">خاموش</span>')}</td>
            </tr>`).join('')}
          </tbody></table></div>
          <p class="muted">هر پورت خام TCP (Reality، AnyTLS، MTProto، وب‌پروکسی) روی Railway باید TCP Proxy داشته باشد و پورت عمومی‌اش تصادفی است؛ این جدول همان نگاشت را نشان می‌دهد و لینک‌های پنل از ستون «پورت عمومی» ساخته می‌شوند — نه از پورتی که داخل کانتینر باز می‌شود.</p>`
        : '<div class="empty">قابلیتی برای فوروارد پیدا نشد.</div>';
    }
    const ports = $('#adAutoPortsInfo');
    if (ports) {
      ports.innerHTML = data.done
        ? `<div class="kv-line"><span>پورت‌های ساخته‌شده در پاس</span><b dir="ltr" style="white-space:normal">${esc((railway.created || []).join(' · ') || '—')}</b></div>`
        : '<div class="kv-line"><span>پورت‌های ساخته‌شده در پاس</span><b>هنوز اجرا نشده</b></div>';
    }
  }

  async runAuto(action) {
    const data = await this.api.post('/api/system/autoconfig', { action });
    this.store.set('autoconfig', data);
    this.renderAuto();
    const railway = (data.outcome || {}).railway || {};
    const created = railway.created || [];
    if (railway.reason) this.toasts.err(railway.reason, 9000);
    else if (created.length) this.toasts.ok(`${Fmt.num(created.length)} پروکسی TCP ساخته شد — پورت‌های عمومی به‌روز شد`, 8000);
    else if ((data.outcome || {}).switches?.length) this.toasts.ok(`${Fmt.num(data.outcome.switches.length)} قابلیت روشن شد`);
    else this.toasts.info('چیز جدیدی برای انجام نبود', 4000);
    await this.load();
    await this.app.reloadNodes();
  }

  /* --------------------------------------------------- second engines */
  renderCores() {
    const data = this.store.get('cores');
    if (!data) return;
    const sni = $('#coSni');
    if (sni && document.activeElement !== sni) sni.value = data.sni || '';
    const catalog = data.catalog || [];
    const published = catalog.filter((item) => item.published);
    const running = (data.engines || []).filter((item) => item.running);
    const tag = $('#coTag');
    if (tag) {
      tag.className = `pill ${published.length ? 'ok' : 'warn'}`;
      tag.innerHTML = published.length
        ? `<i class="dot"></i> ${Fmt.num(published.length)} پروتکل منتشرشده`
        : 'هیچ پروتکلی منتشر نشده';
    }
    const stats = $('#coStats');
    if (stats) {
      stats.innerHTML = `
        <div class="stat glass a-ok"><div class="top"><span class="lbl">پروتکل منتشرشده</span><span class="ico">${ico('zap', 15)}</span></div>
          <div class="val">${Fmt.num(published.length)}</div><div class="foot">از ${Fmt.num(catalog.length)} پروتکل</div></div>
        <div class="stat glass"><div class="top"><span class="lbl">موتور فعال</span><span class="ico">${ico('server', 15)}</span></div>
          <div class="val">${Fmt.num(running.length)}</div><div class="foot">${esc(running.map((item) => item.label).join(' · ') || 'هیچ موتوری روشن نیست')}</div></div>
        <div class="stat glass a-violet"><div class="top"><span class="lbl">آدرس انتشار</span><span class="ico">${ico('globe', 15)}</span></div>
          <div class="val" dir="ltr" style="font-size:15px">${esc(data.host || '—')}</div><div class="foot">${data.udp ? 'TCP و UDP در دسترس' : 'بدون UDP'}</div></div>`;
    }
    const host = $('#coProfiles');
    if (host) {
      host.innerHTML = catalog.map((item) => `
        <div class="setting-row${item.published ? ' ok' : ''}">
          <div class="txt">
            <b>${esc(item.tag)}${item.published ? ' · منتشرشده' : (item.enabled ? ' · منتشر نشده' : '')}</b>
            <p>${esc(item.note)}</p>
            ${item.enabled && !item.reachable ? `<p class="muted" style="margin-top:4px">${esc(item.reason)}</p>` : ''}
          </div>
          <div class="actions" style="margin:0;gap:6px;flex-wrap:wrap">
            <input id="coPort-${esc(item.id)}" type="number" min="1" max="65535" value="${Fmt.num(item.port)}" dir="ltr" style="width:98px" title="پورت عمومی">
            <select id="coEngine-${esc(item.id)}" style="width:auto" title="موتوری که این پروتکل را سرو می‌کند">
              <option value="singbox"${item.engine === 'singbox' ? ' selected' : ''}>sing-box</option>
              <option value="mihomo"${item.engine === 'mihomo' ? ' selected' : ''}>mihomo</option>
            </select>
            <div class="switch${item.enabled ? ' on' : ''}" id="coToggle-${esc(item.id)}" data-co-toggle="${esc(item.id)}"></div>
          </div>
        </div>`).join('') || '<div class="empty">پروتکلی تعریف نشده است.</div>';
      $$('[data-co-toggle]', host).forEach((element) => {
        element.onclick = () => element.classList.toggle('on');
      });
    }
    const engines = $('#coEngines');
    if (engines) {
      engines.innerHTML = (data.engines || []).map((item) => `
        <div class="kv-line"><span>${esc(item.label)} <span class="hint">${esc(item.binary)}</span></span>
          <b dir="ltr" style="white-space:normal">${item.installed
            ? (item.running ? `Running${item.pid ? ` · PID ${Fmt.num(item.pid)}` : ''}` : 'خاموش')
            : 'نصب نیست'}${(item.profiles || []).length ? ` · ${esc((item.profiles || []).join(' + '))}` : ''}${
            item.error ? ` · ${esc(Fmt.truncate(item.error, 90))}` : ''}</b></div>`).join('');
    }
    const notes = $('#coNotes');
    if (notes) {
      notes.innerHTML = [
        ...(data.notes || []).map((note) => `<div class="kv-line"><span>نکته</span><b style="white-space:normal">${esc(note)}</b></div>`),
        `<div class="kv-line"><span>SNI گواهی</span><b dir="ltr">${esc(data.sni || '—')}</b></div>`,
        `<div class="kv-line"><span>اعتبار کاربران</span><b>هر کاربر یک UUID جدا دارد؛ غیرفعال کردن کاربر این نودها را هم قطع می‌کند</b></div>`,
      ].join('');
    }
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

  async saveCores() {
    const catalog = this.store.get('cores')?.catalog || [];
    const profiles = {};
    catalog.forEach((item) => {
      profiles[item.id] = {
        enabled: $(`#coToggle-${item.id}`)?.classList.contains('on') ? '1' : '0',
        port: Number($(`#coPort-${item.id}`)?.value || item.port),
        engine: $(`#coEngine-${item.id}`)?.value || item.engine,
      };
    });
    const data = await this.api.post('/api/cores', { profiles, sni: $('#coSni')?.value.trim() || '' });
    this.store.set('cores', data);
    this.renderCores();
    // A protocol that cannot be reached here is not a silent success: the engine
    // reports why, and that reason is what the admin needs to act on.
    const failed = Object.entries(data.sync || {}).filter(([, item]) => item.reason);
    if (failed.length) this.toasts.err(`${failed[0][0]}: ${failed[0][1].reason}`, 9000);
    else this.toasts.ok('تنظیمات هسته‌های دوم ذخیره شد');
    await this.app.reloadNodes();
  }

  async reloadCores() {
    const data = await this.api.post('/api/cores/reload', {});
    this.store.set('cores', data);
    this.renderCores();
    const up = Object.values(data.sync || {}).filter((item) => item.running);
    this.toasts.ok(`${Fmt.num(up.length)} موتور فعال است`);
    await this.app.reloadNodes();
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
    const autoRun = $('#adAutoRun');
    if (autoRun) autoRun.onclick = () => this.app.safe(() => this.runAuto('all'));
    const autoPorts = $('#adAutoPorts');
    if (autoPorts) autoPorts.onclick = () => this.app.safe(() => this.runAuto('railway'));
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
    const coresSave = $('#coSave');
    if (coresSave) coresSave.onclick = () => this.app.safe(() => this.saveCores());
    const coresReload = $('#coReload');
    if (coresReload) coresReload.onclick = () => this.app.safe(() => this.reloadCores());
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
