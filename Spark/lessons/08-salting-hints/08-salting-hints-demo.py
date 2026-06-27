# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 08 — Data skew: salting & SQL hints
# MAGIC
# MAGIC **Goal:** create a genuinely skewed key, *see* the straggler in the Spark UI, then fix it
# MAGIC with (a) a two-stage **salted aggregation**, (b) a **salted join**, and (c) **SQL hints** —
# MAGIC measuring the effect at every step with `df.explain()`, partition counts, and timing.
# MAGIC
# MAGIC ### Prerequisites
# MAGIC - Any **DBR LTS** (e.g. 12.2 LTS+); **AQE is on by default** since DBR 7.3 / Spark 3.2,
# MAGIC   and so is **AQE skew join** (`spark.sql.adaptive.skewJoin.enabled=true`).
# MAGIC - To show the *before* (un-fixed) state, some cells **temporarily disable** AQE skew handling
# MAGIC   and/or auto-broadcast, then **reset** them — each is clearly commented "demo only".
# MAGIC - Unity Catalog enabled; permission to create a schema/tables in the target catalog.
# MAGIC - No source data needed — we generate skew with `spark.range(...)` + `rand()`/`when`.
# MAGIC
# MAGIC ### What you'll learn
# MAGIC - How to **confirm skew** in the Spark UI (Max task time ≫ Median; Max shuffle-read ≫ 75th pct).
# MAGIC - **AQE skew join** as the first, zero-code line of defence.
# MAGIC - **Salting an aggregation** (two-stage group-by) and **salting a join** (salt one side, explode the other).
# MAGIC - **Salting only the hot keys** to keep the N× blow-up proportional.
# MAGIC - **Join + partitioning SQL hints** and how to verify them in the plan.
# MAGIC
# MAGIC ### How to read the result (Spark UI)
# MAGIC - **Stages tab → the slow stage → task table:** compare **Min / Median / Max** duration and
# MAGIC   **Shuffle Read Size / Records**. Skew = **Max ≫ Median**; a fix makes **Max ≈ Median**.
# MAGIC - **SQL / DataFrame tab:** the DAG; look for the `Exchange`, `HashAggregate`, `SortMergeJoin`,
# MAGIC   and `AQEShuffleRead` (which shows `skewed=true` when AQE splits a skewed partition).
# MAGIC - **The plan is the primary evidence in this track** — `df.explain(mode="formatted")`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters & setup (Unity Catalog three-level namespacing)

# COMMAND ----------

# Parameterize catalog/schema at the top — three-level UC names: catalog.schema.table.
catalog = "main"
schema  = "pyspark_perf_demo"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")

from pyspark.sql import functions as F
import time

# Small timing helper: the noop sink runs the FULL job without writing real output —
# the clean way to time a plan without pulling data to the driver (no collect()).
def timed(df, label):
    t0 = time.time()
    df.write.format("noop").mode("overwrite").save()
    dt = time.time() - t0
    print(f"{label:38s} {dt:6.1f}s")
    return dt

