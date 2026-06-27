# OPTIMIZE (Compaction) & Z-ORDER

> **Topic 3.1 · Delta Lake Optimization & Performance** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 3
> notebook (built at the last subtopic); snippets below are the teaching units.

## What it is

- **OPTIMIZE** = file **compaction** (bin-packing): rewrites many small files into
  fewer large ones to fix the **small-file problem**.
- **ZORDER BY** = an optional clause on OPTIMIZE that **co-locates related values**
  into the same files, so queries filtering on those columns **skip more files**.

**Analogy:** a warehouse where every item arrived in its own tiny box.
**OPTIMIZE** repacks them into a few big, neatly stacked boxes (faster to fetch).
**Z-ORDER** also groups *related* items together — all "red shirts" near each
other — so a picker filtering for red shirts opens far fewer boxes.

## Why it matters

- Streaming / frequent writes create **thousands of small files** → every query
  pays to open them all (the small-file problem). OPTIMIZE restores fast reads.
- **Data skipping** (Topic 2.1: file-level min/max stats) is how Delta avoids
  reading irrelevant files. Z-ORDER tightens those ranges on your filter columns
  so skipping is far more effective.

**Real-world use case:** an Auto Loader job writes a bronze table every minute,
leaving tiny files. A nightly `OPTIMIZE events ZORDER BY (event_date, user_id)`
compacts them and clusters by the common filter columns → dashboards speed up.

---

## How it works — deep dive

### 1. OPTIMIZE — bin-packing compaction

OPTIMIZE reads small files and rewrites their rows into fewer files near a target
size, **toward ~1 GB by default** (`spark.databricks.delta.optimize.maxFileSize`).

```sql
OPTIMIZE main.sales.events;                              -- compact the whole table
OPTIMIZE main.sales.events WHERE event_date >= '2026-06-01';  -- scope = cheaper
```

```python
from delta.tables import DeltaTable
DeltaTable.forName(spark, "main.sales.events").optimize().executeCompaction()
```

- **Bin-packing is idempotent** — re-running on already-compacted data does
  nothing, so it's safe to schedule.
- **Reader-safe** — snapshot isolation (Topic 2.1) means queries keep running
  during OPTIMIZE; the compaction lands as one atomic commit.
- **Scope it** — a `WHERE` predicate compacts only the partitions/files you need
  (e.g. yesterday's data), keeping cost down.

### 2. ZORDER — multi-dimensional clustering for data skipping

`ZORDER BY` sorts data along a space-filling (Z-order) curve so rows with similar
values in the chosen columns land in the **same files**. Tighter per-file min/max
ranges → more files skipped on those filters.

```sql
-- compact + cluster by the columns you actually filter/join on
OPTIMIZE main.sales.events ZORDER BY (event_date, user_id);
```

```python
(DeltaTable.forName(spark, "main.sales.events")
   .optimize()
   .executeZOrderBy("event_date", "user_id"))
```

- **Pick 1–3 high-value columns** — the ones in `WHERE`/`JOIN`. Each extra ZORDER
  column dilutes locality on the others, so more columns ≠ better.
- **Not "set and forget":** ZORDER applies to the data present at rewrite time.
  As new data lands it isn't clustered until you **OPTIMIZE … ZORDER again** —
  unlike the bin-packing step, the clustering work re-runs.
- **Best on high-cardinality filter columns** (e.g. `user_id`); low-cardinality
  columns are better handled by partitioning or clustering.

### 3. Avoid small files at write time — optimized writes & auto compaction

Rather than only cleaning up after the fact, reduce small files *as you write*:

```sql
ALTER TABLE main.sales.events SET TBLPROPERTIES (
  'delta.autoOptimize.optimizeWrite' = 'true',   -- shuffle to ~128 MB files on write
  'delta.autoOptimize.autoCompact'   = 'true'    -- compact small files after write (~128 MB)
);
```

- **Optimized writes** target ~128 MB per file at write time; **auto compaction**
  runs a small compaction right after a write. Both target ~128 MB (vs OPTIMIZE's
  ~1 GB), trading a little write latency for fewer small files.

### 4. Predictive Optimization — let Databricks run it

On **Unity Catalog managed tables**, **Predictive Optimization** automatically
runs `OPTIMIZE` (and `VACUUM`, `ANALYZE`) when it detects benefit — so often you
**don't schedule OPTIMIZE yourself** (don't double-schedule). External tables are
not covered, so you maintain those manually.

### 5. ZORDER vs Liquid Clustering (the modern default)

- **Databricks now recommends Liquid Clustering instead of partitioning *or*
  ZORDER for new tables** (next lesson, 3.2). Clustering keys are a table property
  (`CLUSTER BY`) maintained incrementally — no per-OPTIMIZE re-specification.
- Reach for ZORDER mainly on **existing** tables not yet on liquid clustering.

---

## Comparison

| | OPTIMIZE (bin-pack) | OPTIMIZE … ZORDER | Liquid Clustering |
|---|---|---|---|
| Fixes small files | ✅ | ✅ | ✅ (incremental) |
| Improves filter skipping | ❌ | ✅ (chosen cols) | ✅ (chosen cols) |
| Idempotent / re-spec | ✅ idempotent | re-applied each run | key set on table |
| Target file size | ~1 GB | ~1 GB | auto |
| Status | current | **older approach** | **recommended (new tables)** |

## Uses, edge cases & limitations

- **Uses:** compacting streaming/bronze tables; speeding selective queries via
  ZORDER on high-cardinality filter columns; optimized writes to prevent small
  files up front.
- **Edge cases:**
  - OPTIMIZE is **CPU-intensive** (Parquet encode/decode) — run off-peak on
    adequate compute and use a `WHERE` to limit scope.
  - **ZORDER on too many columns** dilutes the benefit — pick the 1–3 you filter
    on; ZORDER on a never-filtered column is wasted compute.
- **Limitations:** ZORDER must be **re-applied on each OPTIMIZE**; it's the older
  layout approach. For new tables prefer **Liquid Clustering**. OPTIMIZE rewrites
  files, so it interacts with VACUUM retention and streaming readers (a rewrite is
  a data-change commit).

## Common gotchas

- ❌ Running plain `OPTIMIZE` and expecting query *filtering* to speed up — that's
  what **ZORDER** adds; OPTIMIZE alone just compacts.
- ❌ Z-ORDERing by a column you never filter on — no benefit, wasted compute.
- ❌ Choosing partitioning + ZORDER for a brand-new table — prefer **Liquid
  Clustering** now.
- ❌ Forgetting **Predictive Optimization** may already handle this for managed
  tables — don't double-schedule.
- ❌ Assuming ZORDER auto-maintains — new data needs another `OPTIMIZE … ZORDER`.

## References

- [OPTIMIZE — Databricks docs](https://docs.databricks.com/aws/en/delta/optimize)
- [OPTIMIZE (SQL reference)](https://docs.databricks.com/aws/en/sql/language-manual/delta-optimize)
- [Data skipping & Z-ordering](https://docs.databricks.com/aws/en/delta/data-skipping)
- [Control data file size (optimized writes / auto compaction)](https://docs.databricks.com/aws/en/delta/tune-file-size)
- [Liquid clustering (recommended)](https://docs.databricks.com/aws/en/delta/clustering)
