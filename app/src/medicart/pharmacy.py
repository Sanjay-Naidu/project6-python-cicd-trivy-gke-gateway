# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/pharmacy.py - pharmacist review queue + audit view
# =============================================================================
# Role check is a dependency on every route (require_pharmacist), so a new
# route added here can't forget it - the router itself enforces it.
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from medicart import audit, orders
from medicart.db import get_db
from medicart.deps import flash, render, require_pharmacist
from medicart.models import User
from medicart.security import check_csrf, client_ip

router = APIRouter(prefix="/pharmacy", dependencies=[Depends(require_pharmacist)])


@router.get("")
def review_queue(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_pharmacist),
):
    return render(request, "pharmacy_queue.html", user, orders=orders.pending_review(db))


@router.post("/orders/{order_id}/review")
def review(
    request: Request,
    order_id: int,
    decision: str = Form(),
    note: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_pharmacist),
):
    check_csrf(request, csrf_token)
    try:
        order = orders.review_order(
            db,
            user,
            order_id,
            approve=decision == "approve",
            note=note,
            source_ip=client_ip(request),
        )
        flash(request, f"Order #{order.id} {order.status}.", "success")
    except orders.OrderError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/pharmacy", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/audit")
def audit_log(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_pharmacist),
):
    return render(request, "pharmacy_audit.html", user, events=audit.recent(db))
