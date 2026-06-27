# Databricks notebook source

# MAGIC %md
# MAGIC # Lesson 05 — Adaptive Query Execution (AQE), hands-on
# MAGIC
# MAGIC **Goal:** *see* AQE re-optimize a plan at runtime — coalesce 200 tiny shuffle
# MAGIC partitions into a few ~64 MB chunks, split a skewed partition, and flip a
# MAGIC sort-merge join to a broadcast — and **measure** each effect in the plan, the
# MAGIC partition count, the wall-clock, and the Spark UI.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - **Any current DBR LTS** (AQE is on by default since **DBR 7.3 LTS** / **Spark 3.2.0**).
# MAGIC   AQE was *introduced* in Spark 1.6.0 but only became **default-`true` in 3.2.0**
# MAGIC   (SPARK-33679) — don't conflate the two.
# MAGIC - **Unity Catalog** enabled, with `CREATE`/`USE` on the target catalog & schema.
# MAGIC - This notebook uses **generated in-memory DataFrames** — no source data needed.
# MAGIC - **AQE is on by default.** Several demos turn the relevant lever **off** to capture
# MAGIC   the "before" state, then **reset it on** immediately after. Those toggles are for
# MAGIC   demonstration only — in production, leave AQE on.
# MAGIC
# MAGIC ## What you'll learn
# MAGIC - How AQE **coalesces** post-shuffle partitions toward the **64 MB** advisory size.
# MAGIC - How AQE **splits skewed** partitions (> **5×** median **AND** > **256 MB**).
# MAGIC - How AQE **switches sort-merge → broadcast** at runtime (OSS 10 MB vs DBX 30 MB).
# MAGIC - How to **verify** each via `df.explain(mode="formatted")`, `df.rdd.getNumPartitions()`,
# MAGIC   and `df.write.format("noop")` timing — and which **Spark UI** signal to read.
# MAGIC
# MAGIC ## How to read the results (the heart of this track)
# MAGIC - **`df.explain(mode="formatted")`** — look for `AdaptiveSparkPlan isFinalPlan=true`,
# MAGIC   `AQEShuffleRead coalesced` / `skewed`, and `SortMergeJoin` → `BroadcastHashJoin`.
# MAGIC - **Spark UI → SQL/DataFrame tab** — the DAG redraws to the **final** plan after a run;
# MAGIC   the shuffle node shows the post-shuffle partition count.
# MAGIC - **Spark UI → Stages tab** — the **task-time distribution** (min / median / max).
# MAGIC   Skew shows as `max ≫ median`; after a skew split, `max` collapses toward `median`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters & helpers (Unity Catalog three-level namespacing)

# COMMAND ----------

# Parameterize the UC namespace at the top so the notebook is portable.
dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema",  "pyspark_perf_demo", "Schema")

catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")

spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")          # no-op if it already exists / managed
spark.sql(f"CREATE SCHEMA  IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Using {catalog}.{schema}")

# COMMAND ----------

import time
from pyspark.sql import functions as F

def timed_noop(df, label=""):
    """Run the full job WITHOUT writing real output, and time it.
    The 'noop' sink forces materialization (like a write) but discards the rows —
    the clean way to time a plan without pulling data to the driver (collect() risks driver OOM)."""
    t0 = time.time()
    df.write.format("noop").mode("overwrite").save()
    dt = time.time() - t0
    print(f"[{label}] wall-clock: {dt:0.2f}s")
    return dt

