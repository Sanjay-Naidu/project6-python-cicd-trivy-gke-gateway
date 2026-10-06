# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/web.py - storefront pages (catalog, cart, orders, auth)
# =============================================================================
# Server-rendered HTML with plain forms: no JavaScript bundle to build, and
# the strict Content-Security-Policy can forbid inline scripts entirely.
# Every POST follows Post/Redirect/Get, so a browser refresh never
# re-submits an order.
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from medicart import catalog, orders, users
from medicart.db import get_db
from medicart.deps import (
    current_user,
    flash,
    get_cart,
    render,
    require_user,
    safe_next,
    save_cart,
)
from medicart.models import User
from medicart.security import check_csrf, client_ip

router = APIRouter()


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


# --- Catalog -------------------------------------------------------------------------
@router.get("/")
def index(
    request: Request,
    q: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    return render(
        request,
        "index.html",
        user,
        products=catalog.list_products(db, q=q, category=category),
        categories=catalog.categories(db),
        q=q or "",
        category=category or "",
    )


@router.get("/products/{product_id}")
def product_detail(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    product = catalog.get_product(db, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return render(request, "product.html", user, product=product)


# --- Cart ------------------------------------------------------------------------------
@router.get("/cart")
def view_cart(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    cart = get_cart(request)
    products = catalog.products_by_id(db, list(cart))
    lines = [(products[pid], qty) for pid, qty in cart.items() if pid in products]
    if len(lines) != len(cart):  # drop products that were deactivated
        save_cart(request, {p.id: qty for p, qty in lines})
    return render(
        request,
        "cart.html",
        user,
        lines=lines,
        total_cents=sum(p.price_cents * qty for p, qty in lines),
        needs_rx=any(p.requires_prescription for p, _ in lines),
        max_qty=orders.MAX_QUANTITY_PER_ITEM,
    )


@router.post("/cart/add")
def add_to_cart(
    request: Request,
    product_id: int = Form(),
    quantity: int = Form(1),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    product = catalog.get_product(db, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    cart = get_cart(request)
    cart[product_id] = max(1, min(cart.get(product_id, 0) + quantity, orders.MAX_QUANTITY_PER_ITEM))
    save_cart(request, cart)
    flash(request, f"Added {product.name} to your cart.", "success")
    return _redirect("/cart")


@router.post("/cart/update")
def update_cart(
    request: Request,
    product_id: int = Form(),
    quantity: int = Form(),
    csrf_token: str = Form(""),
):
    check_csrf(request, csrf_token)
    cart = get_cart(request)
    if quantity <= 0:
        cart.pop(product_id, None)
    elif product_id in cart:
        cart[product_id] = min(quantity, orders.MAX_QUANTITY_PER_ITEM)
    save_cart(request, cart)
    return _redirect("/cart")


# --- Checkout + orders --------------------------------------------------------------
@router.get("/checkout")
def checkout_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    cart = get_cart(request)
    if not cart:
        flash(request, "Your cart is empty.", "error")
        return _redirect("/cart")
    products = catalog.products_by_id(db, list(cart))
    lines = [(products[pid], qty) for pid, qty in cart.items() if pid in products]
    return render(
        request,
        "checkout.html",
        user,
        lines=lines,
        total_cents=sum(p.price_cents * qty for p, qty in lines),
        needs_rx=any(p.requires_prescription for p, _ in lines),
    )


@router.post("/checkout")
def checkout(
    request: Request,
    prescriber_name: str = Form(""),
    rx_number: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    check_csrf(request, csrf_token)
    try:
        order = orders.place_order(
            db,
            user,
            get_cart(request),
            prescriber_name=prescriber_name,
            rx_number=rx_number,
            source_ip=client_ip(request),
        )
    except orders.OrderError as exc:
        flash(request, str(exc), "error")
        return _redirect("/checkout")
    save_cart(request, {})
    flash(request, f"Order #{order.id} placed.", "success")
    return _redirect(f"/orders/{order.id}")


@router.get("/orders")
def my_orders(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return render(request, "orders.html", user, orders=orders.orders_for_user(db, user))


@router.get("/orders/{order_id}")
def order_detail(
    request: Request,
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    order = orders.get_order(db, order_id)
    # 404, not 403, for someone else's order: don't confirm that it exists
    # (order ids are sequential and easy to guess).
    if order is None or (order.user_id != user.id and not user.is_pharmacist):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return render(request, "order_detail.html", user, order=order)


# --- Accounts ------------------------------------------------------------------------
@router.get("/register")
def register_form(request: Request, user: User | None = Depends(current_user)):
    return render(request, "register.html", user, form={})


@router.post("/register")
def register(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    try:
        new_user = users.create_user(
            db,
            email=email,
            full_name=full_name,
            password=password,
            source_ip=client_ip(request),
        )
    except users.UserError as exc:
        return render(
            request,
            "register.html",
            None,
            status_code=status.HTTP_400_BAD_REQUEST,
            error=str(exc),
            form={"full_name": full_name, "email": email},
        )
    _start_session(request, new_user)
    flash(request, f"Welcome to MediCart, {new_user.full_name}.", "success")
    return _redirect("/")


@router.get("/login")
def login_form(request: Request, next: str = "/", user: User | None = Depends(current_user)):
    return render(request, "login.html", user, next=safe_next(next), email="")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    user = users.authenticate(db, email, password, client_ip(request))
    if user is None:
        # Same message for "no such user" and "wrong password".
        return render(
            request,
            "login.html",
            None,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error="Email or password is incorrect.",
            next=safe_next(next),
            email=email,
        )
    _start_session(request, user)
    return _redirect(safe_next(next))


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    check_csrf(request, csrf_token)
    request.session.clear()
    return _redirect("/")


def _start_session(request: Request, user: User) -> None:
    # Start from a clean session on login (new CSRF token, no leftovers from
    # whoever used the browser before), but keep the cart so a shopper who
    # logs in at checkout doesn't lose it.
    cart = get_cart(request)
    request.session.clear()
    request.session["user_id"] = user.id
    save_cart(request, cart)
