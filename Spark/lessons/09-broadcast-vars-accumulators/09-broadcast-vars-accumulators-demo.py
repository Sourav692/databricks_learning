# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 09 — Broadcast variables & accumulators (hands-on)
# MAGIC
# MAGIC **Goal:** *see* the two Spark shared variables behave — a **broadcast variable** shipped
# MAGIC once per executor and read inside a UDF, and an **accumulator** that workers `.add()` to
# MAGIC while only the driver reads `.value` — and prove the **exactly-once-only-in-actions** caveat.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - **Cluster/runtime:** any current **DBR LTS** on a **classic single-user cluster** where the
# MAGIC   `SparkContext` (`sc`) is accessible. ⚠️ `sc.broadcast` / `sc.accumulator` are classic-`SparkContext`
# MAGIC   APIs and are **not supported on Spark Connect** — they will not work on **serverless /
# MAGIC   shared-access** clusters. On those, use a broadcast *join* hint and DataFrame aggregations instead.
# MAGIC - **AQE** is **on by default** (DBR 7.3+ / Spark 3.2+). This lesson is about shared variables,
# MAGIC   not joins, so we leave AQE alone — no before/after conf flip is needed here.
# MAGIC - **Unity Catalog** enabled; permission to create a schema in the target catalog. We use a
# MAGIC   tiny demo table; in-memory DataFrames carry most of the work.
# MAGIC
# MAGIC ## What you'll learn
# MAGIC - Build a broadcast variable, read it in a UDF, and confirm it ships **once per executor**.
# MAGIC - Tell a broadcast **variable** apart from a broadcast **join** (Lesson 02).
# MAGIC - Use an accumulator inside an **action** (`foreachPartition`) for an exactly-once count.
# MAGIC - Reproduce the **double-count** when an accumulator update sits inside a **transformation**.
# MAGIC - Read the right **Spark UI** signals for each.
# MAGIC
# MAGIC ## How to read the result (Spark UI)
# MAGIC - **Broadcast variable** → **Storage** tab: one **Broadcast** block sized ~your value
# MAGIC   (NOT × number of tasks). It is *not* a `.explain()` plan node — it's runtime state.
# MAGIC - **Accumulator** → PySpark's `sc.accumulator(0)` is **unnamed** and is typically **not surfaced
# MAGIC   by name** in the Spark UI Stages → Accumulators view (there is no `sc.longAccumulator(name)` in
# MAGIC   PySpark — that's a Scala/Java-only API). Verify the count by comparing `acc.value` against a real
# MAGIC   aggregation (`filter().count()`), which this notebook does below.
# MAGIC - We also time actions with the `noop` sink and print `getNumPartitions()` so partition/task
# MAGIC   counts are explicit.
# MAGIC
# MAGIC ## Databricks single-user execution note
# MAGIC This lesson specifically requires a **classic single-user** cluster because it uses
# MAGIC `SparkContext` APIs (`sc.broadcast`, `sc.accumulator`). On serverless / shared-access
# MAGIC clusters, use DataFrame-native broadcast joins and aggregations instead. Each action below
# MAGIC creates a Spark UI job; accumulator examples call that out because repeated actions can
# MAGIC intentionally double-count.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Setup — Unity Catalog namespacing (parameterized at the top)

# COMMAND ----------

# Three-level UC namespacing — change these to an existing catalog/schema you can write to.
catalog = "main"
schema  = "pyspark_perf_demo"
table   = "orders_09"
fqn     = f"{catalog}.{schema}.{table}"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print("Using:", f"{catalog}.{schema}", "| table:", fqn)

LESSON_ID = "Lesson 09 - Broadcast variables and accumulators"

