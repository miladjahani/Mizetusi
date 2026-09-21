/* NEXUS service worker — makes the panel installable and usable offline.
 *
 * Caching rules that matter for a control panel:
 *   · the app shell (css/js/icons) is cached, so a flaky mobile connection
 *     still paints the panel instead of a blank screen;
 *   · /api/* is never cached — a stale metrics or settings payload would be a
 *     lie, and the panel must always talk to the server;
 *   · subscriptions, the status window and the protocol sockets are never
 *     touched, because a cached subscription would hand out a dead node.
 */
// Replaced by the server with a content hash of the shipped assets, so every
// deploy installs a new worker and the stale shell cache is dropped.
const VERSION = 'nexus-dev';
const SHELL = `${VERSION}-shell`;
const OFFLINE_URL = '/login';

const PRECACHE = [
  '/static/app.css',
  '/static/js/core.js',
  '/static/js/ui.js',
  '/static/js/session.js',
  '/static/js/api.js',
  '/static/js/store.js',
  '/static/js/pwa.js',
  // Every module of the graph, not just the first few: a missing entry means an
  // offline launch fails to link the panel at all.
  '/static/js/views/dashboard.js',
  '/static/js/views/nodes.js',
  '/static/js/views/users.js',
  '/static/js/views/system.js',
  '/static/js/views/customize.js',
  '/static/js/views/tools.js',
  '/static/js/views/advanced.js',
  '/static/js/views/guide.js',
  '/static/js/app.js',
  '/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-maskable-512.png',
  '/static/icons/apple-touch-icon.png',
  '/static/icons/nexus.svg',
  OFFLINE_URL,
];

const NEVER_CACHE = [/^\/api\//, /^\/sub\//, /^\/feed\//, /^\/portal\//, /^\/status\//, /^\/health$/, /^\/ws/];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    // Individual failures must not abort the install (an icon may 404 after a
    // rebrand), so every entry is added on its own.
    await Promise.all(PRECACHE.map((url) => cache.add(new Request(url, { cache: 'reload' })).catch(() => null)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key !== SHELL).map((key) => caches.delete(key)));
    await self.clients.claim();
    // Tell the open panel that a newer build took over, so it can reload once
    // instead of showing the previous CSS/JS until the next manual refresh.
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach((client) => client.postMessage({ type: 'NEXUS_ACTIVATED', version: VERSION }));
  })());
});

self.addEventListener('message', (event) => {
  if (event.data === 'NEXUS_SKIP_WAITING') self.skipWaiting();
  if (event.data === 'NEXUS_VERSION') event.source?.postMessage({ version: VERSION });
});

const isNeverCached = (url) => NEVER_CACHE.some((rule) => rule.test(url.pathname));

async function networkFirst(request) {
  const cache = await caches.open(SHELL);
  try {
    const response = await fetch(request);
    if (response && response.ok) cache.put(request, response.clone());
    return response;
  } catch (error) {
    const cached = await cache.match(request);
    if (cached) return cached;
    const offline = await cache.match(OFFLINE_URL);
    if (offline) return offline;
    throw error;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(SHELL);
  const cached = await cache.match(request);
  if (cached) {
    // Refresh in the background so the next launch already has the new file.
    fetch(request).then((response) => { if (response && response.ok) cache.put(request, response.clone()); }).catch(() => {});
    return cached;
  }
  const response = await fetch(request);
  if (response && response.ok && response.type === 'basic') cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isNeverCached(url)) return;

  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request));
    return;
  }
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    event.respondWith(cacheFirst(request));
  }
});
