"""Telegram proxies: MTProto, the HTTP/SOCKS5 web proxy and Telegram Web.

Telegram is the one thing an Iranian user asks for before anything else, and it
is *not* a VPN subscription: the app has its own proxy protocol (MTProto), its
desktop client takes an ordinary HTTP/SOCKS5 proxy, and the web version is a
website that has to be reached through a host that is not blocked. This package
publishes all three from the deployment that is already serving the VPN:

* :mod:`app.telegram.mtproto` — the ``tg://proxy`` link, served by ``mtg``;
* :mod:`app.telegram.webproxy` — HTTP/SOCKS5 inbounds on the running Xray engine,
  one credential per user, published as ready-to-paste proxy lines;
* :mod:`app.telegram.webapp` — ``web.telegram.org`` proxied through this
  deployment's own domain (HTTP + WebSocket), with the Cloudflare Worker in
  front of it carrying the same prefix;
* :mod:`app.telegram.service` — the one payload the panel card, the API and the
  public status window all read, so the three cannot drift apart.

Nothing here is on by default: each switch publishes its links only once its
listener is really running **and** its port is reachable from outside, which is
the same rule the transports and the second engines follow.
"""
