/* =============================================================================
   NEXUS · view — customization.

   Everything an *end user* notices: the banner of the status window, the support
   link, whether node names carry their country flag, which subscription shape is
   highlighted, and the branding (name + accent colours). The default config count
   lives here too, because it is the "how much does a quick-created user get"
   answer the operator tunes once.
   ========================================================================== */
import { $, $$, ico, esc, Fmt } from '../core.js';

export class CustomizeView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  get toasts() { return this.app.toasts; }

  async load() {
    try {
      this.store.set('customization', await this.api.get('/api/customization'));
    } catch (error) {
      this.store.set('customization', null);
      if (!error.unauthorized) throw error;
    }
    this.render();
  }

  render() {
    const data = this.store.get('customization');
    if (!data) return;
    const set = (selector, value) => {
      const el = $(selector);
      if (el && document.activeElement !== el) el.value = value ?? '';
    };
    set('#czBanner', data.portal_banner);
    set('#czSupport', data.support_url);
    set('#czAppName', data.app_name);
    set('#czAccent', data.accent);
    set('#czAccent2', data.accent_secondary);
    const format = $('#czFormat');
    if (format) format.value = data.default_format || 'auto';
    const cap = data.default_max_configs || '';
    set('#czMaxConfigs', cap);
    const state = $('#czMaxState');
    if (state) state.value = cap ? `${cap} کانفیگ` : 'بدون سقف';

    const flags = $('#czFlags');
    if (flags) flags.classList.toggle('on', data.flags !== false);

    const tag = $('#czTag');
    if (tag) {
      tag.className = 'pill ok';
      tag.innerHTML = `${ico('edit', 12)} ${data.flags === false ? 'بدون پرچم' : 'پرچم فعال'}`;
    }

    const preview = $('#czPreview');
    if (preview) {
      // The client sublinks are published in engine families; the chosen format
      // is the family the status window marks as recommended.
      const core = `
        <div class="panel-head" style="margin-top:14px"><div><h3>خانواده‌های کلاینت</h3><p class="sub">سابلینک هر کلاینت در همین گروه منتشر می‌شود</p></div></div>
        ${(data.core_formats || []).map((item) => `
        <div class="setting-row${item.id === data.default_format ? ' ok' : ''}">
          <div class="txt"><b>${esc(item.label)} ${item.id === data.default_format ? '· پیشنهادی' : ''}</b><p>${esc(item.hint || '')}</p></div>
          <span class="pill info">${esc(item.id)}</span>
        </div>`).join('')}`;
      preview.innerHTML = `
        ${data.portal_banner ? `<div class="link-box"><div class="lb-main"><b>بنر</b><code>${esc(data.portal_banner)}</code></div></div>` : ''}
        <div class="kv-list" style="margin-top:10px">
          <div class="kv-line"><span>نام برنامه</span><b>${esc(data.app_name)}</b></div>
          <div class="kv-line"><span>لینک پشتیبانی</span><b dir="ltr">${esc(data.support_url || '—')}</b></div>
          <div class="kv-line"><span>پرچم کشور روی نودها</span><b>${data.flags === false ? 'خاموش' : 'روشن'}</b></div>
          <div class="kv-line"><span>تعداد کانفیگ پیش‌فرض</span><b>${cap ? Fmt.num(cap) : 'بدون سقف'}</b></div>
        </div>
        ${core}`;
    }
  }

  async save() {
    const flags = $('#czFlags')?.classList.contains('on');
    const payload = {
      portal_banner: $('#czBanner')?.value.trim() || '',
      support_url: $('#czSupport')?.value.trim() || '',
      app_name: $('#czAppName')?.value.trim() || '',
      accent: $('#czAccent')?.value.trim() || '',
      accent_secondary: $('#czAccent2')?.value.trim() || '',
      default_format: $('#czFormat')?.value || 'auto',
      flags_enabled: flags ? '1' : '0',
      default_max_configs: $('#czMaxConfigs')?.value.trim() || '',
    };
    const result = await this.api.post('/api/customization', payload);
    this.store.set('customization', result.customization);
    this.render();
    // The brand lives in the settings payload too, so refresh the shell theme
    // and the PWA title from one place.
    await this.app.loadSettings();
    this.toasts.ok('شخصی‌سازی ذخیره شد');
  }

  bindEvents() {
    const switchEl = $('#czFlags');
    if (switchEl) switchEl.onclick = () => switchEl.classList.toggle('on');
    const save = $('#czSave');
    if (save) save.onclick = () => this.app.safe(() => this.save());
    const reload = $('#czReload');
    if (reload) reload.onclick = () => this.app.safe(async () => {
      await this.load();
      this.toasts.info('بازخوانی شد', 1600);
    });
    const cap = $('#czMaxConfigs');
    if (cap) cap.oninput = () => {
      const state = $('#czMaxState');
      if (state) state.value = cap.value.trim() ? `${cap.value.trim()} کانفیگ` : 'بدون سقف';
    };
  }
}
