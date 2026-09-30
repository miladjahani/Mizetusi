import os
from pydantic_settings import BaseSettings, SettingsConfigDict

# The support channel every install falls back to when the admin has not set a
# support link in «شخصی‌سازی». One place, so the panel shell, the login screen,
# the live guide and the public status window all point at the same channel.
SUPPORT_CHANNEL = 'https://t.me/miliconfig'

class Settings(BaseSettings):
    app_name: str = 'NEXUS Xray Control Plane'
    environment: str = 'production'
    admin_password: str = 'admin'
    jwt_secret: str = ''
    database_url: str = 'sqlite:////data/nexus.db'
    sqlite_path: str = '/data/nexus.db'
    public_base_url: str = ''
    # Sessions last a week by default: an admin who was logged out every day on
    # a phone saw it as a broken panel. Overridable at runtime in Settings.
    session_ttl: int = 604800
    auto_reset_interval: int = 60
    cf_probe_interval: int = 900
    # How many clean IPs are kept per pass, and how many probes run at once.
    # Both are deliberately small: a panel that fires hundreds of outbound
    # TCP/TLS connects the moment it boots looks like port scanning to its host,
    # which is how a workspace ends up flagged for "suspicious activity".
    cf_probe_limit: int = 64
    cf_probe_concurrency: int = 8
    # Filling the clean-IP pool at boot is opt-in (NEXUS_SCAN_ON_BOOT=1). The
    # catalog is complete without it — the origin node is always published — and
    # an admin can scan any provider on demand from the panel.
    scan_on_boot: bool = False
    # Master switch for outbound probing (NEXUS_OUTBOUND_PROBE_ENABLED=0 stops
    # every external probe and ping; the nodes already measured stay published).
    outbound_probe_enabled: bool = True
    xray_enabled: bool = True
    xray_binary: str = '/usr/local/bin/xray'
    xray_config: str = '/data/xray.json'
    # One local listener per published transport: the FastAPI edge bridges each
    # WebSocket path to the matching Xray inbound.
    xray_vless_port: int = 10001
    xray_trojan_port: int = 10002
    xray_vmess_port: int = 10003
    xray_ss_port: int = 10004
    xray_vless_cdn_port: int = 10005
    xray_vmess_cdn_port: int = 10006
    xray_trojan_cdn_port: int = 10007
    xray_warp_port: int = 10008
    # One listener per Shadowsocks-2022 cipher family, in both edge path shapes.
    xray_ss_cdn_port: int = 10009
    xray_ss_aes256_port: int = 10010
    xray_ss_aes256_cdn_port: int = 10011
    xray_ss_chacha_port: int = 10012
    xray_ss_chacha_cdn_port: int = 10013
    xray_ss_legacy_port: int = 10014
    xray_ss_legacy_cdn_port: int = 10015
    # The universally supported classic cipher (aes-256-gcm), published first so
    # every client — not only the SIP022-aware ones — lists and pings it.
    xray_ss_classic_port: int = 10016
    xray_ss_classic_cdn_port: int = 10017
    # Reality + fallback children (gRPC/HTTPUpgrade/H2/XHTTP) share ONE public
    # TCP port. Off until a direct endpoint exists (Railway TCP proxy env vars
    # or the direct_host/direct_port settings).
    xray_reality_port: int = 8443
    xray_fallback_grpc_port: int = 10101
    xray_fallback_httpupgrade_port: int = 10102
    xray_fallback_xhttp_port: int = 10104
    xray_fallback_vmess_grpc_port: int = 10105
    xray_fallback_trojan_grpc_port: int = 10106
    direct_host: str = ''
    direct_port: int = 0
    # ---------------------------------------------- second engines (app/cores)
    # AnyTLS and TUIC v5 have no Xray inbound at all: sing-box and mihomo are the
    # only implementations, so both binaries ship with the image and the panel
    # picks one per protocol. Everything here is opt-in — a protocol is published
    # only once an admin enables it *and* the port is actually reachable.
    singbox_binary: str = '/usr/local/bin/sing-box'
    mihomo_binary: str = '/usr/local/bin/mihomo'
    singbox_config: str = '/data/sing-box.json'
    mihomo_config: str = '/data/mihomo.yaml'
    mihomo_home: str = '/data/mihomo'
    cores_enabled: bool = True
    cores_sync_interval: int = 15
    # Public ports of the hosted protocols. They are free on a VPS; on Railway
    # each one needs its own TCP proxy (and UDP is not available there at all).
    core_anytls_port: int = 8444
    core_tuic_port: int = 8445
    # These protocols do their own TLS, so the certificate is self-signed for this
    # name and the links carry ``insecure``. The name is a disguise parameter, the
    # same job the Reality SNI does, so one innocuous host is used for both.
    core_sni: str = 'www.cloudflare.com'
    # Where mihomo's own API is bound (loopback only, and never the default 9090 so
    # it cannot collide with anything else in this container).
    mihomo_api: str = '127.0.0.1:9095'
    # ------------------------------------------------- Telegram proxies
    # Three ways an end user reaches Telegram from this deployment, each with its
    # own switch, and each published only when its listener really runs and its
    # port is reachable from outside:
    #
    # * an **MTProto proxy** (``mtg``) — the ``tg://proxy`` link a user pastes in
    #   the Telegram app itself, so no client has to be installed;
    # * an **HTTP/SOCKS5 web proxy** on the Xray engine the panel already runs,
    #   with one credential per user — what Telegram Desktop calls «custom proxy»
    #   (and what a browser can use as well);
    # * **web.telegram.org** through this deployment's own domain, so the web app
    #   opens where the site itself is blocked (and through the Cloudflare Worker
    #   in front of it when the panel's own address is blocked too).
    mtg_binary: str = '/usr/local/bin/mtg'
    mtg_config: str = '/data/mtg.toml'
    telegram_mtproto_port: int = 8446
    telegram_mtproto_concurrency: int = 8192
    # The FakeTLS fronting name. It goes *inside* the secret and is the SNI an
    # active probe sees, so it has to be a hostname that really serves TLS.
    telegram_mtproto_domain: str = 'www.cloudflare.com'
    telegram_mtproto_dns: str = 'https://1.1.1.1'
    telegram_sync_interval: int = 15
    # Public ports of the HTTP and SOCKS5 web proxies (Xray inbounds).
    telegram_http_port: int = 8448
    telegram_socks_port: int = 8449
    # Telegram Desktop 7.1+'s **WEB** proxy (the ``tg://webproxy`` link). MTProto is
    # carried inside an ordinary HTTPS page and a same-origin WebSocket, so the
    # client never opens a raw TCP socket — which is what makes this the one
    # Telegram proxy that works on a forwarder (Railway, a CDN, a Worker) with no
    # TCP proxy of its own. The relay is mtproto.zig's ``mtproto-proxy web-relay``;
    # its data plane listens on loopback only, because the relay — not a client —
    # is what dials it.
    webrelay_binary: str = '/usr/local/bin/mtproto-proxy'
    webrelay_config: str = '/data/webrelay.toml'
    webrelay_port: int = 8081
    webrelay_backend_port: int = 8447
    # Telegram Desktop multiplexes its sockets over one WebSocket, so a handful of
    # clients is a crowd of streams: the data plane's ceiling is derived from these
    # two (sessions x (streams + 1)) rather than guessed.
    webrelay_sessions: int = 6
    webrelay_streams: int = 32
    webrelay_ws_path: str = '/api/v1/socket'
    # web.telegram.org through this deployment: one path prefix on the panel's own
    # domain (and the same prefix forwarded by the Cloudflare Worker).
    telegram_web_path: str = '/tg'
    telegram_web_origin: str = 'https://web.telegram.org'
    xray_api_port: int = 10085
    xray_sync_interval: int = 10
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)
settings=Settings()
