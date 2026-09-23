"""NEXUS Xray-core supervisor.

Xray is the protocol engine; FastAPI remains the public HTTPS/WebSocket edge on
Railway. The public edge terminates TLS, then bridges WebSocket streams to the
local Xray listeners, so one HTTP service/port can serve **every** published
transport:

* VLESS, VMess, Trojan and Shadowsocks-2022 over WebSocket — live by default,
  on two path shapes each (a plain ``/ws/...`` path and a CDN-looking
  ``/cdn/...`` path) so a blocked path never takes the whole service down;
* a WARP node that really exits through Cloudflare's WireGuard network, added
  automatically once a WARP peer is registered;
* Reality + gRPC/HTTPUpgrade/H2/XHTTP, built only when a raw TCP endpoint exists
  (Railway TCP proxy or a custom ``host:port``) — those transports cannot ride
  an HTTPS-terminating edge, and a link is never published for a listener that
  does not exist.

The config is assembled from :mod:`app.subscriptions.transports`, which is also
what the subscription generator and the panel read, so a transport is defined
exactly once.
"""
import asyncio, hashlib, json, os, re, secrets, subprocess
from app.config import settings
from app.db import rows, row, execute
from app.subscriptions import transports as tp

_proc = None
_last_hash = None
_last_warning = ''
# Tags of the inbounds the last successful (re)start really contains. ``None``
# means "nothing was started in this process yet", which is what the Telegram
# web-proxy card reads to tell «configured» from «served».
_last_served = None


# ------------------------------------------------------------------- credentials
def _active_users():
    return rows("SELECT username,uuid,protocol,is_active FROM users WHERE is_active=1")


def _email(user):
    return str(user.get('username') or 'user') + '@nexus.local'


def _users_for(protocol):
    """Active users whose enabled protocol set contains ``protocol``.

    A user is one credential (UUID/password) across **every** protocol, so by
    default they are registered on all of the inbounds and all of their links
    work. Only an explicit multi-protocol restriction in the panel narrows this;
    the legacy single-value rows keep meaning "all protocols".
    """
    return [u for u in _active_users() if protocol in tp.user_protocols(u)]


def _clients_for(protocol, profile=None):
    """Xray client entries for one protocol, one entry per active user.

    Shadowsocks is the exception: its listener authenticates a single PSK (see
    :data:`tp.SS_CIPHERS`), so it has no client list at all. Everything else gets
    one credential per active user.
    """
    if protocol == 'ss':
        return []
    users = _users_for(protocol)
    if protocol == 'vless':
        return [{'id': u['uuid'], 'email': _email(u), 'level': 0, 'flow': ''} for u in users]
    if protocol == 'vmess':
        return [{'id': u['uuid'], 'email': _email(u), 'level': 0, 'alterId': 0} for u in users]
    if protocol == 'trojan':
        return [{'password': u['uuid'], 'email': _email(u), 'level': 0} for u in users]
    return []


def _protocol_settings(profile):
    """The ``settings`` block of one inbound (clients + protocol extras)."""
    protocol = profile['protocol']
    if protocol == 'ss':
        # Single-user Shadowsocks: one PSK per cipher, of exactly the cipher's
        # key length. Multi-user 2022 inbounds are rejected by Xray for every
        # method but blake3-aes-*-gcm, and *one* rejected inbound used to take
        # the whole engine down - which is why no node answered at all.
        return {'method': profile.get('method') or tp.SS_METHOD,
                'password': tp.ss_shared_key(profile), 'network': 'tcp'}
    clients = _clients_for(protocol, profile)
    if protocol == 'vless':
        return {'clients': clients, 'decryption': 'none'}
    if protocol == 'vmess':
        return {'clients': clients}
    if protocol == 'trojan':
        return {'clients': clients}
    return {'clients': clients}


def _transport_block(profile):
    """``streamSettings`` for a profile's inbound listener.

    The profile's ``security`` describes what the *client* does, and for the edge
    profiles that is TLS terminated by Railway/Cloudflare — the listener itself is
    always plaintext. Only a direct (Reality) inbound terminates TLS itself.
    """
    network = profile.get('network') or 'tcp'
    security = 'reality' if profile.get('security') == 'reality' else 'none'
    stream = {'network': network, 'security': security}
    path = profile.get('path') or ''
    if network == 'ws':
        stream['wsSettings'] = {'path': path}
    elif network == 'grpc':
        stream['grpcSettings'] = {'serviceName': path.lstrip('/')}
    elif network == 'httpupgrade':
        stream['httpupgradeSettings'] = {'path': path}
    elif network == 'xhttp':
        stream['xhttpSettings'] = {'path': path, 'mode': 'auto'}
    return stream