def mark_action(label):
    """Label the next Spark action in the Spark UI for tutorial walkthroughs."""
    spark.sparkContext.setJobGroup(f"{LESSON_ID}: {label}", f"{LESSON_ID}: {label}", True)
    print(f"\nACTION -> {label}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## CREATE — a large fact DataFrame with a low-cardinality lookup key
# MAGIC We generate ~20M `orders` rows tagged with a `country_code` drawn from a small set, plus a
# MAGIC small `amount` column (some nulls) so we have malformed rows to count later.

# COMMAND ----------

from pyspark.sql import functions as F

N = 20_000_000  # 20M rows — enough to spread across many tasks
codes = ["US", "CA", "DE", "IN", "BR", "JP", "GB", "AU"]

orders = (
    spark.range(N)
    .withColumn("country_code", F.element_at(F.array(*[F.lit(c) for c in codes]),
                                             (F.col("id") % len(codes) + 1).cast("int")))
    # ~1% of rows have a NULL amount → these are our "bad rows" to count via an accumulator
    .withColumn("amount", F.when(F.rand(seed=7) < 0.01, F.lit(None)).otherwise(F.rand(seed=1) * 100))
)

print("in-memory partitions of orders:", orders.rdd.getNumPartitions())  # one task per partition
mark_action("materialize generated orders")
orders.write.format("noop").mode("overwrite").save()  # materialize once to fix the row count
display(orders.limit(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## BROADCAST VARIABLE — ship a small lookup once per executor
# MAGIC The lookup maps `country_code → region`. Referencing the dict directly in a UDF would
# MAGIC serialize it **per task**; wrapping it in `sc.broadcast(...)` ships it **once per executor**
# MAGIC and every task reads the same `bv.value`.

# COMMAND ----------

from pyspark.sql.types import StringType

# DRIVER: build the small lookup once, then broadcast it (read-only).
region_of = {"US": "NA", "CA": "NA", "DE": "EU", "GB": "EU",
             "IN": "APAC", "JP": "APAC", "AU": "APAC", "BR": "LATAM"}
bv = sc.broadcast(region_of)          # shipped ONCE per executor (NOT once per task)

# WORKER: each task reads the SAME cached copy via bv.value — no per-task re-send.
@F.udf(StringType())
def to_region(code):
    return bv.value.get(code, "UNKNOWN")   # bv.value is READ-ONLY on the worker

regions = orders.withColumn("region", to_region("country_code"))
display(regions.groupBy("region").count())

# COMMAND ----------

# MAGIC %md
# MAGIC ### MEASURE — where to look
# MAGIC - A broadcast **variable** is runtime state, **not** a `.explain()` plan node — the plan below
# MAGIC   just shows the Python UDF (`BatchEvalPython`/`ArrowEvalPython`), not the broadcast.
# MAGIC - **Spark UI signal:** open the **Storage** tab while/after this runs → you should see a single
# MAGIC   **Broadcast** block sized ~the dict, **not** multiplied by the number of tasks.

# COMMAND ----------

# The UDF appears in the plan; the broadcast itself is runtime state (see the Storage tab).
regions.explain(mode="formatted")

import time
t0 = time.time()
regions.write.format("noop").mode("overwrite").save()  # time the full pass (noop = no real write)
print(f"UDF + broadcast pass: {time.time()-t0:.1f}s  | tasks ≈ {regions.rdd.getNumPartitions()} partitions")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Release the broadcast (it pins executor Storage memory)
# MAGIC `unpersist()` drops the cached copies (Spark re-broadcasts if you use `bv` again);
# MAGIC `destroy()` removes it permanently (using it afterwards raises an error).

# COMMAND ----------

bv.unpersist()      # drop cached copies on executors; re-broadcast on next use
# bv.destroy()      # … or remove permanently — DON'T read bv.value after this
print("broadcast released")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Broadcast VARIABLE vs broadcast JOIN — keep them apart
# MAGIC Same word, two mechanisms. The variable is a value you read in code; the **join** is a
# MAGIC strategy the optimizer applies — and *that* one shows up in `.explain()` as `BroadcastHashJoin`.

# COMMAND ----------

from pyspark.sql.functions import broadcast  # NOTE: this broadcast() takes a DataFrame, not a dict

# A small region dimension table to JOIN (contrast with the dict we broadcast above).
region_df = spark.createDataFrame(
    [(c, r) for c, r in region_of.items()], ["country_code", "region"]
)

# Broadcast JOIN: ship the small *table*, skip the big-side shuffle. This IS a plan node.
joined = orders.join(broadcast(region_df), on="country_code", how="inner")

# VERIFY: the plan shows BroadcastHashJoin + BroadcastExchange on region_df, no Exchange on orders.
joined.explain(mode="formatted")
# Compare the two APIs:
#   sc.broadcast(region_of)   -> broadcast VARIABLE (plain dict), read via bv.value in a UDF
#   broadcast(region_df)      -> broadcast JOIN hint (a DataFrame), a BroadcastHashJoin plan node

# COMMAND ----------

# MAGIC %md
# MAGIC ## ACCUMULATOR — count bad rows the SAFE way (inside an action)
# MAGIC We count rows with a NULL `amount`. Doing it inside `foreachPartition` (an **action**) means
# MAGIC each task's update is applied **exactly once** — the doc-grounded guarantee. PySpark's only
# MAGIC accumulator constructor is `sc.accumulator(value)` (unnamed); we confirm the total against a
# MAGIC real aggregation rather than reading it by name in the UI.

# COMMAND ----------

# DRIVER: the PySpark accumulator constructor. NOTE: sc.accumulator(0) produces an UNNAMED
# accumulator — PySpark has no sc.longAccumulator(name) (that's a Scala/Java-only API), so
# Python accumulators are typically NOT surfaced by name in the Spark UI. We verify the count
# below by cross-checking against a real aggregation instead.
bad_rows = sc.accumulator(0)

def count_bad(rows):
    for r in rows:
        if r["amount"] is None:
            bad_rows.add(1)                    # WORKER: only .add() — never read .value here

# Cache + materialize FIRST so both actions below read the SAME rows. orders is built with
# F.rand(...); without caching, an uncached recompute would regenerate values and the
# accumulator-vs-filter cross-check would rely on rand() reproducing identical per-partition rows.
orders = orders.cache()
orders.count()                                 # materialize the cache once

# ✅ Inside an ACTION (foreachPartition) → each task's update applied EXACTLY ONCE.
orders.foreachPartition(count_bad)

# DRIVER ONLY reads the merged total — safe because the update ran inside an action.
print("bad rows (action / exactly-once):", bad_rows.value)

# Cross-check with a real aggregation — these match because orders is cached (same rows).
print("bad rows (true count via filter):", orders.filter(F.col("amount").isNull()).count())

# Release the cache now: the NEXT cell deliberately needs an UNCACHED recompute to demonstrate
# the transformation-side double-count, so we must not leave orders cached past this point.
orders = orders.unpersist()

# COMMAND ----------

# MAGIC %md
# MAGIC ### MEASURE — where to look
# MAGIC - PySpark's `sc.accumulator(0)` is **unnamed**, so don't rely on spotting it by name in the
# MAGIC   Spark UI Stages → Accumulators view (there is no `sc.longAccumulator(name)` in PySpark).
# MAGIC - **The real verification** is the cross-check below: the accumulator total and the
# MAGIC   `filter().count()` total should be **equal** — that's the exactly-once guarantee holding
# MAGIC   because we updated inside an action.

# COMMAND ----------

# MAGIC %md
# MAGIC ## ANTI-PATTERN — accumulator inside a TRANSFORMATION (may double-count)
# MAGIC Updating an accumulator inside a `map` is **lazy** and **not exactly-once**: a second action
# MAGIC over the same **uncached** DataFrame recomputes the transformation and re-applies the update.

# COMMAND ----------

seen = sc.accumulator(0)

# Update lives inside a TRANSFORMATION (map) — NOT exactly-once.
counted = orders.rdd.map(lambda r: (seen.add(1), r)[1])

counted.count()                       # first action runs the map once
after_first = seen.value
counted.count()                       # second action RE-RUNS the map (DataFrame/RDD not cached)
after_second = seen.value

print("after 1st action:", after_first, "(expected", N, ")")
print("after 2nd action:", after_second, "(INFLATED — the transformation re-ran)")
print("=> trust accumulators only inside ACTIONS; for a real count use orders.count()")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Uses, edge cases & limitations (quick reference)
# MAGIC - **Use a broadcast variable** for a small static lookup/dict/set/model read in a UDF/`map`;
# MAGIC   it must fit on the **driver** and in **each executor's** memory. Release with `unpersist()`/`destroy()`.
# MAGIC - **Use an accumulator** for cheap side-channel metrics gathered inside an **action**
# MAGIC   (`foreach`/`foreachPartition`). It is **not** a substitute for a real aggregation.
# MAGIC - **Edge cases:** broadcasting an almost-too-big value (OOM risk); mutating a broadcast on a
# MAGIC   worker (silently discarded); an accumulator inside a transformation that re-runs (double-count);
# MAGIC   reading `acc.value` on a worker (partial only).
# MAGIC - **Limitations:** both are classic-`SparkContext` APIs → **not on Spark Connect / serverless**;
# MAGIC   accumulators give **no exactly-once** inside transformations by design.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cleanup — release broadcasts, drop demo objects, reset state
# MAGIC Leaves no state behind so the notebook is rerunnable. (We changed no `spark.conf` in this
# MAGIC lesson, so there's nothing to reset; the line below is a no-op safety net.)

# COMMAND ----------

# Release any live broadcast (idempotent / guarded).
try:
    bv.destroy()
except Exception as e:
    print("broadcast already released:", e)

# Release the orders cache if it is still materialized (idempotent / guarded).
try:
    orders.unpersist()
except Exception as e:
    print("orders cache already released:", e)

# Drop the demo schema (and its tables) — comment out if you want to keep it.
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")

# No spark.conf changes were made in this lesson; nothing to reset.
print("cleanup complete")
