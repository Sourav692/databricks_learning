# Databricks notebook source

# MAGIC %md
# MAGIC # Lesson 04 — Executor memory: unified model, spill & OOM
# MAGIC
# MAGIC **Goal:** *see* the executor's unified memory model behave — borrow & evict between
# MAGIC execution and storage, a real **spill** to disk, the asymmetry (execution evicts
# MAGIC cache, never the reverse), and where **off-heap** and **Python-worker** memory live.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - **Cluster/runtime:** any current **DBR LTS** (12.2 LTS+). **AQE is ON by default**
# MAGIC   (since DBR 7.3 / Spark 3.2). One demo wants the *before* state, so we temporarily
# MAGIC   set `spark.sql.shuffle.partitions` and reset it — commented loudly each time.
# MAGIC - **Unity Catalog** enabled, with `CREATE`/`USE` on the target catalog & schema.
# MAGIC - A few GB of cluster RAM; a small cluster makes spill easy to trigger (that's the point).
# MAGIC - No source data needed — we generate everything with `spark.range(...)`.
# MAGIC
# MAGIC ## What you'll learn
# MAGIC - The four heap regions and the arithmetic of region **M** and floor **R**.
# MAGIC - How to **create a spill** and read **Spill (Memory)/(Disk)** in the Spark UI.
# MAGIC - How execution **evicts** cache down to **R** — and how to see it on the Storage tab.
# MAGIC - Why a **Python UDF** OOM is an **overhead** problem, not a heap problem.
# MAGIC - The **create → stress → apply → MEASURE** loop for every memory fix.
# MAGIC
# MAGIC ## How to read the result (Spark UI is the heart of this lesson)
# MAGIC - **SQL / DataFrame tab** → the DAG; per-node rows and "spill" metrics.
# MAGIC - **Stages tab** → the task-time distribution (skew = **max ≫ median**), and the
# MAGIC   **Spill (Memory)** / **Spill (Disk)** columns, plus **GC Time**.
# MAGIC - **Storage tab** → each cached DataFrame, its **storage level**, and **Fraction Cached**
# MAGIC   (< 100% = execution evicted cache → the asymmetry, visible).
# MAGIC - **Executors tab** → **Storage Memory** used/total, **Off Heap Memory**, **Failed Tasks**.
# MAGIC - We also MEASURE with `df.explain(mode="formatted")`, `df.rdd.getNumPartitions()`, and a
# MAGIC   `df.write.format("noop")` sink to time/trigger a plan without writing real output.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters — Unity Catalog three-level namespacing
# MAGIC Edit these two widgets; everything below is parameterized off them.

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "pyspark_perf_demo", "Schema")

catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")

# Create + select the schema (Delta is the default table format on Databricks — no USING DELTA).
spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Using {catalog}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Read the unified-memory configs (and do the arithmetic)
# MAGIC The four numbers that define region **M** and floor **R**. Read them before tuning anything.

# COMMAND ----------

# These are the doc-grounded defaults (identical OSS-Spark and Databricks).
exec_mem  = spark.conf.get("spark.executor.memory", "1g")            # default "1g" — the JVM heap
frac      = float(spark.conf.get("spark.memory.fraction", "0.6"))     # default 0.6 — M = frac * (heap - 300 MiB)
stor_frac = float(spark.conf.get("spark.memory.storageFraction", "0.5"))  # default 0.5 — R = stor_frac * M

print(f"spark.executor.memory        = {exec_mem}")
print(f"spark.memory.fraction        = {frac}")
print(f"spark.memory.storageFraction = {stor_frac}")

# Worked example on a 1g executor (MiB). Reserved is a fixed 300 MiB carved off first.
reserved = 300
heap_mib = 1024            # if executor.memory == "1g"
usable   = heap_mib - reserved
M        = frac * usable
R        = stor_frac * M
print(f"\nOn a 1g executor:")
print(f"  usable = {heap_mib} - {reserved} = {usable} MiB")
print(f"  M = {frac} * {usable} = {M:.0f} MiB   (shared: execution + storage)")
print(f"  R = {stor_frac} * {M:.0f} = {R:.0f} MiB  (cache immune to eviction)")
print(f"  user memory ≈ {usable - M:.0f} MiB (outside M)")

# MEASURE / takeaway: ~434 MiB of M is shared by EVERY shuffle buffer AND every cached block.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · CREATE the demo data
# MAGIC A large generated DataFrame. `k` has ~50M distinct values (for a wide aggregation),
# MAGIC and we add a deliberately **skewed** key `hot_k` for the OOM/skew demo.

# COMMAND ----------

from pyspark.sql import functions as F

