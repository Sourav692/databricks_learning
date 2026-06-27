# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 01 — Spark architecture & the execution model (hands-on)
# MAGIC
# MAGIC **Goal:** *see* how one line of PySpark becomes jobs → stages → tasks across a driver and
# MAGIC executors — and watch a wide dependency create the expensive shuffle.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - **Cluster/runtime:** any current **DBR LTS** (e.g. 12.2 LTS+). **AQE is ON by default**
# MAGIC   (since DBR 7.3 / Spark 3.2). A couple of demos below temporarily turn AQE *off* to show the
# MAGIC   raw "before" plan (the un-coalesced 200 shuffle partitions), then **reset it** — never leave
# MAGIC   AQE off in real work.
# MAGIC - **Unity Catalog** enabled, with permission to create a demo schema (only the optional
# MAGIC   on-disk-partition cell writes a table; everything else uses in-memory `spark.range` data).
# MAGIC - No source data needed — we generate it with `spark.range(...)`.
# MAGIC
# MAGIC ## What you'll learn
# MAGIC - The difference between a **transformation** (lazy) and an **action** (fires a job).
# MAGIC - How a **wide** dependency (`groupBy`/`join`) inserts an `Exchange` (shuffle) and a new stage,
# MAGIC   while **narrow** ops (`filter`/`withColumn`) fuse into one stage.
# MAGIC - That **one task runs per partition**, and how `spark.sql.shuffle.partitions` (200) sets the
# MAGIC   post-shuffle count.
# MAGIC - How to **MEASURE** all of this: `df.explain(mode="formatted")`, `df.rdd.getNumPartitions()`,
# MAGIC   timing with a `noop` sink, and the exact **Spark UI** signal to read.
# MAGIC
# MAGIC ## How to read the result (Spark UI)
# MAGIC Open the Spark UI from the cluster (or the **View** link under a running cell) and use:
# MAGIC - **SQL / DataFrame tab** — the query DAG. Each **`Exchange`** node = a shuffle = a stage boundary.
# MAGIC - **Jobs tab** — one row per action; click a job to see its stages.
# MAGIC - **Stages tab** — **task count** (= partition count) and the **task-time distribution**
# MAGIC   (min/median/max — skew shows as max ≫ median), plus **Shuffle Read/Write** bytes.
# MAGIC - **Executors tab** — how many executors/cores you actually got, and per-executor GC time.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters (Unity Catalog three-level namespacing)
# MAGIC Parameterize `catalog.schema` at the top. Delta is the default format on Databricks — we never
# MAGIC write `USING DELTA`.

# COMMAND ----------

from pyspark.sql.functions import col, when, rand, floor

# Three-level UC namespacing — change these to a catalog/schema you can write to.
catalog = "main"
schema  = "pyspark_perf_demo"

spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")        # no-op if it already exists / managed
spark.sql(f"CREATE SCHEMA  IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")

# Capture original confs so the cleanup cell can restore them exactly.
_orig_shuffle_parts = spark.conf.get("spark.sql.shuffle.partitions")
_orig_aqe           = spark.conf.get("spark.sql.adaptive.enabled")
print("shuffle.partitions =", _orig_shuffle_parts, "| adaptive.enabled =", _orig_aqe)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE — generate demo data in memory
# MAGIC `spark.range(n)` makes an `id` column across several partitions (one task each). We add a
# MAGIC low-cardinality `bucket` key to group/join on later.

# COMMAND ----------

# CREATE: 20M rows. `bucket` (0..99) is a low-cardinality grouping/join key.
events = (spark.range(0, 20_000_000)
                .withColumn("bucket", (col("id") % 100).cast("int"))
                .withColumn("amount", (rand() * 100)))

# MEASURE (partitions -> tasks): how many input partitions does this DataFrame have?
print("input partitions (=> tasks in stage 0):", events.rdd.getNumPartitions())
# Spark UI signal: when an action runs, the FIRST stage will have exactly this many tasks.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Lazy evaluation — transformations build, actions fire
# MAGIC Transformations return a new DataFrame and run **nothing**. Only an **action** submits a job.

