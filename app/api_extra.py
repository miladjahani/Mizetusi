"""Extra admin endpoints: customization, Hysteria2, location packs and tools.

These live outside ``app/main.py`` on purpose. That module owns the core routing
(auth, users, subscriptions, the WebSocket edge), while everything added here is a
self-contained capability an admin drives from one of the newer panel tabs:

* ``/api/customization`` — how the *end user* sees the product (banner, support
  link, country flags, the default subscription shape);
* ``/api/hysteria`` — the external Hysteria2 node (Hysteria2 is QUIC and has no
  Xray inbound, so this is an endpoint the admin owns, published only once it is
  saved and enabled);
* ``/api/edge/packs`` and ``/api/edge/import`` — multi-location: a ready-made
  pack of country locations, or one built from any subscription URL;
* ``/api/tools/*`` — the network tools tab: TCP/TLS reachability, DoH lookup,
  CIDR maths and a subscription parser.

The router authenticates through the panel's own session, resolved lazily so this
module can be imported by ``app.main`` without a circular import.
"""
import ipaddress
import re
import time
import urllib.parse

from fastapi import APIRouter, HTTPException, Request

from app.core import clientip
from app.cores import service as core_service
from app.core.settings_store import store
from app.db import execute
from app.edge import packs as edge_packs
from app.edge import sources as edge_sources
from app.subscriptions import transports
from app.subscriptions import scope as node_scope
from app.subscriptions import flags as sub_flags

router = APIRouter()

CORE_FORMATS = ('auto', 'base64', 'singbox', 'clash')
H2_OBFS = ('', 'salamander')


def _main():
    """The application module, resolved at call time (no import cycle)."""
    from app import main
    return main


def _auth(request):
    _main().auth(request)


def _setting(key):
    return store.get(key)


def _set(key, value):
    return store.set(key, value)


def _audit(action, detail=''):
    _main()._audit(action, detail)


def _flags_on():
    return (_setting('flags_enabled') or '1') != '0'


async def _measure(request, source_ids):
    """Sync and ping the locations a pack install / import just created.

    A location that nobody measured is published unverified and reads as «پینگ
    نمی‌دهد»; measuring here means the counts come back with the same answer the
    admin would get from the per-location ping button.
    """
    ids = [str(item) for item in (source_ids or []) if item]
    if not ids:
        return {'probed': 0, 'healthy': 0, 'failed': 0, 'locations': 0}
    main = _main()
    # A pack/import writes locations from ranges and remarks, so their countries
    # are guesses until the addresses themselves are measured — measure them
    # before publishing, then correct any label the data contradicts (a Canadian
    # flag on an American address is what users see otherwise).
    geo_result = await main._edge_geo(limit=16)
    main._edge_sync(request)
    totals = {'probed': 0, 'healthy': 0, 'failed': 0, 'locations': len(ids),
              'geo': geo_result, 'results': []}
    for source_id in ids:
        result = await main._ping_edge_nodes(source_id=source_id, timeout=2.5, limit=40)
        totals['probed'] += result['probed']
        totals['healthy'] += result['healthy']
        totals['failed'] += result['failed']
        totals['results'].extend(result['results'])
    return totals


# --------------------------------------------------------------- customization
def _customization():
    brand = _main()._brand()
    return {
        'app_name': brand['app_name'], 'accent': brand['accent'],
        'accent_secondary': brand['accent_secondary'],
        'portal_banner': _setting('portal_banner') or '',
        'support_url': _setting('support_url') or '',
        'flags': _flags_on(),
        # Whether a location's country is measured from its addresses (and its
        # label corrected when the data disagrees).
        'geo_lookup': (_setting('geo_lookup') or '1') != '0',
        'default_format': (_setting('default_format') or 'auto').strip().lower(),
        'default_max_configs': _setting('default_max_configs') or '',
        'default_scope': node_scope.normalize(_setting('default_scope') or 'all'),
        'scopes': node_scope.options(_main().active_nodes()),
        'core_formats': [{'id': item['id'], 'label': item['label'], 'hint': item['hint']}
                         for item in _main()._core_subs('', '')],
    }


