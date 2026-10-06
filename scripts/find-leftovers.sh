#!/usr/bin/env bash
# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : scripts/find-leftovers.sh - after teardown: is anything still billing?
# =============================================================================
# Run at the end of the teardown in docs/SETUP.md (Phase 15). READ-ONLY: it
# lists what's left and never deletes anything. Delete leftovers in the
# console, and understand WHY each one survived. The two classic ones:
#   - database disks: the StorageClass uses reclaimPolicy: Retain on purpose,
#     so deleting the cluster does NOT delete them;
#   - load balancer pieces: if the cluster was deleted before the Gateway,
#     the controller was gone before it could clean up.
#
# Usage (Cloud Shell):  bash scripts/find-leftovers.sh
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/config.sh
source "${SCRIPT_DIR}/config.sh"
require_project

FOUND=0
show() {
  local title="$1"
  shift
  local out
  out="$("$@" 2>/dev/null)"
  if [[ -n "${out}" ]]; then
    printf '\n\033[0;31m%s\033[0m\n%s\n' "${title}" "${out}"
    FOUND=1
  else
    printf '  \033[0;32mnone\033[0m  %s\n' "${title}"
  fi
}

step "Billable resources still present in ${PROJECT_ID}"
show "GKE clusters"            gcloud container clusters list --format='table(name,location,status)'
show "VM instances"            gcloud compute instances list --format='table(name,zone,status)'
show "Persistent disks"        gcloud compute disks list --format='table(name,zone,sizeGb,users.basename())'
show "Disk snapshots"          gcloud compute snapshots list --format='table(name,diskSizeGb,creationTimestamp)'
show "Forwarding rules (LB)"   gcloud compute forwarding-rules list --format='table(name,region,IPAddress)'
show "Backend services"        gcloud compute backend-services list --format='table(name,region)'
show "Network endpoint groups" gcloud compute network-endpoint-groups list --format='table(name,zone)'
show "Static IP addresses"     gcloud compute addresses list --format='table(name,region,address,status)'
show "SSL certificates"        gcloud compute ssl-certificates list --format='table(name,type,managed.status)'
show "Cloud Armor policies"    gcloud compute security-policies list --format='table(name)'
show "Artifact Registry repos" gcloud artifacts repositories list --format='table(name,format,mode)'
show "Secrets"                 gcloud secrets list --format='table(name)'

if [[ "${FOUND}" -eq 0 ]]; then
  printf '\nNothing billable left. (VPC, service accounts and the OIDC pool cost nothing, but delete them too for a clean project.)\n'
else
  printf '\nDelete the items above in the console (docs/SETUP.md, Phase 15 has the order).\n'
fi
