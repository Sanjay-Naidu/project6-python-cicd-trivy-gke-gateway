# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : Dockerfile - multi-stage, distroless, non-root production image
# =============================================================================
# WHY multi-stage   : uv, apt, compilers and caches stay in the build stage.
#                     Only the virtualenv ships.
# WHY distroless    : no shell, no package manager, no curl. Far fewer OS
#                     packages for Trivy to flag, and nothing for an attacker
#                     with code execution to use.
# WHY Debian's own  : the build stage uses Debian 13's python3 (3.13), the
# python in build     SAME interpreter distroless/python3-debian13 ships, so
#                     the venv's symlink and compiled wheels match at runtime.
#                     (python:3.x-slim builds its own Python in /usr/local and
#                     would NOT match.)
# WHY digests       : tags are mutable - ':nonroot' can point at a different
#                     image tomorrow. tag@sha256 is reproducible, and
#                     Dependabot bumps both together.

# ---------- Stage 1 : build the virtualenv ----------
FROM debian:trixie-20260918-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Dependencies first: this layer is cached and only rebuilt when uv.lock
# changes, not on every code edit. --frozen = install exactly the lock file
# (with its hashes) or fail.
COPY app/pyproject.toml app/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the app itself, installed as a regular (non-editable) package.
# Tests already ran in the CI quality job for this commit.
COPY app/src ./src
RUN uv sync --frozen --no-dev --no-editable

# ---------- Stage 2 : runtime ----------
FROM gcr.io/distroless/python3-debian13:nonroot@sha256:774595d652a294b54c9bd575b2d9fdd1a4b47547dc17b8bfa4c0e953c64855b3

LABEL org.opencontainers.image.title="medicart" \
      org.opencontainers.image.authors="Sanjay Naidu" \
      org.opencontainers.image.description="MediCart online pharmacy (FastAPI) deployed to GKE with Kustomize and GitHub Actions" \
      org.opencontainers.image.source="https://github.com/Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway"

COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# distroless ':nonroot' = UID 65532, matching the pod's runAsUser.
USER 65532:65532
WORKDIR /app

# 8080 = app traffic (Service/Gateway), 9090 = Prometheus metrics (in-cluster only)
EXPOSE 8080 9090

# No HEALTHCHECK: distroless has no shell/curl, and in the cluster the
# Kubernetes probes (/livez, /readyz) are the source of truth.
# The same image also runs the migration init container:
#   python -m medicart.migrate
ENTRYPOINT ["/app/.venv/bin/python", "-m", "medicart"]
