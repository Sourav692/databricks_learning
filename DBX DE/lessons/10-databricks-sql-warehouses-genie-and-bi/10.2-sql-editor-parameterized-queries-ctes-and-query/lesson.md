# SQL Editor, Parameterized Queries, CTEs, Snippets & Caching

> **Topic 10.2 · Databricks SQL — Warehouses, Genie & BI** — enterprise deep-dive,
> interview-focused. Every sub-topic pairs the **mechanism** with a commented,
> production-shaped SQL snippet. Hands-on code also lives in the consolidated
> Topic 10 notebook (built at the last subtopic, 10.4).

## What it is

The everyday authoring layer of Databricks SQL — how analysts and engineers write,
parameterize, structure, reuse, and speed up SQL that runs on a **SQL Warehouse**:

- **SQL editor** — write/run SQL against a warehouse; save, version, and share queries.
- **Named parameter markers** (`:param`) — typed, injection-safe placeholders.
- **`IDENTIFIER()`** — safely parameterize *object names* (catalog/schema/table/column).
- **CTEs** (`WITH … AS (…)`) — name sub-queries for readable multi-step SQL.
- **Query snippets** — saved reusable SQL fragments inserted by a trigger keyword.
- **Query caching** — three layers (UI cache, result cache, disk cache) that make
  repeated BI queries return in milliseconds.

**Analogy:** the SQL editor is your **word processor for data**; **`:parameters`**
are **mail-merge fields** (one template, many values, safely filled in); **CTEs**
are **named paragraphs** you reference instead of re-typing; **snippets** are
**text-expander shortcuts**; **caching** is the **"recently opened" shelf** that
hands back an answer you already computed.

## Why it matters

- Parameterized queries are *the* safe, reusable way to power dashboards and
  reports — hard-coding values invites **SQL injection** and copy-paste sprawl.
- Caching is the single biggest reason a BI dashboard re-opens instantly — knowing
  *which* cache served a query (and when each invalidates) is a classic
  "why was the second run 100× faster?" interview question.

**Real-world use case:** a sales dashboard uses `:start_date`/`:end_date`
parameters so one query serves every date range; a **CTE** builds a clean
aggregate; the **result cache** makes re-opening the dashboard instant; a shared
**snippet** standardizes the "active customer" filter across the whole team.

---

## How it works — deep dive

### 1. The SQL editor — where queries live

**Mechanism:** the new SQL editor runs statements against a selected **SQL
Warehouse** (10.1). Queries are first-class objects — saved, owned, permissioned
(UC `GRANT`), tabbed, and runnable from dashboards/alerts/jobs by reference.

**Why:** a saved query is reusable infrastructure, not a throwaway — one query
backs a dashboard tile, a scheduled refresh, and an alert at once.

**Trade-off:** the warehouse you pick determines cost/latency (serverless vs pro);
the editor itself is free, the compute under it is not.

```sql
-- A saved query targets a UC object with the 3-level namespace.
-- Pick the warehouse in the editor's compute dropdown; the query is the asset.
SELECT region, count(*) AS orders
FROM prod.sales.orders            -- catalog.schema.table (Unity Catalog)
WHERE order_date >= current_date() - INTERVAL 7 DAYS
GROUP BY region
ORDER BY orders DESC;
```

### 2. Named parameter markers — `:param` (typed & injection-safe)

**Mechanism:** prefix an identifier with a colon (`:start_date`). The mandatory
`:` separates parameter names from column names. Values are **bound** by the
engine as typed literals (string/int/decimal/date/timestamp) — never
string-spliced into the SQL text.

**Why:** binding is what makes parameters **injection-proof** and type-checked —
`'2026-01-01'; DROP TABLE …` arrives as a *string value*, not executable SQL.

**Trade-off:** parameter markers replace the **legacy mustache `{{ }}`** syntax of
the old editor — queries using `{{ }}` must be migrated before they run in the new
editor (see gotchas).

```sql
-- :named markers are typed and bound — safe + reusable across every date range.
SELECT region, sum(amount) AS revenue
FROM prod.sales.orders
WHERE order_date BETWEEN :start_date AND :end_date   -- bound as typed DATEs
  AND region = :region                               -- bound as STRING
GROUP BY region;
```

### 3. `IDENTIFIER()` — parameterize object *names* safely

**Mechanism:** a plain `:param` binds a *value*, not an identifier — you cannot put
`:table` directly after `FROM`. `IDENTIFIER(:x)` tells the engine to interpret the
bound string as a catalog/schema/table/column **name**.

**Why:** lets one query target different tables/columns (e.g., env-switching
`dev`→`prod`, or a column chosen by the dashboard) without unsafe concatenation.

**Trade-off:** `IDENTIFIER()` resolves a name, so the object must exist and the
caller must have grants on it — it does not bypass Unity Catalog permissions.

```sql
-- Dynamic, injection-safe table & column selection.
SELECT IDENTIFIER(:metric_col) AS metric          -- column name as a parameter
FROM IDENTIFIER(:catalog || '.' || :schema || '.orders')   -- table name as a parameter
WHERE order_date >= :since;
```

### 4. CTEs — `WITH` for readable multi-step SQL

