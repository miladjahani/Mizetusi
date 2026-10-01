/* VLESS, parsed — the one place that turns a client's opening frame into
   «who is this and where do they want to go».

   The protocol is deliberately small. A VLESS client opens a WebSocket and sends
   one binary frame that begins with:

     0        version            (1 byte — only 0 exists)
     1..16    user id            (16 raw bytes, the UUID without its dashes)
     17       addon length       (how many option bytes follow — always 0 today)
     18..     addons             (skipped verbatim)
     n        command            (1 = TCP, 2 = UDP, 3 = the mux protocol)
     n+1..2   port               (big endian)
     n+3      address type       (1 = IPv4, 2 = domain, 3 = IPv6)
     n+4..    address            (fixed length, or a length byte then the name)

   and then the first bytes of the stream it wants proxied. The server answers
   with two zero bytes and from then on the socket is raw.

   Nothing here imports anything, which is the point: this is the part of the
   relay that can be driven by a Node test with a synthetic frame, rather than
   only by a real client against a deployed Worker. */

export const ADDR_IPV4 = 1;
export const ADDR_DOMAIN = 2;
export const ADDR_IPV6 = 3;

export const CMD_TCP = 1;
export const CMD_UDP = 2;
export const CMD_MUX = 3;

/** The two bytes every VLESS server answers with before the stream starts. */
export const VLESS_RESPONSE = new Uint8Array([0, 0]);

const decoder = new TextDecoder();

/** 16 raw bytes → the dashed UUID a panel shows and a client is configured with. */
export function uuidFromBytes(bytes) {
  const hex = [];
  for (let i = 0; i < 16; i += 1) hex.push((bytes[i] || 0).toString(16).padStart(2, '0'));
  const text = hex.join('');
  return `${text.slice(0, 8)}-${text.slice(8, 12)}-${text.slice(12, 16)}-${text.slice(16, 20)}-${text.slice(20)}`;
}

/** A UUID as text → its 16 bytes, or null when it is not a UUID at all. */
export function bytesFromUuid(uuid) {
  const text = String(uuid ?? '').trim().toLowerCase();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(text)) return null;
  const hex = text.replace(/-/g, '');
  const bytes = new Uint8Array(16);
  for (let i = 0; i < 16; i += 1) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return bytes;
}

/**
 * Parse the opening VLESS frame.
 *
 * Returns null — never throws — while the frame is still incomplete, which is
 * the state a relay sits in until the client's first (or first few) messages
 * have arrived: the caller appends and retries. `headLength` is where the header
 * ends, so everything after it is already stream data and must be written to the
 * remote socket rather than dropped.
 */
export function parseVlessHeader(input) {
  const buf = input instanceof Uint8Array ? input : new Uint8Array(input);
  // version + uuid + addon length + command + port + address type
  if (buf.length < 22) return null;
  if (buf[0] !== 0) return null;

  const uuid = uuidFromBytes(buf.subarray(1, 17));
  const optLength = buf[17];
  let cursor = 18 + optLength;
  if (buf.length < cursor + 4) return null;

  const command = buf[cursor];
  const port = (buf[cursor + 1] << 8) | buf[cursor + 2];
  const addrType = buf[cursor + 3];
  cursor += 4;

  let address = '';
  if (addrType === ADDR_IPV4) {
    if (buf.length < cursor + 4) return null;
    address = `${buf[cursor]}.${buf[cursor + 1]}.${buf[cursor + 2]}.${buf[cursor + 3]}`;
    cursor += 4;
  } else if (addrType === ADDR_DOMAIN) {
    if (buf.length < cursor + 1) return null;
    const length = buf[cursor];
    cursor += 1;
    if (buf.length < cursor + length) return null;
    address = decoder.decode(buf.subarray(cursor, cursor + length));
    cursor += length;
  } else if (addrType === ADDR_IPV6) {
    if (buf.length < cursor + 16) return null;
    const groups = [];
    for (let i = 0; i < 8; i += 1) {
      groups.push(((buf[cursor + i * 2] << 8) | buf[cursor + i * 2 + 1]).toString(16));
    }
    address = groups.join(':');
    cursor += 16;
  } else {
    return null;
  }

  if (!address || !port) return null;
  return { uuid, command, port, address, addrType, headLength: cursor, payload: buf.subarray(cursor) };
}

/** Append two chunks — a client may split its header across WebSocket frames. */
export function concatBytes(left, right) {
  const a = left instanceof Uint8Array ? left : new Uint8Array(0);
  const b = right instanceof Uint8Array ? right : new Uint8Array(0);
  if (!a.length) return b;
  if (!b.length) return a;
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/**
 * Addresses a relay must refuse to dial.
 *
 * A proxy reaches whatever the client asks for — that is what it is *for* — but
 * the addresses below are not the public internet: link-local is the cloud
 * metadata service on most hosts, and `.internal`/`localhost` are this
 * deployment's own neighbourhood. A user who can name them can probe the
 * platform rather than the network, so the relay declines instead of connecting.
 * This is deliberately a small deny-list and not an allow-list: an allow-list
 * would break the product.
 */
export function addressAllowed(address) {
  const host = String(address || '').trim().toLowerCase().replace(/^\[|\]$/g, '');
  if (!host) return false;
  if (host === 'localhost' || host.endsWith('.localhost')) return false;
  if (host.endsWith('.internal') || host.endsWith('.local')) return false;
  if (host === '169.254.169.254' || host.startsWith('169.254.')) return false;
  if (host === '::1' || host === '0:0:0:0:0:0:0:1') return false;
  if (host.startsWith('fe80:') || host.startsWith('fd00:') || host.startsWith('fc00:')) return false;
  return true;
}
