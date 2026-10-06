# Project 6 — MediCart: a Python online pharmacy on GKE (Gateway API, PostgreSQL on persistent disk)

**Author: Sanjay Naidu** · Platform Engineering portfolio project

**MediCart** is an online pharmacy: a medicine catalog, cart and checkout, **prescription-only items that a pharmacist must approve**, and an append-only audit trail. It's written in Python (FastAPI) with a PostgreSQL database, and I ship it through a secured delivery pipeline to Google Kubernetes Engine.

Project 5 ran a stateless Java shop with Helm and a platform built by one script. This project deliberately changes three things:

1. **A real database on Kubernetes.** PostgreSQL runs as a StatefulSet on a persistent disk, with migrations, backups (disk snapshots) and a tested restore drill.
2. **Production-style security in layers**: Cloud Armor WAF at the edge, Gateway API, Pod Security *restricted*, default-deny NetworkPolicies, secrets from Secret Manager through Workload Identity, and nodes with no internet access at all.
3. **Built by hand, then verified.** Every cloud resource was created in the Google Cloud console following my own [step-by-step guide](docs/SETUP.md), and a read-only script checks the result. No Helm: plain YAML with Kustomize.

```
Python 3.13 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 18 · uv · pytest · Ruff
Docker (distroless, non-root) · Trivy · SBOM · GitHub Actions (OIDC / Workload Identity Federation)
GKE · Gateway API (global external ALB) · Cloud Armor · Secret Manager CSI · Persistent Disk CSI + VolumeSnapshots
Kustomize · NetworkPolicy (Dataplane V2) · Pod Security Admission · HPA · PDB · Artifact Registry (+ Docker Hub mirror)
```

