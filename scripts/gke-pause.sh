#!/usr/bin/env bash
# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : scripts/gke-pause.sh - scale nodes to zero between demos
# =============================================================================
# GKE has no "stop cluster" button. The equivalent is scaling the node pool
# to 0: VM cost stops, and the control plane stays (its fee is covered by
# GKE's free tier for one zonal cluster). Every Kubernetes object is kept,
# AND the database disks too: they are persistent disks, not node disks. On
# resume, postgres-0 re-attaches its disk and every order is still there.
#
# Still billed while paused: the Gateway's load balancer (~$0.60/day), the
# static IP, Cloud Armor, and the database disks (a few cents a day).
#
# Usage:  bash scripts/gke-pause.sh pause
#         bash scripts/gke-pause.sh resume
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/config.sh
source "${SCRIPT_DIR}/config.sh"
require_project

case "${1:-}" in
  pause)
    # The autoscaler would immediately add nodes back for the pending pods,
    # so it has to be switched off first.
    step "Disabling autoscaling and scaling ${CLUSTER} to 0 nodes"
    gcloud container clusters update "${CLUSTER}" --zone "${ZONE}" \
      --node-pool "${NODE_POOL}" --no-enable-autoscaling --quiet
    gcloud container clusters resize "${CLUSTER}" --zone "${ZONE}" \
      --node-pool "${NODE_POOL}" --num-nodes 0 --quiet
    info "Paused. Run 'bash scripts/gke-pause.sh resume' before the next demo."
    ;;
  resume)
    step "Scaling ${CLUSTER} back to ${NODE_COUNT} nodes and re-enabling autoscaling"
    gcloud container clusters resize "${CLUSTER}" --zone "${ZONE}" \
      --node-pool "${NODE_POOL}" --num-nodes "${NODE_COUNT}" --quiet
    gcloud container clusters update "${CLUSTER}" --zone "${ZONE}" \
      --node-pool "${NODE_POOL}" --enable-autoscaling \
      --min-nodes "${MIN_NODES}" --max-nodes "${MAX_NODES}" --quiet
    info "Resumed. Pods take ~3-4 minutes (postgres re-attaches its disk first)."
    ;;
  *)
    echo "Usage: $0 pause|resume" >&2
    exit 1
    ;;
esac