# COMMAND ----------

# These are all LAZY — no job runs, the Spark UI > Jobs tab gets no new entries here.
f1 = events.filter("amount > 50")                 # transformation
f2 = f1.withColumn("amt_x2", col("amount") * 2)   # transformation
f3 = f2.select("id", "bucket", "amt_x2")          # transformation
print("Built a 3-step plan — still zero jobs submitted.")

# COMMAND ----------

# ACTION: count() fires exactly ONE job.
# MEASURE (Spark UI): before running, note the Jobs tab count; after, it increases by 1.
n = f3.count()
print("rows after filter:", n, "-> check Spark UI > Jobs: exactly one new job for this count().")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Narrow dependency — MEASURE the plan (no Exchange, single stage)
# MAGIC `filter` + `withColumn` + `select` are narrow: each input partition maps to one output
# MAGIC partition, so they **pipeline into one stage** with no data movement.

# COMMAND ----------

narrow = (events.filter("id > 10")
                .withColumn("x", col("id") * 2)
                .select("id", "x"))

# MEASURE (the plan is the primary evidence in this track):
narrow.explain(mode="formatted")
# WHAT TO LOOK FOR: a Project / Filter / Range chain and **NO `Exchange`** node.
# No Exchange => no shuffle => a single stage. Spark UI > SQL tab shows one stage, no Shuffle Read.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Wide dependency — MEASURE the shuffle appear (Exchange + new stage)
# MAGIC `groupBy(...).count()` is wide: each output group draws from every input partition, so Spark
# MAGIC inserts an `Exchange` (the shuffle) and cuts a new stage.
# MAGIC
# MAGIC We turn **AQE off temporarily** so the raw plan shows the un-coalesced **200** post-shuffle
# MAGIC partitions (`spark.sql.shuffle.partitions`). **This is for demonstration only** — we reset AQE
# MAGIC right after.

# COMMAND ----------

# --- DEMO ONLY: disable AQE so we see the raw 200-partition shuffle (the "before" state). ---
spark.conf.set("spark.sql.adaptive.enabled", "false")

wide = events.groupBy("bucket").count()

# MEASURE (plan): look for the `Exchange hashpartitioning(bucket, 200)` node.
wide.explain(mode="formatted")
# WHAT TO LOOK FOR:
#   HashAggregate (final)
#   +- Exchange hashpartitioning(bucket, 200)   <- THE SHUFFLE = stage boundary
#      +- HashAggregate (partial)  +- Range
# This is two stages: partial aggregate (pre-shuffle) -> Exchange -> final aggregate (post-shuffle).

# COMMAND ----------

# MEASURE (partitions): the post-shuffle DataFrame lands on spark.sql.shuffle.partitions.
print("post-shuffle partitions (=> tasks in the final stage):", wide.rdd.getNumPartitions())
# Expect 200 with AQE off. The final stage in Spark UI > Stages will show ~200 tasks.

# COMMAND ----------

# MEASURE (timing): the `noop` sink runs the FULL job (all stages/tasks) but writes nothing —
# the clean way to time a plan WITHOUT a driver-OOM-risky collect().
import time
t0 = time.time()
wide.write.format("noop").mode("overwrite").save()
print("wide groupBy wall-clock:", round(time.time() - t0, 2), "s")
# Spark UI signal: SQL tab shows the completed query; the Exchange node reports Shuffle Write /
# Shuffle Read bytes — that number is the cost of the wide dependency.

# COMMAND ----------

# --- RESET AQE immediately (demo of the "before" state is done). ---
spark.conf.set("spark.sql.adaptive.enabled", _orig_aqe)
print("AQE reset to:", spark.conf.get("spark.sql.adaptive.enabled"))

