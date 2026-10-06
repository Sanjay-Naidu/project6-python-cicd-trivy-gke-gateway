# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/tests/test_orders.py - cart, checkout, pharmacist review
# =============================================================================
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from medicart import orders
from medicart.models import AuditEvent, Order, OrderStatus, User
from tests.conftest import (
    TEST_DATABASE_URL,
    add_to_cart,
    csrf,
    login,
    make_pharmacist,
    product_id,
    register,
    stock_of,
)


def checkout(client: TestClient, **fields):
    return client.post(
        "/checkout",
        data={**fields, "csrf_token": csrf(client, "/cart")},
        follow_redirects=False,
    )


# --- cart -----------------------------------------------------------------------------
def test_cart_add_update_remove(client: TestClient, db):
    pid = product_id(db, "OTC-IBU-200")
    add_to_cart(client, pid, 2)
    add_to_cart(client, pid, 1)
    cart = client.get("/cart").text
    assert 'value="3"' in cart and "$16.47" in cart

    client.post(
        "/cart/update", data={"product_id": pid, "quantity": 50, "csrf_token": csrf(client)}
    )
    assert 'value="10"' in client.get("/cart").text  # clamped to the maximum

    client.post("/cart/update", data={"product_id": pid, "quantity": 0, "csrf_token": csrf(client)})
    assert "Your cart is empty" in client.get("/cart").text


def test_cart_rejects_unknown_product(client: TestClient):
    assert add_to_cart(client, 999999).status_code == 404


def test_cart_drops_products_that_were_deactivated(client: TestClient, db):
    pid = product_id(db, "OTC-CET-010")
    add_to_cart(client, pid)
    db.execute(text("UPDATE products SET active = false WHERE id = :i"), {"i": pid})
    db.commit()
    assert "Your cart is empty" in client.get("/cart").text


