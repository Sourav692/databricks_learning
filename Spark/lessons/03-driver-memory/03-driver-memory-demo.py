# Databricks notebook source

# MAGIC %md
# MAGIC # Lesson 03 — Driver memory & driver OOM (hands-on)
# MAGIC
# MAGIC **Goal:** *see* why the single driver heap is a single point of failure — how `collect()`,
# MAGIC an over-sized broadcast, and a partition-count explosion each load the one fixed driver
# MAGIC heap, and how `write()` / `take()` / aggregation keep big data on the executors.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - **Cluster/runtime:** any DBR LTS (DBR 12.2 LTS+ recommended). **AQE is on by default**
# MAGIC   (DBR 7.3 LTS+ / Spark 3.2+). This lesson does not need AQE off, but a couple of cells
# MAGIC   toggle `spark.sql.autoBroadcastJoinThreshold` to demonstrate the broadcast path and then
# MAGIC   **reset it** — every conf changed for a demo is restored in the final cell.
# MAGIC - **Unity Catalog** enabled; permission to create a schema + table in the target catalog.
# MAGIC - Driver defaults this lesson references: `spark.driver.memory = 1g`,
# MAGIC   `spark.driver.maxResultSize = 1g`.
# MAGIC
# MAGIC ## What you'll learn
# MAGIC 1. What lives on the driver heap (plan/scheduler, action results, the broadcast build, partition metadata).
# MAGIC 2. How `spark.driver.maxResultSize` aborts an over-sized `collect()` **before** a real OOM.
# MAGIC 3. Why `write()` / `write.format("noop")` / `take(n)` / aggregate-then-collect keep data off the driver.
# MAGIC 4. Why a mis-sized `broadcast()` OOMs the **driver** (during the build), not an executor.
# MAGIC 5. How a partition-count explosion pressures the driver via **metadata alone**.
# MAGIC
# MAGIC ## How to read the result (this track is about *seeing* the engine)
# MAGIC - `df.explain(mode="formatted")` → the plan node (`BroadcastExchange`, `HashAggregate`, scans).
# MAGIC - `df.rdd.getNumPartitions()` → in-memory partition count (the driver tracks one entry per partition).
# MAGIC - Time an action with the **`noop`** sink: `df.write.format("noop").mode("overwrite").save()`
# MAGIC   runs the full job **without returning rows to the driver** — the safe way to benchmark.
# MAGIC - **Spark UI** signals for *this* lesson:
# MAGIC   - **Executors** tab → the single **`driver`** row shows the driver's memory + **GC time**
# MAGIC     (rising driver GC during a `collect()` = heap under pressure — the OOM warning sign).
# MAGIC   - **Jobs / SQL** tab → a `collect()` job returns a final result task set **to the driver**;
# MAGIC     a `write` / `noop` job returns **nothing**.
# MAGIC   - Driver **log** → the `maxResultSize` abort message and any `OutOfMemoryError`.
# MAGIC
# MAGIC > ⚠️ **Safety:** the "naive `collect()`" cells are written to **stay under the 1g limit** so the
# MAGIC > notebook runs end-to-end. The cell that would actually trip the guardrail is commented out —
# MAGIC > uncomment it only on a disposable cluster to watch the abort fire.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parameters — Unity Catalog three-level namespacing
# MAGIC Edit these to a catalog/schema you can write to. Delta is the default format (no `USING DELTA`).

# COMMAND ----------

# Parameterize the UC namespace at the top so the whole notebook is portable.
catalog = "main"
schema  = "pyspark_perf_demo"
table   = "driver_demo_facts"
fqn     = f"{catalog}.{schema}.{table}"          # fully-qualified catalog.schema.table

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print("Writing demo objects under:", f"{catalog}.{schema}")

# COMMAND ----------

# Record the driver budget we are working against (read-only — these are launch-time defaults).
print("spark.driver.memory        =", spark.conf.get("spark.driver.memory"))         # '1g' — driver JVM heap
print("spark.driver.maxResultSize =", spark.conf.get("spark.driver.maxResultSize"))  # '1g' — cap on serialized results
# NOTE: spark.driver.memory is a LAUNCH-TIME JVM flag. To raise it, set it in the cluster's
# Spark config and restart — spark.conf.set(...) at runtime will NOT change the heap.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1) CREATE — a fact table large enough to be dangerous to collect
# MAGIC `spark.range(...)` generates rows on the **executors**. We give each row a few columns so a
# MAGIC full `collect()` would be gigabytes — exactly the shape that endangers the driver.

