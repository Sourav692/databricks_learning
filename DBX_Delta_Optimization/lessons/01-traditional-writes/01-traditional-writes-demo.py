# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 01 — Traditional Writes & the Small-File Problem
# MAGIC
# MAGIC **Goal:** *See* a default Spark/Delta write produce many small files, measure the
# MAGIC damage with `DESCRIBE DETAIL` / `DESCRIBE HISTORY`, and contrast the brittle manual
# MAGIC controls (`coalesce` / `repartition`) with letting the platform size files.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Any current Databricks Runtime (DBR 13.3 LTS+ recommended); serverless is fine.
# MAGIC - **Unity Catalog** enabled. You need `USE CATALOG` / `USE SCHEMA` and
# MAGIC   `CREATE SCHEMA` + `CREATE TABLE` grants on the target catalog.
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. Output files ≈ the number of writing tasks/partitions at write time.
# MAGIC 2. How a shuffle (`spark.sql.shuffle.partitions`) and repeated appends multiply tiny files.
# MAGIC 3. How to *measure* the small-file problem (`numFiles`, `sizeInBytes`, history).
# MAGIC 4. Why `coalesce(n)` / `repartition(n)` are brittle — and what Databricks says to do instead.
# MAGIC 5. What a Delta write really does (Parquet files + an atomic `_delta_log` commit).

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
# MAGIC ## 1 · CREATE — a plain write, ~one file per task
# MAGIC The number of DataFrame partitions decides how many output files a plain write makes.

# COMMAND ----------

# Build a ~1M-row synthetic dataset and FORCE 16 partitions so the link
# "partitions -> files" is unmistakable. repartition(16) here is purely to set up
# the demo condition (NOT a recommended pre-write pattern — see cell 4).
from pyspark.sql import functions as F

df = (spark.range(0, 1_000_000)
        .withColumn("country", F.element_at(F.array(*[F.lit(c) for c in ["IN","US","UK","DE","SG"]]), (F.col("id") % 5 + 1).cast("int")))
        .withColumn("amount", (F.rand() * 1000).cast("double"))
        .repartition(16))   # 16 partitions -> a plain write emits ~16 files

print("DataFrame partitions:", df.rdd.getNumPartitions())

# Plain write. Delta is the default format (no USING DELTA).
df.write.mode("overwrite").saveAsTable(f"{catalog}.{schema}.events_plain")

# COMMAND ----------

# MAGIC %md
# MAGIC ### MEASURE the "before": file count & size
# MAGIC `DESCRIBE DETAIL` is the fastest way to see `numFiles` and `sizeInBytes`.

# COMMAND ----------

