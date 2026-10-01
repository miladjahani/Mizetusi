/* The Worker's front door: one `fetch`, four kinds of answer.
 *
 *   /            the panel (the login page and the app are the same document)
 *   /api/*       the panel's own JSON API, behind the session cookie
 *   /sub/<uuid>  a subscription body (the UUID *is* the credential)
 *   /ws*         the VLESS relay
 *
 * There is no origin and no fallback: every route is answered here, which is
 * what lets this deployment run on the free plan at all. */

import { COOKIE_NAME, createSession, hashPassword, randomToken, readCookie, safeEqual, sessionCookie, verifySession } from './auth.js';
import { buildLinks, normalizeUuid, randomUuid, subscriptionBody } from './links.js';
import { panelHtml } from './panel.js';
import {
  DEFAULT_SETTINGS, countUsers, deleteUser, ensureSchema, findUser, findUserById, insertUser,
  listUsers, loadSettings, resetUsage, saveSettings, updateUser, userUsable,
} from './store.js';
import { vlessUpgrade } from './vless.js';
import { connect } from 'cloudflare:sockets';
import { addressAllowed } from './vless-core.js';

const VERSION = '1.0.0';

const json = (body, init = {}) => new Response(JSON.stringify(body), {
  ...init,
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...(init.headers || {}) },
});

const fail = (message, status = 400) => json({ error: message }, { status });

async function bodyOf(request) {
  try {
    return (await request.json()) || {};
  } catch {
    return {};
  }
}

async function authenticated(request, settings) {
  return verifySession(settings.sessionSecret, readCookie(request, COOKIE_NAME));
}

/** Everything the panel needs, and nothing it must not see (no hashes). */
function safeSettings(settings, request) {
  return {
    title: settings.title,
    endpointHost: settings.endpointHost || new URL(request.url).host,
    hosts: settings.hosts || [],
    wsPath: settings.wsPath || '/ws',
    subPath: settings.subPath || '/sub',
    defaultPassword: !settings.adminChanged,
  };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/health') {
      let database = 'unknown';
      try {
        await ensureSchema(env);
        database = 'ready';
      } catch (error) {
        database = `error: ${error.message}`;
      }
      return json({ ok: true, service: 'nexus-worker-panel', version: VERSION, database, host: url.host });
    }

    try {
      await ensureSchema(env);
    } catch (error) {
      return fail(`آماده‌سازی جدول کاربران شکست خورد: ${error.message}`, 500);
    }

    const settings = await loadSettings(env);

    if (path === '/ws' || path.startsWith('/ws/')) return vlessUpgrade(request, env, ctx);

    if (path.startsWith('/sub/')) {
      const uuid = normalizeUuid(decodeURIComponent(path.slice('/sub/'.length)));
      if (!uuid) return fail('not found', 404);
      const user = await findUser(env, uuid);
      if (!user) return fail('not found', 404);
      const links = userUsable(user)
        ? buildLinks({
          uuid: user.uuid,
          name: user.name,
          workerHost: settings.endpointHost || url.host,
          hosts: settings.hosts || [],
          path: settings.wsPath || '/ws',
        })
        : [];
      const accept = request.headers.get('Accept') || '';
      if (url.searchParams.get('plain') === '1' || (accept.includes('text/plain') && !accept.includes('*/*'))) {
        return new Response(links.join('\n'), { headers: { 'content-type': 'text/plain; charset=utf-8' } });
      }
      return new Response(subscriptionBody(links), {
        headers: {
          'content-type': 'text/plain; charset=utf-8',
          'profile-update-interval': '12',
          // The client's own screen shows this: no quota split is metered here,
          // so the whole usage is reported as «download», the same choice the
          // Python panel makes. total=0 is «unlimited» to every client.
          'subscription-userinfo': `upload=0; download=${Number(user.used_bytes) || 0}; total=${
            user.limit_gb ? Math.round(Number(user.limit_gb) * 1024 ** 3) : 0}; expire=${Number(user.expires_at) || 0}`,
        },
      });
    }

    if (path.startsWith('/api/')) return apiRoute(request, env, settings, url, ctx);

    if (path !== '/') return new Response('Not found\n', { status: 404, headers: { 'content-type': 'text/plain; charset=utf-8' } });
    return new Response(panelHtml({ host: url.host, version: VERSION }), {
      headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' },
    });
  },
};