# COMMAND ----------

from pyspark.sql import functions as F

# 60M rows × a handful of columns. Generated lazily on the executors — nothing on the driver yet.
facts = (spark.range(0, 60_000_000)
         .withColumn("account_id", (F.col("id") % 5_000_000))          # join key for later
         .withColumn("amount",     (F.rand(seed=7) * 1000).cast("double"))
         .withColumn("country_code", (F.col("id") % 240).cast("int"))  # 240 distinct countries
         .withColumn("pad", F.lit("x" * 80)))                          # widen the row (~bytes/row)

facts.write.mode("overwrite").saveAsTable(fqn)   # executors write in parallel — driver untouched
facts = spark.table(fqn)
print("rows:", facts.count(), "| in-memory partitions:", facts.rdd.getNumPartitions())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2) STRESS + MEASURE the "before" — the naive collect() path
# MAGIC `collect()` serializes **every partition into the one driver heap**. Below we keep it bounded so
# MAGIC the notebook survives, then show the cell that would actually trip the guardrail (commented out).

# COMMAND ----------

# MEASURE the plan first — note this is a full scan with NO aggregation to shrink the result.
facts.explain(mode="formatted")
# Spark UI → SQL tab: a collect() of this would return ALL partitions' rows to the driver.

# COMMAND ----------

# ❌ NAIVE (bounded here for safety): collect() pulls rows into the driver heap.
# We cap at 50k rows so this cell is safe to run; conceptually collect() with NO bound = all 60M rows.
import time
t0 = time.time()
bounded_rows = facts.limit(50_000).collect()     # 50k rows reach the driver (safe)
print(f"collected {len(bounded_rows):,} rows to the driver in {time.time()-t0:.2f}s")

# ⚠️ DO NOT RUN on a shared cluster — this is the cell that trips the guardrail / OOMs the driver:
# all_rows = facts.collect()                      # ~60M rows → "Total size of serialized results ...
#                                                 #   is bigger than spark.driver.maxResultSize (1024.0 MiB)"
# Watch the driver log for that exact abort message, and Executors tab → 'driver' GC time spiking.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3) APPLY the fix — keep data on the executors
# MAGIC Three safe patterns: **write** the result, **time** a job with the `noop` sink (no rows to the
# MAGIC driver), and pull only a **bounded** sample when you genuinely need local rows.

# COMMAND ----------

# ✅ FIX A — write the result. Executors persist their own partitions; nothing returns to the driver.
(facts
 .withColumn("amount_with_tax", F.col("amount") * 1.2)
 .write.mode("overwrite").saveAsTable(f"{catalog}.{schema}.driver_demo_result"))
print("written — 0 rows returned to the driver")

# COMMAND ----------

# ✅ FIX B — TIME the full job with the noop sink (runs everything, returns NOTHING to the driver).
# This is the track's safe alternative to collect() for benchmarking a plan.
t0 = time.time()
facts.withColumn("amount_with_tax", F.col("amount") * 1.2) \
     .write.format("noop").mode("overwrite").save()
print(f"full job via noop sink: {time.time()-t0:.2f}s  (driver heap stayed flat)")
# Spark UI → Jobs tab: this job has NO result returned to the driver.

# COMMAND ----------

# ✅ FIX C — aggregate on the cluster, collect only the tiny summary (1 row, not 60M).
t0 = time.time()
total = facts.agg(F.sum("amount").alias("t")).collect()[0]["t"]   # 1 row to the driver
print(f"sum(amount) = {total:,.0f}  | collected 1 summary row in {time.time()-t0:.2f}s")

# MEASURE: the plan shows partial + final HashAggregate on the executors; the driver gets 1 row.
facts.agg(F.sum("amount").alias("t")).explain(mode="formatted")
# Look for: HashAggregate (partial) -> Exchange -> HashAggregate (final). Final result is tiny.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4) The broadcast path — a mis-sized broadcast OOMs the *driver*
# MAGIC A broadcast join collects the **small side to the driver** to build the broadcast relation. Forcing
# MAGIC `broadcast()` on a large table OOMs the **driver during the build** — not an executor. We verify the
# MAGIC plan node here (without forcing a real OOM).

