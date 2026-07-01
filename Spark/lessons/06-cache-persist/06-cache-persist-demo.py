# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 06 — Cache &amp; persist: stop recomputing the same DataFrame
# MAGIC
# MAGIC **Goal:** see Spark *recompute an expensive join on every action*, then `persist()` the result
# MAGIC once and watch downstream actions read from the cache instead — and learn when caching hurts.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - Any **DBR LTS** (12.2 LTS+ recommended); Unity Catalog enabled with create rights on the target catalog/schema.
# MAGIC - **AQE is on by default** (DBR 7.3 LTS+). This lesson is about caching, not adaptivity, so we leave AQE on.
# MAGIC   When a demo needs the *before* state (an uncached recompute), we don't disable AQE — we simply don't cache;
# MAGIC   the one conf we toggle (`autoBroadcastJoinThreshold = -1`) is **reset** at the end so the join is a real
# MAGIC   shuffle/sort-merge we can observe, then restored.
# MAGIC - No external data needed — we generate everything with `spark.range(...)`.
# MAGIC
# MAGIC ## What you'll learn
# MAGIC - `cache()` == `persist()` with the **default level**; DataFrame default = `MEMORY_AND_DISK` (`MEMORY_AND_DISK_DESER`), **not** the RDD's `MEMORY_ONLY`.
# MAGIC - Cache/persist are **lazy** — the **first action** computes *and* stores; later actions reuse.
# MAGIC - How to pick a **storage level** (`MEMORY_AND_DISK`, `DISK_ONLY`, `_2`, `OFF_HEAP`) and read it back.
# MAGIC - `unpersist()` is **eager** — always free the cache when done.
# MAGIC - When caching helps (≥ 2 reuses) vs hurts (read-once, or over-caching that evicts execution memory).
# MAGIC
# MAGIC ## How to read the result (the measurement toolkit)
# MAGIC - **`df.explain(mode="formatted")`** — look for **`InMemoryTableScan`** / **`InMemoryRelation`** (cache used)
# MAGIC   vs a re-run **`SortMergeJoin` + `Exchange` + `FileScan`** (recompute).
# MAGIC - **Wall-clock timing** of an action via the **`noop`** sink (runs the full job, writes nothing).
# MAGIC - **Spark UI → Storage tab**: cached entry, its **Storage Level**, **Size in Memory / on Disk**, and
# MAGIC   **Fraction Cached** (&lt; 100% means it didn't all fit).
# MAGIC - **Spark UI → SQL/DataFrame tab**: the second action's DAG shows the cached scan; the join stages vanish.
# MAGIC
# MAGIC ## Databricks single-user execution note
# MAGIC Run on a **classic single-user** cluster. Cache lessons are easiest to teach when one user owns
# MAGIC the driver and the Spark UI, because each action (`count`, `noop` write, `show`) maps directly
# MAGIC to a Jobs / SQL entry. The comments below distinguish the action that materializes the cache
# MAGIC from later actions that reuse it.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters — Unity Catalog three-level namespacing
# MAGIC Parameterize `catalog.schema` at the top. Delta is the default format (no `USING DELTA`).

# COMMAND ----------

import time
from pyspark.storagelevel import StorageLevel

catalog = "main"                      # existing catalog you can write to
schema  = "pyspark_perf_demo"         # demo schema; dropped in the cleanup cell

# Single-user tutorial assumption: create only the demo schema, not the catalog itself.
spark.sql(f"CREATE SCHEMA  IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")

LESSON_ID = "Lesson 06 - Cache and persist"

def mark_action(label):
    """Label the next Spark action in the Spark UI for tutorial walkthroughs."""
    spark.sparkContext.setJobGroup(f"{LESSON_ID}: {label}", f"{LESSON_ID}: {label}", True)
    print(f"\nACTION -> {label}")

# Small helper: time a full job without writing real output (the noop sink).
# Cleaner than collect() — it triggers the whole plan but pulls nothing to the driver.
def time_action(df, label):
    mark_action(label)
    t0 = time.time()
    df.write.format("noop").mode("overwrite").save()
    dt = time.time() - t0
    print(f"{label:<34} {dt:6.2f}s")
    return dt

