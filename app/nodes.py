"""Node catalog and real latency probing.

Latency convention used by the whole panel:

    NULL  -> never probed yet
    >= 0  -> measured round-trip in milliseconds
    -1    -> the last probe failed, the node is held back from subscriptions

Two classes own the work: :class:`NodeCatalog` is the CRUD/sync view of the
``nodes`` table, :class:`NodeProbe` measures real connectivity. The module-level
function names are thin aliases kept for the routes and the tests.
"""
import asyncio
import json
import os
import ssl
import time
import urllib.parse

from app.db import rows, row, execute, is_pg
from app.cloudflare.monitor import best

FAILED_LATENCY = -1.0

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS nodes (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, '
    'kind TEXT NOT NULL, server TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 443, '
    'tls INTEGER NOT NULL DEFAULT 1, sni TEXT, host TEXT, enabled INTEGER NOT NULL DEFAULT 1, '
    'latency_ms DOUBLE PRECISION, source TEXT, metadata TEXT DEFAULT \'{}\', '
    'created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)'
)
INDEX = 'CREATE INDEX IF NOT EXISTS idx_nodes_kind_enabled ON nodes(kind,enabled)'

# Cloudflare / Railway hostnames cannot be guessed, so the origin comes from
# the environment Railway injects (or the admin's saved base URL).
ENV_ORIGIN_KEYS = ('PUBLIC_BASE_URL', 'RAILWAY_PUBLIC_DOMAIN', 'RAILWAY_STATIC_URL', 'RAILWAY_PRIVATE_DOMAIN')


