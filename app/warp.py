"""Cloudflare WARP peer management.

WARP gives an exit node that is *inside* Cloudflare's network, which is what
unblocks services that filter by datacenter IP. Xray already speaks WireGuard, so
a WARP node is the ``/ws/warp`` inbound routed to a ``wireguard`` outbound.

Registration is a single anonymous API call and produces exactly the four values
the outbound needs. It is deliberately **opt-in and verified**: the peer is only
published after the admin turns it on, because a WARP tunnel depends on the host
allowing outbound WireGuard/UDP — something the panel cannot assume, and a node
that silently dead-ends is worse than no node.

``python scripts/check_transports_e2e.py`` with ``WARP_CONFIG`` set proves the
tunnel end to end on the host that will run it.
"""
import base64
import json
import os
import subprocess
import time
import urllib.request

from app.config import settings
from app.db import row, execute

REGISTER_URL = 'https://api.cloudflareclient.com/v0a2158/reg'
CLIENT_VERSION = 'a-6.11-2158'
DEFAULT_ENDPOINT = 'engage.cloudflareclient.com:2408'


def _put(key, value):
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, value))


def _setting(key):
    found = row('SELECT value FROM settings WHERE key=?', (key,))
    return (found or {}).get('value') if found else None


def _keypair():
    """X25519 keys from the Xray binary; falls back to none when unavailable."""
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
    # The registration API wants standard base64; Xray prints base64url.
    standard = base64.b64encode(base64.urlsafe_b64decode(public + '=' * (-len(public) % 4))).decode()
    return private, standard


def register(timeout=25):
    """Create a disposable WARP peer and store it under ``warp_config``."""
    pair = _keypair()
    if not pair:
        raise RuntimeError('Xray binary unavailable: cannot generate WireGuard keys')
    private, public = pair
    body = json.dumps({
        'key': public, 'install_id': '', 'fcm_token': '',
        'tos': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
        'model': 'PC', 'serial_number': '', 'locale': 'en_US',
    }).encode()
    request = urllib.request.Request(REGISTER_URL, data=body, headers={
        'CF-Client-Version': CLIENT_VERSION, 'User-Agent': 'okhttp/3.12.1',
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    config = payload.get('config') or {}
    peers = config.get('peers') or []
    if not peers:
        raise RuntimeError('Cloudflare did not return a WARP peer')
    peer = peers[0]
    entry = {
        'secret_key': private,
        'peer_public_key': peer.get('public_key'),
        'endpoint': ((peer.get('endpoint') or {}).get('host')) or DEFAULT_ENDPOINT,
        'address': ((config.get('interface') or {}).get('addresses') or {}).get('v4') or '172.16.0.2',
        'device_id': payload.get('id'),
        'registered_at': int(time.time()),
        'mtu': 1280,
    }
    _put('warp_config', json.dumps(entry))
    return entry


def enable():
    _put('warp_enabled', '1')


def disable():
    _put('warp_enabled', '0')


def discard():
    """Forget the peer (the Cloudflare-side device simply expires)."""
    execute('DELETE FROM settings WHERE key=?', ('warp_config',))
    disable()


def status():
    raw = _setting('warp_config')
    entry = None
    if raw:
        try:
            entry = json.loads(raw)
        except Exception:
            entry = None
    return {
        'registered': bool(entry),
        'enabled': (_setting('warp_enabled') or '') == '1',
        'endpoint': (entry or {}).get('endpoint'),
        'address': (entry or {}).get('address'),
        'registered_at': (entry or {}).get('registered_at'),
        'key_ready': bool(os.path.exists(settings.xray_binary)),
        'note': 'پس از فعال‌سازی، نود WARP به سابلینک همه کاربران اضافه می‌شود.',
    }
