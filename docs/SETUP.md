# Setup Guide — Project 6: MediCart on GKE, built by hand in the console

**Author: Sanjay Naidu**

In Project 5 a setup script created the whole platform. That was fast, but a script hides the decisions. This time **every Google Cloud resource is created by hand in the console**, in dependency order, and each step says *why* it exists and what breaks without it. The pipeline (GitHub Actions) only deploys the application. It never creates infrastructure.

Each phase has the same four parts:

- **Why**: what the resource is for.
- **Console steps**: what to click and type.
- **CLI equivalent**: the same thing as a `gcloud` command, folded away. It's worth reading: interviewers ask "how would you automate this?", and it's also the exact spec if a console label has moved.
- **Check**: how to confirm it worked.

After Phase 11, `scripts/verify-platform.sh` inspects everything you built and prints PASS/FAIL per item.

> Console labels change over time. If a field isn't exactly where this guide says, look for the same setting nearby. The CLI block beside it is the source of truth for the values.

Budget about **2–3 hours** the first time, including ~10 minutes of waiting for the cluster and 15–60 minutes for the HTTPS certificate.

---

## Phase 0 — What you need, and what it costs

- A Google Cloud **free-trial** account ($300 credit, 90 days). While it stays a trial (you never click "Activate full account"), Google **cannot charge your card**.
- A GitHub account (`Sanjay-Naidu`).
- `git` on your laptop, only to push the code. Everything else runs in **Cloud Shell** (the browser terminal in the console) or on GitHub's runners. **Docker is not needed on your laptop.**

Running 24×7 this costs about **$2.75/day** (~$83/month, see the README for the breakdown). `scripts/gke-pause.sh pause` drops that to about $1.10/day between sessions.

