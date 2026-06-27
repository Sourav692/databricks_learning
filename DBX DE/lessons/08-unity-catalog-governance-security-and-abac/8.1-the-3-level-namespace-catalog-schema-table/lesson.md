# Unity Catalog — Namespace, Volumes, Discovery, Lineage & Audit

> **Topic 8.1 · Unity Catalog — Governance, Security & ABAC** — enterprise
> deep-dive, interview-focused. Runnable end-to-end code lives in the consolidated
> Topic 8 notebook (built at the last subtopic); snippets below are the teaching
> units.

## What it is

- **Unity Catalog (UC)** is the **governance layer** over all data & AI assets —
  one model for naming, permissions, discovery, lineage, and audit.
- **Object hierarchy:** **metastore** → **catalog** → **schema** → objects
  (table, view, **volume**, function, model).
- **3-level namespace:** every asset is `catalog.schema.object` — e.g.
  `prod.sales.orders`.
- **Volumes** govern **non-tabular files** (images, JSON, models) — the UC way to
  store files instead of legacy DBFS mounts.
- **Discovery** (Catalog Explorer + search), **lineage** (automatic table & column
  lineage), and **audit logs** (system tables) come built-in.

**Analogy:** UC is a **library system for a whole city**. The **metastore** is the
system; **catalogs** are branches; **schemas** are sections; **tables/volumes** are
the books/media. The 3-level name is the call number; **lineage** is the "who cited
this" trail; **audit** is the checkout log.

## Why it matters

- One governance model across **every workspace** = no per-tool permission sprawl.
  It's the backbone the rest of Topic 8 (access control, ABAC) builds on.
- "How is data governed on Databricks?" → **Unity Catalog** (namespace + lineage +
  audit) is the answer interviewers expect.

**Real-world use case:** `prod.finance.invoices` is governed once in UC — analysts
discover it in Catalog Explorer, **lineage** shows it feeds the revenue dashboard,
and **audit logs** record every access for the compliance team.

---

## How it works — deep dive

### 1. The 3-level namespace

```sql
-- Create the hierarchy (catalog → schema), then objects live under it
CREATE CATALOG IF NOT EXISTS prod;
CREATE SCHEMA  IF NOT EXISTS prod.sales;

-- Address any object fully-qualified...
SELECT * FROM prod.sales.orders;

-- ...or set a default catalog+schema and use short names
USE CATALOG prod;
USE SCHEMA  sales;          -- or: USE prod.sales;
SELECT * FROM orders;       -- resolves to prod.sales.orders
```

```python
spark.sql("USE CATALOG prod"); spark.sql("USE SCHEMA sales")
df = spark.table("orders")              # 3-level resolution applies
df2 = spark.table("prod.sales.orders")  # or fully-qualified
```

- A common org pattern: **catalog per environment** (`dev`/`staging`/`prod`) or
  **per domain**, schemas for bronze/silver/gold or by subject area.
- Prefer UC catalogs over legacy **`hive_metastore`** (the old 2-level world).

### 2. Inspect metadata with `information_schema`

Every catalog exposes an **`information_schema`** for programmatic discovery:

```sql
SELECT table_name, table_type FROM prod.information_schema.tables
WHERE table_schema = 'sales';

SELECT column_name, data_type FROM prod.information_schema.columns
WHERE table_schema = 'sales' AND table_name = 'orders';
```

### 3. Volumes — govern files (not tables)

A **Volume** is the UC-governed place for files; access them at
`/Volumes/<catalog>/<schema>/<volume>/...`.

```sql
-- Managed volume (UC owns the storage)
CREATE VOLUME prod.sales.landing;

-- External volume (you point at a path under a UC external location)
CREATE EXTERNAL VOLUME prod.sales.archive
  LOCATION 's3://acme-lake/sales/archive/';
```

```python
# Read/write files through the /Volumes path — governed like any UC object
dbutils.fs.ls("/Volumes/prod/sales/landing/")
df = spark.read.json("/Volumes/prod/sales/landing/clickstream.json")
df.write.format("delta").saveAsTable("prod.sales.bronze_clicks")
```

- **Managed vs external** mirrors managed/external tables (2.3): managed = UC owns
  lifecycle; external = you own the path. Volumes replace **DBFS mounts**.

### 4. Discovery & tags

```sql
-- Document + tag for search/governance (Catalog Explorer surfaces these)
COMMENT ON TABLE prod.sales.orders IS 'Curated order facts (gold).';
ALTER TABLE prod.sales.orders SET TAGS ('domain' = 'sales', 'pii' = 'false');
ALTER TABLE prod.sales.orders ALTER COLUMN email SET TAGS ('pii' = 'true');
```

### 5. Lineage & audit (built-in, via system tables)

- **Lineage** is captured **automatically** (table→table→dashboard, down to
  **column** level) — use it for impact analysis.
- **Audit** and lineage are queryable as **system tables**:

```sql
-- Who accessed what, recently (audit)
SELECT event_time, user_identity.email, action_name, request_params.full_name_arg
FROM system.access.audit
WHERE action_name = 'getTable' AND event_date >= current_date() - 7;

-- Column lineage
SELECT * FROM system.access.column_lineage
WHERE target_table_name = 'orders' AND target_table_schema = 'sales';
```

---

## Uses, edge cases & limitations

- **Uses:** organizing data by environment/domain (catalogs/schemas); governing
  files via Volumes; impact analysis via lineage; compliance via audit; programmatic
  discovery via `information_schema` / system tables.
- **Edge cases:**
  - Mixing legacy **`hive_metastore`** (2-level) with UC (3-level) confuses
    references — prefer UC catalogs.
  - Lineage captures **UC-tracked** operations; activity outside UC may not appear.
  - `information_schema` is **per-catalog**; `system.information_schema` spans the
    metastore.
- **Limitations:** lineage/audit reflect what UC governs — external tools writing
  raw files outside UC aren't fully captured. Volumes are for files, **not** a
  replacement for Delta tables.

## Common gotchas

- ❌ Using **DBFS mounts** for files instead of **UC Volumes** (legacy, ungoverned).
- ❌ Two-level `schema.table` habits — UC is **three-level** `catalog.schema.table`.
- ❌ Assuming lineage covers everything — it covers **UC-tracked** flows.
- ❌ Treating a **Volume** like a table — Volumes hold files; query tables for data.
- ❌ Hardcoding a single catalog — parameterize so the same code runs in
  dev/staging/prod catalogs.

## References

- [Unity Catalog — Databricks docs](https://docs.databricks.com/aws/en/data-governance/unity-catalog/)
- [Volumes](https://docs.databricks.com/aws/en/volumes/)
- [information_schema](https://docs.databricks.com/aws/en/sql/language-manual/information-schema/)
- [Data lineage](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage)
- [Audit log system table](https://docs.databricks.com/aws/en/admin/system-tables/audit-logs)
