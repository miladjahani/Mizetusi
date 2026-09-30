"""Runtime detection: the same image must behave correctly on every host.

NEXUS used to read only ``RAILWAY_*`` variables, which meant a Render or VPS
deployment published an empty catalog (no origin node, no subscription URLs) and
crashed at boot when ``/data`` did not exist. These tests pin the behaviour of
the generic detector and of the auto-detected raw TCP endpoint that turns Reality
on for a VPS with no configuration at all.
"""
import os
from pathlib import Path

import pytest

from app import runtime


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every marker is removed so the tests describe exactly one platform."""
    keys = ('RAILWAY_ENVIRONMENT_ID', 'RAILWAY_PROJECT_ID', 'RAILWAY_SERVICE_ID', 'RAILWAY_PUBLIC_DOMAIN',
            'RAILWAY_STATIC_URL', 'RAILWAY_TCP_PROXY_DOMAIN', 'RAILWAY_TCP_PROXY_PORT',
            'RENDER', 'RENDER_SERVICE_ID', 'RENDER_EXTERNAL_URL', 'RENDER_EXTERNAL_HOSTNAME',
            'FLY_APP_NAME', 'FLY_ALLOC_ID', 'FLY_REGION', 'KOYEB_APP_NAME', 'KOYEB_PUBLIC_DOMAIN',
            'DYNO', 'HEROKU_APP_NAME', 'HEROKU_APP_DOMAIN', 'VERCEL', 'VERCEL_URL', 'REPL_ID',
            'REPLIT_DEV_DOMAIN', 'WEBSITE_HOSTNAME', 'NEXUS_PLATFORM', 'NEXUS_PUBLIC_DOMAIN',
            'NEXUS_PUBLIC_IP', 'NEXUS_DIRECT_HOST', 'NEXUS_DIRECT_PORT', 'NEXUS_DATA_DIR',
            'PUBLIC_BASE_URL', 'PUBLIC_IP', 'NEXUS_NO_PUBLIC_IP')
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    runtime._public_ip_cache.update({'value': None, 'at': 0.0})
    yield monkeypatch


def test_render_is_detected_from_its_own_variables(clean_env):
    clean_env.setenv('RENDER', 'true')
    clean_env.setenv('RENDER_EXTERNAL_URL', 'https://nexus.onrender.com')
    assert runtime.platform() == 'render'
    assert runtime.label() == 'Render'
    assert runtime.host() == 'nexus.onrender.com'
    assert runtime.public_base() == 'https://nexus.onrender.com'
    # Render has no free raw TCP port, so nothing direct may be published.
    assert runtime.has_tcp() is False
    assert runtime.direct() is None


def test_render_hostname_only_variable_is_enough(clean_env):
    clean_env.setenv('RENDER_EXTERNAL_HOSTNAME', 'nexus-abc.onrender.com')
    assert runtime.platform() == 'render'
    assert runtime.host() == 'nexus-abc.onrender.com'
    assert runtime.info()['id'] == 'render'
    assert runtime.info()['notes']


def test_railway_needs_a_tcp_proxy_for_a_direct_endpoint(clean_env):
    clean_env.setenv('RAILWAY_PROJECT_ID', 'p')
    clean_env.setenv('RAILWAY_PUBLIC_DOMAIN', 'nexus-production.up.railway.app')
    assert runtime.platform() == 'railway'
    assert runtime.host() == 'nexus-production.up.railway.app'
    assert runtime.has_tcp() is False
    clean_env.setenv('RAILWAY_TCP_PROXY_DOMAIN', 'roundhouse.proxy.rlwy.net')
    clean_env.setenv('RAILWAY_TCP_PROXY_PORT', '26789')
    assert runtime.has_tcp() is True
    assert runtime.direct() == {'host': 'roundhouse.proxy.rlwy.net', 'port': 26789, 'source': 'env'}


def test_a_vps_needs_no_configuration_at_all(clean_env):
    # A plain Docker/VPS host: no PaaS markers, but ports are free and the public
    # address is discovered locally (no packet is sent), so Reality is published.
    clean_env.setenv('NEXUS_PLATFORM', 'vps')
    clean_env.setenv('NEXUS_PUBLIC_IP', '93.184.216.34')
    assert runtime.platform() == 'docker'
    assert runtime.label() == 'VPS / Docker'
    assert runtime.has_tcp() is True
    assert runtime.direct() == {'host': '93.184.216.34', 'port': runtime.DEFAULT_DIRECT_PORT, 'source': 'auto'}


def test_an_explicit_port_wins_on_a_vps(clean_env):
    clean_env.setenv('NEXUS_PLATFORM', 'vps')
    clean_env.setenv('NEXUS_PUBLIC_IP', '93.184.216.34')
    clean_env.setenv('NEXUS_DIRECT_PORT', '9443')
    assert runtime.direct()['port'] == 9443


def test_a_private_address_is_never_published_as_direct(clean_env, monkeypatch):
    """A NAT'd container resolves to 172.x/10.x: publishing it would be a dead link."""
    clean_env.setenv('NEXUS_PLATFORM', 'vps')
    monkeypatch.setattr(runtime, 'public_ip', lambda *a, **k: '172.20.0.78')
    assert runtime.direct() is None
    monkeypatch.setattr(runtime, 'public_ip', lambda *a, **k: '203.0.113.7')  # reserved range
    assert runtime.direct() is None
    assert runtime.is_public_address('172.20.0.78') is False
    assert runtime.is_public_address('93.184.216.34') is True
    # An operator-provided hostname is trusted: they know their port mapping.
    clean_env.setenv('NEXUS_DIRECT_HOST', 'vpn.example.com')
    clean_env.setenv('NEXUS_DIRECT_PORT', '8443')
    assert runtime.direct() == {'host': 'vpn.example.com', 'port': 8443, 'source': 'env'}


