import os, sqlite3, threading
from contextlib import contextmanager

DEFAULT_SQLITE = '/data/nexus.db'
LEGACY_SQLITE = '/data/zeus.db'

def _default_sqlite():
    # Keep reading the pre-rebrand database file so an existing Railway volume
    # does not restart empty after the rename.
    if not os.path.exists(DEFAULT_SQLITE) and os.path.exists(LEGACY_SQLITE): return LEGACY_SQLITE
    return DEFAULT_SQLITE

SQLITE_PATH = os.getenv('SQLITE_PATH') or _default_sqlite()
DB_URL = os.getenv('DATABASE_URL') or os.getenv('database_url') or ('sqlite:///' + SQLITE_PATH)
_LOCK = threading.RLock()

def is_pg(): return DB_URL.startswith(('postgres://','postgresql://'))

SCHEMA = [
'''CREATE TABLE IF NOT EXISTS users (
 id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, uuid TEXT UNIQUE NOT NULL,
 protocol TEXT NOT NULL DEFAULT 'vless', limit_gb DOUBLE PRECISION, expiry_days INTEGER, limit_req BIGINT, ip_limit INTEGER,
 used_gb DOUBLE PRECISION NOT NULL DEFAULT 0, used_req BIGINT NOT NULL DEFAULT 0, lifetime_used_gb DOUBLE PRECISION NOT NULL DEFAULT 0,
 is_active INTEGER NOT NULL DEFAULT 1, start_on_first_connect INTEGER NOT NULL DEFAULT 0, first_connection_time BIGINT,
 created_at BIGINT NOT NULL, expires_at BIGINT, reset_volume_at BIGINT, reset_request_at BIGINT, ips TEXT DEFAULT '',
 fingerprint TEXT DEFAULT 'chrome', tls TEXT DEFAULT 'on', port INTEGER DEFAULT 443, sni TEXT, host TEXT,
 frag_len TEXT DEFAULT '', frag_int TEXT DEFAULT '', advanced_frag TEXT, cipher_suites TEXT, tls_mask TEXT,
 block_ads INTEGER DEFAULT 0, block_porn INTEGER DEFAULT 0, auto_rotate_ip INTEGER DEFAULT 0, rotate_time INTEGER DEFAULT 5,
 ip_operator TEXT DEFAULT 'all', ip_count INTEGER DEFAULT 5, user_proxy TEXT, metadata TEXT DEFAULT '{}')''',
'''CREATE TABLE IF NOT EXISTS proxies (
 id BIGSERIAL PRIMARY KEY, value TEXT UNIQUE NOT NULL, kind TEXT DEFAULT 'socks5', country TEXT,
 latency_ms DOUBLE PRECISION, enabled INTEGER DEFAULT 1, fail_count INTEGER DEFAULT 0, last_check BIGINT, metadata TEXT DEFAULT '{}')''',
'''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS audit_logs (id BIGSERIAL PRIMARY KEY, action TEXT, actor TEXT, detail TEXT, created_at BIGINT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS traffic_events (id BIGSERIAL PRIMARY KEY, user_id BIGINT, bytes BIGINT DEFAULT 0, requests BIGINT DEFAULT 0, ip TEXT, created_at BIGINT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS cf_ips (id BIGSERIAL PRIMARY KEY, ip TEXT UNIQUE NOT NULL, source TEXT, enabled INTEGER DEFAULT 1, latency_ms DOUBLE PRECISION, ok INTEGER DEFAULT 0, fail_count INTEGER DEFAULT 0, last_probe BIGINT, last_seen BIGINT)''',
'''CREATE INDEX IF NOT EXISTS idx_users_uuid ON users(uuid)''',
'''CREATE INDEX IF NOT EXISTS idx_traffic_user_time ON traffic_events(user_id,created_at)''',
'''CREATE TABLE IF NOT EXISTS nodes (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, server TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 443, tls INTEGER NOT NULL DEFAULT 1, sni TEXT, host TEXT, enabled INTEGER NOT NULL DEFAULT 1, latency_ms DOUBLE PRECISION, source TEXT, metadata TEXT DEFAULT '{}', created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)'''
]

def _connect():
    if is_pg():
        import psycopg
        c=psycopg.connect(DB_URL, connect_timeout=10)
        c.autocommit=False
        return c
    path=SQLITE_PATH
    if path != ':memory:': os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c=sqlite3.connect(path, timeout=30, check_same_thread=False)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c

def _sql(sql):
    if not is_pg():
        # Convert PostgreSQL-only identity syntax into SQLite syntax.
        return sql.replace('BIGSERIAL PRIMARY KEY','INTEGER PRIMARY KEY AUTOINCREMENT')
    return sql.replace('?', '%s')

@contextmanager
def conn():
    with _LOCK:
        c=_connect()
        try:
            if not is_pg(): c.row_factory=sqlite3.Row
            yield c; c.commit()
        except Exception:
            c.rollback(); raise
        finally: c.close()

def init_db():
    with conn() as c:
        for stmt in SCHEMA: c.execute(_sql(stmt))

def rows(sql,args=()):
    with conn() as c:
        cur=c.execute(_sql(sql),args); data=cur.fetchall()
        if is_pg(): return [dict(zip([d.name for d in cur.description],x)) for x in data]
        return [dict(x) for x in data]

def row(sql,args=()):
    with conn() as c:
        cur=c.execute(_sql(sql),args); x=cur.fetchone()
        if not x:return None
        if is_pg(): return dict(zip([d.name for d in cur.description],x))
        return dict(x)

def execute(sql,args=()):
    with conn() as c:
        cur=c.execute(_sql(sql),args)
        if is_pg():
            try: return cur.rowcount
            except Exception: return 0
        return getattr(cur,'lastrowid',None)
