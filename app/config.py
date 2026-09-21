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
    xray_api_port: int = 10085
    xray_sync_interval: int = 10
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)
settings=Settings()