def test_explanation_variables_are_tried_first(clean_env):
    clean_env.setenv('RENDER', 'true')
    clean_env.setenv('RENDER_EXTERNAL_URL', 'https://ignored.onrender.com')
    clean_env.setenv('NEXUS_PUBLIC_DOMAIN', 'panel.example.com')
    assert runtime.host() == 'panel.example.com'
    clean_env.setenv('PUBLIC_BASE_URL', 'https://forced.example.com')
    assert runtime.host() == 'panel.example.com'  # NEXUS_* is the operator override


def test_fly_and_heroku_hostnames_are_derived_from_the_app_name(clean_env):
    clean_env.setenv('FLY_APP_NAME', 'nexus-edge')
    assert runtime.platform() == 'fly'
    assert runtime.host() == 'nexus-edge.fly.dev'
    assert runtime.has_tcp() is True  # Fly can publish TCP services


def test_unknown_host_falls_back_to_the_local_platform(clean_env):
    clean_env.setenv('NEXUS_NO_PUBLIC_IP', '1')
    assert runtime.platform() in ('docker', 'local')
    assert runtime.host() is None
    assert runtime.public_base() is None
    assert runtime.info()['host'] is None


def test_the_image_ships_every_binary_the_panel_looks_for():
    """A binary the build stage installs but never copies into the runtime stage.

    The build still succeeds, every test that renders a config still passes, and
    the feature that needs that binary quietly switches itself off at boot —
    which is precisely how the WEB relay's own binary was missed once. The image
    is the only place it matters, so the Dockerfile is what is checked: each
    engine is installed into ``/usr/local/bin`` in the ``engines`` stage and must
    be copied into the runtime stage and made executable there.
    """
    from app.config import settings

    text = (Path(__file__).resolve().parents[1] / 'Dockerfile').read_text(encoding='utf-8')
    # The stages are what make this subtle: a path can appear anywhere in the
    # file and still be absent from the image a client runs.
    runtime_stage = text.split('FROM python:3.12-slim', 1)[1]
    # The line that makes the engines executable in the image, which is separate
    # from the COPY that brings them in (a copied file keeps the build stage's
    # mode only by accident).
    chmod = [line for line in text.splitlines() if 'chmod 0755' in line]
    assert chmod, 'the image must make its engines executable'
    for name in (settings.xray_binary, settings.singbox_binary, settings.mihomo_binary,
                 settings.mtg_binary, settings.webrelay_binary):
        assert name.startswith('/usr/local/bin/'), name
        assert f' {name} {name}' in runtime_stage, f'the runtime stage never copies {name}'
        assert f' {name}' in chmod[-1], f'{name} must be executable in the image'


def test_data_dir_is_always_writable(clean_env, tmp_path):
    clean_env.setenv('NEXUS_DATA_DIR', str(tmp_path / 'nexus'))
    folder = runtime.data_dir()
    assert os.path.isdir(folder)
    with open(os.path.join(folder, 'probe'), 'w', encoding='utf-8') as handle:
        handle.write('ok')
    # A second call is stable, so the sqlite file never moves between calls.
    assert runtime.data_dir() == folder
