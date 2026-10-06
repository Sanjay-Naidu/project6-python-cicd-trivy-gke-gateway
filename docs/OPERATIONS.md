# Operations Runbook — MediCart on GKE

**Author: Sanjay Naidu**

Day-2 tasks once the platform from [SETUP.md](SETUP.md) is live. Every command runs in **Cloud Shell** from the repo root, after:

```bash
gcloud container clusters get-credentials medicart-gke --zone us-central1-a --dns-endpoint
HOST=34-49-10-20.sslip.io        # your prod hostname
```

---

## 1. Pause between sessions (save ~60% of the cost)

```bash
bash scripts/gke-pause.sh pause     # nodes -> 0; the disks, LB and IP stay
bash scripts/gke-pause.sh resume    # ~3-4 min; postgres-0 re-attaches its disk
```

After a resume, every order placed before the pause is still there. That's the persistent volume doing its job, and it's worth showing in a demo.

---

## 2. Look around

```bash
kubectl -n medicart-prod get pods,svc,httproute,hpa,pdb,pvc -o wide
kubectl -n medicart-prod get networkpolicy
kubectl -n gateway-infra describe gateway medicart-gateway     # listeners, attached routes, address
kubectl get pv                                                  # the disks, with RECLAIM POLICY = Retain

# Which build is live?
curl -s http://$HOST/api/info | jq
```

**Logs.** The app writes one JSON object per line:

```bash
kubectl -n medicart-prod logs deploy/medicart -c medicart --tail=50
kubectl -n medicart-prod logs deploy/medicart -c migrate          # last migration run
kubectl -n medicart-prod logs postgres-0 --tail=50
```

The cluster was created with *System* logging only (free tier), so app logs are available through `kubectl` but not in Cloud Logging. To send them there as well: cluster → *Features* → Logging → tick *Workloads*. Cloud Logging then reads the `severity` field, so `severity>=ERROR` filters work straight away.

The **load balancer's** request logs *are* in Cloud Logging (enabled by the `GCPBackendPolicy`). In **Logs Explorer**:

```
resource.type="http_load_balancer"
jsonPayload.enforcedSecurityPolicy.name="medicart-armor"
```

**Metrics** (port 9090, never exposed publicly):

```bash
kubectl -n medicart-prod port-forward deploy/medicart 9090:9090 &
curl -s localhost:9090/metrics | grep '^medicart_'
# medicart_http_requests_total{method="GET",route="/",status="200"} 42.0
# medicart_orders_placed_total{status="pending_review"} 3.0
kill %1
```

---

## 3. Database access

```bash
kubectl -n medicart-prod exec -it postgres-0 -- psql -U medicart -d medicart
```

```sql
\dt
SELECT status, count(*), sum(total_cents)/100.0 AS revenue FROM orders GROUP BY status;
SELECT id, occurred_at, action, entity, entity_id, source_ip FROM audit_events ORDER BY id DESC LIMIT 10;

-- The audit trail is append-only, enforced by the DATABASE:
DELETE FROM audit_events WHERE id = 1;
-- ERROR:  audit_events is append-only (DELETE blocked)

-- Schema version applied by the migrate init container:
SELECT * FROM alembic_version;
```

---

## 4. Backup and restore drill (disk snapshots)

Practise this at least once. "We have backups" means nothing until a restore has been tested.

### 4.1 Take a snapshot

```bash
kubectl -n medicart-prod exec postgres-0 -- psql -U medicart -d medicart -c 'CHECKPOINT;'

cat <<'EOF' | kubectl apply -f -
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: prod-db-drill
  namespace: medicart-prod
spec:
  volumeSnapshotClassName: medicart-snapshots
  source:
    persistentVolumeClaimName: data-postgres-0
EOF

kubectl -n medicart-prod get volumesnapshot prod-db-drill -w     # wait for READYTOUSE=true
```

In the console, **Compute Engine → Snapshots** now shows a real disk snapshot, created through the Kubernetes API.

### 4.2 Simulate the incident

Place a test order in the shop, then "accidentally" wipe it:

```bash
kubectl -n medicart-prod exec postgres-0 -- psql -U medicart -d medicart \
  -c 'TRUNCATE order_items, orders RESTART IDENTITY;'
```

The orders page is now empty.

### 4.3 Restore

```bash
NS=medicart-prod
# 1. Stop writers and the database (the app shows its "database unavailable" page)
kubectl -n $NS scale deployment/medicart --replicas=0
kubectl -n $NS scale statefulset/postgres --replicas=0
kubectl -n $NS wait --for=delete pod/postgres-0 --timeout=120s

# 2. Swap the volume: delete the broken PVC (the disk is RETAINED, so nothing is lost yet)
kubectl -n $NS delete pvc data-postgres-0

# 3. A new PVC with the SAME name, created from the snapshot
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: data-postgres-0
  namespace: medicart-prod
  labels:
    app.kubernetes.io/name: postgres
spec:
  storageClassName: medicart-pd
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 10Gi
  dataSource:
    apiGroup: snapshot.storage.k8s.io
    kind: VolumeSnapshot
    name: prod-db-drill
EOF

# 4. Start again. The StatefulSet reuses the PVC named data-postgres-0.
kubectl -n $NS scale statefulset/postgres --replicas=1
kubectl -n $NS rollout status statefulset/postgres
kubectl -n $NS scale deployment/medicart --replicas=2
```

