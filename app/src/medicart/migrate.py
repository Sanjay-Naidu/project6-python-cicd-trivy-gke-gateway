# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/migrate.py - schema migrations (init container)
# =============================================================================
# Runs as an init container of every app pod: `python -m medicart.migrate`.
#
# WHY an init container (and not a separate Job):
#   A pod's app container cannot start until its init container succeeds, so
#   new code can never run against an old schema - no ordering to get right
#   in the pipeline.
# WHY the advisory lock:
#   During a rollout several pods start at once and would all run Alembic
#   concurrently. pg_advisory_lock makes them queue: the first applies the
#   migrations, the rest wait, then find nothing left to do.
# The catch (documented in the README): migrations must be backward
# compatible (expand/contract), because old pods keep serving during the
# rollout.
from __future__ import annotations

import logging
import logging.config
import os
import sys
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from medicart.config import Settings
from medicart.db import CONNECT_TIMEOUT_S
from medicart.observability import logging_config

log = logging.getLogger("medicart.migrate")

# Any fixed 64-bit number, unique to this app. It identifies the lock.
MIGRATION_LOCK_KEY = 72_616_001


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "medicart:migrations")
    return cfg


def wait_for_database(database_url: str, timeout_s: int) -> None:
    """On a fresh environment Postgres may still be running initdb. Waiting
    here (instead of crash-looping) keeps the pod events readable."""
    engine = create_engine(database_url, connect_args={"connect_timeout": CONNECT_TIMEOUT_S})
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except OperationalError as exc:
                if time.monotonic() > deadline:
                    raise
                log.info("database not reachable yet (%s); retrying", exc.__class__.__name__)
                time.sleep(3)
    finally:
        engine.dispose()


def run_migrations(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
            conn.commit()
            try:
                cfg = alembic_config()
                cfg.attributes["connection"] = conn
                # One transaction for the whole upgrade: PostgreSQL DDL is
                # transactional, so a failing migration leaves NO half-applied
                # schema behind.
                with conn.begin():
                    command.upgrade(cfg, "head")
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
                conn.commit()
    finally:
        engine.dispose()


def main() -> int:
    logging.config.dictConfig(logging_config(os.environ.get("LOG_LEVEL", "INFO").upper()))
    settings = Settings.from_env()
    wait_for_database(settings.database_url, int(os.environ.get("MIGRATE_WAIT_SECONDS", "300")))
    log.info("database reachable; applying migrations")
    run_migrations(settings.database_url)
    log.info("migrations complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
