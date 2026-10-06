# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/deps.py - auth dependencies, cart, templates
# =============================================================================
# Sessions are signed cookies (Starlette SessionMiddleware + itsdangerous):
# no server-side session store, so any replica can serve any request and the
# HPA can add or remove pods freely. The signing key comes from Secret
# Manager and is identical on every pod of an environment.
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.responses import Response

from medicart.db import get_db
from medicart.models import User
from medicart.security import csrf_token

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
templates.env.globals["csrf_token"] = csrf_token
templates.env.filters["money"] = lambda cents: f"${cents / 100:,.2f}"


class LoginRequired(Exception):
    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None:  # account deleted since the cookie was issued
        request.session.clear()
    return user


def require_user(request: Request, user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise LoginRequired(request.url.path)
    return user


def require_pharmacist(user: User = Depends(require_user)) -> User:
    if not user.is_pharmacist:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Pharmacists only")
    return user


# --- Cart (kept in the signed session cookie) ------------------------------------
def get_cart(request: Request) -> dict[int, int]:
    raw = request.session.get("cart", {})
    return {int(pid): int(qty) for pid, qty in raw.items() if int(qty) > 0}


def save_cart(request: Request, cart: dict[int, int]) -> None:
    request.session["cart"] = {str(pid): qty for pid, qty in cart.items() if qty > 0}


# --- Flash messages + rendering -----------------------------------------------------
def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flashes", []).append({"kind": kind, "message": message})


def render(
    request: Request,
    template: str,
    user: User | None,
    status_code: int = 200,
    **context: Any,
) -> Response:
    context.update(
        user=user,
        cart_count=sum(get_cart(request).values()),
        flashes=request.session.pop("flashes", []),
        app_info=request.app.state.settings,
    )
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def safe_next(path: str | None) -> str:
    """Only allow redirects back into this site. '//evil.com' is a
    protocol-relative URL to another host, so it is rejected too."""
    if path and path.startswith("/") and not path.startswith("//"):
        return path
    return "/"
