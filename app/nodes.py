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
import ssl
import time

from app.db import rows, row, execute, is_pg
from app.cloudflare.monitor import best
from app import runtime
from app.edge import sources as edge_sources

FAILED_LATENCY = -1.0

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS nodes (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, '
    'kind TEXT NOT NULL, server TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 443, '
    'tls INTEGER NOT NULL DEFAULT 1, sni TEXT, host TEXT, enabled INTEGER NOT NULL DEFAULT 1, '
    'latency_ms DOUBLE PRECISION, source TEXT, metadata TEXT DEFAULT \'{}\', '
    'created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)'
)
INDEX = 'CREATE INDEX IF NOT EXISTS idx_nodes_kind_enabled ON nodes(kind,enabled)'

# Public hostnames cannot be guessed, so the origin comes from the environment
# the platform injects (Railway, Render, Fly, Koyeb, Heroku…) or from the
# admin's saved base URL — see :mod:`app.runtime`.
def origin_node_name(platform_id=None):
    """Name of this deployment's own node (``vps-direct``, ``render-direct``…)."""
    pid = platform_id or runtime.platform()
    return 'railway-direct' if pid == 'railway' else f'{pid}-direct'


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
        # metadata is stored as JSON: the panel and the subscription labels read
        # the location/provider back out of it, and a Python repr would not parse.
        payload = json.dumps(metadata, ensure_ascii=False) if isinstance(metadata, (dict, list)) else str(metadata)
        values = (name, kind, server, port, int(bool(tls)), sni, host, source, payload, now, now)
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
        """The public host this deployment answers on (env-injected per platform)."""
        candidate = (base_url or '').strip()
        if not candidate:
            return runtime.host()
        if '://' not in candidate:
            candidate = 'https://' + candidate
        import urllib.parse
        return urllib.parse.urlparse(candidate).hostname

    def ensure_origin(self, base_url=None):
        """Guarantee this deployment's own node exists.

        Without it a fresh deployment publishes an empty Node Catalog (and every
        subscription 404s), so this runs at startup and whenever the panel is
        opened. The node is named after the platform it runs on (``vps-direct``,
        ``render-direct``, ``railway-direct``) so a user sees where a location
        actually is, and it stays the one node that is never dropped when a probe
        fails — it is the guaranteed baseline of every subscription.
        """
        host = self.origin_host(base_url)
        if not host:
            return None
        name = origin_node_name()
        node = self.get(name)
        if node and node.get('server') == host:
            return node
        self.upsert(name, 'railway', host, 443, True, host, host, runtime.platform(),
                    {'role': 'direct', 'platform': runtime.platform(), 'location': ''})
        return self.get(name)

    # Deployment-shape detection is a network probe; the result is cached so a
    # sync storm (panel open, ping button, background loop) never hammers the
    # edge more than once per TTL.
    edge_cache = {'host': None, 'at': 0.0}
    EDGE_TTL = 600.0

    async def detect_edge(self, base_url=None, force=False):
        """Find the hostname clients can already reach THROUGH Cloudflare.

        Sources, in priority order: a configured Worker URL (checked by the
        caller) and — with no user action at all — the panel's own public
        domain when it is fronted by Cloudflare (custom-domain setups). The
        check is one TLS handshake against a healthy clean IP carrying the
        panel host as SNI, cached for ten minutes.
        """
        cache = NodeCatalog.edge_cache
        now = time.time()
        if not force and cache['host'] and now - cache['at'] < self.EDGE_TTL:
            return cache['host']
        host = None
        origin = self.origin_host(base_url)
        probe = self._probe
        if origin and probe:
            for item in self.clean_candidates(3):
                ms, _err = await NodeProbe.tcp(item['ip'], 443, timeout=3.0, tls=True,
                                               server_hostname=origin)
                if ms is not None:
                    host = origin
                    break
        cache.update({'host': host, 'at': now})
        return host

    @staticmethod
    def clean_candidates(limit=3, provider_id=None):
        """Clean IPs to test the edge against.

        Measured-healthy IPs come first, but a deployment that has not probed
        anything yet (``ok`` still 0) must not end up with an empty edge catalog,
        so unprobed entries are used as the fallback.
        """
        chosen = best(limit, provider_id)
        if len(chosen) < limit:
            seen = {item['ip'] for item in chosen}
            # Top up with entries that have not been measured yet: the ping loop
            # immediately probes them, so nothing unverified stays published for
            # long, and a fresh deployment still gets a full catalog.
            extra = rows('SELECT * FROM cf_ips WHERE enabled=1 '
                         'ORDER BY COALESCE(latency_ms,999999) ASC LIMIT ?', (limit,))
            chosen += [item for item in extra if item['ip'] not in seen]
        return chosen[:limit]

    def sync(self, base_url=None, worker_url=None, edge_host=None):
        """Rebuild the catalog: this deployment's own node plus every location.

        Everything here is automatic. When the admin has defined edge sources
        (clean-IP providers, hand-written IPs, clean domains — each with a
        location), those define the catalog: one group of nodes per location, each
        carrying its own Host/SNI. With no source configured the historical
        behaviour is unchanged: the Worker host (or a Cloudflare-fronted panel
        domain, detected by :meth:`detect_edge`) fronts the healthiest clean IPs.
        """
        self.ensure()
        created = 0
        if self.ensure_origin(base_url) is not None:
            created += 1
        # The previous edge catalog is disabled first so stale IPs never survive a
        # re-probe. Only the rows this sync owns are reset: a node the admin added
        # by hand — or from one of the ready-made samples — must survive a rebuild,
        # and so must the deployment's own node.
        execute("UPDATE nodes SET enabled=0, updated_at=? "
                "WHERE kind IN ('cloudflare','edge') "
                "AND (source IN ('cloudflare-probe','cloudflare-edge') OR metadata LIKE '%source_id%')",
                (int(time.time()),))
        edge = edge_host or NodeCatalog.edge_cache.get('host')
        keep = set()
        for item in edge_sources.plan(worker_url, edge):
            self.upsert(item['name'], item['kind'], item['server'], item['port'], item['tls'],
                        item['sni'], item['host'], item['source'], item['metadata'])
            self.update(item['name'], {'enabled': 1, 'latency_ms': item.get('latency_ms')})
            keep.add(item['name'])
            created += 1
        # A location the admin deleted takes its nodes with it.
        edge_sources.cleanup_orphans(keep)
        return created