class NodeCatalog:
    """Every read and write against the ``nodes`` table."""

    def __init__(self, probe=None):
        self._probe = probe

    # ------------------------------------------------------------------ schema
    def ensure(self):
        try:
            execute(SCHEMA)
            execute(INDEX)
        except Exception:
            if not is_pg():
                execute(SCHEMA.replace('BIGSERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT'))
                execute(INDEX)

    # ------------------------------------------------------------------ reads
    def list(self):
        self.ensure()
        # Fastest node first; failed and unprobed nodes sink to the bottom.
        return rows(
            'SELECT * FROM nodes ORDER BY '
            'CASE WHEN latency_ms IS NULL OR latency_ms < 0 THEN 1 ELSE 0 END, latency_ms, name'
        )

    def get(self, name):
        self.ensure()
        return row('SELECT * FROM nodes WHERE name=?', (name,))

    def enabled(self):
        self.ensure()
        return rows('SELECT * FROM nodes WHERE enabled=1')

    # ----------------------------------------------------------------- writes
    def upsert(self, name, kind, server, port=443, tls=True, sni=None, host=None,
               source=None, metadata=None):
        self.ensure()
        now = int(time.time())
        metadata = metadata or {}
        values = (name, kind, server, port, int(bool(tls)), sni, host, source, str(metadata), now, now)
        if is_pg():
            return execute(
                'INSERT INTO nodes(name,kind,server,port,tls,sni,host,source,metadata,created_at,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET kind=EXCLUDED.kind,'
                'server=EXCLUDED.server,port=EXCLUDED.port,tls=EXCLUDED.tls,sni=EXCLUDED.sni,host=EXCLUDED.host,'
                'source=EXCLUDED.source,metadata=EXCLUDED.metadata,updated_at=EXCLUDED.updated_at', values,
            )
        return execute(
            'INSERT INTO nodes(name,kind,server,port,tls,sni,host,source,metadata,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,'
            'server=excluded.server,port=excluded.port,tls=excluded.tls,sni=excluded.sni,host=excluded.host,'
            'source=excluded.source,metadata=excluded.metadata,updated_at=excluded.updated_at', values,
        )

    def update(self, name, fields):
        """Patch a node; unknown keys are ignored by the caller's whitelist."""
        sets, values = [], []
        for key, value in fields.items():
            sets.append(f'{key}=?')
            values.append(value)
        if not sets:
            return self.get(name)
        sets.append('updated_at=?')
        values.extend([int(time.time()), name])
        execute('UPDATE nodes SET ' + ','.join(sets) + ' WHERE name=?', values)
        return self.get(name)

    def delete(self, name):
        self.ensure()
        execute('DELETE FROM nodes WHERE name=?', (name,))

    def mark(self, name, latency, metadata=None):
        meta = metadata if metadata is None else json.dumps(metadata, ensure_ascii=False)
        if meta is None:
            execute('UPDATE nodes SET latency_ms=?, updated_at=? WHERE name=?',
                    (latency, int(time.time()), name))
        else:
            execute('UPDATE nodes SET latency_ms=?, metadata=?, updated_at=? WHERE name=?',
                    (latency, meta, int(time.time()), name))

    # ------------------------------------------------------------------- sync
    def origin_host(self, base_url=None):
        """The public host this deployment answers on (Railway injects it)."""
        candidate = (base_url or '').strip()
        if not candidate:
            for key in ENV_ORIGIN_KEYS:
                candidate = (os.getenv(key) or '').strip()
                if candidate:
                    break
        if not candidate:
            return None
        if '://' not in candidate:
            candidate = 'https://' + candidate
        return urllib.parse.urlparse(candidate).hostname

    def ensure_origin(self, base_url=None):
        """Guarantee the Railway direct node exists.

        Without it a fresh deployment publishes an empty Node Catalog (and every
        subscription 404s), so this runs at startup and whenever the panel is
        opened.
        """
        host = self.origin_host(base_url)
        if not host:
            return None
        node = self.get('railway-direct')
        if node and node.get('server') == host:
            return node
        self.upsert('railway-direct', 'railway', host, 443, True, host, host, 'railway', {'role': 'direct'})
        return self.get('railway-direct')

    def sync(self, base_url=None, worker_url=None):
        """Rebuild the catalog: Railway direct plus the healthy Cloudflare IPs."""
        self.ensure()
        created = 0
        if self.ensure_origin(base_url) is not None:
            created += 1
        # The previous Cloudflare catalog is disabled first so stale IPs never
        # survive a re-probe; Railway direct stays enabled as the baseline.
        execute("UPDATE nodes SET enabled=0, updated_at=? WHERE kind='cloudflare'", (int(time.time()),))
        if worker_url:
            whost = urllib.parse.urlparse(worker_url).hostname
            if whost:
                for i, item in enumerate(best(20), 1):
                    name = f'cloudflare-{i:02d}'
                    self.upsert(name, 'cloudflare', item['ip'], 443, True, whost, whost,
                                'cloudflare-probe',
                                {'probe_latency_ms': item.get('latency_ms'), 'worker_host': whost})
                    self.update(name, {'enabled': 1, 'latency_ms': item.get('latency_ms')})
                    created += 1
        return created


