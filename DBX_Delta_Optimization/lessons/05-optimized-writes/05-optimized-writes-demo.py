# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 05 — Optimized writes
# MAGIC
# MAGIC **Goal:** *See* optimized writes right-size files **at write time**. We write the
# MAGIC same partitioned data twice — once with optimized writes **OFF** and once **ON** —
# MAGIC and measure how the pre-write shuffle turns a swarm of small files into a few
# MAGIC ~128 MB files, using `DESCRIBE DETAIL` (numFiles / sizeInBytes) and `DESCRIBE HISTORY`.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Any current Databricks Runtime (DBR 13.3 LTS+ recommended); serverless is fine.
# MAGIC   - DBR 13.3 LTS+ gives optimized writes for CTAS + INSERT on **partitioned**
# MAGIC     Unity Catalog–registered tables.
# MAGIC - **Unity Catalog** enabled, with `USE CATALOG`/`USE SCHEMA`, `CREATE SCHEMA`,
# MAGIC   and `CREATE TABLE` grants on the target catalog.
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. Optimized writes adds an **extra shuffle before the write** so each partition
# MAGIC    gets **fewer, larger files** (target **128 MB**) — measured OFF vs ON.
# MAGIC 2. It is **most effective for partitioned tables** (per-partition fan-out).
# MAGIC 3. Turn it on via the table property `delta.autoOptimize.optimizeWrite` or the
# MAGIC    session config `spark.databricks.delta.optimizeWrite.enabled`.
# MAGIC 4. It is **always on for MERGE / UPDATE-with-subquery / DELETE-with-subquery**
# MAGIC    (cannot be disabled) — we MERGE and inspect the history.
# MAGIC 5. Why you must **not** `coalesce(n)`/`repartition(n)` right before the write.

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
# MAGIC `DESCRIBE DETAIL` gives `numFiles` / `sizeInBytes`. We print both plus the average
# MAGIC file size so the OFF→ON improvement is obvious. We reuse this to make the numbers move.

# COMMAND ----------