@router.get('/api/customization')
def get_customization(request: Request):
    _auth(request)
    return _customization()


@router.post('/api/customization')
async def save_customization(request: Request):
    """Validate and store everything that changes the end-user experience."""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, 'invalid payload')
    changed = []
    if 'portal_banner' in body:
        _set('portal_banner', str(body['portal_banner'] or '').strip()[:200])
        changed.append('portal_banner')
    if 'support_url' in body:
        url = str(body['support_url'] or '').strip()
        if url and not (url.startswith(('http://', 'https://')) and urllib.parse.urlparse(url).hostname):
            raise HTTPException(400, 'support_url باید یک آدرس http(s) معتبر باشد')
        _set('support_url', url)
        changed.append('support_url')
    if 'flags_enabled' in body:
        value = str(body['flags_enabled']).strip().lower()
        _set('flags_enabled', '0' if value in ('0', 'false', 'off', 'no') else '1')
        changed.append('flags_enabled')
    if 'geo_lookup' in body:
        value = str(body['geo_lookup']).strip().lower()
        _set('geo_lookup', '0' if value in ('0', 'false', 'off', 'no') else '1')
        changed.append('geo_lookup')
    if 'default_format' in body:
        value = str(body['default_format'] or 'auto').strip().lower()
        if value not in CORE_FORMATS:
            raise HTTPException(400, 'default_format باید یکی از ' + ', '.join(CORE_FORMATS) + ' باشد')
        _set('default_format', value)
        changed.append('default_format')
    if 'default_max_configs' in body:
        raw = str(body['default_max_configs'] or '').strip()
        if raw:
            try:
                number = int(float(raw))
            except ValueError:
                raise HTTPException(400, 'default_max_configs باید عدد باشد')
            if not 1 <= number <= 500:
                raise HTTPException(400, 'default_max_configs باید بین ۱ و ۵۰۰ باشد')
            _set('default_max_configs', str(number))
        else:
            _set('default_max_configs', '')
        changed.append('default_max_configs')
    if 'default_scope' in body:
        # The node scope a quick-created user gets when the admin does not pick
        # one on the spot: all / multi-location only / this server only / country.
        _set('default_scope', node_scope.normalize(body['default_scope']))
        changed.append('default_scope')
    if 'app_name' in body:
        _set('app_name', str(body['app_name'] or '').strip()[:24])
        changed.append('app_name')
    for key in ('accent', 'accent_secondary'):
        if key in body:
            value = str(body[key] or '').strip()
            if value and not re.match(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$', value):
                raise HTTPException(400, f'{key} باید رنگ hex باشد، مثل #5ad1ff')
            _set(key, value)
            changed.append(key)
    if changed:
        _audit('customization.update', ','.join(changed))
    return {'success': True, 'changed': changed, 'customization': _customization()}


# ------------------------------------------------------------------ hysteria2
def _hysteria_payload():
    return transports.catalog()['hysteria2']


@router.get('/api/hysteria')
def get_hysteria(request: Request):
    _auth(request)
    return {'hysteria': _hysteria_payload(), 'profile': transports.HY2_PROFILE,
            'obfs_types': [item for item in H2_OBFS if item]}


@router.post('/api/hysteria')
async def save_hysteria(request: Request):
    """Save, enable or disable the external Hysteria2 endpoint.

    Nothing about Hysteria2 is published until a host and a password are stored
    *and* it is enabled: a QUIC node that silently dead-ends is worse than no
    node, and Xray cannot serve this protocol for us.
    """
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, 'invalid payload')
    action = str(body.get('action') or 'save').strip().lower()
    if action == 'disable':
        _set('hy2_enabled', '0')
        _audit('hysteria.disable', 'hysteria2 node')
        return {'success': True, 'hysteria': _hysteria_payload()}
    if action == 'clear':
        for key in ('hy2_enabled', 'hy2_host', 'hy2_port', 'hy2_password', 'hy2_sni',
                    'hy2_obfs', 'hy2_obfs_password', 'hy2_insecure', 'hy2_label'):
            _set(key, '')
        _audit('hysteria.clear', 'hysteria2 node removed')
        return {'success': True, 'hysteria': _hysteria_payload()}
    host = str(body.get('host') or _setting('hy2_host') or '').strip()
    password = str(body.get('password') or '').strip() or (_setting('hy2_password') or '')
    if not host or '.' not in host:
        raise HTTPException(400, 'هاست هاستریزیا۲ معتبر نیست (مثال: hy2.example.com)')
    try:
        port = int(body.get('port') or _setting('hy2_port') or 443)
    except (TypeError, ValueError):
        raise HTTPException(400, 'پورت معتبر نیست')
    if not 1 <= port <= 65535:
        raise HTTPException(400, 'پورت باید بین ۱ و ۶۵۵۳۵ باشد')
    if not password:
        raise HTTPException(400, 'رمز/مسیر هاستریزیا۲ لازم است')
    obfs = str(body.get('obfs') or _setting('hy2_obfs') or '').strip().lower()
    if obfs and obfs not in H2_OBFS:
        raise HTTPException(400, 'نوع obfs پشتیبانی نمی‌شود (salamander)')
    _set('hy2_host', host)
    _set('hy2_port', str(port))
    _set('hy2_password', password)
    _set('hy2_sni', str(body.get('sni') or _setting('hy2_sni') or '').strip())
    _set('hy2_obfs', obfs)
    _set('hy2_obfs_password', str(body.get('obfs_password') or _setting('hy2_obfs_password') or '').strip())
    _set('hy2_insecure', '1' if str(body.get('insecure') or '').strip() in ('1', 'true', 'on') else '0')
    _set('hy2_label', str(body.get('label') or _setting('hy2_label') or '').strip()[:32])
    enabled = body.get('enabled')
    if enabled is None:
        _set('hy2_enabled', '1')
    else:
        _set('hy2_enabled', '1' if str(enabled).strip().lower() in ('1', 'true', 'on', 'yes') else '0')
    _audit('hysteria.save', f'{host}:{port}')
    return {'success': True, 'hysteria': _hysteria_payload()}


