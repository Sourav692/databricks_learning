# Databricks Architecture at a Glance

> **Topic 1.2 · Lakehouse & Medallion Foundations** — enterprise deep-dive,
> interview-focused. Conceptual topic, so each sub-topic pairs the **idea** with a
> grounding config/CLI snippet (bundle compute defs, the UC namespace, a CLI call)
> that shows where each piece lives.

## What it is

- Databricks runs as **two planes**:
  - **Control plane** — backend services Databricks manages in **its** account
    (web UI, job scheduler, notebooks, cluster manager, REST APIs).
  - **Compute plane** — where your data is actually processed.
- The compute plane has **two variants**:
  - **Serverless** — compute runs in a serverless compute plane in **Databricks'**
    account, spun up instantly.
  - **Classic** — compute runs in **your** cloud account (your VPC/storage).

**Analogy:** the control plane is the **restaurant's front desk + ordering system**
(takes your order, manages tables); the compute plane is the **kitchen that cooks**.
Serverless = you use Databricks' shared kitchen (no setup); classic = the kitchen is
built inside your own house (more control).

## Why it matters

- **Your data stays in your cloud storage** — Databricks orchestrates; it doesn't
  hold your tables. Compute and storage are **separate and scale independently**.
- The control/compute split is *the* security and cost story interviewers probe:
  **who runs what, and where the data sits.**

**Real-world use case:** a bank keeps all data in its own S3 and runs **classic**
compute inside its VPC for network isolation; a startup uses **serverless** to skip
cluster management and pay only while queries run.

---

## How it works — deep dive

### 1. Control plane vs compute plane — what lives where

**Mechanism:** the **control plane** (Databricks' account) hosts the web app, job
scheduler, notebooks, and APIs. The **compute plane** runs the clusters/warehouses
that process data. The control plane *issues* work; the compute plane *does* it.

**Why:** it's the security boundary — Databricks-managed orchestration is separate
from where your data is actually read/written.

**Trade-off:** the control plane is always Databricks-managed (not self-hostable);
you choose only the compute-plane variant.

```bash
# A CLI/API call hits the CONTROL plane (scheduler/APIs); the job then runs
# on the COMPUTE plane. Same split whether you click the UI or call the API.
databricks jobs run-now --job-id 620 --profile prod
```

### 2. Compute-plane variants — serverless vs classic

**Mechanism:** **serverless** compute runs in a serverless compute plane in
Databricks' account (starts in seconds, near-zero ops). **Classic** compute runs in
**your** cloud account/VPC (natural network isolation, you size it).

**Why:** lets you trade ops/speed (serverless) against network control/residency
(classic) per workload.

**Trade-off:** classic = isolation + control but minutes to start and you manage it;
serverless = instant + low-ops but runs in Databricks' account (managed isolation).

```yaml
# Serverless: a SQL warehouse with serverless enabled (no cluster to size).
resources:
  sql_warehouses:
    bi_wh: { name: bi, cluster_size: Small, enable_serverless_compute: true }
# Classic: a job cluster you size, running in your account's compute plane.
  jobs:
    etl:
      name: nightly-etl
      job_clusters:
        - job_cluster_key: main
          new_cluster: { spark_version: 15.4.x-scala2.12, node_type_id: i3.xlarge, num_workers: 4 }
```

### 3. Account → workspaces → metastore (Unity Catalog)

**Mechanism:** one **account** holds many **workspaces**. **Unity Catalog** is
**account-level** governance; each account gets **one metastore per cloud region**,
and workspaces attach to the metastore in their region.

**Why:** governance and identity are centralized once at the account level, then
shared across every workspace in the region — not re-configured per workspace.

**Trade-off:** workspaces must be in the **same region** as the metastore to attach;
data isolation is designed at the **catalog** level, not the metastore.

```sql
-- UC governs every workspace via the 3-level namespace (metastore → catalog → schema → table):
SELECT * FROM prod_catalog.sales.orders;   -- catalog.schema.table
-- One regional metastore backs all workspaces in that region; isolate by catalog.
```

### 4. Compute types — match the tool to the job

**Mechanism:** **all-purpose clusters** (interactive dev), **jobs clusters**
(ephemeral, per-run, cheaper), **SQL warehouses** (BI/SQL), and **serverless**
variants of each — all read the same UC-governed data.

**Why:** each is cost/perf-tuned for its workload — interactive vs scheduled vs BI.

**Trade-off:** running scheduled ETL on an always-on all-purpose cluster wastes money
— use an ephemeral **jobs cluster**; running BI on a cluster instead of a **SQL
warehouse** is the wrong tool.

```yaml
# Right tool per workload (all read the same governed tables):
#   all-purpose cluster → interactive dev      (attach a notebook)
#   jobs cluster        → scheduled ETL        (ephemeral, see 1.2#2 job_clusters)
#   SQL warehouse       → dashboards / BI SQL   (resources.sql_warehouses)
#   serverless          → instant, low-ops      (enable_serverless_compute: true)
```

### 5. How a query/job flows through the planes

**Mechanism:** you submit work via the **control plane** (UI/API/schedule) → it
provisions/uses **compute-plane** clusters/warehouses → those read & write your
**data in cloud object storage**, governed by UC → results return to the control
plane UI.

**Why:** understanding the path explains *where data moves* (it stays in your storage)
and *who runs the compute* — the core of any security review.

**Trade-off:** more moving parts than a single-box DB, but each scales independently.

```text
You ──(control plane: UI/API/schedule)──▶ provision compute
        compute plane (classic VPC or serverless) ──reads/writes──▶ your cloud storage
        UC governs access at every step ──▶ results back to the control-plane UI
```

---

## Classic vs Serverless compute

| | Classic | Serverless |
|---|---|---|
| Compute runs in | **Your** cloud account (VPC) | **Databricks'** account |
| Startup time | Minutes (cluster spins up) | **Seconds** |
| Network isolation | In your VPC | Managed, multi-layer isolation |
| Ops overhead | You size/manage clusters | **Near zero** |
| Best for | Strict network control, custom VPC | Fast, bursty, low-ops workloads |

## Uses, edge cases & limitations

- **Uses:** choosing the right compute model; explaining where data lives for a
  security review; reasoning about cost (idle clusters vs pay-per-use serverless).
- **Edge cases:** strict data-residency / no-egress requirements often push to
  **classic** compute in your own VPC; serverless availability varies by region and
  feature.
- **Limitations:** the **control plane** is always Databricks-managed — you can't
  self-host it. Don't delete workspace storage: it's tied to control-plane state and
  is unrecoverable.

## Common gotchas

- ❌ Thinking Databricks "stores your data." It **orchestrates**; your tables live in
  **your** cloud storage (or serverless managed storage), governed by UC.
- ❌ Confusing **workspace storage** (notebooks/logs) with **data storage** (UC
  tables/Volumes) — different things.
- ❌ Assuming serverless = classic with less setup. The compute runs in a **different
  account** with different isolation and networking.
- ❌ Expecting a workspace to attach to a metastore in **another region** — it must
  match the regional metastore.

## References

- [High-level architecture — Databricks docs](https://docs.databricks.com/aws/en/getting-started/high-level-architecture)
- [Serverless compute](https://docs.databricks.com/aws/en/compute/serverless/)
- [Unity Catalog (account-level metastore)](https://docs.databricks.com/aws/en/data-governance/unity-catalog/)
- [Compute types](https://docs.databricks.com/aws/en/compute/)
