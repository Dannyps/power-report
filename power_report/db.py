"""PostgreSQL connection (schema: sql/schema.sql)."""

import psycopg2


def connect(cfg):
    d = cfg.db
    with open(d.get("password_file", "/etc/power-report/pgpass")) as f:
        pw = f.read().strip()
    return psycopg2.connect(host=d.get("host", "127.0.0.1"), dbname=d.get("name", "power"),
                            user=d.get("user", "power"), password=pw)