The orders are back. What to understand from it:
- The snapshot is **crash-consistent**: PostgreSQL starts like after a power cut and replays its WAL. `CHECKPOINT` beforehand shortens that replay.
- **RPO** (data you can lose) = time since the last snapshot. **RTO** (downtime) = the few minutes above.
- The old disk still exists (`kubectl get pv` shows it *Released*). Delete it in **Compute Engine → Disks** once you're happy with the restore.
- Clean up: `kubectl -n medicart-prod delete volumesnapshot prod-db-drill` (this deletes the Compute Engine snapshot too, via `deletionPolicy: Delete`).

For real production you'd schedule snapshots (a CronJob, or Backup for GKE) and add logical backups (`pg_dump` to Cloud Storage) plus WAL archiving for point-in-time recovery. See the README roadmap.

---

## 5. Roll back a bad release

The fastest option, in the cluster:

```bash
kubectl -n medicart-prod rollout history deployment/medicart
kubectl -n medicart-prod rollout undo deployment/medicart
```

The correct option, through git: `git revert <sha>` → PR → merge. The pipeline builds, scans and promotes the reverted code like any other change, and git stays the truth. (The deploy job also runs `rollout undo` automatically when a rollout fails its health checks.)

**Database caveat:** migrations only go forward. A rollback of the *code* is safe only because every migration is backward compatible with the previous release (expand/contract: add the new column first, stop using the old one in a later release, drop it in a third).

---

## 6. Prove Cloud Armor works

```bash
# SQL injection in the search box -> blocked at Google's edge (403), never reaches a pod
curl -s -o /dev/null -w '%{http_code}\n' "http://$HOST/?q=1%27%20UNION%20SELECT%20password_hash%20FROM%20users--"

# XSS attempt -> 403
curl -s -o /dev/null -w '%{http_code}\n' "http://$HOST/?q=%3Cscript%3Ealert(1)%3C/script%3E"

# Normal search still works -> 200
curl -s -o /dev/null -w '%{http_code}\n' "http://$HOST/?q=vitamin"

# Password guessing: after 10 POSTs to /login in a minute this IP is banned for 5 minutes
for i in $(seq 1 15); do curl -s -o /dev/null -w '%{http_code} ' -X POST "http://$HOST/login" -d 'email=x@y.z&password=guess'; done; echo
# 403 403 ... (app: no CSRF token) then 429 429 429 (Cloud Armor ban)
```

Then find the blocked requests in Logs Explorer (section 2). Each shows which rule matched.

---

## 7. Watch the autoscaler

```bash
kubectl -n medicart-prod get hpa medicart -w &
# from a second Cloud Shell tab: generate load
for n in 1 2 3 4; do (for i in $(seq 1 3000); do curl -s -o /dev/null "http://$HOST/?q=a"; done) & done; wait
```

The replica count climbs from 2 toward 4 as CPU passes 70%, then falls back after 5 minutes of quiet (the scale-down stabilisation window). It never goes above 4, and the reason is database connection capacity, not nodes (see `k8s/overlays/prod/hpa.yaml`).

---

## 8. Rotate secrets

**Session signing key** (logs everyone out, which is the point after a suspected leak):

1. Secret Manager → `medicart-prod-session-key` → **New version** → paste `openssl rand -hex 32`
2. `kubectl -n medicart-prod rollout restart deployment/medicart`. New pods mount the new version (`versions/latest`).

**Database password** (two sides must change together):

```bash
NEW=$(openssl rand -hex 24)
# 1. Change it inside PostgreSQL (existing app connections stay logged in)
kubectl -n medicart-prod exec -i postgres-0 -- psql -U medicart -d medicart \
  -c "ALTER USER medicart PASSWORD '$NEW';"
# 2. Store it as the new secret version
printf %s "$NEW" | gcloud secrets versions add medicart-prod-db-password --data-file=-
# 3. Restart the app so new pods pick it up
kubectl -n medicart-prod rollout restart deployment/medicart
unset NEW
```

PostgreSQL reads `POSTGRES_PASSWORD_FILE` only on its very first boot (initdb). That's why step 1 is needed, and why the old secret version must stay enabled until step 3 completes. Disable the old version afterwards.

---

## 9. A 5-minute interview demo

1. Open the shop and place an order containing an Rx item. Show the *pending review* page, and that the footer names the pod.
2. Log in as the pharmacist, approve it, open the **audit trail**. In `psql`, try to `DELETE` from it and show the trigger error.
3. Open a PR that changes something visible. Show PR Validation: ruff, pytest against real PostgreSQL, kubeconform, Trivy.
4. Merge. Show the pipeline graph: Trivy gate → SBOM → dev deploy with public-URL check → **approval gate** → prod.
5. `kubectl get pods -w` during the rollout: zero downtime (`maxUnavailable: 0`, preStop, graceful shutdown, LB draining).
6. `bash scripts/gke-pause.sh pause`, then `resume`: the orders survive. That's the persistent disk.
7. Run the Cloud Armor `curl` from section 6: the 403 happens at Google's edge.

---

*Runbook by Sanjay Naidu — Project 6.*
