# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/tests/conftest.py - fixtures against a real PostgreSQL
# =============================================================================
# CI starts PostgreSQL 18 as a GitHub Actions service container and sets
# TEST_DATABASE_URL. The schema is built by the REAL migration runner
# (advisory lock + Alembic), so every test run also tests the migrations.
from __future__ import annotations

import os
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from medicart.config import Settings
from medicart.main import create_app
from medicart.migrate import run_migrations
from medicart.models import Role
from medicart.users import create_user

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
PASSWORD = "correct-horse-battery"

if not TEST_DATABASE_URL:
    pytest.exit(
        "TEST_DATABASE_URL is not set, e.g. "
        "postgresql+psycopg://postgres:postgres@localhost:5432/medicart_test",
        returncode=2,
    )


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = create_engine(TEST_DATABASE_URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    run_migrations(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def initial_stock(engine: Engine) -> dict[int, int]:
    with engine.connect() as conn:
        return {pid: stock for pid, stock in conn.execute(text("SELECT id, stock FROM products"))}


@pytest.fixture(autouse=True)
def clean_db(engine: Engine, initial_stock: dict[int, int]) -> Iterator[None]:
    yield
    # TRUNCATE is not a row-level UPDATE/DELETE, so the audit trigger allows
    # it (in production the app role would not hold TRUNCATE - see README).
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE order_items, orders, audit_events, users RESTART IDENTITY CASCADE")
        )
        conn.execute(
            text("UPDATE products SET stock = :stock, active = true WHERE id = :id"),
            [{"id": pid, "stock": s} for pid, s in initial_stock.items()],
        )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        version="sha-test123",
        pod_name="pytest-pod",
        database_url=TEST_DATABASE_URL,
        session_secret="test-session-secret",
        cookie_secure=False,
        log_level="INFO",
        metrics_port=0,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session


# --- helpers ---------------------------------------------------------------------------
_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf(client: TestClient, path: str = "/") -> str:
    match = _CSRF_RE.search(client.get(path).text)
    assert match, f"no csrf token on {path}"
    return match.group(1)


def product_id(db: Session, sku: str) -> int:
    return db.execute(text("SELECT id FROM products WHERE sku = :s"), {"s": sku}).scalar_one()


def stock_of(db: Session, pid: int) -> int:
    db.expire_all()
    return db.execute(text("SELECT stock FROM products WHERE id = :i"), {"i": pid}).scalar_one()


def register(client: TestClient, email: str = "pat@example.com", name: str = "Pat Patient"):
    return client.post(
        "/register",
        data={"full_name": name, "email": email, "password": PASSWORD, "csrf_token": csrf(client)},
        follow_redirects=False,
    )


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf(client), "next": "/"},
        follow_redirects=False,
    )


def make_pharmacist(db: Session, email: str = "pharm@example.com"):
    return create_user(
        db, email=email, full_name="Phil Pharmacist", password=PASSWORD, role=Role.PHARMACIST
    )


def add_to_cart(client: TestClient, pid: int, quantity: int = 1):
    return client.post(
        "/cart/add",
        data={"product_id": pid, "quantity": quantity, "csrf_token": csrf(client)},
        follow_redirects=False,
    )