# -------------------------------------------------------------- location packs
@router.get('/api/edge/packs')
def get_packs(request: Request):
    _auth(request)
    return {'packs': edge_packs.catalog(), 'sources': edge_sources.status()['sources'],
            'locations': sorted({item.get('location') for item in edge_sources.sources() if item.get('location')})}


@router.post('/api/edge/packs')
async def manage_pack(request: Request):
    """Install or remove a ready-made multi-location pack."""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    pack_id = str(body.get('id') or '').strip().lower()
    action = str(body.get('action') or 'install').strip().lower()
    if not edge_packs.pack(pack_id):
        raise HTTPException(400, 'بستهٔ لوکیشن ناشناخته است')
    if action == 'uninstall':
        removed = edge_packs.uninstall(pack_id)
        _audit('edge.pack', f'uninstall {pack_id} ({len(removed)})')
        return {'success': True, 'removed': removed, 'packs': edge_packs.catalog(),
                'sources': edge_sources.status()['sources']}
    hosts = body.get('hosts')
    if isinstance(hosts, str):
        hosts = [token for token in re.split(r'[\s,]+', hosts) if token]
    # A clean-IP pack (the Cloudflare regions) fronts the origin that already
    # answers on the Worker URL or the panel's own domain, so the admin never has
    # to retype it.
    default_host = _setting('cloudflare_worker_url') or ''
    if not default_host:
        default_host = _main().public_base(request)
    try:
        created, _sources = edge_packs.install(pack_id, override_hosts=hosts or None,
                                               default_host=default_host)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    ping = await _measure(request, [item['id'] for item in created])
    _audit('edge.pack', f'install {pack_id} ({len(created)})')
    return {'success': True, 'created': [item['id'] for item in created],
            'sources': edge_sources.status()['sources'], 'ping': ping,
            'packs': edge_packs.catalog()}


