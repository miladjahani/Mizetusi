"""Runtime configuration.

Every value the panel can change while running lives in the ``settings`` table
(never in a module global), so this class is the single place that knows how to
read and write it. Endpoints and services depend on the object, not on the
database helpers, which keeps the SQL in one class.
"""
import json

from app.db import row, rows, execute


class SettingsStore:
    """Thin typed wrapper around the ``settings`` key/value table."""

    def __init__(self, defaults=None):
        # Process-level fallbacks used when a key was never saved.
        self._defaults = dict(defaults or {})

    # ------------------------------------------------------------------ reads
    def get(self, key, default=None):
        found = row('SELECT value FROM settings WHERE key=?', (key,))
        if found and found.get('value') is not None:
            return found['value']
        if key in self._defaults:
            return self._defaults[key]
        return default

    def get_int(self, key, default=0):
        return self._number(key, default, int)

    def get_float(self, key, default=0.0):
        return self._number(key, default, float)

    def _number(self, key, default, cast):
        raw = self.get(key)
        if raw is None or str(raw).strip() == '':
            return default
        try:
            return cast(float(raw))
        except (TypeError, ValueError):
            return default

    def get_json(self, key, default=None):
        raw = self.get(key)
        if not raw:
            return {} if default is None else default
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {} if default is None else default
        return data

    def many(self, keys):
        """One SELECT for a batch of keys (used by the settings payload)."""
        return {key: (self.get(key) or '') for key in keys}

    # ------------------------------------------------------------------ writes
    def set(self, key, value):
        execute(
            'INSERT INTO settings(key,value) VALUES(?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, '' if value is None else str(value)),
        )
        return value

    def set_json(self, key, value):
        return self.set(key, json.dumps(value, ensure_ascii=False))

    def delete(self, key):
        execute('DELETE FROM settings WHERE key=?', (key,))

    def exists(self, key):
        return bool(rows('SELECT key FROM settings WHERE key=? LIMIT 1', (key,)))


# The application shares one store instance.
store = SettingsStore()
