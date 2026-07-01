# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 10 — Garbage-Collection Tuning (hands-on demo)
# MAGIC
# MAGIC **Goal:** *see* GC pressure change in the Spark UI as you change how you cache and how
# MAGIC much heap the cache is allowed to pin — so you can connect "GC Time is high" to a concrete fix.
# MAGIC
# MAGIC ### Prerequisites
# MAGIC - Any **DBR LTS** cluster (Spark 3.2+). On **DBR with JDK 17 / Spark 4.0+**, **G1GC is already
# MAGIC   the default** collector; on older DBR/JDK 8, ParallelGC is default and G1GC is an opt-in.
# MAGIC - **AQE is on by default** (DBR 7.3 LTS+). This lesson does not need AQE off; we leave it on.
# MAGIC   *(Pattern reminder: when a demo needs the before-state of a feature, set the relevant conf
# MAGIC   off, run, then reset it. The only conf we change here — `spark.memory.fraction` — we reset
# MAGIC   in the cleanup cell.)*
# MAGIC - **Unity Catalog** enabled with permission to create a schema in the target catalog.
# MAGIC - **`spark.executor.extraJavaOptions` is start-time only** — GC-log / collector flags must be
# MAGIC   set in the **cluster's Spark config** before the cluster starts; you cannot flip them from a
# MAGIC   running notebook. We show the flags as text and read GC from the UI instead.
# MAGIC
# MAGIC ### What you'll learn
# MAGIC - Why GC cost is **proportional to the number of Java objects**, not bytes.
# MAGIC - How **deserialized caching** loads the **Old generation** and drives **full GCs**.
# MAGIC - How **lowering `spark.memory.fraction`** eases Old-gen pressure and shortens pauses.
# MAGIC - Exactly **which Spark UI signal** ("GC Time") proves the change worked.
# MAGIC
# MAGIC ### How to read the Spark UI (the heart of this lesson)
# MAGIC - **Stages** tab → open a stage → the **task table** has a **GC Time** column next to Duration.
# MAGIC   If GC Time is a large fraction of task time (rule of thumb: **> ~10%** is worth investigating),
# MAGIC   GC is your bottleneck.
# MAGIC - **Executors** tab → the **Task Time (GC Time)** column shows per-executor GC — spot the one
# MAGIC   executor that's GC-thrashing.
# MAGIC - **Storage** tab → confirms what is cached and its size/storage level.
# MAGIC - For deep diagnosis: with `-verbose:gc` set in the cluster Spark config, the **Executors → stdout**
# MAGIC   log shows `[GC (Allocation Failure) ...]` (minor) and `[Full GC (Ergonomics) ...]` (full) lines.
# MAGIC
# MAGIC > We measure with `df.explain(mode="formatted")`, `df.rdd.getNumPartitions()`, and a
# MAGIC > `df.write.format("noop")` timing sink (runs the full job without writing output — and without
# MAGIC > pulling data to the driver the way `collect()` would).
# MAGIC
# MAGIC ## Databricks single-user execution note
# MAGIC Use a **classic single-user** cluster so the Executors tab, Storage tab, and driver logs belong
# MAGIC to this notebook session. Each action below creates a Spark UI job; the helper function labels
# MAGIC timing runs and the comments point to the exact GC columns to compare. Shared-access /
# MAGIC Spark Connect clusters make those signals harder to attribute during a tutorial.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters — Unity Catalog three-level namespacing
# MAGIC Parameterize `catalog.schema` at the top so the notebook is portable. Delta is the default
# MAGIC table format on Databricks (no `USING DELTA` needed).

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "pyspark_perf_demo", "Schema")

catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")

# Use an existing catalog; create only the demo schema under it.
spark.sql(f"CREATE SCHEMA  IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")

# Record the starting memory.fraction so the cleanup cell can restore it exactly.
ORIGINAL_MEMORY_FRACTION = spark.conf.get("spark.memory.fraction", "0.6")
print("catalog.schema =", f"{catalog}.{schema}")
print("starting spark.memory.fraction =", ORIGINAL_MEMORY_FRACTION)

LESSON_ID = "Lesson 10 - Garbage collection"

