# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 02 — Partitioning (and when NOT to)
# MAGIC
# MAGIC **Goal:** *See* how a low-cardinality partition column produces a few healthy
# MAGIC partitions, how a high-cardinality column causes a **partition explosion** of tiny
# MAGIC files, and why Databricks recommends **liquid clustering** instead for new tables.
# MAGIC We measure every step with `DESCRIBE DETAIL` (numFiles / sizeInBytes /
# MAGIC partitionColumns) and `DESCRIBE HISTORY`.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Any current Databricks Runtime (DBR 13.3 LTS+ recommended); serverless is fine.
# MAGIC   - Liquid-clustering DDL (`CLUSTER BY`) needs **DBR 15.4 LTS+** (GA).
# MAGIC   - `ALTER TABLE … REPLACE PARTITIONED BY WITH CLUSTER BY` needs **DBR 18.1+** (shown, not run).
# MAGIC - **Unity Catalog** enabled, with `USE CATALOG`/`USE SCHEMA`, `CREATE SCHEMA`,
# MAGIC   and `CREATE TABLE` grants on the target catalog.
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. `PARTITIONED BY` syntax and how partition pruning reads only matching folders.
# MAGIC 2. A **good** (low-cardinality) vs a **bad** (high-cardinality) partition column — measured.
# MAGIC 3. Why partition explosion creates tiny files, and the ≥ 1 GB / < 1 TB rules.
# MAGIC 4. Ingestion-time clustering: the no-tuning default for unpartitioned tables.
# MAGIC 5. The modern fix — liquid clustering (`CLUSTER BY`) instead of partitioning.

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
# MAGIC ### Measurement helper
# MAGIC `DESCRIBE DETAIL` is the fastest way to see `numFiles`, `sizeInBytes`, and
# MAGIC `partitionColumns`. We reuse this helper throughout to *see the numbers move*.

# COMMAND ----------