print("Spark:", spark.version)
print("AQE enabled            :", spark.conf.get("spark.sql.adaptive.enabled"))
print("AQE skew join enabled  :", spark.conf.get("spark.sql.adaptive.skewJoin.enabled"))
print("shuffle.partitions     :", spark.conf.get("spark.sql.shuffle.partitions"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE — generate a skewed fact and a small dimension
# MAGIC
# MAGIC `transactions` is skewed on `merchant_id`: one **mega-merchant** holds ~70% of all rows.
# MAGIC `merchants` is a small, unskewed dimension. This is the classic enterprise skew shape.

# COMMAND ----------

ROWS = 60_000_000        # 60M fact rows — enough to make skew visible in the UI
N_MERCHANTS = 5_000      # distinct non-hot merchants

# ~70% of rows go to merchant_id = 0 (the mega-merchant); the rest spread over 1..N_MERCHANTS.
tx = (spark.range(ROWS)
      .withColumn("merchant_id",
                  F.when(F.rand() < F.lit(0.70), F.lit(0))                       # hot key
                   .otherwise((F.rand() * N_MERCHANTS).cast("int") + 1))
      .withColumn("amount", (F.rand() * 100).cast("double")))

merchants = (spark.range(N_MERCHANTS + 1)
             .withColumnRenamed("id", "merchant_id")
             .withColumn("merchant_name", F.concat(F.lit("merchant_"), F.col("merchant_id"))))

# Quick sanity check: how dominant is the hot key? (this itself triggers a small shuffle)
print("Top merchant_id row counts (skew check):")
tx.groupBy("merchant_id").count().orderBy(F.desc("count")).show(5, truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · STRESS + MEASURE the "before" — a skewed aggregation
# MAGIC
# MAGIC A plain `groupBy("merchant_id").sum("amount")` sends ~70% of rows (the hot key) to a
# MAGIC **single partition** → one straggler task. We time it and point at the Spark UI signal.

# COMMAND ----------

agg_naive = tx.groupBy("merchant_id").agg(F.sum("amount").alias("total"))

# MEASURE 1 — the plan: one HashAggregate → Exchange → HashAggregate (the shuffle is where skew bites).
agg_naive.explain(mode="formatted")

# MEASURE 2 — wall clock (forces the full job via the noop sink).
timed(agg_naive, "agg: naive groupBy (skewed)")

# >>> SPARK UI: open the Stages tab for this job's shuffle stage. Look at the task table:
#     the MAX task duration and MAX "Shuffle Read Size / Records" are FAR above the MEDIAN —
#     that single task is processing the mega-merchant. That is the straggler.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · APPLY + MEASURE — salt the aggregation (two-stage group-by)
# MAGIC
# MAGIC Stage 1: partial aggregate on `(merchant_id, salt)` so the hot key spreads across `N` tasks.
# MAGIC Stage 2: re-aggregate the partials by the real `merchant_id`. `sum` re-aggregates trivially.

# COMMAND ----------

N = 32  # salt buckets — split the hot key across up to 32 partitions

# Stage 1 — partial aggregate on the SALTED key.
partial = (tx
           .withColumn("salt", (F.rand() * N).cast("int"))        # salt in 0..N-1
           .groupBy("merchant_id", "salt")
           .agg(F.sum("amount").alias("partial_total")))

# Stage 2 — drop the salt, re-aggregate the N partials by the REAL key.
agg_salted = (partial
              .groupBy("merchant_id")
              .agg(F.sum("partial_total").alias("total")))

# MEASURE — the plan now shows TWO HashAggregate/Exchange pairs (partial then final).
agg_salted.explain(mode="formatted")
timed(agg_salted, "agg: salted two-stage")

# >>> SPARK UI: the partial-agg stage's MAX task time is now close to its MEDIAN — the single
#     straggler is gone; the hot key's work is shared across N tasks.
# NOTE: for an AVERAGE, carry sum + count partials and divide at the end — never avg the avgs.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · STRESS + MEASURE the "before" — a skewed join (AQE skew join OFF)
# MAGIC
# MAGIC To *see* the un-fixed join straggler, we **temporarily disable AQE skew join** (demo only)
# MAGIC and disable auto-broadcast so the join is a real shuffle sort-merge on the skewed key.

# COMMAND ----------

# --- demo only: turn OFF the automatic fixes to expose the raw skew ---
spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "false")   # demo only — reset in step 7
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", -1)       # demo only — force a shuffle join

join_naive = tx.join(merchants, "merchant_id")

# MEASURE — SortMergeJoin with two Exchanges; the hot key lands in one partition.
join_naive.explain(mode="formatted")
timed(join_naive, "join: naive (skew, AQE-skew OFF)")

# >>> SPARK UI Stages tab: the join's shuffle stage shows MAX task time ≫ MEDIAN, and that
#     straggler task likely shows non-zero "spill (memory)/(disk)". This is the cluster
#     sitting idle waiting on one core.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · APPLY + MEASURE — AQE skew join (the zero-code first defence)
# MAGIC
# MAGIC Re-enable AQE skew join and re-run the **same** join. AQE splits the skewed partition at
# MAGIC runtime — no query change. This is what you should always try **before** salting.

# COMMAND ----------

spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "true")    # back ON (the default)
# A partition is "skewed" if size > skewedPartitionFactor (5.0) × median AND
# > skewedPartitionThresholdInBytes (256 MB) — then AQE splits it into sub-partitions.

join_aqe = tx.join(merchants, "merchant_id")
join_aqe.explain(mode="formatted")
timed(join_aqe, "join: AQE skew join ON")

# >>> SPARK UI SQL tab: the join's AQEShuffleRead node now shows skewed=true; in the Stages
#     tab the MAX task time drops toward the MEDIAN. If this fully balances, you are DONE —
#     no salting needed. Salt only when a single key is too big for AQE's split to help.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · APPLY + MEASURE — salt the join (salt one side, explode the other)
# MAGIC
# MAGIC When AQE's split isn't enough (a single key bigger than the split target), salt manually.
# MAGIC We salt **only the hot key** so the dimension only blows up N× for that one key, not all.

# COMMAND ----------

hot = [0]  # the mega-merchant id confirmed from step 1 (top of the skew check)

# 1) Salt the SKEWED side: hot key gets a real 0..N-1 salt; everyone else a constant 0.
tx_salted = tx.withColumn(
    "salt",
    F.when(F.col("merchant_id").isin(hot), (F.rand() * N).cast("int")).otherwise(F.lit(0)))

# 2) EXPLODE the dimension ×N for hot keys only; cold rows keep a single salt=0 copy.
m_hot = (merchants.filter(F.col("merchant_id").isin(hot))
         .withColumn("salt", F.explode(F.array([F.lit(i) for i in range(N)]))))
m_cold = merchants.filter(~F.col("merchant_id").isin(hot)).withColumn("salt", F.lit(0))
merchants_salted = m_hot.unionByName(m_cold)

# 3) Join on the COMBINED (merchant_id, salt) key.
join_salted = tx_salted.join(merchants_salted, on=["merchant_id", "salt"], how="inner")

join_salted.explain(mode="formatted")           # SortMergeJoin on (merchant_id, salt)
print("salted dim row count blow-up:", merchants_salted.count(), "vs base", merchants.count())
timed(join_salted, "join: salted hot-key only")

# >>> SPARK UI Stages tab: the join stage's MAX task time now tracks the MEDIAN. The dimension
#     grew by N× ONLY for the hot key — the proportional, correct way to salt a join.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · SQL hints — join strategy & partitioning (DataFrame API + SQL)
# MAGIC
# MAGIC Hints override the planner. `.hint()` / `broadcast()` in the DataFrame API; `/*+ ... */`
# MAGIC right after `SELECT` in SQL. Verify the forced node / Exchange in the plan.

# COMMAND ----------

from pyspark.sql.functions import broadcast

# Reset auto-broadcast to its default before showing the hints (we forced -1 in step 4).
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", 10 * 1024 * 1024)  # 10 MB (OSS default)

# DataFrame-API hints — each maps to a SQL /*+ ... */ hint:
broadcast(merchants).join(tx, "merchant_id").explain(mode="formatted")   # BROADCAST -> BroadcastHashJoin
tx.hint("merge").join(merchants, "merchant_id").explain(mode="formatted")        # MERGE -> SortMergeJoin
tx.hint("shuffle_hash").join(merchants, "merchant_id").explain(mode="formatted") # SHUFFLE_HASH -> ShuffledHashJoin

# Partitioning hints (DataFrame API):
print("repartition(200, merchant_id) ->", tx.repartition(200, "merchant_id").rdd.getNumPartitions(), "partitions")
print("coalesce(50)                  ->", tx.coalesce(50).rdd.getNumPartitions(), "partitions")

# COMMAND ----------

# MAGIC %md
# MAGIC ### The same hints in Spark SQL
# MAGIC Register temp views and run the hints with `/*+ ... */` immediately after `SELECT`.

# COMMAND ----------

# MAGIC %python
tx.createOrReplaceTempView("tx_v")
merchants.createOrReplaceTempView("merchants_v")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Join-strategy hint (priority BROADCAST > MERGE > SHUFFLE_HASH > SHUFFLE_REPLICATE_NL).
# MAGIC -- VERIFY: the plan below shows BroadcastHashJoin (no big-side Exchange).
# MAGIC EXPLAIN FORMATTED
# MAGIC SELECT /*+ BROADCAST(m) */ t.merchant_id, m.merchant_name, t.amount
# MAGIC FROM   tx_v t
# MAGIC JOIN   merchants_v m ON t.merchant_id = m.merchant_id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Partitioning hints. REPARTITION = full shuffle to n / by col; COALESCE = no shuffle.
# MAGIC -- REBALANCE evens partition sizes but is IGNORED unless AQE is enabled (it is, by default).
# MAGIC EXPLAIN FORMATTED
# MAGIC SELECT /*+ REPARTITION(200, merchant_id) */ * FROM tx_v;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Uses, edge cases & limitations (interview-relevant)
# MAGIC
# MAGIC **Uses**
# MAGIC - Skewed `groupBy` → two-stage salted aggregation; skewed join AQE can't split → salted join.
# MAGIC - Stale-stats / mis-estimated join → a join-strategy hint; bad layout before a wide op → a partitioning hint.
# MAGIC
# MAGIC **Edge cases**
# MAGIC - **N× blow-up** — explode only the hot keys (constant `salt=0` for the rest); too-large `N` makes tiny partitions.
# MAGIC - **Non-composable aggregates** — `avg`/`countDistinct`/`percentile` don't re-aggregate naively; carry `sum`+`count` for `avg`.
# MAGIC - **`REBALANCE` is a no-op with AQE off** — a silently ignored hint is a classic "why didn't it change?" trap.
# MAGIC - **Skew AQE can't fix** — a single key bigger than the split target needs salting (it sub-divides the key itself).
# MAGIC
# MAGIC **Limitations**
# MAGIC - Salting is manual, intrusive, and adds a shuffle — only worth it when one key truly dominates (Max ≫ Median).
# MAGIC - Hints override the optimizer; a hint frozen into code can become wrong as data grows. Prefer AQE; hint with a known reason.
# MAGIC - Salting helps **skew**, not raw size — a huge non-skewed join still needs the full shuffle (or bucketing, Lesson 11).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · Cleanup & reset (leave no state behind)

# COMMAND ----------

# Reset every spark.conf we changed back to its default.
spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "true")            # default
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", 10 * 1024 * 1024) # 10 MB OSS default

# Drop temp views and the demo schema (no persistent tables were created here).
spark.catalog.dropTempView("tx_v")
spark.catalog.dropTempView("merchants_v")
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")

print("Cleanup complete. Confs reset:",
      spark.conf.get("spark.sql.adaptive.skewJoin.enabled"),
      spark.conf.get("spark.sql.autoBroadcastJoinThreshold"))
