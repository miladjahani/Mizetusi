import time, uuid, json
from app.db import row, rows, execute, is_pg
from app.core.models import UserCreate

def create_user(m:UserCreate):
    uid=str(uuid.uuid4()); now=int(time.time()); exp=now+m.expiry_days*86400 if m.expiry_days is not None and not m.start_on_first_connect else None
    sql='''INSERT INTO users(username,uuid,protocol,limit_gb,expiry_days,limit_req,ip_limit,start_on_first_connect,created_at,expires_at,ips,fingerprint,tls,port,sni,host,frag_len,frag_int,advanced_frag,cipher_suites,tls_mask,block_ads,block_porn,auto_rotate_ip,rotate_time,ip_operator,ip_count,user_proxy,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
    execute(sql,(m.username,uid,m.protocol,m.limit_gb,m.expiry_days,m.limit_req,m.ip_limit,int(m.start_on_first_connect),now,exp,m.ips,m.fingerprint,m.tls,m.port,m.sni,m.host,m.frag_len,m.frag_int,m.advanced_frag,m.cipher_suites,m.tls_mask,int(m.block_ads),int(m.block_porn),int(m.auto_rotate_ip),m.rotate_time,m.ip_operator,m.ip_count,m.user_proxy,json.dumps(m.metadata)))
    return get_user(m.username)
def get_user(username): return row('SELECT * FROM users WHERE username=?',(username,))
def get_by_token(token): return row('SELECT * FROM users WHERE uuid=? OR username=?',(token,token))
def list_users(): return rows('SELECT * FROM users ORDER BY id DESC')
def delete_user(username): return execute('DELETE FROM users WHERE username=?',(username,))
def toggle_user(username): execute('UPDATE users SET is_active=CASE is_active WHEN 1 THEN 0 ELSE 1 END WHERE username=?',(username,)); return get_user(username)
def reset_user(username,kind='volume'):
    now=int(time.time())
    if kind=='volume': execute('UPDATE users SET used_gb=0,reset_volume_at=NULL WHERE username=?',(username,))
    elif kind=='req': execute('UPDATE users SET used_req=0,reset_request_at=NULL WHERE username=?',(username,))
    else: raise ValueError('unknown reset type')
    return get_user(username)
def track(user,bytes_count,requests,ip=None):
    now=int(time.time()); u=get_user(user)
    if not u:return None
    if not u['is_active']: return u
    if u['start_on_first_connect'] and not u['first_connection_time']:
        exp=now+(u['expiry_days'] or 0)*86400 if u['expiry_days'] else None
        execute('UPDATE users SET first_connection_time=?,expires_at=? WHERE username=?',(now,exp,user))
    gb=bytes_count/1_000_000_000
    execute('UPDATE users SET used_gb=used_gb+?, lifetime_used_gb=lifetime_used_gb+?, used_req=used_req+? WHERE username=?',(gb,gb,requests,user))
    execute('INSERT INTO traffic_events(user_id,bytes,requests,ip,created_at) VALUES((SELECT id FROM users WHERE username=?),?,?,?,?)',(user,bytes_count,requests,ip,now))
    return get_user(user)
def allowed(u):
    if not u or not u['is_active']: return False,'disabled'
    now=int(time.time())
    if u['start_on_first_connect'] and not u['first_connection_time']: return True,'ok'
    if u['expires_at'] and now>=u['expires_at']: return False,'expired'
    if u['limit_gb'] is not None and u['used_gb']>=u['limit_gb']: return False,'quota'
    if u['limit_req'] is not None and u['used_req']>=u['limit_req']: return False,'requests'
    return True,'ok'
def reset_due():
    # Only automatic resets explicitly scheduled by external integrations are acted on.
    now=int(time.time()); changed=0
    for u in list_users():
        if u['reset_volume_at'] and u['reset_volume_at']<=now: reset_user(u['username'],'volume'); changed+=1
        if u['reset_request_at'] and u['reset_request_at']<=now: reset_user(u['username'],'req'); changed+=1
    return changed