@router.get('/api/edge/import')
def get_import_hint(request: Request):
    """What the importer would do, described for the panel (no network call)."""
    _auth(request)
    return {'packs': edge_packs.catalog(),
            'hint': 'آدرس یک سابلینک (یا متن کانفیگ‌ها) را وارد کنید؛ هر هاست با پرچم کشورش '
                    'به یک لوکیشن تبدیل می‌شود و کل ماتریس پروتکل‌ها روی آن منتشر می‌شود.'}


@router.post('/api/edge/import')
async def import_subscription(request: Request):
    """Turn any subscription (URL or pasted links) into edge locations."""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    provider = str(body.get('provider') or edge_sources.DEFAULT_PROVIDER).strip().lower()
    if not edge_sources.provider(provider) or provider == 'domain':
        provider = edge_sources.DEFAULT_PROVIDER
    try:
        max_nodes = int(body.get('max_nodes') or 3)
    except (TypeError, ValueError):
        max_nodes = 3
    max_nodes = max(1, min(max_nodes, edge_sources.MAX_SOURCE_NODES))
    port = body.get('port')
    try:
        port = int(port) if port not in (None, '') else None
    except (TypeError, ValueError):
        port = None
    apply_ = bool(body.get('apply'))
    text = str(body.get('text') or '').strip()
    url = str(body.get('url') or '').strip()
    try:
        if url:
            result = edge_packs.import_url(url, provider=provider, max_nodes=max_nodes,
                                           port_override=port, apply=apply_)
        elif text:
            result = edge_packs.import_plan(text, provider=provider, max_nodes=max_nodes,
                                            port_override=port, apply=apply_)
        else:
            raise ValueError('آدرس سابلینک یا متن کانفیگ‌ها را وارد کنید')
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if apply_:
        _audit('edge.import', f"{result.get('url') or 'text'} → {result['found']} locations")
    result['success'] = True
    result['ping'] = await _measure(request, [item['id'] for item in result.get('created') or []])
    result['packs'] = edge_packs.catalog()
    result['sources'] = edge_sources.status()['sources']
    return result


# ------------------------------------------------------------------- net tools
@router.post('/api/tools/check')
async def tools_check(request: Request):
    """Dial one host:port (with a real TLS handshake when asked)."""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    host = str(body.get('host') or '').strip()
    if not host:
        raise HTTPException(400, 'هاست لازم است')
    if '://' in host:
        host = urllib.parse.urlparse(host).hostname or ''
    try:
        port = int(body.get('port') or 443)
    except (TypeError, ValueError):
        port = 443
    if not 1 <= port <= 65535:
        raise HTTPException(400, 'پورت معتبر نیست')
    try:
        timeout = min(max(float(body.get('timeout') or 4), 1), 15)
    except (TypeError, ValueError):
        timeout = 4
    from app.nodes import NodeProbe
    results = {}
    started = time.time()
    if body.get('tls'):
        # The same handshake the client node performs, so this answers the exact
        # question a location raises: does this IP serve that Host/SNI?
        ms, err, verified = await NodeProbe.handshake(
            host, port, timeout=timeout, server_hostname=str(body.get('sni') or '').strip() or host)
        results['tls'] = {'ok': ms is not None, 'latency_ms': ms, 'error': err, 'verified': verified}
    else:
        ms, err = await NodeProbe.tcp(host, port, timeout=timeout)
        results['tcp'] = {'ok': ms is not None, 'latency_ms': ms, 'error': err}
    if body.get('tls'):
        plain_ms, plain_err = await NodeProbe.tcp(host, port, timeout=timeout)
        results['tcp'] = {'ok': plain_ms is not None, 'latency_ms': plain_ms, 'error': plain_err}
    return {'success': True, 'host': host, 'port': port, 'results': results,
            'elapsed_ms': round((time.time() - started) * 1000, 1)}


@router.post('/api/tools/parse')
async def tools_parse(request: Request):
    """Parse a subscription body (or a single link) into its entries."""
    _auth(request)
    body = await _json_body(request)
    text = str(body.get('text') or '').strip()
    if not text:
        raise HTTPException(400, 'متن سابلینک لازم است')
    lines = edge_packs.decode_subscription(text)
    entries = edge_packs.entries(text)
    return {'success': True, 'lines': len(lines), 'count': len(entries),
            'protocols': sorted({item['scheme'] for item in entries}),
            'locations': sorted({item['location'] for item in entries if item.get('location')}),
            'entries': entries,
            'preview': edge_packs.plan(text)}


