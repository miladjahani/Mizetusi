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

from app.core.settings_store import store
from app.db import execute
from app.edge import packs as edge_packs
from app.edge import sources as edge_sources
from app.subscriptions import transports
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


# --------------------------------------------------------------- customization
def _customization():
    brand = _main()._brand()
    return {
        'app_name': brand['app_name'], 'accent': brand['accent'],
        'accent_secondary': brand['accent_secondary'],
        'portal_banner': _setting('portal_banner') or '',
        'support_url': _setting('support_url') or '',
        'flags': _flags_on(),
        'default_format': (_setting('default_format') or 'auto').strip().lower(),
        'default_max_configs': _setting('default_max_configs') or '',
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
    return {'packs': edge_packs.catalog(), 'sources': edge_sources.sources(),
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
                'sources': edge_sources.sources()}
    hosts = body.get('hosts')
    if isinstance(hosts, str):
        hosts = [token for token in re.split(r'[\s,]+', hosts) if token]
    created, sources = edge_packs.install(pack_id, override_hosts=hosts or None)
    _audit('edge.pack', f'install {pack_id} ({len(created)})')
    return {'success': True, 'created': [item['id'] for item in created], 'sources': sources,
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
    result['packs'] = edge_packs.catalog()
    result['sources'] = edge_sources.sources()
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
    ms, err = await NodeProbe.tcp(host, port, timeout=timeout, tls=bool(body.get('tls')),
                                  server_hostname=str(body.get('sni') or '').strip() or host)
    results['tls' if body.get('tls') else 'tcp'] = {'ok': ms is not None, 'latency_ms': ms, 'error': err}
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
