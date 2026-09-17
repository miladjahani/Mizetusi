/* =============================================================================
   NEXUS · view — dashboard. Pure rendering: it reads the store and paints,
   all loading happens in the app shell so every view shares the same data.
   ========================================================================== */
import { $, ico, esc, Fmt } from '../core.js';
import { Charts, StatusKit } from '../ui.js';

export class DashboardView {
  constructor(app) {
    this.app = app;
  }

  get store() { return this.app.store; }

  render() {
    this.heroChips();
    this.stats();
    this.traffic();
    this.mix();
    this.latency();
    this.topUsers();
    this.coreCard();
    this.cloudflareSummary();
    this.recentLogs();
  }

  heroChips() {
    const metrics = this.store.get('metrics');
    if (!metrics) return;
    const t = metrics.totals;
    const host = $('#heroChips');
    if (!host) return;
    host.innerHTML = [
      `<span class="chip">${ico('users', 13)} ${Fmt.num(t.users)} کاربر · ${Fmt.num(t.active_users)} فعال</span>`,
      `<span class="chip">${ico('nodes', 13)} ${Fmt.num(t.railway_nodes)} نود Railway · ${Fmt.num(t.cloudflare_nodes)} نود CF</span>`,
      `<span class="chip">${ico('zap', 13)} ${Fmt.num(t.requests)} درخواست ثبت‌شده</span>`,
      `<span class="chip">${ico('shield', 13)} ${Fmt.num(t.cf_ips_ok)} IP سالم Cloudflare</span>`,
      `<span class="chip">${ico('clock', 13)} آپ‌تایم ${Fmt.until(metrics.uptime_seconds)}</span>`,
    ].join('');
  }

  stats() {
    const metrics = this.store.get('metrics');
    if (!metrics) return;
    const t = metrics.totals;
    const trend = (metrics.series_hourly || []).slice(-14).map((point) => point.gb);
    const cards = [
      { k: 'users', label: 'کل کاربران', value: t.users, ico: 'users', cls: '', foot: `${Fmt.num(t.active_users)} فعال · ${Fmt.num(t.disabled_users)} غیرفعال` },
      { k: 'usage', label: 'مصرف کل', value: t.used_gb, digits: 2, unit: Fmt.size(t.used_gb).u, ico: 'activity', cls: 'a-violet', foot: `مجموع عمر: ${Fmt.sizeText(t.lifetime_gb)}`, series: trend, color: '#b6a9ff' },
      { k: 'nodes', label: 'نودهای فعال', value: t.nodes_enabled, ico: 'nodes', cls: 'a-ok', foot: `${Fmt.num(t.cloudflare_nodes)} CF · ${Fmt.num(t.railway_nodes)} Railway` },
      { k: 'ips', label: 'IP سالم Cloudflare', value: t.cf_ips_ok, ico: 'globe', cls: 'a-warn', foot: `از ${Fmt.num(t.cf_ips_total)} IP اسکن‌شده` },
      { k: 'req', label: 'درخواست‌ها', value: t.requests, ico: 'zap', cls: '', foot: `${Fmt.num(t.active_ips_1h)} IP فعال در ساعت اخیر` },
      { k: 'up', label: 'آپ‌تایم سرویس', value: 0, ico: 'clock', cls: 'a-ok', foot: `شروع: ${Fmt.dateTime(metrics.started_at)}`, uptime: true },
    ];
    const host = $('#dashStats');
    if (!host) return;
    if (host.children.length !== cards.length) {
      host.innerHTML = cards.map((card, index) => `
        <div class="stat glass ${card.cls}" style="--i:${index}">
          <div class="top"><span class="lbl">${esc(card.label)}</span><span class="ico">${ico(card.ico, 15)}</span></div>
          <div class="val" id="stat-${card.k}">۰</div>
          <div class="foot" id="stat-foot-${card.k}">${esc(card.foot)}</div>
          <div class="spark">${card.series ? Charts.sparkline(card.series, card.color) : ''}</div>
        </div>`).join('');
    }
    cards.forEach((card) => {
      const valueEl = $(`#stat-${card.k}`);
      if (card.uptime) {
        valueEl.innerHTML = esc(Fmt.until(metrics.uptime_seconds));
        valueEl.style.fontSize = '17px';
      } else {
        Charts.countUp(valueEl, card.value, card.digits || 0, card.unit || '');
      }
      const footEl = $(`#stat-foot-${card.k}`);
      if (footEl) footEl.textContent = card.foot;
    });
  }

