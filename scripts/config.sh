#!/usr/bin/env bash
# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : scripts/config.sh - every resource name, in ONE place
# =============================================================================
# The resources are created by hand in the Google Cloud console, following
# docs/SETUP.md. These names MUST match what you type there; the helper
# scripts (verify-platform, gke-pause, find-leftovers) read them from here.

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"
GITHUB_REPO="${GITHUB_REPO:-Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway}"

NETWORK="medicart-vpc"
SUBNET="medicart-subnet"
AR_REPO="medicart"                 # our images (standard repo, immutable tags)
AR_REMOTE="dockerhub"              # Docker Hub mirror (remote repo)
NODE_SA_NAME="gke-nodes"
DEPLOY_SA_NAME="github-deployer"
CLUSTER="medicart-gke"
NODE_POOL="default-pool"
STATIC_IP="medicart-ip"
ARMOR_POLICY="medicart-armor"
SSL_CERT="medicart-cert"
WIF_POOL="github-pool"
WIF_PROVIDER="github-oidc"
ENVIRONMENTS=(dev prod)

NODE_COUNT=2
MIN_NODES=1
MAX_NODES=3

NODE_SA="${NODE_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }

require_project() {
  if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
    echo "No GCP project selected. Run: gcloud config set project <PROJECT_ID>" >&2
    exit 1
  fi
}
