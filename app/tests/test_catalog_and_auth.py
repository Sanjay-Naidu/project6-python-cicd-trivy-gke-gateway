# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/tests/test_catalog_and_auth.py - browsing, accounts, CSRF
# =============================================================================
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from medicart.models import AuditEvent
from medicart.security import hash_password, verify_password
from tests.conftest import PASSWORD, csrf, login, product_id, register


# --- catalog ------------------------------------------------------------------------
def test_home_lists_seeded_catalog(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "Paracetamol 500 mg" in r.text
    assert "Amoxicillin 500 mg" in r.text
    assert "sha-test123" in r.text  # footer shows the running version


def test_search_and_category_filter(client: TestClient):
    assert "Ibuprofen" in client.get("/?q=ibuprofen").text
    assert "Ibuprofen" not in client.get("/?q=vitamin").text
    allergy = client.get("/?category=Allergy").text
    assert "Cetirizine" in allergy and "Paracetamol" not in allergy


def test_search_treats_wildcards_literally(client: TestClient):
    # '%' must not match everything (autoescape), and quotes can't break SQL.
    assert "No products found" in client.get("/?q=%25").text
    assert client.get("/?q=' OR 1=1 --").status_code == 200


def test_product_page_and_404(client: TestClient, db):
    pid = product_id(db, "RX-AMOX-500")
    r = client.get(f"/products/{pid}")
    assert "Prescription required" in r.text
    assert client.get("/products/999999").status_code == 404


def test_inactive_products_are_hidden(client: TestClient, db):
    pid = product_id(db, "OTC-PAR-500")
    db.execute(text("UPDATE products SET active = false WHERE id = :i"), {"i": pid})
    db.commit()
    assert client.get(f"/products/{pid}").status_code == 404
    assert "Paracetamol" not in client.get("/").text


def test_products_api(client: TestClient, db):
    products = client.get("/api/products").json()
    assert len(products) == 18
    amox = next(p for p in products if p["sku"] == "RX-AMOX-500")
    assert amox["requires_prescription"] is True and amox["price"] == "12.99"
    assert client.get("/api/products?category=Vitamins").json()[0]["category"] == "Vitamins"
    pid = product_id(db, "VIT-D3-1000")
    assert client.get(f"/api/products/{pid}").json()["sku"] == "VIT-D3-1000"
    missing = client.get("/api/products/999999")
    assert missing.status_code == 404 and missing.json() == {"detail": "Product not found"}


# --- accounts -------------------------------------------------------------------------
def test_register_logs_in_and_audits(client: TestClient, db):
    r = register(client)
    assert r.status_code == 303
    assert "Log out (Pat)" in client.get("/").text
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "user.registered"))
    assert event is not None and event.detail == {"role": "patient"}


def test_register_validation(client: TestClient):
    token = csrf(client, "/register")
    cases = [
        ({"full_name": "A", "email": "not-an-email", "password": PASSWORD}, "valid email"),
        ({"full_name": "", "email": "a@b.co", "password": PASSWORD}, "full name"),
        ({"full_name": "A", "email": "a@b.co", "password": "short"}, "at least 10"),
    ]
    for data, message in cases:
        r = client.post("/register", data={**data, "csrf_token": token})
        assert r.status_code == 400 and message in r.text


def test_duplicate_email_rejected_case_insensitively(client: TestClient):
    register(client, email="dup@example.com")
    client.post("/logout", data={"csrf_token": csrf(client)})
    r = register(client, email="DUP@example.com")
    assert r.status_code == 400 and "already exists" in r.text


def test_login_logout_and_audit(client: TestClient, db):
    register(client, email="joe@example.com")
    client.post("/logout", data={"csrf_token": csrf(client)})
    assert "Log in" in client.get("/").text

    bad = login(client, "joe@example.com", "wrong-password")
    assert bad.status_code == 401 and "incorrect" in bad.text
    unknown = login(client, "nobody@example.com")
    assert unknown.status_code == 401 and "incorrect" in unknown.text  # same message

    ok = login(client, "Joe@Example.com")
    assert ok.status_code == 303 and ok.headers["location"] == "/"

    actions = list(db.scalars(select(AuditEvent.action).order_by(AuditEvent.id)))
    assert actions.count("auth.login_failed") == 2
    assert actions[-1] == "auth.login_succeeded"
    failed = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.login_failed"))
    assert "password" not in str(failed.detail)


def test_login_redirect_is_local_only(client: TestClient):
    register(client, email="nx@example.com")
    client.post("/logout", data={"csrf_token": csrf(client)})
    token = csrf(client, "/login")
    for evil in ("//evil.example", "https://evil.example"):
        r = client.post(
            "/login",
            data={
                "email": "nx@example.com",
                "password": PASSWORD,
                "next": evil,
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == "/"
        client.post("/logout", data={"csrf_token": csrf(client)})
        token = csrf(client, "/login")


def test_forms_require_csrf_token(client: TestClient):
    r = client.post("/register", data={"full_name": "x", "email": "x@y.zz", "password": PASSWORD})
    assert r.status_code == 403
    r = client.post(
        "/login", data={"email": "x@y.zz", "password": PASSWORD, "csrf_token": "forged"}
    )
    assert r.status_code == 403


def test_password_hashing():
    h = hash_password("s3cret-password")
    assert h.startswith("scrypt$") and "s3cret" not in h
    assert verify_password("s3cret-password", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "bcrypt$not$ours$at$all$x")
    assert not verify_password("x", "garbage")
    assert hash_password("same") != hash_password("same")  # random salt
