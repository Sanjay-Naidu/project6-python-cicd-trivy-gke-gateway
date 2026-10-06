# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/api.py - JSON API (read-only catalog + build info)
# =============================================================================
# /api/info is what the pipeline calls through the PUBLIC load balancer after
# a deploy: it proves the internet-facing URL serves the exact image tag that
# was just rolled out, not merely that some pod is healthy.
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from medicart import catalog
from medicart.db import get_db
from medicart.models import Product

router = APIRouter(prefix="/api", tags=["api"])


def _product_json(p: Product) -> dict[str, Any]:
    return {
        "id": p.id,
        "sku": p.sku,
        "name": p.name,
        "category": p.category,
        "description": p.description,
        "price": f"{p.price_cents / 100:.2f}",
        "in_stock": p.stock > 0,
        "requires_prescription": p.requires_prescription,
    }


@router.get("/info")
def info(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "app": "medicart",
        "version": settings.version,
        "environment": settings.environment,
        "pod": settings.pod_name,
    }


@router.get("/products")
def products(
    q: str | None = None, category: str | None = None, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return [_product_json(p) for p in catalog.list_products(db, q=q, category=category)]


@router.get("/products/{product_id}")
def product(product_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    p = catalog.get_product(db, product_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return _product_json(p)