# --------------------------------------------------------------------- inbounds
def _inbound(profile, port, listen='127.0.0.1'):
    return {
        'tag': profile['id'], 'listen': listen, 'port': int(port),
        'protocol': {'ss': 'shadowsocks'}.get(profile['protocol'], profile['protocol']),
        'settings': _protocol_settings(profile),
        'streamSettings': _transport_block(profile),
    }


def edge_inbounds(profiles=None):
    """One inbound per WebSocket profile; these ride the FastAPI bridge."""
    items = []
    for profile in (tp.EDGE_PROFILES if profiles is None else profiles):
        port = tp.profile_port(profile)
        if port:
            items.append(_inbound(profile, port))
    if tp.warp_config() and tp.profile_port(tp.WARP_PROFILE):
        items.append(_inbound(tp.WARP_PROFILE, tp.profile_port(tp.WARP_PROFILE)))
    return items


def telegram_inbounds():
    """The Telegram web proxies (HTTP/SOCKS5) the admin switched on.

    They ride *this* engine on purpose: Xray serves both inbound types with real
    accounts, so the web proxies need no second binary, share the supervisor's
    candidate ladder, and are revoked with the user they authenticate. The import
    is deferred because the Telegram package reads this module back.
    """
    from app.telegram import webproxy
    return webproxy.xray_inbounds()


def served_tags():
    """Which inbound tags the running config really has (``None`` = not known)."""
    return None if _last_served is None else list(_last_served)


def edge_profile_ids(config=None):
    """Profile tags of a config's inbounds (what the engine actually serves)."""
    cfg = config if config is not None else _config()
    return [item['tag'] for item in cfg['inbounds']]


def _reality_settings():
    keys = tp.reality_keys()
    if not keys:
        return None
    return {
        'show': False, 'dest': tp.REALITY_SNI + ':443', 'xver': 0,
        'serverNames': [tp.REALITY_SNI, 'www.apple.com', 'www.samsung.com'],
        'privateKey': keys['private_key'], 'shortIds': [keys['short_id'], ''],
    }


def direct_inbounds():
    """The Reality listener, built only when a raw TCP endpoint exists.

    Reality does its own TLS with the certificate of a real site, so the node is
    reachable as ordinary HTTPS traffic on the published TCP port.
    """
    reality = _reality_settings()
    if not reality or not tp.direct_endpoint():
        return []
    parent = _inbound(tp.DIRECT_PROFILES[0], settings.xray_reality_port, listen='0.0.0.0')
    parent['streamSettings'] = {
        'network': 'tcp', 'security': 'reality', 'realitySettings': reality, 'tcpSettings': {},
    }
    return [parent]


# -------------------------------------------------------------------- outbounds
def outbounds():
    items = [
        {'protocol': 'freedom', 'tag': 'direct', 'settings': {'domainStrategy': 'AsIs'}},
        {'protocol': 'blackhole', 'tag': 'block'},
    ]
    warp = tp.warp_config()
    if warp:
        items.append({
            'protocol': 'wireguard', 'tag': 'warp',
            'settings': {
                'secretKey': warp['secret_key'],
                'address': [str(warp.get('address') or '172.16.0.2') + '/32'],
                'peers': [{'publicKey': warp['peer_public_key'],
                           'endpoint': warp.get('endpoint') or 'engage.cloudflareclient.com:2408',
                           'allowedIPs': ['0.0.0.0/0', '::/0']}],
                'mtu': int(warp.get('mtu') or 1280),
            },
        })
    return items


def routing():
    rules = []
    if tp.warp_config():
        # Anything that arrives on the WARP inbound leaves through WARP.
        rules.append({'type': 'field', 'inboundTag': [tp.WARP_PROFILE['id']], 'outboundTag': 'warp'})
    return {'domainStrategy': 'AsIs', 'rules': rules}


def _config(edge=None, with_warp=True, with_direct=True, with_telegram=True):
    """The engine config.

    ``edge``/``with_warp``/``with_direct``/``with_telegram`` exist so a transport the
    installed Xray build refuses (or a Telegram web proxy it rejects) can be
    dropped **without** taking the whole engine down: the candidates in
    :func:`_candidate_configs` are tried richest-first.
    """
    inbounds = list(edge_inbounds() if edge is None else edge)
    if not with_warp:
        inbounds = [item for item in inbounds if item['tag'] != tp.WARP_PROFILE['id']]
    if with_telegram:
        inbounds += telegram_inbounds()
    if with_direct:
        inbounds += direct_inbounds()
    outs = [item for item in outbounds() if with_warp or item.get('tag') != 'warp']
    rules = routing()['rules'] if with_warp else []
    return {
        'log': {'loglevel': 'warning'},
        'api': {'tag': 'api', 'listen': f'127.0.0.1:{settings.xray_api_port}', 'services': ['StatsService']},
        'stats': {},
        'policy': {'levels': {'0': {'statsUserUplink': True, 'statsUserDownlink': True}},
                   'system': {'statsInboundUplink': True, 'statsInboundDownlink': True}},
        'inbounds': inbounds,
        'outbounds': outs,
        'routing': {'domainStrategy': 'AsIs', 'rules': rules},
    }


