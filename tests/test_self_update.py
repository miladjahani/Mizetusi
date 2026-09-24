"""The in-panel GitHub update must never trade data for a newer image."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import self_update
from app.config import settings as cfg
from app.db import execute, row
from app.main import _setting, app

client = TestClient(app)


def h():
    return {'X-Admin-Password': _setting('admin_password') or cfg.admin_password}


@pytest.fixture(autouse=True)
def clean_guard():
    execute('DELETE FROM settings WHERE key=?', (self_update.MARKER,))
    yield
    execute('DELETE FROM settings WHERE key=?', (self_update.MARKER,))


def _ready(monkeypatch, target='f' * 40, current='a' * 40):
    monkeypatch.setattr(self_update, 'storage', lambda: {
        'safe': True, 'kind': 'SQLite', 'path': '/data/nexus.db', 'detail': 'persistent'})
    monkeypatch.setattr(self_update, 'provider', lambda: {
        'id': 'railway', 'label': 'Railway', 'configured': True, 'missing': []})
    monkeypatch.setattr(self_update, 'current_commit', lambda: current)
    monkeypatch.setattr(self_update, 'github_latest', lambda: ({
        'sha': target, 'message': 'latest', 'url': 'https://github.com/example/commit'}, ''))
    return target


def test_status_compares_the_github_head_without_exposing_a_token(monkeypatch):
    target = _ready(monkeypatch)
    body = self_update.status(check_remote=True)
    assert body['safe'] is True
    assert body['update_available'] is True
    assert body['current_commit'] == 'a' * 40
    assert body['latest']['sha'] == target
    assert body['repository'] == self_update.repository()
    assert 'token' not in json.dumps(body).lower()


def test_ephemeral_sqlite_is_refused_before_a_redeploy(monkeypatch):
    monkeypatch.setattr(self_update.db, 'is_pg', lambda: False)
    monkeypatch.setattr(self_update.db, 'SQLITE_PATH', '/tmp/nexus-ephemeral.db')
    monkeypatch.setattr(self_update.os.path, 'ismount', lambda path: False)
    monkeypatch.delenv('NEXUS_PERSISTENT_STORAGE', raising=False)
    state = self_update.storage()
    assert state['safe'] is False and 'volume' in state['detail']


def test_postgres_is_external_and_safe(monkeypatch):
    monkeypatch.setattr(self_update.db, 'is_pg', lambda: True)
    state = self_update.storage()
    assert state['safe'] is True and state['kind'] == 'PostgreSQL'


def test_apply_backs_up_then_triggers_and_the_new_process_verifies_uuid_identity(monkeypatch, tmp_path):
    target = _ready(monkeypatch)
    monkeypatch.setattr(self_update.os.path, 'ismount', lambda path: True)
    monkeypatch.setenv('NEXUS_UPDATE_BACKUP_DIR', str(tmp_path / 'backups'))
    triggered = []
    monkeypatch.setattr(self_update, '_trigger', lambda: (triggered.append(target), (True, ''))[1])

    result = self_update.apply()
    assert result['ok'] is True and result['target_commit'] == target
    assert triggered == [target]
    assert os.path.exists(result['backup_path'])
    with open(result['backup_path'], encoding='utf-8') as handle:
        backup = json.load(handle)
    assert {'users', 'settings', 'nodes'} <= set(backup['tables'])

    # The same process is still serving, so preservation is measured but a restart
    # is not yet claimed. Simulate the replacement process and verify the guard.
    guard = self_update._guard()
    assert guard['preserved'] is True and guard['restarted'] is False
    monkeypatch.setattr(self_update, 'STARTED_AT', time.time() + 10)
    guard = self_update._guard()
    assert guard['restarted'] is True and guard['verified'] is True


def test_a_failed_deploy_request_does_not_leave_a_false_success_guard(monkeypatch, tmp_path):
    _ready(monkeypatch)
    monkeypatch.setattr(self_update, '_backup', lambda: (str(tmp_path / 'backup.json'), 'test'))
    monkeypatch.setattr(self_update, '_trigger', lambda: (False, 'deploy hook rejected'))
    result = self_update.apply()
    assert result['ok'] is False and 'rejected' in result['reason']
    assert row('SELECT value FROM settings WHERE key=?', (self_update.MARKER,)) is None


def test_the_update_api_requires_an_admin_session(monkeypatch):
    monkeypatch.setattr(self_update, 'status', lambda check_remote=True: {'safe': True})
    assert client.get('/api/system/update').status_code == 401
    assert client.post('/api/system/update', json={}).status_code == 401


def test_the_update_api_returns_the_preflight_and_accepted_deploy(monkeypatch):
    monkeypatch.setattr(self_update, 'status', lambda check_remote=True: {
        'safe': True, 'repository': 'owner/repo', 'branch': 'main'})
    assert client.get('/api/system/update', headers=h()).json()['success'] is True
    monkeypatch.setattr(self_update, 'apply', lambda: {
        'ok': True, 'target_commit': 'f' * 40, 'provider': {'id': 'railway'}})
    body = client.post('/api/system/update', headers=h(), json={}).json()
    assert body['success'] is True and body['target_commit'] == 'f' * 40
