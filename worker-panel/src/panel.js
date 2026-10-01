/* The panel, as one page.

   It is a string because there is no build step: a Worker serves exactly what is
   in its bundle, and one self-contained document means the panel loads in a
   single request with no asset pipeline to keep in sync. The palette is the
   repository's own (near-black green, lime accent) so the Worker panel and the
   Python panel look like the same product.

   Two things it deliberately says out loud:

   * **The default password.** Until the admin changes it, a banner stays on
     screen. A panel with a guessable password is worse than no panel, and the
     Worker's address is public the moment it exists.
   * **A custom domain.** `*.workers.dev` is filtered in Iran, so the panel says
     where to change it rather than letting a deployment look broken. */

export function panelHtml({ host, version }) {
  return `<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEXUS · پنل ورکر</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='%23c9f24c'/><text x='16' y='23' font-size='20' font-weight='900' text-anchor='middle' fill='%2312180a'>N</text></svg>">
<style>
*{box-sizing:border-box}
:root{
  --bg:#060a05;--panel:rgba(16,24,12,.74);--panel2:rgba(21,30,16,.6);
  --line:rgba(216,242,178,.10);--line2:rgba(216,242,178,.20);
  --ink:#eaf4dc;--muted:#93a885;--muted2:#6b7d60;
  --accent:#c9f24c;--accent2:#5fce62;--ok:#5fe08a;--warn:#ffc95c;--bad:#ff7a6b;
  --mono:ui-monospace,'SFMono-Regular',Menlo,Consolas,monospace;
  --r:20px;color-scheme:dark
}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:Vazirmatn,system-ui,'Segoe UI',Tahoma,sans-serif;
  background-image:radial-gradient(760px 320px at 85% -8%,rgba(201,242,76,.13),transparent 62%),
    radial-gradient(560px 280px at 4% 104%,rgba(95,206,98,.11),transparent 60%);
  min-height:100vh}
.wrap{max-width:1180px;margin:0 auto;padding:18px}
.glass{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
  backdrop-filter:blur(18px) saturate(140%);box-shadow:0 22px 60px rgba(0,0,0,.42)}
head,header{display:block}
.top{display:flex;align-items:center;gap:12px;padding:14px 16px;margin-bottom:14px}
.mark{width:44px;height:44px;flex:0 0 44px;border-radius:14px;display:grid;place-items:center;
  background:linear-gradient(140deg,#e6fb84,#c9f24c 55%,#6fce4a);color:#12180a;font-weight:900;font-size:20px}
.top h1{margin:0;font-size:17px}
.top .sub{font-size:10.5px;color:var(--muted);font-family:var(--mono);direction:ltr}
.spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:11px;padding:6px 11px;border-radius:99px;
  background:rgba(255,255,255,.05);color:#b6c7ab;border:1px solid var(--line);white-space:nowrap}
.pill.ok{color:#86efc0;background:rgba(62,230,160,.09);border-color:rgba(62,230,160,.2)}
.pill.warn{color:#ffd587;background:rgba(255,200,92,.09);border-color:rgba(255,200,92,.2)}
.pill.bad{color:#ff9dab;background:rgba(255,107,129,.09);border-color:rgba(255,107,129,.2)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--ok)}
button{font:inherit;cursor:pointer}
.btn{border:1px solid var(--line);background:rgba(255,255,255,.05);color:#d5e4c0;border-radius:12px;
  padding:9px 13px;font-size:12px;transition:.18s}
.btn:hover{background:rgba(255,255,255,.09);border-color:var(--line2)}
.btn.primary{background:linear-gradient(135deg,#e6fb84,#b7e93f 52%,#5fce62);color:#111a08;border:0;font-weight:700}
.btn.bad{color:#ffa8b5;border-color:rgba(255,107,129,.3);background:rgba(255,107,129,.1)}
.btn.tiny{padding:6px 9px;font-size:11px;border-radius:10px}
.grid{display:grid;gap:13px}
.stats{grid-template-columns:repeat(auto-fit,minmax(168px,1fr));margin-bottom:13px}
.stat{padding:15px;border-radius:18px;position:relative;overflow:hidden}
.stat .lbl{font-size:11.5px;color:var(--muted)}
.stat .val{font-family:var(--mono);font-size:26px;font-weight:700;margin-top:8px;direction:ltr;text-align:right}
.stat .foot{font-size:10.5px;color:var(--muted2);margin-top:3px}
.stat .bar{height:5px;border-radius:99px;background:rgba(255,255,255,.07);margin-top:10px;overflow:hidden}
.stat .bar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#c9f24c,#5fce62);transition:width .8s}
.card{padding:16px}
.card h2{margin:0;font-size:15px}
.card .hint{margin:5px 0 0;font-size:11.5px;color:var(--muted);line-height:1.8}
label{display:block;font-size:11.5px;color:#bccdb1;margin:13px 0 6px;font-weight:500}
label:first-child{margin-top:0}
input,textarea,select{width:100%;border:1px solid var(--line);border-radius:12px;background:rgba(5,10,5,.55);
  color:#fff;padding:10px 12px;outline:none;font-size:13px;font-family:inherit;transition:.18s}
input:focus,textarea:focus{border-color:rgba(201,242,76,.5);box-shadow:0 0 0 3px rgba(201,242,76,.1)}
textarea{min-height:84px;direction:ltr;font-family:var(--mono);font-size:12px}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0 12px}
.two{display:grid;grid-template-columns:1.35fr .95fr;gap:13px}
@media(max-width:900px){.two{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:10px}
th{text-align:right;font-size:10.5px;color:#8ea380;font-weight:600;padding:9px 10px;
  background:rgba(255,255,255,.035);white-space:nowrap}
td{padding:9px 10px;border-top:1px solid rgba(255,255,255,.05);vertical-align:middle}
tbody tr:hover{background:rgba(255,255,255,.03)}
.mono{font-family:var(--mono);direction:ltr;font-variant-numeric:tabular-nums}
.muted{color:var(--muted);font-size:11.5px}
.acts{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}
.ban{border-radius:16px;padding:13px 15px;margin-bottom:13px;font-size:12.5px;line-height:1.9;border:1px solid}
.ban.warn{background:rgba(255,200,92,.07);border-color:rgba(255,200,92,.26);color:#ffd995}
.ban.info{background:rgba(201,242,76,.06);border-color:rgba(201,242,76,.22);color:#e2f8ab}
.link{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:13px;
  padding:9px 11px;background:rgba(3,7,4,.5);margin-top:8px}
.link code{flex:1;min-width:0;font-family:var(--mono);font-size:10.5px;color:#b6d47e;direction:ltr;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.login{margin:8vh auto 0;max-width:410px;padding:30px;text-align:center}
.login h1{margin:14px 0 4px;font-size:25px}
.login p{color:var(--muted);font-size:12px;margin:0 0 18px}
.err{color:#ff9dab;font-size:11.5px;min-height:17px;margin-top:9px}
.hide{display:none!important}
.toast{position:fixed;bottom:16px;left:16px;background:rgba(12,19,10,.96);border:1px solid var(--line2);
  border-radius:14px;padding:11px 14px;font-size:12px;box-shadow:0 18px 44px rgba(0,0,0,.5);z-index:50;
  animation:in .25s ease}
@keyframes in{from{opacity:0;transform:translateY(10px)}}
</style>
</head>
<body>
<div id="loginView" class="wrap">
  <div class="login glass">
    <div class="mark" style="margin:0 auto">N</div>
    <h1>NEXUS</h1>
    <p>پنل ورکر · ورود مدیر</p>
    <input id="pw" type="password" placeholder="رمز عبور" autocomplete="current-password">
    <div class="err" id="loginErr"></div>
    <button class="btn primary" id="loginBtn" style="width:100%">ورود</button>
    <p style="margin-top:14px;font-size:10.5px;color:var(--muted2)">
      رمز پیش‌فرض <b class="mono">admin</b> است — بعد از ورود از «تنظیمات» عوضش کنید.
    </p>
  </div>
</div>

<div id="appView" class="wrap hide">
  <div class="top glass">
    <div class="mark">N</div>
    <div><h1 id="panelTitle">NEXUS Worker Panel</h1>
      <div class="sub" id="panelHost">${host}</div></div>
    <div class="spacer"></div>
    <span class="pill" id="statePill">—</span>
    <button class="btn tiny" id="logoutBtn">خروج</button>
  </div>

  <div id="banners"></div>

  <div class="grid stats" id="stats"></div>

  <div class="two">
    <div class="card glass">
      <h2>کاربران</h2>
      <p class="hint">هر کاربر یک UUID دارد که همان رمز اتصال اوست. با «چرخش UUID» لینک‌های قبلی بی‌اعتبار می‌شوند.</p>
      <div class="row">
        <div><label>نام کاربر</label><input id="newName" placeholder="ali"></div>
        <div><label>سقف حجم (GB) <span class="muted">۰ = نامحدود</span></label><input id="newLimit" type="number" min="0" value="0" dir="ltr"></div>
        <div><label>مدت (روز) <span class="muted">۰ = بدون انقضا</span></label><input id="newDays" type="number" min="0" value="30" dir="ltr"></div>
      </div>
      <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn primary" id="addUser">ساخت کاربر</button>
        <button class="btn" id="reload">بازخوانی</button>
      </div>
      <div style="overflow:auto">
        <table>
          <thead><tr><th>کاربر</th><th>UUID</th><th>مصرف</th><th>سقف</th><th>انقضا</th><th>وضعیت</th><th></th></tr></thead>
          <tbody id="userRows"></tbody>
        </table>
      </div>
    </div>

    <div>
      <div class="card glass">
        <h2>تنظیمات</h2>
        <p class="hint">دامنه/هاستی که لینک‌ها با آن ساخته می‌شوند. اگر Worker روی <b class="mono">*.workers.dev</b> باشد در ایران فیلتر است — یک دامنهٔ خودت را در Cloudflare به همین Worker وصل کن و اینجا بنویس.</p>
        <label>هاست اصلی (Host / SNI)</label>
        <input id="setHost" dir="ltr" placeholder="panel.example.com">
        <label>میزبان‌های اضافه (هر خط یکی) <span class="muted">IP تمیز کلودفلر</span></label>
        <textarea id="setHosts" placeholder="104.16.0.1&#10;104.17.0.1"></textarea>
        <label>مسیر WebSocket</label>
        <input id="setPath" dir="ltr" placeholder="/ws">
        <div style="margin-top:14px"><button class="btn primary" id="saveSettings">ذخیره</button></div>
      </div>

      <div class="card glass" style="margin-top:13px">
        <h2>رمز عبور</h2>
        <label>رمز فعلی</label><input id="pwNow" type="password">
        <label>رمز جدید</label><input id="pwNext" type="password">
        <div style="margin-top:14px"><button class="btn" id="savePw">تغییر رمز</button></div>
      </div>

      <div class="card glass" style="margin-top:13px">
        <h2>لینک اتصال</h2>
        <p class="hint">این سابلینک را در کلاینت (v2rayNG، Hiddify، Streisand…) اضافه کن. برای هر کاربر یک آدرس جدا وجود دارد.</p>
        <div id="subHook" class="muted">اول یک کاربر بساز.</div>
      </div>
    </div>
  </div>

  <p class="muted" style="text-align:center;margin:18px 0 8px">
    NEXUS Worker Panel · نسخهٔ ${version} · ترنسپورت: VLESS over WebSocket + TLS
  </p>
</div>

<script>
const $ = (id) => document.getElementById(id);
const api = async (path, options = {}) => {
  const response = await fetch('/api' + path, {
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
  return data;
};
const fmtBytes = (value) => {
  const bytes = Number(value) || 0;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0, size = bytes;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return (index === 0 ? String(Math.round(size)) : size.toFixed(2)) + ' ' + units[index];
};
const until = (seconds) => {
  const left = Math.max(0, Math.floor(seconds));
  if (!left) return 'پایان یافته';
  const days = Math.floor(left / 86400), hours = Math.floor((left % 86400) / 3600);
  if (days) return days + ' روز و ' + hours + ' ساعت';
  const minutes = Math.floor((left % 3600) / 60);
  return hours ? hours + ' ساعت و ' + minutes + ' دقیقه' : minutes + ' دقیقه';
};
const ago = (ts) => {
  if (!ts) return 'هرگز';
  const seconds = Math.floor(Date.now() / 1000) - Number(ts);
  if (seconds < 60) return 'همین حالا';
  if (seconds < 3600) return Math.floor(seconds / 60) + ' دقیقه پیش';
  if (seconds < 86400) return Math.floor(seconds / 3600) + ' ساعت پیش';
  return Math.floor(seconds / 86400) + ' روز پیش';
};
const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const toast = (message) => {
  const node = document.createElement('div');
  node.className = 'toast';
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 3200);
};

let state = { users: [], settings: {}, stats: {}, links: {}, host: '' };

function paintStats() {
  const s = state.stats || {};
  const users = state.users || [];
  const used = users.reduce((sum, user) => sum + (Number(user.used_bytes) || 0), 0);
  const capped = users.filter((user) => user.limit_gb).length;
  const cards = [
    { label: 'کاربران', value: s.total || 0, foot: (s.enabled || 0) + ' فعال', pct: 100 },
    { label: 'آنلاین (۱۵ دقیقه)', value: s.online || 0, foot: 'اتصال در پانزده دقیقهٔ اخیر', pct: s.total ? Math.round(((s.online || 0) / s.total) * 100) : 0 },
    { label: 'مصرف کل', value: fmtBytes(used), foot: capped + ' کاربر سقف حجم دارند', pct: 100 },
    { label: 'میزبان‌های لینک', value: (state.hosts || []).length, foot: 'IP تمیز و دامنه‌های اضافه', pct: 100 },
  ];
  $('stats').innerHTML = cards.map((card) => \`
    <div class="stat glass">
      <div class="lbl">\${esc(card.label)}</div>
      <div class="val">\${esc(card.value)}</div>
      <div class="foot">\${esc(card.foot)}</div>
      <div class="bar"><i style="width:\${Math.max(4, Math.min(100, card.pct))}%"></i></div>
    </div>\`).join('');
}

function paintUsers() {
  const rows = state.users || [];
  if (!rows.length) {
    $('userRows').innerHTML = '<tr><td colspan="7" class="muted" style="text-align:center;padding:22px">هنوز کاربری ساخته نشده</td></tr>';
    $('subHook').textContent = 'اول یک کاربر بساز.';
    return;
  }
  $('userRows').innerHTML = rows.map((user) => {
    const used = Number(user.used_bytes) || 0;
    const limit = user.limit_gb ? Number(user.limit_gb) * 1024 ** 3 : 0;
    const expired = user.expires_at && Number(user.expires_at) < Math.floor(Date.now() / 1000);
    const state_ = !user.enabled ? ['bad', 'غیرفعال'] : expired ? ['warn', 'منقضی'] : ['ok', 'فعال'];
    return \`<tr>
      <td><b>\${esc(user.name)}</b><div class="muted">\${esc(ago(user.last_seen_at))}</div></td>
      <td class="mono" style="font-size:10.5px">\${esc(String(user.uuid).slice(0, 13))}…</td>
      <td class="mono">\${esc(fmtBytes(used))}</td>
      <td class="mono">\${limit ? esc(fmtBytes(limit)) : '∞'}</td>
      <td class="muted">\${user.expires_at ? esc(until(Number(user.expires_at) - Date.now() / 1000)) : 'بدون انقضا'}</td>
      <td><span class="pill \${state_[0]}"><span class="dot" style="background:currentColor"></span>\${state_[1]}</span></td>
      <td><div class="acts">
        <button class="btn tiny" data-act="sub" data-id="\${user.id}">لینک</button>
        <button class="btn tiny" data-act="toggle" data-id="\${user.id}">\${user.enabled ? 'خاموش' : 'روشن'}</button>
        <button class="btn tiny" data-act="rotate" data-id="\${user.id}">چرخش UUID</button>
        <button class="btn tiny" data-act="reset" data-id="\${user.id}">صفر مصرف</button>
        <button class="btn tiny bad" data-act="delete" data-id="\${user.id}">حذف</button>
      </div></td>
    </tr>\`;
  }).join('');
  document.querySelectorAll('#userRows [data-act]').forEach((button) => {
    button.onclick = () => act(button.dataset.act, Number(button.dataset.id));
  });
}

function paintSettings() {
  $('panelTitle').textContent = state.settings.title || 'NEXUS Worker Panel';
  $('setHost').value = state.settings.endpointHost || state.host || '';
  $('setHosts').value = (state.settings.hosts || []).join('\\n');
  $('setPath').value = state.settings.wsPath || '/ws';
  const banners = [];
  if (state.settings.defaultPassword) {
    banners.push('<div class="ban warn"><b>رمز عبور هنوز پیش‌فرض است.</b> همین حالا از کارت «رمز عبور» عوضش کن — آدرس این پنل عمومی است و رمز پیش‌فرض را همه می‌دانند.</div>');
  }
  if (/workers\\.dev$/i.test(state.host || '')) {
    banners.push('<div class="ban info">این پنل روی <b class="mono">*.workers.dev</b> است که در ایران فیلتر می‌شود. یک دامنهٔ خودت را در Cloudflare به همین Worker وصل کن و در «هاست اصلی» بنویس.</div>');
  }
  banners.push('<div class="ban info">این ورکر بدون سرور و فقط با Cloudflare اجرا می‌شود: پنل + KV + D1 + پروکسی VLESS. برای هر آدرس، از <b>IP تمیز کلودفلر</b> در میزبان‌های اضافه استفاده کن.</div>');
  $('banners').innerHTML = banners.join('');
}

function paintLinks(user) {
  const data = state.links[user.id] || {};
  $('subHook').innerHTML = \`
    <div style="margin-top:8px"><b>\${esc(user.name)}</b></div>
    <div class="link"><code>\${esc(data.subscription || '')}</code>
      <button class="btn tiny" data-copy="\${esc(data.subscription || '')}">کپی</button></div>
    \${(data.links || []).slice(0, 3).map((link) => \`<div class="link"><code>\${esc(link)}</code>
      <button class="btn tiny" data-copy="\${esc(link)}">کپی</button></div>\`).join('')}\`;
  document.querySelectorAll('#subHook [data-copy]').forEach((button) => {
    button.onclick = async () => {
      try { await navigator.clipboard.writeText(button.dataset.copy); toast('کپی شد'); }
      catch { toast('کپی نشد — دستی انتخاب کن'); }
    };
  });
}

async function act(action, id) {
  const user = (state.users || []).find((item) => item.id === id);
  if (!user) return;
  try {
    if (action === 'sub') {
      const data = await api('/users/' + id + '/links');
      state.links[id] = data;
      paintLinks(user);
      toast('لینک ساخته شد');
    } else if (action === 'toggle') {
      await api('/users/' + id, { method: 'PUT', body: { enabled: user.enabled ? 0 : 1 } });
      await load();
    } else if (action === 'rotate') {
      if (!confirm('UUID این کاربر عوض شود؟ همهٔ لینک‌های قبلی بی‌اعتبار می‌شوند.')) return;
      await api('/users/' + id, { method: 'PUT', body: { rotate: 1 } });
      toast('UUID چرخید');
      await load();
    } else if (action === 'reset') {
      await api('/users/' + id, { method: 'PUT', body: { used_bytes: 0 } });
      toast('مصرف صفر شد');
      await load();
    } else if (action === 'delete') {
      if (!confirm('کاربر «' + user.name + '» حذف شود؟')) return;
      await api('/users/' + id, { method: 'DELETE' });
      toast('حذف شد');
      await load();
    }
  } catch (error) { toast(error.message); }
}

async function load() {
  const data = await api('/state');
  state = { ...state, ...data, links: state.links || {} };
  state.hosts = (data.settings.hosts || []);
  paintStats();
  paintUsers();
  paintSettings();
  $('statePill').className = 'pill ok';
  $('statePill').innerHTML = '<span class="dot"></span> متصل';
}

async function boot() {
  $('loginView').classList.add('hide');
  $('appView').classList.remove('hide');
  await load();
}

$('loginBtn').onclick = async () => {
  $('loginErr').textContent = '';
  try {
    await api('/login', { method: 'POST', body: { password: $('pw').value } });
    await boot();
  } catch (error) { $('loginErr').textContent = error.message; }
};
$('pw').addEventListener('keydown', (event) => { if (event.key === 'Enter') $('loginBtn').click(); });

$('logoutBtn').onclick = async () => {
  await api('/logout', { method: 'POST' }).catch(() => {});
  location.reload();
};
$('reload').onclick = () => load().then(() => toast('بازخوانی شد')).catch((error) => toast(error.message));

$('addUser').onclick = async () => {
  const name = $('newName').value.trim();
  if (!name) { toast('نام کاربر را بنویس'); return; }
  try {
    await api('/users', { method: 'POST', body: {
      name,
      limit_gb: Number($('newLimit').value) || 0,
      days: Number($('newDays').value) || 0,
    } });
    $('newName').value = '';
    toast('کاربر ساخته شد');
    await load();
  } catch (error) { toast(error.message); }
};

$('saveSettings').onclick = async () => {
  try {
    await api('/settings', { method: 'POST', body: {
      endpointHost: $('setHost').value.trim(),
      hosts: $('setHosts').value.split('\\n').map((line) => line.trim()).filter(Boolean),
      wsPath: $('setPath').value.trim() || '/ws',
    } });
    toast('ذخیره شد');
    await load();
  } catch (error) { toast(error.message); }
};

$('savePw').onclick = async () => {
  try {
    await api('/password', { method: 'POST', body: { current: $('pwNow').value, next: $('pwNext').value } });
    $('pwNow').value = ''; $('pwNext').value = '';
    toast('رمز عوض شد');
    await load();
  } catch (error) { toast(error.message); }
};

// An existing session skips the login card entirely: a page reload should not
// ask again for a password the cookie already proves.
api('/state').then(() => boot()).catch(() => {
  $('loginView').classList.remove('hide');
});
</script>
</body>
</html>`;
}
