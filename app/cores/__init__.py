"""Second engines for the protocols Xray cannot serve.

Xray is this deployment's protocol engine and it covers VLESS, VMess, Trojan and
Shadowsocks — but it has no inbound for the newer anti-DPI protocols. AnyTLS and
TUIC v5 are implemented by **sing-box** and **mihomo** only, so one of those
engines runs next to it, in the same container, on its own public port.

Nothing here is a bolt-on: a hosted protocol becomes an ordinary transport
profile, so it flows through the same subscription generator, the same per-user
credentials, the same node scope and the same client formats as everything else.
:mod:`app.cores.profiles` is pure data, :mod:`app.cores.engines` turns a profile
set into each engine's own config (they agree on almost nothing), and
:mod:`app.cores.service` supervises the processes and answers the one question the
rest of the panel asks: *is this protocol really listening right now?*
"""
