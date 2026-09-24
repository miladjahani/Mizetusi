"""Railway's own TCP proxies, driven from the panel itself.

Railway publishes a service on HTTPS/443 and **nothing else**. Every capability
whose listener is a raw TCP port — the Reality transport, AnyTLS, MTProto and the
HTTP/SOCKS5 web proxies — needs its own *TCP proxy* there, and the port Railway
allocates for it is random and is *not* the port the container binds. That gap is
the single most common way a card looks «روشن» while the link it hands a user
answers nothing, so the panel closes it itself instead of asking an admin to copy
numbers between two dashboards:

```
container 0.0.0.0:8443   ← the listener the panel renders
   ↓  TCP proxy (created here)
*.proxy.rlwy.net:23177   ← the port a client must actually dial
```

Two facts about the API shape everything below:

* a TCP proxy maps an ``applicationPort`` (the container's) to a ``proxyPort``
  (Railway's) on a ``*.proxy.rlwy.net`` name — so the mapping, not the env vars,
  is what a link has to be built from (``RAILWAY_TCP_PROXY_*`` describes only the
  first proxy a service has);
* ``tcpProxyCreate`` is marked *deprecated* in the published schema — it is still
  the only create path the public API exposes, and its own deprecation note says
  why it is deprecated rather than what replaced it: a TCP proxy created this way
  is only **active after the service is redeployed**, which this module therefore
  does once (see :func:`redeploy`).

Credentials come from the environment like every other secret here — a Railway
project token (``RAILWAY_API_TOKEN``) or a project token
(``RAILWAY_TOKEN``/``NEXUS_RAILWAY_TOKEN``); Railway injects the project,
environment and service ids into every deployment by itself. Nothing is sent
anywhere but ``backboard.railway.com``, and every failure is returned as a
readable reason rather than raised, because a card has to be able to say *why*.
"""
import os

import httpx

ENDPOINT = 'https://backboard.railway.com/graphql/v2'
# An operator override first, then the two names Railway's own tooling uses.
TOKEN_KEYS = ('NEXUS_RAILWAY_TOKEN', 'RAILWAY_API_TOKEN', 'RAILWAY_TOKEN')
PROJECT_KEYS = ('NEXUS_RAILWAY_PROJECT_ID', 'RAILWAY_PROJECT_ID')
ENVIRONMENT_KEYS = ('NEXUS_RAILWAY_ENVIRONMENT_ID', 'RAILWAY_ENVIRONMENT_ID')
SERVICE_KEYS = ('NEXUS_RAILWAY_SERVICE_ID', 'RAILWAY_SERVICE_ID')
TIMEOUT = httpx.Timeout(20.0, connect=8.0)

# One query and two mutations — spelled out rather than built from strings so the
# exact fields a card needs are visible here, and a schema field that disappears
# fails loudly in a test instead of silently emptying a card.
_LIST = ('query($environmentId:String!,$serviceId:String!){'
         ' tcpProxies(environmentId:$environmentId,serviceId:$serviceId){'
         ' id domain proxyPort applicationPort syncStatus } }')
_CREATE = ('mutation($input:TCPProxyCreateInput!){ tcpProxyCreate(input:$input){'
           ' id domain proxyPort applicationPort syncStatus } }')
_DELETE = 'mutation($id:String!){ tcpProxyDelete(id:$id) }'
_REDEPLOY = ('mutation($serviceId:String!,$environmentId:String!){'
             ' serviceInstanceRedeploy(serviceId:$serviceId,environmentId:$environmentId) }')


def _env(*names):
    """First non-empty environment value among ``names``."""
    for name in names:
        value = (os.getenv(name) or '').strip()
        if value:
            return value
    return ''


def token():
    """The Railway API token, or ``''`` when an admin has not provided one."""
    return _env(*TOKEN_KEYS)


def project():
    return _env(*PROJECT_KEYS)


def environment():
    return _env(*ENVIRONMENT_KEYS)


def service():
    return _env(*SERVICE_KEYS)


def configured():
    """Whether every half of a Railway API call is present."""
    return bool(token() and project() and environment() and service())


def missing():
    """The names an admin still has to set, in the order they matter."""
    out = []
    if not token():
        out.append('RAILWAY_API_TOKEN')
    if not environment():
        out.append('RAILWAY_ENVIRONMENT_ID')
    if not service():
        out.append('RAILWAY_SERVICE_ID')
    if not project():
        out.append('RAILWAY_PROJECT_ID')
    return out