def test_checkout_requires_login_and_keeps_cart(client: TestClient, db):
    pid = product_id(db, "OTC-PAR-500")
    add_to_cart(client, pid)
    r = client.get("/checkout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/checkout"
    register(client)
    assert "Paracetamol" in client.get("/checkout").text  # cart survived login


def test_empty_cart_cannot_check_out(client: TestClient):
    register(client)
    r = client.get("/checkout", follow_redirects=False)
    assert r.headers["location"] == "/cart"
    r = checkout(client)
    assert r.headers["location"] == "/checkout"
    assert (
        "cart is empty" in client.get("/checkout").text
        or "cart is empty" in client.get("/cart").text
    )


# --- checkout ---------------------------------------------------------------------------
def test_otc_order_is_confirmed_and_reserves_stock(client: TestClient, db):
    pid = product_id(db, "OTC-PAR-500")
    before = stock_of(db, pid)
    register(client)
    add_to_cart(client, pid, 3)
    r = checkout(client)
    assert r.status_code == 303 and r.headers["location"] == "/orders/1"

    page = client.get("/orders/1").text
    assert "confirmed" in page and "$11.97" in page and "pytest-pod" in page
    assert stock_of(db, pid) == before - 3
    assert "Your cart is empty" in client.get("/cart").text
    assert "#1" in client.get("/orders").text
    assert (
        db.scalar(select(AuditEvent.action).where(AuditEvent.entity == "order")) == "order.placed"
    )


def test_rx_order_needs_prescription_details(client: TestClient, db):
    pid = product_id(db, "RX-AMOX-500")
    register(client)
    add_to_cart(client, pid)
    assert "Prescriber" in client.get("/checkout").text

    r = checkout(client)
    assert r.headers["location"] == "/checkout"
    assert "prescriber" in client.get("/checkout").text.lower()

    r = checkout(client, prescriber_name="Dr. Rao", rx_number="RX-12345")
    assert r.headers["location"] == "/orders/1"
    assert "pharmacist is checking" in client.get("/orders/1").text


def test_insufficient_stock_is_refused(client: TestClient, db):
    pid = product_id(db, "RX-SALB-100")
    db.execute(text("UPDATE products SET stock = 1 WHERE id = :i"), {"i": pid})
    db.commit()
    register(client)
    add_to_cart(client, pid, 2)
    r = checkout(client, prescriber_name="Dr. A", rx_number="1")
    assert r.headers["location"] == "/checkout"
    assert (
        "Only 1 left" in client.get("/cart").text or "Only 1 left" in client.get("/checkout").text
    )
    assert stock_of(db, pid) == 1


def test_service_validates_quantities_and_availability(db):
    user = make_pharmacist(db)
    with pytest.raises(orders.OrderError, match="between 1 and"):
        orders.place_order(db, user, {1: 11})
    with pytest.raises(orders.OrderError, match="no longer available"):
        orders.place_order(db, user, {999999: 1})


def test_orders_are_private(client: TestClient, db):
    register(client, email="a@example.com")
    add_to_cart(client, product_id(db, "OTC-PAR-500"))
    checkout(client)
    client.post("/logout", data={"csrf_token": csrf(client)})
    register(client, email="b@example.com")
    assert client.get("/orders/1").status_code == 404  # not 403: don't leak existence
    assert client.get("/orders/424242").status_code == 404


def test_concurrent_checkouts_never_oversell(engine, db):
    """Two customers race for the last box. Row locks (SELECT ... FOR
    UPDATE) must let exactly one of them win."""
    pid = product_id(db, "RX-LISI-010")
    db.execute(text("UPDATE products SET stock = 1 WHERE id = :i"), {"i": pid})
    db.commit()
    buyers = [make_pharmacist(db, f"buyer{i}@example.com") for i in range(2)]

    results: list[str] = []
    barrier = threading.Barrier(2)
    race_engine = create_engine(TEST_DATABASE_URL)

    def buy(user: User) -> None:
        with Session(race_engine) as s:
            barrier.wait()
            try:
                orders.place_order(s, user, {pid: 1}, prescriber_name="Dr", rx_number="1")
                results.append("ok")
            except orders.OrderError:
                results.append("refused")

    threads = [threading.Thread(target=buy, args=(u,)) for u in buyers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    race_engine.dispose()

    assert sorted(results) == ["ok", "refused"]
    assert stock_of(db, pid) == 0


# --- pharmacist review ------------------------------------------------------------------
def _pending_rx_order(client: TestClient, db) -> int:
    register(client, email="patient@example.com")
    add_to_cart(client, product_id(db, "RX-METF-500"), 2)
    add_to_cart(client, product_id(db, "OTC-PAR-500"), 1)
    checkout(client, prescriber_name="Dr. Mehta", rx_number="RX-777")
    client.post("/logout", data={"csrf_token": csrf(client)})
    return 1


def review(client: TestClient, order_id: int, decision: str, note: str = ""):
    return client.post(
        f"/pharmacy/orders/{order_id}/review",
        data={"decision": decision, "note": note, "csrf_token": csrf(client)},
        follow_redirects=False,
    )


def test_patients_cannot_open_the_pharmacy(client: TestClient):
    assert client.get("/pharmacy", follow_redirects=False).status_code == 303  # anonymous -> login
    register(client)
    assert client.get("/pharmacy").status_code == 403
    assert client.get("/pharmacy/audit").status_code == 403


def test_pharmacist_approves(client: TestClient, db):
    order_id = _pending_rx_order(client, db)
    make_pharmacist(db)
    login(client, "pharm@example.com")

    queue = client.get("/pharmacy").text
    assert "Dr. Mehta" in queue and "RX-777" in queue and "Order #1" in queue

    assert review(client, order_id, "approve").status_code == 303
    assert "Nothing waiting" in client.get("/pharmacy").text
    order = db.get(Order, order_id)
    db.refresh(order)
    assert order.status == OrderStatus.APPROVED and order.reviewed_by is not None

    # A pharmacist can open any order; a second decision is refused.
    assert client.get(f"/orders/{order_id}").status_code == 200
    review(client, order_id, "reject", "changed my mind")
    assert "already been approved" in client.get("/pharmacy").text

    audit = client.get("/pharmacy/audit").text
    assert "order.approved" in audit and "order.placed" in audit and "pharm@example.com" in audit


def test_pharmacist_rejects_with_reason_and_stock_returns(client: TestClient, db):
    metformin = product_id(db, "RX-METF-500")
    before = stock_of(db, metformin)
    order_id = _pending_rx_order(client, db)
    assert stock_of(db, metformin) == before - 2

    make_pharmacist(db)
    login(client, "pharm@example.com")
    review(client, order_id, "reject")  # no reason
    assert "reason" in client.get("/pharmacy").text
    review(client, order_id, "reject", "Prescription has expired")
    assert stock_of(db, metformin) == before

    client.post("/logout", data={"csrf_token": csrf(client)})
    login(client, "patient@example.com")
    page = client.get(f"/orders/{order_id}").text
    assert "rejected" in page and "Prescription has expired" in page


def test_review_service_guards(db, client):
    patient_order = _pending_rx_order(client, db)
    patient = db.scalar(select(User).where(User.email == "patient@example.com"))
    with pytest.raises(orders.OrderError, match="Only pharmacists"):
        orders.review_order(db, patient, patient_order, approve=True)
    pharmacist = make_pharmacist(db)
    with pytest.raises(orders.OrderError, match="not found"):
        orders.review_order(db, pharmacist, 999, approve=True)
