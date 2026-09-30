"""What the deployment does not have to announce.

A panel reached only by its owner is still a **fingerprint**: an open port, a
``Server: uvicorn`` header, an ``/openapi.json`` that lists every route this
product has, and a ``/health`` that names its own service are four cheap signals
that add up to «this address is a proxy panel» — which is exactly the kind of
address a filter recognises and blocks.

This module is the one place that decides what stays quiet, and every item is a
switch rather than a hard-coded rule, because a deployment that hides too much
also hides itself from its own operator:

* **response headers** — ``Server`` and ``X-Powered-By`` are removed (or replaced
  with whatever plausible value an admin sets), on every response. That is the
  cheapest fingerprint to kill and the one uvicorn puts there by default.
* **the API docs** — FastAPI publishes ``/docs``, ``/redoc`` and ``/openapi.json``
  by default. The last one is free reconnaissance: it names every route, so a
  scanner learns the product before sending a single packet. They are **off**
  until an admin switches them on, and even then the OpenAPI document no longer
  carries the product's own title.
* **the health body** — a public ``/health`` answers ``{"ok": true}``. The full
  body (service name, database state, uptime) is still there for the *inside*:
  the loopback caller that runs a health check, and the panel's own diagnostics.
* **the subscription headers** — ``X-NEXUS-*`` on ``/sub/{token}`` are useful
  diagnostics and are kept by default; a deployment that wants every scrap of
  identifying header gone can drop them with one switch.

Nothing here hides the transport: the disguise of the *traffic* is
:mod:`app.subscriptions.obfuscation` and the TLS that terminates in front of it.
This is about the HTTP surface the panel itself shows.
"""
from app.core.settings_store import store

# One settings key per switch, all off-by-default except the header scrub.
HEADERS = 'stealth_headers'            # strip Server / X-Powered-By (default on)
DOCS = 'stealth_docs'                  # serve /docs and /openapi.json (default off)
HEALTH = 'stealth_health'              # minimal public /health (default on)
SUB_HEADERS = 'stealth_sub_headers'    # keep X-NEXUS-* on /sub (default on)
SERVER_HEADER = 'stealth_server_header'  # plausible Server value ('' = send none)

# Headers that name the stack, whatever put them there.
IDENTIFYING = ('server', 'x-powered-by', 'x-aspnet-version', 'x-served-by',
               'x-generator', 'x-runtime')
# The panel's own diagnostic prefix on a subscription response.
SUB_PREFIX = 'x-nexus-'

DEFAULTS = {HEADERS: '1', DOCS: '0', HEALTH: '1', SUB_HEADERS: '1', SERVER_HEADER: ''}

LABELS = {
    HEADERS: 'حذف هدرهای معرف (Server / X-Powered-By)',
    DOCS: 'انتشار مستندات API (/docs و /openapi.json)',
    HEALTH: 'پاسخ کوتاه برای /health عمومی',
    SUB_HEADERS: 'نگه‌داشتن هدرهای X-NEXUS-* روی سابلینک',
    SERVER_HEADER: 'مقدار هدر Server به‌جای حذف',
}


def _on(key):
    """A switch that is **on unless it was explicitly turned off**.

    ``'0'``/``''`` means off; anything else (including a key that was never
    written) means on. That is the safe direction for the scrub: a deployment
    that has never heard of this module is already scrubbed.
    """
    raw = store.get(key, DEFAULTS.get(key, '1'))
    return str(raw if raw is not None else '1').strip().lower() not in ('', '0', 'false', 'off', 'no')


def headers_on():
    return _on(HEADERS)


def docs_enabled():
    return _on(DOCS)


def minimal_health():
    return _on(HEALTH)


def sub_headers_on():
    return _on(SUB_HEADERS)


def server_header():
    """The plausible ``Server`` value an admin wants instead of no header."""
    return str(store.get(SERVER_HEADER, '') or '').strip()


def catalog():
    """The panel view: every switch, its state and what it changes."""
    return {
        'switches': [{'key': key, 'label': LABELS[key],
                      'on': _on(key), 'default': DEFAULTS.get(key, '1')}
                     for key in (HEADERS, DOCS, HEALTH, SUB_HEADERS)],
        'server_header': server_header(),
        'server_header_key': SERVER_HEADER,
    }


def save(payload):
    """Write the switches, validating the one free-text value there is."""
    changed = []
    for key in (HEADERS, DOCS, HEALTH, SUB_HEADERS):
        if key not in (payload or {}):
            continue
        store.set(key, '1' if str(payload[key]).strip().lower() in
                  ('1', 'true', 'on', 'yes') else '0')
        changed.append(key)
    if SERVER_HEADER in (payload or {}):
        value = str(payload[SERVER_HEADER] or '').strip()
        # A header value is one line and one token-ish string: a newline in it
        # would split the response, so it is refused rather than sanitised.
        if '\n' in value or '\r' in value or len(value) > 64:
            raise ValueError('مقدار هدر Server باید یک خط کوتاه باشد')
        store.set(SERVER_HEADER, value)
        changed.append(SERVER_HEADER)
    return changed


def _drop(headers, name):
    """Remove one header, whatever kind of mapping the response is holding.

    Starlette's ``MutableHeaders`` has no ``pop``, and a plain ``dict`` may not
    have the key at all, so the removal is spelled once here instead of at every
    call site.
    """
    try:
        del headers[name]
        return True
    except (KeyError, TypeError):
        return False


def scrub(response):
    """Remove (or replace) the headers that name the stack.

    Called from the one middleware every response passes through, so it covers
    the panel, the portal, the subscription routes and the static assets alike.
    """
    if headers_on():
        for name in list(IDENTIFYING):
            _drop(response.headers, name)
        replacement = server_header()
        if replacement:
            response.headers['Server'] = replacement
    if not sub_headers_on():
        for name in [key for key in response.headers.keys()
                     if key.lower().startswith(SUB_PREFIX)]:
            _drop(response.headers, name)
    return response


def public_health(full):
    """What an unauthenticated ``/health`` answers.

    The full body is a map of this deployment's internals — which database, how
    long it has been up, what the service calls itself — so a scanner that
    reaches the panel's own health route gets the one byte that says «alive» and
    nothing else. The inside (a loopback health check, the panel's diagnostics)
    still gets the real thing.
    """
    if not minimal_health():
        return full
    return {'ok': bool((full or {}).get('ok'))}


def is_internal(request):
    """Whether a request came from this very container.

    A loopback peer cannot be a scanner on the other side of a proxy, so it is
    the one caller allowed the detailed health body. An ``X-Forwarded-For`` is
    deliberately **not** trusted here: forging one is exactly the kind of thing
    this module exists to stop counting as proof.
    """
    client = getattr(request, 'client', None)
    host = str(getattr(client, 'host', '') or '')
    return host in ('127.0.0.1', '::1', 'localhost') or host.startswith('127.')
