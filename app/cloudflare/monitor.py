import asyncio, ipaddress, json, socket, time
from urllib.request import Request, urlopen
from app.db import rows, execute, is_pg

CF_URLS = ["https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6"]

def fetch_cidr_lists():
    out=[]
    for url in CF_URLS:
        req=Request(url, headers={"User-Agent":"NEXUS-Railway-Monitor/1.0"})
        with urlopen(req, timeout=15) as r:
            text=r.read().decode("utf-8", "replace")
        for line in text.splitlines():
            line=line.strip()
            if line:
                try: out.append(str(ipaddress.ip_network(line, strict=False)))
                except ValueError: pass
    return out

def expand_networks(networks, limit=512):
    # Probe a bounded representative set; never attempt an unbounded scan.
    ips=[]
    for net in networks:
        if net.version != 4: continue
        hosts=list(net.hosts()) if net.num_addresses <= 1024 else []
        if hosts: ips.extend(str(x) for x in hosts[:limit-len(ips)])
        if len(ips)>=limit: break
    return ips[:limit]

def seed_ips(limit=256):
    nets=fetch_cidr_lists(); ips=expand_networks(nets, limit)
    now=int(time.time())
    for ip in ips:
        try: execute('INSERT INTO cf_ips(ip,source,enabled,last_seen) VALUES(?,?,1,?) ON CONFLICT(ip) DO UPDATE SET last_seen=excluded.last_seen',(ip,'cloudflare-official',now))
        except Exception:
            try: execute('INSERT OR IGNORE INTO cf_ips(ip,source,enabled,last_seen) VALUES(?,?,1,?)',(ip,'cloudflare-official',now))
            except Exception: pass
    return len(ips)

async def probe_one(ip, port=443, timeout=2.5):
    started=time.perf_counter()
    try:
        reader, writer=await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close();
        try: await writer.wait_closed()
        except Exception: pass
        return round((time.perf_counter()-started)*1000,1), True
    except Exception:
        return None, False

async def probe_all(concurrency=32, limit=256):
    candidates=rows('SELECT ip FROM cf_ips WHERE enabled=1 ORDER BY COALESCE(latency_ms,999999) LIMIT ?', (limit,))
    sem=asyncio.Semaphore(max(1,min(concurrency,64)))
    async def one(item):
        async with sem:
            ms,ok=await probe_one(item['ip'])
            now=int(time.time())
            execute('UPDATE cf_ips SET latency_ms=?,ok=?,fail_count=CASE WHEN ?=1 THEN 0 ELSE fail_count+1 END,last_probe=? WHERE ip=?',(ms,int(ok),int(ok),now,item['ip']))
            return item['ip'],ms,ok
    return await asyncio.gather(*(one(x) for x in candidates))

def best(limit=20):
    return rows('SELECT * FROM cf_ips WHERE enabled=1 AND ok=1 ORDER BY latency_ms ASC LIMIT ?', (limit,))

async def loop(interval=900):
    while True:
        try:
            if not rows('SELECT ip FROM cf_ips LIMIT 1'):
                seed_ips()
            await probe_all()
            # Keep the Node Catalog synchronized with the latest healthy CF results.
            try:
                from app.config import settings
                from app.db import row as db_row
                from app.nodes import sync_from_sources
                worker = db_row('SELECT value FROM settings WHERE key=?', ('cloudflare_worker_url',))
                worker_url = worker['value'] if worker else None
                if worker_url:
                    sync_from_sources(settings.public_base_url, worker_url)
            except Exception:
                pass
        except Exception:
            pass
        await asyncio.sleep(max(60,interval))
