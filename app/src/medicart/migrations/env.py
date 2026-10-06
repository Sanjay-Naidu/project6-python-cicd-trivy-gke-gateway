# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/migrations/env.py - Alembic runtime
# =============================================================================
# Normally called from medicart.migrate, which passes in a connection that
# already holds the migration advisory lock. `alembic` CLI use (local only)
# falls back to building its own engine from the environment.
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from medicart.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    from medicart.config import Settings

    engine = create_engine(Settings.from_env().database_url)
    with engine.begin() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) migrations are not used in this project.")
run_migrations_online()
