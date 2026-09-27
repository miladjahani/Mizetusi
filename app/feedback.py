"""End-user feedback.

The panel is meant to be handed to many people, so the one thing an admin
cannot see from the dashboard is what those people *think*. A feedback row is
written from the public status window (``/portal/<token>``), which already
proves the sender owns a subscription — so the endpoint needs no login, but it
is still keyed to the user and throttled (see ``app/main.py``).

Everything here is deliberately small: validate, store, list, flip a status,
delete. Nothing in this module trusts its input, and a rating is normalised to
1–5 rather than stored as sent.
"""
import time

from app.db import execute, row, rows

# The categories the status window offers. ``other`` is the catch-all so a
# message is never rejected just because it fits no bucket.
KINDS = ('bug', 'slow', 'idea', 'other')
KIND_LABELS = {'bug': 'اشکال', 'slow': 'کندی', 'idea': 'پیشنهاد', 'other': 'دیگر'}
STATUSES = ('new', 'read', 'done')
MAX_MESSAGE = 2000
MAX_CONTACT = 200


def _clean(value, limit):
    """A single-line-safe, length-capped string (never ``None``)."""
    text = str(value if value is not None else '').strip()
    # Collapse runs of blank lines but keep the message readable, then cap it.
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')
    return text[:limit]


def _rating(value):
    """The rating as an int in 1–5, or ``None`` when none was given."""
    if value in (None, '', 0, '0'):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 5 else None


def submit(user, kind, message, rating=None, contact='', ip=''):
    """Store one feedback row for a subscription owner.

    Raises ``ValueError`` with a user-facing Persian message when the body is
    unusable, so the route can turn it into a 400 without re-implementing the
    rules.
    """
    text = _clean(message, MAX_MESSAGE)
    if len(text) < 3:
        raise ValueError('متن بازخورد خیلی کوتاه است (حداقل ۳ نویسه)')
    where = _clean(kind, 24).lower()
    if where not in KINDS:
        where = 'other'
    user = user or {}
    execute(
        'INSERT INTO feedback(user_id,username,token,kind,rating,message,contact,ip,status,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?)',
        (user.get('id'), user.get('username') or '', str(user.get('uuid') or ''),
         where, _rating(rating), text, _clean(contact, MAX_CONTACT), _clean(ip, 64),
         'new', int(time.time())),
    )
    return {'kind': where, 'kind_label': KIND_LABELS[where], 'rating': _rating(rating)}


def _decorate(item):
    item = dict(item)
    item['kind_label'] = KIND_LABELS.get(item.get('kind'), item.get('kind') or '')
    return item


def list_all(status='', limit=200):
    """Newest first, with a filter by status when one is asked for."""
    limit = max(1, min(int(limit or 200), 500))
    wanted = _clean(status, 16).lower()
    if wanted in STATUSES:
        data = rows('SELECT * FROM feedback WHERE status=? ORDER BY created_at DESC LIMIT ?',
                    (wanted, limit))
    else:
        data = rows('SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?', (limit,))
    return [_decorate(item) for item in data]


def set_status(feedback_id, status):
    """Move one row to ``read``/``done`` (anything else is rejected)."""
    wanted = _clean(status, 16).lower()
    if wanted not in STATUSES:
        raise ValueError('وضعیت نامعتبر است')
    if not row('SELECT id FROM feedback WHERE id=?', (feedback_id,)):
        raise ValueError('این بازخورد پیدا نشد')
    execute('UPDATE feedback SET status=? WHERE id=?', (wanted, feedback_id))
    return {'id': int(feedback_id), 'status': wanted}


def remove(feedback_id):
    if not row('SELECT id FROM feedback WHERE id=?', (feedback_id,)):
        raise ValueError('این بازخورد پیدا نشد')
    execute('DELETE FROM feedback WHERE id=?', (feedback_id,))
    return {'id': int(feedback_id), 'deleted': True}


def summary():
    """Counts the panel badge and the inbox header both read."""
    total = row('SELECT COUNT(*) AS n FROM feedback')
    fresh = row("SELECT COUNT(*) AS n FROM feedback WHERE status='new'")
    tally = {'total': int((total or {}).get('n') or 0),
             'new': int((fresh or {}).get('n') or 0)}
    tally['done'] = max(0, tally['total'] - tally['new'])
    return tally
