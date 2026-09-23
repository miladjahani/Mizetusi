import asyncio, hashlib, json, os, re, secrets, time, urllib.parse
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import jwt
from app.config import SUPPORT_CHANNEL, settings
from app.db import init_db, row, rows, execute
from app.core.models import UserCreate, TrafficEvent, PROTOCOL_LIST
from app.users.service import (list_users, get_user, get_by_token, create_user, delete_user, toggle_user,
    reset_user, track, allowed, reset_due, set_metadata_value)
from app.subscriptions.generator import (render, active_nodes, node_links, normalize_target, profiles_for,
    entry_pairs, TRANSPORT_TARGETS as SUB_TRANSPORT_TARGETS, TARGETS as SUB_TARGETS)
from app.subscriptions.clients import (CLIENTS, PRESETS, FAMILIES, FORMAT_LABELS, DEFAULT_PRESET, catalog as client_catalog,
    client_links, client_groups, download_overrides, preset as get_preset, subscription_url as client_subscription_url)
from app.subscriptions import transports as transports
from app.subscriptions import scope as node_scope
from app.subscriptions import flags as sub_flags
from app import warp as warp_service
from app.proxy.manager import add as add_proxy, list_all as list_proxies, check as check_proxy, parse as parse_proxy
from app.dns.service import doh
from app.services.backup import export_all
from app.cloudflare.monitor import seed_ips, probe_all, best, loop as cf_loop
from app.nodes import (ensure as ensure_nodes, list_nodes, upsert as upsert_node, sync_from_sources,
    ping_all as ping_all_nodes, ping_loop, ensure_origin_node, catalog as node_catalog, auto_sync,
    origin_node_name)
from app.edge import sources as edge_sources
from app.edge import samples as edge_samples
from app.edge import packs as edge_packs
from app.api_extra import router as extra_router
from app import runtime
from app.core.settings_store import store
from app.core.security import SessionManager, LoginThrottle
from app.core.clientip import client_ip as resolve_client_ip
from app.cores import service as cores
from app.telegram import service as telegram_proxies
from app.telegram import webapp as telegram_web
from app.services.audit import AuditLog
from app import xray
import websockets
_started=time.time()

