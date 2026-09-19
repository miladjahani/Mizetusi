"""Clean-IP monitoring.

The pool itself now lives in :mod:`app.edge.sources`, which knows about several
CDN providers and about hand-written clean IPs. This module keeps the
measurement half — probing the pool and telling the rest of the app which
addresses are healthy — and preserves the function names the panel and the tests
have always used.
"""
import asyncio
import ipaddress
import time

from app.db import rows, execute

# Kept for the callers that ask for Cloudflare's published ranges directly.
CF_URLS = ["https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6"]


def fetch_cidr_lists():
    """Cloudflare's official ranges (the historical entry point)."""
    from app.edge import sources
    for url in CF_URLS:
        try:
            found = sources.parse_list('cloudflare', sources._http_text(url))
        except Exception:
            continue
        if found:
            return found
    return []


def expand_networks(networks, limit=512):
    """Sample addresses inside the given networks (bounded, deterministic)."""
    from app.edge import sources
    cleaned = []
    for net in networks:
        value = net if isinstance(net, str) else str(net)
        try:
            parsed = ipaddress.ip_network(value, strict=False)
        except ValueError:
            continue
        if parsed.version == 4:
            cleaned.append(str(parsed))
    return sources.sample(cleaned, limit=limit)


def seed_ips(limit=256, provider_id=None):
    """Fill the clean-IP pool from one provider (Cloudflare by default)."""
    from app.edge import sources
    if provider_id:
        return sources.scan(provider_id, limit=limit)['found']
    total = 0
    for result in sources.scan_all(limit):
        total += result.get('found') or 0
    return total


async def probe_one(ip, port=443, timeout=2.5):
    started = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return round((time.perf_counter() - started) * 1000, 1), True
    except Exception:
        return None, False


async def probe_all(concurrency=32, limit=256, provider_id=None):
    """TCP-probe the pool; a provider filter keeps one scan from starving another."""
    where = 'WHERE enabled=1'
    args = []
    if provider_id:
        where += ' AND source=?'
        args.append(str(provider_id))
    candidates = rows(f'SELECT ip FROM cf_ips {where} ORDER BY COALESCE(latency_ms,999999) LIMIT ?',
                      (*args, int(limit)))
    sem = asyncio.Semaphore(max(1, min(concurrency, 64)))

    async def one(item):
        async with sem:
            ms, ok = await probe_one(item['ip'])
            now = int(time.time())
            execute('UPDATE cf_ips SET latency_ms=?,ok=?,fail_count=CASE WHEN ?=1 THEN 0 ELSE fail_count+1 END,'
                    'last_probe=? WHERE ip=?', (ms, int(ok), int(ok), now, item['ip']))
            return item['ip'], ms, ok

    return await asyncio.gather(*(one(x) for x in candidates))


def best(limit=20, provider_id=None):
    """Healthy clean IPs, fastest first (optionally from one provider)."""
    if provider_id:
        return rows('SELECT * FROM cf_ips WHERE enabled=1 AND ok=1 AND source=? ORDER BY latency_ms ASC LIMIT ?',
                    (str(provider_id), limit))
    return rows('SELECT * FROM cf_ips WHERE enabled=1 AND ok=1 ORDER BY latency_ms ASC LIMIT ?', (limit,))


async def loop(interval=900):
    while True:
        try:
            if not rows('SELECT ip FROM cf_ips LIMIT 1'):
                seed_ips()
            await probe_all()
            # Keep the Node Catalog synchronized with the latest healthy results —
            # with a Worker when one is configured, and otherwise via the automatic
            # edge detection (a Cloudflare-fronted panel domain).
            try:
                from app.config import settings
                from app.db import row as db_row
                from app.nodes import auto_sync
                worker = db_row('SELECT value FROM settings WHERE key=?', ('cloudflare_worker_url',))
                worker_url = worker['value'] if worker else None
                await auto_sync(settings.public_base_url, worker_url)
            except Exception:
                pass
        except Exception:
            pass
        await asyncio.sleep(max(60, interval))