**Names used everywhere** (they're also in [scripts/config.sh](../scripts/config.sh)):

| Thing | Name |
|---|---|
| Region / zone | `us-central1` / `us-central1-a` |
| VPC / subnet | `medicart-vpc` / `medicart-subnet` |
| Artifact Registry | `medicart` (our images), `dockerhub` (Docker Hub mirror) |
| Service accounts | `gke-nodes`, `github-deployer` |
| Static IP | `medicart-ip` |
| GKE cluster | `medicart-gke` |
| Secrets | `medicart-{dev,prod}-db-password`, `medicart-{dev,prod}-session-key` |
| Cloud Armor | `medicart-armor` |
| Certificate | `medicart-cert` |
| OIDC pool / provider | `github-pool` / `github-oidc` |
| GitHub repo | `Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway` |

---

## Phase 1 — GCP project and GitHub repository

### 1.1 GCP project

**Why:** a project is the boundary for billing, IAM and quotas. A dedicated one means the ultimate cleanup is "delete the project".

1. Open [console.cloud.google.com](https://console.cloud.google.com) → project picker (top bar) → **New project**. Name: `medicart-demo`. Note the generated **Project ID** (e.g. `medicart-demo-472915`). You'll use the ID everywhere, not the name.
2. **Billing → Account management**: confirm the project is linked to the free-trial billing account.
3. **Billing → Budgets & alerts → Create budget**: scope = this project, amount `60` USD, thresholds 50/90/100%. On a trial it can't cost you money, but it tells you when the credit burns faster than planned.
4. Open **Cloud Shell** (the `>_` icon, top right) and run:

```bash
gcloud config set project <PROJECT_ID>
gcloud projects describe <PROJECT_ID> --format='value(projectNumber)'   # note the PROJECT NUMBER too
```

You'll need both the **project ID** (letters) and the **project number** (digits) later.

### 1.2 GitHub repository and first push

On github.com create a **public** repository named `project6-python-cicd-trivy-gke-gateway`, with no README, license or .gitignore (the project has them). Public means free Actions minutes, the free Security tab, and environment protection rules on the Free plan.

Commit in logical steps rather than one giant commit: the history then reads like real work and each commit is reviewable. From the project folder (PowerShell):

```powershell
cd "<path>\Project-6-python_app_CICD+trivy+GKE"
git init -b main
git config user.name  "Sanjay Naidu"
git config user.email "<the email verified on your GitHub account>"   # same as Project 5, so commits link to your profile

git add app/ .gitignore .gitattributes LICENSE
git commit -m "MediCart: FastAPI online pharmacy with PostgreSQL, tests and migrations"

git add Dockerfile .dockerignore .trivyignore
git commit -m "Distroless, non-root container image"

git add k8s/ platform/
git commit -m "Kubernetes manifests: Kustomize overlays, Gateway API, StatefulSet with persistent disk"

git add .github/ scripts/
git commit -m "CI/CD: GitHub Actions with OIDC, Trivy gates and gated promotion to prod"

git add README.md docs/
git commit -m "Docs: architecture, console setup guide, operations runbook"

git remote add origin https://github.com/Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway.git
git push -u origin main
```

The push triggers **Build and Deploy**. With no GCP variables set yet it runs lint, tests and the Kubernetes validation, builds the image and Trivy-scans it, then **skips** push/deploy. That's expected, and a useful first check that the build is green before any cloud work.

Also enable **Settings → Code security → Code scanning** (free on public repos), so the PR pipeline can upload Trivy findings.

---

## Phase 2 — Enable the APIs

**Why:** every Google service is off by default in a new project. Calls to a disabled API fail with `SERVICE_DISABLED`.

**Console:** **APIs & Services → Library**. Search for and **Enable** each one:

| API | Needed for |
|---|---|
| Compute Engine API | VPC, disks, IPs, load balancer, Cloud Armor |
| Kubernetes Engine API | the cluster |
| Artifact Registry API | image registry |
| Identity and Access Management (IAM) API | service accounts |
| IAM Service Account Credentials API | GitHub impersonating the deployer SA |
| Security Token Service API | exchanging GitHub's OIDC token |
| Secret Manager API | the database password and session key |
| Cloud Resource Manager API | project IAM lookups |

<details><summary>CLI equivalent</summary>

```bash
gcloud services enable compute.googleapis.com container.googleapis.com \
  artifactregistry.googleapis.com iam.googleapis.com iamcredentials.googleapis.com \
  sts.googleapis.com secretmanager.googleapis.com cloudresourcemanager.googleapis.com
```
</details>

**Check:** **APIs & Services → Enabled APIs & services** lists all eight.

---

## Phase 3 — Network (VPC + subnet)

**Why:**
- A **custom VPC** gives an explicit IP plan, and nothing else lives in it.
- **Secondary ranges** make the cluster *VPC-native*: pods get real VPC IPs, which is what lets the load balancer send traffic straight to pods (container-native load balancing).
- **Private Google Access** lets nodes without public IPs reach Google APIs (Artifact Registry, Secret Manager).

Note there is **no Cloud NAT** this time (Project 5 had one). The nodes get *no* internet access at all: our image comes from Artifact Registry, and PostgreSQL comes through an Artifact Registry *mirror* of Docker Hub (Phase 4). One less resource to pay for, and a compromised pod has no route to the internet.

**Console:** **VPC network → VPC networks → Create VPC network**

1. Name: `medicart-vpc`. Subnet creation mode: **Custom**.
2. **New subnet**:
   - Name `medicart-subnet`, Region `us-central1`, IPv4 range `10.10.0.0/20` (nodes)
   - Click **Add a secondary IPv4 range** twice:
     - `pods` → `10.20.0.0/16`
     - `services` → `10.30.0.0/20`
   - **Private Google Access: On**
   - Flow logs: Off
3. Firewall rules: leave all *unticked*. GKE creates the rules it needs when the cluster and the load balancer are created.
4. Dynamic routing mode: Regional. **Create**.

<details><summary>CLI equivalent</summary>

```bash
gcloud compute networks create medicart-vpc --subnet-mode=custom
gcloud compute networks subnets create medicart-subnet --network medicart-vpc \
  --region us-central1 --range 10.10.0.0/20 \
  --secondary-range pods=10.20.0.0/16,services=10.30.0.0/20 \
  --enable-private-ip-google-access
```
</details>

**Check:** open the subnet. It shows both secondary ranges, with Private Google Access **On**.

---

## Phase 4 — Artifact Registry (two repositories)

**Why:**
- **`medicart`** (standard): where the pipeline pushes our images. **Immutable tags**: once `sha-abc1234` exists it can never be overwritten, so a tag always means exactly one image.
- **`dockerhub`** (remote): a pull-through cache of Docker Hub. The private nodes pull `postgres` from *here*, which works without internet access, avoids Docker Hub's anonymous rate limits, and keeps a copy even if upstream deletes the tag.

**Console:** **Artifact Registry → Repositories → Create repository**

**Repository 1: `medicart`**
- Format **Docker**, Mode **Standard**, Location type **Region** → `us-central1`
- **Immutable image tags: Enabled**
- Encryption: Google-managed
- **Cleanup policies**: select *Delete artifacts* (not dry run), then add two policies:
  - `keep-10` → Policy type **Keep most recent versions**, count `10`
  - `delete-old` → Policy type **Conditional delete**, *Older than* `30d`
- Vulnerability scanning: **Disabled**. Trivy already scans in CI, and Artifact Analysis costs ~$0.26 per image scanned.
- **Create**

**Repository 2: `dockerhub`**
- Format **Docker**, Mode **Remote**
- Remote repository source: **Docker Hub**, Authentication: **Unauthenticated**
- Location: `us-central1`. **Create**.

<details><summary>CLI equivalent</summary>

```bash
gcloud artifacts repositories create medicart --repository-format=docker \
  --location=us-central1 --immutable-tags --description="MediCart images"
cat > /tmp/cleanup.json <<'EOF'
[{"name":"keep-10","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":10}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"olderThan":"30d"}}]
EOF
gcloud artifacts repositories set-cleanup-policies medicart --location=us-central1 \
  --policy=/tmp/cleanup.json --no-dry-run

gcloud artifacts repositories create dockerhub --repository-format=docker \
  --location=us-central1 --mode=remote-repository \
  --remote-docker-repo=DOCKER-HUB --description="Docker Hub mirror"
```
</details>

**Check:** both repos are listed. `medicart` shows *Immutable tags: Enabled*, and `dockerhub` shows *Mode: Remote*.

---

## Phase 5 — Service accounts (least privilege)

**Why:** two machine identities, each with only what it needs.

| SA | Used by | Gets | Deliberately does NOT get |
|---|---|---|---|
| `gke-nodes` | the cluster's VMs | write logs/metrics, pull images from our two repos | Editor (the default Compute SA has Editor on the whole project) |
| `github-deployer` | GitHub Actions | push to `medicart` repo, deploy into the cluster | create VMs, change IAM, delete the cluster |

**Console:** **IAM & Admin → Service Accounts → Create service account**

1. **`gke-nodes`** (display name "GKE nodes"): **Create and continue**, then under *Grant this service account access to project* add the role **Kubernetes Engine Default Node Service Account**. **Done**.
2. **`github-deployer`** (display name "GitHub Actions deployer"): add the role **Kubernetes Engine Developer**. **Done**.

Repository-level permissions (*not* project-wide, so the grant covers one repo only):

3. **Artifact Registry → `medicart` → Permissions** (or tick the repo and click **Show info panel**) → **Add principal**:
   - `gke-nodes@<PROJECT_ID>.iam.gserviceaccount.com` → **Artifact Registry Reader**
   - `github-deployer@<PROJECT_ID>.iam.gserviceaccount.com` → **Artifact Registry Writer**
4. **Artifact Registry → `dockerhub` → Add principal**:
   - `gke-nodes@<PROJECT_ID>.iam.gserviceaccount.com` → **Artifact Registry Reader**

<details><summary>CLI equivalent</summary>

```bash
P=$(gcloud config get-value project)
gcloud iam service-accounts create gke-nodes --display-name="GKE nodes"
gcloud iam service-accounts create github-deployer --display-name="GitHub Actions deployer"
gcloud projects add-iam-policy-binding $P --role roles/container.defaultNodeServiceAccount \
  --member serviceAccount:gke-nodes@$P.iam.gserviceaccount.com --condition None
gcloud projects add-iam-policy-binding $P --role roles/container.developer \
  --member serviceAccount:github-deployer@$P.iam.gserviceaccount.com --condition None
for repo in medicart dockerhub; do
  gcloud artifacts repositories add-iam-policy-binding $repo --location us-central1 \
    --role roles/artifactregistry.reader --member serviceAccount:gke-nodes@$P.iam.gserviceaccount.com
done
gcloud artifacts repositories add-iam-policy-binding medicart --location us-central1 \
  --role roles/artifactregistry.writer --member serviceAccount:github-deployer@$P.iam.gserviceaccount.com
```
</details>

**Check:** **IAM & Admin → IAM** shows each SA with exactly one project role.

---

## Phase 6 — Global static IP (and your hostnames)

**Why:** the load balancer's public address. Reserving it separately means it survives if the Gateway is deleted and re-created, and it's what DNS points at.

**Console:** **VPC network → IP addresses → Reserve external static IP address**
- Name `medicart-ip`, Network Service Tier **Premium**, IP version **IPv4**, Type **Global**. **Reserve**.

Note the address, e.g. `34.49.10.20`.

**Your hostnames, with no domain to buy:** [sslip.io](https://sslip.io) is a free public DNS service where any name containing an IP resolves to that IP. With the example address:

| Environment | Hostname |
|---|---|
| prod | `34-49-10-20.sslip.io` |
| dev | `dev.34-49-10-20.sslip.io` |

Check that DNS works: `nslookup dev.34-49-10-20.sslip.io` should return your IP. The Gateway routes by hostname, which is how **one** load balancer serves both environments. If you own a domain, create two A records (`@` and `dev`) pointing at the IP and use those names instead.

<details><summary>CLI equivalent</summary>

```bash
gcloud compute addresses create medicart-ip --global --ip-version IPV4
gcloud compute addresses describe medicart-ip --global --format='value(address)'
```
</details>

---

## Phase 7 — The GKE cluster

**Why each setting** is in the README's *Infrastructure* table. The short version: private nodes, a least-privilege node identity, Workload Identity, the Secret Manager add-on, Gateway API, and Dataplane V2 for NetworkPolicy.

**Console:** **Kubernetes Engine → Clusters → Create**. If you're offered Autopilot, choose **Switch to Standard cluster** (or *Create → Standard: You manage your cluster*).

**Cluster basics**
- Name `medicart-gke`
- Location type **Zonal**, Zone `us-central1-a`. GKE's free tier covers the management fee for exactly one zonal cluster.
- Control plane version: **Release channel → Regular** (default version)

**Node pools → default-pool**
- Number of nodes: `2`
- **Enable cluster autoscaler: on**. Location policy *Balanced*, Minimum `1`, Maximum `3` (per zone)
- Surge upgrade: max surge `1`, max unavailable `0`
- **Nodes**: Image type *Container-Optimized OS with containerd*; Machine configuration **E2 → `e2-medium`**; Boot disk type **Standard persistent disk**, size `30` GB. Leave Spot VMs off.
- **Security**: Service account **`gke-nodes`**; tick **Enable secure boot** and **Enable integrity monitoring**

**Cluster → Automation**
- Autoscaling profile: **Optimize utilization**

**Cluster → Networking**
- Network `medicart-vpc`, Node subnet `medicart-subnet`
- **Enable private nodes: on**. Nodes get no public IPs.
- **Control plane access**:
  - **Access using DNS: on**. This is what GitHub's runners and Cloud Shell use (see "What broke" in the README for why).
  - Access using IPv4 addresses: leave the default (enabled, authorized networks on)
- Untick *Automatically create secondary ranges*. Cluster default Pod address range: **`pods`**; Service address range: **`services`**
- **Enable Dataplane V2: on**
- **Enable Gateway API: on** (Standard channel)
- HTTP load balancing: on (default)

**Cluster → Security**
- **Enable Workload Identity: on** (pool `<PROJECT_ID>.svc.id.goog`)
- **Enable Secret Manager: on** (this installs the Secret Manager CSI add-on)
- **Enable Shielded GKE Nodes: on**
- Everything else at its default

**Cluster → Features**
- Logging: **System** only (untick Workloads). Stays inside the free Cloud Logging allotment; app logs are still available through `kubectl logs`.
- Cloud Monitoring: **System**. Managed Service for Prometheus: leave at its default.
- Backup for GKE, Cost allocation: off

**Create.** Provisioning takes 6–10 minutes.

<details><summary>CLI equivalent (the exact spec)</summary>

```bash
P=$(gcloud config get-value project)
gcloud container clusters create medicart-gke \
  --zone us-central1-a --release-channel regular \
  --network medicart-vpc --subnetwork medicart-subnet --enable-ip-alias \
  --cluster-secondary-range-name pods --services-secondary-range-name services \
  --enable-private-nodes --enable-dns-access \
  --enable-dataplane-v2 --gateway-api=standard \
  --workload-pool $P.svc.id.goog --enable-secret-manager \
  --service-account gke-nodes@$P.iam.gserviceaccount.com \
  --machine-type e2-medium --disk-type pd-standard --disk-size 30 \
  --num-nodes 2 --enable-autoscaling --min-nodes 1 --max-nodes 3 \
  --autoscaling-profile optimize-utilization \
  --enable-shielded-nodes --shielded-secure-boot --shielded-integrity-monitoring \
  --enable-autorepair --enable-autoupgrade \
  --logging SYSTEM --monitoring SYSTEM
```
</details>

**Check (Cloud Shell):**

```bash
# --dns-endpoint: the IP endpoint only accepts authorized networks
gcloud container clusters get-credentials medicart-gke --zone us-central1-a --dns-endpoint
kubectl get nodes -o wide              # 2 nodes, INTERNAL-IP only, no EXTERNAL-IP
kubectl get gatewayclass               # includes gke-l7-global-external-managed
kubectl get crd | grep -E 'secretproviderclasses|healthcheckpolicies|gcpbackendpolicies'
```

---

## Phase 8 — Secret Manager (4 secrets) + who may read them

**Why:** the database password and the session-signing key never appear in git, in GitHub, or in a Kubernetes Secret. They live only in Secret Manager. At pod start the Secret Manager add-on fetches them *as the pod's Kubernetes ServiceAccount* and mounts them as in-memory files. Access is granted **per environment**: dev pods can't read prod secrets.

### 8.1 Create the secrets

Generate strong values in **Cloud Shell** (run once per secret, and paste each value only into the console):

```bash
openssl rand -hex 24     # use for a db-password
openssl rand -hex 32     # use for a session-key
```

**Console:** **Security → Secret Manager → Create secret**, four times:

| Name | Value |
|---|---|
| `medicart-dev-db-password` | a fresh `openssl rand -hex 24` |
| `medicart-dev-session-key` | a fresh `openssl rand -hex 32` |
| `medicart-prod-db-password` | a fresh `openssl rand -hex 24` |
| `medicart-prod-session-key` | a fresh `openssl rand -hex 32` |

Replication policy: **Automatic**. Leave rotation and expiration unset. **Create secret**.

> The database password is read by PostgreSQL **only on its very first start** (initdb). Changing the secret later does not change the database's password; see "Rotating the DB password" in [OPERATIONS.md](OPERATIONS.md).

### 8.2 Grant access, per environment

Each Kubernetes namespace is a Google Cloud principal through **Workload Identity Federation for GKE**. Its identifier:

```
principalSet://iam.googleapis.com/projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/<PROJECT_ID>.svc.id.goog/namespace/medicart-prod
```

(`<PROJECT_NUMBER>` is the digits from Phase 1. The pool `<PROJECT_ID>.svc.id.goog` came into existence with the cluster, which is why this phase comes after Phase 7.)

For **each prod secret** (`medicart-prod-db-password`, `medicart-prod-session-key`): open the secret → **Permissions** tab → **Grant access** → New principals: the `.../namespace/medicart-prod` identifier above → Role **Secret Manager Secret Accessor** → **Save**.

Do the same for the **two dev secrets**, with `.../namespace/medicart-dev`.

<details><summary>CLI equivalent (values never touch your clipboard)</summary>

```bash
P=$(gcloud config get-value project); N=$(gcloud projects describe $P --format='value(projectNumber)')
for env in dev prod; do
  openssl rand -hex 24 | tr -d '\n' | gcloud secrets create medicart-$env-db-password --data-file=- --replication-policy=automatic
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets create medicart-$env-session-key --data-file=- --replication-policy=automatic
  for s in db-password session-key; do
    gcloud secrets add-iam-policy-binding medicart-$env-$s --role roles/secretmanager.secretAccessor \
      --member "principalSet://iam.googleapis.com/projects/$N/locations/global/workloadIdentityPools/$P.svc.id.goog/namespace/medicart-$env"
  done
done
```
</details>

Finer-grained option (good for an interview answer): grant each secret to one ServiceAccount instead of the whole namespace, e.g. `principal://.../subject/ns/medicart-prod/sa/medicart` for the session key, since PostgreSQL never needs it.

---

## Phase 9 — Cloud Armor (WAF + rate limiting)

**Why:** filters requests at Google's edge, **before** they reach the cluster. It blocks SQL injection and XSS attempts and throttles password guessing on `/login`. The app defends itself too (bound parameters, escaping, CSRF tokens); this is the independent outer layer.

**Console:** **Network Security → Cloud Armor policies → Create policy**
- Name `medicart-armor`, Policy type **Backend security policy**, Default rule action **Allow**. **Next step**.
- **Add a rule** three times (use **Advanced mode** for the match):

| Priority | Match (advanced mode) | Action |
|---|---|---|
| `1000` | `request.path == '/login' && request.method == 'POST'` | **Rate-based ban**: 10 requests per 60 s, Enforce on key **IP**, Exceed action **Deny (429)**, Ban duration **300 s** |
| `2000` | `evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})` | **Deny (403)** |
| `2100` | `evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})` | **Deny (403)** |

- Skip *Apply policy to targets*: the Gateway attaches it through `GCPBackendPolicy` (`k8s/base/app-gateway-policies.yaml`). **Create policy**.

Sensitivity 1 = only high-confidence signatures, so few false positives on normal form input. Cost: $5/month per policy + $1 per rule.

<details><summary>CLI equivalent</summary>

```bash
gcloud compute security-policies create medicart-armor --type=CLOUD_ARMOR
gcloud compute security-policies rules create 1000 --security-policy medicart-armor \
  --expression "request.path == '/login' && request.method == 'POST'" \
  --action rate-based-ban --rate-limit-threshold-count 10 --rate-limit-threshold-interval-sec 60 \
  --ban-duration-sec 300 --conform-action allow --exceed-action deny-429 --enforce-on-key IP
gcloud compute security-policies rules create 2000 --security-policy medicart-armor \
  --expression "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})" --action deny-403
gcloud compute security-policies rules create 2100 --security-policy medicart-armor \
  --expression "evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})" --action deny-403
```
</details>

---

## Phase 10 — GitHub → Google Cloud trust (OIDC, no keys)

**Why:** GitHub Actions must push images and deploy, *without* a service-account JSON key. GitHub signs a short-lived token for each workflow run; Google verifies that the token came from **your repository** and swaps it for a 1-hour token of `github-deployer`. The full explanation, compared with the Azure setup from Projects 3–4, is in [GITHUB-OIDC-TO-GCP.md](GITHUB-OIDC-TO-GCP.md).

First get your **numeric GitHub id** (Cloud Shell): `curl -s https://api.github.com/users/Sanjay-Naidu | jq .id`

**Console:** **IAM & Admin → Workload Identity Federation → Create pool** (or *Get started*)

1. Pool name `github-pool`, display name "GitHub Actions". **Continue**.
2. Provider: **OpenID Connect (OIDC)**
   - Provider name `github-oidc`
   - Issuer URL `https://token.actions.githubusercontent.com`
   - Audiences: **Default audience**. **Continue**.
3. **Provider attributes** (mapping):

   | Google | OIDC |
   |---|---|
   | `google.subject` | `assertion.sub` |
   | `attribute.repository` | `assertion.repository` |
   | `attribute.repository_owner_id` | `assertion.repository_owner_id` |
   | `attribute.ref` | `assertion.ref` |

4. **Attribute conditions → Add condition**:
   ```
   assertion.repository == 'Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway' && assertion.repository_owner_id == '<your numeric id>'
   ```
   **Save**.
5. On the pool page click **Grant access** → **Grant access using service account impersonation**:
   - Service account `github-deployer`
   - Principals: **Only identities matching the filter** → attribute name `repository`, value `Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway`
   - **Save**. A *Configure your application* dialog appears; dismiss it. The workflow doesn't need a config file.
6. Open the provider and copy its **resource name**. It looks like `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github-pool/providers/github-oidc`. That's `GCP_WIF_PROVIDER` in Phase 12.

<details><summary>CLI equivalent</summary>

```bash
P=$(gcloud config get-value project); N=$(gcloud projects describe $P --format='value(projectNumber)')
REPO=Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway
OWNER_ID=$(curl -s https://api.github.com/users/Sanjay-Naidu | jq -r .id)
gcloud iam workload-identity-pools create github-pool --location global --display-name "GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc github-oidc --location global \
  --workload-identity-pool github-pool --issuer-uri https://token.actions.githubusercontent.com \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref" \
  --attribute-condition "assertion.repository == '$REPO' && assertion.repository_owner_id == '$OWNER_ID'"
gcloud iam service-accounts add-iam-policy-binding github-deployer@$P.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$N/locations/global/workloadIdentityPools/github-pool/attribute.repository/$REPO"
```
</details>

---

## Phase 11 — Platform layer inside the cluster (Cloud Shell)

**Why:** some Kubernetes objects are *platform* concerns: namespaces with security guardrails, the storage class, and the shared Gateway (the load balancer). You apply them once, as the cluster admin. The pipeline only deploys *into* the namespaces, which mirrors how platform and app teams split ownership.

```bash
git clone https://github.com/Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway.git
cd project6-python-cicd-trivy-gke-gateway
gcloud container clusters get-credentials medicart-gke --zone us-central1-a --dns-endpoint

kubectl apply -f platform/00-namespaces.yaml     # medicart-dev, medicart-prod, gateway-infra (+ Pod Security 'restricted')
kubectl apply -f platform/10-storage.yaml        # StorageClass medicart-pd (pd-balanced, Retain) + VolumeSnapshotClass
kubectl apply -f platform/20-gateway-http.yaml   # the shared Gateway -> Google builds the load balancer
```

> Apply **only** `20-gateway-http.yaml` now, not the whole `platform/` folder. Files 21 and 22 are the HTTPS stages (Phase 13).

Watch the Gateway come up (1–3 minutes):

```bash
kubectl -n gateway-infra get gateway medicart-gateway -w
# NAME               CLASS                            ADDRESS       PROGRAMMED
# medicart-gateway   gke-l7-global-external-managed   34.49.10.20   True
```

`ADDRESS` must be your `medicart-ip`. In the console, **Network services → Load balancing** now shows a load balancer named `gkegw1-...`. You never created it by hand: the Gateway controller did.

**Now run the verifier:**

```bash
bash scripts/verify-platform.sh
```

Every line should be **PASS**. Each FAIL names the phase to revisit.

---

## Phase 12 — Connect GitHub and deploy

### 12.1 Repository variables

**Settings → Secrets and variables → Actions → Variables tab → New repository variable**:

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | your project ID |
| `GCP_REGION` | `us-central1` |
| `GAR_REPOSITORY` | `medicart` |
| `GAR_DOCKERHUB_REMOTE` | `dockerhub` |
| `GKE_CLUSTER` | `medicart-gke` |
| `GKE_LOCATION` | `us-central1-a` |
| `GCP_WIF_PROVIDER` | the provider resource name from Phase 10 step 6 |
| `GCP_DEPLOY_SA` | `github-deployer@<PROJECT_ID>.iam.gserviceaccount.com` |

These are **Variables, not Secrets**: none of them is sensitive. With OIDC there's no password or key. A provider name and an SA email are useless without a token that GitHub signs for *this* repository.

### 12.2 Environments (with their own variables)

**Settings → Environments → New environment**, twice:

| Environment | Protection rules | Environment variables |
|---|---|---|
| `dev` | none, deploys automatically | `APP_HOSTNAME` = `dev.34-49-10-20.sslip.io`, `PUBLIC_SCHEME` = `http` |
| `prod` | **Required reviewers: yourself** (untick *Prevent self-review* if shown) | `APP_HOSTNAME` = `34-49-10-20.sslip.io`, `PUBLIC_SCHEME` = `http` |

The same variable name holds a different value per environment. The workflow reads `vars.APP_HOSTNAME`, and GitHub supplies the value of whichever environment the job runs in.

### 12.3 Branch protection (recommended)

**Settings → Branches → Add rule** for `main`: require a pull request, and require the *PR Validation* checks (they appear after the first PR). The PR pipeline then becomes an actual gate.

### 12.4 First deployment

**Actions → Build and Deploy → Run workflow → main**.

What happens, and roughly how long each part takes on the first run:

1. **Code quality**: ruff, pytest against a real PostgreSQL, Kustomize + kubeconform (~2 min)
2. **Build, scan, push**: image build, smoke test, Trivy (blocking), SBOM, push (~3 min)
3. **Deploy to DEV** (~8–12 min the first time):
   - server-side dry run, then apply
   - the StorageClass provisions a **new persistent disk** for `postgres-0`, and PostgreSQL runs `initdb`
   - the app's `migrate` init container waits for the database, then creates the schema and seeds the catalog
   - the Gateway gets its first backend. Google programs the load balancer and health checks, and the pipeline retries `http://dev.<ip>.sslip.io/api/info` until it returns the new version.
4. **Waiting for approval**: click **Review deployments → prod → Approve**
5. **Deploy to PROD**: same steps, 2 app replicas + HPA + PDB, a 10 GiB disk

Watch it from Cloud Shell while it runs:

```bash
kubectl -n medicart-dev get pods,pvc -w
kubectl -n medicart-dev logs -f deploy/medicart -c migrate     # the migration run
```

When it's green, open `http://34-49-10-20.sslip.io/` (prod) and `http://dev.34-49-10-20.sslip.io/` (dev). The footer shows `version sha-xxxxxxx`, the environment and the pod that served the page.

### 12.5 Create a pharmacist account

Patients self-register on the site. Pharmacists are created by an operator, from inside the cluster:

```bash
kubectl -n medicart-prod exec -it deploy/medicart -c medicart -- \
  python -m medicart.cli create-user --role pharmacist \
    --email pharmacist@medicart.demo --name "Priya Sharma"
# prompts for the password twice (never echoed, never in shell history)
```

Now try the full flow: register as a patient → add Amoxicillin (Rx) + Paracetamol → checkout with a prescriber and Rx number → log in as the pharmacist → **Pharmacist** → approve → see it in the **Audit trail**.

---

## Phase 13 — HTTPS (Google-managed certificate)

**Why:** a login form over plain HTTP sends passwords in clear text. Google issues and auto-renews the certificate for free.

### 13.1 Create the certificate

**Console:** **Network services → Load balancing → Load balancing components** (link at the top) → **Certificates** tab → **Create SSL certificate**
- Name `medicart-cert`, **Create Google-managed certificate**
- Domains: `34-49-10-20.sslip.io` and `dev.34-49-10-20.sslip.io` (your IP)
- **Create**. Status shows *PROVISIONING*.

<details><summary>CLI equivalent</summary>

```bash
gcloud compute ssl-certificates create medicart-cert --global \
  --domains=34-49-10-20.sslip.io,dev.34-49-10-20.sslip.io
```
</details>

### 13.2 Stage 2: serve HTTPS next to HTTP

```bash
kubectl apply -f platform/21-gateway-https.yaml
```

Google can only issue the certificate once it's attached to a load balancer that the domains resolve to. That's what this step does. Issuance takes **15–60 minutes**, and HTTP keeps serving the site the whole time. Poll:

```bash
gcloud compute ssl-certificates describe medicart-cert --global \
  --format='value(managed.status, managed.domainStatus)'
# PROVISIONING ... -> ACTIVE  {'34-49-10-20.sslip.io': 'ACTIVE', 'dev.34-...': 'ACTIVE'}
```

### 13.3 Stage 3: HTTPS only (after ACTIVE)

```bash
kubectl apply -f platform/22-gateway-https-redirect.yaml
curl -sI http://34-49-10-20.sslip.io/ | head -3     # 301 -> https://...
```

Then in GitHub set **`PUBLIC_SCHEME` = `https`** in both environments and re-run **Build and Deploy**. The pods restart with `Secure` cookies and an HSTS header, and the pipeline's public check now runs over HTTPS.

> If the certificate goes to `FAILED_RATE_LIMITED` or `FAILED_CAA`: shared DNS services like sslip.io sometimes hit the CA's per-domain limits. Delete the cert and try `nip.io` hostnames (`34-49-10-20.nip.io`, same idea, different registered domain), or use a real domain. Update `APP_HOSTNAME` in both environments to match.

---

## Phase 14 — Day-2 operations

Everything you'd do *after* go-live is in **[OPERATIONS.md](OPERATIONS.md)**: pausing the cluster, logs, database access, the **backup/restore drill with disk snapshots**, rollbacks, testing Cloud Armor, and rotating secrets.

---

## Phase 15 — Teardown (in this order)

**The order matters.** The load balancer was created by the Gateway controller *inside* the cluster. Delete the cluster first and the controller is gone before it can clean up: the LB keeps billing and its leftovers block the VPC delete. And the database disks use `reclaimPolicy: Retain`, so they survive the cluster **by design** and need their own step.

1. **Delete the Gateway** (the controller deletes the load balancer):
   ```bash
   kubectl delete -f platform/20-gateway-http.yaml --ignore-not-found   # (or 21/22 if you applied those: same object)
   kubectl -n gateway-infra get gateway                                  # wait until gone
   ```
   Check **Network services → Load balancing** until it's empty (a few minutes).
2. **Delete the app namespaces**: `kubectl delete namespace medicart-dev medicart-prod`. PVCs go with them, **disks stay**.
3. **Kubernetes Engine → Clusters → `medicart-gke` → Delete** (~5 min).
4. **Compute Engine → Disks**: delete the two `pvc-...` disks (the database volumes). **Compute Engine → Snapshots**: delete any snapshot from the drill.
5. **VPC network → IP addresses**: release `medicart-ip`.
6. **Load balancing components → Certificates**: delete `medicart-cert`.
7. **Cloud Armor policies**: delete `medicart-armor`.
8. **Artifact Registry**: delete `medicart` and `dockerhub`.
9. **Secret Manager**: delete the four secrets.
10. **Workload Identity Federation**: delete `github-pool` (it's soft-deleted for 30 days; re-creating with the same name inside that window needs an *undelete*).
11. **Service Accounts**: delete `github-deployer` and `gke-nodes`.
12. **VPC networks**: delete `medicart-vpc` (this removes its subnet and any remaining firewall rules).

Then confirm nothing is billing:

```bash
bash scripts/find-leftovers.sh
```

In GitHub, delete the `GCP_PROJECT_ID` variable. The pipeline notices, goes back to build + scan only, and stays green.

**The nuclear option:** `gcloud projects delete <PROJECT_ID>` removes everything at once. It's worth doing the ordered teardown at least once though: the ordering problem *is* the interview question.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `auth`: *attribute condition* / *unable to exchange token* | The token's `repository` doesn't match the provider condition (case-sensitive), or the owner id is wrong. Phase 10 step 4. |
| `auth`: *Permission 'iam.serviceAccounts.getAccessToken' denied* | Missing *Grant access* (Phase 10 step 5), or still propagating. Wait 2–5 minutes. |
| Deploy: `kubernetes cluster unreachable ... i/o timeout` | The DNS endpoint is off. Cluster → Networking → *Access using DNS* on, or `gcloud container clusters update medicart-gke --zone us-central1-a --enable-dns-access`. |
| Deploy: *Namespace medicart-dev not found* | Phase 11 not done. The pipeline deliberately doesn't create namespaces. |
| Server-side dry run: *no matches for kind "SecretProviderClass"* | The Secret Manager add-on is off. Cluster → Security → *Enable Secret Manager*. |
| Dry run: *no matches for kind "HTTPRoute"/"HealthCheckPolicy"* | Gateway API is off. Cluster → Networking → *Enable Gateway API*. |
| Dry run: *violates PodSecurity "restricted"* | Working as intended: a manifest lost part of its security context. The message names the field. |
| Pods stuck `ContainerCreating`, event *failed to mount secrets ... PermissionDenied* | Phase 8.2: the namespace principal lacks *Secret Accessor* on that secret, or there's a typo in the project number or namespace. |
| `postgres-0` `ImagePullBackOff` | `gke-nodes` lacks *Reader* on the `dockerhub` repo (Phase 5), or the `GAR_DOCKERHUB_REMOTE` variable is wrong. |
| `postgres-0` `Pending`, PVC `Pending` | Normal for ~30 s (WaitForFirstConsumer). Longer: `kubectl describe pvc -n medicart-dev` → quota (`SSD_TOTAL_GB`) or a missing StorageClass (Phase 11). |
| App pods stuck at `Init:0/1` | The migration is waiting for PostgreSQL: `kubectl -n medicart-dev logs deploy/medicart -c migrate`. If postgres is fine, check the `medicart-egress-to-postgres` NetworkPolicy. |
| Public URL: `404` from Google (`fault filter abort`) | The HTTPRoute's hostname doesn't match the URL (check `APP_HOSTNAME`), or the route isn't attached: `kubectl -n medicart-dev describe httproute medicart`. |
| Public URL: `502`/`no healthy upstream` for >15 min | The LB health check fails. Check the `medicart-ingress` NetworkPolicy allows `35.191.0.0/16` and `130.211.0.0/22`, and that `/readyz` answers inside the cluster. |
| Public URL: `403` on a normal form post | A Cloud Armor WAF false positive. Logs Explorer → `resource.type="http_load_balancer"` → `jsonPayload.enforcedSecurityPolicy` shows the rule. |
| Cluster create: *Quota 'CPUS' / 'SSD_TOTAL_GB' exceeded* | Free-trial limits. Keep max 3 nodes and pd-standard boot disks. |
| Trivy fails the build | That's it working. Bump the package or base image it names (Dependabot usually has a PR). If there's truly no fix, document it in `.trivyignore` with a reason and a re-check date. |

---

*Setup guide by Sanjay Naidu — Project 6, September 2026.*