def _edge_without_ciphers(keep):
    """Edge **inbounds** keeping only the first ``keep`` Shadowsocks ciphers.

    ``keep`` counts ciphers, not profiles: each cipher owns two path shapes, so a
    rung drops 0, 2, 4 … inbounds at a time. Returns inbound dicts (not profiles),
    because that is what :func:`_config` puts on the wire.
    """
    wanted = {cipher['id'] for cipher in tp.SS_CIPHERS[:keep]}
    profiles = [profile for profile in tp.EDGE_PROFILES
                if profile['protocol'] != 'ss' or _cipher_of(profile) in wanted]
    return edge_inbounds(profiles=profiles)


def _cipher_of(profile):
    """``ss-aes256-cdn`` -> ``ss-aes256``."""
    return str(profile.get('id') or '').rsplit('-', 1)[0]


def _candidate_configs():
    """Configs to try, richest first, with the profiles each one serves.

    The ladder matters because Xray validates the whole file at once: a single
    inbound it does not like (a Shadowsocks-2022 cipher it refuses to run in the
    requested mode, a Reality/WARP build the host forbids) aborts startup and
    every other protocol stops answering. Dropping the offender keeps the rest of
    the service alive, and the dropped profiles are then withheld from the
    subscription instead of being published as dead links.
    """
    def served(config):
        return [item['tag'] for item in config['inbounds']]

    full = _config()
    yield full, served(full), ''
    trimmed = _config(edge=edge_inbounds(), with_warp=False, with_direct=False)
    yield trimmed, served(trimmed), 'WARP/Reality حذف شد'
    # A Telegram web proxy is the newest inbound here, so it is the first thing
    # dropped when the engine refuses a config it cannot see: the VPN transports
    # must never be the casualty of a Telegram listener.
    plain = _config(edge=edge_inbounds(), with_warp=False, with_direct=False, with_telegram=False)
    yield plain, served(plain), 'WARP/Reality/پروکسی تلگرام حذف شد'
    for keep in range(len(tp.SS_CIPHERS) - 1, -1, -1):
        reduced = _config(edge=_edge_without_ciphers(keep), with_warp=False, with_direct=False,
                          with_telegram=False)
        names = ', '.join(c['id'] for c in tp.SS_CIPHERS[:keep]) or 'هیچ'
        yield reduced, served(reduced), f'شادوساکس محدود به {names}'


# ------------------------------------------------------------------ reality keys
def ensure_reality_keys():
    """Generate the Reality key pair once (needs the Xray binary itself)."""
    if tp.reality_keys():
        return tp.reality_keys()
    if not os.path.exists(settings.xray_binary):
        return None
    try:
        out = subprocess.run([settings.xray_binary, 'x25519'], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception:
        return None
    private = public = None
    for line in out.splitlines():
        if line.startswith('PrivateKey:'):
            private = line.split(':', 1)[1].strip()
        elif 'PublicKey' in line:
            public = line.split(':', 1)[1].strip()
    if not (private and public):
        return None
    short_id = secrets.token_hex(8)
    for key, value in (('reality_private', private), ('reality_public', public),
                       ('reality_short_id', short_id)):
        try:
            execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (key, value))
        except Exception:
            pass
    return {'private_key': private, 'public_key': public, 'short_id': short_id}


def edge_routes():
    """WS path -> local Xray port, for the FastAPI bridge routes."""
    routes = {profile['path']: tp.profile_port(profile) for profile in tp.EDGE_PROFILES}
    routes[tp.WARP_PROFILE['path']] = int(getattr(settings, tp.WARP_PROFILE['port_setting']))
    return routes