# With AQE back ON, re-run the same wide op and MEASURE again: AQE coalesces the 200 tiny
# post-shuffle partitions toward the ~64 MB advisory size (Lesson 05), so the final-stage task
# count drops. Confirm in the plan (AQEShuffleRead) and in Stages (far fewer tasks).
wide_aqe = events.groupBy("bucket").count()
wide_aqe.write.format("noop").mode("overwrite").save()
print("post-shuffle partitions with AQE on:", wide_aqe.rdd.getNumPartitions(),
      "(AQE coalesced — compare to 200 above)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · One task per partition — control the count and watch tasks change
# MAGIC `spark.sql.shuffle.partitions` sets the **post-shuffle** partition count (200 in OSS;
# MAGIC can be `auto` on Databricks). It does **not** change a plain scan's partition count.

# COMMAND ----------

# DEMO ONLY: shrink the post-shuffle partition count to make the effect obvious.
spark.conf.set("spark.sql.shuffle.partitions", 8)
small = events.groupBy("bucket").count()
print("post-shuffle partitions now:", small.rdd.getNumPartitions(),
      "-> the final stage will show 8 tasks in Spark UI > Stages.")

# Reset to the original value.
spark.conf.set("spark.sql.shuffle.partitions", _orig_shuffle_parts)
print("shuffle.partitions reset to:", spark.conf.get("spark.sql.shuffle.partitions"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Equivalent Spark SQL (same engine, same Exchange)
# MAGIC The DataFrame API and SQL compile to the same physical plan — the `GROUP BY` introduces the
# MAGIC same `Exchange`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- MEASURE: read the plan in SQL too. Look for the `Exchange` node from the GROUP BY.
# MAGIC EXPLAIN FORMATTED
# MAGIC SELECT id % 100 AS g, count(*) AS c
# MAGIC FROM   range(20000000)
# MAGIC GROUP  BY id % 100;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · (Optional) On-disk partitions vs in-memory partitions
# MAGIC "Partition" is overloaded. Above we tuned **in-memory** Spark partitions (chunks → tasks).
# MAGIC Writing `PARTITIONED BY` creates **on-disk** directory partitions — a different concept used
# MAGIC for pruning (Lesson 07). This cell just shows the contrast; skip if you only want the engine model.

# COMMAND ----------

# Create a small partitioned Delta table (Delta is the default format — no USING DELTA needed).
(events.limit(100_000)
       .write.mode("overwrite")
       .partitionBy("bucket")                      # on-disk directory per bucket value
       .saveAsTable(f"{catalog}.{schema}.events_partitioned"))

# MEASURE: DESCRIBE EXTENDED shows the partition columns (on-disk layout), NOT task counts.
spark.sql(f"DESCRIBE EXTENDED {catalog}.{schema}.events_partitioned").show(truncate=False)
# Takeaway: this `bucket` is an on-disk partition (a folder), unrelated to the 200 in-memory
# shuffle partitions from section 4. Keep the two meanings straight.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Uses, edge cases & limitations (recap)
# MAGIC - **Uses:** the mental model for reading any Spark UI / `.explain()`; sizing clusters
# MAGIC   (match cores to partitions); choosing client vs cluster deployment mode.
# MAGIC - **Edge cases:** `show()`/`take()` also fire jobs; too few partitions = idle cores + spill,
# MAGIC   too many = scheduling overhead (AQE coalesces the latter); `shuffle.partitions` only affects
# MAGIC   shuffles, not scans.
# MAGIC - **Limitations:** the driver can't scale out (single point of failure — Lesson 03); a required
# MAGIC   shuffle can be avoided or reduced but never made free; lazy evaluation hides cost until the
# MAGIC   action runs the whole plan at once.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Cleanup — drop demo objects & reset every changed conf
# MAGIC Leave no state behind so the notebook is rerunnable.

# COMMAND ----------

# Drop the optional demo table.
spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.events_partitioned")

# Reset every conf we touched back to its original value.
spark.conf.set("spark.sql.shuffle.partitions", _orig_shuffle_parts)
spark.conf.set("spark.sql.adaptive.enabled", _orig_aqe)

# Optional: drop the demo schema entirely (uncomment if you created it just for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")

print("Cleanup complete. shuffle.partitions =", spark.conf.get("spark.sql.shuffle.partitions"),
      "| adaptive.enabled =", spark.conf.get("spark.sql.adaptive.enabled"))
