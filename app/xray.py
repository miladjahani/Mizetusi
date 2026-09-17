"""ZEUS Xray-core supervisor.

Xray is the protocol engine; FastAPI remains the public HTTPS/WebSocket edge on
Railway. The public edge terminates TLS, then bridges WebSocket streams to the
local Xray listeners. This lets Railway use one HTTP service/port while Xray
handles VLESS/Trojan parsing, UDP tunnelling, routing, mux and other core
features supported by the installed Xray release.
"""
import asyncio, hashlib, json, os, signal, re
from app.config import settings
from app.db import rows, row, execute

_proc = None
_last_hash = None


def _config():
    users = rows("SELECT username,uuid,protocol,is_active FROM users")
    vclients = [{"id": u["uuid"], "email": u["username"] + "@zeus.local", "level": 0} for u in users if u.get("protocol") == "vless" and u.get("is_active")]
    tclients = [{"password": u["uuid"], "email": u["username"] + "@zeus.local", "level": 0} for u in users if u.get("protocol") == "trojan" and u.get("is_active")]
    return {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "listen": f"127.0.0.1:{settings.xray_api_port}", "services": ["StatsService"]},
        "stats": {},
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
                   "system": {"statsInboundUplink": True, "statsInboundDownlink": True}},
        "inbounds": [
            {"tag": "vless-ws", "listen": "127.0.0.1", "port": settings.xray_vless_port,
             "protocol": "vless", "settings": {"clients": vclients, "decryption": "none"},
             "streamSettings": {"network": "ws", "wsSettings": {"path": "/ws/vless"}}},
            {"tag": "trojan-ws", "listen": "127.0.0.1", "port": settings.xray_trojan_port,
             "protocol": "trojan", "settings": {"clients": tclients},
             "streamSettings": {"network": "ws", "wsSettings": {"path": "/ws/trojan"}}},
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct", "settings": {"domainStrategy": "AsIs"}},
            {"protocol": "blackhole", "tag": "block"}
        ],
        "routing": {"domainStrategy": "AsIs", "rules": []}
    }


def write_config():
    cfg = _config()
    raw = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    os.makedirs(os.path.dirname(settings.xray_config), exist_ok=True)
    with open(settings.xray_config, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return digest, cfg


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
    global _proc, _last_hash
    if not settings.xray_enabled or not os.path.exists(settings.xray_binary):
        return {"running": False, "reason": "xray binary unavailable"}
    digest, _ = write_config()
    test = await asyncio.create_subprocess_exec(settings.xray_binary, "run", "-test", "-config", settings.xray_config, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    tout, terr = await test.communicate()
    if test.returncode != 0:
        return {"running": False, "reason": (terr or tout).decode(errors="ignore")[-1500:] or "invalid xray config"}
    if not force and digest == _last_hash and _proc and _proc.returncode is None:
        return {"running": True, "pid": _proc.pid, "reloaded": False}
    await _stop()
    _proc = await asyncio.create_subprocess_exec(
        settings.xray_binary, "run", "-config", settings.xray_config,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _last_hash = digest
    await asyncio.sleep(0.25)
    if _proc.returncode is not None:
        err = (await _proc.stderr.read()).decode(errors="ignore")[-1000:]
        return {"running": False, "reason": err or "xray exited"}
    return {"running": True, "pid": _proc.pid, "reloaded": True}


async def sync_traffic_stats():
    if not _proc or _proc.returncode is not None or not os.path.exists(settings.xray_binary):
        return
    try:
        p = await asyncio.create_subprocess_exec(settings.xray_binary, "api", "statsquery", f"--server=127.0.0.1:{settings.xray_api_port}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), 5)
        payload = json.loads(out.decode(errors="ignore"))
        totals = {}
        for item in payload.get("stat", []):
            m = re.match(r"user>>>(.+)>>>traffic>>>(uplink|downlink)$", str(item.get("name", "")))
            if m:
                email, direction = m.groups(); totals.setdefault(email, {})[direction] = int(item.get("value", 0))
        for u in rows("SELECT username,used_gb,limit_gb,is_active FROM users WHERE is_active=1"):
            email = u["username"] + "@zeus.local"; t = totals.get(email)
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
    return {"enabled": bool(settings.xray_enabled), "binary": settings.xray_binary,
            "running": bool(_proc and _proc.returncode is None),
            "pid": _proc.pid if _proc else None,
            "vless_listener": settings.xray_vless_port,
            "trojan_listener": settings.xray_trojan_port}
