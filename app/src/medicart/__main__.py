# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/__main__.py - `python -m medicart` starts the server
# =============================================================================
# ONE uvicorn worker per pod, on purpose. Kubernetes is the process manager:
# more capacity = more pods (HPA), each with its own CPU/memory accounting,
# probes and rolling updates. Several workers inside one pod would hide
# crashes from Kubernetes and make memory limits and metrics per-worker.
from __future__ import annotations

import os

import uvicorn

from medicart.observability import logging_config


def main() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    uvicorn.run(
        "medicart.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - inside a container, the pod IP is the interface
        port=int(os.environ.get("PORT", "8080")),
        workers=1,
        log_config=logging_config(level),
        # X-Forwarded-For is parsed deliberately in security.client_ip();
        # uvicorn must not rewrite request.client from an untrusted header.
        proxy_headers=False,
        server_header=False,
        # Finish in-flight requests on SIGTERM. Budget: preStop sleep 10s +
        # this 20s < terminationGracePeriodSeconds 45s.
        timeout_graceful_shutdown=20,
        # Longer than the Google LB's 600s backend keep-alive, so the LB never
        # reuses a connection the app has just closed (a source of random 502s).
        timeout_keep_alive=620,
    )


if __name__ == "__main__":
    main()
