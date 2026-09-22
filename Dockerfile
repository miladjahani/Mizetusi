# Official Xray-core binary + NEXUS Python control plane in one Railway service.

# The second engines. AnyTLS and TUIC v5 have no Xray inbound at all — sing-box
# and mihomo are the only implementations — so both ship with the image and an
# admin picks one per protocol in the panel. They are static Go binaries; the
# versions are pinned so a configuration that validated once keeps validating.
# ``mihomo`` is the *compatible* build: it makes no CPU-feature assumptions.
FROM debian:bookworm-slim AS engines
ARG SINGBOX_VERSION=1.14.1
ARG MIHOMO_VERSION=1.19.31
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL -o /tmp/sing-box.tar.gz "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-linux-amd64.tar.gz" \
 && tar -xzf /tmp/sing-box.tar.gz -C /tmp \
 && install -m 0755 "/tmp/sing-box-${SINGBOX_VERSION}-linux-amd64/sing-box" /usr/local/bin/sing-box \
 && curl -fsSL -o /tmp/mihomo.gz "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/mihomo-linux-amd64-compatible-v${MIHOMO_VERSION}.gz" \
 && gunzip -f /tmp/mihomo.gz \
 && install -m 0755 /tmp/mihomo /usr/local/bin/mihomo \
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
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY cloudflare-worker ./cloudflare-worker
COPY pyproject.toml railway.json .env.example README.md ./
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /app /data \
 && chmod 0755 /usr/local/bin/xray /usr/local/bin/sing-box /usr/local/bin/mihomo
USER appuser
# 8080 is the panel and the WebSocket edge; the hosted protocols listen on their
# own public ports (AnyTLS: TCP, TUIC: UDP) and each one needs its own forwarding.
EXPOSE 8080 8444 8445
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/health',timeout=3)"
# --forwarded-allow-ips is deliberately loopback-only. With '*' uvicorn replaces
# the TCP peer with the *leftmost* X-Forwarded-For entry, which is whatever the
# client sent — that is what made the header unchallengeable and the login
# throttle forgeable. The app resolves the real address itself
# (app/core/clientip.py), where the raw peer is still visible, and reads
# X-Forwarded-Proto itself for cookie policy, so nothing is lost here.
CMD ["sh","-c","exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='127.0.0.1' --workers 1 --timeout-keep-alive 30"]
