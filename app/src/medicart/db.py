# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/db.py - engine, connection pool, request sessions
# =============================================================================
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

CONNECT_TIMEOUT_S = 3


def make_engine(database_url: str) -> Engine:
    # Pool sizing is capacity maths, not a default: each pod holds at most
    # pool_size + max_overflow = 10 connections, and prod runs up to 4 pods,
    # so 40 of PostgreSQL's max_connections=60 (set in the StatefulSet) -
    # leaving room for migrations and a psql session during an incident.
    return create_engine(
        database_url,
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        # pre_ping: a connection killed by a DB restart is detected and
        # replaced instead of failing the next request.
        pool_pre_ping=True,
        pool_recycle=1800,
        # libpq waits indefinitely by default. If the DB host stops
        # answering, fail in 3s with our 503 page instead of hanging every
        # request (and every uvicorn thread) until the LB gives up.
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
    )


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: templates can still read an order's fields
    # after the service layer has committed it.
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db: Session = request.app.state.sessionmaker()
    try:
        yield db
    finally:
        db.close()