**Mechanism:** `WITH name AS (subquery)` names an intermediate result you can
reference (and chain) later in the same statement — turning a deep nest of
sub-queries into a top-to-bottom pipeline.

**Why:** readability and maintainability — each step is named and testable; the
optimizer still plans the whole statement as one.

**Trade-off (the gotcha):** a CTE is **not guaranteed to be materialized** — if you
reference it many times it may be **recomputed** each time. For expensive,
multiply-referenced logic, persist a temp view/table instead.

```sql
-- Chained CTEs read top-down; each name is a clean, reusable step.
WITH recent AS (
  SELECT * FROM prod.sales.orders
  WHERE order_date BETWEEN :start_date AND :end_date
),
by_region AS (
  SELECT region, sum(amount) AS revenue, count(*) AS orders
  FROM recent GROUP BY region
)
SELECT region, revenue, revenue / orders AS avg_order_value
FROM by_region
ORDER BY revenue DESC;
```

### 5. Query snippets — team-standard SQL fragments

**Mechanism:** save a reusable SQL fragment with a **trigger keyword**; typing the
trigger in the editor expands it. Snippets can include parameter placeholders.

**Why:** standardizes common logic (a canonical "active customer" filter, a date
spine) so every analyst writes it the same way — fewer subtle definition drifts.

**Trade-off:** snippets are an editor-authoring convenience, not a governed object
— for logic that *must* be consistent and permissioned, prefer a **view or SQL
UDF** in Unity Catalog over a copy-pasted snippet.

```sql
-- Snippet "active_cust" might expand to this standardized predicate,
-- so every query defines "active" identically:
--   status = 'ACTIVE' AND last_seen >= current_date() - INTERVAL 90 DAYS
SELECT customer_id, region
FROM prod.sales.customers
WHERE status = 'ACTIVE' AND last_seen >= current_date() - INTERVAL 90 DAYS;
```

### 6. Query caching — three layers that make BI instant

**Mechanism:** Databricks SQL caches at three levels, each invalidating
automatically when the underlying tables change:

| Layer | Scope & lifetime | What it stores |
|---|---|---|
| **UI cache** | per-user, in the DBSQL UI, up to **7 days** | recent query results + visualizations |
| **Result cache** — *local* | per-warehouse, **in-memory**, lives until the cluster stops/restarts | exact results of a previously run query |
| **Result cache** — *remote* | **serverless**, persists across restarts, shared across the workspace's warehouses, **24-hour** lifecycle | results as workspace system data |
| **Disk cache** | per-node local **SSD**, auto-detects data changes | hot data files in an optimized format |

**Why:** a re-run of the *same* query on *unchanged* data skips computation
entirely (result cache); the disk cache speeds *different* queries that read the
*same hot data*. Together they make dashboards re-open in milliseconds.

**Trade-off:** caches **invalidate on write** — don't expect cached speed right
after an ETL load. To force a fresh run (e.g., benchmarking), disable result reuse.

```sql
-- Force a cold run for benchmarking; re-enable afterward.
SET use_cached_result = false;   -- bypass the result cache for this session
SELECT region, sum(amount) FROM prod.sales.orders GROUP BY region;
SET use_cached_result = true;    -- restore caching
```

---

## Uses, edge cases & limitations

- **Uses:** reusable dashboard/report queries (`:param`), env/column-agnostic
  queries (`IDENTIFIER`), readable pipelines (CTEs), team-standard fragments
  (snippets), millisecond re-opens (caching).
- **Edge cases:**
  - **CTE recompute** — a CTE referenced many times may be re-evaluated; persist a
    temp view/table for heavy reuse.
  - **Cache after a write** — any result/disk cache layer invalidates when the
    source table changes; the first post-load query pays full cost.
  - **Remote result cache** needs a **serverless** warehouse and an active
    warehouse to read from.
- **Limitations:** `:param`/`IDENTIFIER` and snippets are Databricks SQL editor /
  SQL features; the **query profile** (covered as a diagnostic tool) reflects a
  single run's plan — re-check after data/size changes.

## Common gotchas

- ❌ String-concatenating user input into SQL instead of `:param` → injection risk
  + no type safety.
- ❌ Putting `:table` directly after `FROM` — bind *values* with `:param`, bind
  *names* with `IDENTIFIER(:table)`.
- ❌ Pasting legacy **`{{ mustache }}`** parameters into the new editor — they must
  be migrated to `:named` markers or the query won't run.
- ❌ Referencing a heavy CTE many times and assuming it's computed once — it may be
  recomputed; materialize it.
- ❌ Assuming the **result cache** still helps after the table changed (it won't),
  or benchmarking with caching on (set `use_cached_result = false`).

## References

- [Use named parameter markers — docs](https://docs.databricks.com/aws/en/sql/user/queries/query-parameters)
- [Parameter markers (SQL language ref)](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-parameter-marker)
- [Work with parameter widgets (SQL editor)](https://docs.databricks.com/aws/en/sql/user/sql-editor/parameter-widgets)
- [Caching in Databricks SQL](https://docs.databricks.com/aws/en/sql/user/queries/query-caching)
- [Common table expressions (WITH)](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-cte)