def file_stats(table):
    """Print and return (numFiles, sizeInBytes, partitionColumns) via DESCRIBE DETAIL."""
    d = (spark.sql(f"DESCRIBE DETAIL {table}")
              .select("numFiles", "sizeInBytes", "partitionColumns").first())
    n, b, pcols = d["numFiles"], d["sizeInBytes"], d["partitionColumns"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    print(f"{table}: numFiles={n}, sizeInBytes={b:,} (~{avg_mb:.2f} MB/file), partitionColumns={pcols}")
    return n, b, pcols

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE — a synthetic events dataset
# MAGIC One dataset, several columns of differing **cardinality** so we can partition it
# MAGIC different ways and compare:
# MAGIC - `region` — ~5 distinct values (LOW cardinality → a good partition candidate)
# MAGIC - `event_date` — ~30 distinct values here (low/known cardinality)
# MAGIC - `customer_id` — thousands of distinct values (HIGH cardinality → bad to partition)

# COMMAND ----------

from pyspark.sql import functions as F

ROWS = 600_000  # small enough to run fast; large enough to show the file multiplier

events = (spark.range(0, ROWS)
    .withColumn("region", F.element_at(
        F.array(*[F.lit(c) for c in ["NA", "EU", "APAC", "LATAM", "MEA"]]),
        (F.col("id") % 5 + 1).cast("int")))                       # ~5 values
    .withColumn("event_date", F.expr("date_add(DATE'2026-06-01', cast(id % 30 as int))"))  # ~30 days
    .withColumn("customer_id", (F.col("id") % 4000))              # ~4,000 values (HIGH)
    .withColumn("amount", (F.rand() * 1000).cast("double")))

events.createOrReplaceTempView("events_src")
print("Distinct counts (cardinality):")
display(events.agg(
    F.countDistinct("region").alias("region"),
    F.countDistinct("event_date").alias("event_date"),
    F.countDistinct("customer_id").alias("customer_id")))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · GOOD partition key — low cardinality (`region`)
# MAGIC Partitioning on a low-cardinality column yields a **few** folders, each holding
# MAGIC enough data to stay reasonably sized. (On a real ≥ 1 TB table these would each be ≥ 1 GB.)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Delta is the default format (no USING DELTA). Partition by a LOW-cardinality column.
# MAGIC CREATE OR REPLACE TABLE events_by_region
# MAGIC PARTITIONED BY (region)
# MAGIC AS SELECT * FROM events_src;

# COMMAND ----------

# MEASURE: few partitions (~5), modest file count.
n_good, _, p_good = file_stats(f"{catalog}.{schema}.events_by_region")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Partition pruning in action: this reads only the region='EU' folder.
# MAGIC -- (Look at the query plan / scan stats — only one partition is touched.)
# MAGIC SELECT count(*) AS eu_rows FROM events_by_region WHERE region = 'EU';

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · BAD partition key — high cardinality (`customer_id`)
# MAGIC Partitioning on a high-cardinality column creates **one folder per value** — a
# MAGIC **partition explosion**. Each folder holds almost nothing → many tiny files.
# MAGIC Watch `numFiles` jump and average file size collapse.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- ANTI-PATTERN (for teaching): partition by a HIGH-cardinality column.
# MAGIC -- This produces ~one folder per customer_id and a tiny file in each.
# MAGIC CREATE OR REPLACE TABLE events_by_customer
# MAGIC PARTITIONED BY (customer_id)
# MAGIC AS SELECT * FROM events_src;

# COMMAND ----------

# MEASURE: thousands of partitions, file count explodes, avg file size tiny.
n_bad, _, p_bad = file_stats(f"{catalog}.{schema}.events_by_customer")

print(f"\nGood key (region):    {n_good} files, partitionColumns={p_good}")
print(f"Bad key (customer_id): {n_bad} files, partitionColumns={p_bad}")
print(f"--> {n_bad}/{max(n_good,1)} = ~{n_bad/max(n_good,1):.0f}x more files from the WRONG partition column.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Uses, edge cases & limitations — partitioning
# MAGIC - **Uses:** very large (≥ 1 TB) tables filtered on a LOW/known-cardinality column
# MAGIC   (date, region), each partition ≥ 1 GB; legacy/external tables already partitioned.
# MAGIC - **Edge cases:** high-cardinality key (`customer_id`, `timestamp`) → partition explosion;
# MAGIC   partitioning a < 1 TB table → tiny partitions; partition < 1 GB → folder-level small files;
# MAGIC   Z-order can't cross partitions and can't target a partition column.
# MAGIC - **Limitations:** partition column must be **top-level** (no struct fields / complex types);
# MAGIC   the choice is **fixed at creation** (changing = full rewrite, or `REPLACE PARTITIONED BY
# MAGIC   WITH CLUSTER BY` on DBR 18.1+); **not compatible** with liquid clustering; Hive-style
# MAGIC   directory layout is **not** part of the Delta protocol (read the `_delta_log`, not folders).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · The < 1 TB / ≥ 1 GB rules — don't fragment small tables
# MAGIC Databricks says **don't partition tables < 1 TB** and aim for **≥ 1 GB per
# MAGIC partition**. Our demo table is tiny, so even the "good" partitioning above splits
# MAGIC it into partitions far under 1 GB — confirming that on a *small* table, NOT
# MAGIC partitioning is best.

# COMMAND ----------

GB = 1024 * 1024 * 1024
detail = spark.sql(f"DESCRIBE DETAIL {catalog}.{schema}.events_by_region") \
              .select("sizeInBytes").first()
total_bytes = detail["sizeInBytes"]
num_parts = spark.table(f"{catalog}.{schema}.events_by_region") \
                 .select("region").distinct().count()
avg_part_mb = (total_bytes / num_parts) / (1024 * 1024) if num_parts else 0

print(f"Table size: {total_bytes/(1024*1024):.1f} MB across {num_parts} partitions")
print(f"Avg partition: ~{avg_part_mb:.1f} MB  (target is >= 1024 MB / 1 GB)")
print("Verdict:", "OK" if avg_part_mb >= 1024 else
      "Each partition is FAR under 1 GB -> on a real small table, do NOT partition.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · The no-tuning default — leave it UNPARTITIONED (ingestion-time clustering)
# MAGIC On DBR 11.3 LTS+, unpartitioned tables are auto-clustered by **ingestion time**,
# MAGIC giving a date-partition-like benefit with zero tuning. For our small table this
# MAGIC is the recommended layout: fewer, larger files and no partition columns to maintain.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- No PARTITIONED BY: ingestion-time clustering + data skipping do the work.
# MAGIC CREATE OR REPLACE TABLE events_unpartitioned
# MAGIC AS SELECT * FROM events_src;

# COMMAND ----------

# MEASURE: no partitionColumns, far fewer files than the customer_id explosion.
_ = file_stats(f"{catalog}.{schema}.events_unpartitioned")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · APPLY the modern fix — liquid clustering (`CLUSTER BY`)
# MAGIC For new tables, Databricks recommends **liquid clustering** instead of
# MAGIC partitioning. It colocates by the keys you actually filter on — **including the
# MAGIC high-cardinality `customer_id`** that was disastrous to partition — with **no
# MAGIC folder explosion** and right-sized files. Requires **DBR 15.4 LTS+**.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Liquid clustering on the SAME high-cardinality column that exploded as a partition.
# MAGIC -- No directories per value; you can redefine these keys later with NO rewrite.
# MAGIC -- (Do NOT combine CLUSTER BY with PARTITIONED BY / ZORDER.)
# MAGIC CREATE OR REPLACE TABLE events_clustered
# MAGIC CLUSTER BY (customer_id, region)
# MAGIC AS SELECT * FROM events_src;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Trigger clustering (incremental; cheap to run often).
# MAGIC OPTIMIZE events_clustered;

# COMMAND ----------

# MEASURE: clustered on a HIGH-cardinality key, yet far fewer files than the
# partition-explosion table -- and no partitionColumns.
n_clu, _, _ = file_stats(f"{catalog}.{schema}.events_clustered")
print(f"\ncustomer_id as PARTITION: {n_bad} files (partition explosion)")
print(f"customer_id as CLUSTER BY: {n_clu} files (no explosion) "
      f"-> ~{n_bad/max(n_clu,1):.0f}x fewer files with the modern layout.")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Confirm the layout: clusteringColumns set, partitionColumns empty.
# MAGIC DESCRIBE DETAIL events_clustered;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Converting a partitioned table to liquid clustering (DBR 18.1+)
# MAGIC The migration path off a bad/legacy partition layout, shown for reference.
# MAGIC Run only on DBR 18.1+ (it will error on older runtimes):
# MAGIC
# MAGIC ```sql
# MAGIC ALTER TABLE events_by_customer
# MAGIC   REPLACE PARTITIONED BY WITH CLUSTER BY (customer_id, region);
# MAGIC OPTIMIZE events_by_customer FULL;   -- recluster existing data to the new keys
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Side-by-side summary — the numbers that matter
# MAGIC numFiles tells the story: the high-cardinality **partition** explodes; the
# MAGIC high-cardinality **cluster** does not.

# COMMAND ----------

import pandas as pd

rows = []
for name in ["events_by_region", "events_by_customer",
             "events_unpartitioned", "events_clustered"]:
    n, b, pcols = file_stats(f"{catalog}.{schema}.{name}")
    rows.append((name, n, round(b / (1024 * 1024), 1),
                 round((b / n) / (1024 * 1024), 2) if n else 0,
                 str(pcols)))

summary = pd.DataFrame(rows, columns=["table", "numFiles", "sizeMB", "avgFileMB", "partitionColumns"])
display(spark.createDataFrame(summary))

# COMMAND ----------

# MAGIC %sql
# MAGIC -- DESCRIBE HISTORY shows the CREATE/WRITE and the OPTIMIZE (clustering) commit.
# MAGIC DESCRIBE HISTORY events_clustered;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Takeaways
# MAGIC - **Low/known-cardinality (region, date) = good partition key; high-cardinality
# MAGIC   (customer_id, timestamp) = partition explosion → tiny files.**
# MAGIC - **Don't partition tables < 1 TB; keep each partition ≥ 1 GB.** Small tables are
# MAGIC   best left unpartitioned (ingestion-time clustering handles recency).
# MAGIC - **Partition columns are top-level only; the choice is fixed at creation;
# MAGIC   partitioning is not compatible with liquid clustering.**
# MAGIC - **For new tables, prefer `CLUSTER BY`** — it colocates by high-cardinality keys
# MAGIC   with no folder explosion and lets you change keys with no rewrite.
# MAGIC - **Measure with `DESCRIBE DETAIL` (numFiles / partitionColumns) + `DESCRIBE HISTORY`** —
# MAGIC   read the log, not the folder.
# MAGIC - **Next:** Lesson 03 — Data skipping & Z-ordering (why colocation makes queries fast).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Cleanup
# MAGIC Drop the demo tables so the notebook is rerunnable.

# COMMAND ----------

for name in ["events_by_region", "events_by_customer",
             "events_unpartitioned", "events_clustered"]:
    spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.{name}")

# Optional: also drop the demo schema (uncomment if you created it solely for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete.")
