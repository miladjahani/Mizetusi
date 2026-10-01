/* Config links and the subscription body.

   Pure string work, deliberately in its own module: the panel, the subscription
   endpoint and the tests must build the *same* link, and the only way to be sure
   of that is for there to be one function that builds it.

   A WebSocket VLESS link has two hostnames in it and mixing them up is the
   classic way a working deployment hands out dead links:

   * `address` — where the client actually connects (a clean Cloudflare IP, or
     the Worker's own hostname). This is the `@address:port` part.
   * `host`    — the name the client sends in the Host header and as SNI. For a
     clean-IP entry this is the Worker's hostname, because that is the only name
     Cloudflare will route to this Worker. */

export function normalizeUuid(value) {
  const text = String(value ?? '').trim().toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(text) ? text : null;
}

export function randomUuid() {
  return crypto.randomUUID().toLowerCase();
}

/** Base64 as a subscription endpoint needs it: the bytes, not the JS string. */
export function base64Utf8(text) {
  const bytes = new TextEncoder().encode(String(text ?? ''));
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

/**
 * One `vless://` link.
 *
 * `address` is dialled, `host` is what the TLS handshake and the Host header
 * carry. `security=tls` is not optional — a Worker only answers on HTTPS, and a
 * plaintext WS link would be both unusable and instantly recognisable.
 */
export function vlessLink({ uuid, address, host, port = 443, path = '/ws', name = 'NEXUS' }) {
  const params = new URLSearchParams({
    encryption: 'none',
    security: 'tls',
    type: 'ws',
    host: host || address,
    sni: host || address,
    path,
  });
  return `vless://${uuid}@${address}:${port}?${params.toString()}#${encodeURIComponent(name)}`;
}

/** The body a subscription client expects: the links, Base64'd, one per line. */
export function subscriptionBody(links) {
  return base64Utf8((links || []).join('\n'));
}

/**
 * Every link this user's subscription should carry, newest host first.
 *
 * The Worker's own hostname is always the first entry because it always works;
 * every extra host the admin added (clean IPs, a custom domain) is an *extra*
 * way in, never a replacement — which matters because a clean IP that stops
 * answering must not take the subscription down with it.
 */
export function buildLinks({ uuid, name, workerHost, hosts = [], path = '/ws', port = 443 }) {
  const targets = [];
  const seen = new Set();
  const push = (address) => {
    const value = String(address || '').trim();
    if (!value || seen.has(value)) return;
    seen.add(value);
    targets.push(value);
  };
  push(workerHost);
  hosts.forEach(push);
  return targets.map((address, index) => vlessLink({
    uuid,
    address,
    host: workerHost,
    port,
    path,
    name: targets.length > 1 ? `${name} · ${index + 1}` : name,
  }));
}
