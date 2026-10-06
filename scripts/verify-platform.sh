#!/usr/bin/env bash
# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : scripts/verify-platform.sh - check the console work, READ-ONLY
# =============================================================================
# You build the platform by hand in the console (docs/SETUP.md). Clicking
# through forms makes it easy to miss one checkbox, and the symptom often
# shows up much later and far away (e.g. a missing Secret Manager grant
# surfaces as a pod stuck in ContainerCreating). This script inspects every
# resource and prints PASS/FAIL with the SETUP phase to revisit.
#
# It only runs describe/list commands: it never creates or changes anything.
#
# Usage (Cloud Shell, repo root):  bash scripts/verify-platform.sh
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/config.sh
source "${SCRIPT_DIR}/config.sh"
require_project

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
PASS=0
FAIL=0

# check "<description>" "<phase>" "<actual>" "<expected regex>"
check() {
  local desc="$1" phase="$2" actual="$3" expected="$4"
  if [[ "${actual}" =~ ${expected} ]]; then
    printf '  \033[0;32mPASS\033[0m %s\n' "${desc}"
    PASS=$((PASS + 1))
  else
    printf '  \033[0;31mFAIL\033[0m %s  (got: %s)  -> docs/SETUP.md %s\n' "${desc}" "${actual:-<nothing>}" "${phase}"
    FAIL=$((FAIL + 1))
  fi
}

step "Project ${PROJECT_ID} (${PROJECT_NUMBER}), region ${REGION}, zone ${ZONE}"

# -----------------------------------------------------------------------------
step "Phase 2 - APIs"
ENABLED="$(gcloud services list --enabled --format='value(config.name)')"
for api in compute container artifactregistry iam iamcredentials sts secretmanager cloudresourcemanager; do
  check "${api}.googleapis.com enabled" "Phase 2" "$(grep -x "${api}.googleapis.com" <<<"${ENABLED}")" "^${api}"
done

# -----------------------------------------------------------------------------
step "Phase 3 - Network"
check "VPC ${NETWORK} is custom-mode" "Phase 3" \
  "$(gcloud compute networks describe "${NETWORK}" --format='value(autoCreateSubnetworks)' 2>/dev/null)" "^False$"
SUBNET_JSON="$(gcloud compute networks subnets describe "${SUBNET}" --region "${REGION}" \
  --format='value(ipCidrRange,privateIpGoogleAccess,secondaryIpRanges[].rangeName.list())' 2>/dev/null)"
check "Subnet ${SUBNET} range 10.10.0.0/20" "Phase 3" "$(cut -f1 <<<"${SUBNET_JSON}")" "^10\.10\.0\.0/20$"
check "Private Google Access ON (private nodes reach Google APIs)" "Phase 3" "$(cut -f2 <<<"${SUBNET_JSON}")" "^True$"
check "Secondary ranges 'pods' and 'services'" "Phase 3" "$(cut -f3 <<<"${SUBNET_JSON}")" "pods.*services|services.*pods"

# -----------------------------------------------------------------------------
step "Phase 4 - Artifact Registry"
check "Repo ${AR_REPO}: immutable tags" "Phase 4" \
  "$(gcloud artifacts repositories describe "${AR_REPO}" --location "${REGION}" --format='value(dockerConfig.immutableTags)' 2>/dev/null)" "^True$"
check "Repo ${AR_REMOTE}: remote (Docker Hub mirror)" "Phase 4" \
  "$(gcloud artifacts repositories describe "${AR_REMOTE}" --location "${REGION}" --format='value(mode)' 2>/dev/null)" "REMOTE_REPOSITORY"

# -----------------------------------------------------------------------------
step "Phase 5 - Service accounts and roles"
project_roles() {
  gcloud projects get-iam-policy "${PROJECT_ID}" --flatten=bindings \
    --filter="bindings.members:serviceAccount:$1" --format='value(bindings.role)' 2>/dev/null | tr '\n' ' '
}
repo_members() {
  gcloud artifacts repositories get-iam-policy "$1" --location "${REGION}" --format=json 2>/dev/null | tr -d ' \n'
}
check "${NODE_SA_NAME}: container.defaultNodeServiceAccount" "Phase 5" "$(project_roles "${NODE_SA}")" "roles/container\.defaultNodeServiceAccount"
check "${DEPLOY_SA_NAME}: container.developer" "Phase 5" "$(project_roles "${DEPLOY_SA}")" "roles/container\.developer"
check "${DEPLOY_SA_NAME} has NO Owner/Editor (least privilege)" "Phase 5"   "$(grep -Eo 'roles/(owner|editor)' <<<"$(project_roles "${DEPLOY_SA}")" || echo none)" "^none$"
check "${NODE_SA_NAME} can read ${AR_REPO}" "Phase 5" "$(repo_members "${AR_REPO}")" "artifactregistry\.reader.*${NODE_SA_NAME}|${NODE_SA_NAME}.*artifactregistry\.reader"
check "${NODE_SA_NAME} can read ${AR_REMOTE}" "Phase 5" "$(repo_members "${AR_REMOTE}")" "artifactregistry\.reader.*${NODE_SA_NAME}|${NODE_SA_NAME}.*artifactregistry\.reader"
check "${DEPLOY_SA_NAME} can push to ${AR_REPO}" "Phase 5" "$(repo_members "${AR_REPO}")" "artifactregistry\.writer.*${DEPLOY_SA_NAME}|${DEPLOY_SA_NAME}.*artifactregistry\.writer"

# -----------------------------------------------------------------------------
step "Phase 6 - Static IP"
check "Global static IP ${STATIC_IP}" "Phase 6" \
  "$(gcloud compute addresses describe "${STATIC_IP}" --global --format='value(address)' 2>/dev/null)" "^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$"