# ------------------------------------------------------------- object services
def _session_days():
    """Admin-configured session lifetime in days (falls back to the env default)."""
    return store.get_int('session_days',0) or max(1,int(settings.session_ttl)//86400)

sessions=SessionManager(secret_provider=lambda: store.get('jwt_secret') or settings.jwt_secret,
                        ttl_provider=lambda: _session_days()*86400,
                        default_ttl=settings.session_ttl)
throttle=LoginThrottle(limit=10,window=300)
audit=AuditLog(actor='admin')
BASE_DIR=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERAL_SETTING_KEYS=('public_base_url','sub_prefix','default_protocol','default_limit_gb','default_expiry_days','default_ip_limit','session_days','ping_interval','accent','accent_secondary','app_name',
                     # Customization (the «شخصی‌سازی» tab) and the new defaults.
                     'portal_banner','support_url','flags_enabled','default_format','default_max_configs',
                     # Whether a location's country is measured from its addresses
                     # (app/edge/geo.py) or kept as typed.
                     'geo_lookup',
                     # Node scope of a quick-created user (all / multi-location only /
                     # this server only / a country) — see app/subscriptions/scope.py.
                     'default_scope',
                     # Hysteria2 endpoint (the password is deliberately absent: it is
                     # never echoed back to the panel, only its presence is reported).
                     'hy2_enabled','hy2_host','hy2_port','hy2_sni','hy2_obfs','hy2_insecure','hy2_label',
                     # Which address the panel believes a request came from — see
                     # app/core/clientip.py and «شبکه و لبه → IP واقعی کاربر».
                     'trust_client_ip','trusted_proxy_cidrs')
# Subscription shapes the status window offers as its four main buttons. The
# matching validation lives next to the endpoints that own them
# (``app/api_extra.py``), so nothing is declared twice here.
PWA_ICONS={'192':'/static/icons/icon-192.png','512':'/static/icons/icon-512.png','maskable':'/static/icons/icon-maskable-512.png','apple':'/static/icons/apple-touch-icon.png','favicon':'/static/icons/favicon-32.png','logo':'/static/icons/nexus.svg'}
# One label per accepted target: subscription formats plus client ids.
SUB_LABELS={**FORMAT_LABELS, **{c['id']:f"{c['name']} · {c['platform']}" for c in CLIENTS}}
WORKER_PATH=os.path.join(BASE_DIR,'cloudflare-worker','worker.js')
APP_VERSION='9.2.0'

def _asset_fingerprint():
    """Content hash of the shipped front-end.

    The service worker's cache name is derived from it, so editing any asset
    automatically invalidates the cached app shell on the next visit. Without
    this an installed phone could keep painting the previous CSS/JS forever and
    no amount of "hard refresh" would help.
    """
    digest=hashlib.sha1()
    root=os.path.join(BASE_DIR,'static')
    for folder,_,files in os.walk(root):
        for name in sorted(files):
            path=os.path.join(folder,name)
            digest.update(os.path.relpath(path,root).encode('utf-8'))
            try:
                with open(path,'rb') as fh: digest.update(fh.read())
            except OSError: pass
    digest.update(APP_VERSION.encode('utf-8'))
    return digest.hexdigest()[:12]

BUILD_TOKEN=_asset_fingerprint()
REASON_LABELS={'ok':'فعال','disabled':'غیرفعال','expired':'منقضی‌شده','quota':'حجم تمام شده','requests':'سقف درخواست'}

def _setting(key):
    return store.get(key)

def _set(key,value):
    return store.set(key,value)

def _audit(action,detail=''):
    audit.record(action,detail)

def _flags_on():
    """Whether subscription labels carry the country flag (on by default)."""
    return (_setting('flags_enabled') or '1') != '0'


def _trust_cdn_headers():
    """Whether CDN/proxy headers may be believed for the client address."""
    return (_setting('trust_client_ip') or '1') != '0'


def _client_ip(request:Request):
    """The real client address of a request (app/core/clientip.py).

    Never ``request.client.host`` directly: behind Railway/Cloudflare that is the
    proxy, and the raw header is client-controlled. The login throttle and the
    audit log both key on this value.
    """
    return resolve_client_ip(request,extra_trusted=_setting('trusted_proxy_cidrs') or '',
                             trust_headers=_trust_cdn_headers())


def _brand():
    """Panel identity: also feeds the PWA manifest so the icon name matches."""
    return {'app_name':(_setting('app_name') or 'NEXUS').strip()[:24] or 'NEXUS',
            'accent':(_setting('accent') or '#5ad1ff').strip(),
            'accent_secondary':(_setting('accent_secondary') or '#8b7bff').strip(),
            # The status window is skinned from these, so a rebrand reaches the
            # end user without touching the template.
            'banner':(_setting('portal_banner') or '').strip(),
            # An admin's own support link always wins; with nothing configured the
            # built-in channel is used, so «پشتیبانی» is never a dead button.
            'support_url':(_setting('support_url') or '').strip() or SUPPORT_CHANNEL,
            'flags':_flags_on(),
            'default_format':(_setting('default_format') or 'auto').strip().lower(),
            'logo':PWA_ICONS['logo'],'icons':PWA_ICONS,'short_name':'NEXUS'}

def _ping_interval():
    """Background node-probe interval in seconds (admin configurable)."""
    minutes=store.get_int('ping_interval',0)
    return max(60,minutes*60) if minutes else max(60,settings.cf_probe_interval)

def _ensure_catalog(request:Request):
    """Self-heal the Node Catalog so a fresh deployment always publishes nodes.

    Railway injects its public domain, but a service that only ever read the
    database would publish an empty catalog (and 404 every subscription) until
    an admin pressed Sync by hand.
    """
    try:
        origin=(_setting('public_base_url') or settings.public_base_url or '').strip() or None
        node_catalog.ensure_origin(origin or public_base(request))
    except Exception:
        pass

def _sub_prefix(): return _setting('sub_prefix') or ''

def _session_secret(): return _setting('jwt_secret') or settings.jwt_secret

def _token(remember=False):
    return sessions.issue(remember)

def _valid_token(token):
    return sessions.verify(token)

def auth(request:Request):
    supplied=request.headers.get('x-admin-password')
    admin=_setting('admin_password') or settings.admin_password
    if supplied and admin and secrets.compare_digest(supplied, admin): return True
    # Cookie first, then the header fallback used by clients that cannot store
    # cookies (embedded preview panes, third-party-cookie-blocking browsers).
    if sessions.verify(sessions.presented(request)): return True
    raise HTTPException(401,'authentication required')

def _is_https(request:Request):
    proto=(request.headers.get('x-forwarded-proto') or '').split(',')[0].strip().lower()
    return (proto or request.url.scheme or 'http')=='https'

def _cross_site(request:Request):
    return (request.headers.get('sec-fetch-site') or '').strip().lower()=='cross-site'

def set_session(request:Request,response,token=None):
    """Store the admin session in an HttpOnly cookie.

    SameSite=strict would be ideal, but the panel is also rendered inside
    embedded preview panes on another origin, where a strict cookie is never
    sent back (login succeeds, then every request looks unauthenticated).
    Cross-site requests therefore get SameSite=None; Secure, which is the only
    policy browsers accept for embedded sessions.
    """
    return sessions.apply(request,response,token)
def public_base(request:Request):
    override=(_setting('public_base_url') or settings.public_base_url or '').strip()
    if override: return override.rstrip('/')
    proto=request.headers.get('x-forwarded-proto','https').split(',')[0].strip()
    host=request.headers.get('x-forwarded-host') or request.headers.get('host')
    return f'{proto}://{host}' if host else 'https://example.invalid'
async def maintenance():
    while True:
        try: reset_due()
        except Exception: pass
        await asyncio.sleep(max(15,settings.auto_reset_interval))


async def catalog_loop():
    """Keep the Node Catalog self-building in the background.

    The ping loop measures latency; this loop (every ~10 minutes, aligned with
    the edge-detection cache TTL) rebuilds the catalog so healthy clean IPs and
    the Railway origin exist without any admin action — including the very
    first minutes of a fresh Railway deployment.
    """
    while True:
        try:
            base=(_setting('public_base_url') or settings.public_base_url or '').strip() or None
            worker=(_setting('cloudflare_worker_url') or '').strip() or None
            await auto_sync(base, worker)
        except Exception:
            pass
        await asyncio.sleep(600)

def bootstrap():
    # Secrets persist in the DB so Railway Variables are not required for first boot.
    if not _setting('jwt_secret'):
        execute('INSERT INTO settings(key,value) VALUES(?,?)',('jwt_secret',secrets.token_urlsafe(48)))
    if not (_setting('admin_password') or settings.admin_password):
        execute('INSERT INTO settings(key,value) VALUES(?,?)',('admin_password','admin'))
    if not _setting('bootstrap_complete'):
        execute('INSERT INTO settings(key,value) VALUES(?,?)',('bootstrap_complete','0'))
def bootstrap_nodes():
    """Create the Railway origin node at startup (and the Cloudflare set if configured).

    Works with Railway's injected RAILWAY_PUBLIC_DOMAIN, a configured PUBLIC_BASE_URL
    or the panel's own base URL setting, so nodes exist without any manual step.
    """
    try:
        base=(_setting('public_base_url') or settings.public_base_url or '').strip()
        worker=(_setting('cloudflare_worker_url') or '').strip()
        if base and worker:
            sync_from_sources(base,worker)
        else:
            node_catalog.ensure_origin(base or None)
    except Exception:
        pass


async def bootstrap_nodes_full():
    """Startup pass that also detects a Cloudflare-fronted domain.

    Runs once in the background of the lifespan, so even the Cloudflare nodes
    exist before the admin ever opens the Node Catalog — the catalog becomes
    self-building: Railway origin + clean-IP edge entries, zero user action.
    """
    try:
        # A brand-new deployment has no clean-IP rows at all, and filling them at
        # boot means hundreds of outbound connects within seconds of the deploy —
        # exactly what gets a host flagged for "suspicious activity". The catalog
        # works without it (the origin node is always published), so seeding is
        # opt-in (NEXUS_SCAN_ON_BOOT=1) and an admin can scan any provider on
        # demand from the panel instead.
        if settings.scan_on_boot and not rows('SELECT ip FROM cf_ips LIMIT 1'):
            try: seed_ips(settings.cf_probe_limit, 'cloudflare')
            except Exception: pass
        base=(_setting('public_base_url') or settings.public_base_url or '').strip() or None
        worker=(_setting('cloudflare_worker_url') or '').strip() or None
        await auto_sync(base, worker)
    except Exception:
        pass

@asynccontextmanager
async def lifespan(app:FastAPI):
    init_db(); bootstrap(); ensure_nodes(); bootstrap_nodes()
    await xray.start_or_reload(force=True)
    # The second engines (AnyTLS/TUIC) reconcile themselves, then keep doing so:
    # an admin switch is picked up without a restart.
    await cores.sync(force=True)
    # The Telegram proxies: the web-proxy inbounds are in the Xray config Xray
    # itself just rendered above, so this reconciles the MTProto process and only
    # restarts Xray if the Telegram inbounds really changed its config.
    await telegram_proxies.reconcile(force=False)
    task=asyncio.create_task(maintenance()); cf_task=asyncio.create_task(cf_loop(settings.cf_probe_interval)); xray_task=asyncio.create_task(xray.loop()); ping_task=asyncio.create_task(ping_loop(settings.cf_probe_interval,_ping_interval)); auto_task=asyncio.create_task(bootstrap_nodes_full()); cat_task=asyncio.create_task(catalog_loop()); cores_task=asyncio.create_task(cores.loop()); tg_task=asyncio.create_task(telegram_proxies.loop())
    try: yield
    finally:
        task.cancel(); cf_task.cancel(); xray_task.cancel(); ping_task.cancel(); auto_task.cancel(); cat_task.cancel(); cores_task.cancel(); tg_task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        try: await cf_task
        except asyncio.CancelledError: pass
        try: await xray_task
        except asyncio.CancelledError: pass
        try: await ping_task
        except asyncio.CancelledError: pass
        try: await auto_task
        except asyncio.CancelledError: pass
        try: await cat_task
        except asyncio.CancelledError: pass
        try: await cores_task
        except asyncio.CancelledError: pass
        try: await tg_task
        except asyncio.CancelledError: pass
        try: await telegram_proxies.stop_all()
        except Exception: pass
        try: await cores.stop_all()
        except Exception: pass
        try: await xray._stop()
        except Exception: pass
app=FastAPI(title=settings.app_name,version=APP_VERSION,lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[],allow_methods=['GET','POST','PUT','DELETE','OPTIONS'],allow_headers=['Content-Type','X-Admin-Password'])
templates=Jinja2Templates(directory=os.path.join(BASE_DIR,'templates'))
app.mount('/static',StaticFiles(directory=os.path.join(BASE_DIR,'static')),name='static')
# The newer capability endpoints (customization, Hysteria2, location packs, the
# network tools) live in their own router so this module keeps owning the core.
app.include_router(extra_router)
# The Telegram Web proxy is a public path on this domain (``/tg/…``), so it owns
# its own router: the admin API lives in the extra router, this one is reached by
# whoever opens the Telegram web app from a blocked network.
app.include_router(telegram_web.router)
@app.get('/health')
def health():
    try:
        row('SELECT 1'); return {'ok':True,'service':'nexus-python','database':'ok','uptime_seconds':int(time.time()-_started),'time':int(time.time())}
    except Exception as exc: raise HTTPException(503,f'database unavailable: {type(exc).__name__}')
@app.get('/',response_class=HTMLResponse)
def home(request:Request,token:str=''):
    try: auth(request)
    except HTTPException:
        # Session bootstrap for clients whose browser refuses the session cookie
        # (embedded preview panes): a valid token renders the panel directly and
        # the front-end keeps using it as the X-Nexus-Session header.
        if not _valid_token(token): return RedirectResponse('/login',303)
    _ensure_catalog(request)
    return templates.TemplateResponse(request,'index.html',{'users':len(list_users()),'proxies':len(list_proxies()),'nodes':len(active_nodes()),'brand':_brand()})
@app.get('/login',response_class=HTMLResponse)
def login_page(request:Request): return templates.TemplateResponse(request,'login.html',{'brand':_brand()})
@app.post('/api/login')
async def login(request:Request):
    ip=_client_ip(request)
    if throttle.blocked(ip): raise HTTPException(429,'too many login attempts')
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    password=str(body.get('password','')); remember=bool(body.get('remember'))
    admin=_setting('admin_password') or settings.admin_password
    if not admin or not secrets.compare_digest(password,admin):
        throttle.fail(ip); _audit('auth.login',f'{ip} · failed')
        raise HTTPException(401,'invalid password')
    throttle.reset(ip); _audit('auth.login',f"{ip}{' · remember' if remember else ''}")
    token=_token(remember)
    payload={'success':True,'token':token,'expires_in':sessions.ttl(remember),'ttl_days':max(1,sessions.ttl(remember)//86400)}
    return sessions.apply(request,JSONResponse(payload),token,remember)
@app.post('/api/logout')
def logout():
    _audit('auth.logout','panel')
    return sessions.clear(JSONResponse({'success':True}))
@app.get('/api/stats')
def stats(request:Request):
    auth(request); us=list_users(); ps=list_proxies(); return {'users':len(us),'active_users':sum(bool(x['is_active']) for x in us),'proxies':len(ps),'enabled_proxies':sum(bool(x['enabled']) for x in ps),'nodes':len(list_nodes()),'cf_ips':len(rows('SELECT ip FROM cf_ips WHERE ok=1'))}
@app.get('/api/users')
def users(request:Request): auth(request); return [_user_payload(u) for u in list_users()]
@app.post('/api/users')
def create(request:Request,m:UserCreate):
    auth(request)
    try: u=create_user(m)
    except Exception as e: raise HTTPException(400,str(e))
    _audit('user.create',f"{m.username} ({transports.protocol_value(m.protocol)})")
    return _user_payload(u)
def _portal_url(base,token): return f"{base}/portal/{urllib.parse.quote(str(token),safe='')}"

def _preset_summary(fields):
    """Human-readable list of what a preset actually applied."""
    bits=[]
    selected=[p for p in transports.PROTOCOLS if p in transports.parse_protocols(fields.get('protocol'))]
    bits.append('همه پروتکل‌ها' if len(selected)==len(transports.PROTOCOLS)
                else ' · '.join(p.upper() for p in selected) or 'همه پروتکل‌ها')
    bits.append(f"فرگمنت {fields['frag_len']} / {fields.get('frag_int') or '۱۰'}" if fields.get('frag_len') else 'بدون فرگمنت')
    bits.append('حجم نامحدود' if not fields.get('limit_gb') else f"{float(fields['limit_gb']):g} گیگ")
    bits.append('بدون انقضا' if not fields.get('expiry_days') else f"{int(fields['expiry_days'])} روز")
    bits.append(f"{fields.get('ip_limit') or '∞'} دستگاه همزمان")
    if fields.get('block_ads'): bits.append('بلاک تبلیغات')
    return bits

def _user_target(u):
    """The default subscription target for a user.

    A user's primary URI protocol when they have one (so a legacy row still
    resolves to ``vless``), otherwise the full node × transport matrix.
    """
    enabled=[p for p in transports.PROTOCOLS if p in transports.user_protocols(u)]
    for candidate in enabled:
        if candidate in transports.URI_PROTOCOLS:
            return candidate
    return 'auto'

def _target_label(target):
    """Label for a subscription target: a transport profile, a format or a client."""
    profile=transports.find(target)
    return profile['tag'] if profile else SUB_LABELS.get(target,target)

def _target_offerable(target,protocols):
    """Whether a target has anything to publish for this user at all.

    Two gates: a protocol the user was not given, and a transport this
    deployment has not published (Reality with no dedicated TCP port, WARP not
    registered). Both raise a *clear* 400 when a link is opened — but a link the
    panel never should have offered in the first place: the drawer listed every
    target and handed out rows that were wrong the moment they were tapped.
    Format and client targets always resolve, so they are always offered.
    """
    if target in transports.PROTOCOLS:
        return target in protocols
    if target in SUB_TRANSPORT_TARGETS:
        try: return bool(profiles_for(target,protocols))
        except Exception: return False
    return True

def _transport_targets(protocols=None):
    """One subscription URL per published transport profile.

    ``protocols`` narrows the list to one user's enabled protocol set, so the
    panel and the status window never offer a link the user cannot dial.
    """
    return [{'target':p['id'],'label':p['tag'],'protocol':p['protocol'],'network':p['network'],
             'group':p['group'],'security':p.get('security') or 'tls',
             'method':p.get('method'),'key_bytes':p.get('key_len'),
             # Shadowsocks has no single-line sharing URI: its subscription is
             # the sing-box JSON, and the panel says so instead of hiding it.
             'uri':p['protocol'] in transports.URI_PROTOCOLS}
            for p in transports.available_profiles(protocols)]

def _user_payload(u):
    """A user row plus the derived protocol view the panel renders."""
    if not u: return u
    item=dict(u)
    item['max_configs']=transports.user_max_configs(u) or None
    # Which nodes this user's subscription may contain (the panel's node-scope
    # picker), plus its human label — the one place that naming is derived.
    item['node_scope']=transports.user_scope(u)
    item['node_scope_label']=node_scope.label(item['node_scope'])
    enabled=[p for p in transports.PROTOCOLS if p in transports.user_protocols(u)]
    item['protocols']=enabled
    item['protocol_label']=('همه پروتکل‌ها' if len(enabled)==len(transports.PROTOCOLS)
                            else ' · '.join(p.upper() for p in enabled) or '—')
    item['protocol_value']=transports.protocol_value(enabled)
    return item

def _protocol_catalog():
    """The protocol multi-select the panel renders (plus the SS cipher families)."""
    return transports.protocol_catalog()

def _share_payload(request:Request,u,node=''):
    """Everything an end user needs: status window, per-client subs, formats."""
    base=public_base(request); token=urllib.parse.quote(u['uuid'],safe='')
    transport_protocols=transports.user_protocols(u)
    return {
        'base_url':base,
        'portal_url':_portal_url(base,token),
        'smart_url':client_subscription_url(base,token,'auto',node),
        'clients':client_links(base,token,node,None),
        'transports':_transport_targets(),
        'node_scope':transports.user_scope(u),'node_scope_label':node_scope.label(transports.user_scope(u)),
        'scopes':_scope_options(base,token,u),
        'targets':[{'target':t,'label':_target_label(t),'url':client_subscription_url(base,token,t,node)}
                   for t in SUB_TARGETS if _target_offerable(t,transport_protocols)],
    }

def _scope_options(base,token,u):
    """The node-scope picker: every scope with its live node count and URL.

    Handed to the panel's user form, the quick-create result and the public
    status window, so "only the multi-location nodes", "only the US edge" and
    "only my own server" are all one tap — and the counts come from the catalog
    that exists right now, not from a static list.
    """
    return [{**item,'url':_sub_url(base,token,_user_target(u),'','',item['id'])}
            for item in node_scope.options(active_nodes(), transports.user_scope(u))]

@app.get('/api/presets')
def get_presets(request:Request):
    """Ready-made "best settings" bundles offered by the quick-create button."""
    auth(request)
    # A "mode" is a preset plus the node scope it publishes: the quick-create
    # screen shows one card per mode, so nobody has to guess which nodes they get.
    modes=[{**item,'scope_label':node_scope.label(item.get('scope') or 'all')} for item in PRESETS]
    return {'default':DEFAULT_PRESET,'presets':PRESETS,'modes':modes,'protocols':_protocol_catalog(),
            'scopes':node_scope.options(active_nodes()),'scope':_setting('default_scope') or 'all',
            'defaults':{
        'protocol':_setting('default_protocol') or 'all',
        'limit_gb':_setting('default_limit_gb') or '',
        'expiry_days':_setting('default_expiry_days') or '','ip_limit':_setting('default_ip_limit') or '',
        'max_configs':_setting('default_max_configs') or '','node_scope':_setting('default_scope') or 'all'}}

@app.get('/api/clients')
def get_clients(request:Request):
    """Client catalog: import format, download link and notes."""
    auth(request)
    return {'clients':client_catalog(),'families':FAMILIES,'targets':SUB_TARGETS,'labels':SUB_LABELS,
            'base_url':public_base(request),'transports':_transport_targets()}

@app.get('/api/transports')
def get_transports(request:Request):
    """The published protocol/transport matrix: what every node offers right now."""
    auth(request)
    payload=transports.catalog()
    payload['nodes']=[{'name':n['name'],'kind':n['kind'],'latency_ms':n['latency_ms'],
                       'transports':[p['id'] for p in transports.available_profiles()]} for n in active_nodes()]
    payload['xray']=xray.status()
    payload['warp']=warp_service.status()
    return payload

@app.get('/api/warp')
def get_warp(request:Request):
    auth(request); return warp_service.status()

@app.post('/api/warp')
async def manage_warp(request:Request):
    """Register / enable / disable the opt-in WARP exit node."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    action=str(body.get('action') or 'status').strip().lower()
    if action=='register':
        try: await asyncio.to_thread(warp_service.register)
        except Exception as exc: raise HTTPException(400,f'ثبت WARP ناموفق بود: {exc}')
        _audit('warp.register','peer registered')
    elif action in ('enable','disable'):
        if action=='enable' and not warp_service.status()['registered']:
            raise HTTPException(400,'ابتدا WARP را ثبت کنید')
        warp_service.enable() if action=='enable' else warp_service.disable()
        _audit('warp.'+action,'warp exit node')
    elif action=='discard':
        warp_service.discard(); _audit('warp.discard','peer removed')
    else:
        raise HTTPException(400,'unknown action')
    await xray.start_or_reload(force=True)
    return {'success':True,'warp':warp_service.status(),'xray':xray.status()}

def _quick_username():
    for _ in range(25):
        name='nxs-'+secrets.token_hex(3)
        if not get_user(name): return name
    raise HTTPException(500,'could not allocate a username')

@app.post('/api/users/quick')
async def quick_create(request:Request):
    """One click user: the real engine creates the Xray user with Iran-tuned
    settings and the response already carries every client link."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    if not isinstance(body,dict): body={}
    try: chosen=get_preset(body.get('preset'))
    except ValueError as e: raise HTTPException(400,str(e))
    fields=dict(chosen['fields'])
    # Precedence: admin defaults, then the preset, then anything the request sends.
    # An admin default protocol set wins over the preset's own selection.
    admin_protocols=(_setting('default_protocol') or '').strip().lower()
    if admin_protocols=='all' or admin_protocols in transports.PROTOCOLS:
        fields['protocol']=admin_protocols
    for key,setting_key in (('limit_gb','default_limit_gb'),('expiry_days','default_expiry_days'),
                            ('ip_limit','default_ip_limit'),('max_configs','default_max_configs')):
        configured=(_setting(setting_key) or '').strip()
        if configured:
            try: fields[key]=float(configured) if key=='limit_gb' else int(float(configured))
            except ValueError: pass
    # The scope is a real field on UserCreate, so it rides in with the rest and
    # is normalised (any spelling) by the model itself.
    fields['node_scope']=fields.get('scope') or chosen.get('scope') or 'all'
    for key in list(fields.keys()):
        if key in body and body[key] is not None: fields[key]=body[key]
    if body.get('scope') is not None: fields['node_scope']=body['scope']
    username=str(body.get('username') or '').strip() or _quick_username()
    if get_user(username): raise HTTPException(400,'username already exists')
    try: m=UserCreate(username=username,**fields)
    except Exception as e: raise HTTPException(400,f'invalid settings: {e}')
    try: u=create_user(m)
    except Exception as e: raise HTTPException(400,str(e))
    _audit('user.quick',f"{username} · {chosen['id']}")
    return {'success':True,'preset':chosen['id'],'preset_name':chosen['name'],'applied':_preset_summary(fields),'user':_user_payload(u),**_share_payload(request,u)}
@app.get('/api/users/{username}')
def user_detail(request:Request,username:str):
    auth(request); u=get_user(username)
    if not u: raise HTTPException(404,'user not found')
    return _user_payload(u)
@app.put('/api/users/{username}')
def update(request:Request,username:str,body:dict):
    auth(request); u=get_user(username)
    if not u: raise HTTPException(404,'user not found')
    if body.get('toggle_only'): return _user_payload(toggle_user(username))
    if body.get('reset_action'):
        # An unknown action is the caller's mistake, so it answers 400 with the
        # reason instead of escaping as a ValueError and a 500.
        try: return _user_payload(reset_user(username,str(body['reset_action'])))
        except ValueError as exc: raise HTTPException(400,str(exc))
    allowed_keys={'protocol','limit_gb','expiry_days','limit_req','ip_limit','is_active','ips','port','sni','host','fingerprint','tls','user_proxy','frag_len','frag_int','advanced_frag','cipher_suites','tls_mask','block_ads','block_porn','auto_rotate_ip','rotate_time','ip_operator','ip_count'}
    payload=dict(body)
    # The config cap is not a column: it lives in the user's metadata JSON, so it
    # is written through its own helper instead of the generic UPDATE below.
    if 'max_configs' in payload:
        try: cap=int(payload.pop('max_configs')) if str(payload.get('max_configs')).strip() not in ('','None') else None
        except (TypeError,ValueError): raise HTTPException(400,'max_configs must be a number')
        if cap is not None and not 1<=cap<=500: raise HTTPException(400,'max_configs must be between 1 and 500')
        set_metadata_value(username,'max_configs',cap)
        _audit('user.update',f"{username} · max_configs={cap or 'unlimited'}")
    # The node scope is metadata as well (all / multi-location / own server / a
    # country), written through its own helper just like the config cap.
    if 'node_scope' in payload:
        raw=str(payload.pop('node_scope') or '').strip()
        value=node_scope.normalize(raw) if raw else None
        set_metadata_value(username,'node_scope',value)
        _audit('user.update',f"{username} · node_scope={value or 'all'}")
    if 'protocol' in payload:
        # The protocol set is a multi-select: validate it here and store the
        # canonical comma-separated form so an empty selection cannot lock the
        # user out of every inbound.
        value=transports.protocol_value(payload['protocol'])
        if value not in ('all',) and not re.match(PROTOCOL_LIST,value):
            raise HTTPException(400,'protocol must be all or a comma-separated list of vless, vmess, trojan, ss')
        payload['protocol']=value
    sets=[]; vals=[]
    for k,v in payload.items():
        if k in allowed_keys: sets.append(k+'=?'); vals.append(v)
    if sets:
        vals.append(username); execute('UPDATE users SET '+','.join(sets)+' WHERE username=?',vals)
        _audit('user.update',f"{username} · {','.join(sorted(k for k in payload if k in allowed_keys))}")
    return _user_payload(get_user(username))
@app.delete('/api/users/{username}')
def delete(request:Request,username:str):
    auth(request)
    # ``execute`` returns ``lastrowid`` on SQLite, which is None for a DELETE, so
    # a real deletion used to answer ``success: false``. Existence is the honest
    # answer for the caller (and it keeps working the same way on Postgres).
    existed=bool(get_user(username)); delete_user(username)
    _audit('user.delete',username); return {'success':existed}
@app.post('/api/traffic/{username}')
def traffic(request:Request,username:str,e:TrafficEvent):
    auth(request); result=track(username,e.bytes,e.requests,e.ip)
    if not result: raise HTTPException(404,'user not found')
    return result
def _sub_nodes(node:str='',location:str='',scope:str=''):
    items=active_nodes(location=location,scope=scope)
    if node:
        wanted={x.strip().lower() for x in str(node).split(',') if x.strip()}
        items=[n for n in items if str(n['name']).lower() in wanted]
    return items

@app.get('/sub/{token}')
def subscription(request:Request,token:str,target:str='auto',node:str='',location:str='',scope:str=''):
    u=get_by_token(urllib.parse.unquote(token))
    if not u: raise HTTPException(404,'subscription not found')
    ok,reason=allowed(u)
    if not ok: raise HTTPException(403,reason)
    # ``?scope=`` narrows the catalog to a country / the edge only / this server
    # only; with no parameter the user's own scope applies, so the smart link an
    # admin handed out already publishes exactly the nodes that user may see.
    wanted_scope=node_scope.normalize(scope or transports.user_scope(u))
    nodes=_sub_nodes(node,location,wanted_scope)
    if not nodes:
        empty_scope=node_scope.filter_nodes(active_nodes(),wanted_scope)
        reason_text=('هیچ نودی در محدودهٔ «'+node_scope.label(wanted_scope)+'» منتشر نشده است'
                     if not empty_scope else 'هیچ نودی با این فیلتر پیدا نشد')
        raise HTTPException(404,reason_text)
    # Generate from the live Node Catalog on every request. This means a client
    # refresh automatically receives the current origin node plus every healthy
    # clean IP / clean domain of every configured location.
    # The Hysteria2 endpoint and the hosted protocols belong to the whole
    # subscription, not to one node, so a per-node address (``?node=``) leaves
    # them out.
    try: text=render(u,public_base(request),target,nodes,_sub_prefix(),include_extras=not node)
    except ValueError as e: raise HTTPException(400,str(e))
    cap=transports.user_max_configs(u)
    headers={'Cache-Control':'no-store, max-age=0','X-Content-Type-Options':'nosniff',
             'X-NEXUS-Node-Count':str(len(nodes)),'X-NEXUS-Target':normalize_target(target),
             'X-NEXUS-Format':'singbox' if text.lstrip().startswith('{') else 'lines',
             'X-NEXUS-Max-Configs':str(cap),'X-NEXUS-Flags':'1' if _flags_on() else '0',
             'X-NEXUS-Transports':str(len(transports.available_profiles()))}
    if location: headers['X-NEXUS-Location']=str(location)
    headers['X-NEXUS-Scope']=wanted_scope
    return PlainTextResponse(text,headers=headers)
@app.get('/sub/{token}/{node_name}')
def subscription_node(request:Request,token:str,node_name:str,target:str='auto'):
    # Per-node subscription: one catalog node per link makes client-side
    # grouping and failover trivial.
    return subscription(request,token,target,node_name)
@app.get('/feed/{token}')
def feed(request:Request,token:str,target:str='auto',node:str='',location:str='',scope:str=''):
    return subscription(request,token,target,node,location,scope)


async def _relay(ws: WebSocket, reader, writer, user, initial=b''):
    sent = len(initial); received = 0
    if initial:
        writer.write(initial); await writer.drain()
    async def ws_to_tcp():
        nonlocal sent
        while True:
            data = await ws.receive_bytes()
            if not data: continue
            writer.write(data); await writer.drain(); sent += len(data)
    async def tcp_to_ws():
        nonlocal received
        while True:
            data = await reader.read(65536)
            if not data: break
            await ws.send_bytes(data); received += len(data)
    a,b = await asyncio.gather(ws_to_tcp(), tcp_to_ws(), return_exceptions=True)
    try: writer.close(); await writer.wait_closed()
    except Exception: pass
    try: track(user['username'], sent + received, 1, None)
    except Exception: pass


def _vless_request(data: bytes):
    if len(data) < 24 or data[0] != 1: raise ValueError('invalid vless header')
    uid = __import__('uuid').UUID(bytes=data[1:17]); addons_len=data[17]; pos=18+addons_len
    if len(data) < pos+4: raise ValueError('short vless header')
    port=int.from_bytes(data[pos:pos+2],'big'); cmd=data[pos+2]; at=data[pos+3]; pos += 4
    if cmd != 1: raise ValueError('only TCP VLESS is supported')
    if at == 1:
        if len(data)<pos+4: raise ValueError('short ipv4'); host='.'.join(map(str,data[pos:pos+4])); pos+=4
    elif at == 2:
        ln=data[pos]; pos+=1; host=data[pos:pos+ln].decode('utf-8'); pos+=ln
    elif at == 3:
        if len(data)<pos+16: raise ValueError('short ipv6')
        import ipaddress; host=str(ipaddress.IPv6Address(data[pos:pos+16])); pos+=16
    else: raise ValueError('invalid address type')
    return str(uid),host,port,data[pos:]


def _trojan_request(data: bytes):
    if b'\r\n' not in data: raise ValueError('short trojan header')
    password, rest=data.split(b'\r\n',1); rest=rest[2:] if rest.startswith(b'\r\n') else rest
    if len(rest)<7: raise ValueError('short trojan request')
    cmd=rest[0]; atyp=rest[1]
    if cmd != 1: raise ValueError('only TCP Trojan is supported')
    port=int.from_bytes(rest[2:4],'big'); pos=4
    if atyp==1: host='.'.join(map(str,rest[pos:pos+4])); pos+=4
    elif atyp==3: ln=rest[pos];pos+=1;host=rest[pos:pos+ln].decode();pos+=ln
    elif atyp==4:
        import ipaddress;host=str(ipaddress.IPv6Address(rest[pos:pos+16]));pos+=16
    else: raise ValueError('invalid trojan address type')
    return password.decode(errors='ignore'),host,port,rest[pos:]


# Every WebSocket path the edge exposes, mapped to the local Xray listener that
# speaks the matching protocol/transport. One bridge function serves them all.
EDGE_ROUTES = xray.edge_routes()


async def _bridge_ws(ws: WebSocket, upstream_path: str):
    await ws.accept()
    port = EDGE_ROUTES.get(upstream_path)
    if not port:
        try: await ws.close(code=1011, reason='transport not available')
        except Exception: pass
        return
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}{upstream_path}", max_size=None, ping_interval=20, ping_timeout=20) as upstream:
            async def client_to_xray():
                while True:
                    msg = await ws.receive()
                    if msg.get('type') == 'websocket.disconnect':
                        break
                    data = msg.get('bytes')
                    if data is not None:
                        await upstream.send(data)
                    elif msg.get('text') is not None:
                        await upstream.send(msg['text'])
            async def xray_to_client():
                while True:
                    data = await upstream.recv()
                    if isinstance(data, bytes):
                        await ws.send_bytes(data)
                    else:
                        await ws.send_text(data)
            await asyncio.gather(client_to_xray(), xray_to_client(), return_exceptions=True)
    except Exception as exc:
        try: await ws.close(code=1011, reason=str(exc)[:120])
        except Exception: pass


def _edge_handler(upstream_path, route_path):
    """Build the bridge handler for one published edge path."""
    async def handler(ws: WebSocket):
        await _bridge_ws(ws, upstream_path)
    handler.__name__ = 'edge_ws_' + (re.sub(r'\W+', '_', route_path).strip('_') or 'root')
    return handler


def _register_edge_routes():
    """Register one WebSocket route per published edge path.

    The route table is generated from the transport profiles instead of being
    hand-written, so adding a transport (a new Shadowsocks cipher, a new path
    shape) can never leave a published link without a route on the edge.
    """
    for path in list(EDGE_ROUTES) + ['/ws']:
        upstream = '/ws/vless' if path == '/ws' else path
        app.websocket(path)(_edge_handler(upstream, path))


_register_edge_routes()


def _sub_url(base,token,target='auto',node='',location='',scope=''):
    """One subscription URL, with the optional location/scope filters appended."""
    url=client_subscription_url(base,token,target,node)
    if location:
        url += '&location=' + urllib.parse.quote(str(location))
    if scope:
        # ``:`` and ``,`` stay literal so a country scope reads ``&scope=cc:us``
        # instead of percent-encoded soup, while everything else is escaped.
        url += '&scope=' + urllib.parse.quote(str(scope), safe=':,')
    return url


def _portal_transports(base,token,protocols=None):
    """One ready-to-copy subscription URL per published transport profile.

    The window's transport list used to render only the profile metadata, so
    every row came out with an empty link; the URL is built here, next to the
    profile it belongs to.
    """
    return [{'target':p['target'],'label':p['label'],'protocol':p['protocol'],'network':p['network'],
             'group':p['group'],'url':_sub_url(base,token,p['target'])}
            for p in _transport_targets(protocols)]


def _core_subs(base,token,node=''):
    """The four main subscription shapes the status window offers.

    A user should never have to guess which format their client wants: normal
    text, Base64 (V2Ray), sing-box JSON and Clash/Mihomo cover essentially every
    client on the market.
    """
    return [
        {'id':'auto','label':'سابلینک عادی','hint':'متن ساده VLESS/VMess/Trojan/Shadowsocks — همه کلاینت‌های مدرن',
         'url':_sub_url(base,token,'auto',node)},
        {'id':'base64','label':'Base64 (V2Ray)','hint':'برای v2rayNG، Bettbox، Shadowrocket، V2Box و هر کلاینت کلاسیک',
         'url':_sub_url(base,token,'base64',node)},
        {'id':'singbox','label':'sing-box JSON','hint':'برای Hiddify، NekoBox، Karing، sing-box و v2rayN (هسته sing-box)',
         'url':_sub_url(base,token,'singbox',node)},
        {'id':'clash','label':'Clash / Mihomo','hint':'برای Clash Verge، Mihomo، ClashX و Stash',
         'url':_sub_url(base,token,'clash',node)},
    ]


def _portal_data(request:Request,u):
    """Public payload behind the subscription status window (no admin auth)."""
    base=public_base(request); token=urllib.parse.quote(u['uuid'],safe='')
    ok,reason=allowed(u)
    now=int(time.time()); used=float(u.get('used_gb') or 0); limit=u.get('limit_gb')
    protocols=transports.user_protocols(u)
    profiles=transports.available_profiles(protocols)
    cap=transports.user_max_configs(u)
    # The whole catalog is read for the pickers, but the window lists (and counts)
    # exactly the nodes this user's subscription really contains: a scoped user
    # sees their own slice, with the rest one tap away.
    user_scope=transports.user_scope(u)
    catalog_nodes=active_nodes()
    scoped_nodes=node_scope.filter_nodes(catalog_nodes,user_scope)
    nodes=[]
    for n in scoped_nodes:
        latency=n.get('latency_ms')
        nodes.append({
            'name':n['name'],'label':transports.node_label(n),'flag':transports.node_flag(n),
            'kind':n['kind'],'server':n['server'],'port':int(n.get('port') or 443),
            'location':transports.node_location(n),'provider':transports.node_provider(n),
            'latency_ms':latency,'online':bool(latency is not None and float(latency)>=0),
            'subscription':_sub_url(base,token,_user_target(u),n['name']),
            'subscription_all':_sub_url(base,token,'all',n['name']),
            'location_url':_sub_url(base,token,_user_target(u),'',transports.node_location(n)) if transports.node_location(n) else '',
            'transport_count':len(profiles),
            'transports':[{'target':p['id'],'label':p['tag'],'protocol':p['protocol'],
                           'url':_sub_url(base,token,p['id'],n['name'])}
                          for p in profiles],
        })
    # Multi-location: one sublink per country, so "just the German edge" is one tap
    # away instead of a URL the user has to build.
    locations=[]
    for location in sorted({n['location'] for n in nodes if n['location']}):
        sample=next(n for n in nodes if n['location']==location)
        locations.append({'location':location,'flag':sample['flag'],
                          'label':(sub_flags.name(location) or location.upper()),
                          'nodes':sum(1 for n in nodes if n['location']==location),
                          'url':_sub_url(base,token,_user_target(u),'',location)})
    configs=len(entry_pairs(u,scoped_nodes,profiles)) if scoped_nodes else 0
    return {
        'brand':'NEXUS','token':u['uuid'],'username':u['username'],'protocol':(u.get('protocol') or 'all'),
        'protocol_label':_user_payload(u)['protocol_label'],
        'is_active':bool(u['is_active']),'allowed':ok,'reason':reason,'reason_label':REASON_LABELS.get(reason,reason),
        'used_gb':round(used,3),'limit_gb':limit,'quota_pct':round(min(100.0,used/float(limit)*100),1) if limit else 0.0,
        'used_req':int(u.get('used_req') or 0),'limit_req':u.get('limit_req'),'ip_limit':u.get('ip_limit'),
        'expires_at':u.get('expires_at'),'start_on_first_connect':bool(u.get('start_on_first_connect')),
        'first_connection_time':u.get('first_connection_time'),
        'max_configs':cap or None,'config_count':configs,'config_limit':cap or configs,
        'fragment':u.get('frag_len') or '','fragment_interval':u.get('frag_int') or '','fingerprint':u.get('fingerprint') or 'chrome',
        'server_time':now,'base_url':base,
        'portal_url':_portal_url(base,token),
        'smart_url':_sub_url(base,token,'auto'),
        # Client links, already split by engine family: a Clash client only ever
        # sees YAML, an Xray client only Base64/text.
        'clients':client_links(base,token,'',None),
        'client_groups':client_groups(base,token),
        'transports':_portal_transports(base,token,protocols),
        'nodes':nodes,'nodes_total':len(nodes),'nodes_online':sum(1 for n in nodes if n['online']),
        'catalog_total':len(catalog_nodes),'scope_empty':bool(node_scope.parse(user_scope)['mode']!=node_scope.SCOPE_ALL and not nodes),
        'locations':locations,
        # Node scope: which slice this subscription publishes, and every other
        # slice as a ready-made link (multi-location only / the server only /
        # one country). This is the public half of the panel's scope picker.
        'scope':user_scope,'scope_label':node_scope.label(user_scope),'scope_hint':node_scope.hint(user_scope),
        'scopes':[{**item,'url':_sub_url(base,token,_user_target(u),'','',item['id'])}
                  for item in node_scope.options(catalog_nodes,user_scope)],
        # Deployment-wide extras an end user should still see.
        'hysteria':transports.catalog()['hysteria2'],
        # The Telegram proxies this user may use: their own web-proxy lines (their
        # username and credential), the shared MTProto link and the web address.
        # Empty when nothing is published, so the window grows no empty section.
        'telegram':telegram_proxies.portal_payload(base,u),
        # Which of the four core sublinks the status window highlights, and whether
        # node names carry their country flag.
        'default_format':_brand()['default_format'],
        'flags':_flags_on(),
        'banner':_brand()['banner'],'support_url':_brand()['support_url'],
    }

@app.get('/portal/{token}',response_class=HTMLResponse)
def portal(request:Request,token:str):
    """Public status window an end user can bookmark: traffic, links and nodes."""
    u=get_by_token(urllib.parse.unquote(token))
    if not u: raise HTTPException(404,'subscription not found')
    return templates.TemplateResponse(request,'portal.html',{'p':_portal_data(request,u),'brand':_brand()})

@app.get('/portal/{token}/json')
def portal_json(request:Request,token:str):
    u=get_by_token(urllib.parse.unquote(token))
    if not u: raise HTTPException(404,'subscription not found')
    return _portal_data(request,u)

@app.get('/status/{username}',response_class=HTMLResponse)
def status(request:Request,username:str):
    # Legacy admin URL: same public status window, addressed by username.
    u=get_user(username)
    if not u: raise HTTPException(404,'not found')
    return templates.TemplateResponse(request,'portal.html',{'p':_portal_data(request,u),'brand':_brand()})
@app.get('/api/proxies')
def proxies(request:Request): auth(request); return list_proxies()
async def _optional_json(request:Request):
    """The request body as a dict: a missing or malformed one is simply empty.

    Subscripting ``await request.json()`` directly turned a body-less call into a
    ``KeyError`` and a 500, which reads as a broken feature rather than as «the
    value you sent is wrong».
    """
    try: body=await request.json()
    except Exception: body=None
    return body if isinstance(body,dict) else {}

@app.post('/api/proxies')
async def proxy_create(request:Request):
    auth(request); b=await _optional_json(request)
    proxy=str(b.get('proxy') or '').strip()
    if not proxy: raise HTTPException(400,'آدرس پروکسی لازم است (مثل socks5://user:pass@host:1080)')
    try: add_proxy(proxy,b.get('country'))
    except Exception as e: raise HTTPException(400,str(e))
    return {'success':True}
@app.post('/api/test-proxy')
async def proxy_test(request:Request):
    auth(request); b=await _optional_json(request)
    proxy=str(b.get('proxy') or '').strip()
    if not proxy: raise HTTPException(400,'آدرس پروکسی لازم است (مثل socks5://user:pass@host:1080)')
    if not parse_proxy(proxy): raise HTTPException(400,'آدرس پروکسی قابل خواندن نیست — شکل درست: socks5://user:pass@host:1080')
    return check_proxy(proxy)
@app.get('/api/dns')
async def dns(request:Request,name:str): auth(request); return await doh(name)
@app.get('/api/backup')
def backup(request:Request): auth(request); return export_all()
@app.get('/api/logs')
def logs(request:Request,limit:int=100): auth(request); return rows('SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?',(min(max(limit,1),500),))
@app.get('/setup')
def setup_info():
    return {'ready': bool(_setting('bootstrap_complete')=='1'), 'bootstrap_password_available': bool(_setting('bootstrap_admin_password'))}

@app.post('/api/setup/admin')
async def setup_admin(request:Request):
    # One-time bootstrap: change the generated password without Railway Variables.
    if _setting('bootstrap_complete')=='1': raise HTTPException(409,'setup already completed')
    b=await request.json(); password=str(b.get('password',''))
    if len(password)<16: raise HTTPException(400,'password must be at least 16 characters')
    execute('UPDATE settings SET value=? WHERE key=?',(password,'admin_password'))
    execute('UPDATE settings SET value=? WHERE key=?',('1','bootstrap_complete'))
    execute('DELETE FROM settings WHERE key=?',('bootstrap_admin_password',))
    return {'success':True}


@app.get('/api/settings/cloudflare-worker')
def get_worker_settings(request:Request):
    auth(request)
    return {'url': _setting('cloudflare_worker_url') or '', 'configured': bool(_setting('cloudflare_worker_url'))}

@app.post('/api/settings/cloudflare-worker')
async def save_worker_settings(request:Request):
    auth(request); b=await request.json(); url=str(b.get('url','')).strip(); key=str(b.get('api_key','')).strip()
    if url and not (url.startswith('https://') and urllib.parse.urlparse(url).hostname):
        raise HTTPException(400,'Worker URL must be a valid HTTPS URL')
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('cloudflare_worker_url',url))
    if key: execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('cloudflare_worker_key',key))
    sync_from_sources(public_base(request), url or None)
    _audit('cloudflare.worker',url or 'disabled')
    return {'success':True,'configured':bool(url),'nodes':[_node_payload(n) for n in list_nodes()]}

