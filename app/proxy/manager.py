import re,time,socket,urllib.parse
from app.db import rows,execute,is_pg
PROXY_RE=re.compile(r'^(?:(socks5|socks4|http)://)?(?:(?:([^:@]+):([^@]+)@))?([^:]+):(\d+)$',re.I)
def parse(value):
    v=value.strip(); m=PROXY_RE.match(v)
    if not m:return None
    return {'kind':(m.group(1) or 'socks5').lower(),'username':m.group(2),'password':m.group(3),'host':m.group(4),'port':int(m.group(5))}
def add(value,country=None):
    p=parse(value)
    if not p: raise ValueError('invalid proxy')
    if is_pg(): return execute('INSERT INTO proxies(value,kind,country) VALUES(?,?,?) ON CONFLICT(value) DO NOTHING',(value,p['kind'],country))
    return execute('INSERT OR IGNORE INTO proxies(value,kind,country) VALUES(?,?,?)',(value,p['kind'],country))
def list_all(): return rows('SELECT * FROM proxies ORDER BY CASE WHEN latency_ms IS NULL THEN 1 ELSE 0 END, latency_ms')
def check(value,timeout=3):
    p=parse(value); start=time.perf_counter(); ok=False
    if p:
        try:
            s=socket.create_connection((p['host'],p['port']),timeout); s.close(); ok=True
        except OSError: pass
    ms=round((time.perf_counter()-start)*1000,1)
    execute('UPDATE proxies SET latency_ms=?,fail_count=CASE WHEN ? THEN 0 ELSE fail_count+1 END,last_check=? WHERE value=?',(ms if ok else None,int(ok),int(time.time()),value))
    return {'success':ok,'latency_ms':ms if ok else None}
