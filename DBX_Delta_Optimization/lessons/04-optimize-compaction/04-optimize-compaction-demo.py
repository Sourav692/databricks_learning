# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 04 — OPTIMIZE / compaction (bin-packing)
# MAGIC
# MAGIC **Goal:** *See* compaction work — deliberately fragment a table into many small
# MAGIC files, then run `OPTIMIZE` and **measure** `numFiles` / `sizeInBytes` drop while the
# MAGIC data (and results) stay identical. We also prove **idempotency** (a second run is a
# MAGIC no-op), scope a rewrite with `WHERE`, and read the `operationMetrics` from history.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Any current Databricks Runtime; serverless is fine.
# MAGIC   - `OPTIMIZE`, `OPTIMIZE … WHERE`, `OPTIMIZE … ZORDER BY` work on all current DBR.
# MAGIC   - `OPTIMIZE … FULL` (force reclustering for liquid-clustering tables) needs **DBR 16.0+**
# MAGIC     — shown as a commented, optional cell so this notebook runs on any runtime.
# MAGIC - **Unity Catalog** enabled, with `USE CATALOG` / `USE SCHEMA`, `CREATE SCHEMA`,
# MAGIC   and `CREATE TABLE` grants on the target catalog.
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. How small files accumulate, and how `OPTIMIZE` bin-packs them into fewer right-sized files.
# MAGIC 2. Measure the effect: `DESCRIBE DETAIL` (numFiles / sizeInBytes) + `DESCRIBE HISTORY` (operationMetrics).
# MAGIC 3. Bin-packing is **idempotent** — a second `OPTIMIZE` rewrites nothing.
# MAGIC 4. Scope a rewrite to a partition subset with `OPTIMIZE … WHERE`.
# MAGIC 5. The DeltaTable API (`optimize().executeCompaction()` / `.where(...)`), and how
# MAGIC    `OPTIMIZE` behaves on liquid-clustering vs partitioned tables.

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
# MAGIC `DESCRIBE DETAIL` gives `numFiles` / `sizeInBytes`; `DESCRIBE HISTORY` gives the
# MAGIC per-operation `operationMetrics`. We reuse these to make the numbers move.

# COMMAND ----------