def _node_payload(node):  # noqa: D401
    """A node row plus the edge-source view (its location, provider, last probe)."""
    if not node: return node
    item=dict(node)
    meta=edge_sources.parse_metadata(node.get('metadata'))
    item['location']=transports.node_location(node)
    item['provider']=transports.node_provider(node)
    item['source_id']=str(meta.get('source_id') or '')
    # Where the address really is, when it has been measured: the panel shows the
    # country next to the location label so a mismatch is visible instead of being
    # silently trusted (a wrong country in a client's flag is exactly the bug this
    # answers).
    item['geo']={'country':str(meta.get('geo_cc') or ''),
                 'declared':str(meta.get('declared_location') or ''),
                 'measured':bool(meta.get('geo_cc'))}
    # The panel shows *why* a node has no latency (TLS refused, port closed), so
    # the verdict travels with the row instead of only living in the metadata.
    item['probe']={'ok':bool(meta.get('ping_ok')),'tls':bool(meta.get('ping_tls')),
                   'verified':bool(meta.get('ping_verified')),'tcp':bool(meta.get('ping_tcp')),
                   'ms':meta.get('ping_ms'),'tcp_ms':meta.get('ping_tcp_ms'),
                   'error':str(meta.get('ping_error') or ''),'hint':str(meta.get('ping_hint') or ''),
                   'at':meta.get('ping_at')}
    item['role']='origin' if str(node.get('kind'))=='railway' else 'edge'
    return item

