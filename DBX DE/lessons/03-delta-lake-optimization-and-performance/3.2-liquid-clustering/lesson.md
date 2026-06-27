# Liquid Clustering

> **Topic 3.2 · Delta Lake Optimization & Performance** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 3
> notebook (built at the last subtopic); snippets below are the teaching units.

## What it is

- **Liquid Clustering** is the **modern data layout** for Delta tables — it
  automatically organizes data by **clustering keys**, replacing **both**
  partitioning **and** ZORDER.
- Its headline trick: you can **change the clustering keys later without
  rewriting** existing data — layout evolves with your query patterns.
- Set with **`CLUSTER BY (cols)`**, or let Databricks pick & adapt keys with
  **`CLUSTER BY AUTO`**.

**Analogy:** old **partitioning** is a filing cabinet with fixed, labeled drawers
— change the scheme and you re-file everything. **Liquid Clustering** is a smart
shelf that re-sorts itself as you add items, and you can change the sort rule any
time without re-shelving what's already there.

## Why it matters

- Partitioning is **rigid** (wrong choice = small-file skew + expensive rewrites);
  ZORDER must be re-applied each OPTIMIZE and can't be combined with partitioning.
  Liquid Clustering fixes both.
- **Databricks recommends it for virtually all new tables** — a very common
  "what layout would you choose today?" interview answer.

**Real-world use case:** a fast-growing events table is queried by `event_date`
now but by `region` next quarter. With Liquid Clustering you just
`ALTER TABLE … CLUSTER BY (region)` — no full rewrite; future writes re-cluster.

---

## How it works — deep dive

### 1. What it replaces, and the mechanism

- Instead of fixed partition **directories**, Delta clusters rows by key values
  into files and tracks the layout in the log — so there's no high-cardinality
  small-file explosion and no rigid directory scheme.
- Tighter per-file min/max on the cluster keys → better **data skipping**
  (Topic 2.1) on those columns, same as ZORDER aimed for, but maintained
  incrementally instead of re-specified each OPTIMIZE.
- **Requires DBR 15.4 LTS+** (GA). Some cases need newer runtimes (see below).

### 2. Creating a table & choosing keys

```sql
-- Explicit keys: pick the columns you filter/join on most (max 4)
CREATE TABLE main.sales.events (id BIGINT, event_date DATE, region STRING)
  CLUSTER BY (event_date, region);

-- Or let Databricks choose & adapt keys from query history (DBR 15.4 LTS+,
-- Unity Catalog managed tables) — powered by Predictive Optimization
CREATE OR REPLACE TABLE main.sales.events2 (id BIGINT, region STRING)
  CLUSTER BY AUTO;
```

```python
# PySpark (DataFrameWriterV2): create a clustered table from a DataFrame
(df.writeTo("main.sales.events").using("delta")
   .clusterBy("event_date", "region").create())
```

- **Up to 4 clustering keys.** On smaller tables (<10 TB), more than ~2 keys can
  dilute single-column filter performance — pick the highest-value filters.
- **Supported key types:** Date, Timestamp/TimestampNTZ, String, Int/Long/Short/
  Byte, Float/Double/Decimal. **Nested struct *fields*** work via dot notation;
  whole complex columns (Array/Map/Struct) do **not**.

### 3. Changing keys without rewriting (the killer feature)

```sql
-- Query patterns shifted to region — re-key instantly; existing data is NOT rewritten
ALTER TABLE main.sales.events CLUSTER BY (region);

-- New writes + OPTIMIZE now use (region). To recluster legacy data immediately:
OPTIMIZE main.sales.events FULL;     -- reclusters all existing data (DBR 16.4 LTS+)
```

- `ALTER … CLUSTER BY` is metadata-only and applies **going forward** — there's
  no full-table rewrite (impossible with partitioning).
- **`OPTIMIZE … FULL`** forces a full recluster of old data to the new keys; it's
  available on **DBR 16.4 LTS+**. Plain `OPTIMIZE` does incremental compaction +
  clustering of recent data.

### 4. Clustering on write & ongoing maintenance

- **Clustering on write is automatic** but only triggers once a write reaches a
  size threshold (≈64 MB–1 GB depending on key count). Small/streaming writes may
  land unclustered until an `OPTIMIZE` runs.
- Because not every operation clusters, **run `OPTIMIZE` regularly** — or rely on
  **Predictive Optimization** to do it automatically on UC managed tables.
- **Structured Streaming** writes to clustered tables are supported on
  **DBR 16.4 LTS+**.

### 5. Liquid Clustering vs Partitioning vs ZORDER

| | Partitioning | ZORDER (on OPTIMIZE) | **Liquid Clustering** |
|---|---|---|---|
| Change layout key | full rewrite | re-run OPTIMIZE | **no rewrite** (`ALTER CLUSTER BY`) |
| High cardinality | poor (small-file skew) | OK | **good** |
| Maintenance | manual | re-applied each OPTIMIZE | incremental + auto-key option |
| Combine with the others | — | with partitioning only | **cannot** mix |
| New tables | ❌ | ❌ | ✅ **recommended** |

**Decision:** for new tables, **Liquid Clustering**. Migrate existing ZORDER/
partitioned tables when practical (re-key + `OPTIMIZE FULL`).

---

## Uses, edge cases & limitations

- **Uses:** the default for new tables; high-cardinality filter columns; skewed or
  fast-growing tables; shifting access patterns; concurrent writes.
- **Edge cases:**
  - Up to **4 keys**; small tables (<10 TB) can degrade past ~2 keys.
  - Clustering-on-write only kicks in past a size threshold — tiny/streaming
    writes may need `OPTIMIZE` to actually cluster.
  - `ALTER … CLUSTER BY` doesn't touch old data — use `OPTIMIZE FULL` to recluster.
- **Limitations:**
  - **GA on DBR 15.4 LTS+**; `OPTIMIZE FULL` and streaming need **16.4 LTS+**.
  - **Cannot be combined with partitioning or ZORDER** — pick one.
  - Keys must be simple types (nested struct fields OK via dot notation;
    Array/Map/Struct columns are not).

## Common gotchas

- ❌ Trying to use partitioning **and** Liquid Clustering together — incompatible.
- ❌ Expecting `ALTER … CLUSTER BY` to instantly reorganize old data — it applies
  going forward; use `OPTIMIZE FULL` (DBR 16.4 LTS+) to recluster now.
- ❌ Picking 5+ clustering columns — keep to the most-filtered **1–4**.
- ❌ Clustering on a complex (Array/Map/Struct) column — unsupported.
- ❌ Assuming every write clusters — small writes may need `OPTIMIZE`; lean on
  Predictive Optimization for managed tables.

## References

- [Liquid clustering — Databricks docs](https://docs.databricks.com/aws/en/delta/clustering)
- [OPTIMIZE / OPTIMIZE FULL](https://docs.databricks.com/aws/en/sql/language-manual/delta-optimize)
- [Predictive optimization](https://docs.databricks.com/aws/en/optimizations/predictive-optimization)
