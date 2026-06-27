# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 03 — Data skipping & Z-ordering
# MAGIC
# MAGIC **Goal:** *See* data skipping work — how per-file MIN/MAX/NULL/count stats let the
# MAGIC engine prune files, how a **scattered** layout defeats skipping, and how
# MAGIC `OPTIMIZE … ZORDER BY` colocates rows so skipping becomes aggressive. We measure
# MAGIC every step with `DESCRIBE DETAIL`, `DESCRIBE HISTORY`, and query scan metrics.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Any current Databricks Runtime (DBR 13.3 LTS+ recommended); serverless is fine.
# MAGIC   - `delta.dataSkippingStatsColumns` needs **DBR 13.3 LTS+**.
# MAGIC   - `ANALYZE TABLE … COMPUTE DELTA STATISTICS` needs **DBR 14.3 LTS+**.
# MAGIC   - On older runtimes, fall back to `delta.dataSkippingNumIndexedCols` (all DBR).
# MAGIC - **Unity Catalog** enabled, with `USE CATALOG`/`USE SCHEMA`, `CREATE SCHEMA`,
# MAGIC   and `CREATE TABLE` grants on the target catalog.
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. Per-file stats (MIN/MAX/NULL/count) are collected automatically on write.
# MAGIC 2. How the optimizer prunes files; why a **scattered** layout skips poorly.
# MAGIC 3. `dataSkippingStatsColumns` vs `dataSkippingNumIndexedCols`, and that changing
# MAGIC    them does NOT recompute existing stats (use `ANALYZE … COMPUTE DELTA STATISTICS`).
# MAGIC 4. `OPTIMIZE … ZORDER BY` colocates rows → tight ranges → more files skipped (measured).
# MAGIC 5. Why Databricks recommends **liquid clustering** instead for new tables.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters (Unity Catalog 3-level names)
# MAGIC Edit the widgets, then run top-to-bottom. Everything is namespaced `catalog.schema.table`.

# COMMAND ----------

# Widgets let you point this at any catalog/schema you can write to.
dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "delta_opt_demo", "Schema")

catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")

# Create the schema if needed and set it as the working namespace.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Working in {catalog}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Measurement helpers
# MAGIC `DESCRIBE DETAIL` gives `numFiles` / `sizeInBytes`. To *see skipping*, we also read
# MAGIC the scan metrics from `DESCRIBE HISTORY` after a query, and parse `EXPLAIN` for the
# MAGIC files-pruned line. We reuse these to make the numbers move.

# COMMAND ----------

