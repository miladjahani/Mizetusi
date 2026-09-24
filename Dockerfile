# Official Xray-core binary + NEXUS Python control plane in one Railway service.

# The second engines. AnyTLS and TUIC v5 have no Xray inbound at all — sing-box
# and mihomo are the only implementations — so both ship with the image and an
# admin picks one per protocol in the panel. They are static Go binaries; the
# versions are pinned so a configuration that validated once keeps validating.
# ``mihomo`` is the *compatible* build: it makes no CPU-feature assumptions.
#
# ``mtg`` is the other third binary: Telegram's MTProto proxy is its own protocol
# (the ``tg://proxy`` link), nothing in Xray or sing-box speaks it, and mtg is the
# implementation that still handles the current FakeTLS handshake. Same rule as
# the engines — the version is pinned, and it is the ``amd64`` (not ``-v3``)
# build so it runs on every host.
#
# ``mtproto-proxy`` (mtproto.zig) is the fourth: it is the only implementation of
# Telegram Desktop 7.1's **WEB** proxy — MTProto carried inside an ordinary HTTPS
# page and a same-origin WebSocket, so it needs no public raw port (see
# app/telegram/webrelay.py). One binary, two modes: ``mtproto-proxy config.toml``
# is the loopback data plane the relay dials, ``mtproto-proxy web-relay config.toml``
# is the relay our own domain serves. Its tarball is checksum-verified against the
# digest published with the release, because it is the one engine that terminates
# the browser-facing half.
FROM debian:bookworm-slim AS engines
ARG SINGBOX_VERSION=1.14.1
ARG MIHOMO_VERSION=1.19.31
ARG MTG_VERSION=2.2.8
ARG MTPROTOZIG_VERSION=1.15.3
ARG MTPROTOZIG_SHA256=0403bbd70f6a1f6c011efd8e6dccdb96b10556c6b16051be43631619d9724ba8
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL -o /tmp/sing-box.tar.gz "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-linux-amd64.tar.gz" \
 && tar -xzf /tmp/sing-box.tar.gz -C /tmp \
 && install -m 0755 "/tmp/sing-box-${SINGBOX_VERSION}-linux-amd64/sing-box" /usr/local/bin/sing-box \
 && curl -fsSL -o /tmp/mihomo.gz "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/mihomo-linux-amd64-compatible-v${MIHOMO_VERSION}.gz" \
 && gunzip -f /tmp/mihomo.gz \
 && install -m 0755 /tmp/mihomo /usr/local/bin/mihomo \
 && curl -fsSL -o /tmp/mtg.tar.gz "https://github.com/9seconds/mtg/releases/download/v${MTG_VERSION}/mtg-${MTG_VERSION}-linux-amd64.tar.gz" \
 && tar -xzf /tmp/mtg.tar.gz -C /tmp \
 && install -m 0755 "/tmp/mtg-${MTG_VERSION}-linux-amd64/mtg" /usr/local/bin/mtg \
 && curl -fsSL -o /tmp/mtproto-proxy.tar.gz "https://github.com/sleep3r/mtproto.zig/releases/download/v${MTPROTOZIG_VERSION}/mtproto-proxy-linux-x86_64.tar.gz" \
 && echo "${MTPROTOZIG_SHA256}  /tmp/mtproto-proxy.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/mtproto-proxy.tar.gz -C /tmp \
 && install -m 0755 /tmp/mtproto-proxy-linux-x86_64 /usr/local/bin/mtproto-proxy \
 && rm -rf /var/lib/apt/lists/*

FROM ghcr.io/xtls/xray-core:26.9.9 AS xray
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY --from=engines /usr/local/bin/sing-box /usr/local/bin/sing-box
COPY --from=engines /usr/local/bin/mihomo /usr/local/bin/mihomo
COPY --from=engines /usr/local/bin/mtg /usr/local/bin/mtg
# Both halves of the WEB relay are this one binary, so a missing COPY here (the
# build stage installs it and the runtime stage is what a client ever sees) does
# not fail the build — it silently switches the panel's default Telegram proxy
# off at boot. ``tests/test_runtime.py`` asserts every path the app looks for is
# in the image.
COPY --from=engines /usr/local/bin/mtproto-proxy /usr/local/bin/mtproto-proxy
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY cloudflare-worker ./cloudflare-worker
COPY pyproject.toml railway.json .env.example README.md ./
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /app /data \
 && chmod 0755 /usr/local/bin/xray /usr/local/bin/sing-box /usr/local/bin/mihomo /usr/local/bin/mtg /usr/local/bin/mtproto-proxy
USER appuser
# 8080 is the panel and the WebSocket edge; every protocol that terminates its own
# connection listens on its own public port and needs its own forwarding:
# AnyTLS 8444 (TCP), TUIC 8445 (UDP), MTProto 8446 (TCP), the HTTP and SOCKS5 web
# proxies 8448/8449 (TCP). The WEB proxy is deliberately absent from this list:
# its carrier is HTTPS on the panel's own 443 plus a same-origin WebSocket, so it
# is the one Telegram proxy that is reachable here without any forwarding (8081
# and 8447 are loopback-only: the relay, and the data plane it dials).
EXPOSE 8080 8444 8445 8446 8448 8449
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/health',timeout=3)"
# --forwarded-allow-ips is deliberately loopback-only. With '*' uvicorn replaces
# the TCP peer with the *leftmost* X-Forwarded-For entry, which is whatever the
# client sent — that is what made the header unchallengeable and the login
# throttle forgeable. The app resolves the real address itself
# (app/core/clientip.py), where the raw peer is still visible, and reads
# X-Forwarded-Proto itself for cookie policy, so nothing is lost here.
# The port is ``NEXUS_HTTP_PORT`` first, then the platform's own $PORT, then 8080.
# The override exists for one measured reason: on Railway, creating a TCP proxy
# makes Railway hand the service that proxy's *application* port as $PORT — and a
# raw transport (Reality, AnyTLS, MTProto, a web proxy) is already listening on it,
# so uvicorn would die on «address already in use» and the panel crash-loop.
# ``app/railway.py`` pins the port the edge is really on (``pin_http_port``) before
# it redeploys, and this is the other half of that: the same two names, in the same
# order, so app/railway.py and this command can never disagree about the edge port.
CMD ["sh","-c","exec uvicorn app.main:app --host 0.0.0.0 --port ${NEXUS_HTTP_PORT:-${PORT:-8080}} --proxy-headers --forwarded-allow-ips='127.0.0.1' --workers 1 --timeout-keep-alive 30"]
