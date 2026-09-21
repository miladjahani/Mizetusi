"""One file-backed test database for the whole suite, whatever order modules load.

Each test module pins its own SQLite path with ``os.environ.setdefault`` so a
developer can point the suite elsewhere, but a module that *assigns* the variable
(``tests/test_auto.py`` keeps its bare-import checks on an in-memory database)
re-points the shared ``app.db`` constants mid-collection. An in-memory SQLite
database drops its tables between connections, so every module collected after it
failed with «no such table: main.nodes» — the suite passed or failed depending on
the order pytest happened to read the directory in.

Importing the database here, before pytest loads any test module, pins a single
file-backed path once: module order stops mattering, and the suite can never talk
to whatever database the surrounding environment (a real ``.env``, a production
volume) happens to point at. ``NEXUS_TEST_SQLITE`` overrides the location.
"""
import os

DB_PATH = os.environ.get('NEXUS_TEST_SQLITE') or '/tmp/nexus-pytest.db'
os.environ['ENVIRONMENT'] = 'test'
os.environ['SQLITE_PATH'] = DB_PATH
os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH

from app.db import init_db  # noqa: E402 — must run after the lines above

init_db()
