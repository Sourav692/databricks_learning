# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 11 — Bucketing to eliminate the shuffle
# MAGIC
# MAGIC **Goal:** see the join `Exchange` (shuffle) *disappear* when two big tables are bucketed
# MAGIC by the same key into the same number of buckets — and see it *return* when the bucket
# MAGIC counts mismatch.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - Any current **DBR LTS** cluster; **Unity Catalog** enabled (we use three-level
# MAGIC   `catalog.schema.table` names and `saveAsTable`).
# MAGIC - **AQE is on by default** (DBR 7.3 LTS+). AQE coalesces *post-shuffle* partitions; it
# MAGIC   never retro-fits bucketing, so it does not hide the effect we are measuring. We leave
# MAGIC   AQE on. We *do* temporarily set `spark.sql.autoBroadcastJoinThreshold = -1` so the
# MAGIC   planner cannot quietly broadcast our small demo tables — that lets us see the
# MAGIC   sort-merge shuffle (the "before") clearly. We reset it in the cleanup cell.
# MAGIC - `spark.sql.sources.bucketing.enabled` is **true** by default (since Spark 2.0) — you
# MAGIC   do not set it to use bucketing; you would only set it `false` to disable.
# MAGIC
# MAGIC ## What you will learn
# MAGIC - How to write a bucketed table: `df.write.bucketBy(N, key).sortBy(key).saveAsTable(...)`.
# MAGIC - Why two tables bucketed by the **same key into the same N** join with **no `Exchange`**.
# MAGIC - That mismatched bucket counts bring the shuffle back, and how
# MAGIC   `spark.sql.bucketing.coalesceBucketsInJoin.enabled` (default **false**, since 3.1) rescues
# MAGIC   the multiple-of case.
# MAGIC - That bucketing requires `saveAsTable()` — it is **not** supported for `save`/`insertInto`/`jdbc`.
# MAGIC
# MAGIC ## How to read the result (Spark UI)
# MAGIC - Primary evidence: **`df.explain(mode="formatted")`** — look for `SortMergeJoin` and
# MAGIC   whether there is an **`Exchange hashpartitioning(...)`** under each branch. No Exchange =
# MAGIC   shuffle eliminated.
# MAGIC - **Spark UI -> SQL / DataFrame tab:** the bucketed query's DAG has **no Exchange node**
# MAGIC   before the join; the **Stages** tab shows the shuffle stages (large Shuffle Read/Write)
# MAGIC   are gone.
# MAGIC - We time each plan with the **`noop`** sink (runs the full job, writes nothing) instead of
# MAGIC   `collect()` (which would pull data to the driver and risk driver OOM — Lesson 03).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters — Unity Catalog namespacing (edit these for your workspace)

# COMMAND ----------

# Three-level UC namespacing. Delta is the default table format (no `USING DELTA` needed).
catalog = "main"
schema  = "pyspark_perf_demo"
N       = 8          # bucket count used for the "matching" demo (small for a demo; use 100s in prod)

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")

# Fully-qualified names we will create
TX_RAW   = f"{catalog}.{schema}.transactions_raw"
ACC_RAW  = f"{catalog}.{schema}.accounts_raw"
TX_BKT   = f"{catalog}.{schema}.transactions_bucketed"
ACC_BKT  = f"{catalog}.{schema}.accounts_bucketed"
ACC_BKT4 = f"{catalog}.{schema}.accounts_bucketed_4"   # mismatched-count table (N/2 buckets)

print("Working in", f"{catalog}.{schema}", "| bucket count N =", N)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE — generate two "big" tables that join on `account_id`
# MAGIC
# MAGIC We use `spark.range(...)` + `withColumn` to generate data at scale. `transactions` is the
# MAGIC fact side (many rows per account); `accounts` is the dimension side (one row per account).
# MAGIC Both are far too big to broadcast in the real world — the case bucketing is built for.

# COMMAND ----------

from pyspark.sql.functions import col, expr, rand

N_ACCOUNTS = 2_000_000        # distinct account_ids
N_TX       = 20_000_000       # transaction rows (~10 per account)

# Fact table: 20M transactions, each pointing at a random account_id.
transactions = (spark.range(N_TX)
    .withColumn("account_id", (col("id") % N_ACCOUNTS).cast("long"))   # join key
    .withColumn("amount", (rand() * 1000).cast("decimal(10,2)"))
    .withColumn("ts", expr("timestamp_millis(1700000000000 + id)"))
    .select("account_id", "amount", "ts"))

# Dimension table: one row per account_id (still 2M rows — not broadcastable).
accounts = (spark.range(N_ACCOUNTS)
    .withColumnRenamed("id", "account_id")                              # join key
    .withColumn("segment", expr("CASE WHEN account_id % 3 = 0 THEN 'retail' "
                                "WHEN account_id % 3 = 1 THEN 'smb' ELSE 'enterprise' END"))
    .withColumn("region", expr("concat('r', cast(account_id % 12 as string))")))

