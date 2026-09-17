# Official Xray-core binary + ZEUS Python control plane in one Railway service.
FROM ghcr.io/xtls/xray-core:26.9.9 AS xray
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY cloudflare-worker ./cloudflare-worker
COPY pyproject.toml railway.json .env.example README.md ./
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /app /data /usr/local/bin/xray
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/health',timeout=3)"
CMD ["sh","-c","exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*' --workers 1 --timeout-keep-alive 30"]
