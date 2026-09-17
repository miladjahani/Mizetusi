import asyncio, secrets, time, urllib.parse, os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import jwt
from app.config import settings
from app.db import init_db, row, rows, execute
from app.core.models import UserCreate, TrafficEvent
from app.users.service import list_users, get_user, get_by_token, create_user, delete_user, toggle_user, reset_user, track, allowed, reset_due
from app.subscriptions.generator import render, active_nodes
from app.proxy.manager import add as add_proxy, list_all as list_proxies, check as check_proxy
from app.dns.service import doh
from app.services.backup import export_all
from app.cloudflare.monitor import seed_ips, probe_all, best, loop as cf_loop
from app.nodes import ensure as ensure_nodes, list_nodes, upsert as upsert_node, sync_from_sources
from app import xray
import websockets
_started=time.time(); _login_attempts={}

def _setting(key):
    x=row('SELECT value FROM settings WHERE key=?',(key,)); return x['value'] if x else None

def _token():
    secret=_setting('jwt_secret') or settings.jwt_secret
    return jwt.encode({'iat':int(time.time()),'exp':int(time.time())+settings.session_ttl,'sub':'admin'},secret,algorithm='HS256')
def auth(request:Request):
    supplied=request.headers.get('x-admin-password')
    admin=_setting('admin_password') or settings.admin_password
    if supplied and admin and secrets.compare_digest(supplied, admin): return True
    token=request.cookies.get('zeus_session')
    if token:
        try:
            p=jwt.decode(token,_setting('jwt_secret') or settings.jwt_secret,algorithms=['HS256'])
            if p.get('sub')=='admin': return True
        except jwt.PyJWTError: pass
    raise HTTPException(401,'authentication required')
def public_base(request:Request):
    if settings.public_base_url: return settings.public_base_url.rstrip('/')
    proto=request.headers.get('x-forwarded-proto','https').split(',')[0].strip()
    host=request.headers.get('x-forwarded-host') or request.headers.get('host')
    return f'{proto}://{host}' if host else 'https://example.invalid'
async def maintenance():
    while True:
        try: reset_due()
        except Exception: pass
        await asyncio.sleep(max(15,settings.auto_reset_interval))

def bootstrap():
    # Secrets persist in the DB so Railway Variables are not required for first boot.
    if not _setting('jwt_secret'):
        execute('INSERT INTO settings(key,value) VALUES(?,?)',('jwt_secret',secrets.token_urlsafe(48)))
    if not (_setting('admin_password') or settings.admin_password):
        execute('INSERT INTO settings(key,value) VALUES(?,?)',('admin_password','admin'))
    if not _setting('bootstrap_complete'):
        execute('INSERT INTO settings(key,value) VALUES(?,?)',('bootstrap_complete','0'))
@asynccontextmanager
async def lifespan(app:FastAPI):
    init_db(); bootstrap(); ensure_nodes(); await xray.start_or_reload(force=True); task=asyncio.create_task(maintenance()); cf_task=asyncio.create_task(cf_loop(settings.cf_probe_interval)); xray_task=asyncio.create_task(xray.loop())
    try: yield
    finally:
        task.cancel(); cf_task.cancel(); xray_task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        try: await cf_task
        except asyncio.CancelledError: pass
        try: await xray_task
        except asyncio.CancelledError: pass
        try: await xray._stop()
        except Exception: pass
