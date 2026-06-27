# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 07 — Partition Pruning & Dynamic Partition Pruning (DPP)
# MAGIC
# MAGIC **Goal:** *read fewer files.* Build a partitioned fact table, then watch Spark skip
# MAGIC directories — **statically** when you filter a partition column with a literal, and
# MAGIC **dynamically (DPP)** when you join the partitioned fact to a filtered dimension.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - Any **DBR LTS** cluster (DPP requires Spark 3.0+, included in all current DBR).
# MAGIC - **Unity Catalog** enabled; permission to create a schema + tables in the target catalog.
# MAGIC - **AQE is on by default** (Spark 3.2+ / DBR 7.3+). DPP is also **on by default**
# MAGIC   (`spark.sql.optimizer.dynamicPartitionPruning.enabled=true`). Where a demo needs the
# MAGIC   *before-state*, we set the relevant conf **off**, observe, then **reset it** — and say so loudly.
# MAGIC
# MAGIC ## What you'll learn
# MAGIC - The two meanings of "partition": **on-disk Hive-style** (`PARTITIONED BY`) vs **in-memory** (`shuffle.partitions`).
# MAGIC - How to **create** on-disk partitions with `partitionBy` and confirm with `DESCRIBE EXTENDED`.
# MAGIC - **Static pruning**: a literal filter → `PartitionFilters` in the plan → fewer files read.
# MAGIC - How a **function on the partition column** silently defeats pruning.
# MAGIC - **DPP**: a fact ⋈ filtered dimension → `dynamicpruningexpression` → only matching fact partitions scanned.
# MAGIC
# MAGIC ## How to read the result (this track is about *seeing* the engine)
# MAGIC - **`df.explain(mode="formatted")`** — look on the `Scan` node for
# MAGIC   `PartitionFilters: [col = literal]` (static) or `PartitionFilters: [dynamicpruningexpression(...)]` (DPP).
# MAGIC - **Spark UI → SQL / DataFrame tab** — open the query DAG, click the `Scan`/`PhotonScan`
# MAGIC   node, and read **"number of files read" / "number of partitions read"**. Pruning makes this
# MAGIC   number much smaller than the table's total.
# MAGIC - **Timing** — we trigger jobs with a `df.write.format("noop")` sink (runs the full plan,
# MAGIC   writes nothing) so wall-clock reflects work done, not output I/O.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters — Unity Catalog three-level namespacing
# MAGIC Edit `catalog` / `schema` to a location you can write to. Delta is the default format.

# COMMAND ----------

catalog = "main"
schema  = "pyspark_perf_demo"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")

fact_tbl = f"{catalog}.{schema}.sales"        # partitioned fact
dim_tbl  = f"{catalog}.{schema}.date_dim"     # small dimension (broadcastable)