def file_stats(table):
    """Print and return (numFiles, sizeInBytes) via DESCRIBE DETAIL."""
    d = (spark.sql(f"DESCRIBE DETAIL {table}")
              .select("numFiles", "sizeInBytes").first())
    n, b = d["numFiles"], d["sizeInBytes"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    print(f"{table}: numFiles={n}, sizeInBytes={b:,} (~{avg_mb:.2f} MB/file)")
    return n, b

def scanned_files(table, where_sql):
    """Run a filtered COUNT and report files read from the query's scan metrics.

    We read 'numFiles' (total) vs the files actually scanned, surfaced via the
    Spark query plan. The simplest portable signal is the difference DESCRIBE DETAIL
    numFiles vs. the files the predicate must touch -- we approximate by EXPLAIN FORMATTED,
    which prints 'PartitionFilters'/'DataFilters' and (on Photon/Delta) pruned counts.
    """
    plan = spark.sql(f"EXPLAIN FORMATTED SELECT count(*) FROM {table} WHERE {where_sql}").first()[0]
    # Show the relevant Delta scan lines so the learner can see filters pushed down.
    for line in plan.splitlines():
        if any(tok in line for tok in ("Filter", "files", "Files", "PushedFilters", "ZORDER")):
            print("  ", line.strip())
    return plan

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE + STRESS — a synthetic orders table with a SCATTERED layout
# MAGIC We write data so that each `order_status` value is spread across **many** files
# MAGIC (random row order). This is the worst case for data skipping: every file's
# MAGIC min/max range for `order_status`/`amount` overlaps almost any predicate, so the
# MAGIC engine can't prune. We force several files with `maxRecordsPerFile`.

# COMMAND ----------

from pyspark.sql import functions as F

ROWS = 2_000_000  # small enough to run fast; large enough to span many files

# Scattered: order_status & amount vary row-to-row (no locality), so ranges overlap.
orders = (spark.range(0, ROWS)
    .withColumn("order_status", F.element_at(
        F.array(*[F.lit(s) for s in ["RETURNED", "SHIPPED", "PENDING", "CANCELLED", "REFUNDED"]]),
        (F.col("id") % 5 + 1).cast("int")))                       # 5 statuses, interleaved
    .withColumn("region", F.element_at(
        F.array(*[F.lit(r) for r in ["NA", "EU", "APAC", "LATAM", "MEA"]]),
        (F.col("id") % 5 + 1).cast("int")))
    .withColumn("amount", (F.rand(seed=7) * 6000).cast("double"))  # uniform 0..6000 -> wide ranges
    .withColumn("order_date", F.expr("date_add(DATE'2026-06-01', cast(id % 30 as int))"))
    .withColumn("notes_text", F.expr("repeat(concat('note-', cast(id as string), '-'), 8)")))  # long string

# Force MANY files so per-file ranges are meaningful (scattered across files).
(orders.write
   .option("maxRecordsPerFile", 50_000)   # cap rows/file -> ~40 files for 2M rows
   .mode("overwrite")
   .saveAsTable(f"{catalog}.{schema}.orders_scattered"))

n_scatter, _ = file_stats(f"{catalog}.{schema}.orders_scattered")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · MEASURE the "before" — skipping is poor on a scattered layout
# MAGIC Data skipping is automatic (stats were collected on write), but with a scattered
# MAGIC layout the per-file ranges overlap the predicate, so few files can be pruned.
# MAGIC `EXPLAIN FORMATTED` shows the pushed-down filters; the query plan/scan metrics
# MAGIC reveal how many files are actually read.

# COMMAND ----------

print("Scattered table — filter on order_status='RETURNED':")
_ = scanned_files(f"{catalog}.{schema}.orders_scattered", "order_status = 'RETURNED'")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Run the query; then check DESCRIBE HISTORY (below) for scan metrics.
# MAGIC SELECT count(*) AS returned_rows
# MAGIC FROM orders_scattered
# MAGIC WHERE order_status = 'RETURNED';

# COMMAND ----------

# MAGIC %md
# MAGIC ### Inspect the per-file stats the engine uses
# MAGIC The MIN/MAX/NULL/count stats live in `_delta_log`. `DESCRIBE DETAIL` is the
# MAGIC table-level view; the operation metrics in `DESCRIBE HISTORY` show files read by
# MAGIC the latest commands.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE HISTORY orders_scattered;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Stats columns — explicit list vs the leading-N knob
# MAGIC By default Delta indexes the **first 32 columns** (UC EXTERNAL tables). We narrow
# MAGIC the stats to the columns we actually filter on — and **exclude the long
# MAGIC `notes_text`** column (long strings are truncated during stats collection, so
# MAGIC their min/max barely help skipping).
# MAGIC
# MAGIC > **Key fact:** changing these properties does NOT recompute stats on existing
# MAGIC > files — it only affects future writes. We backfill in the next cell.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- PREFERRED (DBR 13.3 LTS+): name exactly the columns to collect stats on.
# MAGIC -- Supersedes delta.dataSkippingNumIndexedCols. 'notes_text' is omitted on purpose.
# MAGIC ALTER TABLE orders_scattered
# MAGIC   SET TBLPROPERTIES (
# MAGIC     'delta.dataSkippingStatsColumns' = 'order_status, region, amount'
# MAGIC   );

# COMMAND ----------

# MAGIC %md
# MAGIC ### Backfill stats on EXISTING files (DBR 14.3 LTS+)
# MAGIC Because the property change above doesn't touch already-written files, recompute
# MAGIC their stats with `ANALYZE … COMPUTE DELTA STATISTICS` so they too skip on the
# MAGIC newly-chosen columns. (On older runtimes, omit this; the next writes will use the
# MAGIC new config.)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Recompute data-skipping stats on existing files using the current config.
# MAGIC -- Requires DBR 14.3 LTS+. Reads the files but does NOT rewrite them.
# MAGIC ANALYZE TABLE orders_scattered COMPUTE DELTA STATISTICS;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Older-runtime fallback (shown, not required)
# MAGIC On runtimes without `dataSkippingStatsColumns`, use the order-dependent knob.
# MAGIC It indexes the first N columns in schema order:
# MAGIC
# MAGIC ```sql
# MAGIC ALTER TABLE orders_scattered
# MAGIC   SET TBLPROPERTIES ('delta.dataSkippingNumIndexedCols' = '8');  -- all DBR; order-dependent
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ### Uses, edge cases & limitations — data-skipping stats
# MAGIC - **Uses:** always on; tune which columns are indexed via `dataSkippingStatsColumns`
# MAGIC   (DBR 13.3 LTS+) — put hot filter columns in the stats set; exclude long strings.
# MAGIC - **Edge cases:** hot filter column past col 32 (no stats); scattered data (ranges
# MAGIC   overlap → nothing pruned); long free-text column (truncated min/max → weak skip);
# MAGIC   changing props but forgetting that existing files keep their old stats.
# MAGIC - **Limitations:** first-32-cols default on UC EXTERNAL; property changes don't
# MAGIC   recompute existing stats; `ANALYZE … COMPUTE DELTA STATISTICS` needs DBR 14.3 LTS+;
# MAGIC   long strings truncated. UC MANAGED tables: predictive optimization manages stats
# MAGIC   (no 32-col limit) — let the platform do it.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · APPLY — `OPTIMIZE … ZORDER BY` colocates rows for skipping
# MAGIC Now we create a COPY and Z-order it by the columns we filter on. The space-filling
# MAGIC (Z-order) curve colocates rows with similar `order_status`/`region` into the same
# MAGIC files → tight, non-overlapping min/max ranges → far more files become skippable.
# MAGIC
# MAGIC > Z-order columns must have stats; `OPTIMIZE … ZORDER BY` is **NOT idempotent**
# MAGIC > (re-running may rewrite); you **cannot** Z-order a partition column.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Start from the same data so the comparison is fair.
# MAGIC CREATE OR REPLACE TABLE orders_zordered AS SELECT * FROM orders_scattered;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Ensure the Z-order columns are in the stats set first (they need min/max).
# MAGIC ALTER TABLE orders_zordered
# MAGIC   SET TBLPROPERTIES ('delta.dataSkippingStatsColumns' = 'order_status, region, amount');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Compact AND colocate by the dominant filter columns. NOT idempotent.
# MAGIC -- (1-2 dominant columns; effectiveness drops with each extra column.)
# MAGIC OPTIMIZE orders_zordered ZORDER BY (order_status, region);

# COMMAND ----------

# MAGIC %md
# MAGIC ### Same Z-order via the DeltaTable (PySpark) API
# MAGIC Equivalent of the SQL above — handy inside Python pipelines. (Already done in SQL;
# MAGIC shown for reference.)
# MAGIC
# MAGIC ```python
# MAGIC from delta.tables import DeltaTable
# MAGIC dt = DeltaTable.forName(spark, f"{catalog}.{schema}.orders_zordered")
# MAGIC dt.optimize().executeZOrderBy("order_status", "region")   # NOT idempotent
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · MEASURE the "after" — more files skipped on the Z-ordered table
# MAGIC `DESCRIBE HISTORY` shows the OPTIMIZE/ZORDER commit and its metrics (files added/
# MAGIC removed, Z-order stats). The same filter now prunes more files because matching
# MAGIC rows are colocated.

# COMMAND ----------

n_z, _ = file_stats(f"{catalog}.{schema}.orders_zordered")

print("\nZ-ordered table — filter on order_status='RETURNED':")
_ = scanned_files(f"{catalog}.{schema}.orders_zordered", "order_status = 'RETURNED'")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The OPTIMIZE ... ZORDER BY commit + its operationMetrics (zOrderStats, files).
# MAGIC DESCRIBE HISTORY orders_zordered;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Run the same filtered query on the Z-ordered table; compare scan metrics in the
# MAGIC -- Spark UI / query profile against the scattered table from step 2.
# MAGIC SELECT count(*) AS returned_rows
# MAGIC FROM orders_zordered
# MAGIC WHERE order_status = 'RETURNED';

# COMMAND ----------

# MAGIC %md
# MAGIC ### Read the OPTIMIZE metrics programmatically
# MAGIC The latest history row for the Z-ordered table carries the operation metrics that
# MAGIC prove the rewrite happened (files removed/added, zOrderStats).

# COMMAND ----------

hist = (spark.sql(f"DESCRIBE HISTORY {catalog}.{schema}.orders_zordered")
             .select("version", "operation", "operationMetrics")
             .filter("operation = 'OPTIMIZE'")
             .orderBy(F.col("version").desc())
             .first())
if hist:
    print(f"OPTIMIZE version={hist['version']}")
    for k, v in (hist["operationMetrics"] or {}).items():
        print(f"  {k} = {v}")
else:
    print("No OPTIMIZE commit found yet — run the OPTIMIZE cell above.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Uses, edge cases & limitations — Z-ordering
# MAGIC - **Uses:** legacy/external Delta tables filtered on a high-cardinality column you
# MAGIC   won't convert to liquid clustering; Z-order the 1-2 dominant filter columns.
# MAGIC - **Edge cases:** too many Z-order columns (per-column locality drops); Z-order
# MAGIC   columns without stats (no benefit); re-running OPTIMIZE…ZORDER (NOT a no-op).
# MAGIC - **Limitations:** NOT idempotent; cannot Z-order partition columns; columns must
# MAGIC   have stats; **NOT compatible with liquid clustering** — use one or the other.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · The modern fix — liquid clustering instead of Z-order (DBR 13.3+ recommendation)
# MAGIC For NEW tables, Databricks recommends **liquid clustering** (`CLUSTER BY`, Lesson 08)
# MAGIC instead of Z-order: same skipping benefit, **incremental** maintenance, and you can
# MAGIC change keys with **no rewrite**. Do NOT combine `CLUSTER BY` with `ZORDER` — they're
# MAGIC incompatible. Requires **DBR 15.4 LTS+** for the `CLUSTER BY` DDL (GA).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Modern default: liquid clustering on the same filter columns (no ZORDER).
# MAGIC CREATE OR REPLACE TABLE orders_clustered
# MAGIC CLUSTER BY (order_status, region)
# MAGIC AS SELECT * FROM orders_scattered;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Trigger clustering (incremental; cheap to run often). Then inspect the layout.
# MAGIC OPTIMIZE orders_clustered;
# MAGIC DESCRIBE DETAIL orders_clustered;   -- clusteringColumns set; no partitionColumns

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Side-by-side summary — the numbers that matter
# MAGIC Compare file counts and confirm the layouts. The Z-ordered/clustered tables put
# MAGIC matching rows together so the same filter reads fewer files.

# COMMAND ----------

import pandas as pd

rows = []
for name in ["orders_scattered", "orders_zordered", "orders_clustered"]:
    n, b = file_stats(f"{catalog}.{schema}.{name}")
    rows.append((name, n, round(b / (1024 * 1024), 1),
                 round((b / n) / (1024 * 1024), 2) if n else 0))

summary = pd.DataFrame(rows, columns=["table", "numFiles", "sizeMB", "avgFileMB"])
display(spark.createDataFrame(summary))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Takeaways
# MAGIC - **Per-file MIN/MAX/NULL/count stats are collected automatically on write** and
# MAGIC   power data skipping — the engine prunes files whose ranges can't match.
# MAGIC - **Skipping needs locality, not just stats.** A scattered layout makes ranges
# MAGIC   overlap, so little is pruned despite perfect stats.
# MAGIC - **Stats default to the first 32 columns (UC EXTERNAL).** Use
# MAGIC   `dataSkippingStatsColumns` (DBR 13.3 LTS+) to name the columns you filter on and
# MAGIC   exclude long strings; it supersedes `dataSkippingNumIndexedCols`.
# MAGIC - **Property changes don't recompute existing stats** — backfill with
# MAGIC   `ANALYZE … COMPUTE DELTA STATISTICS` (DBR 14.3 LTS+).
# MAGIC - **`OPTIMIZE … ZORDER BY` colocates rows** → tight ranges → more files skipped.
# MAGIC   It's NOT idempotent, can't target partition columns, needs stats on its columns.
# MAGIC - **For new tables, prefer liquid clustering** — incompatible with ZORDER; same
# MAGIC   skipping benefit with incremental maintenance and no-rewrite key changes.
# MAGIC - **Measure with `DESCRIBE DETAIL` + `DESCRIBE HISTORY`** (and the query profile).
# MAGIC - **Next:** Lesson 04 — OPTIMIZE / compaction (bin-packing).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Cleanup
# MAGIC Drop the demo tables so the notebook is rerunnable.

# COMMAND ----------

for name in ["orders_scattered", "orders_zordered", "orders_clustered"]:
    spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.{name}")

# Optional: also drop the demo schema (uncomment if you created it solely for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete.")
