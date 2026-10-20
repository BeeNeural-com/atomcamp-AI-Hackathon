"""Run database migrations once (Cloud Run Job `beeproof-migrate`, compose service `migrate`).

* Serialized with a PostgreSQL advisory lock, so concurrent deployments cannot race.
* A database created earlier by `create_all` (no alembic_version table) is adopted by
  stamping the baseline revision, then upgraded normally.
* Never drops data. A failure exits non-zero, which stops the deployment pipeline.
* With DB_APP_USER set (production), grants that runtime role data access only
  (SELECT/INSERT/UPDATE/DELETE + sequences); it cannot change the schema.

Usage: python -m beeproof.migrate [--sql]   (--sql prints the SQL without applying it)
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from beeproof import logging_setup
from beeproof.config import get_settings

log = logging.getLogger("beeproof.migrate")
LOCK_KEY = 727171  # arbitrary constant shared by every migration runner
BASELINE = "0001_initial"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def grant_app_privileges(conn, app_user: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", app_user):
        raise ValueError(f"invalid DB_APP_USER {app_user!r}")
    for stmt in (
        f"GRANT USAGE ON SCHEMA public TO {app_user}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_user}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_user}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_user}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {app_user}",
    ):
        conn.execute(text(stmt))
    log.info("granted data-only privileges to %s", app_user)


def run(sql_only: bool = False) -> None:
    settings = get_settings()
    url = settings.database_url
    cfg = alembic_config(url)
    if sql_only:
        command.upgrade(cfg, "head", sql=True)
        return
    engine = create_engine(url, pool_pre_ping=True)
    postgres = engine.dialect.name == "postgresql"
    with engine.connect() as conn:
        if postgres:
            log.info("waiting for migration lock")
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        try:
            tables = set(inspect(conn).get_table_names())
            cfg.attributes["connection"] = conn
            if "alembic_version" not in tables and "documents" in tables:
                log.warning("existing schema without migration history: stamping %s", BASELINE)
                command.stamp(cfg, BASELINE)
            command.upgrade(cfg, "head")
            app_user = os.environ.get("DB_APP_USER", "")
            if postgres and app_user:
                grant_app_privileges(conn, app_user)
            conn.commit()
            log.info("database is at head")
        finally:
            if postgres:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                conn.commit()
    engine.dispose()


def main() -> None:
    logging_setup.configure(get_settings().log_format)
    try:
        run(sql_only="--sql" in sys.argv)
    except Exception:
        log.exception("migration failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
