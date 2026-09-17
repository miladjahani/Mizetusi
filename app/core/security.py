"""Panel authentication.

``SessionManager`` owns everything about a signed-in admin: how a token is
signed, whether a presented token is valid, and which cookie policy the current
request needs. ``LoginThrottle`` keeps the brute-force counter out of the route
handlers.
"""
import time

import jwt


class SessionManager:
    """Signed (HS256) admin sessions carried by cookie or header."""

    COOKIE = 'nexus_session'
    HEADER = 'x-nexus-session'
    SUBJECT = 'admin'

    def __init__(self, secret_provider, ttl_provider, default_ttl=604800):
        # Both values are callables: the signing secret and the session lifetime
        # can change at runtime (rotate-session / settings) without a restart.
        self._secret = secret_provider
        self._ttl = ttl_provider
        self._default_ttl = default_ttl

    # ------------------------------------------------------------------ tokens
    def ttl(self, remember=False):
        base = int(self._ttl() or self._default_ttl)
        return max(base, 30 * 86400) if remember else max(base, 300)

    def issue(self, remember=False):
        now = int(time.time())
        return jwt.encode(
            {'iat': now, 'exp': now + self.ttl(remember), 'sub': self.SUBJECT},
            self._secret(), algorithm='HS256',
        )

    def verify(self, token):
        if not token:
            return False
        try:
            return jwt.decode(token, self._secret(), algorithms=['HS256']).get('sub') == self.SUBJECT
        except jwt.PyJWTError:
            return False

    def presented(self, request):
        """The token a request carries, header first (works without cookies)."""
        return request.headers.get(self.HEADER) or request.cookies.get(self.COOKIE)

    # ----------------------------------------------------------------- cookies
    @staticmethod
    def _is_https(request):
        proto = (request.headers.get('x-forwarded-proto') or '').split(',')[0].strip().lower()
        return (proto or request.url.scheme or 'http') == 'https'

    @staticmethod
    def _cross_site(request):
        return (request.headers.get('sec-fetch-site') or '').strip().lower() == 'cross-site'

    def policy(self, request):
        """Cookie flags for this request.

        SameSite=strict is the goal, but the panel is also rendered inside an
        embedded preview pane on another origin, where a strict cookie is never
        sent back (login succeeds, then every request looks unauthenticated).
        Cross-site requests therefore get SameSite=None; Secure, the only policy
        browsers accept for an embedded session.
        """
        cross = self._cross_site(request)
        secure = True if cross else self._is_https(request)
        same = 'none' if cross else ('strict' if secure else 'lax')
        return {'secure': secure, 'samesite': same}

    def apply(self, request, response, token=None, remember=False):
        policy = self.policy(request)
        response.set_cookie(
            self.COOKIE, token or self.issue(remember), httponly=True,
            secure=policy['secure'], samesite=policy['samesite'],
            max_age=self.ttl(remember), path='/',
        )
        return response

    def clear(self, response):
        response.delete_cookie(self.COOKIE, path='/')
        return response


class LoginThrottle:
    """Sliding-window limiter for password attempts, keyed by client IP."""

    def __init__(self, limit=10, window=300):
        self.limit = max(1, int(limit))
        self.window = max(30, int(window))
        self._hits = {}

    def _prune(self, key, now):
        recent = [t for t in self._hits.get(key, []) if now - t < self.window]
        if recent:
            self._hits[key] = recent
        else:
            self._hits.pop(key, None)
        return recent

    def blocked(self, key):
        now = time.time()
        return len(self._prune(key, now)) >= self.limit

    def fail(self, key):
        now = time.time()
        recent = self._prune(key, now)
        recent.append(now)
        self._hits[key] = recent

    def reset(self, key):
        self._hits.pop(key, None)

