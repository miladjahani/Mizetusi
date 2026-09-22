"""The hosted protocols, as data.

A profile here is the same shape the rest of the panel already understands (see
``app.subscriptions.transports``), so a hosted protocol needs no special case
anywhere downstream — it is a normal entry in the subscription, in the node
drawer and in the panel's coverage view.

Everything is opt-in and per protocol:

* ``enabled`` — an admin switch. A protocol that is on but whose port the host
  cannot expose is still not published: see :mod:`app.cores.service`.
* ``port`` — the *public* port the listener binds (a VPS needs nothing else; on
  Railway each port needs its own TCP proxy, and UDP is unavailable).
* ``engine`` — which of the two engines hosts it. Both can serve both protocols,
  so this is a real choice, and the default (``sing-box``) is the one with the
  more complete server implementation.
"""

ANYTLS = 'anytls'
TUIC = 'tuic'

# ``needs`` is what the host has to be able to expose: AnyTLS is a TCP + TLS
# protocol, TUIC is QUIC and therefore UDP only. It is what turns "enabled" into
# "actually reachable" — the panel says which one is missing instead of handing a
# client a link nothing answers on.
PROFILES = (
    {
        'id': 'anytls', 'protocol': ANYTLS, 'tag': 'AnyTLS · TLS',
        'network': 'tcp', 'security': 'tls', 'needs': 'tcp',
        'engine': 'singbox',
        'note': 'ضد DPI: TLS با padding و کانال‌های بیکار؛ Xray اصلاً اینباند AnyTLS ندارد.',
    },
    {
        'id': 'tuic', 'protocol': TUIC, 'tag': 'TUIC v5 · QUIC',
        'network': 'quic', 'security': 'tls', 'needs': 'udp',
        'engine': 'singbox',
        'note': 'روی QUIC؛ برای بازی و تماس تصویری کم‌تأخیرتر از TCP است و UDP را کامل عبور می‌دهد.',
    },
)

PROFILE_IDS = tuple(item['id'] for item in PROFILES)
BY_ID = {item['id']: item for item in PROFILES}

# Engines that can host a protocol, in the order the panel offers them.
ENGINES = ('singbox', 'mihomo')
ENGINE_LABELS = {'singbox': 'sing-box', 'mihomo': 'mihomo'}

# Everything belongs to one transport group so the generator, the node scope and
# the panel's transport dropdown treat hosted protocols consistently.
GROUP = 'core'


def profile(profile_id):
    return BY_ID.get(str(profile_id or '').strip().lower())


def enabled_key(item):
    return 'core_%s_enabled' % item['id']


def port_key(item):
    return 'core_%s_port' % item['id']


def engine_key(item):
    return 'core_%s_engine' % item['id']