# COMMAND ----------

from pyspark.sql.functions import broadcast

# A genuinely small dimension (240 rows) — safe to broadcast.
country = (spark.range(0, 240)
           .withColumnRenamed("id", "country_code")
           .withColumn("country_code", F.col("country_code").cast("int"))
           .withColumn("country_name", F.concat(F.lit("country_"), F.col("country_code"))))

ok = facts.join(broadcast(country), "country_code")   # small side built on the driver — fine
ok.explain(mode="formatted")
# VERIFY: a BroadcastExchange feeds the COUNTRY (small) side, and NO Exchange on facts.
# If you instead broadcast() a multi-GB table, this same BroadcastExchange would OOM the DRIVER
# during the build — check the DRIVER log, not the executor logs.

# COMMAND ----------

# Demonstration-only: cap auto-broadcast so a creeping dimension can't silently start OOMing the driver.
# (Read the current value, set it explicitly, then it is reset in the cleanup cell.)
_orig_bcast = spark.conf.get("spark.sql.autoBroadcastJoinThreshold")
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", 10 * 1024 * 1024)   # 10 MB (OSS default)
print("autoBroadcastJoinThreshold (bytes) =", spark.conf.get("spark.sql.autoBroadcastJoinThreshold"))
# On Databricks, AQE also uses a higher *runtime* switch (spark.databricks.adaptive.autoBroadcastJoinThreshold, 30 MB).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5) The metadata path — a partition-count explosion pressures the driver
# MAGIC The driver tracks **one entry per partition/task**. Fanning out to a huge partition count loads the
# MAGIC driver with **metadata/scheduling state alone** — no result collected. We measure the partition
# MAGIC count (we keep it modest so the cluster survives; the real hazard is 100,000s of partitions/files).

# COMMAND ----------

# MEASURE: repartition explodes the in-memory partition count the driver must track.
exploded = facts.repartition(4000)              # modest here; real explosions are far larger
print("partitions BEFORE:", facts.rdd.getNumPartitions(),
      "| AFTER repartition:", exploded.rdd.getNumPartitions())

# ✅ FIX: coalesce back down (and let AQE coalesce post-shuffle partitions — Lesson 05).
calmed = exploded.coalesce(200)
print("partitions after coalesce:", calmed.rdd.getNumPartitions())
# Time the wide job safely with the noop sink (no rows to the driver):
t0 = time.time(); calmed.write.format("noop").mode("overwrite").save()
print(f"job after coalesce: {time.time()-t0:.2f}s")
# Real-world: avoid repartition(500000) / millions of tiny files — that OOMs the driver on metadata alone.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Uses, edge cases & limitations (interview-ready recap)
# MAGIC - **Uses:** `maxResultSize` as a fail-fast guardrail; raise `driver.memory` **deliberately** (paired
# MAGIC   with `maxResultSize`) for a bounded local result; the `noop` sink to benchmark with zero driver data.
# MAGIC - **Edge cases:** a broadcast *almost* too big (OOMs the driver during the build, not the executors);
# MAGIC   `toPandas()` hits **both** the JVM heap and the Python driver process (overhead); a partition
# MAGIC   explosion OOMs the driver on **metadata alone**; raising `maxResultSize` without raising the heap
# MAGIC   just trades a clean abort for a real OOM.
# MAGIC - **Limitations:** the driver **does not scale out** (one fixed heap); `spark.driver.memory` is
# MAGIC   **launch-time** (cluster config + restart), while `maxResultSize` is runtime-settable; the
# MAGIC   broadcast-build and metadata OOM paths are **operational practice**, not verbatim docs (only the
# MAGIC   `collect()`/`maxResultSize` path is doc-quoted).

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cleanup — drop demo objects and RESET every changed conf
# MAGIC Leaves no state behind so the notebook is rerunnable.

# COMMAND ----------

# Reset the only conf we changed for a demo (back to its original value).
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", _orig_bcast)
print("reset autoBroadcastJoinThreshold ->", spark.conf.get("spark.sql.autoBroadcastJoinThreshold"))

# Drop the demo tables (and the schema if you created it just for this lesson).
spark.sql(f"DROP TABLE IF EXISTS {fqn}")
spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.driver_demo_result")
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")   # uncomment if the schema is disposable
print("cleanup complete — demo tables dropped, conf reset.")