@app.get('/api/nodes')
def nodes(request:Request):
    auth(request); _ensure_catalog(request); return [_node_payload(n) for n in list_nodes()]

@app.get('/api/nodes/samples')
def node_samples(request:Request):
    """Ready-made nodes with different settings (clean IPs, alt ports, domains…)."""
    auth(request)
    existing={n['name'] for n in list_nodes()}
    items=edge_samples.catalog(public_host=_samples_host(request), worker_url=_setting('cloudflare_worker_url') or '')
    for item in items:
        item['added']=all(name in existing for name in item['names'])
        item['present']=[name for name in item['names'] if name in existing]
    return {'samples':items,'host':_samples_host(request),
            'worker':_setting('cloudflare_worker_url') or ''}


def _samples_host(request:Request):
    """The hostname a CDN would serve this deployment on (its public host)."""
    return (node_catalog.origin_host(public_base(request)) or '').strip()


@app.post('/api/nodes/samples')
async def add_node_samples(request:Request):
    """Create one or more sample nodes, then ping them for real."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    wanted=[str(item).strip().lower() for item in (body.get('ids') or []) if str(item).strip()]
    if body.get('all'):
        wanted=[spec['id'] for spec in edge_samples.SAMPLES]
    if not wanted: raise HTTPException(400,'حداقل یک نمونه لازم است')
    host=_samples_host(request); worker=_setting('cloudflare_worker_url') or ''
    created=[]; skipped=[]
    for sample_id in wanted:
        nodes=edge_samples.nodes_for(sample_id,host,worker)
        if nodes is None:
            skipped.append(sample_id); continue
        for node in nodes:
            existing=row('SELECT name FROM nodes WHERE name=?',(node['name'],))
            if existing:
                skipped.append(node['name']); continue
            upsert_node(node['name'],node['kind'],node['server'],int(node['port']),bool(node['tls']),
                        node['sni'] or None,node['host'] or None,'sample',
                        {**edge_samples.SAMPLE_META,'sample':sample_id,'provider':node.get('provider'),
                         'location':node.get('location') or '','role':'sample'})
            node_catalog.update(node['name'],{'enabled':1,'latency_ms':None})
            created.append(node['name'])
    if not created:
        raise HTTPException(400,'این نمونه‌ها از قبل وجود دارند')
    ping=await ping_all_nodes(names=created,timeout=4.0)
    _audit('nodes.samples',','.join(created))
    return {'success':True,'created':created,'skipped':skipped,'healthy':ping['healthy'],
            'probed':ping['probed'],'nodes':[_node_payload(n) for n in list_nodes()]}


@app.delete('/api/nodes/samples')
def clear_node_samples(request:Request):
    """Remove every node that came from a sample (hand-made ones stay)."""
    auth(request)
    removed=[]
    for node in list_nodes():
        if edge_samples.is_sample(node):
            execute('DELETE FROM nodes WHERE name=?',(node['name'],)); removed.append(node['name'])
    _audit('nodes.samples.clear',','.join(removed) or 'nothing')
    return {'success':True,'removed':removed,'nodes':[_node_payload(n) for n in list_nodes()]}


@app.post('/api/nodes/sync')
async def sync_nodes(request:Request):
    """Auto-rebuild: Worker host if configured, else a detected CF-fronted domain."""
    auth(request)
    result=await auto_sync(public_base(request), _setting('cloudflare_worker_url') or None, force_edge=True)
    _audit('nodes.sync',f"{result['synced']} nodes · edge {result['edge_host'] or '-'}")
    return {'success':True,'synced':result['synced'],'edge_host':result['edge_host'],
            'nodes':[_node_payload(n) for n in result['nodes']]}

@app.post('/api/nodes/ping')
async def ping_nodes(request:Request):
    """Ping every enabled node for real: Cloudflare clean IPs (dialled by IP with
    the Worker host as SNI) and the Railway origin (full TLS handshake)."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    try: timeout=min(max(float(body.get('timeout') or 4.0),1.0),10.0)
    except (TypeError,ValueError): timeout=4.0
    payload=await ping_all_nodes(names=body.get('nodes'),timeout=timeout)
    _audit('nodes.ping',f"{payload['healthy']}/{payload['probed']} reachable")
    return payload