@router.post('/api/tools/cidr')
async def tools_cidr(request: Request):
    """Summarise a list of IPs/CIDRs and show the addresses a scan would use."""
    _auth(request)
    body = await _json_body(request)
    raw = body.get('value')
    if isinstance(raw, (list, tuple)):
        text = ' '.join(str(item) for item in raw)
    else:
        text = str(raw or '')
    tokens = [token for token in re.split(r'[\s,;]+', text) if token]
    if not tokens:
        raise HTTPException(400, 'مقدار CIDR یا آی‌پی لازم است')
    try:
        limit = max(1, min(int(body.get('limit') or 16), 128))
    except (TypeError, ValueError):
        limit = 16
    valid, invalid = [], []
    for token in tokens:
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError:
            invalid.append(token)
            continue
        if network.version == 4:
            valid.append(str(network))
        else:
            invalid.append(token)
    # Nothing usable is an error the operator should see, not a silent empty answer.
    if not valid:
        raise HTTPException(400, 'هیچ آی‌پی یا CIDR معتبری پیدا نشد')
    addresses = edge_sources.sample(valid, limit=limit, per_net=4)
    total = 0
    for cidr in valid:
        try:
            total += ipaddress.ip_network(cidr).num_addresses
        except ValueError:
            continue
    return {'success': True, 'networks': valid, 'invalid': invalid,
            'addresses': addresses, 'count': len(valid), 'total_addresses': total}


@router.get('/api/tools/dns')
async def tools_dns(request: Request, name: str = ''):
    """DoH lookup (Cloudflare/Google) so a blocked local resolver is bypassed."""
    _auth(request)
    target = str(name or '').strip()
    if not target:
        raise HTTPException(400, 'دامنه لازم است')
    from app.dns.service import doh
    try:
        return {'success': True, 'name': target, 'answer': await doh(target)}
    except Exception as exc:
        raise HTTPException(400, f'جست‌وجوی DNS ناموفق بود ({type(exc).__name__})')


async def _json_body(request):
    """The request body as a dict, never raising on a missing/invalid one."""
    try:
        body = await request.json()
    except Exception:
        body = None
    return body if isinstance(body, dict) else {}


# ------------------------------------------------------------------ client ip
def _trust_cdn_headers():
    return (_setting('trust_client_ip') or '1') != '0'


def _client_ip_state(request, state=None):
    """What the panel believes this request's address is, and the evidence.

    The panel renders every field — the raw peer, the forwarded chain and which
    rule won — because a trust decision nobody can inspect is a trust decision
    nobody can fix. The default trusted ranges are listed too, so it is obvious
    that a same-host nginx or the platform's own proxy is already covered.
    """
    extra = _setting('trusted_proxy_cidrs') or ''
    resolved = state or clientip.resolve(request, extra_trusted=extra,
                                         trust_headers=_trust_cdn_headers())
    return {
        **resolved,
        'trust_cdn_headers': _trust_cdn_headers(),
        'trusted_proxy_cidrs': extra,
        'default_trusted': list(clientip.DEFAULT_TRUSTED),
        'cloudflare_ranges': len(clientip.CLOUDFLARE),
    }


@router.get('/api/net/client-ip')
def get_client_ip(request: Request):
    """The address behind the proxies, the way nginx ``real_ip`` resolves it."""
    _auth(request)
    return {'success': True, **_client_ip_state(request)}