# --------------------------------------------------------------------- lifecycle
def write_config(config=None):
    cfg = config if config is not None else _config()
    raw = json.dumps(cfg, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    os.makedirs(os.path.dirname(settings.xray_config), exist_ok=True)
    with open(settings.xray_config, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return digest, cfg


async def _test(config_path):
    test = await asyncio.create_subprocess_exec(settings.xray_binary, 'run', '-test', '-config', config_path,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    tout, terr = await test.communicate()
    if test.returncode != 0:
        return (terr or tout).decode(errors='ignore')[-1500:] or 'invalid xray config'
    return None


async def _stop():
    global _proc
    if not _proc:
        return
    if _proc.returncode is None:
        _proc.terminate()
        try:
            await asyncio.wait_for(_proc.wait(), 5)
        except asyncio.TimeoutError:
            _proc.kill(); await _proc.wait()
    _proc = None


async def start_or_reload(force=False):
    global _proc, _last_hash, _last_warning, _last_served
    if not settings.xray_enabled or not os.path.exists(settings.xray_binary):
        return {'running': False, 'reason': 'xray binary unavailable'}
    if tp.direct_endpoint():
        await asyncio.to_thread(ensure_reality_keys)
    digest = cfg = error = served = None
    note = ''
    for candidate, candidate_served, candidate_note in _candidate_configs():
        digest, cfg = write_config(candidate)
        error = await _test(settings.xray_config)
        served, note = candidate_served, candidate_note
        if not error:
            break
    if error:
        # Even the smallest config is invalid; nothing can be served, so nothing
        # may be published either.
        tp.set_served([])
        _last_served = []
        _last_warning = error
        return {'running': False, 'reason': error, 'served': []}
    # From here on, links are only generated for inbounds that really exist.
    tp.set_served(served)
    _last_served = list(served)
    _last_warning = note
    if not force and digest == _last_hash and _proc and _proc.returncode is None:
        return {'running': True, 'pid': _proc.pid, 'reloaded': False, 'warning': note or None, 'served': served}
    await _stop()
    _proc = await asyncio.create_subprocess_exec(
        settings.xray_binary, 'run', '-config', settings.xray_config,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _last_hash = digest
    await asyncio.sleep(0.25)
    if _proc.returncode is not None:
        err = (await _proc.stderr.read()).decode(errors='ignore')[-1000:]
        return {'running': False, 'reason': err or 'xray exited', 'served': served}
    return {'running': True, 'pid': _proc.pid, 'reloaded': True, 'warning': note or None, 'served': served}


async def sync_traffic_stats():
    if not _proc or _proc.returncode is not None or not os.path.exists(settings.xray_binary):
        return
    try:
        p = await asyncio.create_subprocess_exec(settings.xray_binary, 'api', 'statsquery', f'--server=127.0.0.1:{settings.xray_api_port}', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), 5)
        payload = json.loads(out.decode(errors='ignore'))
        totals = {}
        for item in payload.get('stat', []):
            m = re.match(r"user>>>(.+)>>>traffic>>>(uplink|downlink)$", str(item.get("name", "")))
            if m:
                email, direction = m.groups(); totals.setdefault(email, {})[direction] = int(item.get("value", 0))
        for u in rows("SELECT username,used_gb,limit_gb,is_active FROM users WHERE is_active=1"):
            email = u["username"] + "@nexus.local"; t = totals.get(email)
            if not t: continue
            total = int(t.get("uplink", 0)) + int(t.get("downlink", 0)); key = "xray_total:" + u["username"]
            old_row = row("SELECT value FROM settings WHERE key=?", (key,)); old = int(old_row["value"]) if old_row else total
            if total < old: old = total
            delta = total - old
            execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(total)))
            if delta > 0:
                gb = delta / 1_000_000_000
                execute("UPDATE users SET used_gb=used_gb+?, lifetime_used_gb=lifetime_used_gb+? WHERE username=?", (gb, gb, u["username"]))
                if u["limit_gb"] is not None and float(u["used_gb"]) + gb >= float(u["limit_gb"]):
                    execute("UPDATE users SET is_active=0 WHERE username=?", (u["username"],))
    except Exception:
        return


async def loop():
    while True:
        try:
            await start_or_reload()
            await sync_traffic_stats()
        except Exception: pass
        await asyncio.sleep(max(3, settings.xray_sync_interval))


def status():
    profiles = tp.available_profiles()
    served = tp.served_profiles()
    return {'enabled': bool(settings.xray_enabled), 'binary': settings.xray_binary,
            'running': bool(_proc and _proc.returncode is None),
            'pid': _proc.pid if _proc else None,
            'served': sorted(served) if served is not None else None,
            'withheld': ([p['id'] for p in tp.EDGE_PROFILES if served is not None and p['id'] not in served]
                         + ([tp.WARP_PROFILE['id']] if served is not None and tp.WARP_PROFILE['id'] not in served else [])),
            'vless_listener': settings.xray_vless_port,
            'trojan_listener': settings.xray_trojan_port,
            'vmess_listener': settings.xray_vmess_port,
            'shadowsocks_listener': settings.xray_ss_port,
            'shadowsocks_listeners': {c['id']: getattr(settings, 'xray_' + c['id'].replace('-', '_') + '_port')
                                      for c in tp.SS_CIPHERS},
            'shadowsocks_methods': [c['method'] for c in tp.SS_CIPHERS],
            'warp_listener': settings.xray_warp_port if tp.warp_config() else None,
            'reality_listener': settings.xray_reality_port if tp.direct_endpoint() else None,
            'transports': [p['id'] for p in profiles],
            'transport_tags': [p['tag'] for p in profiles],
            'warning': _last_warning or None}
