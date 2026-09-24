from app.db import rows

# Everything needed to restore accounts, their UUID-based links and the network
# catalog. High-volume traffic/audit tables stay in the live database so a manual
# backup cannot grow without bound.
TABLES = ['users', 'proxies', 'settings', 'nodes', 'cf_ips']


def export_all():
    return {table: rows('SELECT * FROM ' + table) for table in TABLES}
