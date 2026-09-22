"""Administrative audit trail.

The panel shows a Persian label per action, so the mapping lives next to the
class that writes the rows instead of in the front-end.
"""
from app.db import row, rows, execute


class AuditLog:
    """Append-only log of admin actions (``audit_logs``)."""

    LABELS = {
        'auth.login': 'ورود مدیر',
        'auth.logout': 'خروج مدیر',
        'user.create': 'ساخت کاربر',
        'user.quick': 'ساخت سریع کاربر',
        'user.delete': 'حذف کاربر',
        'user.reset': 'صفر کردن مصرف',
        'user.toggle': 'تغییر وضعیت کاربر',
        'user.update': 'ویرایش کاربر',
        'node.update': 'ویرایش نود',
        'node.delete': 'حذف نود',
        'node.bootstrap': 'ساخت خودکار نود',
        'nodes.sync': 'همگام‌سازی نودها',
        'nodes.ping': 'پینگ نودها',
        'settings.update': 'تغییر تنظیمات',
        'settings.clients': 'تنظیمات کلاینت‌ها',
        'settings.password': 'تغییر رمز',
        'settings.session': 'ابطال یا تمدید نشست',
        'settings.client_ip': 'اعتماد به هدرهای پروکسی',
        'cores.update': 'تنظیم هسته‌های دوم (AnyTLS/TUIC)',
        'cores.reload': 'راه‌اندازی مجدد هسته‌های دوم',
        'cloudflare.worker': 'تنظیم Worker',
        'logs.clear': 'پاک‌سازی گزارش',
    }

    def __init__(self, actor='admin', max_length=500):
        self.actor = actor
        self.max_length = max_length

    def record(self, action, detail=''):
        """Best-effort write: logging must never break the request it describes."""
        try:
            execute(
                'INSERT INTO audit_logs(action,actor,detail,created_at) VALUES(?,?,?,?)',
                (action, self.actor, str(detail)[:self.max_length], int(__import__('time').time())),
            )
        except Exception:
            pass

    def recent(self, limit=100):
        size = min(max(int(limit or 1), 1), 500)
        return rows('SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?', (size,))

    def count(self):
        found = row('SELECT COUNT(*) AS n FROM audit_logs')
        return int((found or {}).get('n') or 0)

    def clear(self):
        execute('DELETE FROM audit_logs')

    @classmethod
    def label(cls, action):
        return cls.LABELS.get(action, action or 'رویداد')


audit = AuditLog()