class NodeProbe:
    """Real connectivity measurement for one node or the whole catalog."""

    def __init__(self, catalog):
        self.catalog = catalog

    @staticmethod
    def metadata(node):
        return edge_sources.parse_metadata(node.get('metadata'))

    @staticmethod
    async def _tls_once(host, port, timeout, server_hostname, verify):
        """One TLS handshake attempt: ``(ms, error)``."""
        started = time.perf_counter()
        try:
            ctx = ssl.create_default_context()
            if not verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ctx, server_hostname=server_hostname or host),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return round((time.perf_counter() - started) * 1000, 1), None
        except Exception as exc:
            return None, type(exc).__name__

    @classmethod
    async def handshake(cls, host, port, timeout=4.0, server_hostname=None):
        """The handshake a client performs, and whether the certificate verified.

        Verification is tried first and then the same handshake without it: plenty
        of honest setups complete the handshake while the certificate proves
        nothing about this deployment (a clean IP dialled with a Host/SNI whose
        certificate is issued for another name, any Reality-style endpoint). The
        *handshake* is what decides whether a client can connect, so it is what
        decides health; whether the certificate verified is reported next to it.
        """
        ms, err = await cls._tls_once(host, port, timeout, server_hostname, True)
        if ms is not None:
            return ms, None, True
        plain_ms, plain_err = await cls._tls_once(host, port, timeout, server_hostname, False)
        if plain_ms is not None:
            return plain_ms, None, False
        return None, err or plain_err, False

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

    @staticmethod
    def _tls_hint(error, tcp_ok, target):
        """A sentence an admin can act on when the handshake failed."""
        if 'gai' in str(error).lower() or 'dns' in str(error).lower():
            return f'نام «{target}» حل نشد (DNS)'
        if not tcp_ok:
            return 'پورت بسته است یا مسیر شبکه به این آی‌پی باز نیست'
        return (f'TLS با Host/SNI «{target}» پاسخ نداد — این آی‌پی این دامنه را سرو نمی‌کند '
                'یا روی این پورت TLS نیست')

    async def ping(self, node, timeout=4.0):
        """Probe one node the way a client does, then persist the verdict.

        For a TLS node health means a **completed handshake with the node's own
        Host/SNI** — the very check a client runs when it pings the entry. A bare
        TCP connect is still measured, but it is reported as ``tcp_ok`` instead of
        counting as health: a location whose Host/SNI is not served by its
        addresses must show up as broken here rather than as a healthy entry that
        every client times out on.
        """
        name = str(node.get('name') or '')
        kind = str(node.get('kind') or 'railway')
        server = str(node.get('server') or '').strip()
        port = int(node.get('port') or 443)
        host = str(node.get('host') or node.get('sni') or '').strip()
        target = host or server
        tls = bool(node.get('tls', 1))
        ms, err, tls_ok, verified = None, 'no server address', False, False
        tcp_ok, tcp_ms = False, None
        if server:
            if tls:
                ms, err, verified = await self.handshake(server, port, timeout=timeout,
                                                         server_hostname=host or server)
                tls_ok = ms is not None
                if ms is None:
                    # A location can answer on the port without serving TLS at all
                    # (a plain relay, a closed 443 that only accepts SYN/ACK on an
                    # anycast edge). Recording that separately is what turns
                    # "ناموفق" into an explanation.
                    tcp_ms, tcp_err = await self.tcp(server, port, timeout=timeout)
                    tcp_ok = tcp_ms is not None
                    err = err or tcp_err
            else:
                tcp_ms, err = await self.tcp(server, port, timeout=timeout)
                tcp_ok = tcp_ms is not None
                ms = tcp_ms
        now = int(time.time())
        ok = ms is not None
        hint = '' if ok else self._tls_hint(err, tcp_ok, target)
        meta = self.metadata(node)
        meta.update({'ping_ok': ok, 'ping_tls': tls_ok, 'ping_verified': verified,
                     'ping_tcp': tcp_ok, 'ping_tcp_ms': tcp_ms, 'ping_ms': ms, 'ping_at': now,
                     'ping_error': err, 'ping_hint': hint, 'last_probe': now,
                     'probe_target': f'{server}:{port}'})
        try:
            self.catalog.mark(name, ms if ok else FAILED_LATENCY, meta)
        except Exception:
            pass
        return {'name': name, 'kind': kind, 'server': server, 'port': port, 'ok': ok,
                'tls_ok': tls_ok, 'tls_verified': verified, 'tcp_ok': tcp_ok,
                'latency_ms': ms, 'tcp_latency_ms': tcp_ms, 'error': err, 'hint': hint, 'at': now}

    async def ping_all(self, names=None, timeout=4.0, concurrency=None):
        """Probe every enabled node: Cloudflare clean IPs and the Railway origin."""
        items = self.catalog.enabled()
        if names:
            wanted = {str(n).strip().lower() for n in names if str(n).strip()}
            items = [n for n in items if str(n['name']).lower() in wanted]
        from app.config import settings as live_settings
        sem = asyncio.Semaphore(max(1, min(int(concurrency or live_settings.cf_probe_concurrency), 16)))

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
                # ``NEXUS_OUTBOUND_PROBE_ENABLED=0`` turns every external probe off
                # (latencies freeze at their last value) for a host that does not
                # want any outbound measurement traffic.
                from app.config import settings as live_settings
                if live_settings.outbound_probe_enabled and self.catalog.enabled():
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