def file_stats(table):
    """Return (numFiles, sizeInBytes, avgFileMB) for a Delta table via DESCRIBE DETAIL."""
    d = spark.sql(f"DESCRIBE DETAIL {table}").select("numFiles", "sizeInBytes").first()
    n, b = d["numFiles"], d["sizeInBytes"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    print(f"{table}: numFiles={n}, sizeInBytes={b:,}  (~{avg_mb:.1f} MB/file)")
    return n, b, avg_mb

_ = file_stats(f"{catalog}.{schema}.events_plain")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Same thing in SQL: numFiles, sizeInBytes, format=delta, partition/clustering cols.
# MAGIC DESCRIBE DETAIL events_plain;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · STRESS — a shuffle + repeated appends multiply tiny files
# MAGIC A shuffle resets the partition count to `spark.sql.shuffle.partitions`. Combined with
# MAGIC many small appends, this is how real pipelines accumulate thousands of tiny files.

# COMMAND ----------

# Lower the shuffle width so the demo runs fast but still shows the multiplier effect.
# (Default is historically 200; AQE may coalesce. We use 32 to keep the demo quick.)
print("shuffle.partitions =", spark.conf.get("spark.sql.shuffle.partitions"))
spark.conf.set("spark.sql.shuffle.partitions", "32")

# Create an append target.
spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.events_appends")

from pyspark.sql import functions as F

# Simulate 8 micro-batch appends. Each batch does a groupBy (a SHUFFLE) right before
# the write, so each append emits ~ (post-AQE) files -> they pile up across commits.
for batch in range(8):
    batch_df = (spark.range(0, 50_000)
                  .withColumn("country", F.element_at(
                      F.array(*[F.lit(c) for c in ["IN","US","UK","DE","SG"]]),
                      (F.col("id") % 5 + 1).cast("int")))
                  .groupBy("country").count()          # <-- shuffle
                  .withColumn("batch", F.lit(batch)))
    batch_df.write.mode("append").saveAsTable(f"{catalog}.{schema}.events_appends")

print("Appended 8 micro-batches.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### MEASURE the appends: small files accumulate across commits

# COMMAND ----------

_ = file_stats(f"{catalog}.{schema}.events_appends")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- DESCRIBE HISTORY shows one row per commit. Note the repeated WRITE operations:
# MAGIC -- nothing merged the small files across commits — that's the small-file problem.
# MAGIC DESCRIBE HISTORY events_appends;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · The brittle manual controls — `coalesce(n)` vs `repartition(n)`
# MAGIC The legacy way to cut file count *before* a write. Understand them, but see cell 4
# MAGIC for why Databricks says not to use them when optimized writes is enabled.

# COMMAND ----------

from pyspark.sql import functions as F

base = (spark.range(0, 1_000_000)
          .withColumn("amount", (F.rand() * 1000).cast("double"))
          .repartition(16))

# coalesce(4): NO shuffle — merges neighbors down to 4 partitions -> ~4 files.
# Cheap, but file sizes can be uneven and it can cut upstream parallelism.
base.coalesce(4).write.mode("overwrite").saveAsTable(f"{catalog}.{schema}.events_coalesce")

# repartition(4): FULL shuffle — even redistribution into 4 -> ~4 even files.
# Cleaner sizes, but pays a network shuffle, and the hardcoded 4 goes stale as data grows.
base.repartition(4).write.mode("overwrite").saveAsTable(f"{catalog}.{schema}.events_repartition")

print("Wrote coalesce(4) and repartition(4) variants.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### MEASURE: both land ~4 files, but you had to *guess* the 4

# COMMAND ----------

_ = file_stats(f"{catalog}.{schema}.events_coalesce")
_ = file_stats(f"{catalog}.{schema}.events_repartition")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Uses, edge cases & limitations — `coalesce` / `repartition`
# MAGIC - **Uses:** OSS/non-Databricks jobs without optimized writes; a deliberate small single-file export (`coalesce(1)`).
# MAGIC - **Edge cases:** `repartition(1)` on a *growing* table eventually writes a multi-GB file (or OOMs);
# MAGIC   `coalesce(1)` before a wide transform can serialize the **whole** stage, not just the write.
# MAGIC - **Limitations:** you must hand-pick `n`; it does not adapt to data growth. A plain write does
# MAGIC   **no** automatic file sizing — only optimized writes / auto compaction / OPTIMIZE resize files.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · APPLY the recommended path — let the platform size files
# MAGIC **Databricks guidance:** do **not** `coalesce`/`repartition` before a write when optimized
# MAGIC writes is enabled. Optimized writes shuffles to a ~128 MB target so you don't guess `n`.
# MAGIC (Deep dive in Lesson 05 — here we just contrast the result.)

# COMMAND ----------

from pyspark.sql import functions as F

# Enable optimized writes for this session (Lesson 05 covers the table-property form).
spark.conf.set("spark.databricks.delta.optimizeWrite.enabled", True)

ow = (spark.range(0, 1_000_000)
        .withColumn("amount", (F.rand() * 1000).cast("double"))
        .repartition(16))   # even with 16 partitions, optimized writes resizes on write

# NOTE: no coalesce/repartition right before .write — optimized writes handles sizing.
ow.write.mode("overwrite").saveAsTable(f"{catalog}.{schema}.events_optimized")

_ = file_stats(f"{catalog}.{schema}.events_optimized")

# COMMAND ----------

# MAGIC %md
# MAGIC ### MEASURE: side-by-side file counts
# MAGIC One small table is fine either way; the difference compounds across thousands of appends.

# COMMAND ----------

import pandas as pd

rows = []
for name in ["events_plain", "events_appends", "events_coalesce",
             "events_repartition", "events_optimized"]:
    n, b, avg = file_stats(f"{catalog}.{schema}.{name}")
    rows.append((name, n, round(b / (1024 * 1024), 1), round(avg, 1)))

summary = pd.DataFrame(rows, columns=["table", "numFiles", "sizeMB", "avgFileMB"])
display(spark.createDataFrame(summary))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · What a Delta write actually committed
# MAGIC A Delta write = Parquet files + an atomic JSON commit to `_delta_log`. The log — not the
# MAGIC directory listing — is the source of truth. `DESCRIBE HISTORY` reads that log.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- One row per commit. operationMetrics shows numFiles / numOutputRows per write.
# MAGIC DESCRIBE HISTORY events_optimized;

# COMMAND ----------

# MAGIC %md
# MAGIC ### `spark.sql.files.maxRecordsPerFile` — a ceiling, not a target
# MAGIC Caps **rows** per file (a guardrail against one giant file). `0`/negative = no limit.
# MAGIC It bounds rows, not bytes — set too low it *creates* small files.

# COMMAND ----------

from pyspark.sql import functions as F

# Per-write option form (doesn't change the session config). Cap = 100k rows/file.
(spark.range(0, 1_000_000)
   .withColumn("amount", (F.rand() * 1000).cast("double"))
   .write.option("maxRecordsPerFile", 100_000)
   .mode("overwrite").saveAsTable(f"{catalog}.{schema}.events_capped"))

# 1,000,000 rows / 100,000 cap -> ~10 files (the cap forced the rollover).
_ = file_stats(f"{catalog}.{schema}.events_capped")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Takeaways
# MAGIC - **Output files ≈ writing tasks/partitions.** A shuffle resets that to `spark.sql.shuffle.partitions`.
# MAGIC - **Appends never self-merge** — small files pile up across commits until you OPTIMIZE / auto-compact.
# MAGIC - **`coalesce`/`repartition` are brittle** (guess `n`, goes stale). Don't use them before a write
# MAGIC   when optimized writes is on.
# MAGIC - **Measure with `DESCRIBE DETAIL` (numFiles/sizeInBytes) + `DESCRIBE HISTORY`** — read the log, not the folder.
# MAGIC - **Next:** Lesson 02 (Partitioning), then OPTIMIZE, optimized writes, auto compaction, liquid clustering.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Cleanup
# MAGIC Drop the demo tables so the notebook is rerunnable. (Drop the schema too if you created it just for this.)

# COMMAND ----------

for name in ["events_plain", "events_appends", "events_coalesce",
             "events_repartition", "events_optimized", "events_capped"]:
    spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.{name}")

# Optional: also drop the demo schema (uncomment if you created it solely for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")

# Reset the shuffle width we lowered for the demo.
spark.conf.unset("spark.sql.shuffle.partitions")
print("Cleanup complete.")
