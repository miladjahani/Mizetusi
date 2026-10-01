/* The panel's own door: one password, one signed cookie.

   Two decisions worth naming.

   * **The password is salted and stretched, not hashed once.** `crypto.subtle`
     gives us PBKDF2 in the runtime, so the stored value is a real KDF output
     rather than a bare digest of the password. The iteration count is the one
     number that is deliberately modest: a Worker invocation has a CPU budget
     (10 ms on the free plan), and a login that blows it fails at the edge rather
     than being merely slow. 10,000 PBKDF2-SHA256 rounds sit inside that budget
     and still cost an attacker a great deal more than one SHA-256.

   * **A session is a signed token, not a KV entry.** Nothing needs to be stored
     for a session to exist: `exp.signature` is verified with an HMAC key that
     lives in the settings, so signing out everywhere is one secret rotation and
     a Worker cold start can never lose a login. */

const encoder = new TextEncoder();
const ITERATIONS = 10000;
export const COOKIE_NAME = 'nexus_panel';
const SESSION_TTL = 60 * 60 * 24 * 7;

const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, '0')).join('');

/** Compare without leaking where the two strings first differ. */
export function safeEqual(left, right) {
  const a = String(left ?? '');
  const b = String(right ?? '');
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function randomToken(bytes = 32) {
  const buffer = new Uint8Array(bytes);
  crypto.getRandomValues(buffer);
  return hex(buffer);
}

export async function hashPassword(password, salt) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(String(password)), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: encoder.encode(String(salt)), iterations: ITERATIONS, hash: 'SHA-256' },
    key,
    256,
  );
  return hex(bits);
}

async function hmac(secret, data) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(String(secret)), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, encoder.encode(String(data))));
}

/** `base64url(expiry).signature` — verifiable without a lookup. */
export async function createSession(secret, ttl = SESSION_TTL) {
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + ttl }));
  return `${payload}.${await hmac(secret, payload)}`;
}

export async function verifySession(secret, token) {
  const [payload, signature] = String(token ?? '').split('.');
  if (!payload || !signature) return false;
  if (!safeEqual(await hmac(secret, payload), signature)) return false;
  try {
    const body = JSON.parse(atob(payload));
    return Number(body.exp) > Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}

export function readCookie(request, name = COOKIE_NAME) {
  const header = request.headers.get('Cookie') || '';
  for (const part of header.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return rest.join('=');
  }
  return '';
}

export function sessionCookie(token, { clear = false } = {}) {
  const attrs = [
    `${COOKIE_NAME}=${clear ? '' : token}`,
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
    'Secure',
    `Max-Age=${clear ? 0 : SESSION_TTL}`,
  ];
  return attrs.join('; ');
}
