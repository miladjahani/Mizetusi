-- The Worker panel's one table.
--
-- Users live here rather than in KV because the proxy asks this question on
-- every WebSocket open — «is this UUID a real, enabled, unexpired user?» — and a
-- table answers it with one indexed lookup instead of listing a namespace. The
-- counters on the same row (`used_bytes`, `last_seen_at`) are written by the
-- relay itself, so the panel's usage column is measured rather than estimated.
--
-- The name is prefixed because this D1 database is shared with the account's
-- other projects; nothing here touches a table this file does not create.
CREATE TABLE IF NOT EXISTS nexus_users (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid         TEXT    NOT NULL UNIQUE,
  name         TEXT    NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1,
  used_bytes   INTEGER NOT NULL DEFAULT 0,
  limit_gb     REAL,
  expires_at   INTEGER,
  created_at   INTEGER NOT NULL,
  last_seen_at INTEGER
);

CREATE INDEX IF NOT EXISTS nexus_users_uuid ON nexus_users (uuid);