@app.post('/api/nodes')
async def create_node(request:Request):
    auth(request); b=await request.json();
    name=str(b.get('name','')).strip(); server=str(b.get('server','')).strip(); kind=str(b.get('kind','railway')).strip()
    if not name or not server or kind not in {'railway','cloudflare','edge'}: raise HTTPException(400,'name, server and kind are required')
    upsert_node(name,kind,server,int(b.get('port',443)),bool(b.get('tls',True)),b.get('sni'),b.get('host'),kind,{})
    return {'success':True,'node':row('SELECT * FROM nodes WHERE name=?',(name,))}

@app.get('/api/cloudflare/ips')
def cloudflare_ips(request:Request,limit:int=50):
    auth(request); return rows('SELECT * FROM cf_ips ORDER BY CASE WHEN ok=1 THEN 0 ELSE 1 END, latency_ms ASC LIMIT ?', (min(max(limit,1),256),))

@app.post('/api/cloudflare/refresh')
async def cloudflare_refresh(request:Request):
    auth(request); count=seed_ips(settings.cf_probe_limit,'cloudflare'); results=await probe_all(limit=settings.cf_probe_limit); result=await auto_sync(public_base(request), _setting('cloudflare_worker_url') or None, force_edge=True); return {'seeded':count,'probed':len(results),'best':best(20),'edge_host':result['edge_host'],'nodes':[_node_payload(n) for n in result['nodes']]}


# ------------------------------------------------------- runtime + edge sources
@app.get('/api/system/runtime')
def system_runtime(request:Request):
    """Which platform this is, where it answers and whether a raw TCP port exists."""
    auth(request)
    return runtime.info()