def file_stats(table, label=""):
    """Print and return (numFiles, sizeInBytes, avg_MB) via DESCRIBE DETAIL."""
    d = (spark.sql(f"DESCRIBE DETAIL {table}")
              .select("numFiles", "sizeInBytes").first())
    n, b = d["numFiles"], d["sizeInBytes"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    tag = f"[{label}] " if label else ""
    print(f"{tag}{table}: numFiles={n}, sizeInBytes={b:,} (~{avg_mb:.2f} MB/file)")
    return n, b, avg_mb

def last_operation_metrics(table):
    """Return (operation, operationMetrics) for the most recent table version."""
    h = (spark.sql(f"DESCRIBE HISTORY {table}")
              .select("version", "operation", "operationMetrics")
              .orderBy("version", ascending=False).first())
    print(f"  latest op: v{h['version']} {h['operation']}")
    # operationMetrics is a map; show the file-movement keys when present.
    m = h["operationMetrics"] or {}
    for k in ("numFilesAdded", "numFilesRemoved", "numRemovedFiles", "numAddedFiles",
              "filesAdded", "filesRemoved", "numBatches", "partitionsOptimized"):
        if k in m:
            print(f"    {k} = {m[k]}")
    return h["operation"], m

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE + STRESS — fragment a table into many small files
# MAGIC We simulate a day of streaming micro-batches by writing the same dataset in **many
# MAGIC tiny appends**, and we cap rows per file with `spark.sql.files.maxRecordsPerFile`
# MAGIC so each write emits several small files. This is exactly the small-file condition
# MAGIC `OPTIMIZE` exists to fix.

# COMMAND ----------

from pyspark.sql import functions as F

events = f"{catalog}.{schema}.events"
spark.sql(f"DROP TABLE IF EXISTS {events}")

# Cap rows per file so each append produces several small files (the stress condition).
spark.conf.set("spark.sql.files.maxRecordsPerFile", 5000)

# Write ~20 tiny appends to mimic streaming micro-batches. Each append commits new files.
EVENT_TYPES = ["click", "view", "purchase", "scroll", "signup"]
for batch in range(20):
    df = (spark.range(0, 20000)
              .withColumn("user_id", (F.rand(seed=batch) * 1_000_000).cast("long"))
              .withColumn("event_type", F.element_at(F.array(*[F.lit(t) for t in EVENT_TYPES]),
                                                     (F.rand(seed=batch + 100) * 5 + 1).cast("int")))
              .withColumn("amount", F.round(F.rand(seed=batch + 7) * 500, 2))
              .withColumn("event_date", F.lit("2026-06-25").cast("date")))
    # First append CREATEs the (managed, Delta-default) table; the rest append to it.
    (df.write.mode("append").saveAsTable(events))

# Reset the per-file cap so it doesn't affect OPTIMIZE's own output sizing.
spark.conf.unset("spark.sql.files.maxRecordsPerFile")

print("Stress writes done.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · MEASURE the "before" — many small files

# COMMAND ----------

before_n, before_b, before_avg = file_stats(events, "before OPTIMIZE")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Same view in SQL: numFiles is high, avg file size is tiny.
# MAGIC DESCRIBE DETAIL IDENTIFIER(:catalog || '.' || :schema || '.events');

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · APPLY — run `OPTIMIZE` (bin-packing)
# MAGIC `OPTIMIZE` compacts the many small files into fewer right-sized files. It returns a
# MAGIC row of file statistics (files added/removed, batches, partitions optimized).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Compact ALL small files into fewer right-sized files (bin-packing).
# MAGIC -- The result row shows the file statistics OPTIMIZE returns.
# MAGIC OPTIMIZE IDENTIFIER(:catalog || '.' || :schema || '.events');

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · MEASURE the "after" — fewer, larger files (data unchanged)

# COMMAND ----------

after_n, after_b, after_avg = file_stats(events, "after OPTIMIZE")

print("\n--- Effect of OPTIMIZE ---")
print(f"numFiles:   {before_n}  ->  {after_n}   ({before_n - after_n} fewer files)")
print(f"avg MB/file: {before_avg:.2f}  ->  {after_avg:.2f}")
print(f"total bytes: {before_b:,}  ->  {after_b:,}   (roughly preserved — same data)")

# Read the operationMetrics for the OPTIMIZE we just ran.
print("\noperationMetrics from DESCRIBE HISTORY:")
last_operation_metrics(events)

# COMMAND ----------

# MAGIC %md
# MAGIC **Sanity check — results don't change.** `OPTIMIZE` makes no data changes, so a
# MAGIC count/aggregation is identical before and after. Snapshot isolation also means any
# MAGIC concurrent readers or streams using this table as a source are never interrupted.

# COMMAND ----------

# Row count and a per-type aggregate are identical to pre-OPTIMIZE values.
print("total rows:", spark.table(events).count())
display(spark.table(events).groupBy("event_type").count().orderBy("event_type"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Bin-packing is IDEMPOTENT — a second run is a no-op
# MAGIC The files are already right-sized, so a second immediate `OPTIMIZE` finds nothing to
# MAGIC merge: `numFiles` is unchanged and (in the history) `numFilesAdded`/`numFilesRemoved`
# MAGIC are 0. Contrast: `OPTIMIZE … ZORDER BY` is **not** idempotent and can rewrite again.

# COMMAND ----------

spark.sql(f"OPTIMIZE {events}")              # second run, immediately after the first
idem_n, _, _ = file_stats(events, "after 2nd OPTIMIZE (expect no change)")
print(f"\nnumFiles after 1st OPTIMIZE = {after_n}; after 2nd = {idem_n} "
      f"-> {'NO-OP (idempotent)' if idem_n == after_n else 'changed'}")
print("Files rewritten by the 2nd run (from history):")
last_operation_metrics(events)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · DeltaTable API — `optimize().executeCompaction()` and `.where(...)`
# MAGIC The Python/Scala DeltaTable API mirrors the SQL command. Here we re-fragment, then
# MAGIC compact via the API. (A Scala equivalent has the same call shape.)

# COMMAND ----------

from delta.tables import DeltaTable

# Re-fragment with a few more tiny appends so the API call has something to compact.
spark.conf.set("spark.sql.files.maxRecordsPerFile", 5000)
for batch in range(5):
    (spark.range(0, 20000)
          .withColumn("user_id", (F.rand(seed=batch + 500) * 1_000_000).cast("long"))
          .withColumn("event_type", F.lit("click"))
          .withColumn("amount", F.round(F.rand(seed=batch + 9) * 500, 2))
          .withColumn("event_date", F.lit("2026-06-25").cast("date"))
          .write.mode("append").saveAsTable(events))
spark.conf.unset("spark.sql.files.maxRecordsPerFile")
file_stats(events, "re-fragmented")

# DeltaTable API equivalent of `OPTIMIZE table_name`.
dt = DeltaTable.forName(spark, events)
dt.optimize().executeCompaction()            # bin-packing (compaction only)
file_stats(events, "after API executeCompaction")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Scope a rewrite to a partition subset — `OPTIMIZE … WHERE`
# MAGIC On a **partitioned** table, `OPTIMIZE … WHERE <partition predicate>` compacts only
# MAGIC the matching partitions (cheaper on big tables). Compaction always happens **within**
# MAGIC each partition — never across partition boundaries.

# COMMAND ----------

events_part = f"{catalog}.{schema}.events_partitioned"
spark.sql(f"DROP TABLE IF EXISTS {events_part}")

# Build a small partitioned table over two dates, fragmented within each partition.
spark.conf.set("spark.sql.files.maxRecordsPerFile", 4000)
for d in ["2026-06-24", "2026-06-25"]:
    for batch in range(6):
        (spark.range(0, 16000)
              .withColumn("user_id", (F.rand(seed=batch) * 1_000_000).cast("long"))
              .withColumn("event_type", F.lit("view"))
              .withColumn("amount", F.round(F.rand(seed=batch) * 500, 2))
              .withColumn("event_date", F.lit(d).cast("date"))
              .write.mode("append")
              .partitionBy("event_date")          # partitioned table (demo only)
              .saveAsTable(events_part))
spark.conf.unset("spark.sql.files.maxRecordsPerFile")
file_stats(events_part, "partitioned, before")

# Compact ONLY the 2026-06-25 partition. The WHERE must reference partition columns.
spark.sql(f"OPTIMIZE {events_part} WHERE event_date = DATE'2026-06-25'")
file_stats(events_part, "after OPTIMIZE WHERE (one partition)")
print("operationMetrics (note: only one partition was optimized):")
last_operation_metrics(events_part)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · (Optional, DBR 16.0+) liquid clustering: `OPTIMIZE` incremental vs `OPTIMIZE FULL`
# MAGIC On a **liquid clustering** table, plain `OPTIMIZE` compacts *and* groups data by the
# MAGIC clustering keys **incrementally**; after first enabling clustering or **changing
# MAGIC keys**, run `OPTIMIZE … FULL` to force a full reclustering of all existing data.
# MAGIC `OPTIMIZE FULL` requires **DBR 16.0+** — left commented so this notebook runs anywhere.
# MAGIC `ZORDER` is **not compatible** with liquid clustering (use one or the other).

# COMMAND ----------

events_lc = f"{catalog}.{schema}.events_clustered"
spark.sql(f"DROP TABLE IF EXISTS {events_lc}")

# Create a liquid-clustering table (CLUSTER BY) and load it. Delta is the default format.
spark.sql(f"""
  CREATE TABLE {events_lc} (
    user_id    BIGINT,
    event_type STRING,
    amount     DOUBLE,
    event_date DATE
  )
  CLUSTER BY (event_type, event_date)
""")
(spark.table(events).select("user_id", "event_type", "amount", "event_date")
      .write.mode("append").saveAsTable(events_lc))

# Plain OPTIMIZE on an LC table = INCREMENTAL: compacts + groups by clustering keys.
spark.sql(f"OPTIMIZE {events_lc}")
file_stats(events_lc, "LC table after incremental OPTIMIZE")

# After CHANGING the clustering keys, force a full recluster of existing data (DBR 16.0+):
# spark.sql(f"ALTER TABLE {events_lc} CLUSTER BY (user_id)")
# spark.sql(f"OPTIMIZE {events_lc} FULL")     # <-- requires DBR 16.0+; one-time after key change
# file_stats(events_lc, "LC table after OPTIMIZE FULL")

# Inspect clustering columns:
display(spark.sql(f"DESCRIBE DETAIL {events_lc}").select("clusteringColumns", "numFiles", "sizeInBytes"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · (Optional) Let the platform run OPTIMIZE — predictive optimization
# MAGIC On **UC managed** tables, predictive optimization runs `OPTIMIZE`/`VACUUM`/`ANALYZE`
# MAGIC automatically when cost-effective (Lesson 09) — no scheduled job. You can also pin the
# MAGIC target file size the rewrite aims for (else it's autotuned ~256 MB→1 GB by table size,
# MAGIC Lesson 07). These are shown commented; enabling PO needs a Premium workspace + region.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Enable predictive optimization at the schema level (UC managed tables) — Lesson 09:
# MAGIC -- ALTER SCHEMA IDENTIFIER(:catalog || '.' || :schema) ENABLE PREDICTIVE OPTIMIZATION;
# MAGIC
# MAGIC -- Optionally pin the target file size OPTIMIZE aims for (else autotuned) — Lesson 07:
# MAGIC -- ALTER TABLE IDENTIFIER(:catalog || '.' || :schema || '.events')
# MAGIC --   SET TBLPROPERTIES ('delta.targetFileSize' = '256mb');
# MAGIC
# MAGIC -- Verify whether predictive optimization is in effect:
# MAGIC DESCRIBE EXTENDED IDENTIFIER(:catalog || '.' || :schema || '.events');

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10 · Uses, edge cases & limitations (recap)
# MAGIC - **Uses:** routine compaction of fragmented tables (streaming sinks, MERGE-heavy CDC);
# MAGIC   `WHERE` to scope big partitioned tables; `ZORDER BY` to compact + improve skipping on
# MAGIC   non-LC tables; `OPTIMIZE FULL` once after enabling/changing LC keys; predictive
# MAGIC   optimization as the modern default on UC managed tables.
# MAGIC - **Edge cases:** safe on streaming sources (snapshot isolation, no result change);
# MAGIC   re-running compaction is a no-op but re-running `ZORDER BY` is not; over-partitioned
# MAGIC   tables keep small files compaction can't merge across boundaries; growing the
# MAGIC   autotuned target does NOT re-optimize existing files (set `delta.targetFileSize`).
# MAGIC - **Limitations:** `OPTIMIZE` is a CPU-heavy rewrite (prefer compute-optimized + SSD);
# MAGIC   `ZORDER` is not idempotent and not compatible with liquid clustering; `OPTIMIZE FULL`
# MAGIC   needs DBR 16.0+; partitioned compaction is within-partition only; predictive
# MAGIC   optimization is UC managed tables only.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11 · Cleanup
# MAGIC Drop the demo tables so this notebook is rerunnable. (Leave the schema in place if
# MAGIC other lessons share it; uncomment the schema drop to remove it entirely.)

# COMMAND ----------

for t in ["events", "events_partitioned", "events_clustered"]:
    spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.{t}")
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup done.")