# 200M rows, generated — no source table needed.
big = (spark.range(0, 200_000_000)
       .withColumn("k", (F.rand(seed=1) * 50_000_000).cast("long"))   # many groups → wide agg
       .withColumn("val", (F.rand(seed=2) * 1000).cast("double")))

# Skewed key: ~90% of rows collapse to key 0, the rest spread out. This is the OOM villain.
skewed = big.withColumn(
    "hot_k",
    F.when(F.rand(seed=3) < F.lit(0.9), F.lit(0)).otherwise((F.rand(seed=4) * 1_000_000).cast("long"))
)

print("input partitions:", big.rdd.getNumPartitions())   # MEASURE: in-memory partition count

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · STRESS + MEASURE the "before" — create a spill
# MAGIC A wide aggregation over many groups stresses **execution** memory. With the default
# MAGIC 200 shuffle partitions, each task's slice is large → execution memory is exhausted →
# MAGIC Spark **spills** to local disk. The job still finishes, just slowly.

# COMMAND ----------

import time

# --- DEMONSTRATION ONLY: force the "before" with the default 200 shuffle partitions. ---
# (AQE coalesce would normally tidy this; we set partitions explicitly to make the contrast clear.)
spark.conf.set("spark.sql.shuffle.partitions", 200)

agg = big.groupBy("k").count()

# MEASURE 1 — the plan: HashAggregate is the execution-memory consumer that spills.
agg.explain(mode="formatted")
# Look for:  HashAggregate(keys=[k], functions=[count])   +   Exchange hashpartitioning(k, 200)

# COMMAND ----------

# MEASURE 2 — run the job with a noop sink (full job, no real output) and time it.
t0 = time.time()
agg.write.format("noop").mode("overwrite").save()
print(f"agg (200 partitions): {time.time() - t0:.1f} s")

# SPARK UI SIGNAL FOR THIS CELL:
#   Stages tab → the aggregation stage → columns "Spill (Memory)" and "Spill (Disk)".
#   Non-zero spill = execution memory ran out. Also note elevated "GC Time".

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · APPLY + MEASURE the "after" — more, smaller partitions
# MAGIC More partitions → each task handles a smaller slice → its buffers fit in **M** → spill
# MAGIC drops toward zero. (In production, prefer letting **AQE** coalesce; here we set it by hand
# MAGIC to make the cause-and-effect explicit.)

# COMMAND ----------

# APPLY: smaller partitions so each per-task buffer fits in M.
spark.conf.set("spark.sql.shuffle.partitions", 800)

agg2 = big.groupBy("k").count()
t0 = time.time()
agg2.write.format("noop").mode("overwrite").save()
print(f"agg (800 partitions): {time.time() - t0:.1f} s")

# MEASURE: re-open the Stages tab for THIS stage — "Spill (Disk)" should be far smaller (often 0),
# and wall-clock typically drops. Same logical aggregation, different memory pressure.

# COMMAND ----------

# Reset the conf we changed for the demo.
spark.conf.set("spark.sql.shuffle.partitions", 200)   # back to OSS default (DBX can use "auto")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · See the evict asymmetry — execution evicts cache, never the reverse
# MAGIC Cache a DataFrame (storage memory), then run heavy **execution** work on it in the same
# MAGIC stage. Execution evicts cache blocks **above R** to make room. Storage cannot, in turn,
# MAGIC evict execution — that's the asymmetry.

# COMMAND ----------

from pyspark import StorageLevel

# Cache: DataFrame default level is MEMORY_AND_DISK (spills cache to disk under pressure).
cached = big.select("k", "val").persist(StorageLevel.MEMORY_AND_DISK)
cached.count()    # materialize — persist()/cache() are LAZY until the first action

# Now run heavy execution work that competes for M:
heavy = cached.groupBy("k").agg(F.sum("val").alias("s"))
heavy.write.format("noop").mode("overwrite").save()

# SPARK UI SIGNAL: Storage tab → the cached DataFrame → "Fraction Cached" may be < 100%.
#   Execution evicted cache blocks above R (~217 MiB) to make room. Below R stays safe.
print("Check the Storage tab now: Fraction Cached for the persisted DataFrame.")

# COMMAND ----------

# Always release cache when done — cached blocks pin storage memory in R and pressure GC.
cached.unpersist(blocking=True)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Skew → the OOM villain (read-only demo)
# MAGIC A `groupBy` on the **skewed** key sends ~90% of rows to one task. That one task's buffers
# MAGIC blow up far faster than spill can drain — the classic executor-OOM cause. We MEASURE the
# MAGIC skew signal; we do **not** force the OOM (it would kill the executor).

# COMMAND ----------

skew_agg = skewed.groupBy("hot_k").agg(F.sum("val").alias("s"))

# MEASURE the plan (same HashAggregate + Exchange — the issue is data distribution, not the plan):
skew_agg.explain(mode="formatted")