def _edge_sync(request:Request):
    """Rebuild the catalog right away so a new source/domain is usable at once."""
    try:
        return sync_from_sources(public_base(request), _setting('cloudflare_worker_url') or None,
                                 node_catalog.edge_cache.get('host'))
    except Exception:
        return 0

async def _edge_geo(limit=12,timeout=3.0,budget=8.0):
    """Measure where the published addresses really are, then fix the labels.

    Every location carries a country, and a client's flag comes from that one
    field — so it is *measured* (three geo databases, majority vote) instead of
    inherited from the range the location was built from. A location whose
    addresses answer with another country is re-labelled here, because the flag,
    the node names (``us-cloudflare-01``) and the per-country sublink slug all
    read the same field. This is the fix for «پرچم کانادا زده، آی‌پی می‌زند
    آمریکا»; see ``app/edge/geo.py`` for the measurements behind it.
    """
    result=await edge_sources.measure_async(limit=limit,timeout=timeout,budget=budget)
    changes=edge_sources.align_labels()
    return {**result,'changes':changes}

def _edge_node_names(source_id='',provider_id='',only_pending=False):
    """Node names of one location (or one provider), for a scoped probe."""
    names=[]
    for node in list_nodes():
        meta=edge_sources.parse_metadata(node.get('metadata'))
        if source_id and str(meta.get('source_id') or '')!=str(source_id): continue
        if provider_id and str(meta.get('provider') or '')!=str(provider_id): continue
        if not source_id and not provider_id and str(node.get('kind')) not in ('cloudflare','edge'): continue
        if only_pending and node.get('latency_ms') is not None: continue
        names.append(node['name'])
    return names

async def _ping_edge_nodes(source_id='',provider_id='',only_pending=False,timeout=4.0,limit=60):
    """Measure the nodes of a location right after it changed.

    A location that is never probed shows as «اندازه‌گیری نشده» and is published
    unverified — which is exactly what made a freshly created location look like
    it «does not ping». Measuring it in the same request means the answer is
    there the moment the admin saves or scans, and a location whose Host/SNI its
    addresses cannot serve is marked broken instead of being handed to users.
    """
    names=_edge_node_names(source_id,provider_id,only_pending)[:max(1,int(limit))]
    if not names:
        return {'probed':0,'healthy':0,'failed':0,'avg_latency_ms':None,'results':[]}
    payload=await ping_all_nodes(names=names,timeout=timeout)
    return {'probed':payload['probed'],'healthy':payload['healthy'],'failed':payload['failed'],
            'avg_latency_ms':payload['avg_latency_ms'],'results':payload['results']}

@app.get('/api/edge')
def edge_status(request:Request):
    """Everything the panel's "منابع لبه و لوکیشن‌ها" card renders."""
    auth(request); _ensure_catalog(request)
    payload=edge_sources.status()
    payload['nodes']=[_node_payload(n) for n in list_nodes()]
    payload['worker']={'url':_setting('cloudflare_worker_url') or '','configured':bool(_setting('cloudflare_worker_url'))}
    # How much outbound traffic this deployment is allowed to make: a scan is
    # admin-triggered, small and visible instead of hundreds of connects at boot.
    payload['probing']={'enabled':bool(settings.outbound_probe_enabled),'scan_on_boot':bool(settings.scan_on_boot),
                        'limit':int(settings.cf_probe_limit),'concurrency':int(settings.cf_probe_concurrency)}
    # Whether a location's country is measured from its addresses (the panel
    # switch in «شخصی‌سازی») — without it a location keeps the label it was made
    # with instead of being corrected.
    payload['geo']={**(payload.get('geo') or {}),'setting_on':(_setting('geo_lookup') or '1')!='0'}
    return payload

@app.post('/api/edge/geo')
async def edge_geo(request:Request):
    """Measure the country of the published addresses and align the labels.

    The honest answer to «چرا روی این آی‌پی پرچم کشور دیگری است؟»: every address of
    every location is put to three independent geo databases, the majority answer
    becomes the location's country, and a label the data contradicts is corrected
    (its nodes are rebuilt from the new country, so the flag, the node names and
    the per-country sublink all agree). Bounded: at most ``limit`` addresses that
    have never been measured, and every answer is cached for good.
    """
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    try: limit=min(max(int(body.get('limit') or 24),1),96)
    except (TypeError,ValueError): limit=24
    result=await _edge_geo(limit=limit)
    synced=_edge_sync(request)
    status_payload=edge_sources.status()
    _audit('edge.geo',f"{result['checked']} measured · {len(result['changes'])} labels aligned")
    return {'success':True,'synced':synced,**result,
            'stats':status_payload.get('geo') or {},
            'sources':status_payload['sources'],
            'nodes':[_node_payload(n) for n in list_nodes()]}

@app.get('/api/edge/providers')
def edge_providers(request:Request):
    auth(request)
    return {'providers':edge_sources.provider_summary(),'runtime':runtime.info(),
            'pool':len(edge_sources.ips(limit=500))}

@app.post('/api/edge/scan')
async def edge_scan(request:Request):
    """Refresh every provider's clean-IP pool, then probe and republish."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    provider_id=str(body.get('provider') or '').strip().lower()
    try: limit=min(max(int(body.get('limit') or 96),4),512)
    except (TypeError,ValueError): limit=96
    if provider_id:
        if not edge_sources.provider(provider_id): raise HTTPException(400,'provider ناشناخته است')
        results=[edge_sources.scan(provider_id,limit=limit)]
    else:
        results=edge_sources.scan_all(limit)
    found=sum(int(r.get('found') or 0) for r in results)
    if not found and all(not r.get('ok') for r in results):
        detail='; '.join(str(r.get('error')) for r in results if r.get('error'))
        raise HTTPException(502,f'لیست هیچ provider دریافت نشد: {detail[:300]}')
    probed=await probe_all(limit=min(limit*2,192),provider_id=provider_id or None)
    # A scan is where addresses are born, so it is also the moment to ask where
    # they are: a location built from a fresh pool gets its real country here
    # instead of publishing whichever country the range suggested.
    geo_result=await _edge_geo(limit=8)
    synced=_edge_sync(request)
    ping=await _ping_edge_nodes(provider_id=provider_id,only_pending=True,timeout=2.5,limit=40)
    _audit('edge.scan',f"{provider_id or 'all'} · +{found} ips · {ping['healthy']}/{ping['probed']} ping")
    return {'success':True,'results':results,'found':found,'probed':len(probed),'synced':synced,'ping':ping,
            'geo':geo_result,'providers':edge_sources.provider_summary(),
            'nodes':[_node_payload(n) for n in list_nodes()]}

@app.get('/api/edge/ips')
def edge_ips(request:Request,provider:str='',limit:int=200):
    auth(request)
    return {'ips':edge_sources.ips(provider,min(max(limit,1),500)),'providers':edge_sources.provider_summary()}

@app.post('/api/edge/ips')
async def edge_add_ips(request:Request):
    """Add hand-written clean IPs (or CIDRs) to the pool and publish them."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    values=body.get('ips') if body.get('ips') is not None else body.get('value')
    if values is None: raise HTTPException(400,'فیلد ips لازم است')
    provider_id=str(body.get('provider') or edge_sources.MANUAL_PROVIDER).strip().lower()
    if not edge_sources.provider(provider_id): raise HTTPException(400,'provider ناشناخته است')
    result=edge_sources.add_ips(values,provider_id)
    if not result['added']:
        raise HTTPException(400,'هیچ آی‌پی معتبری پیدا نشد' if result['skipped'] else 'چیزی برای افزودن نبود')
    probed=await probe_all(limit=settings.cf_probe_limit,provider_id=provider_id)
    synced=_edge_sync(request)
    ping=await _ping_edge_nodes(provider_id=provider_id,timeout=2.5,limit=40)
    _audit('edge.ips',f"+{len(result['added'])} · {provider_id} · {ping['healthy']}/{ping['probed']} ping")
    return {'success':True,'added':result['added'],'skipped':result['skipped'],'probed':len(probed),
            'synced':synced,'ping':ping,'ips':edge_sources.ips(provider_id,200)}

@app.delete('/api/edge/ips/{ip}')
def edge_delete_ip(request:Request,ip:str):
    auth(request); edge_sources.remove_ip(ip); synced=_edge_sync(request)
    _audit('edge.ip.remove',ip)
    return {'success':True,'synced':synced,'providers':edge_sources.provider_summary()}

@app.post('/api/edge/sources')
async def edge_save_source(request:Request):
    """Create or update one location: a clean-IP provider, a manual list, a domain."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    # An admin who corrects a location by hand means it: auto-aligning the label
    # to the measured country would silently undo the correction on the next
    # pass, so an explicit change pins this location.
    existing=next((item for item in edge_sources.sources()
                   if item['id']==str(body.get('id') or '').strip()),None)
    pinned=str(body.get('location') or '').strip().lower()
    if (existing and pinned and 'autolabel' not in body
            and pinned!=str(existing.get('location') or '').strip().lower()):
        body['autolabel']=0
    try:
        source,items=edge_sources.save_source(body,str(body.get('id') or '').strip())
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    del items  # the enriched list below is what the panel renders
    if source.get('ips'):
        edge_sources.add_ips(source['ips'],source.get('provider') or edge_sources.MANUAL_PROVIDER)
    await probe_all(limit=settings.cf_probe_limit)
    geo_result=await _edge_geo(limit=4)
    synced=_edge_sync(request)
    # The location is measured in the same request, so the panel (and the toast)
    # can say how many of its addresses really answer instead of leaving it at
    # «اندازه‌گیری نشده» until the next ping loop pass.
    ping=await _ping_edge_nodes(source_id=source['id'],timeout=2.5,limit=48)
    items=edge_sources.status()['sources']
    _audit('edge.source.save',f"{source['id']} · {source['kind']} · {ping['healthy']}/{ping['probed']} ping")
    return {'success':True,'source':next((s for s in items if s['id']==source['id']),source),
            'sources':items,'synced':synced,'ping':ping,'geo':geo_result,
            'nodes':[_node_payload(n) for n in list_nodes()]}

@app.post('/api/edge/sources/{source_id}/ping')
async def edge_ping_source(request:Request,source_id:str):
    """Ping one location — «لوکیشن‌ها پینگ نمی‌دهند» answered with real numbers.

    Syncs first (so the nodes exist), then probes exactly this location's nodes
    the way a client would: TLS handshake against the node's own Host/SNI.
    """
    auth(request)
    current=next((item for item in edge_sources.sources() if item['id']==source_id),None)
    if not current: raise HTTPException(404,'منبع پیدا نشد')
    geo_result=await _edge_geo(limit=6)
    synced=_edge_sync(request)
    ping=await _ping_edge_nodes(source_id=source_id,timeout=4.0,limit=60)
    _audit('edge.source.ping',f"{source_id} · {ping['healthy']}/{ping['probed']} reachable")
    items=edge_sources.status()['sources']
    return {'success':True,'synced':synced,**ping,'geo':geo_result,
            'source':next((s for s in items if s['id']==source_id),current),'sources':items,
            'nodes':[_node_payload(n) for n in list_nodes()],
            'providers':edge_sources.provider_summary()}

@app.delete('/api/edge/sources/{source_id}')
def edge_delete_source(request:Request,source_id:str):
    auth(request); edge_sources.delete_source(source_id); synced=_edge_sync(request)
    _audit('edge.source.delete',source_id)
    return {'success':True,'sources':edge_sources.status()['sources'],'synced':synced,
            'nodes':[_node_payload(n) for n in list_nodes()]}

@app.post('/api/edge/sources/{source_id}/toggle')
async def edge_toggle_source(request:Request,source_id:str):
    auth(request)
    current=next((item for item in edge_sources.sources() if item['id']==source_id),None)
    if not current: raise HTTPException(404,'منبع پیدا نشد')
    source,items=edge_sources.save_source({'enabled':0 if current.get('enabled',1) else 1},source_id)
    synced=_edge_sync(request)
    _audit('edge.source.toggle',f"{source_id} · {'on' if source['enabled'] else 'off'}")
    return {'success':True,'source':source,'sources':items,'synced':synced,
            'nodes':[_node_payload(n) for n in list_nodes()]}

def _worker_steps():
    """The Worker deployment guide, worded for the platform this runs on."""
    where=runtime.label()
    return [
        f'کد زیر را کپی یا دانلود کنید؛ آدرس همین سرویس ({where}) از قبل داخلش قرار گرفته است.',
        'در Cloudflare → Workers & Pages → Create Worker کد را جایگزین و Deploy کنید.',
        f'اختیاری: در Settings → Variables متغیری با نام NEXUS_ORIGIN و مقدار آدرس {where} بسازید.',
        'آدرس Worker را در فیلد همین بخش ذخیره کنید تا لوکیشن کلودفلر ساخته و پینگ شود.',
        'روی «پینگ همه نودها» بزنید؛ نودها به ترتیب کمترین پینگ در سابلینک‌ها می‌آیند.',
        'برای لوکیشن‌های بیشتر (Fastly، آروان، دامنهٔ تمیز و…) از بخش «منابع لبه و لوکیشن‌ها» استفاده کنید.',
    ]

def _worker_source(request:Request,origin=None):
    with open(WORKER_PATH,'r',encoding='utf-8') as fh: source=fh.read()
    resolved=(origin or public_base(request)).rstrip('/')
    # The copy ships with this deployment's Railway origin already filled in, so
    # the file can be pasted into the Cloudflare dashboard as-is.
    return source.replace("const ORIGIN_FALLBACK = '';",f"const ORIGIN_FALLBACK = '{resolved}';")

@app.get('/api/cloudflare/worker-code')
def cloudflare_worker_code(request:Request):
    """Worker source prefilled with this deployment's Railway origin."""
    auth(request)
    base=public_base(request)
    return {'code':_worker_source(request,base),'filename':'nexus-worker.js','origin':base,
            'configured_url':_setting('cloudflare_worker_url') or '','steps':_worker_steps(),
            'runtime':runtime.info()}