class NodeProbe:
    """Real connectivity measurement for one node or the whole catalog."""

    def __init__(self, catalog):
        self.catalog = catalog

    @staticmethod
    def metadata(node):
        raw = node.get('metadata')
        if isinstance(raw, dict):
            return dict(raw)
        try:
            data = json.loads(raw or '{}')
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    async def tcp(host, port, timeout=4.0, tls=False, server_hostname=None):
        """One connect (optionally a full TLS handshake) and the elapsed ms.

        This is the most honest ping available without spending a user's traffic:
        it proves the endpoint accepts connections, and with TLS that the exact
        IP + SNI pair a client will use really serves the expected certificate.
        """
        started = time.perf_counter()
        try:
            if tls:
                ctx = ssl.create_default_context()
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port, ssl=ctx, server_hostname=server_hostname or host),
                    timeout=timeout,
                )
            else:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return round((time.perf_counter() - started) * 1000, 1), None
        except Exception as exc:
            return None, type(exc).__name__

    async def ping(self, node, timeout=4.0):
        """Probe one node and persist the result (latency_ms + metadata)."""
        name = str(node.get('name') or '')
        kind = str(node.get('kind') or 'railway')
        server = str(node.get('server') or '').strip()
        port = int(node.get('port') or 443)
        host = str(node.get('host') or node.get('sni') or '').strip()
        ms, err, tls_ok = None, 'no server address', False
        if server:
            # Cloudflare clean IPs are dialled by IP with the Worker host as SNI:
            # that is exactly what the client does, so it is what we verify.
            if kind != 'cloudflare' or host:
                ms, err = await self.tcp(server, port, timeout=timeout, tls=True, server_hostname=host or server)
                tls_ok = ms is not None
            if ms is None:
                fallback_ms, fallback_err = await self.tcp(server, port, timeout=timeout)
                if fallback_ms is not None:
                    ms, err = fallback_ms, None
                elif err == 'no server address':
                    err = fallback_err
        now = int(time.time())
        ok = ms is not None
        meta = self.metadata(node)
        meta.update({'ping_ok': ok, 'ping_tls': tls_ok, 'ping_ms': ms, 'ping_at': now,
                     'ping_error': err, 'last_probe': now, 'probe_target': f'{server}:{port}'})
        try:
            self.catalog.mark(name, ms if ok else FAILED_LATENCY, meta)
        except Exception:
            pass
        return {'name': name, 'kind': kind, 'server': server, 'port': port, 'ok': ok,
                'tls_ok': tls_ok, 'latency_ms': ms, 'error': err, 'at': now}

    async def ping_all(self, names=None, timeout=4.0, concurrency=8):
        """Probe every enabled node: Cloudflare clean IPs and the Railway origin."""
        items = self.catalog.enabled()
        if names:
            wanted = {str(n).strip().lower() for n in names if str(n).strip()}
            items = [n for n in items if str(n['name']).lower() in wanted]
        sem = asyncio.Semaphore(max(1, min(int(concurrency or 8), 16)))

        async def one(item):
            async with sem:
                return await self.ping(item, timeout=timeout)

        results = list(await asyncio.gather(*(one(n) for n in items)))
        healthy = [r for r in results if r['ok']]
        return {
            'results': results,
            'nodes': self.catalog.list(),
            'probed': len(results),
            'healthy': len(healthy),
            'failed': len(results) - len(healthy),
            'avg_latency_ms': round(sum(float(r['latency_ms']) for r in healthy) / len(healthy), 1) if healthy else None,
        }

    async def loop(self, interval=900, interval_provider=None):
        """Keep every node's ping fresh in the background.

        Subscriptions are ordered by the last measured latency, so a stale
        catalog would quietly advertise a dead node. The panel can also trigger
        the same probe on demand with the «پینگ همه نودها» button.
        """
        while True:
            try:
                if self.catalog.enabled():
                    await self.ping_all(timeout=4.0)
            except Exception:
                pass
            wait = interval
            if interval_provider:
                try:
                    wait = interval_provider()
                except Exception:
                    wait = interval
            await asyncio.sleep(max(60, int(wait or interval)))


# --------------------------------------------------------------- module aliases
catalog = NodeCatalog()
probe = NodeProbe(catalog)
catalog._probe = probe


def ensure():
    return catalog.ensure()


def list_nodes():
    return catalog.list()


def upsert(name, kind, server, port=443, tls=True, sni=None, host=None, source=None, metadata=None):
    return catalog.upsert(name, kind, server, port, tls, sni, host, source, metadata)


def sync_from_sources(public_base, worker_url=None):
    return catalog.sync(public_base, worker_url)


def ensure_origin_node(public_base=None):
    return catalog.ensure_origin(public_base)


async def ping_node(node, timeout=4.0):
    return await probe.ping(node, timeout=timeout)


async def ping_all(names=None, timeout=4.0, concurrency=8):
    return await probe.ping_all(names=names, timeout=timeout, concurrency=concurrency)


async def ping_loop(interval=900, interval_provider=None):
    return await probe.loop(interval=interval, interval_provider=interval_provider)
