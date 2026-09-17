import asyncio, json, os, re, secrets, time, urllib.parse
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import jwt
from app.config import settings
from app.db import init_db, row, rows, execute
from app.core.models import UserCreate, TrafficEvent
from app.users.service import list_users, get_user, get_by_token, create_user, delete_user, toggle_user, reset_user, track, allowed, reset_due
from app.subscriptions.generator import render, active_nodes, node_links, normalize_target, TARGETS as SUB_TARGETS
from app.subscriptions.clients import (CLIENTS, PRESETS, FORMAT_LABELS, DEFAULT_PRESET, catalog as client_catalog,
    client_links, download_overrides, preset as get_preset, subscription_url as client_subscription_url)
from app.proxy.manager import add as add_proxy, list_all as list_proxies, check as check_proxy
from app.dns.service import doh
from app.services.backup import export_all
from app.cloudflare.monitor import seed_ips, probe_all, best, loop as cf_loop
from app.nodes import (ensure as ensure_nodes, list_nodes, upsert as upsert_node, sync_from_sources,
    ping_all as ping_all_nodes, ping_loop, ensure_origin_node, catalog as node_catalog, auto_sync)
from app.core.settings_store import store
from app.core.security import SessionManager, LoginThrottle
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
GENERAL_SETTING_KEYS=('public_base_url','sub_prefix','default_protocol','default_limit_gb','default_expiry_days','default_ip_limit','session_days','ping_interval','accent','accent_secondary','app_name')
PWA_ICONS={'192':'/static/icons/icon-192.png','512':'/static/icons/icon-512.png','maskable':'/static/icons/icon-maskable-512.png','apple':'/static/icons/apple-touch-icon.png','favicon':'/static/icons/favicon-32.png','logo':'/static/icons/nexus.svg'}
# One label per accepted target: subscription formats plus client ids.
SUB_LABELS={**FORMAT_LABELS, **{c['id']:f"{c['name']} · {c['platform']}" for c in CLIENTS}}
WORKER_PATH=os.path.join(BASE_DIR,'cloudflare-worker','worker.js')
REASON_LABELS={'ok':'فعال','disabled':'غیرفعال','expired':'منقضی‌شده','quota':'حجم تمام شده','requests':'سقف درخواست'}

def _setting(key):
    return store.get(key)

def _set(key,value):
    return store.set(key,value)

def _audit(action,detail=''):
    audit.record(action,detail)

