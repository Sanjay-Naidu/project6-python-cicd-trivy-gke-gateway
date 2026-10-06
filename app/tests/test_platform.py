# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/tests/test_platform.py - probes, headers, config, operability
# =============================================================================
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from medicart.config import ConfigError, Settings
from medicart.main import create_app
from medicart.migrate import run_migrations
from medicart.security import client_ip
from tests.conftest import TEST_DATABASE_URL


def test_probes(client: TestClient):
    assert client.get("/livez").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_readyz_is_503_before_startup(settings: Settings):
    # Without the lifespan (no `with`), the app has not started yet.
    c = TestClient(create_app(settings))
    assert c.get("/readyz").status_code == 503


def test_info_reports_the_running_build(client: TestClient):
    assert client.get("/api/info").json() == {
        "app": "medicart",
        "version": "sha-test123",
        "environment": "test",
        "pod": "pytest-pod",
    }


def test_security_headers_on_every_response(client: TestClient):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" not in r.headers  # only when served over HTTPS


def test_hsts_when_cookies_are_secure(settings: Settings):
    secure = Settings(**{**settings.__dict__, "cookie_secure": True})
    with TestClient(create_app(secure)) as c:
        assert c.get("/livez").headers["strict-transport-security"].startswith("max-age=")


def test_metrics_are_not_served_on_the_public_port(client: TestClient):
    assert client.get("/metrics").status_code == 404


def test_prod_hides_api_docs(settings: Settings):
    prod = Settings(**{**settings.__dict__, "environment": "prod"})
    with TestClient(create_app(prod)) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404


def test_unknown_page_renders_html_404(client: TestClient):
    r = client.get("/no-such-page")
    assert r.status_code == 404
    assert "Back to the shop" in r.text


def test_database_outage_returns_friendly_503(settings: Settings):
    dead = Settings(
        **{**settings.__dict__, "database_url": "postgresql+psycopg://x:y@127.0.0.1:1/none"}
    )
    with TestClient(create_app(dead)) as c:
        page = c.get("/")
        assert page.status_code == 503
        assert "reach our database" in page.text
        assert c.get("/api/products").status_code == 503
        # ...while the probes stay green, so the pods stay in rotation.
        assert c.get("/readyz").status_code == 200


def test_migrations_are_idempotent(engine):
    run_migrations(TEST_DATABASE_URL)  # already at head: must be a no-op
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"


def test_audit_table_is_append_only(engine, client):
    from tests.conftest import register

    register(client)
    with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
        conn.execute(text("UPDATE audit_events SET action = 'tampered'"))
    with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_events"))


def test_stock_can_never_go_negative(engine):
    with pytest.raises(Exception, match="ck_products_stock_non_negative"), engine.begin() as conn:
        conn.execute(text("UPDATE products SET stock = -1 WHERE id = 1"))


# --- configuration ------------------------------------------------------------------
def test_settings_read_secrets_from_files(tmp_path, monkeypatch):
    pw = tmp_path / "db-password"
    pw.write_text("p@ss:word/with#chars\n")
    key = tmp_path / "session-key"
    key.write_text("k" * 32)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENVIRONMENT", "prod")
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(pw))
    monkeypatch.setenv("SESSION_SECRET_FILE", str(key))
    monkeypatch.setenv("DATABASE_HOST", "postgres")
    s = Settings.from_env()
    assert s.session_secret == "k" * 32
    # special characters are URL-escaped, not mangled
    assert "p%40ss%3Aword%2Fwith%23chars@postgres:5432/medicart" in s.database_url


def test_settings_fail_fast_without_secrets(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("DATABASE_PASSWORD", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    monkeypatch.setenv("APP_ENVIRONMENT", "prod")
    monkeypatch.delenv("SESSION_SECRET_FILE", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_client_ip_uses_the_load_balancer_entry():
    class Req:
        def __init__(self, xff):
            self.headers = {"x-forwarded-for": xff} if xff else {}
            self.client = type("C", (), {"host": "10.0.0.9"})()

    # A client-supplied (forged) first entry must be ignored.
    assert client_ip(Req("6.6.6.6, 203.0.113.7, 34.1.2.3")) == "203.0.113.7"
    assert client_ip(Req("203.0.113.7, 34.1.2.3")) == "203.0.113.7"
    assert client_ip(Req("")) == "10.0.0.9"


def test_wait_for_database_gives_up(monkeypatch):
    from medicart import migrate

    monkeypatch.setattr(migrate.time, "sleep", lambda _s: None)
    with pytest.raises(OperationalError):
        migrate.wait_for_database("postgresql+psycopg://x:y@127.0.0.1:1/none", timeout_s=0)


def test_wait_for_database_succeeds():
    from medicart.migrate import wait_for_database

    wait_for_database(TEST_DATABASE_URL, timeout_s=5)