![Build and Deploy](https://github.com/Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway/actions/workflows/build-and-deploy.yml/badge.svg)
![PR Validation](https://github.com/Sanjay-Naidu/project6-python-cicd-trivy-gke-gateway/actions/workflows/pr-validation.yml/badge.svg)

---

## Architecture

```mermaid
flowchart LR
    User((Patient / Pharmacist)) -->|"34-x-x-x.sslip.io<br/>dev.34-x-x-x.sslip.io"| ARMOR

    subgraph edge [Google edge]
        ARMOR[Cloud Armor<br/>SQLi / XSS WAF<br/>login rate-ban] --> LB[Global external ALB<br/>static IP + managed cert]
    end

    subgraph gke [GKE: private nodes, Dataplane V2, no internet egress]
        GW[Gateway<br/>ns: gateway-infra]
        subgraph prod [ns: medicart-prod - PSA restricted, default-deny]
            APP1[medicart pods x2-4<br/>HPA + PDB] -->|5432| PG1[(postgres-0<br/>StatefulSet)]
            PG1 --- PD1[(Persistent Disk<br/>10 GiB pd-balanced<br/>Retain)]
        end
        subgraph dev [ns: medicart-dev]
            APP2[medicart pod] -->|5432| PG2[(postgres-0)]
            PG2 --- PD2[(Persistent Disk 5 GiB)]
        end
        GW -->|HTTPRoute host| APP1
        GW -->|HTTPRoute dev.host| APP2
    end

    LB -->|container-native NEG| GW
    SM[Secret Manager] -.->|CSI mount, Workload Identity| APP1
    SM -.-> PG1
    AR[(Artifact Registry<br/>medicart + Docker Hub mirror)] -.->|Private Google Access| gke
```

**Pipeline on every merge to `main`:**

```mermaid
flowchart LR
    A[Merge to main] --> B[Ruff + pytest on real PostgreSQL 18<br/>+ Kustomize render + kubeconform]
    B --> C[Docker build + smoke test]
    C --> D[Trivy scan]
    D -->|HIGH/CRITICAL fixable| X[Fail - never pushed]
    D -->|clean| E[SBOM + push sha-tag<br/>to Artifact Registry]
    E --> F[DEV: dry-run, apply, rollout<br/>check public URL]
    F --> G{{Manual approval}}
    G --> H[PROD: same image, same steps]
```

---

## What the app does

| | |
|---|---|
| **Patients** | browse and search the catalog, cart, checkout, order history |
| **Prescription items** | checkout asks for the prescriber and Rx number, and the order waits in `pending_review` |
| **Pharmacists** | review queue: approve, or reject with a reason (stock goes back automatically), plus the audit trail |
| **Audit trail** | every login (success and failure), registration, order and decision, with the real client IP. **UPDATE/DELETE are blocked by a database trigger** |
| **API** | `/api/products`, `/api/info` (version, environment, pod). The pipeline uses the latter to verify deploys |

A demo only: no real medicines, payments or patient data.

---

## Why each decision (the part interviewers ask about)

### Application

| Decision | Why |
|---|---|
| **FastAPI, server-rendered Jinja pages, no JS bundle** | One deployable. Plain HTML forms also let the CSP forbid inline scripts entirely. |
| **Stock locked with `SELECT … FOR UPDATE`, rows locked in id order** | Several replicas share one database. "Check then decrement" in Python would oversell the last box. A test races two threads for one item and asserts exactly one wins. Ordered locking prevents deadlocks. |
| **Money as integer cents; the server re-prices every order** | Floats can't represent 0.10 exactly. The browser only sends product ids and quantities. |
| **Rules as database constraints** | `stock >= 0`, `quantity > 0`, a known `status`: a bug in any replica, or a manual SQL fix, still can't break them. |
| **Append-only audit via trigger, written in the same transaction** | The trail can never claim something that didn't happen, or miss something that did. The trigger holds even for someone with `psql`. |
| **Signed-cookie sessions, pods stateless** | No session store. The HPA can add or kill any pod. The signing key comes from Secret Manager. |
| **scrypt (stdlib) password hashing, constant-time compare, dummy hash for unknown users** | Memory-hard, no extra dependency, and login timing doesn't reveal which emails exist. |
| **CSRF tokens + SameSite=Lax, local-only `next=` redirects** | Two independent CSRF layers, and no open redirect. |
| **Real client IP = second-to-last `X-Forwarded-For` entry** | Google's LB *appends* `client,lb`. The first entry is whatever the client sent, and trivially forged. |
| **`/livez` and `/readyz` don't touch the database** | If PostgreSQL is down, restarting app pods fixes nothing, and marking *all* pods unready would replace our friendly 503 page with the LB's bare 502. |
| **3 s DB connect timeout** | libpq waits forever by default. Found by a test that hung for 2 minutes (see "What broke"). |
| **Migrations in an init container behind `pg_advisory_lock`** | New code can't start against an old schema, and concurrent pods queue instead of racing Alembic. The price is that migrations must be backward compatible (expand/contract). |
| **Metrics on port 9090, not exposed by the Service** | `/metrics` is reachable in-cluster only. A test proves the public port returns 404. |
| **One uvicorn worker per pod** | Kubernetes is the process manager: more capacity = more pods, each with its own probes, limits and metrics. |

### Container

| Decision | Why |
|---|---|
| **Build with Debian 13's own Python, run on `distroless/python3-debian13:nonroot`** | The same interpreter at build and run time, so the venv's symlinks and compiled wheels match. No shell, no package manager, UID 65532. |
| **`uv sync --frozen` from a hash-locked `uv.lock`** | CI and the image install exactly what was reviewed, and fail if the lock is stale. |
| **Base images pinned `tag@sha256`** | Tags are mutable. Dependabot bumps tag and digest together. |
| **Allow-list `.dockerignore`** | Only `app/src` and the lock file enter the build context, so a stray `.env` can't reach an image. |

### Infrastructure (built in the console, see [SETUP.md](docs/SETUP.md))

| Decision | Why |
|---|---|
| **Console by hand + [`verify-platform.sh`](scripts/verify-platform.sh)** | I wanted to meet every GCP setting once, not hide it in a script. The verifier turns "did I tick every box?" into PASS/FAIL. Every console step also shows its `gcloud` equivalent. |
| **Zonal Standard cluster, 2 × e2-medium, autoscaler 1–3** | The free tier covers one zonal control plane, and 3 × 2 vCPU stays under the trial's 8-vCPU cap. |
| **Private nodes, no Cloud NAT** | Nodes have no public IPs *and no internet route*. Images come from Artifact Registry over Private Google Access, and PostgreSQL through an **Artifact Registry remote repo (Docker Hub mirror)**. A compromised pod can't phone home, and there's no NAT to pay for. |
| **Dedicated node SA** | Instead of the default Compute SA (Editor on the project): node duties + read on two repos. |
| **Deployer SA: `container.developer` + writer on one repo** | CI can deploy, but can't create namespaces, change IAM or delete the cluster. |
| **DNS-based control plane endpoint** | GitHub runners have no fixed IP to allow-list, and the DNS endpoint is gated by IAM instead of IP (lesson from Project 5). |
| **Secret Manager add-on (CSI) + Workload Identity** | Secrets live only in Secret Manager: versioned, audited, IAM per secret. Pods get them as tmpfs files via their Kubernetes identity. No Kubernetes Secret, no JSON key, and the pipeline never sees a value. Dev pods can't read prod secrets. |

### Traffic: Gateway API instead of Ingress

| Decision | Why |
|---|---|
| **Gateway API (`gke-l7-global-external-managed`)** | Ingress's successor, and the Project 5 roadmap item. It splits ownership: the platform owns the **Gateway** (LB, IP, certificates, who may attach), the app owns its **HTTPRoute**. |
| **One shared Gateway, host-based routing** | `dev.<ip>.sslip.io` → dev, `<ip>.sslip.io` → prod through **one** load balancer. With Ingress that was one LB (~$18/month) per environment. |
| **`allowedRoutes` by namespace label** | Only namespaces labelled `gateway-access=true` can publish on the Gateway, so a new namespace can't go public by accident. |
| **HTTPS in three stages** | A Google-managed certificate is issued only after it's attached to the LB (15–60 min). Stage 2 serves HTTP *and* HTTPS; stage 3 redirects HTTP only once the cert is ACTIVE, so there's no downtime. The app's routes never change (no `sectionName`; the listener's `allowedRoutes` decides). |
| **`HealthCheckPolicy` → `/readyz`, `GCPBackendPolicy` → Cloud Armor, draining, logging** | The LB and Kubernetes agree on readiness. The WAF and login rate-ban act at Google's edge, and blocked requests are visible in Logs Explorer with the rule that matched. |
| **sslip.io hostnames** | Real DNS names (needed for host routing and certificates) without buying a domain. |

### Data: PostgreSQL on a persistent volume

| Decision | Why |
|---|---|
| **StatefulSet + `volumeClaimTemplates`** | A stable identity (`postgres-0`) that always gets *its* disk back, wherever it's rescheduled. |
| **StorageClass: `pd-balanced`, `Retain`, `WaitForFirstConsumer`, expandable** | SSD-backed for a database. Deleting a PVC or namespace never deletes data. The disk is created in the zone where the pod lands, and it grows online by editing the PVC. |
| **`persistentVolumeClaimRetentionPolicy: Retain`** | Deleting or scaling the StatefulSet keeps the claim too. |
| **Non-root (UID 70), read-only root FS, `fsGroup` + `OnRootMismatch`** | Passes Pod Security *restricted*. The disk is chowned only when needed, not on every restart. |
| **`preStop: pg_ctl stop -m fast`** | Plain SIGTERM means "smart" shutdown, which waits for every client. The app's pool never disconnects, so the pod would be SIGKILLed mid-write. |
| **`safe-to-evict: false` on the DB, no PDB for it** | The autoscaler won't move the DB to save a few cents. A one-replica PDB would block every node drain, and it doesn't make one replica highly available anyway. |
| **`max_connections=60` ↔ HPA max 4** | 4 pods × 10 pooled connections = 40, plus headroom. Scaling the web tier past what the DB accepts only moves the failure downstream. |
| **VolumeSnapshots + a written, tested restore drill** | [OPERATIONS.md §4](docs/OPERATIONS.md): snapshot → simulate data loss → restore from snapshot → verify. |

**Scope, honestly:** one PostgreSQL replica with no automatic failover. If a node dies, the pod reschedules and re-attaches the disk (a minute or two of downtime) with no data loss. Real HA is the first roadmap item.

### Kubernetes security, layer by layer

1. **Pod Security Admission `restricted`** on the namespaces: the API server rejects root, privilege escalation, added capabilities and missing seccomp.
2. **Default-deny NetworkPolicies**: only LB → app:8080, app → postgres:5432, DNS, and Prometheus → app:9090. No other traffic in or out.
3. **Every container**: non-root, read-only root FS, `drop: [ALL]`, seccomp `RuntimeDefault`, no ServiceAccount token mounted.
4. **Platform/app split**: namespaces, StorageClass and the Gateway are applied by an admin (`platform/`). The pipeline only deploys *into* namespaces.

### CI/CD

| Decision | Why |
|---|---|
| **Workload Identity Federation, condition on repo + numeric owner id** | No keys anywhere. See [GITHUB-OIDC-TO-GCP.md](docs/GITHUB-OIDC-TO-GCP.md), which also covers the second keyless flow (pod → Secret Manager). |
| **Tests against a real PostgreSQL 18 service container** | Row locks, CHECK constraints, JSONB and the audit trigger only behave like production on the real engine. The test run also executes the real migrations. |
| **Kustomize, not Helm** | Plain YAML, patches per environment, built into `kubectl`. Deploy-time values go through [`k8s/render.sh`](k8s/render.sh), with an explicit variable allow-list and a failure on any leftover `${…}`. **CI validates exactly what the deploy applies.** |
| **kubeconform with the CRD catalog** | HTTPRoute, HealthCheckPolicy and SecretProviderClass are schema-checked too. It caught a real bug before any cluster existed (see "What broke"). |
| **Trivy before push; the Postgres image scanned report-only** | A vulnerable image never reaches the registry. We don't build Postgres, but we should still know what's in it. |
| **Server-side dry run, then server-side apply with `--force-conflicts`** | The API server validates everything (PSA, CRDs, immutable fields) before anything changes, and git wins over hand edits. |
| **Rollout watch with automatic `rollout undo`** | A failed release doesn't leave an environment half-broken. |
| **Verify through the public URL** | Passes only when `https://host/api/info` returns the tag just deployed: DNS, LB, Cloud Armor, route and pod all proven together. |
| **Composite action for deploy** | Dev and prod run the *same* steps, so a fix can't land in one and be forgotten in the other. |
| **Environment-scoped variables** | `APP_HOSTNAME` / `PUBLIC_SCHEME` hold different values in the `dev` and `prod` GitHub environments under one name. |
| **Approval gate; the same image promoted** | Prod pauses for a reviewer, and nothing is rebuilt between dev and prod. |
| **Actions pinned to SHAs, kubeconform pinned by checksum, Dependabot for uv/docker/actions** | Supply-chain hygiene: a moved tag or a tampered download fails the build. |

---

## Monthly cost (why it fits a free trial)

Approximate `us-central1` list prices, 24×7:

| Resource | Config | ~Cost/month |
|---|---|---|
| GKE control plane | 1 zonal cluster | $0 (free tier) |
| Nodes | 2 × e2-medium | ~$49 |
| Boot disks | 2 × 30 GB pd-standard | ~$2.40 |
| Database disks | 5 + 10 GiB pd-balanced | ~$1.50 |
| Load balancer | 1 forwarding rule (shared by dev + prod) | ~$18 |
| Static IP | global, in use | ~$3 |
| Cloud Armor | 1 policy + 3 rules | ~$8 |
| Secret Manager | 4 secrets | $0 (free tier) |
| Artifact Registry | < 0.5 GB | $0 |
| Cloud NAT | **not needed** (Docker Hub mirror) | $0 |
| **Total** | | **≈ $82/month ≈ $2.75/day** |

`bash scripts/gke-pause.sh pause` stops the nodes between sessions (≈ $1.10/day while paused), and the database survives on its disks. A free-trial account is never charged.

---

## Repository layout

```
├── app/                          # Python service (uv project)
│   ├── src/medicart/             #   web, pharmacy, api, orders, users, audit, migrate, cli
│   │   ├── migrations/           #   Alembic: schema + audit trigger, catalog seed
│   │   └── templates/, static/   #   Jinja pages, CSS (no inline JS/CSS: strict CSP)
│   └── tests/                    #   49 tests against real PostgreSQL, 97% coverage
├── Dockerfile                    # Debian build stage -> distroless nonroot runtime
├── platform/                     # applied ONCE by an admin: namespaces+PSA, storage, Gateway (HTTP -> HTTPS stages)
├── k8s/
│   ├── base/                     # app Deployment, postgres StatefulSet, HTTPRoute, policies, NetworkPolicies
│   ├── overlays/{dev,prod}/      # per-environment patches (prod: HPA, PDB, spread, 10Gi)
│   └── render.sh                 # kustomize + safe variable substitution (CI and deploy share it)
├── .github/
│   ├── workflows/                # pr-validation, build-and-deploy, reusable-quality
│   └── actions/gke-deploy/       # composite deploy: dry-run, apply, rollout/undo, public check
├── scripts/                      # verify-platform (read-only), gke-pause, find-leftovers
└── docs/
    ├── SETUP.md                  # console, step by step: empty project -> HTTPS site -> teardown
    ├── OPERATIONS.md             # runbook: logs, psql, snapshot restore drill, Cloud Armor tests, rotation
    └── GITHUB-OIDC-TO-GCP.md     # the two keyless identity flows, explained
```

---

## Getting started

Follow **[docs/SETUP.md](docs/SETUP.md)**. Every Google Cloud resource is created by hand in the console, with the reasoning and the `gcloud` equivalent beside each step, then checked by `scripts/verify-platform.sh`. Nothing needs to be installed locally: builds run on GitHub, and cluster commands run in Cloud Shell.

Run the tests locally (optional; needs [uv](https://docs.astral.sh/uv/) and any PostgreSQL 16+):

```bash
cd app
uv sync
export TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/medicart_test
uv run pytest            # 49 tests + 85% coverage gate
uv run ruff check . && uv run ruff format --check .
```

Run the app locally against that database:

```bash
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/medicart
uv run python -m medicart.migrate && uv run python -m medicart     # http://localhost:8080
```

---

## What broke on the way (and what it taught me)

**A YAML boolean that would have failed the first deploy.** `kubeconform` rejected the rendered Deployment before any cluster existed: `env[2].value: got boolean, want string`. The manifest said `value: "${COOKIE_SECURE}"`, but Kustomize re-serialises YAML and drops quotes it thinks are unnecessary, so after substitution the line read `value: false`, a boolean, which Kubernetes refuses for env vars. I replaced the flag with `PUBLIC_SCHEME` (`http`/`https`), a string that can't be misread as a boolean, and which also tells the pipeline which URL to verify. What it taught me: validate the *rendered* output, not the templates. That's why `render.sh` is shared by CI and deploy.

**A test that hung for two minutes exposed a production bug.** The "database is down" test pointed the app at a dead port. On Linux that's refused instantly, but on my Windows laptop the connection was silently dropped, and libpq **has no connect timeout by default**. The same thing happens in a cluster when a DB node vanishes: every request thread waits until the load balancer gives up. The fix was `connect_timeout=3` on the engine and in the migration waiter. A slow test was really a missing timeout.

<!-- Add real deployment incidents here as they happen: symptom, root cause, fix, lesson. -->

---

## Production-hardening roadmap (what I'd add with a real budget)

1. **Database HA**: Cloud SQL (regional, PITR, Auth Proxy with Workload Identity), or the CloudNativePG operator with streaming replicas on a regional PD.
2. **Least-privilege DB roles**: a migration role that owns the schema, and an app role with only DML (no `TRUNCATE`/DDL, so the audit table is fully protected).
3. **Logical backups**: scheduled `pg_dump` to Cloud Storage and WAL archiving, plus scheduled VolumeSnapshots.
4. **Terraform** for everything in SETUP.md. Now that I've built it by hand, codify it.
5. **GitOps (Argo CD)**: pull-based deploys, with `kubectl apply` pruning replaced by a real sync.
6. **Binary Authorization**: only images attested by this pipeline may run.
7. **Managed Prometheus** `PodMonitoring` on port 9090, SLO dashboards and burn-rate alerts.
8. **Certificate Manager + a real domain**, pinned secret versions instead of `latest`, and separate projects per environment.

---

*Built by **Sanjay Naidu**, platform engineer. Every file carries an author header, and I can defend every decision above on a whiteboard.*

<sub>MediCart is a fictional portfolio demo. It is not a pharmacy, sells nothing, and must not be used with real patient data.</sub>