def sync_from_sources(public_base, worker_url=None, edge_host=None):
    return catalog.sync(public_base, worker_url, edge_host)


async def detect_edge_host(public_base=None, force=False):
    return await catalog.detect_edge(public_base, force=force)


async def auto_sync(public_base=None, worker_url=None, force_edge=False):
    """One call the routes use: detect the Cloudflare edge, then rebuild.

    This is what makes the Node Catalog fully automatic — even on a deployment
    where the admin never configured a Worker, a Cloudflare-fronted domain
    produces the clean-IP nodes on the first panel open.
    """
    edge = NodeCatalog.edge_cache.get('host')
    if worker_url:
        edge = None  # the Worker host wins; no detection handshake needed
    elif edge is None or force_edge:
        edge = await detect_edge_host(public_base, force=force_edge)
    count = catalog.sync(public_base, worker_url, edge)
    return {'synced': count, 'edge_host': edge, 'nodes': catalog.list()}


def ensure_origin_node(public_base=None):
    return catalog.ensure_origin(public_base)


async def ping_node(node, timeout=4.0):
    return await probe.ping(node, timeout=timeout)


async def ping_all(names=None, timeout=4.0, concurrency=8):
    return await probe.ping_all(names=names, timeout=timeout, concurrency=concurrency)


async def ping_loop(interval=900, interval_provider=None):
    return await probe.loop(interval=interval, interval_provider=interval_provider)
