# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/main.py - application factory, health, middleware
# =============================================================================
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import start_http_server
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from medicart import api, pharmacy, web
from medicart.config import Settings
from medicart.db import make_engine, make_sessionmaker
from medicart.deps import PACKAGE_DIR, LoginRequired, render
from medicart.observability import metrics_middleware

log = logging.getLogger("medicart")

# Strict CSP: only our own origin, no inline scripts or styles, no framing.
# If an attacker ever gets HTML into a page, the browser still refuses to
# run it.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        metrics_server = None
        if settings.metrics_port:
            metrics_server, _ = start_http_server(settings.metrics_port)
        app.state.ready = True
        log.info(
            "medicart started",
            extra={"version": settings.version, "environment": settings.environment},
        )
        yield
        # SIGTERM: uvicorn stops accepting, finishes in-flight requests
        # (timeout_graceful_shutdown), then we get here.
        app.state.ready = False
        if metrics_server:
            metrics_server.shutdown()
        engine.dispose()

    app = FastAPI(
        title="MediCart",
        version=settings.version,
        lifespan=lifespan,
        # No public Swagger UI in prod: it documents the attack surface for
        # free. Local/dev keep it for convenience.
        docs_url="/docs" if settings.environment != "prod" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment != "prod" else None,
    )
    app.state.settings = settings
    app.state.ready = False

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="medicart_session",
        max_age=8 * 60 * 60,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.middleware("http")(metrics_middleware)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    # --- Health endpoints (on the serving port, so probes test the real path)
    @app.get("/livez", include_in_schema=False)
    def livez() -> dict[str, str]:
        # Liveness = "the process can answer". It deliberately does NOT touch
        # the database: if Postgres is down, restarting every app pod fixes
        # nothing and just adds a crash loop on top of the outage.
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(request: Request) -> Response:
        # Readiness = "started and not shutting down". Also no DB check, on
        # purpose: a DB outage would mark EVERY pod unready at once, and the
        # load balancer would answer users with its own bare 502. With pods
        # still in rotation, users get our 503 page explaining the problem.
        if request.app.state.ready:
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "starting"}, status_code=503)

    app.include_router(web.router)
    app.include_router(pharmacy.router)
    app.include_router(api.router)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    # --- Error handling ----------------------------------------------------------
    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired) -> Response:
        return RedirectResponse(f"/login?next={exc.next_path}", status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return render(
            request,
            "error.html",
            None,
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.detail,
        )

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeoutError)
    async def database_unavailable(request: Request, exc: Exception) -> Response:
        log.error("database unavailable: %s", exc.__class__.__name__)
        message = "We can't reach our database right now. Please try again in a minute."
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": message}, status_code=503)
        return render(
            request,
            "error.html",
            None,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=503,
            message=message,
        )

    return app