# Run it and inspect the Stages tab.
t0 = time.time()
skew_agg.write.format("noop").mode("overwrite").save()
print(f"skewed agg: {time.time() - t0:.1f} s")

# SPARK UI SIGNAL: Stages tab → task-time distribution shows MAX ≫ MEDIAN, and the straggler
#   task has huge Shuffle Read + Spill. THAT task is the one that OOMs on a bigger input.
#   Fix in Lesson 08 (AQE skew join → salting), NOT by adding heap.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · PySpark memory lives OUTSIDE the heap (the hidden OOM)
# MAGIC A Python UDF runs in a **Python worker process outside the JVM heap**. Its memory counts
# MAGIC against the container's **overhead**, not region M. `spark.executor.pyspark.memory` is
# MAGIC **Not set** by default, so Spark does not limit Python workers.

# COMMAND ----------

# Default for the Python-worker limit (unset means: not limited; counts against overhead).
print("spark.executor.pyspark.memory =", spark.conf.get("spark.executor.pyspark.memory", "<not set>"))

# A Python UDF forces a Python worker. (Prefer native functions where possible — this is to
# DEMONSTRATE where Python memory lives, not a recommendation to use a UDF here.)
@F.udf("double")
def bump(x):
    return (x or 0.0) * 1.5    # trivial Python-side work to spin up a Python worker

py = big.select("k", bump("val").alias("val2"))

# MEASURE the plan — note the BatchEvalPython / ArrowEvalPython node = the Python worker boundary:
py.explain(mode="formatted")
# Look for:  BatchEvalPython [bump(val)]   (or ArrowEvalPython for a Pandas/Arrow UDF)

t0 = time.time()
py.write.format("noop").mode("overwrite").save()
print(f"python UDF pass: {time.time() - t0:.1f} s")

# TAKEAWAY: if a Python-heavy job OOMs the CONTAINER while the JVM heap looks idle, the fix is
#   MORE OVERHEAD (spark.executor.memoryOverhead) or spark.executor.pyspark.memory / a smaller
#   batch — NOT a bigger spark.executor.memory. Set overhead at CLUSTER creation.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Off-heap memory (concept + how to verify)
# MAGIC Off-heap lives **outside the JVM heap** → no GC for that data. It's **disabled by default**
# MAGIC (`offHeap.enabled=false`, `offHeap.size=0`) and must be sized at **cluster creation** — it
# MAGIC is not meaningfully togglable mid-session. This cell only *shows* the configs to inspect.

# COMMAND ----------

print("spark.memory.offHeap.enabled =", spark.conf.get("spark.memory.offHeap.enabled", "false"))
print("spark.memory.offHeap.size    =", spark.conf.get("spark.memory.offHeap.size", "0"))

# To enable in production, set THESE AT CLUSTER CREATION (Spark config box), e.g.:
#   spark.memory.offHeap.enabled true
#   spark.memory.offHeap.size    2g          # must be > 0 when enabled
# ...and shrink the JVM heap accordingly so you don't double-count RAM (per the tuning docs).
# VERIFY after a cluster restart: Executors tab → "Off Heap Memory Used / Total" is non-zero,
# and "GC Time" drops for a cache-heavy workload.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · Uses, edge cases & limitations (quick reference)
# MAGIC - **Uses:** diagnose slow stages (spill), executor OOM (which region?), right-size cache,
# MAGIC   tame GC with off-heap.
# MAGIC - **Edge cases:** skew AQE can't fully fix → salting; cache pinned in **R**; Python-worker
# MAGIC   blow-up (overhead, not heap); a near-threshold broadcast pressuring M cluster-wide.
# MAGIC - **Limitations:** the unified model manages only the **JVM heap regions** — Python workers,
# MAGIC   off-heap, and the container limit are separate budgets. `memory.fraction`/`storageFraction`
# MAGIC   are cluster-launch settings; changing them mid-session does not resize a running executor.
# MAGIC - **OSS vs Databricks:** the math (0.6 / 0.5 / 300 MiB / overhead 0.10) is identical;
# MAGIC   Databricks may add its own overhead/Photon accounting — verify in the compute docs.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10 · Cleanup — leave no state behind
# MAGIC Unpersist any cache, reset every conf we touched, and drop the demo schema.

# COMMAND ----------

# Unpersist anything still cached (idempotent / safe to re-run).
try:
    cached.unpersist(blocking=True)
except Exception:
    pass

# Reset confs changed during the demo to their defaults.
spark.conf.set("spark.sql.shuffle.partitions", 200)   # OSS default (Databricks may use "auto")

# Drop the demo schema (CASCADE removes any demo tables). Comment out to keep it around.
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete — confs reset and demo schema dropped.")
