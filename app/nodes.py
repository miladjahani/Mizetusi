import time, urllib.parse
from app.db import rows, row, execute, is_pg
from app.cloudflare.monitor import best

def _ensure_schema():
    # Kept as a small idempotent migration so existing Railway databases can upgrade.
    stmt='''CREATE TABLE IF NOT EXISTS nodes (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, server TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 443, tls INTEGER NOT NULL DEFAULT 1, sni TEXT, host TEXT, enabled INTEGER NOT NULL DEFAULT 1, latency_ms DOUBLE PRECISION, source TEXT, metadata TEXT DEFAULT '{}', created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)'''
    try:
        execute(stmt)
        execute('CREATE INDEX IF NOT EXISTS idx_nodes_kind_enabled ON nodes(kind,enabled)')
    except Exception:
        # SQLite migration syntax is handled here explicitly.
        if not is_pg():
            execute(stmt.replace('BIGSERIAL PRIMARY KEY','INTEGER PRIMARY KEY AUTOINCREMENT'))
            execute('CREATE INDEX IF NOT EXISTS idx_nodes_kind_enabled ON nodes(kind,enabled)')

def ensure():
    _ensure_schema()

def list_nodes():
    ensure()
    return rows('SELECT * FROM nodes ORDER BY CASE WHEN latency_ms IS NULL THEN 1 ELSE 0 END, latency_ms, name')

def upsert(name, kind, server, port=443, tls=True, sni=None, host=None, source=None, metadata=None):
    ensure(); now=int(time.time()); metadata=metadata or {}
    if is_pg():
        return execute('INSERT INTO nodes(name,kind,server,port,tls,sni,host,source,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET kind=EXCLUDED.kind,server=EXCLUDED.server,port=EXCLUDED.port,tls=EXCLUDED.tls,sni=EXCLUDED.sni,host=EXCLUDED.host,source=EXCLUDED.source,metadata=EXCLUDED.metadata,updated_at=EXCLUDED.updated_at',(name,kind,server,port,int(tls),sni,host,source,str(metadata),now,now))
    return execute('INSERT INTO nodes(name,kind,server,port,tls,sni,host,source,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,server=excluded.server,port=excluded.port,tls=excluded.tls,sni=excluded.sni,host=excluded.host,source=excluded.source,metadata=excluded.metadata,updated_at=excluded.updated_at',(name,kind,server,port,int(tls),sni,host,source,str(metadata),now,now))

def sync_from_sources(public_base, worker_url=None):
    ensure(); created=0
    host=urllib.parse.urlparse(public_base).hostname
    if host:
        upsert('railway-direct','railway',host,443,True,host,host,'railway',{'role':'direct'})
        created += 1
    # Disable the previous Cloudflare catalog before rebuilding it from the latest
    # healthy probe results. Railway direct stays enabled as the baseline node.
    execute("UPDATE nodes SET enabled=0, updated_at=? WHERE kind='cloudflare'", (int(time.time()),))
    if worker_url:
        whost=urllib.parse.urlparse(worker_url).hostname
        if whost:
            for i,item in enumerate(best(20),1):
                upsert(f'cloudflare-{i:02d}','cloudflare',item['ip'],443,True,whost,whost,'cloudflare-probe',{'probe_latency_ms':item.get('latency_ms'),'worker_host':whost})
                execute('UPDATE nodes SET enabled=1, latency_ms=?, updated_at=? WHERE name=?', (item.get('latency_ms'), int(time.time()), f'cloudflare-{i:02d}'))
                created += 1
    return created
