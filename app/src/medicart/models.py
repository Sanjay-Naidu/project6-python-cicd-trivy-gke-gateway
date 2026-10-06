# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/models.py - SQLAlchemy 2.0 ORM models
# =============================================================================
# Money is stored as integer cents: floats cannot represent 0.10 exactly, and
# rounding errors in a checkout are bugs customers notice.
# Business rules the database can enforce (stock never negative, quantity
# positive, a known order status) are CHECK constraints, so a bug in any
# replica - or a manual SQL fix - cannot break them.
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(StrEnum):
    PATIENT = "patient"
    PHARMACIST = "pharmacist"


class OrderStatus(StrEnum):
    CONFIRMED = "confirmed"  # OTC-only order, no review needed
    PENDING_REVIEW = "pending_review"  # contains Rx items, waiting for a pharmacist
    APPROVED = "approved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('patient', 'pharmacist')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.PATIENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def is_pharmacist(self) -> bool:
        return self.role == Role.PHARMACIST


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("stock >= 0", name="ck_products_stock_non_negative"),
        CheckConstraint("price_cents > 0", name="ck_products_price_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60), index=True)
    description: Mapped[str] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer)
    requires_prescription: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('confirmed', 'pending_review', 'approved', 'rejected')",
            name="ck_orders_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    total_cents: Mapped[int] = mapped_column(Integer)
    prescriber_name: Mapped[str | None] = mapped_column(String(120))
    rx_number: Mapped[str | None] = mapped_column(String(40))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )

    @property
    def needs_prescription(self) -> bool:
        return any(item.requires_prescription for item in self.items)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    # Snapshot of name/price at purchase time: later catalog edits must not
    # rewrite what the customer was charged.
    product_name: Mapped[str] = mapped_column(String(120))
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    requires_prescription: Mapped[bool] = mapped_column(Boolean)

    order: Mapped[Order] = relationship(back_populates="items")

    @property
    def line_total_cents(self) -> int:
        return self.unit_price_cents * self.quantity


class AuditEvent(Base):
    """Append-only trail of who did what. UPDATE/DELETE are blocked by a
    database trigger (see migration 0001), not just by application code."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_ip: Mapped[str | None] = mapped_column(String(64))

    actor: Mapped[User | None] = relationship()
