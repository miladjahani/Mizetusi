/* Where the panel keeps things: KV for its own settings, D1 for the users.

   The split is not arbitrary. Settings are a single small JSON document that is
   read on nearly every request and written by one person occasionally — a KV key
   is exactly that. Users are rows the *proxy* queries on every WebSocket open
   and the panel counts, filters and updates — a table with an index on the UUID
   is exactly that.

   `ensureSchema` is idempotent and runs once per isolate rather than once per
   request, so a deployment that was created before its schema was pushed heals
   itself on the first request instead of answering 500 until somebody notices. */

import { hashPassword, randomToken } from './auth.js';

export const SETTINGS_KEY = 'nexus:settings';

export const DEFAULT_SETTINGS = {
  title: 'NEXUS Worker Panel',
  // Where users connect. Filled with the Worker's own hostname on first boot.
  endpointHost: '',
  hosts: [],
  wsPath: '/ws',
  subPath: '/sub',
  adminSalt: '',
  adminHash: '',
  sessionSecret: '',
  // Flipped the moment the shipped default is replaced; the panel keeps a
  // warning on screen until it is, because the Worker's address is public.
  adminChanged: false,
  defaultLimitGb: 0,
  defaultDays: 30,
  updatedAt: 0,
};

let schemaReady = false;

export async function ensureSchema(env) {
  if (schemaReady) return;
  await env.NEXUS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS nexus_users (
       id           INTEGER PRIMARY KEY AUTOINCREMENT,
       uuid         TEXT    NOT NULL UNIQUE,
       name         TEXT    NOT NULL,
       enabled      INTEGER NOT NULL DEFAULT 1,
       used_bytes   INTEGER NOT NULL DEFAULT 0,
       limit_gb     REAL,
       expires_at   INTEGER,
       created_at   INTEGER NOT NULL,
       last_seen_at INTEGER
     )`,
  ).run();
  await env.NEXUS_DB.prepare('CREATE INDEX IF NOT EXISTS nexus_users_uuid ON nexus_users (uuid)').run();
  schemaReady = true;
}

/**
 * The settings document, created on first boot.
 *
 * The first boot mints the session secret and seeds the admin password with the
 * shipped default, and says so in the panel until it is changed — the same
 * contract the Python panel has, for the same reason: a fresh deployment must
 * not be lockable-out-of, and must not stay on a guessable password either.
 */
export async function loadSettings(env) {
  const stored = await env.NEXUS_KV.get(SETTINGS_KEY, 'json');
  if (stored && stored.sessionSecret) return { ...DEFAULT_SETTINGS, ...stored };
  const salt = randomToken(16);
  const settings = {
    ...DEFAULT_SETTINGS,
    adminSalt: salt,
    adminHash: await hashPassword('admin', salt),
    sessionSecret: randomToken(32),
    updatedAt: Math.floor(Date.now() / 1000),
  };
  await saveSettings(env, settings);
  return settings;
}

export async function saveSettings(env, settings) {
  const next = { ...settings, updatedAt: Math.floor(Date.now() / 1000) };
  await env.NEXUS_KV.put(SETTINGS_KEY, JSON.stringify(next));
  return next;
}

/* ------------------------------------------------------------------- users */

const FIELDS = 'id, uuid, name, enabled, used_bytes, limit_gb, expires_at, created_at, last_seen_at';

export async function listUsers(env) {
  const result = await env.NEXUS_DB.prepare(`SELECT ${FIELDS} FROM nexus_users ORDER BY created_at DESC`).all();
  return result?.results || [];
}

/** The proxy's question, answered with one indexed lookup. */
export async function findUser(env, uuid) {
  return env.NEXUS_DB.prepare(`SELECT ${FIELDS} FROM nexus_users WHERE uuid = ?`).bind(uuid).first();
}

export async function findUserById(env, id) {
  return env.NEXUS_DB.prepare(`SELECT ${FIELDS} FROM nexus_users WHERE id = ?`).bind(id).first();
}

export async function countUsers(env) {
  const row = await env.NEXUS_DB.prepare(
    `SELECT COUNT(*) AS total,
            SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
            SUM(used_bytes) AS used_bytes,
            SUM(CASE WHEN last_seen_at > ? THEN 1 ELSE 0 END) AS online
       FROM nexus_users`,
  ).bind(Math.floor(Date.now() / 1000) - 900).first();
  return {
    total: Number(row?.total || 0),
    enabled: Number(row?.enabled || 0),
    usedBytes: Number(row?.used_bytes || 0),
    online: Number(row?.online || 0),
  };
}

export async function insertUser(env, { uuid, name, limitGb = null, days = null }) {
  const now = Math.floor(Date.now() / 1000);
  const expires = days ? now + Number(days) * 86400 : null;
  await env.NEXUS_DB.prepare(
    'INSERT INTO nexus_users (uuid, name, enabled, used_bytes, limit_gb, expires_at, created_at) VALUES (?, ?, 1, 0, ?, ?, ?)',
  ).bind(uuid, name, limitGb || null, expires, now).run();
  return findUser(env, uuid);
}

export async function updateUser(env, id, patch) {
  const sets = [];
  const values = [];
  const assign = (column, value) => { sets.push(`${column} = ?`); values.push(value); };
  if (patch.name !== undefined) assign('name', patch.name);
  if (patch.enabled !== undefined) assign('enabled', patch.enabled ? 1 : 0);
  if (patch.limit_gb !== undefined) assign('limit_gb', patch.limit_gb || null);
  if (patch.expires_at !== undefined) assign('expires_at', patch.expires_at || null);
  if (patch.uuid !== undefined) assign('uuid', patch.uuid);
  if (!sets.length) return findUserById(env, id);
  values.push(id);
  await env.NEXUS_DB.prepare(`UPDATE nexus_users SET ${sets.join(', ')} WHERE id = ?`).bind(...values).run();
  return findUserById(env, id);
}

export async function deleteUser(env, id) {
  await env.NEXUS_DB.prepare('DELETE FROM nexus_users WHERE id = ?').bind(id).run();
}

export async function resetUsage(env, id) {
  await env.NEXUS_DB.prepare('UPDATE nexus_users SET used_bytes = 0 WHERE id = ?').bind(id).run();
}

/** Called by the relay itself, so the panel's usage column is measured. */
export async function addTraffic(env, uuid, bytes) {
  if (!bytes) return;
  await env.NEXUS_DB.prepare(
    'UPDATE nexus_users SET used_bytes = used_bytes + ?, last_seen_at = ? WHERE uuid = ?',
  ).bind(Math.max(0, Math.round(bytes)), Math.floor(Date.now() / 1000), uuid).run();
}

/** A user is usable when the admin left it on and its clock has not run out. */
export function userUsable(user) {
  if (!user || !user.enabled) return false;
  if (user.expires_at && Number(user.expires_at) < Math.floor(Date.now() / 1000)) return false;
  return true;
}
