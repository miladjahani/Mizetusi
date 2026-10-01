/* The proxy itself: VLESS over WebSocket, terminated inside the Worker.

   There is no origin here and no process. A Worker can speak TCP outwards
   (`cloudflare:sockets`) and it can accept a WebSocket, so the whole relay is a
   bridge between the two — the client gets a WebSocket to Cloudflare's anycast
   address, and the remote host gets an ordinary TCP connection from Cloudflare.

   Three rules keep it honest:

   * **Nobody is proxied before they are recognised.** The opening frame names a
     UUID; if D1 does not return an enabled, unexpired user for it, the socket is
     closed and no connection is dialled. The Worker never becomes an open relay.
   * **The usage counter is measured here, not inferred.** Bytes are added up as
     they cross and flushed to D1 in batches (and once more on close), because a
     per-packet `UPDATE` would spend the whole request's CPU budget on writes.
   * **A hostile destination is refused** (`addressAllowed`), so a user cannot
     use the relay to probe the platform's own neighbourhood. */

import { connect } from 'cloudflare:sockets';
import { CMD_TCP, VLESS_RESPONSE, addressAllowed, concatBytes, parseVlessHeader } from './vless-core.js';
import { addTraffic, findUser, userUsable } from './store.js';

/** Flush the usage counter after roughly this many bytes, not after every frame. */
const FLUSH_AFTER = 128 * 1024;
/** A header larger than this is not a header — stop buffering and hang up. */
const MAX_HEADER = 4096;

const toBytes = (data) => {
  if (!data) return null;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (data instanceof Uint8Array) return data;
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  if (typeof data === 'string') return new TextEncoder().encode(data);
  return null;
};

export function vlessUpgrade(request, env, ctx) {
  const upgrade = (request.headers.get('Upgrade') || '').toLowerCase();
  if (upgrade !== 'websocket') {
    return new Response('This endpoint speaks WebSocket only.\n', {
      status: 426,
      headers: { 'content-type': 'text/plain; charset=utf-8', upgrade: 'websocket' },
    });
  }
  const pair = new WebSocketPair();
  const client = pair[0];
  const server = pair[1];
  server.accept();
  runSession(server, env, ctx).catch(() => {
    try { server.close(1011, 'relay failure'); } catch { /* already closed */ }
  });
  return new Response(null, { status: 101, webSocket: client });
}

async function runSession(ws, env, ctx) {
  let buffered = new Uint8Array(0);
  let socket = null;
  let writer = null;
  let reader = null;
  let user = null;
  let pending = 0;
  let done = false;
  let chain = Promise.resolve();

  const flush = () => {
    if (!user || !pending) return;
    const bytes = pending;
    pending = 0;
    const promise = addTraffic(env, user.uuid, bytes).catch(() => {});
    if (ctx?.waitUntil) ctx.waitUntil(promise);
  };

  const shutdown = (code = 1000, reason = '') => {
    if (done) return;
    done = true;
    flush();
    try { reader?.cancel?.(); } catch { /* nothing to cancel */ }
    try { writer?.releaseLock?.(); } catch { /* already released */ }
    try { if (socket) ctx?.waitUntil ? ctx.waitUntil(socket.close()) : socket.close(); } catch { /* already closed */ }
    try { ws.close(code, reason); } catch { /* already closed */ }
  };

  /** Remote → client. Started only after the VLESS response header has gone out,
   *  so the client never sees stream bytes before it sees the answer.
   *
   *  A failure here is reported rather than swallowed: a relay that closes
   *  quietly leaves the user staring at a client that says «connected» and
   *  loads nothing, and leaves whoever deployed it nothing to go on either. */
  const pump = async () => {
    let failure = '';
    try {
      for (;;) {
        const { value, done: finished } = await reader.read();
        if (finished) break;
        if (value?.length) {
          ws.send(value);
          pending += value.length;
          if (pending >= FLUSH_AFTER) flush();
        }
      }
    } catch (error) {
      failure = String(error?.message || error || 'read failed');
    }
    shutdown(failure ? 1011 : 1000, failure ? failure.slice(0, 110) : 'closed');
  };

  const onMessage = async (data) => {
    if (done) return;
    const bytes = toBytes(data);
    if (!bytes?.length) return;

    if (!writer) {
      buffered = concatBytes(buffered, bytes);
      const header = parseVlessHeader(buffered);
      if (!header) {
        if (buffered.length > MAX_HEADER) shutdown(1009, 'header too large');
        return;
      }
      if (header.command !== CMD_TCP) {
        // UDP and the mux protocol have no equivalent here; saying so is better
        // than dialling a TCP socket the client will not understand.
        shutdown(1003, 'unsupported command');
        return;
      }
      const found = await findUser(env, header.uuid);
      if (!userUsable(found)) {
        shutdown(1008, 'unknown or disabled user');
        return;
      }
      if (!addressAllowed(header.address)) {
        shutdown(1008, 'destination refused');
        return;
      }
      user = found;
      try {
        socket = connect({ hostname: header.address, port: header.port });
        writer = socket.writable.getWriter();
        reader = socket.readable.getReader();
        // `connect()` returns before the handshake is done, so the outcome of
        // the dial is only known here — and it is the difference between «the
        // user's client is misconfigured» and «this deployment cannot reach the
        // internet», which is the one thing worth telling whoever is looking.
        await socket.opened;
      } catch (error) {
        shutdown(1011, `dial failed: ${error?.message || error}`.slice(0, 110));
        return;
      }
      ws.send(VLESS_RESPONSE);
      pump();
      if (header.payload?.length) {
        await writer.write(header.payload);
        pending += header.payload.length;
      }
      return;
    }

    await writer.write(bytes);
    pending += bytes.length;
    if (pending >= FLUSH_AFTER) flush();
  };

  ws.addEventListener('message', (event) => {
    chain = chain.then(() => onMessage(event.data)).catch((error) => {
      shutdown(1011, String(error?.message || error || 'relay error').slice(0, 110));
    });
  });
  ws.addEventListener('close', () => shutdown(1000, 'client closed'));
  ws.addEventListener('error', () => shutdown(1011, 'client error'));
}
