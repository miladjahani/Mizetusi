export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith('/ws')) return new Response('ZEUS Worker OK', {status: 200});
    if (request.headers.get('Upgrade') !== 'websocket') return new Response('WebSocket required', {status: 426});
    const origin = String(env.ZEUS_ORIGIN || '').replace(/\/$/, '');
    if (!origin.startsWith('https://')) return new Response('ZEUS_ORIGIN is not configured', {status: 503});
    const target = origin + url.pathname + url.search;
    const headers = new Headers(request.headers);
    headers.set('Host', new URL(origin).host);
    const upstream = new Request(target, {method: request.method, headers, body: request.body, redirect: 'manual'});
    return fetch(upstream);
  }
};