# Confirm AQE is engaged before we start (should print 'true' on DBR 7.3+ / Spark 3.2+).
print("spark.sql.adaptive.enabled =", spark.conf.get("spark.sql.adaptive.enabled"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · AQE COALESCE — merge 200 tiny shuffle partitions
# MAGIC
# MAGIC **Create → stress → apply → MEASURE.** We aggregate a modest dataset that produces a
# MAGIC small result. Without AQE the shuffle leaves **200** tiny partitions; with AQE on,
# MAGIC coalesce merges them toward the **64 MB** advisory size.

# COMMAND ----------

# CREATE: a few million rows across ~1,000 store_ids -> aggregating yields a SMALL result.
sales = (spark.range(0, 8_000_000)
              .withColumn("store_id", (F.col("id") % 1000).cast("int"))
              .withColumn("amount",   (F.rand() * 100)))
print("input partitions:", sales.rdd.getNumPartitions())

# COMMAND ----------

# MEASURE the "before": turn AQE OFF (DEMO ONLY) to see the static 200-partition shuffle.
spark.conf.set("spark.sql.adaptive.enabled", "false")        # DEMO ONLY — see the baseline

agg_off = sales.groupBy("store_id").agg(F.sum("amount").alias("total"))
print("AQE OFF -> post-shuffle partitions:", agg_off.rdd.getNumPartitions())  # expect 200
agg_off.explain(mode="formatted")     # no AdaptiveSparkPlan / AQEShuffleRead in the plan
timed_noop(agg_off, "coalesce: AQE OFF")

# COMMAND ----------

# APPLY: turn AQE back ON (its default) and re-run. Coalesce sizes partitions from real stats.
spark.conf.set("spark.sql.adaptive.enabled", "true")                         # RESET — AQE stays on
spark.conf.set("spark.sql.adaptive.coalescePartitions.enabled", "true")      # default true
spark.conf.set("spark.sql.adaptive.advisoryPartitionSizeInBytes", 64 * 1024 * 1024)  # 64 MB target
spark.conf.set("spark.sql.adaptive.coalescePartitions.minPartitionSize", 1 * 1024 * 1024)  # 1 MB floor

agg_on = sales.groupBy("store_id").agg(F.sum("amount").alias("total"))
# MEASURE: the post-shuffle partition count collapses from 200 toward a handful.
print("AQE ON  -> post-shuffle partitions:", agg_on.rdd.getNumPartitions())  # e.g. ~1-8
agg_on.explain(mode="formatted")      # look for: AdaptiveSparkPlan + AQEShuffleRead coalesced
timed_noop(agg_on, "coalesce: AQE ON")

# COMMAND ----------

# MAGIC %md
# MAGIC **Spark UI signal (coalesce):** SQL/DataFrame tab → the shuffle node now reports far
# MAGIC fewer post-shuffle partitions than 200; Stages tab → the post-shuffle stage runs a
# MAGIC handful of tasks instead of 200. `getNumPartitions()` above is the quickest proof.
# MAGIC
# MAGIC **Uses / edge cases / limitations (coalesce)**
# MAGIC - *Use:* stop hand-setting `spark.sql.shuffle.partitions`; let AQE size per query.
# MAGIC - *Edge:* a map-only job (no shuffle) has nothing to coalesce — AQE needs a shuffle.
# MAGIC - *Limit:* coalescing trades a little parallelism for fewer tasks; the 64 MB advisory
# MAGIC   keeps tasks reasonable even on large results.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · AQE SPLIT PARTITIONS — break up a skewed join
# MAGIC
# MAGIC **Create → stress → apply → MEASURE.** We build a join where one "house" key holds a
# MAGIC huge share of rows. A partition is split only when it is **both** > **5×** the median
# MAGIC **and** > **256 MB**.

# COMMAND ----------

# CREATE a heavily skewed fact table: key 0 ("house account") gets ~60% of the rows.
n = 60_000_000
transactions = (spark.range(0, n)
                # 60% of rows -> account_id 0 (the hot key); the rest spread over 1..2000
                .withColumn("account_id",
                            F.when(F.rand() < 0.60, F.lit(0))
                             .otherwise((F.rand() * 2000 + 1).cast("int")))
                .withColumn("amount", (F.rand() * 500)))

accounts = (spark.range(0, 2001)
                 .withColumnRenamed("id", "account_id")
                 .withColumn("tier", (F.col("account_id") % 3)))

# Make the join a SORT-MERGE join (not broadcast) so skew-join applies:
# accounts is small, so disable auto-broadcast for THIS demo to force the shuffle path.
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", -1)   # DEMO ONLY — force sort-merge

# COMMAND ----------

# MEASURE the "before": turn the skew-join feature OFF (DEMO ONLY) -> one straggler task.
spark.conf.set("spark.sql.adaptive.enabled", "true")                  # AQE on ...
spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "false")        # ... but skew-split OFF (demo)

joined_skew_off = transactions.join(accounts, "account_id")
joined_skew_off.explain(mode="formatted")   # SortMergeJoin; AQEShuffleRead WITHOUT skewed=true
timed_noop(joined_skew_off, "skew: split OFF")   # watch one slow task in the Stages tab

# COMMAND ----------

# APPLY: turn skew-join ON (its default) with the documented thresholds, and re-run.
spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "true")                       # default true
spark.conf.set("spark.sql.adaptive.skewJoin.skewedPartitionFactor", "5.0")          # > 5x median ...
spark.conf.set("spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes",
               256 * 1024 * 1024)                                                    # ... AND > 256 MB