def mark_action(label):
    """Label the next Spark action in the Spark UI for tutorial walkthroughs."""
    spark.sparkContext.setJobGroup(f"{LESSON_ID}: {label}", f"{LESSON_ID}: {label}", True)
    print(f"\nACTION -> {label}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE — generate an object-heavy, reused DataFrame
# MAGIC We build a wide-ish DataFrame with many columns and many rows. Wide + deserialized cache =
# MAGIC **lots of Java objects** in the Old generation = the condition that drives full GCs.
# MAGIC Adjust `N_ROWS` to your cluster size (bigger = more visible GC, but don't OOM a small cluster).

# COMMAND ----------

from pyspark.sql import functions as F

N_ROWS = 40_000_000        # tune to cluster size; ~40M rows makes GC visible without huge clusters

base = spark.range(0, N_ROWS, numPartitions=64)

# Many columns of small, boxed-style values → many objects per row when cached deserialized.
wide = (
    base
    .withColumn("k",   (F.col("id") % F.lit(1_000)).cast("int"))      # join/group key
    .withColumn("s1",  F.concat(F.lit("user_"), (F.col("id") % 10_000).cast("string")))
    .withColumn("s2",  F.concat(F.lit("region_"), (F.col("id") % 50).cast("string")))
    .withColumn("v1",  (F.rand() * 1000))
    .withColumn("v2",  (F.rand() * 1000))
    .withColumn("v3",  (F.rand() * 1000))
)

print("in-memory partitions:", wide.rdd.getNumPartitions())   # MEASURE: partition count
wide.printSchema()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · A reusable timing helper (the `noop` sink)
# MAGIC `df.write.format("noop")` executes the entire plan **without writing real output** — the clean
# MAGIC way to time a plan in this track. (Prefer it over `collect()`, which pulls data to the driver
# MAGIC and risks driver OOM — Lesson 03.)

# COMMAND ----------

import time

def time_plan(df, label, runs=3):
    """Force full materialization a few times and report wall-clock. Read GC Time in the Spark UI."""
    times = []
    for i in range(runs):
        mark_action(f"{label} run {i + 1}")
        t0 = time.time()
        df.write.format("noop").mode("overwrite").save()
        times.append(time.time() - t0)
    best = min(times)
    print(f"[{label}] best of {runs}: {best:.2f}s   all: {[round(t,2) for t in times]}")
    return best

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · STRESS + INSPECT "before" — deserialized cache (object-heavy, GC-heavy)
# MAGIC We cache the wide DataFrame with the **default deserialized** storage level and reuse it across
# MAGIC several actions. Deserialized = one Java object per field per row → the Old gen fills with
# MAGIC long-lived objects → **full GCs**.
# MAGIC
# MAGIC **Look in the Spark UI now:**
# MAGIC - **Stages** tab → the cache-reading stages → the **GC Time** column. Note GC Time as a % of
# MAGIC   task Duration (this is your baseline).
# MAGIC - **Storage** tab → the cached DataFrame, its size, and storage level (`Memory Deserialized`).

# COMMAND ----------

from pyspark import StorageLevel

wide_deser = wide
wide_deser.cache()            # DataFrame default == MEMORY_AND_DISK (deserialized)
wide_deser.count()            # materialize the cache (first action is slower: compute + store)

# Reuse it across multiple actions so the cache earns its keep (and so GC has time to bite).
_ = wide_deser.groupBy("k").agg(F.sum("v1").alias("sv1")).count()
_ = wide_deser.groupBy("s2").agg(F.avg("v2").alias("av2")).count()

baseline = time_plan(wide_deser.groupBy("k").agg(F.sum("v3")), "deserialized cache, fraction=0.6")

# MEASURE the plan: InMemoryTableScan confirms it's reading the cache, not recomputing.
wide_deser.groupBy("k").agg(F.sum("v3")).explain(mode="formatted")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Lever 1 (JVM/Scala-side only) — serialized caching is NOT a PySpark switch
# MAGIC The docs: a serialized cache stores **"only one object (a byte array) per RDD partition"** →
# MAGIC orders of magnitude fewer objects for the collector to track. **But this is a JVM/Scala-side
# MAGIC lever.**
# MAGIC
# MAGIC **PySpark honesty check:** the PySpark `StorageLevel` enum has **no `_SER` levels** —
# MAGIC `cache()` *is* `persist(StorageLevel.MEMORY_AND_DISK)` — because, per the docs,
# MAGIC *"in Python, stored objects will always be serialized with the Pickle library, so it does not
# MAGIC matter whether you choose a serialized level."* So there is **no deserialized→serialized A/B to
# MAGIC run from PySpark**: both calls produce a byte-identical cache, so GC Time is expected to be
# MAGIC **UNCHANGED**. The actually-effective PySpark GC levers are **`spark.memory.fraction`**
# MAGIC (section 5) and **off-heap** (section 6). We make the no-op explicit, then move to what works.
# MAGIC
# MAGIC *(On a JVM/Scala job, switching to `persist(StorageLevel.MEMORY_AND_DISK_SER)` is where GC Time
# MAGIC would actually drop — that lever is unreachable from the PySpark `StorageLevel` enum.)*

# COMMAND ----------

# Prove the no-op: cache() and persist(MEMORY_AND_DISK) request the IDENTICAL storage level, and in
# PySpark both are pickled. There is nothing to A/B here — re-persisting the same level changes
# nothing, so we do NOT claim a GC-Time delta for this step.
print("section-3 cache() level     ==", wide_deser.storageLevel)         # what cache() set
print("MEMORY_AND_DISK enum target ==", StorageLevel.MEMORY_AND_DISK)     # identical level

# Keep `serialized` defined for the section-7 summary, but label it honestly: it is the SAME cache as
# the baseline, so we expect the SAME wall-clock and the SAME GC Time (no serialized-vs-deserialized
# effect exists in PySpark). The real change comes in section 5.
serialized = time_plan(wide_deser.groupBy("k").agg(F.sum("v3")), "same PySpark cache (no _SER switch)")

# MEASURE: expect NO GC change vs section 3 — the cache is byte-identical. For an effective PySpark
# GC change, see section 5 (lower spark.memory.fraction) and section 6 (off-heap).
wide_deser.unpersist(blocking=True)   # free the Old gen before the section-5 run

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · APPLY (lever 2) — lower `spark.memory.fraction` to ease Old-gen pressure
# MAGIC The unified region (cache + execution) = `spark.memory.fraction` × (heap − 300 MiB), default
# MAGIC **0.6**. If cached blocks keep the Old gen near-full and you see constant full GCs, give the
# MAGIC cache **less** heap so fewer long-lived objects are pinned.
# MAGIC
# MAGIC Docs verbatim: *"better to cache fewer objects than to slow down task execution."*

# COMMAND ----------

# DEMO ONLY: shrink the unified region so the cache pins less of the heap.
spark.conf.set("spark.memory.fraction", "0.4")
print("spark.memory.fraction now =", spark.conf.get("spark.memory.fraction"))

wide_small = wide
wide_small.persist(StorageLevel.MEMORY_AND_DISK)
wide_small.count()

_ = wide_small.groupBy("k").agg(F.sum("v1")).count()
small_frac = time_plan(wide_small.groupBy("k").agg(F.sum("v3")), "default cache, fraction=0.4")

# MEASURE: Stages tab → GC Time should fall vs the fraction=0.6 runs IF cache was the GC culprit.
# (This is the real PySpark GC lever — the cache level is unchanged; only memory.fraction moved.)
# Trade-off: a smaller cache region means more eviction/recompute/spill — watch the wall-clock too.
wide_small.unpersist(blocking=True)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · The collector & GC-log flags (start-time only — set in cluster Spark config)
# MAGIC These cannot be applied from a running notebook (the executor JVM already started). Put them in
# MAGIC the **cluster → Advanced options → Spark config** before the cluster boots, then read the
# MAGIC results in **Executors → stdout**.

# COMMAND ----------

# MAGIC %md
# MAGIC ```text
# MAGIC # --- Cluster Spark config (NOT runnable from the notebook) ---
# MAGIC
# MAGIC # Verbose GC logging → executor stdout shows every collection:
# MAGIC spark.executor.extraJavaOptions -verbose:gc -XX:+PrintGCDetails -XX:+PrintGCTimeStamps
# MAGIC
# MAGIC # Use G1GC explicitly (already the default on Spark 4.0 / JDK 17; opt-in on older DBR/JDK 8):
# MAGIC spark.executor.extraJavaOptions -XX:+UseG1GC -XX:G1HeapRegionSize=16m
# MAGIC
# MAGIC # Off-heap caching keeps data OUT of the JVM heap (no GC scan). Shrink the JVM heap to match,
# MAGIC # or you trade GC for OOM (Lesson 04):
# MAGIC spark.memory.offHeap.enabled true
# MAGIC spark.memory.offHeap.size    2147483648      # 2 GB in bytes (must be > 0)
# MAGIC ```
# MAGIC
# MAGIC **VERIFY:** after restarting the cluster with these set, open **Executors → stdout** — you'll
# MAGIC see `[GC pause (G1 Evacuation Pause) ...]` lines with their durations. Compare full-GC pause
# MAGIC lengths and the **Stages → GC Time** column before vs after.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Summary of the runs
# MAGIC Wall-clock is a coarse proxy — the **authoritative** GC evidence is the **GC Time** column in
# MAGIC the Stages tab. The section-4 run is the **same PySpark cache** as the baseline (no `_SER`
# MAGIC switch exists in PySpark), so expect it to match the baseline — **no** serialized-vs-deserialized
# MAGIC effect. The lever that actually moves GC from PySpark is **lower `spark.memory.fraction`**
# MAGIC (section 5) — and **off-heap** (section 6). The serialized-cache GC win is JVM/Scala-side only.

# COMMAND ----------

print("=== wall-clock summary (best-of-3, seconds) ===")
print(f"default cache, fraction=0.6                 : {baseline:.2f}s   (baseline)")
print(f"same PySpark cache, fraction=0.6 (no _SER)  : {serialized:.2f}s   (expected ≈ baseline)")
print(f"lower memory.fraction=0.4                   : {small_frac:.2f}s   (the real PySpark lever)")
print()
print("Authoritative signal: Spark UI → Stages → GC Time (as a % of task Duration).")
print("Goal of GC tuning: make pauses SHORTER and RARER — you can never eliminate GC.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Uses, edge cases & limitations (interview-ready recap)
# MAGIC - **Uses:** large-heap / cache-heavy / allocation-heavy jobs where the UI shows high GC Time;
# MAGIC   the "caching made it slower" diagnosis; cutting per-row object churn in Python UDFs.
# MAGIC - **Edge cases:** a **bigger heap can make full GCs worse** (more live objects to walk);
# MAGIC   **skew** (Lesson 08) and **spill** (Lesson 04) both amplify GC — the docs note spilling brings
# MAGIC   *"increased garbage collection"* — so fix those first; `extraJavaOptions` is start-time only.
# MAGIC - **Limitations:** you can't eliminate GC; tuning is workload-specific (tune from the GC logs);
# MAGIC   off-heap is partly experimental and forces you to hand-balance heap vs off-heap; **G1GC is the
# MAGIC   default only since Spark 4.0 / JDK 17** — opt-in on older runtimes.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · Cleanup — unpersist, reset conf, drop demo objects
# MAGIC Leaves no state behind so the notebook is rerunnable.

# COMMAND ----------

# Unpersist anything still cached (idempotent — safe even if already unpersisted).
for df in [wide, wide_deser, wide_small]:
    try:
        df.unpersist(blocking=True)
    except Exception:
        pass

# Reset the one conf we changed, back to its original value.
spark.conf.set("spark.memory.fraction", ORIGINAL_MEMORY_FRACTION)
print("restored spark.memory.fraction =", spark.conf.get("spark.memory.fraction"))

# Drop the demo schema (created no tables, but drop CASCADE to be safe and tidy).
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("dropped schema", f"{catalog}.{schema}")

# Note: we did NOT change spark.executor.extraJavaOptions / off-heap from the notebook
# (start-time only), so there is nothing runtime-side to reset for those.
