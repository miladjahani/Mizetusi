import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = 'NEXUS Railway Python Auto'
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
    cf_probe_limit: int = 256
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