joined_skew_on = transactions.join(accounts, "account_id")
# MEASURE: the plan now reports the skew was detected and split.
joined_skew_on.explain(mode="formatted")    # look for: AQEShuffleRead ... skewed=true
timed_noop(joined_skew_on, "skew: split ON")     # max task time collapses toward the median

# COMMAND ----------

# MAGIC %md
# MAGIC **Spark UI signal (skew split):** Stages tab → open the join stage and read the
# MAGIC **task-time distribution**. With split OFF, `max ≫ median` (one straggler). With split
# MAGIC ON, the `max` collapses toward the `median`; the SQL-tab `AQEShuffleRead` node is tagged
# MAGIC `skewed`. If the hot partition is **5× median but < 256 MB** (or large but **< 5×**),
# MAGIC AQE will **not** split it — that residual skew is a salting job (Lesson 08).
# MAGIC
# MAGIC **Uses / edge cases / limitations (skew split)**
# MAGIC - *Use:* first line of defence against skewed joins — before manual salting.
# MAGIC - *Edge:* needs **both** thresholds; sort-merge joins only (not broadcast/shuffle-hash).
# MAGIC - *Limit:* it splits the partition but still replicates the matching side; extreme skew
# MAGIC   may still need salting.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · AQE JOINS STRATEGY — switch sort-merge → broadcast at runtime
# MAGIC
# MAGIC **Create → stress → apply → MEASURE.** A dimension that looks "big" up front but a
# MAGIC filter shrinks it to a few MB. Catalyst plans **sort-merge**; AQE sees the real size
# MAGIC after the shuffle and flips it to a **broadcast hash join**.

# COMMAND ----------

# CREATE: a big fact (events) and a dimension that is "big" until a filter shrinks it.
events = (spark.range(0, 40_000_000)
               .withColumn("country_code", (F.col("id") % 250).cast("int"))
               .withColumn("ts", F.col("id")))

# dim_country has 250 rows but ~95% inactive -> WHERE active=true leaves a TINY result.
dim_country = (spark.range(0, 250)
                    .withColumnRenamed("id", "country_code")
                    .withColumn("active", (F.col("country_code") < 12))   # only 12 active
                    .withColumn("name", F.concat(F.lit("C"), F.col("country_code").cast("string"))))

# Keep the STATIC threshold low so Catalyst does NOT broadcast up front (so AQE can switch later).
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", 10 * 1024 * 1024)  # OSS default 10 MB
spark.conf.set("spark.sql.adaptive.enabled", "true")

# COMMAND ----------

# APPLY + MEASURE: join the FILTERED dimension. The filter shrinks dim below the runtime switch.
filtered_dim = dim_country.where("active = true")
joined_switch = events.join(filtered_dim, "country_code")

