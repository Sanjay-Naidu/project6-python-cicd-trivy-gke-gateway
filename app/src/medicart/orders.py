# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/orders.py - checkout and pharmacist review
# =============================================================================
# Concurrency is the interesting part. Several app replicas share one
# database, so "check stock, then decrement" in Python would oversell the
# last box when two pods check at the same moment. Instead the product rows
# are locked with SELECT ... FOR UPDATE for the length of the transaction:
# the second checkout waits, then sees the reduced stock.
# Rows are always locked in ascending id order, so two carts with the same
# products can never deadlock (each would otherwise hold one lock and wait
# for the other's).
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from medicart import audit
from medicart.models import Order, OrderItem, OrderStatus, Product, User
from medicart.observability import ORDER_REVIEWS, ORDERS_PLACED

MAX_QUANTITY_PER_ITEM = 10


class OrderError(ValueError):
    """A checkout/review problem the user can act on."""


def place_order(
    db: Session,
    user: User,
    cart: dict[int, int],
    *,
    prescriber_name: str = "",
    rx_number: str = "",
    source_ip: str | None = None,
) -> Order:
    if not cart:
        raise OrderError("Your cart is empty.")
    for qty in cart.values():
        if not 1 <= qty <= MAX_QUANTITY_PER_ITEM:
            raise OrderError(f"Quantities must be between 1 and {MAX_QUANTITY_PER_ITEM}.")

    ids = sorted(cart)
    products = {
        p.id: p
        for p in db.scalars(
            select(Product)
            .where(Product.id.in_(ids), Product.active.is_(True))
            .order_by(Product.id)
            .with_for_update()
        )
    }

    try:
        missing = [pid for pid in ids if pid not in products]
        if missing:
            raise OrderError("Some items in your cart are no longer available.")
        for pid in ids:
            if products[pid].stock < cart[pid]:
                raise OrderError(
                    f"Only {products[pid].stock} left of {products[pid].name}. "
                    "Please update your cart."
                )

        needs_rx = any(products[pid].requires_prescription for pid in ids)
        prescriber_name, rx_number = prescriber_name.strip(), rx_number.strip()
        if needs_rx and not (prescriber_name and rx_number):
            raise OrderError("Prescription items need the prescriber's name and the Rx number.")
    except OrderError:
        db.rollback()  # release the row locks straight away
        raise

    # Prices come from the database, never from the browser: the client only
    # says WHICH products and HOW MANY.
    order = Order(
        user_id=user.id,
        status=OrderStatus.PENDING_REVIEW if needs_rx else OrderStatus.CONFIRMED,
        total_cents=sum(products[pid].price_cents * cart[pid] for pid in ids),
        prescriber_name=prescriber_name[:120] if needs_rx else None,
        rx_number=rx_number[:40] if needs_rx else None,
    )
    for pid in ids:
        product = products[pid]
        # Stock is reserved at checkout, including for Rx orders waiting for
        # review, so an approved order can always be fulfilled.
        product.stock -= cart[pid]
        order.items.append(
            OrderItem(
                product_id=pid,
                product_name=product.name,
                unit_price_cents=product.price_cents,
                quantity=cart[pid],
                requires_prescription=product.requires_prescription,
            )
        )
    db.add(order)
    db.flush()
    audit.record(
        db,
        "order.placed",
        actor=user,
        entity="order",
        entity_id=order.id,
        detail={"status": order.status, "total_cents": order.total_cents, "items": len(ids)},
        source_ip=source_ip,
    )
    db.commit()
    ORDERS_PLACED.labels(status=order.status).inc()
    return order


def review_order(
    db: Session,
    pharmacist: User,
    order_id: int,
    *,
    approve: bool,
    note: str = "",
    source_ip: str | None = None,
) -> Order:
    if not pharmacist.is_pharmacist:
        raise OrderError("Only pharmacists can review prescription orders.")
    note = note.strip()[:1000]
    if not approve and not note:
        raise OrderError("Give the patient a reason when rejecting an order.")

    # Lock the order row: two pharmacists clicking at once must not both
    # "win" (and a reject must not restock twice).
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items))
        .with_for_update(of=Order)
    )
    if order is None:
        db.rollback()
        raise OrderError("Order not found.")
    if order.status != OrderStatus.PENDING_REVIEW:
        db.rollback()
        raise OrderError(f"Order #{order.id} has already been {order.status.replace('_', ' ')}.")

    order.status = OrderStatus.APPROVED if approve else OrderStatus.REJECTED
    order.review_note = note or None
    order.reviewed_by = pharmacist.id
    order.reviewed_at = datetime.now(UTC)

    if not approve:
        # Give the reserved stock back, locking products in id order as in
        # place_order.
        ids = sorted(item.product_id for item in order.items)
        products = {
            p.id: p
            for p in db.scalars(
                select(Product).where(Product.id.in_(ids)).order_by(Product.id).with_for_update()
            )
        }
        for item in order.items:
            products[item.product_id].stock += item.quantity

    audit.record(
        db,
        "order.approved" if approve else "order.rejected",
        actor=pharmacist,
        entity="order",
        entity_id=order.id,
        detail={"note": note} if note else {},
        source_ip=source_ip,
    )
    db.commit()
    ORDER_REVIEWS.labels(decision="approved" if approve else "rejected").inc()
    return order


def get_order(db: Session, order_id: int) -> Order | None:
    stmt = select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    return db.scalar(stmt)


def orders_for_user(db: Session, user: User) -> list[Order]:
    stmt = (
        select(Order)
        .where(Order.user_id == user.id)
        .options(selectinload(Order.items))
        .order_by(Order.id.desc())
    )
    return list(db.scalars(stmt))


def pending_review(db: Session) -> list[Order]:
    stmt = (
        select(Order)
        .where(Order.status == OrderStatus.PENDING_REVIEW)
        .options(selectinload(Order.items), selectinload(Order.user))
        .order_by(Order.id)  # oldest first: first come, first served
    )
    return list(db.scalars(stmt))