async function apiRoute(request, env, settings, url, ctx) {
  const path = url.pathname.slice('/api'.length);

  if (path === '/login' && request.method === 'POST') {
    const { password } = await bodyOf(request);
    const hash = await hashPassword(String(password ?? ''), settings.adminSalt);
    if (!safeEqual(hash, settings.adminHash)) return fail('رمز عبور اشتباه است', 401);
    const token = await createSession(settings.sessionSecret);
    return json({ ok: true }, { headers: { 'Set-Cookie': sessionCookie(token) } });
  }

  if (path === '/logout' && request.method === 'POST') {
    return json({ ok: true }, { headers: { 'Set-Cookie': sessionCookie('', { clear: true }) } });
  }

  if (!(await authenticated(request, settings))) return fail('ابتدا وارد شوید', 401);

  if (path === '/state' && request.method === 'GET') {
    const [users, stats] = await Promise.all([listUsers(env), countUsers(env)]);
    return json({
      ok: true,
      host: url.host,
      version: VERSION,
      settings: safeSettings(settings, request),
      users,
      stats,
    });
  }

  if (path === '/users' && request.method === 'POST') {
    const body = await bodyOf(request);
    const name = String(body.name ?? '').trim();
    if (name.length < 2) return fail('نام کاربر حداقل دو حرف باشد');
    const uuid = normalizeUuid(body.uuid) || randomUuid();
    if (await findUser(env, uuid)) return fail('این UUID قبلاً ثبت شده');
    const user = await insertUser(env, {
      uuid,
      name: name.slice(0, 40),
      limitGb: Number(body.limit_gb) > 0 ? Number(body.limit_gb) : null,
      days: Number(body.days) > 0 ? Number(body.days) : null,
    });
    return json({ ok: true, user });
  }

  const userMatch = path.match(/^\/users\/(\d+)$/);
  if (userMatch) {
    const id = Number(userMatch[1]);
    const user = await findUserById(env, id);
    if (!user) return fail('کاربر پیدا نشد', 404);

    if (request.method === 'PUT') {
      const body = await bodyOf(request);
      const patch = {};
      if (body.name !== undefined) patch.name = String(body.name).trim().slice(0, 40) || user.name;
      if (body.enabled !== undefined) patch.enabled = Number(body.enabled) ? 1 : 0;
      if (body.limit_gb !== undefined) patch.limit_gb = Number(body.limit_gb) > 0 ? Number(body.limit_gb) : null;
      if (body.days !== undefined) {
        const days = Number(body.days);
        patch.expires_at = days > 0 ? Math.floor(Date.now() / 1000) + days * 86400 : null;
      }
      if (body.rotate) patch.uuid = randomUuid();
      const updated = await updateUser(env, id, patch);
      if (body.used_bytes === 0) await resetUsage(env, id);
      return json({ ok: true, user: body.used_bytes === 0 ? await findUserById(env, id) : updated });
    }

    if (request.method === 'DELETE') {
      await deleteUser(env, id);
      return json({ ok: true });
    }
  }

  const linksMatch = path.match(/^\/users\/(\d+)\/links$/);
  if (linksMatch && request.method === 'GET') {
    const user = await findUserById(env, Number(linksMatch[1]));
    if (!user) return fail('کاربر پیدا نشد', 404);
    const links = buildLinks({
      uuid: user.uuid,
      name: user.name,
      workerHost: settings.endpointHost || url.host,
      hosts: settings.hosts || [],
      path: settings.wsPath || '/ws',
    });
    return json({
      ok: true,
      subscription: `${url.origin}${settings.subPath || '/sub'}/${user.uuid}`,
      links,
      base64: subscriptionBody(links),
    });
  }

  if (path === '/settings' && request.method === 'POST') {
    const body = await bodyOf(request);
    const next = { ...settings };
    if (body.title !== undefined) next.title = String(body.title).trim().slice(0, 60) || DEFAULT_SETTINGS.title;
    if (body.endpointHost !== undefined) next.endpointHost = String(body.endpointHost).trim().slice(0, 190);
    if (body.wsPath !== undefined) {
      const wsPath = String(body.wsPath).trim();
      next.wsPath = wsPath.startsWith('/') ? wsPath.slice(0, 60) : `/${wsPath.slice(0, 59)}`;
    }
    if (Array.isArray(body.hosts)) {
      // A host list is what config links are built from, so a bad entry is
      // dropped here rather than handed to a user as a dead address.
      next.hosts = body.hosts
        .map((host) => String(host).trim())
        .filter((host) => host && host.length <= 190 && !/[\s/?#]/.test(host))
        .slice(0, 12);
    }
    await saveSettings(env, next);
    return json({ ok: true, settings: safeSettings(next, request) });
  }

  /* «Can this deployment reach the internet at all?» — the one question a
     Worker proxy cannot answer about itself.

     It is behind the session, because an open endpoint that dials any address
     a caller names is a port scanner with your deployment's IP behind it. The
     answer says what the platform said, verbatim: `connect()` refusing a
     destination and the remote host refusing the connection look identical to
     a user (nothing loads), and completely different to whoever has to fix it. */
  if (path === '/diag' && request.method === 'POST') {
    const body = await bodyOf(request);
    const address = String(body.address || 'cloudflare.com').trim();
    const port = Number(body.port) || 443;
    if (!addressAllowed(address)) return fail('این مقصد مجاز نیست', 400);
    let socket;
    try {
      socket = connect({ hostname: address, port });
      await socket.opened;
      const closing = socket.close();
      if (ctx?.waitUntil) ctx.waitUntil(closing);
      return json({ ok: true, address, port, message: 'اتصال خروجی برقرار شد' });
    } catch (error) {
      try { socket?.close?.(); } catch { /* already gone */ }
      return json({ ok: false, address, port, error: String(error?.message || error) });
    }
  }

  if (path === '/password' && request.method === 'POST') {
    const body = await bodyOf(request);
    const current = await hashPassword(String(body.current ?? ''), settings.adminSalt);
    if (!safeEqual(current, settings.adminHash)) return fail('رمز فعلی اشتباه است', 401);
    const next = String(body.next ?? '');
    if (next.length < 6) return fail('رمز جدید حداقل ۶ کاراکتر باشد');
    const salt = randomToken(16);
    await saveSettings(env, {
      ...settings,
      adminSalt: salt,
      adminHash: await hashPassword(next, salt),
      adminChanged: true,
      // Every cookie signed with the old secret stops being valid, which is the
      // point: changing the password must end the sessions it protected.
      sessionSecret: randomToken(32),
    });
    return json({ ok: true }, { headers: { 'Set-Cookie': sessionCookie('', { clear: true }) } });
  }

  return fail('not found', 404);
}