# VERIFY: the FINAL (post-run) plan flips SortMergeJoin -> BroadcastHashJoin.
# explain() may show the initial plan; run an action first so AQE finalizes the plan.
timed_noop(joined_switch, "join switch")           # this run lets AQE finalize the plan
joined_switch.explain(mode="formatted")
#   AdaptiveSparkPlan isFinalPlan=true
#   +- == Final Plan ==    BroadcastHashJoin ...   <-- AQE switched it at runtime ✅
#   +- == Initial Plan ==  SortMergeJoin ...       <-- what Catalyst planned from stale estimates

# COMMAND ----------

# MAGIC %md
# MAGIC **OSS vs Databricks:** Catalyst uses the **static** `spark.sql.autoBroadcastJoinThreshold`
# MAGIC (**10 MB**) up front. AQE's **runtime** switch is what flips the join after the filter runs;
# MAGIC on **Databricks** that runtime switch is `spark.databricks.adaptive.autoBroadcastJoinThreshold`
# MAGIC (**30 MB**), higher than the OSS 10 MB. Check it (Databricks only):

# COMMAND ----------

# Read the Databricks runtime broadcast switch (exists on Databricks runtimes).
try:
    print("DBX runtime switch:", spark.conf.get("spark.databricks.adaptive.autoBroadcastJoinThreshold"))
except Exception as e:
    print("Not a Databricks runtime (OSS Spark) — uses the standard runtime switch instead.")

# COMMAND ----------

# MAGIC %md
# MAGIC **Spark UI signal (join switch):** SQL/DataFrame tab → the *initial* plan shows
# MAGIC `SortMergeJoin` with two `Exchange` nodes; after the run the **final** DAG redraws with
# MAGIC `BroadcastHashJoin` and **no `Exchange` on the big `events` side**.
# MAGIC
# MAGIC **Uses / edge cases / limitations (join switch)**
# MAGIC - *Use:* a safety net when a filter/aggregation shrinks a join side after planning.
# MAGIC - *Edge:* forcing `broadcast()` up front pre-empts AQE's runtime choice — usually fine,
# MAGIC   but you lose the adaptivity.
# MAGIC - *Limit:* only fires when the real size fits the runtime broadcast threshold; a side
# MAGIC   still too big stays sort-merge.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Photon vs AQE (one-line check)
# MAGIC
# MAGIC **Photon ≠ AQE.** Photon is Databricks' native **execution** engine; AQE is the runtime
# MAGIC **planner**. They are complementary — keep AQE on with Photon. In a Photon-enabled plan
# MAGIC you'll see `Photon`-prefixed operators *underneath* the `AdaptiveSparkPlan` root.

# COMMAND ----------

# Whether Photon ran is visible in the plan operators / Spark UI; this is informational only.
try:
    print("Photon enabled:", spark.conf.get("spark.databricks.photon.enabled"))
except Exception:
    print("Photon flag not present on this runtime.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Cleanup & reset — leave no state behind
# MAGIC
# MAGIC Reset every `spark.conf` we changed back to its default so the notebook is rerunnable,
# MAGIC and drop the demo schema. **AQE stays ON** (its default).

# COMMAND ----------

# Reset all confs touched in the demos to their defaults.
spark.conf.set("spark.sql.adaptive.enabled", "true")                                 # default true
spark.conf.set("spark.sql.adaptive.coalescePartitions.enabled", "true")              # default true
spark.conf.set("spark.sql.adaptive.advisoryPartitionSizeInBytes", 64 * 1024 * 1024)  # 64 MB default
spark.conf.set("spark.sql.adaptive.coalescePartitions.minPartitionSize", 1 * 1024 * 1024)  # 1 MB default
spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "true")                        # default true
spark.conf.set("spark.sql.adaptive.skewJoin.skewedPartitionFactor", "5.0")           # default 5.0
spark.conf.set("spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes", 256 * 1024 * 1024)  # 256 MB
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", 10 * 1024 * 1024)             # OSS default 10 MB

# Drop the demo schema (we created no managed tables, but this clears the namespace).
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Reset confs to defaults and dropped the demo schema. AQE remains ON.")