@app.get('/api/cloudflare/worker-download')
def cloudflare_worker_download(request:Request):
    auth(request)
    headers={'Content-Disposition':'attachment; filename="nexus-worker.js"','Cache-Control':'no-store'}
    return PlainTextResponse(_worker_source(request),media_type='text/javascript; charset=utf-8',headers=headers)

@app.post('/api/cloudflare/worker-test')
async def cloudflare_worker_test(request:Request):
    """Call the Worker's /health endpoint so the panel can prove it is live.

    ``?probe=1`` makes the Worker also dial the Railway origin, so one click
    proves the Worker, the origin URL and the request route together.
    """
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    url=str(body.get('url') or _setting('cloudflare_worker_url') or '').strip().rstrip('/')
    if not url.startswith('https://'): raise HTTPException(400,'Worker URL must be a valid HTTPS URL')
    from urllib.request import Request as UrlRequest, urlopen, HTTPError
    def call():
        started=time.perf_counter()
        target=url.split('?',1)[0]+'/health?probe=1'
        try:
            with urlopen(UrlRequest(target,headers={'User-Agent':'NEXUS-Panel/1.0','Accept':'application/json'}),timeout=10) as resp:
                return resp.status,resp.read().decode('utf-8','replace'),round((time.perf_counter()-started)*1000,1)
        except HTTPError as exc:
            # 503 with a JSON body is a usable answer (origin not configured /
            # origin down), so surface it instead of a bare failure.
            return exc.code,exc.read().decode('utf-8','replace'),round((time.perf_counter()-started)*1000,1)
    try: status,text,latency=await asyncio.to_thread(call)
    except Exception as exc:
        return {'ok':False,'detail':type(exc).__name__,'url':url,'health':{}}
    try: parsed=json.loads(text)
    except Exception: parsed={'raw':text[:400]}
    probe=parsed.get('origin_probe') if isinstance(parsed,dict) else None
    ok=200<=status<300 and (not probe or probe.get('reachable'))
    return {'ok':ok,'status':status,'latency_ms':latency,'url':url,'health':parsed,'origin_probe':probe}


@app.get('/api/core/status')
def core_status(request:Request):
    auth(request)
    profiles=transports.available_profiles()
    return {**xray.status(),
            'transport':'WebSocket edge + Xray-core (Reality on the direct port)',
            'protocols':sorted({p['protocol'].upper() for p in profiles}),
            'endpoints':[p['path'] for p in profiles if p['network']=='ws'],
            'profiles':[{'id':p['id'],'tag':p['tag'],'protocol':p['protocol'],'network':p['network'],'group':p['group']} for p in profiles],
            'planned':transports.catalog()['planned'],
            'features':['Xray protocol engine','VLESS · VMess · Trojan · Shadowsocks over WebSocket','Reality on a direct TCP port','live user config reload','Railway TLS/WebSocket edge']}

@app.get('/api/client-config/{username}')
def client_config(request:Request,username:str,target:str='singbox'):
    auth(request); u=get_user(username)
    if not u: raise HTTPException(404,'user not found')
    ok,reason=allowed(u)
    if not ok: raise HTTPException(403,reason)
    return {'username':username,'target':target,'subscription':public_base(request)+'/sub/'+urllib.parse.quote(u['uuid'],safe='')+'?target='+urllib.parse.quote(target),'nodes':active_nodes()}

@app.get('/api/metrics')
def metrics(request:Request,hours:int=24):
    """Single payload that drives every dashboard chart and counter."""
    auth(request)
    now=int(time.time()); window=min(max(hours,1),168)
    users=list_users(); nodes=list_nodes()
    def bucket(items,size):
        acc={}
        for e in items:
            slot=int(e['created_at'] or 0)//size*size
            b=acc.setdefault(slot,{'bytes':0,'requests':0})
            b['bytes']+=int(e['bytes'] or 0); b['requests']+=int(e['requests'] or 0)
        return [{'t':k,'gb':round(v['bytes']/1_000_000_000,4),'mb':round(v['bytes']/1_000_000,3),'requests':v['requests']} for k,v in sorted(acc.items())]
    hourly=bucket(rows('SELECT bytes,requests,created_at FROM traffic_events WHERE created_at>=?',(now-window*3600,)),3600)
    daily=bucket(rows('SELECT bytes,requests,created_at FROM traffic_events WHERE created_at>=?',(now-7*86400,)),86400)
    protocols={}
    for u in users:
        key=u.get('protocol') or 'vless'; protocols[key]=protocols.get(key,0)+1
    top=sorted(users,key=lambda u: float(u.get('used_gb') or 0),reverse=True)[:8]
    connected=rows('SELECT COUNT(DISTINCT ip) AS n FROM traffic_events WHERE ip IS NOT NULL AND created_at>=?',(now-3600,))
    return {
        'server_time':now,
        'server_timezone':time.strftime('%Z'),
        'uptime_seconds':int(now-_started),
        'started_at':int(_started),
        'window_hours':window,
        'totals':{
            'users':len(users),
            'active_users':sum(1 for u in users if u['is_active']),
            'disabled_users':sum(1 for u in users if not u['is_active']),
            'used_gb':round(sum(float(u.get('used_gb') or 0) for u in users),4),
            'lifetime_gb':round(sum(float(u.get('lifetime_used_gb') or 0) for u in users),4),
            'requests':int(sum(int(u.get('used_req') or 0) for u in users)),
            'nodes':len(nodes),
            'nodes_enabled':sum(1 for n in nodes if n['enabled']),
            'railway_nodes':sum(1 for n in nodes if n['kind']=='railway'),
            'origin_nodes':sum(1 for n in nodes if n['kind']=='railway'),
            'cloudflare_nodes':sum(1 for n in nodes if n['kind']=='cloudflare'),
            'edge_domain_nodes':sum(1 for n in nodes if n['kind']=='edge'),
            'locations':sorted({transports.node_location(n) for n in nodes if transports.node_location(n)}),
            'cf_ips_total':len(rows('SELECT ip FROM cf_ips')),
            'cf_ips_ok':len(rows('SELECT ip FROM cf_ips WHERE ok=1')),
            'proxies':len(list_proxies()),
            'active_ips_1h':int((connected[0] or {}).get('n') or 0) if connected else 0,
        },
        'series_hourly':hourly,
        'series_daily':daily,
        'protocols':[{'name':k,'count':v} for k,v in protocols.items()],
        'nodes':[{'name':n['name'],'kind':n['kind'],'server':n['server'],'port':n['port'],'latency_ms':n['latency_ms'],'enabled':bool(n['enabled']),
                   'location':transports.node_location(n),'provider':transports.node_provider(n)} for n in nodes],
        'locations':sorted({transports.node_location(n) for n in nodes if transports.node_location(n)}),
        'top_users':[{'username':u['username'],'protocol':u.get('protocol'),'protocol_label':_user_payload(u)['protocol_label'],'used_gb':round(float(u.get('used_gb') or 0),4),'limit_gb':u.get('limit_gb'),'is_active':bool(u['is_active'])} for u in top],
    }


@app.get('/api/users/{username}/links')
def user_links(request:Request,username:str,node:str=''):
    """Every subscription URL and per-node link combination for one user."""
    auth(request); u=get_user(username)
    if not u: raise HTTPException(404,'user not found')
    ok,reason=allowed(u)
    base=public_base(request); prefix=_sub_prefix()
    token=urllib.parse.quote(u['uuid'],safe='')
    nodes=_sub_nodes(node) or active_nodes()
    def sub(target,name='',location=''):
        url=f"{base}/sub/{token}?target={urllib.parse.quote(normalize_target(target))}"
        if name: url+='&node='+urllib.parse.quote(str(name))
        if location: url+='&location='+urllib.parse.quote(str(location))
        return url
    protocol_set=transports.user_protocols(u)
    # Multi-location: one subscription per edge source (location), so a user who
    # only wants the German edge (or only the Iranian one) gets exactly that.
    catalog_nodes=active_nodes()
    locations=sorted({transports.node_location(n) for n in catalog_nodes if transports.node_location(n)})
    items=[]
    for n in nodes:
        item=node_links(u,n,prefix)
        # A protocol link is only offered when that protocol is live for this
        # user; the format links (base64/singbox/clash/xray) always are.
        item['subscriptions']=[{'target':t,'label':_target_label(t),'url':sub(t,item['name'])}
                               for t in ('vless','trojan','vmess','base64','singbox','clash','xray')
                               if t not in transports.PROTOCOLS or t in protocol_set]
        # One subscription per published transport for this single node.
        item['transport_subscriptions']=[{'target':p['id'],'label':p['tag'],'url':sub(p['id'],item['name'])}
                                         for p in transports.available_profiles(protocol_set)]
        item['subscription']=sub(_user_target(u),item['name'])
        item['subscription_all']=sub('all',item['name'])
        item['clients']=client_links(base,token,item['name'],{})
        items.append(item)
    enabled_protocols=transports.user_protocols(u)
    payload=_user_payload(u)
    return {
        'username':u['username'],'uuid':u['uuid'],'protocol':u.get('protocol'),
        'protocols':payload['protocols'],'protocol_label':payload['protocol_label'],
        'is_active':bool(u['is_active']),'allowed':ok,'reason':reason,
        'limit_gb':u.get('limit_gb'),'used_gb':round(float(u.get('used_gb') or 0),4),
        'used_req':int(u.get('used_req') or 0),'expires_at':u.get('expires_at'),
        'base_url':base,'node_count':len(items),
        'portal_url':_portal_url(base,token),
        'subscription':sub(_user_target(u)),
        'smart_url':sub('auto'),
        # Only the targets this user can actually open (see _target_offerable):
        # «vmess» for a vless+trojan user, or Reality/WARP on a deployment that
        # does not publish them, used to be listed and answered 400 on tap.
        'subscriptions':[{'target':t,'label':_target_label(t),'url':sub(t)} for t in SUB_TARGETS
                         if _target_offerable(t,protocol_set)],
        'protocol_set':sorted(enabled_protocols,key=transports.PROTOCOLS.index),
        'locations':locations,
        'location_subscriptions':[{'location':loc,'label':f'{loc.upper()} · فقط همین لوکیشن',
                                   'url':sub(_user_target(u),'',loc),
                                   'nodes':sum(1 for n in catalog_nodes if transports.node_location(n)==loc)} for loc in locations],
        'runtime':runtime.info(),
        'transports':_transport_targets(enabled_protocols),
        'profiles':profiles_for('auto',enabled_protocols),
        'clients':client_links(base,token,'',None),
        'nodes':items,
    }