  traffic() {
    const metrics = this.store.get('metrics');
    if (!metrics) return;
    const range = this.store.get('trafficRange');
    const hourly = metrics.series_hourly || [];
    const cutoff = Date.now() / 1000 - 24 * 3600;
    const windowed = range === 24 ? hourly.filter((point) => point.t >= cutoff) : hourly;
    const source = range === 24 ? (windowed.length ? windowed : hourly.slice(-24)) : hourly;
    const points = source.map((point) => ({
      t: point.t,
      v: point.gb,
      title: `${Fmt.shortTime(point.t)} · ${Fmt.shortDate(point.t)}`,
      label: range === 24 ? Fmt.shortTime(point.t) : Fmt.shortDate(point.t),
    }));
    const sub = $('#trafficSub');
    if (sub) sub.textContent = range === 24 ? 'حجم عبوری در ۲۴ ساعت گذشته' : 'حجم عبوری در ۷ روز گذشته';
    Charts.area($('#trafficChart'), points, {
      color: '#5ad1ff',
      color2: '#8b7bff',
      height: 215,
      axisFmt: (value) => Fmt.num(value, value < 10 ? 2 : 0),
      valueFmt: (value) => Fmt.sizeText(value),
    });
    const total = points.reduce((acc, point) => acc + point.v, 0);
    const peak = points.reduce((acc, point) => Math.max(acc, point.v), 0);
    const requests = (range === 24 ? windowed : hourly).reduce((acc, point) => acc + (point.requests || 0), 0);
    const legend = $('#trafficLegend');
    if (legend) {
      legend.innerHTML = [
        `<span><i style="background:#5ad1ff"></i>مجموع دوره: <b class="mono">${esc(Fmt.sizeText(total))}</b></span>`,
        `<span><i style="background:#8b7bff"></i>اوج بازه: <b class="mono">${esc(Fmt.sizeText(peak))}</b></span>`,
        `<span><i style="background:#34e0c0"></i>درخواست‌ها: <b class="mono">${Fmt.num(requests)}</b></span>`,
        `<span><i style="background:#ffc85c"></i>میانگین هر ساعت: <b class="mono">${esc(Fmt.sizeText(points.length ? total / points.length : 0))}</b></span>`,
      ].join('');
    }
  }

  mix() {
    const metrics = this.store.get('metrics');
    if (!metrics) return;
    const t = metrics.totals;
    const segments = [
      { label: 'نود Railway', value: t.railway_nodes, color: '#5ad1ff' },
      { label: 'نود Cloudflare', value: t.cloudflare_nodes, color: '#ffc85c' },
      { label: 'نود غیرفعال', value: Math.max(0, t.nodes - t.nodes_enabled), color: '#8b7bff' },
      { label: 'IP سالم CF', value: t.cf_ips_ok, color: '#34e0c0' },
    ].filter((segment) => segment.value > 0);
    const legend = Charts.donut($('#mixDonut'), segments, {
      centerValue: Fmt.num(t.nodes),
      centerLabel: 'نود در کاتالوگ',
    });
    const host = $('#mixLegend');
    if (host) host.innerHTML = legend;
  }

  latency() {
    const metrics = this.store.get('metrics');
    if (!metrics) return;
    const nodes = (metrics.nodes || []).filter((node) => node.enabled);
    const measured = nodes.filter((node) => node.latency_ms != null);
    const worst = Math.max(...measured.map((node) => node.latency_ms), 100);
    const items = (measured.length ? measured : nodes).slice(0, 7).map((node) => ({
      label: node.name,
      value: node.latency_ms != null ? `${Fmt.lat().format(node.latency_ms)} ms` : 'اندازه‌گیری نشده',
      pct: node.latency_ms != null ? (node.latency_ms / worst) * 100 : 4,
      tone: StatusKit.barTone(node.latency_ms),
      icon: `<span class="node-icon ${node.kind === 'cloudflare' ? 'cf' : ''}" style="width:22px;height:22px;flex:0 0 22px;border-radius:8px;font-size:10px">${node.kind === 'cloudflare' ? '☁' : 'R'}</span>`,
    }));
    Charts.bars($('#latencyBars'), items);
  }

  topUsers() {
    const metrics = this.store.get('metrics');
    if (!metrics) return;
    const host = $('#topUsers');
    if (!host) return;
    const top = (metrics.top_users || []).filter((user) => user.used_gb > 0);
    if (!top.length) {
      host.innerHTML = `<div class="empty">${ico('users', 32)}<div>هنوز مصرفی ثبت نشده</div></div>`;
      return;
    }
    const max = Math.max(...top.map((user) => user.used_gb));
    host.innerHTML = top.map((user, index) => `
      <div class="rank">
        <span class="idx mono">${Fmt.num(index + 1)}</span>
        <span class="avatar" style="width:28px;height:28px;flex:0 0 28px;font-size:11px">${esc((user.username || '?')[0].toUpperCase())}</span>
        <span class="who"><b>${esc(user.username)}</b>
          <span>${user.limit_gb ? `از ${esc(Fmt.sizeText(user.limit_gb))}` : 'بدون سقف حجم'} · ${esc((user.protocol || 'vless').toUpperCase())}</span></span>
        <span class="amt">${esc(Fmt.sizeText(user.used_gb))}</span>
        <span class="bar-track" style="width:52px;height:6px"><span class="bar-fill ${user.limit_gb && user.used_gb / user.limit_gb > 0.85 ? 'bad' : ''}" style="width:${(user.used_gb / max) * 100}%"></span></span>
      </div>`).join('');
  }

