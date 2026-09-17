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
    xray_vless_port: int = 10001
    xray_trojan_port: int = 10002
    xray_api_port: int = 10085
    xray_sync_interval: int = 10
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)
settings=Settings()