@router.post('/api/net/client-ip')
async def save_client_ip(request: Request):
    """Store the trusted-proxy rule (the panel's ``set_real_ip_from``).

    An unusable CIDR is a 400 instead of a silently dropped token: an operator
    who typed a range has to know whether it took effect.
    """
    _auth(request)
    body = await _json_body(request)
    changed = []
    if 'trust_client_ip' in body:
        value = str(body['trust_client_ip']).strip().lower()
        _set('trust_client_ip', '0' if value in ('0', 'false', 'off', 'no') else '1')
        changed.append('trust_client_ip')
    if 'trusted_proxy_cidrs' in body:
        raw = str(body['trusted_proxy_cidrs'] or '').strip()
        unusable = clientip.invalid_cidrs(raw)
        if unusable:
            raise HTTPException(400, 'این مقدارها آی‌پی یا CIDR معتبر نیستند: ' + ', '.join(unusable[:6]))
        _set('trusted_proxy_cidrs', raw)
        changed.append('trusted_proxy_cidrs')
    if changed:
        _audit('settings.client_ip', ','.join(changed))
    return {'success': True, 'changed': changed, **_client_ip_state(request)}


# ---------------------------------------------------------------------- cores
def _sync_summary(result):
    """Just enough of a sync result to show on the card after saving."""
    return {engine: {'running': bool(item.get('running')), 'profiles': item.get('profiles') or [],
                     'reason': item.get('reason') or ''}
            for engine, item in (result or {}).items()}


@router.get('/api/cores')
def get_cores(request: Request):
    """The second engines: what each one serves and what is actually published."""
    _auth(request)
    return {'success': True, **core_service.catalog()}


@router.post('/api/cores')
async def save_cores(request: Request):
    """Store the switch, public port and hosting engine of every hosted protocol.

    The engines are reconciled before the answer goes out, so the panel shows the
    real result of the change (a port the host cannot expose is reported as
    withheld, not as published).
    """
    _auth(request)
    body = await _json_body(request)
    updates = dict(body.get('profiles') or {}) if isinstance(body.get('profiles'), dict) else {}
    if 'sni' in body:
        updates['sni'] = body['sni']
    try:
        changed = core_service.save(updates)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    result = await core_service.sync(force=True)
    if changed:
        _audit('cores.update', ','.join(changed))
    return {'success': True, 'changed': changed, 'sync': _sync_summary(result), **core_service.catalog()}


@router.post('/api/cores/reload')
async def reload_cores(request: Request):
    """Bring the engines to the state the settings ask for, right now."""
    _auth(request)
    result = await core_service.sync(force=True)
    _audit('cores.reload', ' '.join(f"{engine}:{'up' if item.get('running') else 'down'}"
                                    for engine, item in result.items()) or 'nothing enabled')
    return {'success': True, 'sync': _sync_summary(result), **core_service.catalog()}


# --------------------------------------------------------------------- scopes
@router.get('/api/scopes')
def get_scopes(request: Request, username: str = ''):
    """The node-scope catalog, with the live node count of every choice.

    ``?username=`` answers for one user (so the panel can show which slice that
    user publishes and hand out a ready link for any other slice).
    """
    _auth(request)
    main = _main()
    nodes = main.active_nodes()
    user = main.get_user(str(username).strip()) if username else None
    current = transports.user_scope(user) if user else node_scope.normalize(_setting('default_scope') or 'all')
    base = main.public_base(request)
    options = [{**item, 'url': main._sub_url(base, urllib.parse.quote(user['uuid'], safe=''),
                                             main._user_target(user), '', '', item['id'])}
               for item in node_scope.options(nodes, current)] if user \
        else node_scope.options(nodes, current)
    return {'username': user['username'] if user else '', 'scope': current,
            'label': node_scope.label(current), 'hint': node_scope.hint(current),
            'catalog_total': len(nodes), 'options': options,
            'locations': sorted({node_scope.location_of(n) for n in nodes if node_scope.location_of(n)})}


# ---------------------------------------------------------------- live guide
# The panel's «راهنمای زنده» answers one question — «حالا چه کار کنم؟» — and every
# step is answered from **real state** (saved settings, edge sources, Node Probe
# results, the user table), never from a checklist somebody has to keep in their
# head. A step that is already true comes back ``done``; the first false one is
# ``next``, which is what the topbar chip and the drawer highlight.