# Persist both as plain (un-bucketed) tables so the "before" join reads from disk like prod.
transactions.write.mode("overwrite").saveAsTable(TX_RAW)
accounts.write.mode("overwrite").saveAsTable(ACC_RAW)

print("rows: transactions_raw =", spark.table(TX_RAW).count(),
      "| accounts_raw =", spark.table(ACC_RAW).count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · STRESS + MEASURE the "before" — an unbucketed big-vs-big join
# MAGIC
# MAGIC We disable auto-broadcast so the planner cannot sidestep the demo by broadcasting a small
# MAGIC table. The result is a textbook **Shuffle Sort-Merge Join**: two `Exchange` nodes.
# MAGIC
# MAGIC **Look for:** `SortMergeJoin` with `Exchange hashpartitioning(account_id, 200)` under
# MAGIC *both* branches. That is the shuffle we are about to eliminate.

# COMMAND ----------

# DEMO ONLY: force a shuffle join by disabling auto-broadcast. Reset in the cleanup cell.
_old_broadcast = spark.conf.get("spark.sql.autoBroadcastJoinThreshold")
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", -1)   # -1 = never auto-broadcast

tx_raw  = spark.table(TX_RAW)
acc_raw = spark.table(ACC_RAW)

joined_before = tx_raw.join(acc_raw, "account_id")

# MEASURE 1: the plan — the primary evidence in this track.
joined_before.explain(mode="formatted")
#   expect: SortMergeJoin
#           :- Sort  +- Exchange hashpartitioning(account_id, 200)   <- both sides shuffled ❌
#           +- Sort  +- Exchange hashpartitioning(account_id, 200)

# COMMAND ----------

# MEASURE 2: in-memory partition count of the join result (post-shuffle).
print("partitions (unbucketed join):", joined_before.rdd.getNumPartitions())

# MEASURE 3: wall-clock via the noop sink (runs the full job, writes nothing -> clean timing).
import time
t0 = time.time()
joined_before.write.format("noop").mode("overwrite").save()
print(f"unbucketed join wall-clock: {time.time() - t0:.1f}s")
# Spark UI -> SQL tab: two Exchange nodes; Stages tab: large Shuffle Read/Write. Note the time.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · APPLY — write both tables bucketed by `account_id` into the SAME N
# MAGIC
# MAGIC `bucketBy(N, key)` hash-partitions into N files; `sortBy(key)` sorts within each bucket so
# MAGIC the read-side join can skip its Sort too; `saveAsTable(...)` is **required** — bucketing is
# MAGIC not supported for `save`/`insertInto`/`jdbc`. This is the one-time write-side shuffle.

# COMMAND ----------

# Bucket BOTH tables identically: same key (account_id), same count (N).
(spark.table(TX_RAW).write
    .bucketBy(N, "account_id").sortBy("account_id")
    .mode("overwrite")
    .saveAsTable(TX_BKT))

(spark.table(ACC_RAW).write
    .bucketBy(N, "account_id").sortBy("account_id")     # SAME key, SAME N as above
    .mode("overwrite")
    .saveAsTable(ACC_BKT))

# MEASURE: confirm the stored bucket spec (Num Buckets / Bucket Columns / Sort Columns).
spark.sql(f"DESCRIBE EXTENDED {TX_BKT}").show(60, False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · MEASURE the "after" — the bucketed join has NO `Exchange`
# MAGIC
# MAGIC **Look for:** `SortMergeJoin` with a `FileScan` directly under each branch and **no
# MAGIC `Exchange`** (and often no separate `Sort`, because `sortBy` already ordered each bucket).
# MAGIC In the Spark UI SQL tab the Exchange node is simply absent.

# COMMAND ----------

t = spark.table(TX_BKT)
a = spark.table(ACC_BKT)
joined_after = t.join(a, "account_id")          # same key both tables are bucketed on

# MEASURE 1: the plan — Exchange should be GONE under both branches.
joined_after.explain(mode="formatted")
#   expect: SortMergeJoin
#           :- FileScan ... transactions_bucketed   <- no Exchange ✅
#           +- FileScan ... accounts_bucketed        <- no Exchange ✅

# COMMAND ----------

# MEASURE 2 + 3: partitions + timing. The shuffle stages are gone, so this should be faster.
print("partitions (bucketed join):", joined_after.rdd.getNumPartitions())   # == N (the bucket count)

import time
t0 = time.time()
joined_after.write.format("noop").mode("overwrite").save()
print(f"bucketed join wall-clock: {time.time() - t0:.1f}s")
# Compare to the unbucketed time in cell 2. Spark UI SQL tab: no Exchange before the join.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · EDGE CASE — mismatched bucket counts bring the shuffle back
# MAGIC
# MAGIC We write `accounts` again with **N/2** buckets. Now the two sides have different
# MAGIC `HashPartitioning`, so by default the planner shuffles one side to reconcile — and the
# MAGIC `Exchange` returns. `coalesceBucketsInJoin` (default **false**, since 3.1) rescues this
# MAGIC *only* because N is an exact multiple of N/2.

# COMMAND ----------

# Re-bucket accounts into N/2 buckets (mismatched vs the N-bucket transactions table).
(spark.table(ACC_RAW).write
    .bucketBy(N // 2, "account_id").sortBy("account_id")
    .mode("overwrite")
    .saveAsTable(ACC_BKT4))

a4 = spark.table(ACC_BKT4)

# 5a: mismatched counts (N vs N/2) with coalesce OFF (the default) -> shuffle returns.
spark.conf.set("spark.sql.bucketing.coalesceBucketsInJoin.enabled", False)  # explicit: this is the default
mismatch = t.join(a4, "account_id")
mismatch.explain(mode="formatted")
#   expect: SortMergeJoin with an Exchange hashpartitioning(account_id, N) under one side ❌

# COMMAND ----------

# 5b: same join, coalesce ON. N is a multiple of N/2, so the larger side is coalesced -> no shuffle.
spark.conf.set("spark.sql.bucketing.coalesceBucketsInJoin.enabled", True)   # opt in (not default)
mismatch_coalesced = t.join(a4, "account_id")
mismatch_coalesced.explain(mode="formatted")
#   expect: SortMergeJoin with NO Exchange — larger side read as N/2 coalesced buckets ✅
# Best practice is still to pick ONE bucket count for both tables and not rely on this.

# Reset the coalesce conf to its default so later cells/sessions are unaffected.
spark.conf.set("spark.sql.bucketing.coalesceBucketsInJoin.enabled", False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · BONUS — partition (prune) + bucket (no shuffle) together
# MAGIC
# MAGIC Partition by a **low-cardinality** filter column to enable pruning (Lesson 07) **and**
# MAGIC bucket by the **high-cardinality** join key to remove the shuffle. They are complementary.

# COMMAND ----------

from pyspark.sql.functions import to_date

events = (spark.range(5_000_000)
    .withColumn("user_id", (col("id") % 500_000).cast("long"))
    .withColumn("event_date", to_date(expr("date_add(date'2026-06-01', cast(id % 7 as int))")))
    .select("user_id", "event_date"))

EVENTS_BPB = f"{catalog}.{schema}.events_bpb"
(events.write
    .partitionBy("event_date")                       # directories -> partition pruning
    .bucketBy(256, "user_id").sortBy("user_id")       # hash files  -> no shuffle on user_id joins
    .mode("overwrite")
    .saveAsTable(EVENTS_BPB))

# MEASURE: a date-filtered read shows PartitionFilters; a user_id self-join shows no Exchange.
spark.table(EVENTS_BPB).where("event_date = date'2026-06-03'").explain(mode="formatted")
#   expect: PartitionFilters: [isnotnull(event_date), (event_date = 2026-06-03)]  <- pruning ✅

# COMMAND ----------

# MAGIC %md
# MAGIC ## Uses, edge cases & limitations (quick reference)
# MAGIC
# MAGIC **Uses** — big ⋈ big on the same key, run repeatedly; repeated `groupBy(key)`
# MAGIC aggregations; partition + bucket together (prune by date, no-shuffle on the join key).
# MAGIC
# MAGIC **Edge cases** — mismatched bucket counts re-introduce the shuffle (coalesce only helps for
# MAGIC multiple-of counts); bucketing a column you never join on wastes the write-time shuffle;
# MAGIC AQE never retro-fits bucketing; if a side is small, just broadcast it (Lesson 02).
# MAGIC
# MAGIC **Limitations** — `saveAsTable()` only (not `save`/`insertInto`/`jdbc`); BOTH sides must be
# MAGIC bucketed the same way; re-bucketing rewrites the whole table; don't confuse the classic
# MAGIC `spark.sql.sources.bucketing.enabled` (this lesson) with the DataSource-V2
# MAGIC `spark.sql.sources.v2.bucketing.enabled` (since 3.3, storage-partitioned join).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · CLEANUP — drop demo tables and reset every changed conf
# MAGIC
# MAGIC Leaves no state behind so the notebook is rerunnable.

# COMMAND ----------

# Drop the demo tables.
for t_name in [TX_RAW, ACC_RAW, TX_BKT, ACC_BKT, ACC_BKT4, EVENTS_BPB]:
    spark.sql(f"DROP TABLE IF EXISTS {t_name}")

# Reset confs we changed back to their defaults.
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", _old_broadcast)         # restore broadcast threshold
spark.conf.set("spark.sql.bucketing.coalesceBucketsInJoin.enabled", False)     # default (since 3.1)

# Optionally drop the demo schema if you created it solely for this lesson:
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")

print("Cleanup complete. autoBroadcastJoinThreshold restored to:",
      spark.conf.get("spark.sql.autoBroadcastJoinThreshold"))