def file_stats(table):
    """Print and return (numFiles, sizeInBytes, avg_mb) via DESCRIBE DETAIL."""
    d = (spark.sql(f"DESCRIBE DETAIL {table}")
              .select("numFiles", "sizeInBytes").first())
    n, b = d["numFiles"], d["sizeInBytes"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    print(f"{table}: numFiles={n}, sizeInBytes={b:,} (~{avg_mb:.2f} MB/file)")
    return n, b, avg_mb

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE the source data (partitioned by region)
# MAGIC We generate a few million rows across 5 regions. Partitioning multiplies the
# MAGIC small-file problem (many tiny files **per partition directory**), which is exactly
# MAGIC the case optimized writes helps most. We reuse this same DataFrame for both writes
# MAGIC so the comparison is fair.

# COMMAND ----------

from pyspark.sql import functions as F

ROWS = 6_000_000  # small enough to run fast; large enough to span many files per region

src = (spark.range(0, ROWS)
    .withColumn("region", F.element_at(
        F.array(*[F.lit(r) for r in ["NA", "EU", "APAC", "LATAM", "MEA"]]),
        (F.col("id") % 5 + 1).cast("int")))                        # 5 partitions
    .withColumn("amount", (F.rand(seed=7) * 6000).cast("double"))
    .withColumn("sale_date", F.expr("date_add(DATE'2026-06-01', cast(id % 30 as int))"))
    .withColumnRenamed("id", "sale_id"))

# Cache so both writes start from identical data (and the rand() values are stable).
src.cache().count()
print("Source rows:", ROWS, "across 5 region partitions")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · STRESS — write with optimized writes OFF (the small-file baseline)
# MAGIC With optimized writes off, each write task emits its own file per partition it
# MAGIC touches, so a partitioned write fans out into **many small files**. We disable the
# MAGIC session config and bump shuffle parallelism so the fan-out is visible.

# COMMAND ----------

# Force the "no optimized writes" baseline for a clear contrast.
spark.conf.set("spark.databricks.delta.optimizeWrite.enabled", "false")
# Raise parallelism so the default write fans out into many files (the small-file problem).
spark.conf.set("spark.sql.shuffle.partitions", "64")

(src.write
    .partitionBy("region")
    .mode("overwrite")
    # NOTE: no .coalesce()/.repartition() — we let the default behavior fan out.
    .saveAsTable(f"{catalog}.{schema}.sales_ow_off"))

n_off, b_off, avg_off = file_stats(f"{catalog}.{schema}.sales_ow_off")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · APPLY — write the SAME data with optimized writes ON
# MAGIC Now we turn optimized writes on (session config). Before the write, an **extra
# MAGIC shuffle** concentrates each partition's rows into a few tasks, each writing one
# MAGIC right-sized file (target **128 MB**). Same data, far fewer files.

# COMMAND ----------

# Enable optimized writes for this session. (Equivalently, set the table property below.)
spark.conf.set("spark.databricks.delta.optimizeWrite.enabled", "true")

(src.write
    .partitionBy("region")
    .mode("overwrite")
    # Still NO manual coalesce/repartition — optimized writes sizes the files for us.
    .saveAsTable(f"{catalog}.{schema}.sales_ow_on"))

n_on, b_on, avg_on = file_stats(f"{catalog}.{schema}.sales_ow_on")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Same switch as a durable TABLE PROPERTY (recommended)
# MAGIC The session config above is per-session. For a table you always want right-sized,
# MAGIC set the property so every future write goes through optimized writes — regardless
# MAGIC of who writes or from what compute.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Durable, per-table switch. Travels with the table.
# MAGIC ALTER TABLE sales_ow_on
# MAGIC   SET TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true');
# MAGIC
# MAGIC -- Confirm it's set:
# MAGIC SHOW TBLPROPERTIES sales_ow_on ('delta.autoOptimize.optimizeWrite');

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · MEASURE — the OFF vs ON file-count win
# MAGIC The same data, the same partitioning — but optimized writes turns a swarm of small
# MAGIC files into a few large ones. The cost was one extra shuffle at write time.

# COMMAND ----------

import pandas as pd

rows = [
    ("optimizeWrite OFF", n_off, round(b_off / (1024 * 1024), 1), round(avg_off, 2)),
    ("optimizeWrite ON",  n_on,  round(b_on  / (1024 * 1024), 1), round(avg_on, 2)),
]
summary = pd.DataFrame(rows, columns=["write", "numFiles", "sizeMB", "avgFileMB"])
display(spark.createDataFrame(summary))

if n_off and n_on:
    print(f"Files reduced from {n_off} -> {n_on} "
          f"({100 * (n_off - n_on) / n_off:.0f}% fewer files); "
          f"avg file size {avg_off:.1f} MB -> {avg_on:.1f} MB.")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The WRITE commits and their operationMetrics (numFiles written, numOutputBytes).
# MAGIC -- Compare the two tables' latest WRITE to see the file-count difference.
# MAGIC DESCRIBE HISTORY sales_ow_on;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · ALWAYS ON for MERGE / UPDATE-with-subquery / DELETE-with-subquery
# MAGIC For these row-level operations optimized writes is **enabled by default and cannot
# MAGIC be disabled** — they rewrite scattered rows across many partitions, the worst
# MAGIC small-file pattern. We build a small updates table and MERGE; even though we never
# MAGIC set any property, the merge's write is reshuffled and right-sized automatically.

# COMMAND ----------

# A handful of updates spread across several regions (the scattered pattern).
updates = (src.where("sale_id % 1000 = 0")              # ~6k rows across all 5 regions
              .withColumn("amount", F.col("amount") + 100.0))
updates.write.mode("overwrite").saveAsTable(f"{catalog}.{schema}.sales_updates")
print("Update rows:", updates.count())

# COMMAND ----------

# MAGIC %sql
# MAGIC -- MERGE gets optimized writes automatically (always on) — no property needed.
# MAGIC -- The pre-write shuffle keeps each touched partition from gaining many tiny files.
# MAGIC MERGE INTO sales_ow_on AS t
# MAGIC USING sales_updates AS s
# MAGIC   ON t.sale_id = s.sale_id
# MAGIC WHEN MATCHED THEN UPDATE SET t.amount = s.amount
# MAGIC WHEN NOT MATCHED THEN INSERT *;

# COMMAND ----------

# MAGIC %md
# MAGIC ### UPDATE / DELETE *with a subquery* also get it by default
# MAGIC The subquery is what triggers the always-on optimized write. (A plain `UPDATE … WHERE
# MAGIC region = 'EU'` without a subquery is not in the always-on set.)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- UPDATE with a subquery predicate -> optimized writes applied automatically.
# MAGIC UPDATE sales_ow_on
# MAGIC   SET amount = amount * 1.01
# MAGIC   WHERE region IN (SELECT region FROM sales_updates WHERE amount > 3000);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Inspect the MERGE/UPDATE commits. Note the table stays right-sized (few files
# MAGIC -- per partition) even after row-level rewrites, thanks to always-on optimized writes.
# MAGIC DESCRIBE HISTORY sales_ow_on;

# COMMAND ----------

n_after, b_after, avg_after = file_stats(f"{catalog}.{schema}.sales_ow_on")
print("After MERGE + UPDATE, the table is still right-sized (no small-file explosion).")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Uses, edge cases & limitations — optimized writes
# MAGIC - **Uses:** partitioned tables; MERGE/UPDATE-with-subquery/DELETE-with-subquery
# MAGIC   (already on); CTAS/INSERT bulk loads (set the property to guarantee it); to
# MAGIC   replace manual `coalesce`/`repartition` tuning.
# MAGIC - **Edge cases:** adding `repartition`/`coalesce` before the write fights the
# MAGIC   mechanism; unpartitioned tables see a smaller benefit; latency-critical streaming
# MAGIC   micro-batches feel the extra shuffle; it does NOT compact files already on disk.
# MAGIC - **Limitations:** write-time only (won't rewrite existing files); always on for
# MAGIC   MERGE/UPDATE/DELETE (can't disable); automatic CTAS/INSERT coverage depends on
# MAGIC   SQL warehouse / DBR 13.3 LTS+ + UC + partitioned; adds an extra shuffle; reduces
# MAGIC   but does NOT replace `OPTIMIZE` (tables > 1 TB still need scheduled OPTIMIZE).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · The anti-pattern — DON'T `coalesce`/`repartition` before the write
# MAGIC Optimized writes already shuffles to size files. A manual `repartition(n)` forces a
# MAGIC second, redundant shuffle with a guessed `n`; `coalesce(n)` can re-create skew and
# MAGIC small files. **Databricks says not to do this when optimized writes is on.** We
# MAGIC demonstrate that `repartition(2)` overrides the sizing (here, too few large files /
# MAGIC skew), versus letting optimized writes decide.

# COMMAND ----------

# ANTI-PATTERN: a guessed repartition overrides optimized writes' sizing.
spark.conf.set("spark.databricks.delta.optimizeWrite.enabled", "true")
(src.repartition(2)                      # do NOT do this when optimized writes is on
    .write.partitionBy("region").mode("overwrite")
    .saveAsTable(f"{catalog}.{schema}.sales_bad_repartition"))
file_stats(f"{catalog}.{schema}.sales_bad_repartition")

# CORRECT: let optimized writes size the files (no manual partition count).
(src.write.partitionBy("region").mode("overwrite")
    .saveAsTable(f"{catalog}.{schema}.sales_good"))
file_stats(f"{catalog}.{schema}.sales_good")

print("\nThe 'good' table's file sizing is driven by the 128 MB target, not a guessed n.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · How it relates to OPTIMIZE and auto compaction
# MAGIC Optimized writes is **write-time** (prevents small files). It does NOT replace:
# MAGIC - **Manual `OPTIMIZE`** (Lesson 04) — bin-pack existing files on demand.
# MAGIC - **Auto compaction** (Lesson 06) — merge small files *after* a write commits.
# MAGIC
# MAGIC They compose: optimized writes reduces how many small files you create; the OFF
# MAGIC baseline table below still benefits from a follow-up `OPTIMIZE`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The OFF-baseline table has many small files. A follow-up OPTIMIZE bin-packs them
# MAGIC -- (this is the REPAIR path optimized writes lets you do less of).
# MAGIC OPTIMIZE sales_ow_off;
# MAGIC DESCRIBE DETAIL sales_ow_off;   -- numFiles drops after compaction

# COMMAND ----------

# MAGIC %md
# MAGIC ## Takeaways
# MAGIC - **Optimized writes adds a shuffle before the write** so each partition gets
# MAGIC   **fewer, larger (~128 MB) files** — it *prevents* the small-file problem.
# MAGIC - **Most effective for partitioned tables** (per-partition fan-out is the worst case).
# MAGIC - **Enable via** `delta.autoOptimize.optimizeWrite` (table property, durable) or
# MAGIC   `spark.databricks.delta.optimizeWrite.enabled` (session config).
# MAGIC - **Always on for MERGE / UPDATE-with-subquery / DELETE-with-subquery** — cannot be
# MAGIC   disabled; also on for CTAS + INSERT on SQL warehouses, and broadly for UC
# MAGIC   partitioned tables on DBR 13.3 LTS+.
# MAGIC - **Don't `coalesce`/`repartition` before the write** — it fights the mechanism.
# MAGIC - **Trade-off:** one extra shuffle (some write latency) for far fewer small files.
# MAGIC - **It complements, not replaces, `OPTIMIZE`** and auto compaction (Lesson 06).
# MAGIC - **Measure with `DESCRIBE DETAIL` (numFiles/sizeInBytes) + `DESCRIBE HISTORY`.**
# MAGIC - **Next:** Lesson 06 — Auto compaction (the post-write cleanup partner).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Cleanup
# MAGIC Drop the demo tables so the notebook is rerunnable.

# COMMAND ----------

src.unpersist()
for name in ["sales_ow_off", "sales_ow_on", "sales_updates",
             "sales_bad_repartition", "sales_good"]:
    spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.{name}")

# Optional: also drop the demo schema (uncomment if you created it solely for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete.")