def _brand():
    """Panel identity: also feeds the PWA manifest so the icon name matches."""
    return {'app_name':(_setting('app_name') or 'NEXUS').strip()[:24] or 'NEXUS',
            'accent':(_setting('accent') or '#5ad1ff').strip(),
            'accent_secondary':(_setting('accent_secondary') or '#8b7bff').strip(),
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
        base=(_setting('public_base_url') or settings.public_base_url or '').strip() or None
        worker=(_setting('cloudflare_worker_url') or '').strip() or None
        await auto_sync(base, worker)
    except Exception:
        pass

@asynccontextmanager
async def lifespan(app:FastAPI):
    init_db(); bootstrap(); ensure_nodes(); bootstrap_nodes()
    await xray.start_or_reload(force=True)
    task=asyncio.create_task(maintenance()); cf_task=asyncio.create_task(cf_loop(settings.cf_probe_interval)); xray_task=asyncio.create_task(xray.loop()); ping_task=asyncio.create_task(ping_loop(settings.cf_probe_interval,_ping_interval)); auto_task=asyncio.create_task(bootstrap_nodes_full()); cat_task=asyncio.create_task(catalog_loop())
    try: yield
    finally:
        task.cancel(); cf_task.cancel(); xray_task.cancel(); ping_task.cancel(); auto_task.cancel(); cat_task.cancel()
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
        try: await xray._stop()
        except Exception: pass
app=FastAPI(title=settings.app_name,version='7.0.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[],allow_methods=['GET','POST','PUT','DELETE','OPTIONS'],allow_headers=['Content-Type','X-Admin-Password'])
templates=Jinja2Templates(directory=os.path.join(BASE_DIR,'templates'))
app.mount('/static',StaticFiles(directory=os.path.join(BASE_DIR,'static')),name='static')
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
    ip=request.client.host if request.client else 'unknown'
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
def users(request:Request): auth(request); return list_users()
@app.post('/api/users')
def create(request:Request,m:UserCreate):
    auth(request)
    try: u=create_user(m)
    except Exception as e: raise HTTPException(400,str(e))
    _audit('user.create',f"{m.username} ({m.protocol})")
    return u
def _portal_url(base,token): return f"{base}/portal/{urllib.parse.quote(str(token),safe='')}"

def _preset_summary(fields):
    """Human-readable list of what a preset actually applied."""
    bits=[]
    bits.append(f"فرگمنت {fields['frag_len']} / {fields.get('frag_int') or '۱۰'}" if fields.get('frag_len') else 'بدون فرگمنت')
    bits.append('حجم نامحدود' if not fields.get('limit_gb') else f"{float(fields['limit_gb']):g} گیگ")
    bits.append('بدون انقضا' if not fields.get('expiry_days') else f"{int(fields['expiry_days'])} روز")
    bits.append(f"{fields.get('ip_limit') or '∞'} دستگاه همزمان")
    if fields.get('block_ads'): bits.append('بلاک تبلیغات')
    return bits

def _share_payload(request:Request,u,node=''):
    """Everything an end user needs: status window, per-client subs, formats."""
    base=public_base(request); token=urllib.parse.quote(u['uuid'],safe='')
    return {
        'base_url':base,
        'portal_url':_portal_url(base,token),
        'smart_url':client_subscription_url(base,token,'auto',node),
        'clients':client_links(base,token,node,None),
        'targets':[{'target':t,'label':SUB_LABELS.get(t,t),'url':client_subscription_url(base,token,t,node)} for t in SUB_TARGETS],
    }

@app.get('/api/presets')
def get_presets(request:Request):
    """Ready-made "best settings" bundles offered by the quick-create button."""
    auth(request)
    return {'default':DEFAULT_PRESET,'presets':PRESETS,'defaults':{
        'protocol':_setting('default_protocol') or 'vless',
        'limit_gb':_setting('default_limit_gb') or '',
        'expiry_days':_setting('default_expiry_days') or '',
        'ip_limit':_setting('default_ip_limit') or ''}}

@app.get('/api/clients')
def get_clients(request:Request):
    """Client catalog: import format, download link and notes."""
    auth(request)
    return {'clients':client_catalog(),'targets':SUB_TARGETS,'labels':SUB_LABELS,'base_url':public_base(request)}

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
    if (_setting('default_protocol') or '') in {'vless','trojan'}: fields['protocol']=_setting('default_protocol')
    for key,setting_key in (('limit_gb','default_limit_gb'),('expiry_days','default_expiry_days'),('ip_limit','default_ip_limit')):
        configured=(_setting(setting_key) or '').strip()
        if configured:
            try: fields[key]=float(configured) if key=='limit_gb' else int(float(configured))
            except ValueError: pass
    for key in list(fields.keys()):
        if key in body and body[key] is not None: fields[key]=body[key]
    username=str(body.get('username') or '').strip() or _quick_username()
    if get_user(username): raise HTTPException(400,'username already exists')
    try: m=UserCreate(username=username,**fields)
    except Exception as e: raise HTTPException(400,f'invalid settings: {e}')
    try: u=create_user(m)
    except Exception as e: raise HTTPException(400,str(e))
    _audit('user.quick',f"{username} · {chosen['id']}")
    return {'success':True,'preset':chosen['id'],'preset_name':chosen['name'],'applied':_preset_summary(fields),'user':u,**_share_payload(request,u)}
@app.get('/api/users/{username}')
def user_detail(request:Request,username:str):
    auth(request); u=get_user(username)
    if not u: raise HTTPException(404,'user not found')
    return u
@app.put('/api/users/{username}')
def update(request:Request,username:str,body:dict):
    auth(request); u=get_user(username)
    if not u: raise HTTPException(404,'user not found')
    if body.get('toggle_only'): return toggle_user(username)
    if body.get('reset_action'): return reset_user(username,body['reset_action'])
    allowed_keys={'limit_gb','expiry_days','limit_req','ip_limit','is_active','ips','port','sni','host','fingerprint','tls','user_proxy','frag_len','frag_int','advanced_frag','cipher_suites','tls_mask','block_ads','block_porn','auto_rotate_ip','rotate_time','ip_operator','ip_count'}
    sets=[]; vals=[]
    for k,v in body.items():
        if k in allowed_keys: sets.append(k+'=?'); vals.append(v)
    if sets: vals.append(username); execute('UPDATE users SET '+','.join(sets)+' WHERE username=?',vals)
    return get_user(username)
@app.delete('/api/users/{username}')
def delete(request:Request,username:str):
    auth(request); removed=bool(delete_user(username)); _audit('user.delete',username); return {'success':removed}
@app.post('/api/traffic/{username}')
def traffic(request:Request,username:str,e:TrafficEvent):
    auth(request); result=track(username,e.bytes,e.requests,e.ip)
    if not result: raise HTTPException(404,'user not found')
    return result
def _sub_nodes(node:str=''):
    items=active_nodes()
    if node:
        wanted={x.strip().lower() for x in str(node).split(',') if x.strip()}
        items=[n for n in items if str(n['name']).lower() in wanted]
    return items

@app.get('/sub/{token}')
def subscription(request:Request,token:str,target:str='auto',node:str=''):
    u=get_by_token(urllib.parse.unquote(token))
    if not u: raise HTTPException(404,'subscription not found')
    ok,reason=allowed(u)
    if not ok: raise HTTPException(403,reason)
    nodes=_sub_nodes(node)
    if not nodes: raise HTTPException(404,'no matching nodes')
    # Generate from the live Node Catalog on every request. This means a client
    # refresh automatically receives the current Railway + healthy Cloudflare nodes.
    try: text=render(u,public_base(request),target,nodes,_sub_prefix())
    except ValueError as e: raise HTTPException(400,str(e))
    headers={'Cache-Control':'no-store, max-age=0','X-Content-Type-Options':'nosniff','X-NEXUS-Node-Count':str(len(nodes)),'X-NEXUS-Target':normalize_target(target)}
    return PlainTextResponse(text,headers=headers)
@app.get('/sub/{token}/{node_name}')
def subscription_node(request:Request,token:str,node_name:str,target:str='auto'):
    # Per-node subscription: one catalog node per link makes client-side
    # grouping and failover trivial.
    return subscription(request,token,target,node_name)
@app.get('/feed/{token}')
def feed(request:Request,token:str,target:str='auto',node:str=''): return subscription(request,token,target,node)


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


async def _bridge_ws(ws: WebSocket, upstream_path: str):
    await ws.accept()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{settings.xray_vless_port if upstream_path == '/ws/vless' else settings.xray_trojan_port}{upstream_path}", max_size=None, ping_interval=20, ping_timeout=20) as upstream:
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


@app.websocket('/ws/vless')
async def vless_ws(ws: WebSocket):
    await _bridge_ws(ws, '/ws/vless')


@app.websocket('/ws/trojan')
async def trojan_ws(ws: WebSocket):
    await _bridge_ws(ws, '/ws/trojan')


@app.websocket('/ws')
async def legacy_ws(ws: WebSocket):
    # Backward-compatible endpoint: VLESS clients using the old /ws path are
    # forwarded to the Xray VLESS listener. New subscriptions use /ws/vless.
    await _bridge_ws(ws, '/ws/vless')


def _portal_data(request:Request,u):
    """Public payload behind the subscription status window (no admin auth)."""
    base=public_base(request); token=urllib.parse.quote(u['uuid'],safe='')
    ok,reason=allowed(u)
    now=int(time.time()); used=float(u.get('used_gb') or 0); limit=u.get('limit_gb')
    nodes=[]
    for n in (_sub_nodes('') or active_nodes()):
        try: meta=json.loads(n.get('metadata') or '{}')
        except Exception: meta={}
        if not isinstance(meta,dict): meta={}
        latency=n.get('latency_ms')
        nodes.append({
            'name':n['name'],'kind':n['kind'],'server':n['server'],'port':int(n.get('port') or 443),
            'latency_ms':latency,'online':bool(latency is not None and float(latency)>=0),'tls_ok':bool(meta.get('ping_tls')),
            'subscription':client_subscription_url(base,token,u.get('protocol') or 'vless',n['name']),
            'subscription_all':client_subscription_url(base,token,'all',n['name']),
        })
    return {
        'brand':'NEXUS','token':u['uuid'],'username':u['username'],'protocol':(u.get('protocol') or 'vless'),
        'is_active':bool(u['is_active']),'allowed':ok,'reason':reason,'reason_label':REASON_LABELS.get(reason,reason),
        'used_gb':round(used,3),'limit_gb':limit,'quota_pct':round(min(100.0,used/float(limit)*100),1) if limit else 0.0,
        'used_req':int(u.get('used_req') or 0),'limit_req':u.get('limit_req'),'ip_limit':u.get('ip_limit'),
        'expires_at':u.get('expires_at'),'start_on_first_connect':bool(u.get('start_on_first_connect')),
        'first_connection_time':u.get('first_connection_time'),
        'fragment':u.get('frag_len') or '','fragment_interval':u.get('frag_int') or '','fingerprint':u.get('fingerprint') or 'chrome',
        'server_time':now,'base_url':base,
        'portal_url':_portal_url(base,token),
        'smart_url':client_subscription_url(base,token,'auto'),
        'clients':client_links(base,token,'',None),
        'nodes':nodes,'nodes_total':len(nodes),'nodes_online':sum(1 for n in nodes if n['online']),
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
@app.post('/api/proxies')
async def proxy_create(request:Request):
    auth(request); b=await request.json()
    try: add_proxy(b['proxy'],b.get('country'))
    except Exception as e: raise HTTPException(400,str(e))
    return {'success':True}
@app.post('/api/test-proxy')
async def proxy_test(request:Request): auth(request); b=await request.json(); return check_proxy(b['proxy'])
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
    return {'success':True,'configured':bool(url),'nodes':list_nodes()}

@app.get('/api/nodes')
def nodes(request:Request):
    auth(request); _ensure_catalog(request); return list_nodes()

@app.post('/api/nodes/sync')
async def sync_nodes(request:Request):
    """Auto-rebuild: Worker host if configured, else a detected CF-fronted domain."""
    auth(request)
    result=await auto_sync(public_base(request), _setting('cloudflare_worker_url') or None, force_edge=True)
    _audit('nodes.sync',f"{result['synced']} nodes · edge {result['edge_host'] or '-'}")
    return {'success':True,'synced':result['synced'],'edge_host':result['edge_host'],'nodes':result['nodes']}

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
    if not name or not server or kind not in {'railway','cloudflare'}: raise HTTPException(400,'name, server and kind are required')
    upsert_node(name,kind,server,int(b.get('port',443)),bool(b.get('tls',True)),b.get('sni'),b.get('host'),kind,{})
    return {'success':True,'node':row('SELECT * FROM nodes WHERE name=?',(name,))}

@app.get('/api/cloudflare/ips')
def cloudflare_ips(request:Request,limit:int=50):
    auth(request); return rows('SELECT * FROM cf_ips ORDER BY CASE WHEN ok=1 THEN 0 ELSE 1 END, latency_ms ASC LIMIT ?', (min(max(limit,1),256),))

@app.post('/api/cloudflare/refresh')
async def cloudflare_refresh(request:Request):
    auth(request); count=seed_ips(settings.cf_probe_limit); results=await probe_all(limit=settings.cf_probe_limit); result=await auto_sync(public_base(request), _setting('cloudflare_worker_url') or None, force_edge=True); return {'seeded':count,'probed':len(results),'best':best(20),'edge_host':result['edge_host'],'nodes':result['nodes']}

WORKER_STEPS=[
    'کد زیر را کپی یا دانلود کنید؛ آدرس Railway شما از قبل داخلش قرار گرفته است.',
    'در Cloudflare → Workers & Pages → Create Worker کد را جایگزین و Deploy کنید.',
    'اختیاری: در Settings → Variables متغیری با نام NEXUS_ORIGIN و مقدار آدرس Railway بسازید.',
    'آدرس Worker را در فیلد همین بخش ذخیره کنید تا نودهای Cloudflare ساخته و پینگ شوند.',
    'روی «پینگ همه نودها» بزنید؛ نودها به ترتیب کمترین پینگ در سابلینک‌ها می‌آیند.',
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
            'configured_url':_setting('cloudflare_worker_url') or '','steps':WORKER_STEPS}

@app.get('/api/cloudflare/worker-download')
def cloudflare_worker_download(request:Request):
    auth(request)
    headers={'Content-Disposition':'attachment; filename="nexus-worker.js"','Cache-Control':'no-store'}
    return PlainTextResponse(_worker_source(request),media_type='text/javascript; charset=utf-8',headers=headers)

@app.post('/api/cloudflare/worker-test')
async def cloudflare_worker_test(request:Request):
    """Call the Worker's /health endpoint so the panel can prove it is live."""
    auth(request)
    try: body=await request.json()
    except Exception: body=None
    body=body if isinstance(body,dict) else {}
    url=str(body.get('url') or _setting('cloudflare_worker_url') or '').strip().rstrip('/')
    if not url.startswith('https://'): raise HTTPException(400,'Worker URL must be a valid HTTPS URL')
    from urllib.request import Request as UrlRequest, urlopen
    def call():
        started=time.perf_counter()
        with urlopen(UrlRequest(url+'/health',headers={'User-Agent':'NEXUS-Panel/1.0'}),timeout=8) as resp:
            return resp.status,resp.read().decode('utf-8','replace'),round((time.perf_counter()-started)*1000,1)
    try: status,text,latency=await asyncio.to_thread(call)
    except Exception as exc:
        return {'ok':False,'detail':type(exc).__name__,'url':url,'health':{}}
    try: parsed=json.loads(text)
    except Exception: parsed={'raw':text[:400]}
    return {'ok':200<=status<300,'status':status,'latency_ms':latency,'url':url,'health':parsed}

@app.get('/api/core/status')
def core_status(request:Request):
    auth(request)
    return {**xray.status(), 'transport':'WebSocket edge + Xray-core', 'protocols':['VLESS','Trojan'], 'endpoints':['/ws/vless','/ws/trojan'], 'features':['Xray protocol engine','UDP tunnelling via supported client transports','live user config reload','Railway TLS/WebSocket edge']}

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
            'cloudflare_nodes':sum(1 for n in nodes if n['kind']=='cloudflare'),
            'cf_ips_total':len(rows('SELECT ip FROM cf_ips')),
            'cf_ips_ok':len(rows('SELECT ip FROM cf_ips WHERE ok=1')),
            'proxies':len(list_proxies()),
            'active_ips_1h':int((connected[0] or {}).get('n') or 0) if connected else 0,
        },
        'series_hourly':hourly,
        'series_daily':daily,
        'protocols':[{'name':k,'count':v} for k,v in protocols.items()],
        'nodes':[{'name':n['name'],'kind':n['kind'],'server':n['server'],'port':n['port'],'latency_ms':n['latency_ms'],'enabled':bool(n['enabled'])} for n in nodes],
        'top_users':[{'username':u['username'],'protocol':u.get('protocol'),'used_gb':round(float(u.get('used_gb') or 0),4),'limit_gb':u.get('limit_gb'),'is_active':bool(u['is_active'])} for u in top],
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
    def sub(target,name=''):
        url=f"{base}/sub/{token}?target={urllib.parse.quote(normalize_target(target))}"
        if name: url+='&node='+urllib.parse.quote(str(name))
        return url
    items=[]
    for n in nodes:
        item=node_links(u,n,prefix)
        item['subscriptions']=[{'target':t,'label':SUB_LABELS.get(t,t),'url':sub(t,item['name'])} for t in ('vless','trojan','base64','singbox','clash','xray')]
        item['subscription']=sub(u.get('protocol') or 'vless',item['name'])
        item['subscription_all']=sub('all',item['name'])
        item['clients']=client_links(base,token,item['name'],{})
        items.append(item)
    return {
        'username':u['username'],'uuid':u['uuid'],'protocol':u.get('protocol'),
        'is_active':bool(u['is_active']),'allowed':ok,'reason':reason,
        'limit_gb':u.get('limit_gb'),'used_gb':round(float(u.get('used_gb') or 0),4),
        'used_req':int(u.get('used_req') or 0),'expires_at':u.get('expires_at'),
        'base_url':base,'node_count':len(items),
        'portal_url':_portal_url(base,token),
        'subscription':sub(u.get('protocol') or 'vless'),
        'smart_url':sub('auto'),
        'subscriptions':[{'target':t,'label':SUB_LABELS.get(t,t),'url':sub(t)} for t in SUB_TARGETS],
        'clients':client_links(base,token,'',None),
        'nodes':items,
    }


@app.put('/api/nodes/{name}')
async def update_node(request:Request,name:str):
    auth(request)
    existing=row('SELECT * FROM nodes WHERE name=?',(name,))
    if not existing: raise HTTPException(404,'node not found')
    b=await request.json(); sets=[]; vals=[]
    if b.get('kind') is not None and str(b['kind']) not in {'railway','cloudflare'}: raise HTTPException(400,'unsupported node kind')
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
    payload['subscription']={'targets':SUB_TARGETS,'prefix':_sub_prefix(),'node_count':len(active_nodes()),'protocols':['VLESS','Trojan'],'transport':'WebSocket + TLS'}
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
    if 'default_protocol' in b and str(b['default_protocol']) not in {'vless','trojan'}:
        raise HTTPException(400,'default_protocol must be vless or trojan')
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
    """Served from the root so the worker's scope can cover the whole panel."""
    return FileResponse(os.path.join(BASE_DIR,'static','sw.js'),media_type='text/javascript',
                        headers={'Service-Worker-Allowed':'/','Cache-Control':'no-cache'})

@app.get('/api/version')
def version(): return {'version':'7.0.0','python':os.sys.version.split()[0]}

@app.exception_handler(Exception)
async def unhandled_error(request:Request,exc:Exception):
    """Never answer a crashed route with an empty body the panel cannot explain."""
    if request.url.path.startswith('/api/'):
        return JSONResponse({'detail':f'خطای داخلی سرور ({type(exc).__name__})'},status_code=500)
    return PlainTextResponse('NEXUS internal error: '+type(exc).__name__,status_code=500)
