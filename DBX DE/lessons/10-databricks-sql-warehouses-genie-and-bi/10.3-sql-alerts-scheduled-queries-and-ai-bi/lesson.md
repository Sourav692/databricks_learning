# SQL Alerts, Scheduled Queries & AI/BI Dashboards

> **Topic 10.3 · Databricks SQL — Warehouses, Genie & BI** — enterprise deep-dive,
> interview-focused. These are UI features, so each section pairs the **mechanism**
> with **config-as-code** (Asset Bundle / CLI) — the way enterprises actually ship
> and version dashboards and alerts. Hands-on SQL lives in the consolidated Topic 10
> notebook (built at 10.4).

## What it is

Turning saved queries into **automation and visuals**:

- **Scheduled query** — run a saved SQL query on a **Quartz cron** schedule (on a
  SQL Warehouse) with no one clicking Run.
- **SQL Alert** — a scheduled query **+ a condition/threshold**; when the condition
  is met, Databricks **notifies** destinations (email / Slack / webhook).
- **AI/BI Dashboard** — datasets + visualizations on an interactive **canvas**, with
  **Genie** natural-language authoring, parameters/filters, scheduled refresh, and
  email/Slack **subscriptions**. (Successor to the retired legacy/Redash dashboards.)

**Analogy:** a scheduled query is an **automatic meter reading**; an **alert** is a
**smoke detector** (evaluates on a cadence, only beeps when a threshold trips); a
**dashboard** is the **car's gauge cluster** — visuals refreshed so you see the
whole picture at a glance.

## Why it matters

- DE doesn't stop at producing gold tables — stakeholders need **monitoring and
  visibility**. Alerts catch problems automatically; dashboards make data usable.
- "How do you alert when revenue drops / a pipeline's row count is 0?" → **SQL Alert
  on a scheduled query** — a very common interview scenario.
- Shipping dashboards/alerts **as code** (versioned, reviewed, promoted dev→prod) is
  what separates a hobby workspace from an enterprise one.

**Real-world use case:** a **scheduled query** computes daily order counts each
morning; a **SQL Alert** emails on-call if today's count is **0** (pipeline
failure); an **AI/BI Dashboard** shows revenue by region, refreshed daily, with a
Slack **subscription** to leadership — all defined in a bundle and promoted through CI/CD.

---

## How it works — deep dive

### 1. Scheduled queries — Quartz cron on a warehouse

**Mechanism:** attach a **schedule** to a saved query; at each fire time the query
runs on a chosen SQL Warehouse under the **owner's** identity. Databricks uses a
**6-field Quartz cron** (`sec min hour day month day-of-week`) — *not* 5-field Unix
cron.

**Why:** materializes fresh results for dashboards/alerts without manual runs.

**Trade-off:** the schedule cadence *is* your data freshness and your cost — a
1-minute schedule on a serverless warehouse keeps it warm (and billed); aggressive
cadence ≠ free.

```yaml
# Quartz cron is 6 fields. "every day at 06:00" =
#   sec=0 min=0 hour=6 day=* month=* dow=?
schedule:
  quartz_cron_schedule: "0 0 6 * * ?"   # 06:00 daily
  timezone_id: "America/New_York"
```

### 2. SQL Alerts — condition on a query result → notify

**Mechanism:** an alert points at a query, defines an **evaluation** (a
`comparison_operator` on a result column vs a `threshold`), and on a schedule
checks it. If the condition is met it sends to **notification destinations**.

**Why:** turns a metric into proactive monitoring — data-quality SLAs, freshness,
revenue drops.

**Trade-off:** alerts evaluate **only when the schedule fires** — a 1-hour schedule
means up to ~1-hour detection lag. They trigger on a **query-result condition**, not
arbitrary logic.

```sql
-- The alert's backing query returns ONE row/column to compare.
-- Alert condition (set in the alert, shown here as intent):
--   WHEN orders_today = 0  → notify on-call (pipeline likely failed)
SELECT count(*) AS orders_today
FROM prod.sales.orders
WHERE order_date = current_date();
```

### 3. Alerts & schedules as code — Asset Bundle

**Mechanism:** the `alert` bundle resource (SQL alert v2) declares the query, the
evaluation, the schedule, and the warehouse — so the alert is versioned and
promoted through environments like any other artifact.

**Why:** no click-ops drift; the prod alert is exactly what's in Git, reviewed and
deployed by CI/CD.

**Trade-off:** the bundle owns the alert — editing it in the UI then re-deploying
will revert your manual change (intended: code is the source of truth).