@app.put('/api/nodes/{name}')
async def update_node(request:Request,name:str):
    auth(request)
    existing=row('SELECT * FROM nodes WHERE name=?',(name,))
    if not existing: raise HTTPException(404,'node not found')
    b=await request.json(); sets=[]; vals=[]
    if b.get('kind') is not None and str(b['kind']) not in {'railway','cloudflare','edge'}: raise HTTPException(400,'unsupported node kind')
    for key in ('kind','server','sni','host'):
        if key in b: sets.append(key+'=?'); vals.append(b[key])
    for key in ('port','tls','enabled'):
        if key in b: sets.append(key+'=?'); vals.append(int(b[key]))
    if sets:
        sets.append('updated_at=?'); vals.append(int(time.time())); vals.append(name)
        execute('UPDATE nodes SET '+','.join(sets)+' WHERE name=?',vals)
        _audit('node.update',name)
    return {'success':True,'node':row('SELECT * FROM nodes WHERE name=?',(name,))}


@app.delete('/api/nodes/{name}')
def delete_node(request:Request,name:str):
    auth(request); execute('DELETE FROM nodes WHERE name=?',(name,)); _audit('node.delete',name)
    return {'success':True,'nodes':list_nodes()}


@app.get('/api/settings')
def get_settings(request:Request):
    auth(request)
    worker=_setting('cloudflare_worker_url') or ''
    defaults={'protocol':_setting('default_protocol') or 'vless','limit_gb':_setting('default_limit_gb') or '','expiry_days':_setting('default_expiry_days') or '','ip_limit':_setting('default_ip_limit') or ''}
    payload={k:(_setting(k) or '') for k in GENERAL_SETTING_KEYS}
    payload['public_base_url']=_setting('public_base_url') or ''
    payload['resolved_base_url']=public_base(request)
    payload['defaults']=defaults
    payload['worker']={'url':worker,'configured':bool(worker)}
    payload['subscription']={'targets':SUB_TARGETS,'prefix':_sub_prefix(),'node_count':len(active_nodes()),
        'protocols':sorted({p['protocol'].upper() for p in transports.available_profiles()}),
        'transport':'WebSocket edge + Reality','transports':_transport_targets(),
        'protocol_catalog':_protocol_catalog()}
    payload['protocols']=_protocol_catalog()
    payload['clients']=client_catalog()
    payload['brand']=_brand()
    payload['pwa']={'manifest':'/manifest.webmanifest','service_worker':'/sw.js','icons':PWA_ICONS,
                    'installable':True,'name':_brand()['app_name'],'standalone':'display: standalone'}
    payload['security']={'session_ttl':settings.session_ttl,'session_days':_session_days(),'remember_supported':True,
                         'password_from_env':bool(settings.admin_password and settings.admin_password!='admin'),
                         'bootstrap_complete':_setting('bootstrap_complete')=='1'}
    return payload


@app.post('/api/settings')
async def save_settings(request:Request):
    auth(request); b=await request.json(); changed=[]
    if 'public_base_url' in b:
        url=str(b.get('public_base_url') or '').strip()
        if url and not (url.startswith(('http://','https://')) and urllib.parse.urlparse(url).hostname):
            raise HTTPException(400,'Base URL must be a valid http(s) URL')
        _set('public_base_url',url); changed.append('public_base_url')
    if 'default_protocol' in b:
        # `all` or a comma-separated set, so the default can be the full matrix.
        value=str(b['default_protocol'] or 'all').strip().lower()
        if value!='all' and not re.match(PROTOCOL_LIST,value):
            raise HTTPException(400,'default_protocol must be all or a comma-separated list of vless, vmess, trojan, ss')
        b=dict(b,default_protocol=value)
    for key in ('sub_prefix','default_protocol','default_limit_gb','default_expiry_days','default_ip_limit'):
        if key in b: _set(key,str(b[key]).strip()); changed.append(key)
    # Presentation + session lifetime: validated so a bad value cannot lock the
    # panel out or break the theme on the next load.
    for key,low,high in (('session_days',1,365),('ping_interval',1,1440)):
        if key in b:
            raw=str(b[key]).strip()
            if raw:
                try: value=int(float(raw))
                except ValueError: raise HTTPException(400,f'{key} must be a number')
                if not low<=value<=high: raise HTTPException(400,f'{key} must be between {low} and {high}')
                _set(key,str(value))
            else: _set(key,'')
            changed.append(key)
    if 'app_name' in b:
        name=str(b['app_name'] or '').strip()[:24]
        _set('app_name',name); changed.append('app_name')
    for key in ('accent','accent_secondary'):
        if key in b:
            value=str(b[key] or '').strip()
            if value and not re.match(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$',value):
                raise HTTPException(400,f'{key} must be a hex colour like #5ad1ff')
            _set(key,value); changed.append(key)
    if changed: _audit('settings.update',','.join(changed))
    return {'success':True,'changed':changed,'settings':get_settings(request)}


@app.post('/api/settings/clients')
async def save_client_links(request:Request):
    """Override the per-client download links (stores and release pages move)."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    if not isinstance(body,dict): raise HTTPException(400,'invalid payload')
    if body.get('reset'):
        execute('DELETE FROM settings WHERE key=?',('client_links',))
        _audit('settings.clients','reset to defaults')
        return {'success':True,'clients':client_catalog()}
    known={c['id'] for c in CLIENTS}; links={}
    for key,value in body.items():
        url=str(value or '').strip()
        if not url: continue
        if key not in known: raise HTTPException(400,f'unknown client: {key}')
        if not url.startswith(('http://','https://')) or not urllib.parse.urlparse(url).hostname:
            raise HTTPException(400,f'invalid URL for {key}')
        links[key]=url
    merged=download_overrides(); merged.update(links)
    _set('client_links',json.dumps(merged,ensure_ascii=False))
    _audit('settings.clients',','.join(sorted(links)) or 'unchanged')
    return {'success':True,'clients':client_catalog()}

@app.post('/api/settings/password')
async def change_password(request:Request):
    auth(request); b=await request.json()
    current=str(b.get('current') or ''); new=str(b.get('new') or '')
    admin=_setting('admin_password') or settings.admin_password
    if not admin or not secrets.compare_digest(current,admin): raise HTTPException(403,'current password is incorrect')
    if len(new)<8: raise HTTPException(400,'new password must be at least 8 characters')
    if secrets.compare_digest(new,current): raise HTTPException(400,'new password must differ from the current password')
    _set('admin_password',new); _audit('settings.password','admin password rotated')
    return {'success':True}


@app.post('/api/settings/rotate-session')
def rotate_session(request:Request):
    auth(request); _set('jwt_secret',secrets.token_urlsafe(48)); _audit('settings.session','session secret rotated')
    return sessions.clear(JSONResponse({'success':True,'signed_out':True}))


@app.post('/api/settings/rotate-shadowsocks')
async def rotate_shadowsocks(request:Request):
    """Rotate every Shadowsocks key and republish the engine.

    Shadowsocks authenticates one PSK per cipher (see ``transports.SS_CIPHERS``),
    so rotating is the only way to revoke access handed out on a link that has
    already been copied. Every other protocol keeps its per-user credential and
    is therefore revoked simply by disabling the user.
    """
    auth(request)
    rotated=transports.rotate_ss_keys()
    engine=await xray.start_or_reload(force=True)
    _audit('settings.shadowsocks','rotated '+','.join(rotated) if rotated else 'nothing rotated')
    return {'success':True,'rotated':rotated,'engine':engine,'transports':transports.catalog()}


@app.delete('/api/logs')
def clear_logs(request:Request):
    auth(request); execute('DELETE FROM audit_logs'); _audit('logs.clear','audit log cleared')
    return {'success':True}


PWA_SHORTCUTS=[{'name':'کاربران','short_name':'کاربران','url':'/#users'},
               {'name':'نودها','short_name':'نودها','url':'/#nodes'},
               {'name':'Cloudflare','short_name':'Edge','url':'/#cloudflare'}]

@app.get('/manifest.webmanifest')
def manifest():
    """Installable PWA manifest, generated from the panel's own brand settings."""
    brand=_brand()
    icons=PWA_ICONS
    body={'name':f"{brand['app_name']} • مرکز کنترل",'short_name':brand['app_name'][:12] or 'NEXUS',
          'description':'مرکز کنترل کاربران، نودها و سابلینک‌های NEXUS روی Railway و Cloudflare.',
          'id':'/','start_url':'/?source=pwa','scope':'/','display':'standalone','display_override':['standalone','minimal-ui'],
          'orientation':'any','background_color':'#05080f','theme_color':'#05080f','lang':'fa','dir':'rtl',
          'categories':['productivity','utilities'],
          'icons':[{'src':icons['192'],'sizes':'192x192','type':'image/png','purpose':'any'},
                   {'src':icons['512'],'sizes':'512x512','type':'image/png','purpose':'any'},
                   {'src':icons['maskable'],'sizes':'512x512','type':'image/png','purpose':'maskable'},
                   {'src':icons['logo'],'sizes':'any','type':'image/svg+xml','purpose':'any'}],
          'shortcuts':PWA_SHORTCUTS}
    return JSONResponse(body,media_type='application/manifest+json',headers={'Cache-Control':'no-store'})

@app.get('/sw.js')
def service_worker():
    """Served from the root so the worker's scope can cover the whole panel.

    The build token is injected here, which is what makes the browser install a
    fresh worker (and drop the old cache) after every deploy.
    """
    with open(os.path.join(BASE_DIR,'static','sw.js'),'r',encoding='utf-8') as fh: source=fh.read()
    source=source.replace("const VERSION = 'nexus-dev';",f"const VERSION = 'nexus-{BUILD_TOKEN}';",1)
    return Response(source,media_type='text/javascript',
                    headers={'Service-Worker-Allowed':'/','Cache-Control':'no-cache','X-Nexus-Build':BUILD_TOKEN})

@app.get('/api/version')
def version(): return {'version':APP_VERSION,'build':BUILD_TOKEN,'python':os.sys.version.split()[0]}

@app.exception_handler(Exception)
async def unhandled_error(request:Request,exc:Exception):
    """Never answer a crashed route with an empty body the panel cannot explain."""
    if request.url.path.startswith('/api/'):
        return JSONResponse({'detail':f'خطای داخلی سرور ({type(exc).__name__})'},status_code=500)
    return PlainTextResponse('NEXUS internal error: '+type(exc).__name__,status_code=500)
