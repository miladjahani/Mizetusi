from app.db import rows
TABLES=['users','proxies','settings']
def export_all(): return {t:rows('SELECT * FROM '+t) for t in TABLES}
