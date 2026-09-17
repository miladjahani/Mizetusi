# ZEUS Cloudflare WebSocket Front

This Worker is a narrow WebSocket reverse proxy for the ZEUS `/ws` endpoint. It does not expose a generic URL-fetch or open-proxy endpoint.

Set `ZEUS_ORIGIN` to the Railway HTTPS origin and deploy the Worker. Then enter the Worker URL in ZEUS → Settings → Cloudflare Worker. ZEUS will health-probe Cloudflare IPs from Railway, maintain the Node Catalog, and subscriptions will use healthy Cloudflare front nodes.
