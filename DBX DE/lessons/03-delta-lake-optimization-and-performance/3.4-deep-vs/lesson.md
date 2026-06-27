# Deep vs Shallow CLONE; Predictive Optimization

> **Topic 3.4 · Delta Lake Optimization & Performance** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 3
> notebook in the topic folder; snippets below are the teaching units.

## What it is

Two ideas: **copying** tables (CLONE) and **automatic maintenance** (Predictive
Optimization).

- **CLONE** makes a copy of a Delta table — two flavors:
  - **DEEP CLONE** — copies **data + metadata** → a fully **independent** table.
  - **SHALLOW CLONE** — copies **only metadata/pointers**, still **references the
    source's data files** → instant and cheap, but **dependent** on the source.
- **Predictive Optimization (PO)** — Databricks **automatically runs maintenance**
  (`OPTIMIZE`, `VACUUM`, `ANALYZE`) on Unity Catalog **managed** tables, so you
  don't schedule it.

**Analogy:**
- **Deep clone** = photocopying a whole book — your own copy; edits don't touch
  the original, but it takes paper and time.
- **Shallow clone** = sticky-notes referencing the original book — instant, but
  useless if someone discards the original.
- **PO** = a robot librarian that reshelves and clears clutter automatically.

## Why it matters

- **Shallow clone = instant test/dev copy** of a huge prod table without
  duplicating terabytes — a favorite real-world trick (and interview answer).
- **Deep clone is incremental** — re-running it syncs only new data, making it a
  cheap, repeatable **backup / DR / cross-region** primitive.
- **PO removes the "did you schedule OPTIMIZE/VACUUM?" toil** — the modern answer
  to "how do you keep tables performant?" is increasingly "PO handles it."

**Real-world use case:** before a risky migration, `SHALLOW CLONE prod.orders` to
a sandbox in seconds, experiment freely, then drop it — zero data copied. For
DR, a nightly `DEEP CLONE` into another catalog re-syncs only the day's changes.

---

## How it works — deep dive

### 1. The two clone types

```sql
-- DEEP CLONE — independent copy of data + metadata (CLONE defaults to deep)
CREATE TABLE main.dr.orders_backup DEEP CLONE main.sales.orders;

-- SHALLOW CLONE — instant; points at the source's existing data files
CREATE TABLE main.dev.orders_dev SHALLOW CLONE main.sales.orders;

-- Clone a specific past version (great for reproducible sandboxes)
CREATE TABLE main.dev.orders_v5 SHALLOW CLONE main.sales.orders VERSION AS OF 5;
```

```python
# PySpark: run the DDL via spark.sql (CLONE is a SQL DDL operation)
spark.sql("CREATE OR REPLACE TABLE main.dr.orders_backup DEEP CLONE main.sales.orders")
```

- Both copy **schema, partitioning, invariants, nullability, `TBLPROPERTIES`**
  (deep also copies stream/`COPY INTO` ingestion metadata).
- **Neither** copies: the table **description**, user commit metadata, **Delta
  history**, or **Unity Catalog tags**.

### 2. Deep clone is INCREMENTAL (the backup superpower)

Re-running a deep clone into an **existing** target doesn't recopy everything —
it commits **only the new/changed data since the last clone**. That makes deep
clone a cheap, repeatable sync, not a one-shot full copy.

```sql
-- Day 1: full copy. Day 2..N: same command re-syncs ONLY the delta since last run.
CREATE OR REPLACE TABLE main.dr.orders_backup DEEP CLONE main.sales.orders;
```

- This is why deep clone is the go-to for **scheduled backups, DR, and migrating
  data between catalogs/regions** — cost scales with the change, not table size.

### 3. Shallow clone & the VACUUM trap

Shallow clone is metadata-only, so it's instant — but it **borrows the source's
data files**. Two consequences interviewers probe:

- **`VACUUM` on the source breaks the shallow clone** → readers hit
  `FileNotFoundException` (the files it pointed at are gone).
- Readers need access to **both** the source's storage and the clone's directory.

```sql
CREATE TABLE main.dev.orders_dev SHALLOW CLONE main.sales.orders;
-- ...later, on the SOURCE:
VACUUM main.sales.orders;     -- ⚠️ removes files orders_dev still references → clone breaks
```

- For a **durable** copy that survives source maintenance, use **DEEP CLONE**.

### 4. What CLONE can't do

- **Cannot clone streaming tables or materialized views** — they can't be the
  source *or* target of a deep/shallow clone.
- Doesn't carry history/description/UC tags (re-apply tags on the clone if needed).

### 5. Predictive Optimization — automatic maintenance

PO watches usage and runs the right maintenance at the right time on **UC managed
tables** — no schedules, no over/under-maintenance.

```sql
-- Enable at catalog or schema level (inherits down the hierarchy)
ALTER CATALOG main ENABLE PREDICTIVE OPTIMIZATION;
ALTER SCHEMA  main.sales ENABLE PREDICTIVE OPTIMIZATION;   -- or DISABLE / INHERIT
```

- **Runs `OPTIMIZE` (incl. incremental clustering), `VACUUM`, and `ANALYZE`.**
- **Enabled by default** for accounts created on/after **Nov 11, 2024** (older
  accounts via gradual rollout). Applies to **managed** tables — **external**
  tables you still maintain yourself.

---

## Comparison

| | Deep clone | Shallow clone |
|---|---|---|
| Copies data files | ✅ yes | ❌ points at source |
| Speed / cost | slower / more storage | **instant / cheap** |
| Independent of source | ✅ yes | ❌ VACUUM on source breaks it |
| Incremental re-sync | ✅ yes | n/a |
| Good for | backup, archive, DR, migration | test/dev, quick sandboxes |

## Uses, edge cases & limitations

- **Uses:** shallow → cheap test/dev/data-sharing copies; deep → backups,
  archiving versions, ML dataset reproducibility, DR, cross-region migration.
- **Edge cases:**
  - **Shallow clone depends on source files** — `VACUUM` on the source breaks it
    (`FileNotFoundException`); readers need access to both locations.
  - Cloned tables have **independent history** — time travel won't match the source.
  - Re-running a **deep** clone is incremental (only the delta is copied).
- **Limitations:** **can't clone streaming tables or materialized views**; CLONE
  doesn't copy history/description/UC tags. PO applies to **UC managed** tables
  (external tables you maintain yourself).

## Common gotchas

- ❌ Treating a **shallow** clone as a real backup — `VACUUM` on the source
  destroys it. Use **deep** clone for durable copies.
- ❌ Expecting CLONE to copy **history/tags/description** — it doesn't.
- ❌ Manually scheduling OPTIMIZE/VACUUM on managed tables where **PO** already runs.
- ❌ Trying to clone a **streaming table / materialized view** — unsupported.
- ❌ Re-doing a full copy for backups — a repeat **DEEP CLONE** only syncs the delta.

## References

- [Clone a table (deep & shallow) — docs](https://docs.databricks.com/aws/en/delta/clone)
- [CREATE TABLE CLONE (SQL reference)](https://docs.databricks.com/aws/en/sql/language-manual/delta-clone)
- [Predictive optimization](https://docs.databricks.com/aws/en/optimizations/predictive-optimization)
- [VACUUM](https://docs.databricks.com/aws/en/delta/vacuum)
