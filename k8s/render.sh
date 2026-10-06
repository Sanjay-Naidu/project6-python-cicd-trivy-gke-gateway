#!/usr/bin/env bash
# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : k8s/render.sh - build the final manifests for one environment
# =============================================================================
# Usage:  k8s/render.sh <dev|prod>  > rendered.yaml
#
# Required environment variables:
#   GCP_PROJECT_ID   project that holds the Secret Manager secrets
#   APP_HOSTNAME     e.g. dev.34-49-10-20.sslip.io / 34-49-10-20.sslip.io
#   APP_IMAGE        full image ref, must carry an immutable sha- tag
#   APP_VERSION      the tag alone (shown in the UI and /api/info)
#   POSTGRES_IMAGE   full postgres image ref
# Optional:
#   PUBLIC_SCHEME    "https" once the Gateway serves HTTPS (default "http")
#
# Used by BOTH the CI validation job and the deploy job, so what gets
# validated is byte-for-byte what gets applied.
set -euo pipefail

ENVIRONMENT="${1:?usage: render.sh <dev|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY="${ROOT}/overlays/${ENVIRONMENT}"
[[ -d "${OVERLAY}" ]] || { echo "render.sh: no overlay '${ENVIRONMENT}'" >&2; exit 1; }

for var in GCP_PROJECT_ID APP_HOSTNAME APP_IMAGE APP_VERSION POSTGRES_IMAGE; do
  [[ -n "${!var:-}" ]] || { echo "render.sh: ${var} is not set" >&2; exit 1; }
done

# Immutable tags only. 'latest' (or no tag) means "whatever was pushed last",
# which makes rollbacks and "what is running?" impossible to answer.
if [[ ! "${APP_IMAGE}" =~ :sha-[0-9a-f]{7,40}$ ]]; then
  echo "render.sh: APP_IMAGE must end in an immutable :sha-<commit> tag (got '${APP_IMAGE}')" >&2
  exit 1
fi

export APP_ENV="${ENVIRONMENT}"
export PUBLIC_SCHEME="${PUBLIC_SCHEME:-http}"
[[ "${PUBLIC_SCHEME}" =~ ^https?$ ]] || { echo "render.sh: PUBLIC_SCHEME must be http or https" >&2; exit 1; }
export GCP_PROJECT_ID APP_HOSTNAME APP_IMAGE APP_VERSION POSTGRES_IMAGE

# The explicit list matters: envsubst without one would replace EVERY $word
# in the YAML (e.g. "$PGDATA" in the postgres preStop hook).
rendered="$(kubectl kustomize "${OVERLAY}" \
  | envsubst '${GCP_PROJECT_ID} ${APP_ENV} ${APP_HOSTNAME} ${APP_IMAGE} ${APP_VERSION} ${POSTGRES_IMAGE} ${PUBLIC_SCHEME}')"

# Fail loudly on any placeholder that slipped through (a typo in a manifest,
# or a new variable someone forgot to add to the list above).
if leftovers="$(grep -n '\${[A-Z_]*}' <<<"${rendered}")"; then
  echo "render.sh: unsubstituted variables:" >&2
  echo "${leftovers}" >&2
  exit 1
fi

printf '%s\n' "${rendered}"
