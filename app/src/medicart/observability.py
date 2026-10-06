# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/observability.py - JSON logs + Prometheus metrics
# =============================================================================
# Logs: one JSON object per line on stdout. GKE's logging agent parses that
# automatically, and the "severity" field maps to Cloud Logging's severity,
# so ERROR lines can be filtered and alerted on without regex parsing.
#
# Metrics: served by a separate HTTP server on METRICS_PORT (9090). The
# Kubernetes Service and the Gateway only expose port 8080, so /metrics can
# never be reached from the internet - only from inside the cluster.
from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response
from prometheus_client import Counter, Histogram

HEALTH_PATHS = ("/livez", "/readyz")

# --- Metrics ---------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "medicart_http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "medicart_http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)
ORDERS_PLACED = Counter("medicart_orders_placed_total", "Orders placed", ["status"])
ORDER_REVIEWS = Counter("medicart_order_reviews_total", "Pharmacist decisions", ["decision"])


async def metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Label by the route TEMPLATE (/orders/{order_id}), not the raw path:
        # one time series per endpoint instead of one per order id.
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        if route_path not in HEALTH_PATHS:
            HTTP_REQUESTS.labels(request.method, route_path, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route_path).observe(time.perf_counter() - start)


# --- Logging ---------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class DropHealthChecks(logging.Filter):
    """Probes hit /livez and /readyz every few seconds on every pod; logging
    them would bury real traffic and eat the free Cloud Logging allotment."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3 and args[2] in HEALTH_PATHS)


def logging_config(level: str) -> dict[str, Any]:
    """dictConfig used for both the app and uvicorn's own loggers."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "filters": {"drop_health": {"()": DropHealthChecks}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "filters": ["drop_health"],
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.error": {"level": level},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["stdout"], "level": level},
    }