  coreCard() {
    const core = this.store.get('core');
    const pill = $('#corePill');
    if (!pill) return;
    if (!core) {
      pill.className = 'pill bad';
      pill.textContent = 'نامشخص';
      return;
    }
    pill.className = `pill ${core.running ? 'ok' : 'bad'}`;
    pill.innerHTML = `<i class="dot${core.running ? '' : ' bad'}"></i> ${core.running ? 'در حال اجرا' : 'متوقف'}`;
    const rows = [
      ['وضعیت هسته', core.running ? `Running${core.pid ? ` (PID ${core.pid})` : ''}` : 'Stopped'],
      ['پروتکل‌ها', (core.protocols || []).join(' / ')],
      ['ترنسپورت', core.transport || '—'],
      ['اندپوینت‌ها', (core.endpoints || []).join('  ')],
      ['باینری', core.binary || '—'],
      ['پورت‌های داخلی', `VLESS ${core.vless_listener ?? '—'} · Trojan ${core.trojan_listener ?? '—'}`],
    ];
    const host = $('#coreInfo');
    if (host) host.innerHTML = rows.map(([key, value]) => `<div class="kv-line"><span>${esc(key)}</span><b>${esc(value)}</b></div>`).join('');
  }

  cloudflareSummary() {
    const host = $('#cfSummary');
    if (!host) return;
    const metrics = this.store.get('metrics');
    const worker = this.store.get('worker');
    const totals = metrics ? metrics.totals : null;
    const nodes = this.store.get('nodes') || [];
    const cfNodes = nodes.filter((node) => node.kind === 'cloudflare' && node.enabled).length;
    const pill = $('#cfPill');
    if (pill) {
      if (worker?.configured) { pill.className = 'pill ok'; pill.innerHTML = '<i class="dot"></i> Worker فعال'; }
      else if (cfNodes > 0) { pill.className = 'pill ok'; pill.innerHTML = '<i class="dot"></i> حالت خودکار'; }
      else { pill.className = 'pill warn'; pill.innerHTML = 'Railway-only'; }
    }
    const rows = [
      ['حالت لبه', worker?.configured ? 'Worker' : cfNodes > 0 ? 'خودکار (دامنه پشت Cloudflare)' : 'فقط Railway'],
      ['آدرس Worker', worker?.url ? worker.url.replace(/^https?:\/\//, '') : '—'],
      ['IP سالم', totals ? `${Fmt.num(totals.cf_ips_ok)} از ${Fmt.num(totals.cf_ips_total)}` : '—'],
      ['نود CF منتشرشده', totals ? `${Fmt.num(Math.max(totals.cloudflare_nodes, cfNodes))}` : '—'],
      ['پروکسی خارجی', totals ? Fmt.num(totals.proxies) : '—'],
    ];
    host.innerHTML = `<div class="kv-list">${rows.map(([key, value]) => `<div class="kv-line"><span>${esc(key)}</span><b>${esc(value)}</b></div>`).join('')}</div>`;
  }

  recentLogs() {
    const host = $('#recentLogs');
    if (!host) return;
    const logs = (this.store.get('logs') || []).slice(0, 6);
    if (!logs.length) {
      host.innerHTML = `<div class="empty">${ico('list', 30)}<div>رویدادی ثبت نشده</div></div>`;
      return;
    }
    host.innerHTML = `<div class="kv-list">${logs.map((log) => `
      <div class="kv-line"><span>${esc(StatusKit.logLabel(log.action))}</span>
        <b title="${esc(log.detail || '')}">${esc(Fmt.truncate(log.detail || '—', 26))} · ${esc(Fmt.ago(log.created_at))}</b></div>`).join('')}</div>`;
  }

  bindEvents() {
    document.querySelectorAll('#trafficSeg button').forEach((button) => {
      button.onclick = () => {
        this.store.set('trafficRange', Number(button.dataset.range));
        document.querySelectorAll('#trafficSeg button').forEach((other) => other.classList.toggle('on', other === button));
        this.traffic();
      };
    });
  }
}
