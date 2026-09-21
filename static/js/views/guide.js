/* =============================================================================
   NEXUS · view — live guide.

   A panel with this many knobs needs to answer one question at a time: «حالا چه
   کار کنم؟». The guide is that answer, and it is never a hand-written checklist:
   the server reports every step with its real state (is a Worker saved? how many
   locations, how many nodes answered a ping?), so a step that is already done is
   ticked off and the topbar chip always points at the first thing still missing.

   The drawer also carries the current tab's own tips, which is what turns a
   dense screen into something a first-time admin can move through.
   ========================================================================== */
import { $, $$, esc, ico, Fmt, copyText } from '../core.js';
import { Charts } from '../ui.js';

export class GuideView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  get api() { return this.app.api; }

  /* -------------------------------------------------------------------- data */
  async load(section = '') {
    try {
      const guide = await this.api.get(`/api/guide?section=${encodeURIComponent(section || this.store.get('section'))}`);
      this.store.set('guide', guide);
      this.renderChip();
      if (this.store.get('guideOpen')) this.render();
    } catch (error) {
      if (!error?.unauthorized) console.warn('[nexus] guide unavailable', error);
    }
  }

  /* ------------------------------------------------------------------- chips */
  renderChip() {
    const guide = this.store.get('guide');
    const pill = $('#guidePill');
    if (!pill) return;
    const done = (guide?.steps || []).filter((step) => step.done).length;
    const total = (guide?.steps || []).length || 0;
    const next = (guide?.steps || []).find((step) => !step.done);
    pill.classList.toggle('ok', !!guide && !next);
    pill.title = next ? `قدم بعدی: ${next.title}` : 'همه قدم‌ها انجام شده است';
    pill.innerHTML = `<span class="g-badge">${ico('shield', 13)}</span> راهنما
      <b>${total ? `${Fmt.num(done)}/${Fmt.num(total)}` : '—'}</b>`;
  }

  /* ------------------------------------------------------------------ drawer */
  toggle(force) {
    const next = force === undefined ? !this.store.get('guideOpen') : !!force;
    this.store.set('guideOpen', next);
    const drawer = $('#guideDrawer');
    const scrim = $('#guideScrim');
    const pill = $('#guidePill');
    if (drawer) drawer.classList.toggle('open', next);
    if (scrim) scrim.classList.toggle('open', next);
    if (pill) pill.classList.toggle('open', next);
    if (next) this.render();
  }

  render() {
    const host = $('#guideBody');
    if (!host) return;
    const guide = this.store.get('guide');
    if (!guide) {
      host.innerHTML = '<div class="skel" style="height:120px"></div><div class="skel" style="height:120px;margin-top:10px"></div>';
      return;
    }
    const done = guide.steps.filter((step) => step.done).length;
    const next = guide.steps.find((step) => !step.done);
    const score = Fmt.clamp(Number(guide.score) || 0, 0, 100);
    host.innerHTML = `
      <div class="guide-score">
        <div class="g-ring">${Charts.ring(score, '#c9f24c')}<span class="g-pct">${Fmt.num(score)}%</span></div>
        <div>
          <b>${Fmt.num(done)} از ${Fmt.num(guide.steps.length)} قدم انجام شده</b>
          <span>${next ? `قدم بعدی: <b style="color:#d9f76e">${esc(next.title)}</b>` : 'همه‌چیز آماده است — فقط کاربر بسازید و لینک بدهید.'}</span>
        </div>
      </div>
      <div class="guide-tip">
        <b>${ico('activity', 13)} ${esc(guide.tip?.title || 'راهنمای این تب')}</b>
        <ul>${(guide.tip?.items || []).map((item) => `<li>${esc(item)}</li>`).join('')}</ul>
      </div>
      ${guide.steps.map((step, index) => `
        <div class="guide-step${step.done ? ' done' : ''}">
          <span class="g-tick">${step.done ? ico('check', 12) : Fmt.num(index + 1)}</span>
          <div class="g-main">
            <b>${esc(step.title)}</b>
            <p>${esc(step.hint)}</p>
            <p style="margin-top:6px;color:#b6d47e">${esc(step.detail || '')}</p>
            ${step.done ? '' : `<div class="g-go"><button data-guide-go="${esc(step.section)}">${ico('chevron', 12)} رفتن به این تب</button></div>`}
          </div>
        </div>`).join('')}
      ${guide.links?.smart ? `
      <div class="guide-tip">
        <b>${ico('link', 13)} اولین کاربر آماده: ${esc(guide.links.username)}</b>
        <div class="link-box" style="margin-top:9px">
          <div class="lb-main"><b>سابلینک هوشمند</b><code>${esc(guide.links.smart)}</code></div>
          <button class="copy-btn" data-copy="${esc(guide.links.smart)}" title="کپی">${ico('copy', 14)}</button>
        </div>
      </div>` : ''}`;
    $$('[data-guide-go]', host).forEach((button) => {
      button.onclick = () => {
        this.toggle(false);
        this.app.go(button.dataset.guideGo);
      };
    });
    $$('[data-copy]', host).forEach((button) => {
      button.onclick = () => this.app.safe(() => copyText(button.dataset.copy, button, this.app.toasts));
    });
  }

  bindEvents() {
    const pill = $('#guidePill');
    if (pill) pill.onclick = () => this.toggle();
    const close = $('#guideClose');
    if (close) close.onclick = () => this.toggle(false);
    const scrim = $('#guideScrim');
    if (scrim) scrim.onclick = () => this.toggle(false);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.store.get('guideOpen')) this.toggle(false);
    });
    // The section change itself reloads the guide (see ``showSection`` and the
    // refresh loop), so no listener is needed here — one request per tab.
    this.renderChip();
  }
}
