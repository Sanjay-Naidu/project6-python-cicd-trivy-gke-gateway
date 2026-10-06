"""Initial schema: users, products, orders, order items, audit trail.

Revision ID: 0001
Revises:
Create Date: 2026-09-23
Author: Sanjay Naidu
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="patient"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('patient', 'pharmacist')", name="ck_users_role"),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sku", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(60), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("price_cents", sa.Integer, nullable=False),
        sa.Column("stock", sa.Integer, nullable=False),
        sa.Column("requires_prescription", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint("stock >= 0", name="ck_products_stock_non_negative"),
        sa.CheckConstraint("price_cents > 0", name="ck_products_price_positive"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("total_cents", sa.Integer, nullable=False),
        sa.Column("prescriber_name", sa.String(120)),
        sa.Column("rx_number", sa.String(40)),
        sa.Column("review_note", sa.Text),
        sa.Column("reviewed_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('confirmed', 'pending_review', 'approved', 'rejected')",
            name="ck_orders_status",
        ),
    )

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer,
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name", sa.String(120), nullable=False),
        sa.Column("unit_price_cents", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("requires_prescription", sa.Boolean, nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(60), nullable=False, index=True),
        sa.Column("entity", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.String(40)),
        sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_ip", sa.String(64)),
    )

    # Append-only audit trail enforced by the DATABASE: an UPDATE or DELETE
    # fails no matter which code path (or which human with psql) tries it.
    op.execute(
        """
        CREATE FUNCTION audit_events_block_changes() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_block_changes();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_changes()")
    for table in ("audit_events", "order_items", "orders", "products", "users"):
        op.drop_table(table)
