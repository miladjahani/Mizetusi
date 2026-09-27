"""End-user feedback.

The feature exists so a deployment handed to many people can hear back from
them. Two halves, two very different trust models, and the tests are split the
same way:

* the **public** half (``POST /api/feedback``) is authorised by a subscription
  token rather than a login, so the tests pin that an unknown token is refused,
  that a short message is refused, and that a script cannot fill the inbox;
* the **admin** half (the ``/api/feedback`` inbox) is authorised by the panel
  session, so the tests pin that it is closed without one and that status
  changes and deletes go through.

The database is cleaned around every test, and the public limiter is reset,
because both are process-wide and would otherwise leak between tests.
"""
import itertools
import os

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('SQLITE_PATH', '/tmp/nexus-feedback-test.db')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexus-feedback-test.db')

import pytest
from fastapi.testclient import TestClient

from app import feedback
from app.config import settings as cfg
from app.db import execute, init_db
from app.main import _setting, app, feedback_throttle

init_db()
client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


@pytest.fixture(autouse=True)
def clean():
    # Every module in the suite shares one file-backed database, so the users
    # this module makes (and their rows) are removed again — a leaked username
    # would break the next module that picks the same name.
    def wipe():
        execute('DELETE FROM feedback')
        execute("DELETE FROM users WHERE username LIKE 'fbtest%'")
        feedback_throttle._hits.clear()
    wipe()
    yield
    wipe()


_counter = itertools.count(1)


def _user():
    """Make a real user and hand back ``(username, token)``.

    The token is the ``uuid`` the status window posts back, so the tests exercise
    the same authorization the public half really uses.
    """
    name = f'fbtest{next(_counter)}'
    response = client.post('/api/users', headers=h(), json={'username': name})
    assert response.status_code == 200, response.text
    return name, response.json()['uuid']


# ------------------------------------------------------------------ the public half
def test_a_note_needs_a_real_subscription_token():
    """No token, or one nobody has, is a 404 — never a row."""
    assert client.post('/api/feedback', json={'message': 'سلام سلام'}).status_code == 404
    assert client.post('/api/feedback', json={'token': 'not-a-real-token',
                                              'message': 'سلام سلام'}).status_code == 404
    assert feedback.summary()['total'] == 0


def test_a_short_message_is_refused():
    _, token = _user()
    bad = client.post('/api/feedback', json={'token': token, 'message': 'x'})
    assert bad.status_code == 400
    assert feedback.summary()['total'] == 0


def test_a_note_is_stored_with_a_normalised_kind_and_rating():
    name, token = _user()
    ok = client.post('/api/feedback', json={'token': token, 'kind': 'idea',
                                            'message': '  سرعت روی موبایل پایین است  ',
                                            'rating': 9, 'contact': '@someone'})
    assert ok.status_code == 200, ok.text
    assert ok.json()['kind'] == 'idea'
    # A rating outside 1–5 is dropped rather than stored as sent.
    items = feedback.list_all()
    assert len(items) == 1
    assert items[0]['message'] == 'سرعت روی موبایل پایین است'
    assert items[0]['rating'] is None
    assert items[0]['status'] == 'new'
    assert items[0]['username'] == name


def test_an_unknown_kind_falls_back_instead_of_being_rejected():
    _, token = _user()
    assert client.post('/api/feedback', json={'token': token, 'kind': 'zzz',
                                              'message': 'یک پیام کامل'}).status_code == 200
    assert feedback.list_all()[0]['kind'] == 'other'


def test_the_public_endpoint_is_rate_limited_per_address():
    """A script cannot fill the inbox: the limiter is what stops it."""
    _, token = _user()
    accepted = 0
    for index in range(feedback_throttle.limit + 3):
        response = client.post('/api/feedback', json={'token': token,
                                                      'message': f'پیام شماره {index}'})
        if response.status_code == 200:
            accepted += 1
    assert accepted == feedback_throttle.limit
    assert feedback.summary()['total'] == feedback_throttle.limit


# ------------------------------------------------------------------- the admin half
def test_the_inbox_is_closed_without_a_session():
    assert client.get('/api/feedback').status_code == 401
    assert client.post('/api/feedback/1', json={'status': 'done'}).status_code == 401
    assert client.delete('/api/feedback/1').status_code == 401


def test_the_inbox_reports_the_counts_and_the_kinds():
    body = client.get('/api/feedback', headers=h()).json()
    assert body['success'] is True
    assert {'total', 'new', 'done', 'items', 'kinds'} <= set(body)
    assert {item['id'] for item in body['kinds']} == set(feedback.KINDS)


def test_a_status_change_and_a_delete_go_through():
    _, token = _user()
    client.post('/api/feedback', json={'token': token, 'message': 'یک پیام کامل'})
    body = client.get('/api/feedback', headers=h()).json()
    assert body['new'] == 1 and body['items'][0]['status'] == 'new'
    fid = body['items'][0]['id']

    done = client.post(f'/api/feedback/{fid}', headers=h(), json={'status': 'done'})
    assert done.status_code == 200 and done.json()['new'] == 0

    removed = client.delete(f'/api/feedback/{fid}', headers=h())
    assert removed.status_code == 200 and removed.json()['total'] == 0


def test_an_unknown_status_or_id_is_refused():
    _, token = _user()
    client.post('/api/feedback', json={'token': token, 'message': 'یک پیام کامل'})
    fid = client.get('/api/feedback', headers=h()).json()['items'][0]['id']
    assert client.post(f'/api/feedback/{fid}', headers=h(),
                       json={'status': 'nope'}).status_code == 400
    assert client.delete('/api/feedback/999999', headers=h()).status_code == 404


def test_the_portal_window_carries_the_form_the_public_half_needs():
    """A blank preview here means the form never renders even though the API works."""
    _, token = _user()
    page = client.get(f'/portal/{token}')
    assert page.status_code == 200
    assert 'id="feedbackCard"' in page.text
    assert 'id="fbSend"' in page.text
    assert '/api/feedback' in page.text