SECTION_TIPS = {
    'dashboard': {'title': 'از این‌جا شروع کنید', 'items': [
        'شمارندهٔ «نودهای سالم» تنها چیزی است که به سابلینک کاربر می‌رود؛ نود ناسالم منتشر نمی‌شود.',
        'هر عدد این صفحه زنده است؛ برای دیدن جزئیات روی همان کارت بروید.',
    ]},
    'nodes': {'title': 'نودها و لوکیشن‌ها', 'items': [
        '«Sync نودها» کاتالوگ را از منابع لبه می‌سازد، «پینگ همه نودها» سلامت واقعی را می‌سنجد.',
        'پینگ = همان handshake کلاینتی که کاربر انجام می‌دهد؛ TCP خالی هرگز «سالم» حساب نمی‌شود.',
        'اگر نودی سالم نیست، Host/SNI آن با آی‌پی‌ها سازگار نیست — دلیلش زیر نام نود نوشته شده است.',
    ]},
    'users': {'title': 'کاربران در سه کلیک', 'items': [
        'یکی از حالت‌های «ساخت سریع» را بزنید: هر کارت یک پریست + محدودهٔ نود است.',
        'محدودهٔ نودها تعیین می‌کند کاربر چه ببیند: همه نودها، فقط مولتی‌لوکیشن، فقط سرور اصلی یا یک کشور.',
        'تعداد کانفیگ را از فرم کاربر یا پیش‌فرض شخصی‌سازی بدهید تا سابلینک سبک بماند.',
    ]},
    'cloudflare': {'title': 'ورکر و آی‌پی تمیز', 'items': [
        'کد ورکر آماده است؛ آن را Deploy کنید و آدرسش را این‌جا بگذارید.',
        'هر لوکیشن دکمهٔ پینگ خودش را دارد و می‌گوید چند آی‌پی پاسخ داد.',
    ]},
    'customize': {'title': 'آن‌چه کاربر می‌بیند', 'items': [
        'بنر، لینک پشتیبانی، پرچم کشورها و فرمت پیشنهادی همین‌جا تنظیم می‌شوند.',
        'پیش‌فرض تعداد کانفیگ و محدودهٔ نود، همان چیزی است که کاربر سریع‌ساخت می‌گیرد.',
    ]},
    'tools': {'title': 'ابزار شبکه', 'items': [
        'برای هر لوکیشن مشکوک، «بررسی دسترسی» را با SNI درست اجرا کنید.',
        'تحلیل سابلینک، هاست‌های یک سابلینک دیگر را با پرچم کشور به لوکیشن تبدیل می‌کند.',
    ]},
    'advanced': {'title': 'گزینه‌های پیشرفته', 'items': [
        'بستهٔ «کلودفلر — لوکیشن‌های چندگانه» با یک کلیک ۸ منطقهٔ واقعی می‌سازد.',
        'نود Hysteria2 تا وقتی هاست و رمز ذخیره و فعال نشود هیچ‌جا منتشر نمی‌شود.',
    ]},
    'settings': {'title': 'تنظیمات و امنیت', 'items': [
        'پیشوند برچسب و آدرس پایه را یک‌بار درست کنید تا همهٔ لینک‌ها تمیز باشند.',
        'با «ابطال همه نشست‌ها» هر دستگاه دیگری از پنل بیرون می‌آید.',
    ]},
}


