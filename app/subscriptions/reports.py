"""Advanced subscription analytics: usage reports and expiry/quota alerts.

Two things an admin running a crowd needs from the subscription tab: a per-user
*usage report* (who is actually consuming, and against which ceiling) and the
*alerts* that must arrive **before** the ceiling does — a user within N days of
expiry, or past a share of their quota.

Both are pure reads over the ``users`` table plus the two thresholds stored in
settings, so nothing here mutates state and a report can be rendered on every
panel open without side effects. Scheduling is the panel's polling: the same
numbers are recomputed whenever the section loads, so an alert appears the moment
it becomes true instead of waiting on a cron nobody set up.
"""
import time

from app.db import rows

# The thresholds are settings keys so they appear in ``GENERAL_SETTING_KEYS`` and
# are resettable from the «پیشرفته» tab like every other preference.
EXPIRY_ALERT_KEY = 'alert_expiry_days'
QUOTA_ALERT_KEY = 'alert_quota_percent'
DEFAULT_EXPIRY_DAYS = 3
DEFAULT_QUOTA_PERCENT = 80

# Alert kinds, worst first: a disabled user is not an alert (the admin said so),
# an expired one is a decision to make, and «near expiry» is the early warning.
ALERT_ORDER = ('quota', 'expired', 'expiring')


def thresholds():
    """The two alert thresholds, clamped to sane values."""
    from app.core.settings_store import store

    def _number(key, default, low, high):
        raw = store.get(key)
        try:
            value = int(float(raw)) if raw not in (None, '') else default
        except (TypeError, ValueError):
            value = default
        return min(max(value, low), high)

    return {
        'expiry_days': _number(EXPIRY_ALERT_KEY, DEFAULT_EXPIRY_DAYS, 0, 365),
        'quota_percent': _number(QUOTA_ALERT_KEY, DEFAULT_QUOTA_PERCENT, 1, 100),
    }


def save_thresholds(expiry_days=None, quota_percent=None):
    """Persist one or both thresholds; returns the stored pair."""
    from app.core.settings_store import store

    if expiry_days is not None:
        value = int(float(expiry_days))
        if not 0 <= value <= 365:
            raise ValueError('alert_expiry_days must be between 0 and 365')
        store.set(EXPIRY_ALERT_KEY, str(value))
    if quota_percent is not None:
        value = int(float(quota_percent))
        if not 1 <= value <= 100:
            raise ValueError('alert_quota_percent must be between 1 and 100')
        store.set(QUOTA_ALERT_KEY, str(value))
    return thresholds()


def _status(user, percent, days_left):
    """One word that says why a user needs (or does not need) attention."""
    if not user.get('is_active'):
        return 'disabled'
    if days_left is not None and days_left < 0:
        return 'expired'
    if percent is not None and percent >= 100:
        return 'quota'
    return 'ok'


def _row(user, now):
    """One user's usage, shaped for the table and the alert list."""
    limit = float(user['limit_gb']) if user.get('limit_gb') is not None else None
    used = round(float(user.get('used_gb') or 0), 4)
    percent = round(used / limit * 100, 1) if limit else None
    expires = user.get('expires_at')
    days_left = None
    if expires:
        # Rounded toward the deadline is wrong here: a user with 23 hours left must
        # read as «0 روز» (about to expire) rather than as a full day the admin
        # schedules around but never reaches.
        days_left = int((int(expires) - now) // 86400)
    return {
        'username': user['username'],
        'uuid': user.get('uuid'),
        'active': bool(user.get('is_active')),
        'used_gb': used,
        'lifetime_gb': round(float(user.get('lifetime_used_gb') or 0), 4),
        'limit_gb': limit,
        'percent': percent,
        'used_req': int(user.get('used_req') or 0),
        'limit_req': user.get('limit_req'),
        'expires_at': expires,
        'days_left': days_left,
        'start_on_first_connect': bool(user.get('start_on_first_connect')),
        'status': _status(user, percent, days_left),
    }


def usage(sort='usage'):
    """Every user's usage, ordered by the report's own sort key."""
    now = int(time.time())
    items = [_row(u, now) for u in rows('SELECT * FROM users')]
    if sort == 'name':
        items.sort(key=lambda item: str(item['username']).lower())
    elif sort == 'expiry':
        items.sort(key=lambda item: (item['days_left'] is None, item['days_left'] if item['days_left'] is not None else 0))
    else:
        items.sort(key=lambda item: item['used_gb'], reverse=True)
    return items


def alerts(items=None, thresholds_=None):
    """The users the thresholds say need attention, worst first.

    A disabled user is deliberately *not* an alert: the admin already made that
    call. Expiry and quota are, because each is a decision still to be made.
    """
    limit = thresholds_ or thresholds()
    items = items if items is not None else usage()
    found = []
    for item in items:
        if item['status'] == 'disabled':
            continue
        kinds = []
        if item['percent'] is not None and item['percent'] >= limit['quota_percent']:
            kinds.append('quota')
        if item['days_left'] is not None and item['days_left'] < 0:
            kinds.append('expired')
        elif item['days_left'] is not None and item['days_left'] <= limit['expiry_days']:
            kinds.append('expiring')
        if kinds:
            found.append({**item, 'alerts': kinds})
    rank = {kind: index for index, kind in enumerate(ALERT_ORDER)}
    found.sort(key=lambda item: min(rank.get(kind, 9) for kind in item['alerts']))
    return found


def report(sort='usage'):
    """The whole advanced-subscription payload: totals, alerts and the table."""
    limit = thresholds()
    items = usage(sort=sort)
    found = alerts(items, limit)
    active = [item for item in items if item['active']]
    metered = [item for item in items if item['limit_gb']]
    return {
        'generated_at': int(time.time()),
        'thresholds': limit,
        'usage': items,
        'alerts': found,
        'alert_counts': {kind: sum(1 for item in found if kind in item['alerts']) for kind in ALERT_ORDER},
        'totals': {
            'users': len(items),
            'active': len(active),
            'disabled': len(items) - len(active),
            'used_gb': round(sum(item['used_gb'] for item in items), 4),
            'lifetime_gb': round(sum(item['lifetime_gb'] for item in items), 4),
            'limited': len(metered),
            'expiring': sum(1 for item in found if 'expiring' in item['alerts']),
            'expired': sum(1 for item in found if 'expired' in item['alerts']),
            'over_quota': sum(1 for item in found if 'quota' in item['alerts']),
        },
    }
