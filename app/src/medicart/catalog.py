# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/catalog.py - product queries
# =============================================================================
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from medicart.models import Product


def list_products(db: Session, q: str | None = None, category: str | None = None) -> list[Product]:
    stmt = select(Product).where(Product.active.is_(True))
    if q:
        # Bound parameter, never string formatting: the search box is the
        # classic SQL injection entry point. autoescape stops '%' and '_' in
        # the user's text from acting as wildcards.
        term = q.strip()[:100]
        stmt = stmt.where(
            or_(
                Product.name.icontains(term, autoescape=True),
                Product.description.icontains(term, autoescape=True),
            )
        )
    if category:
        stmt = stmt.where(Product.category == category)
    return list(db.scalars(stmt.order_by(Product.name)))


def categories(db: Session) -> list[str]:
    stmt = select(Product.category).where(Product.active.is_(True)).distinct()
    return sorted(db.scalars(stmt))


def get_product(db: Session, product_id: int) -> Product | None:
    product = db.get(Product, product_id)
    return product if product and product.active else None


def products_by_id(db: Session, ids: list[int]) -> dict[int, Product]:
    if not ids:
        return {}
    stmt = select(Product).where(Product.id.in_(ids), Product.active.is_(True))
    return {p.id: p for p in db.scalars(stmt)}