```yaml
# databricks.yml — verified keys (alert v2 resource)
resources:
  alerts:
    orders_zero_alert:
      display_name: orders_zero_alert
      query_text: |
        SELECT count(*) AS orders_today
        FROM prod.sales.orders WHERE order_date = current_date()
      evaluation:
        comparison_operator: EQUAL      # EQUAL / GREATER_THAN / LESS_THAN …
        source: { name: orders_today }  # the result column to compare
        threshold: { value: { double_value: 0 } }
      schedule:
        quartz_cron_schedule: "0 0 6 * * ?"
        timezone_id: America/New_York
      warehouse_id: ${var.warehouse_id}
```

### 4. AI/BI Dashboards — datasets, visuals, filters, subscriptions

**Mechanism:** an AI/BI Dashboard has **datasets** (SQL queries that feed the page),
**visualizations** placed on a canvas, and **parameters/filters** that cross-filter
widgets. You author charts directly or with **Genie** (natural language), then
**publish** with a scheduled refresh and email/Slack **subscriptions**.

**Why:** one governed, interactive artifact for self-serve analytics — replacing the
retired legacy/Redash dashboards.

**Trade-off:** dashboards run on a SQL Warehouse — auto-stop/cold-start affects
first-load latency; scheduled refresh and embedded credentials run as the
**publisher/owner**, so mind permissions and cost.

```sql
-- A dashboard DATASET is just a query (often parameterized so filters work):
SELECT region, sum(amount) AS revenue
FROM prod.sales.orders
WHERE order_date BETWEEN :start_date AND :end_date
GROUP BY region
ORDER BY revenue DESC;
```

### 5. Dashboards as code — bundle the `.lvdash.json`

**Mechanism:** an AI/BI Dashboard serializes to a `*.lvdash.json` file. The
`dashboard` bundle resource references that file and a warehouse, so the dashboard
deploys with the project. `databricks bundle generate dashboard` exports an existing
dashboard to the file to start.

**Why:** dashboards become reviewable, diffable, environment-promotable assets.

**Trade-off:** the `.lvdash.json` is a generated definition — hand-editing is
brittle; the normal flow is author/edit in the UI, then `generate` to re-export.

```yaml
# databricks.yml — verified keys (dashboard resource)
resources:
  dashboards:
    revenue_overview:
      display_name: "Revenue Overview"
      file_path: ../src/revenue_overview.lvdash.json
      warehouse_id: ${var.warehouse_id}
      # parent_path / embed_credentials / permissions are optional
```

```bash
# export an existing dashboard to a .lvdash.json, then deploy the bundle
databricks bundle generate dashboard --existing-path "/Workspace/.../Revenue Overview"
databricks bundle deploy -t prod
```

---

## Uses, edge cases & limitations

- **Uses:** data-quality/SLA alerting, daily KPI emails, executive dashboards,
  self-serve NL exploration via Genie, version-controlled BI via bundles.
- **Edge cases:**
  - Alerts evaluate **only when the schedule fires** — cadence = detection lag.
  - Dashboards/scheduled refresh run as the **owner/publisher** — permission & cost
    implications; a stopped warehouse adds cold-start latency.
  - Schedules are **6-field Quartz** cron, not 5-field Unix cron.
- **Limitations:** alerts trigger on a **query-result condition**, not arbitrary
  logic; **legacy/Redash dashboards are retired** — use **AI/BI Dashboards**.

## Common gotchas

- ❌ Expecting an alert to fire **instantly** — it fires **on its schedule** (mind
  the lag).
- ❌ Writing a **5-field Unix cron** (`0 6 * * *`) — Databricks wants **6-field
  Quartz** (`0 0 6 * * ?`).
- ❌ Building on **legacy dashboards** — retired; use **AI/BI Dashboards**.
- ❌ Forgetting scheduled refresh/queries run as the **owner** (permission/cost).
- ❌ No **notification destination** configured → an alert that never tells anyone.
- ❌ Hand-editing the generated `.lvdash.json` instead of editing in the UI and
  re-running `bundle generate dashboard`.

## References

- [AI/BI Dashboards — docs](https://docs.databricks.com/aws/en/dashboards/)
- [Databricks SQL alerts](https://docs.databricks.com/aws/en/sql/user/alerts/)
- [Schedule a query](https://docs.databricks.com/aws/en/sql/user/queries/schedule-query)
- [Bundle resources — `alert` & `dashboard`](https://docs.databricks.com/aws/en/dev-tools/bundles/resources)