def _headers():
    """Both auth headers, because a project token uses the second one.

    Railway's public API takes a workspace/account token in ``Authorization`` and
    a project token in ``Project-Access-Token``. They are mutually exclusive and
    each is ignored when the wrong kind is supplied, so sending both lets one
    setting accept either kind without asking an admin which one they made.
    """
    value = token()
    return {'Authorization': f'Bearer {value}', 'Project-Access-Token': value,
            'Content-Type': 'application/json'}


def _call(query, variables=None):
    """One API call. Returns ``(data, error)`` — never raises.

    GraphQL answers 200 with an ``errors`` array for authorization failures too,
    so the array is inspected rather than the status code alone (see the API's own
    documentation: a query that runs but is denied returns 200).
    """
    if not token():
        return None, 'توکن API رِیلوی ست نشده است (RAILWAY_API_TOKEN)'
    payload = {'query': query, 'variables': variables or {}}
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(ENDPOINT, headers=_headers(), json=payload)
    except Exception as exc:
        return None, f'ارتباط با API رِیلوی برقرار نشد ({type(exc).__name__})'
    if response.status_code == 429:
        return None, 'محدودیت نرخ API رِیلوی (۴۲۹)؛ کمی بعد دوباره تلاش کنید'
    try:
        body = response.json()
    except Exception:
        return None, f'پاسخ API رِیلوی خوانده نشد (HTTP {response.status_code})'
    problems = body.get('errors') or []
    if problems:
        message = str((problems[0] or {}).get('message') or 'unknown error')
        code = str(((problems[0] or {}).get('extensions') or {}).get('code') or '')
        if code == 'INTERNAL_SERVER_ERROR' and 'not authorized' in message.lower():
            return None, 'توکن API رِیلوی مجاز نیست — نقش Workspace/Project Admin لازم است'
        return None, f'API رِیلوی خطا داد: {message[:200]}'
    return body.get('data'), None


# ------------------------------------------------------------------- proxies
def proxies():
    """Every TCP proxy this service has, with the port it really forwards.

    ``[{'id','domain','proxyPort','applicationPort','syncStatus'}]`` — the raw
    API shape, because :mod:`app.ports` is what decides how the panel stores it.
    """
    data, error = _call(_LIST, {'environmentId': environment(), 'serviceId': service()})
    if error:
        return [], error
    rows = ((data or {}).get('tcpProxies') or [])
    return [item for item in rows if isinstance(item, dict)], ''


def create(application_port):
    """Create a TCP proxy in front of ``application_port``. ``(proxy, error)``."""
    if not configured():
        return None, 'شناسه‌های پروژه/محیط/سرویس رِیلوی در دسترس نیست (' + '، '.join(missing()) + ')'
    port = int(application_port)
    if not 1 <= port <= 65535:
        return None, f'پورت داخلی نامعتبر است ({application_port})'
    data, error = _call(_CREATE, {'input': {'environmentId': environment(),
                                            'serviceId': service(),
                                            'applicationPort': port}})
    if error:
        return None, error
    item = (data or {}).get('tcpProxyCreate') or {}
    if not item.get('domain') or not item.get('proxyPort'):
        return None, 'رِیلوی پروکسی TCP را نساخت (پاسخ ناقص بود)'
    return item, ''


def remove(proxy_id):
    """Delete one TCP proxy by id. ``(ok, error)``."""
    data, error = _call(_DELETE, {'id': str(proxy_id)})
    if error:
        return False, error
    return bool((data or {}).get('tcpProxyDelete')), ''


def redeploy():
    """Redeploy once, so a TCP proxy created here is really active.

    The API's own deprecation note for ``tcpProxyCreate`` says a proxy created
    through it only takes effect after the service is redeployed. Since the pass
    that creates them runs inside the running container, the honest completion is
    one restart of that same service — after which the panel's stored mapping and
    the real proxy agree, and the links it publishes answer.
    """
    data, error = _call(_REDEPLOY, {'serviceId': service(), 'environmentId': environment()})
    if error:
        return False, error
    return bool(data), ''


def info(**extra):
    """The panel's view of this integration — never the token itself."""
    return {
        'configured': configured(),
        'missing': missing(),
        'endpoint': ENDPOINT,
        'project_id': project(),
        'environment_id': environment(),
        'service_id': service(),
        **extra,
    }