print("Fact:", fact_tbl)
print("Dim :", dim_tbl)
print("DPP default:", spark.conf.get("spark.sql.optimizer.dynamicPartitionPruning.enabled"))
print("shuffle.partitions (in-memory, NOT pruning):", spark.conf.get("spark.sql.shuffle.partitions"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE — build a partitioned fact + a small dimension
# MAGIC `spark.range(...)` generates rows cheaply. We spread `sales` across **8 quarters** so a
# MAGIC quarter filter should read ~1/8 of the data. The partition column is **low-cardinality**
# MAGIC (`order_quarter`) — exactly what you should partition on.

# COMMAND ----------

from pyspark.sql import functions as F

# 8 quarters across 2 years — a low-cardinality partition column (good choice).
quarters = [f"Q{q}-{y}" for y in (2024, 2025) for q in (1, 2, 3, 4)]
qcol = F.array([F.lit(x) for x in quarters])

sales = (spark.range(0, 8_000_000)                       # 8M rows
              .withColumn("amount", (F.rand() * 1000).cast("double"))
              # assign each row a quarter by id % 8 → evenly spread across 8 partitions
              .withColumn("order_quarter", qcol.getItem((F.col("id") % F.lit(8)).cast("int")))
              .withColumnRenamed("id", "order_id"))

(sales.write
      .mode("overwrite")
      .partitionBy("order_quarter")                      # ← creates /order_quarter=.../ directories
      .saveAsTable(fact_tbl))                            # Delta is the default — no USING DELTA

# A small dimension keyed on the same quarter values (broadcastable → DPP-eligible).
date_dim = (spark.createDataFrame([(q, q.split("-")[0], q.split("-")[1]) for q in quarters],
                                  ["d_quarter", "q_label", "year"]))
date_dim.write.mode("overwrite").saveAsTable(dim_tbl)

print("Rows in fact:", spark.table(fact_tbl).count())
print("Rows in dim :", spark.table(dim_tbl).count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1b · MEASURE — confirm the table really is partitioned on disk
# MAGIC Two different "partition" counts. `DESCRIBE EXTENDED` shows the **on-disk** partition
# MAGIC column; `getNumPartitions()` shows the **in-memory** chunk count. Pruning is about the first.

# COMMAND ----------

# On-disk partition columns (the pruning kind) — look for "Partition Columns: [order_quarter]"
spark.sql(f"DESCRIBE EXTENDED {fact_tbl}").show(60, truncate=False)

# List the actual directories that were created (one per quarter):
spark.sql(f"SHOW PARTITIONS {fact_tbl}").show(truncate=False)

# In-memory partitions (parallelism chunks) — a DIFFERENT concept, unrelated to file pruning:
print("in-memory partitions of the full read:", spark.table(fact_tbl).rdd.getNumPartitions())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · STATIC PRUNING — a literal filter on the partition column
# MAGIC **Apply:** filter `order_quarter` with a literal. **Measure:** the plan must carry a
# MAGIC `PartitionFilters` line, and the Spark UI scan should report ~1/8 of the partitions read.

# COMMAND ----------

one_quarter = spark.table(fact_tbl).where("order_quarter = 'Q4-2025'")

# MEASURE 1 — the plan: look for PartitionFilters: [ ... (order_quarter = Q4-2025)]
one_quarter.explain(mode="formatted")

# COMMAND ----------

import time

# MEASURE 2 — timing via the noop sink (runs the full scan, writes nothing).
# Compare a single-quarter pruned read vs the full-table read.
t0 = time.time(); one_quarter.write.format("noop").mode("overwrite").save()
print("pruned (1 quarter):", round(time.time() - t0, 2), "s")

t0 = time.time(); spark.table(fact_tbl).write.format("noop").mode("overwrite").save()
print("full scan (8 quarters):", round(time.time() - t0, 2), "s")

# SPARK UI: SQL tab → the pruned query's `Scan` node shows "number of partitions read" ≈ 1;
# the full scan shows 8. That ratio is the win.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2b · GOTCHA — a function on the partition column defeats static pruning
# MAGIC `substring(order_quarter, 1, 2)` wraps the column, so Catalyst can't map it to directory
# MAGIC names at plan time → **full scan**. Compare the two plans: the function version has an
# MAGIC empty `PartitionFilters`.

# COMMAND ----------

# ❌ Function on the partition column → no PartitionFilters → all 8 partitions read.
defeated = spark.table(fact_tbl).where("substring(order_quarter, 1, 2) = 'Q4'")
defeated.explain(mode="formatted")    # PartitionFilters: []  (filter shows under Filter/PushedFilters)

# ✅ Fix: filter the raw column with literals / an IN-list instead.
fixed = spark.table(fact_tbl).where("order_quarter IN ('Q4-2024','Q4-2025')")
fixed.explain(mode="formatted")       # PartitionFilters: [order_quarter IN (...)]  → prunes to 2 dirs

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · DYNAMIC PARTITION PRUNING — fact ⋈ filtered dimension
# MAGIC The filter lives on the **dimension** (`d_quarter = 'Q4-2025'`), not the fact, so static
# MAGIC pruning can't fire. **Apply:** join the partitioned fact to the filtered, broadcastable
# MAGIC dimension. **Measure:** the fact-side `Scan` should show a `dynamicpruningexpression`.

# COMMAND ----------

from pyspark.sql.functions import col, broadcast

sales_df = spark.table(fact_tbl)
dim_df   = spark.table(dim_tbl)

# Filter on the DIMENSION — only DPP can prune the fact here.
dpp_query = (sales_df.join(dim_df, sales_df.order_quarter == dim_df.d_quarter)
                     .where(col("d_quarter") == "Q4-2025"))

# MEASURE — the plan: the `sales` Scan's PartitionFilters carries dynamicpruningexpression(...)
# and a SubqueryBroadcast / ReusedExchange feeds it the surviving quarter.
dpp_query.explain(mode="formatted")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3b · A/B — turn DPP OFF to see the before-state (DEMO ONLY, then reset)
# MAGIC With DPP off, the same join reads **all 8** fact partitions and filters after the join.
# MAGIC We disable it, capture the "before" plan/timing, then **re-enable** it immediately.

# COMMAND ----------

import time

# --- DEMONSTRATION ONLY: disable DPP to show the before-state ---
spark.conf.set("spark.sql.optimizer.dynamicPartitionPruning.enabled", "false")
print("DPP now:", spark.conf.get("spark.sql.optimizer.dynamicPartitionPruning.enabled"))

dpp_off = (spark.table(fact_tbl).join(spark.table(dim_tbl),
                                      spark.table(fact_tbl).order_quarter == spark.table(dim_tbl).d_quarter)
                .where(col("d_quarter") == "Q4-2025"))
dpp_off.explain(mode="formatted")     # PartitionFilters: []  → full fact scan ❌
t0 = time.time(); dpp_off.write.format("noop").mode("overwrite").save()
print("DPP OFF:", round(time.time() - t0, 2), "s")

# --- RESET: re-enable DPP (this is the default) ---
spark.conf.set("spark.sql.optimizer.dynamicPartitionPruning.enabled", "true")
print("DPP reset to:", spark.conf.get("spark.sql.optimizer.dynamicPartitionPruning.enabled"))

# COMMAND ----------

# Now time it WITH DPP on — should read ~1/8 of the fact and run faster.
t0 = time.time(); dpp_query.write.format("noop").mode("overwrite").save()
print("DPP ON :", round(time.time() - t0, 2), "s")

# SPARK UI: SQL tab → with DPP on, the `sales` Scan reads ~1 partition; with DPP off it reads 8.
# The SubqueryBroadcast node feeding the fact scan is the visual signature of DPP.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3c · Equivalent in Spark SQL
# MAGIC The DataFrame join above is equivalent to plain SQL — DPP fires the same way. The
# MAGIC dimension is small, so it broadcasts automatically; you can also be explicit with a hint.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Filter on the dimension; DPP prunes the partitioned `sales` fact at runtime.
# MAGIC -- EXPLAIN FORMATTED shows dynamicpruningexpression on the sales Scan node.
# MAGIC EXPLAIN FORMATTED
# MAGIC SELECT /*+ BROADCAST(d) */ s.order_id, s.amount, d.q_label
# MAGIC FROM   sales s
# MAGIC JOIN   date_dim d ON s.order_quarter = d.d_quarter
# MAGIC WHERE  d.d_quarter = 'Q4-2025';

# COMMAND ----------

# MAGIC %md
# MAGIC ## Uses, edge cases & limitations (interview recap)
# MAGIC - **Uses:** date/region-partitioned facts filtered by date/region (static); star-schema
# MAGIC   joins with the filter on a dimension (DPP). Pair with column pruning + predicate pushdown.
# MAGIC - **Edge cases:** a function/UDF on the partition column kills static pruning; DPP needs the
# MAGIC   dimension to be **broadcastable** and the fact **partitioned on the join key**; over-partitioning
# MAGIC   a high-cardinality column creates millions of tiny files + driver metadata pressure.
# MAGIC - **Limitations:** DPP is **not applied to streaming queries**; pruning skips directories only
# MAGIC   (column pruning/pushdown handle within-file trimming); `dynamicPartitionPruning.enabled` is a
# MAGIC   Spark 3.0+ runtime conf (default true). For high-cardinality keys prefer **bucketing** (Lesson 11)
# MAGIC   or Z-ORDER / liquid clustering, not partitioning.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · CLEANUP — drop demo tables/schema and reset any changed conf
# MAGIC Leaves no state behind so the notebook is rerunnable.

# COMMAND ----------

# Reset the one conf this notebook toggled (it's the default; reset is belt-and-suspenders).
spark.conf.set("spark.sql.optimizer.dynamicPartitionPruning.enabled", "true")

# Drop demo objects.
spark.sql(f"DROP TABLE IF EXISTS {fact_tbl}")
spark.sql(f"DROP TABLE IF EXISTS {dim_tbl}")
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")

print("Cleanup complete. DPP enabled:", spark.conf.get("spark.sql.optimizer.dynamicPartitionPruning.enabled"))
