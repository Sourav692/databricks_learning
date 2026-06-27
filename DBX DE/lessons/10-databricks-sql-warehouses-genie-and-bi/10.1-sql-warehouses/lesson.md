# SQL Warehouses

> **Topic 10.1 · Databricks SQL — Warehouses, Genie & BI** — enterprise deep-dive,
> interview-focused. Warehouses are UI-configured, so each section pairs the
> **mechanism** with **config-as-code** (Asset Bundle / CLI) where it applies.

## What it is

- A **SQL Warehouse** is a **compute resource optimized for SQL & BI** — it runs
  Databricks SQL queries and powers dashboards/BI tools.
- **Three types:**
  - **Serverless** (recommended) — instant start, elastic, Databricks-managed.
  - **Pro** — more control, runs in your account.
  - **Classic** — legacy, manual provisioning.
- **T-shirt sizing** (2X-Small → 4X-Large) sets per-cluster power; **autoscaling**
  (`min/max_num_clusters`) handles concurrency; **auto-stop** shuts it down when idle.
- Runs the **Photon** vectorized engine; serverless adds **Intelligent Workload
  Management (IWM)** for query routing.

**Analogy:** a SQL Warehouse is the **espresso machine for the analytics café** —
sized for the rush (sizing), spins up more machines when the queue grows
(autoscaling), and switches off when the café's empty (auto-stop). The all-purpose
cluster is the full kitchen; the warehouse is purpose-built for one fast job.

## Why it matters

- BI/SQL workloads have **bursty, concurrent** demand — warehouses scale and
  auto-stop to balance **speed vs cost** far better than a manually-run cluster.
- "What compute powers a dashboard / SQL query?" → **SQL Warehouse** (serverless),
  not an all-purpose cluster — a common interview clarification.

**Real-world use case:** a BI dashboard hits a **serverless** SQL Warehouse — it
auto-starts on the first query, **autoscales** out during the 9am rush, and
**auto-stops** after hours so you don't pay for idle compute.

---

## How it works — deep dive

### 1. Two knobs: size (power) vs clusters (concurrency)

- **Size** (`cluster_size`: 2X-Small…4X-Large) = power for a **single** query — bump
  it when one query is slow.
- **`max_num_clusters`** = how many concurrent queries before queueing — bump it
  when queries **queue under load** (many users). Each added cluster is the same size.
- **`auto_stop_mins`** = idle timeout → shuts down to save cost (at the price of a
  cold start next time; serverless cold starts are seconds).

### 2. Warehouse types

| | Serverless | Pro | Classic |
|---|---|---|---|
| Startup | **seconds** | slower | slower |
| Managed by | Databricks (their account) | you (your account) | you (legacy) |
| Ops overhead | **lowest** | medium | highest |
| Use when | default for SQL/BI | need in-account control | legacy only |

### 3. Configure in the UI

**SQL → SQL Warehouses → Create** → set name, **size**, **scaling** (min/max
clusters), **auto-stop**, and **type** (toggle **Serverless**). The warehouse
**auto-starts** on a query/job/JDBC connection/dashboard.

### 4. …or as code (Asset Bundle + CLI)

```yaml
# databricks.yml (resources) — verified fields
resources:
  sql_warehouses:
    bi_wh:
      name: bi_serverless
      cluster_size: "Small"            # per-query power (2X-Small…4X-Large)
      min_num_clusters: 1
      max_num_clusters: 4              # concurrency (autoscale up to N; max 40)
      auto_stop_mins: 10              # idle shutdown
      warehouse_type: PRO
      enable_serverless_compute: true  # → Serverless
```

```bash
databricks bundle deploy -t prod
# or imperatively:
databricks warehouses create --json '{"name":"bi_serverless","cluster_size":"Small","enable_serverless_compute":true,"auto_stop_mins":10}'
```

### 5. Point workloads at a warehouse

```yaml
# a Lakeflow Jobs sql_task runs on a warehouse by id (7.1)
sql_task: { warehouse_id: "${resources.sql_warehouses.bi_wh.id}", file: { path: ../sql/report.sql } }
```

- BI tools (Power BI/Tableau) connect via the warehouse's **JDBC/ODBC** endpoint;
  Genie (10.4) and dashboards (10.3) also run on a warehouse.

---

## Uses, edge cases & limitations

- **Uses:** dashboards, ad-hoc SQL, BI tools (Power BI/Tableau), scheduled SQL in
  jobs, Genie.
- **Edge cases:**
  - **Sizing vs scaling** confusion: **size** = power per query; **max clusters** =
    concurrent users — tune the right knob.
  - Aggressive **auto-stop** = cheaper but adds cold-start latency for the next query.
  - `max_num_clusters` is capped (≤ 40).
- **Limitations:** warehouses run **SQL** workloads — for arbitrary Python/Spark ETL
  use jobs/all-purpose compute. Serverless availability/features vary by region.

## Common gotchas

- ❌ Running SQL/dashboards on an **all-purpose cluster** instead of a SQL Warehouse
  (wrong tool, worse cost/perf).
- ❌ Bumping **size** when the problem is **concurrency** (raise max clusters), or
  vice versa.
- ❌ Disabling **auto-stop** → paying for an idle warehouse overnight.
- ❌ Assuming Classic = current — **serverless** is the recommended default.
- ❌ Clicking the warehouse together in prod and never capturing it as a **bundle**.

## References

- [SQL Warehouses — Databricks docs](https://docs.databricks.com/aws/en/compute/sql-warehouse/)
- [Create a SQL warehouse](https://docs.databricks.com/aws/en/compute/sql-warehouse/create)
- [Sizing, scaling & queuing behavior](https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-behavior)
- [Photon](https://docs.databricks.com/aws/en/compute/photon)