print("Namespace ready:", f"{catalog}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE — generate a fact table and a dimension
# MAGIC `events` (the big fact) and `devices` (a mid-size dimension). We force a **real shuffle join**
# MAGIC (not a broadcast) by disabling auto-broadcast, so the lineage is genuinely expensive to recompute.

# COMMAND ----------

from pyspark.sql import functions as F

# DEMO ONLY: disable auto-broadcast so the join is a Shuffle Sort-Merge Join we can observe being
# recomputed. We RESET this in the cleanup cell. (Default is 10 MB in OSS Spark.)
_orig_abj = spark.conf.get("spark.sql.autoBroadcastJoinThreshold")
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", -1)   # -1 = never auto-broadcast

events = (spark.range(0, 30_000_000)                          # 30M-row fact table
                .withColumn("device_id", (F.col("id") % 50_000).cast("long"))
                .withColumn("country",   F.element_at(F.array(
                    F.lit("US"), F.lit("IN"), F.lit("DE"), F.lit("BR"), F.lit("JP")),
                    (F.col("id") % 5 + 1).cast("int")))
                .withColumn("amount",    (F.rand(7) * 100).cast("double"))
                .withColumn("is_fraud",  (F.rand(9) < 0.01)))

devices = (spark.range(0, 50_000)                             # 50K-row dimension
                 .withColumnRenamed("id", "device_id")
                 .withColumn("device_type", F.element_at(F.array(
                     F.lit("ios"), F.lit("android"), F.lit("web")),
                     (F.col("device_id") % 3 + 1).cast("int"))))

print("events  partitions:", events.rdd.getNumPartitions())
print("devices partitions:", devices.rdd.getNumPartitions())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · STRESS — build an expensive lineage hit by several actions
# MAGIC `clean` sits on top of a shuffle join + filter. We will run **three** downstream actions off it.
# MAGIC Without caching, the join is re-run **three times**.

# COMMAND ----------

clean = (events.join(devices, "device_id")            # the costly Shuffle Sort-Merge Join
                .filter("amount > 5"))                # a representative downstream filter

# MEASURE (before): confirm the plan really contains the join + exchanges (no cache yet).
clean.explain(mode="formatted")
# Spark UI signal to look for: SQL tab DAG shows SortMergeJoin with TWO Exchange nodes feeding it.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · MEASURE the "before" — three actions, three recomputes
# MAGIC Each action re-executes the full lineage. Watch the wall-clock: three comparable, non-trivial times.

# COMMAND ----------

print("=== NO CACHE: each action recomputes the join ===")
time_action(clean.groupBy("country").count(),     "action 1 (group by country)")
time_action(clean.where("is_fraud").select("id"), "action 2 (fraud filter)")
time_action(clean.groupBy("device_type").count(), "action 3 (group by device_type)")
# Spark UI → SQL tab: you'll see THREE separate queries, each with its own SortMergeJoin + Exchanges.
# Spark UI → Stages tab: shuffle read/write repeats for every action — that's the waste caching removes.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · APPLY — persist with an explicit storage level, then materialize
# MAGIC `persist()` is **lazy**: it only registers the level. The **first action** computes *and* stores.
# MAGIC We use `count()` as the deliberate materializing action.

# COMMAND ----------

clean.persist(StorageLevel.MEMORY_AND_DISK)   # explicit form of the DataFrame default
# (df.cache() would do the same with the default level.)

print("storageLevel set to:", clean.storageLevel)   # Disk Memory Deserialized 1x Replicated

t_mat = time_action(clean, "FIRST action (materialize cache)")  # slow once: compute + STORE
# Spark UI → Storage tab now lists this DataFrame with its level, size, and Fraction Cached.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · MEASURE the "after" — same three actions now read the cache
# MAGIC The join is gone from the plan; each action reads stored blocks. Compare the times to step 3.

# COMMAND ----------

print("=== CACHED: actions reuse stored blocks ===")
time_action(clean.groupBy("country").count(),     "action 1 (cached)")
time_action(clean.where("is_fraud").select("id"), "action 2 (cached)")
time_action(clean.groupBy("device_type").count(), "action 3 (cached)")

# MEASURE: confirm the plan reads the cache, not the join.
clean.groupBy("country").count().explain(mode="formatted")
# LOOK FOR: InMemoryTableScan over InMemoryRelation, and NO SortMergeJoin / FileScan events. ✅
# Spark UI → SQL tab: the cached queries have a single InMemoryTableScan node; the join stages are gone.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Storage levels — inspect the flags & try a non-default level
# MAGIC A `StorageLevel` encodes (useDisk, useMemory, useOffHeap, deserialized, replication).
# MAGIC In **PySpark** objects are always Pickle-serialized, so the `_SER` levels are not separately exposed;
# MAGIC the Python set is `MEMORY_ONLY(_2)`, `MEMORY_AND_DISK(_2)`, `DISK_ONLY(_2/_3)`, `OFF_HEAP`.

# COMMAND ----------

for lvl in [StorageLevel.MEMORY_ONLY, StorageLevel.MEMORY_AND_DISK,
            StorageLevel.DISK_ONLY, StorageLevel.DISK_ONLY_2, StorageLevel.DISK_ONLY_3]:
    print(f"{str(lvl):<48}")   # e.g. "Disk Serialized 2x Replicated" for DISK_ONLY_2

# Cache a SECOND DataFrame to disk only (imagine it's too big for RAM): keep the heap free of it.
big_branch = events.select("device_id", "amount").filter("amount > 50")
big_branch.persist(StorageLevel.DISK_ONLY)
big_branch.count()                                  # materialize
print("big_branch level:", big_branch.storageLevel) # Disk Serialized 1x Replicated
# Spark UI → Storage tab: big_branch shows Size in Memory = 0 B, Size on Disk > 0 B.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Spark SQL equivalent — and the eager-vs-lazy gotcha
# MAGIC `CACHE TABLE` is **eager** by default (materializes immediately) — the opposite of `df.cache()`.
# MAGIC Use `CACHE LAZY TABLE` to match the DataFrame API's laziness. `UNCACHE TABLE` frees it (eager).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Register the cleaned join as a temp view, then cache it (eager: materializes on this statement).
# MAGIC CREATE OR REPLACE TEMP VIEW clean_sql AS
# MAGIC   SELECT e.id, e.country, e.is_fraud, d.device_type
# MAGIC   FROM   events e JOIN devices d ON e.device_id = d.device_id
# MAGIC   WHERE  e.amount > 5;
# MAGIC CACHE TABLE clean_sql;                       -- eager; use CACHE LAZY TABLE for lazy behaviour
# MAGIC -- VERIFY: the next EXPLAIN shows InMemoryTableScan; Storage tab lists `clean_sql`.
# MAGIC EXPLAIN FORMATTED SELECT country, count(*) FROM clean_sql GROUP BY country;

# COMMAND ----------

# MAGIC %sql
# MAGIC UNCACHE TABLE clean_sql;   -- free the SQL-cached table (eager)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · The anti-pattern — caching a read-once DataFrame (uses, edge cases & limitations)
# MAGIC **Uses:** cache only a DataFrame reused across **≥ 2 actions** with the right level, then `unpersist()`.
# MAGIC **Edge cases:** `cache()` then no action → nothing cached (Storage tab empty); a cache that doesn't fit
# MAGIC recomputes (`MEMORY_ONLY`) or spills (`MEMORY_AND_DISK`) — check **Fraction Cached**.
# MAGIC **Limitations:** lazy (only `unpersist()` is eager); a level can't be reassigned without `unpersist()` first;
# MAGIC `OFF_HEAP` is experimental (needs `spark.memory.offHeap.enabled=true` + size &gt; 0); over-caching evicts
# MAGIC execution memory → spill/GC/OOM (Lesson 04).

# COMMAND ----------

# Demonstrate the "cache then no action caches nothing" edge case.
demo = events.select("country").filter("country = 'US'")
demo.cache()                                         # lazy: registers level, stores NOTHING yet
# (No action here.) The Spark UI Storage tab will NOT show `demo`.
print("demo cached blocks materialized? -> only after an action. Level set:", demo.storageLevel)
demo.unpersist()   # tidy up the unused registration

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · CLEANUP — unpersist caches, reset conf, drop demo objects
# MAGIC Leaves no state behind so the notebook is rerunnable.

# COMMAND ----------

# Unpersist everything we cached (eager).
for d in [clean, big_branch]:
    try:
        d.unpersist()
    except Exception as e:
        print("unpersist skipped:", e)

# Belt-and-suspenders: clear any remaining cached tables/views in this session.
spark.catalog.clearCache()

# Reset the conf we toggled for the demo back to its original value.
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", _orig_abj)
print("autoBroadcastJoinThreshold restored to:", spark.conf.get("spark.sql.autoBroadcastJoinThreshold"))

# Drop the demo schema (and its objects). Comment out if you want to keep it.
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete.")