# -----------------------------------------------------------------------------
step "Phase 7 - GKE cluster"
C="$(gcloud container clusters describe "${CLUSTER}" --zone "${ZONE}" --format=json 2>/dev/null)"
[[ -n "${C}" ]] || C='{}'
cfield() { jq -r "$1 // empty" <<<"${C}"; }
check "Cluster ${CLUSTER} RUNNING" "Phase 7" "$(cfield '.status')" "^RUNNING$"
check "Private nodes" "Phase 7" \
  "$(cfield '(.privateClusterConfig.enablePrivateNodes // .networkConfig.defaultEnablePrivateNodes) | tostring')" "^true$"
check "DNS-based control plane endpoint (external traffic allowed)" "Phase 7" \
  "$(cfield '.controlPlaneEndpointsConfig.dnsEndpointConfig.allowExternalTraffic | tostring')" "^true$"
check "Workload Identity pool ${PROJECT_ID}.svc.id.goog" "Phase 7" "$(cfield '.workloadIdentityConfig.workloadPool')" "^${PROJECT_ID}\.svc\.id\.goog$"
check "Secret Manager add-on" "Phase 7" "$(cfield '.secretManagerConfig.enabled | tostring')" "^true$"
check "Gateway API (standard channel)" "Phase 7" "$(cfield '.networkConfig.gatewayApiConfig.channel')" "CHANNEL_STANDARD"
check "Dataplane V2 (NetworkPolicy enforcement)" "Phase 7" "$(cfield '.networkConfig.datapathProvider')" "ADVANCED_DATAPATH"
check "Nodes run as ${NODE_SA_NAME}" "Phase 7" "$(cfield '.nodeConfig.serviceAccount')" "^${NODE_SA}$"
check "Shielded nodes: secure boot" "Phase 7" "$(cfield '.nodeConfig.shieldedInstanceConfig.enableSecureBoot | tostring')" "^true$"
check "Node pool autoscaling ${MIN_NODES}-${MAX_NODES}" "Phase 7" \
  "$(cfield '.nodePools[0].autoscaling | "\(.minNodeCount)-\(.maxNodeCount)"')" "^${MIN_NODES}-${MAX_NODES}$"

# -----------------------------------------------------------------------------
step "Phase 8 - Secret Manager"
for env in "${ENVIRONMENTS[@]}"; do
  for s in db-password session-key; do
    name="medicart-${env}-${s}"
    check "Secret ${name} has an enabled version" "Phase 8" \
      "$(gcloud secrets versions list "${name}" --filter='state=ENABLED' --format='value(name)' --limit=1 2>/dev/null)" "^[0-9]+$"
    check "  ...readable by namespace medicart-${env} ONLY" "Phase 8" \
      "$(gcloud secrets get-iam-policy "${name}" --format=json 2>/dev/null | jq -r '[.bindings[]? | select(.role=="roles/secretmanager.secretAccessor") | .members[]] | join(",")')" \
      "^[^,]*namespace/medicart-${env}$"
  done
done

# -----------------------------------------------------------------------------
step "Phase 9 - Cloud Armor"
ARMOR_RULES=",$(gcloud compute security-policies describe "${ARMOR_POLICY}" \
  --format='value(rules[].priority.list())' 2>/dev/null | tr ';' ','),"
for prio in 1000 2000 2100; do # login rate-ban, SQLi, XSS (any order)
  check "Policy ${ARMOR_POLICY} has rule ${prio}" "Phase 9" "${ARMOR_RULES}" ",${prio},"
done

# -----------------------------------------------------------------------------
step "Phase 10 - GitHub OIDC (Workload Identity Federation)"
COND="$(gcloud iam workload-identity-pools providers describe "${WIF_PROVIDER}" --location global \
  --workload-identity-pool "${WIF_POOL}" --format='value(attributeCondition)' 2>/dev/null)"
check "Provider condition pins repository ${GITHUB_REPO}" "Phase 10" "${COND}" "assertion\.repository == '${GITHUB_REPO}'"
check "Provider condition pins repository_owner_id" "Phase 10" "${COND}" "repository_owner_id =="
check "Repo may impersonate ${DEPLOY_SA_NAME}" "Phase 10" \
  "$(gcloud iam service-accounts get-iam-policy "${DEPLOY_SA}" --format=json 2>/dev/null | tr -d ' \n')" \
  "workloadIdentityUser.*attribute\.repository/${GITHUB_REPO}"

# -----------------------------------------------------------------------------
step "Phase 11 - Cluster platform layer (kubectl)"
if gcloud container clusters get-credentials "${CLUSTER}" --zone "${ZONE}" --dns-endpoint >/dev/null 2>&1; then
  for ns in medicart-dev medicart-prod gateway-infra; do
    check "Namespace ${ns} enforces Pod Security 'restricted'" "Phase 11" \
      "$(kubectl get ns "${ns}" -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}' 2>/dev/null)" "^restricted$"
  done
  check "StorageClass medicart-pd (Retain)" "Phase 11" \
    "$(kubectl get storageclass medicart-pd -o jsonpath='{.reclaimPolicy}' 2>/dev/null)" "^Retain$"
  check "Gateway medicart-gateway programmed" "Phase 11" \
    "$(kubectl -n gateway-infra get gateway medicart-gateway -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}' 2>/dev/null)" "^True$"
else
  check "kubectl access through the DNS endpoint" "Phase 7" "" "x"
fi

printf '\n%s passed, %s failed\n' "${PASS}" "${FAIL}"
[[ "${FAIL}" -eq 0 ]]