def _guide_state(request):
    """The guide's steps, straight from the live deployment state."""
    main = _main()
    worker = (_setting('cloudflare_worker_url') or '').strip()
    sources = [item for item in (edge_sources.status().get('sources') or [])]
    locations = sorted({str(item.get('location') or '') for item in sources if item.get('location')})
    nodes = list(main.list_nodes())
    enabled = [n for n in nodes if n.get('enabled')]
    healthy = [n for n in enabled
               if n.get('latency_ms') is not None and float(n.get('latency_ms') or 0) >= 0]
    users = list(main.list_users())
    active = [u for u in users if u.get('is_active')]
    scoped = [u for u in users if transports.user_scope(u) != node_scope.SCOPE_ALL]
    brand = main._brand()
    base = main.public_base(request)
    newest = active[0] if active else (users[0] if users else None)

    steps = [
        {'id': 'worker', 'title': 'آدرس Worker یا دامنهٔ کلودفلر', 'section': 'cloudflare',
         'hint': 'بدون این آدرس، لوکیشن‌های آی‌پی تمیز Host/SNI ندارند و پینگ نمی‌دهند.',
         'detail': worker or 'ثبت نشده', 'done': bool(worker)},
        {'id': 'locations', 'title': 'لوکیشن‌های لبه (مولتی‌لوکیشن)', 'section': 'advanced',
         'hint': 'از تب پیشرفته یک بستهٔ لوکیشن نصب کنید یا از سابلینک وارد کنید.',
         'detail': f'{len(sources)} منبع · {len(locations)} لوکیشن', 'done': bool(sources)},
        {'id': 'nodes', 'title': 'کاتالوگ نود فعال', 'section': 'nodes',
         'hint': '«Sync نودها» نودهای هر لوکیشن را می‌سازد؛ سپس آن‌ها را پینگ کنید.',
         'detail': f'{len(enabled)} از {len(nodes)} نود فعال', 'done': bool(enabled)},
        {'id': 'ping', 'title': 'پینگ واقعی نودها', 'section': 'nodes',
         'hint': 'تا نودی اندازه‌گیری نشود، «اندازه‌گیری‌نشده» می‌ماند و در سابلینک مطمئن نیست.',
         'detail': f'{len(healthy)} نود پاسخ داد', 'done': bool(healthy)},
        {'id': 'users', 'title': 'اولین کاربر', 'section': 'users',
         'hint': 'با «ساخت سریع» یک کاربر آماده بگیرید؛ لینک‌ها در همان لحظه ساخته می‌شوند.',
         'detail': f'{len(users)} کاربر ({len(active)} فعال)', 'done': bool(users)},
        {'id': 'scope', 'title': 'محدودهٔ نودهای هر کاربر', 'section': 'users',
         'hint': 'اگر کاربری نباید همهٔ لوکیشن‌ها را ببیند، محدوده‌اش را روی «فقط لبه»، «فقط سرور» یا یک کشور بگذارید.',
         'detail': f'{len(scoped)} کاربر محدود‌شده' if scoped else 'همه روی «همه نودها»',
         'done': bool(scoped)},
        {'id': 'share', 'title': 'تحویل لینک به کاربر', 'section': 'users',
         'hint': 'سابلینک هوشمند را بدهید یا پنجرهٔ وضعیت را برای کاربر باز کنید.',
         'detail': (f"{newest['username']} · {node_scope.label(transports.user_scope(newest))}" if newest else 'کاربری نیست'),
         'done': bool(active)},
        {'id': 'brand', 'title': 'برند و پنجرهٔ وضعیت', 'section': 'customize',
         'hint': 'نام برنامه، بنر و لینک پشتیبانی را تنظیم کنید تا کاربر بداند کجاست.',
         'detail': brand.get('app_name') or 'NEXUS',
         'done': bool((brand.get('app_name') or '') not in ('', 'NEXUS')
                     or (_setting('portal_banner') or '').strip()
                     or (_setting('support_url') or '').strip())},
    ]
    links = {}
    if newest:
        token = urllib.parse.quote(newest['uuid'], safe='')
        links = {'smart': main._sub_url(base, token, main._user_target(newest)),
                 'portal': main._portal_url(base, token), 'username': newest['username']}
    return {'steps': steps, 'links': links, 'tips': SECTION_TIPS,
            # The channel an admin (or an end user asking for a renewal) should
            # reach: what the admin configured, else the built-in channel.
            'support': brand.get('support_url') or '',
            'score': round(100 * sum(1 for step in steps if step['done']) / len(steps)),
            'next': next((step['id'] for step in steps if not step['done']), ''),
            'catalog_total': len(nodes), 'locations': locations}


@router.get('/api/guide')
def get_guide(request: Request, section: str = ''):
    """The live setup guide: what is done, what is next, and the tab's own tips."""
    _auth(request)
    state = _guide_state(request)
    wanted = str(section or '').strip().lower()
    state['section'] = wanted if wanted in SECTION_TIPS else 'dashboard'
    state['tip'] = SECTION_TIPS[state['section']]
    return state