app=FastAPI(title=settings.app_name,version='4.2.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[],allow_methods=['GET','POST','PUT','DELETE','OPTIONS'],allow_headers=['Content-Type','X-Admin-Password'])
templates=Jinja2Templates(directory='templates')
@app.get('/health')
def health():
    try:
        row('SELECT 1'); return {'ok':True,'service':'zeus-python','database':'ok','uptime_seconds':int(time.time()-_started),'time':int(time.time())}
    except Exception as exc: raise HTTPException(503,f'database unavailable: {type(exc).__name__}')
@app.get('/',response_class=HTMLResponse)
def home(request:Request):
    try: auth(request); return templates.TemplateResponse('index.html',{'request':request,'stats':{'users':len(list_users()),'proxies':len(list_proxies())}})
    except HTTPException: return RedirectResponse('/login',303)
@app.get('/login',response_class=HTMLResponse)
def login_page(request:Request): return templates.TemplateResponse('login.html',{'request':request})
@app.post('/api/login')
async def login(request:Request):
    ip=request.client.host if request.client else 'unknown'; now=time.time(); old=_login_attempts.get(ip,[]); old=[x for x in old if now-x<300]
    if len(old)>=10: raise HTTPException(429,'too many login attempts')
    b=await request.json(); password=str(b.get('password',''))
    admin=_setting('admin_password') or settings.admin_password
    if not admin or not secrets.compare_digest(password,admin):
        old.append(now); _login_attempts[ip]=old; raise HTTPException(401,'invalid password')
    _login_attempts.pop(ip,None); r=JSONResponse({'success':True}); r.set_cookie('zeus_session',_token(),httponly=True,secure=request.url.scheme=='https',samesite='strict',max_age=settings.session_ttl,path='/'); return r
@app.post('/api/logout')
def logout():
    r=JSONResponse({'success':True}); r.delete_cookie('zeus_session',path='/'); return r
@app.get('/api/stats')
def stats(request:Request):
    auth(request); us=list_users(); ps=list_proxies(); return {'users':len(us),'active_users':sum(bool(x['is_active']) for x in us),'proxies':len(ps),'enabled_proxies':sum(bool(x['enabled']) for x in ps),'nodes':len(list_nodes()),'cf_ips':len(rows('SELECT ip FROM cf_ips WHERE ok=1'))}
@app.get('/api/users')
def users(request:Request): auth(request); return list_users()
@app.post('/api/users')
def create(request:Request,m:UserCreate):
    auth(request)
    try: return create_user(m)
    except Exception as e: raise HTTPException(400,str(e))
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
def delete(request:Request,username:str): auth(request); return {'success':bool(delete_user(username))}
@app.post('/api/traffic/{username}')
def traffic(request:Request,username:str,e:TrafficEvent):
    auth(request); result=track(username,e.bytes,e.requests,e.ip)
    if not result: raise HTTPException(404,'user not found')
    return result
@app.get('/sub/{token}')
def subscription(request:Request,token:str,target:str='vless'):
    u=get_by_token(urllib.parse.unquote(token))
    if not u: raise HTTPException(404,'subscription not found')
    ok,reason=allowed(u)
    if not ok: raise HTTPException(403,reason)
    # Generate from the live Node Catalog on every request. This means a client
    # refresh automatically receives the current Railway + healthy Cloudflare nodes.
    try: text=render(u,public_base(request),target,active_nodes())
    except ValueError as e: raise HTTPException(400,str(e))
    return PlainTextResponse(text,headers={'Cache-Control':'no-store, max-age=0','X-Content-Type-Options':'nosniff','X-ZEUS-Node-Count':str(len(active_nodes()))})
@app.get('/feed/{token}')
def feed(request:Request,token:str,target:str='vless'): return subscription(request,token,target)


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


@app.get('/status/{username}',response_class=HTMLResponse)
def status(username:str):
    u=get_user(username)
    if not u: raise HTTPException(404,'not found')
    return HTMLResponse(f'<html><body><h2>{u["username"]}</h2><p>Active: {bool(u["is_active"])}</p><p>Used GB: {u["used_gb"]}</p></body></html>')
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
    return {'success':True,'configured':bool(url),'nodes':list_nodes()}

@app.get('/api/nodes')
def nodes(request:Request):
    auth(request); return list_nodes()

@app.post('/api/nodes/sync')
def sync_nodes(request:Request):
    auth(request); worker=_setting('cloudflare_worker_url'); count=sync_from_sources(public_base(request),worker); return {'success':True,'synced':count,'nodes':list_nodes()}

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
    auth(request); count=seed_ips(settings.cf_probe_limit); results=await probe_all(limit=settings.cf_probe_limit); sync_from_sources(public_base(request), _setting('cloudflare_worker_url')); return {'seeded':count,'probed':len(results),'best':best(20),'nodes':list_nodes()}

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

@app.get('/api/version')
def version(): return {'version':'5.0.0','python':os.sys.version.split()[0]}
