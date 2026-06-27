# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 02 — Joins: Sort-Merge vs Shuffle-Hash vs Broadcast
# MAGIC
# MAGIC **Goal:** *see* the same logical join compile to two physically different plans — a
# MAGIC full double-shuffle **SortMergeJoin** vs a shuffle-free **BroadcastHashJoin** — and
# MAGIC measure the difference.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - Any DBR LTS cluster (Unity Catalog enabled). No source data needed — we generate it.
# MAGIC - **AQE is on by default since DBR 7.3 LTS.** AQE can flip a sort-merge join to a
# MAGIC   broadcast join *at runtime*, which would hide the "before" state we want to show.
# MAGIC   So this demo **toggles `spark.sql.autoBroadcastJoinThreshold`** (OSS default
# MAGIC   **10 MB = 10485760 bytes**; **`-1` disables** auto-broadcast) to force the
# MAGIC   sort-merge "before", then re-enables it for the "after". The final cell **resets**
# MAGIC   the conf so the notebook leaves no session state behind.
# MAGIC
# MAGIC ## What you'll learn
# MAGIC - Why a **big fact ⋈ small dimension** join should *broadcast* the small side and
# MAGIC   never shuffle the big side.
# MAGIC - How to read the strategy off `df.explain(mode="formatted")`: `SortMergeJoin` + two
# MAGIC   `Exchange` nodes vs `BroadcastHashJoin` + one `BroadcastExchange` and **no** `Exchange`
# MAGIC   on the orders side.
# MAGIC - How to force a broadcast three ways: the OSS-default 10 MB threshold, an explicit
# MAGIC   `broadcast()`, and the SQL `/*+ BROADCAST(country) */` hint.
# MAGIC - How to time a plan cleanly with the `noop` sink and check in-memory partition counts.
# MAGIC
# MAGIC ## How to read the Spark UI (the heart of this track)
# MAGIC - **SQL / DataFrame tab** → open the query's DAG:
# MAGIC   - **`SortMergeJoin`** fed by **two `Exchange`** nodes (+ `Sort`) = a full shuffle join.
# MAGIC   - **`BroadcastHashJoin`** fed by a single **`BroadcastExchange`** on the small side,
# MAGIC     with **no `Exchange` on the orders (big) side** = the broadcast worked.
# MAGIC - **Stages tab** → the shuffle join adds stages with large **Shuffle Read / Shuffle
# MAGIC   Write**; the broadcast job shuffles neither side of `orders`. Watch the task-time
# MAGIC   distribution (min/median/max) too.
# MAGIC
# MAGIC > Facts (autoBroadcastJoinThreshold = 10 MB, `-1` disables, `preferSortMergeJoin = true`,
# MAGIC > plan node names) are from the verified fact sheet, section 2 — JOINS.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Parameters — Unity Catalog three-level namespacing
# MAGIC Parameterize `catalog.schema` at the top so the notebook is portable. This lesson is a
# MAGIC pure-DataFrame demo (generated in memory), so we don't actually need a table — but we
# MAGIC create the schema for consistency with the track and clean it up at the end.

# COMMAND ----------

catalog = "main"
schema  = "pyspark_perf_demo"

# Three-level namespace: catalog.schema.table
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Using {catalog}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. CREATE — a big-ish `orders` fact + a tiny `country` dimension
# MAGIC The classic enterprise shape: a large fact table joined to a small lookup dimension.
# MAGIC `orders` gets a `country_code` foreign key (0..9); `country` is just 10 rows. In a real
# MAGIC warehouse this is `orders` (TB) ⋈ `country_codes` (KB).

# COMMAND ----------

from pyspark.sql import functions as F

# Big-ish fact table: 50M rows. `country_code` is the join key (low cardinality, 0..9).
# spark.range(...) generates data at a chosen scale with no source files.
orders = (
    spark.range(0, 50_000_000)
    .withColumnRenamed("id", "order_id")
    .withColumn("country_code", (F.col("order_id") % 10).cast("int"))
    .withColumn("amount", (F.rand() * 1000).cast("double"))
)

# Tiny dimension: 10 rows, a few bytes. Well under the 10 MB auto-broadcast threshold.
country = spark.createDataFrame(
    [(i, f"Country_{i}") for i in range(10)],
    schema="country_code int, country_name string",
)

print("orders partitions:", orders.rdd.getNumPartitions())   # in-memory partition count
print("country rows     :", country.count())                  # tiny — the broadcast candidate

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. MEASURE the "before" — force a SortMergeJoin (auto-broadcast disabled)
# MAGIC We set `spark.sql.autoBroadcastJoinThreshold = -1` to **disable auto-broadcast**. With
# MAGIC no broadcast allowed and `spark.sql.join.preferSortMergeJoin = true` (the default),
# MAGIC Spark must shuffle **both** sides → a **SortMergeJoin**. This is the expensive plan we
# MAGIC want to make visible. *(Demonstration only — we reset it in step 3 and at cleanup.)*

# COMMAND ----------

# -1 = never auto-broadcast (fact-sheet §2). This forces the big-vs-small join to shuffle.
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", "-1")

smj = orders.join(country, on="country_code", how="inner")

# Read the plan: expect SortMergeJoin fed by TWO Exchange nodes (one per side) + Sort.
print("=== BEFORE: auto-broadcast DISABLED — expect SortMergeJoin + 2x Exchange ===")
smj.explain(mode="formatted")

# COMMAND ----------

# Time the shuffle join with the noop sink: it runs the FULL job (both shuffles) without
# writing real output — far safer than .collect(), which would pull rows to the driver.
import time

t0 = time.time()
smj.write.format("noop").mode("overwrite").save()
print(f"SortMergeJoin wall-clock: {time.time() - t0:.2f} s")
# In the Spark UI > SQL tab: two Exchange nodes feeding the SortMergeJoin, and the Stages
# tab shows large Shuffle Read/Write for BOTH sides — including the 50M-row orders table.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. APPLY — re-enable the threshold AND force the broadcast explicitly
# MAGIC Two changes, each sufficient on its own:
# MAGIC 1. Reset `autoBroadcastJoinThreshold` to the OSS default **10 MB** so a sub-10 MB side
# MAGIC    auto-broadcasts again.
# MAGIC 2. Wrap the small side in `broadcast(country)` — an explicit hint that survives bad
# MAGIC    size estimates. Either way the planner ships `country` to every executor and the
# MAGIC    big `orders` table is **never shuffled**.

# COMMAND ----------

from pyspark.sql.functions import broadcast

# Reset to the documented OSS default: 10 MB = 10485760 bytes (fact-sheet §2).
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", str(10 * 1024 * 1024))

# broadcast() makes the intent explicit regardless of the size estimate.
bhj = orders.join(broadcast(country), on="country_code", how="inner")

# Read the plan: expect BroadcastHashJoin fed by a single BroadcastExchange on the country
# side, and NO Exchange on the orders side.
print("=== AFTER: broadcast(country) — expect BroadcastHashJoin + BroadcastExchange, no orders Exchange ===")
bhj.explain(mode="formatted")

# COMMAND ----------

# Time the broadcast join — the big side is read once and never shuffled, so this should be
# noticeably faster than the SortMergeJoin above.
t0 = time.time()
bhj.write.format("noop").mode("overwrite").save()
print(f"BroadcastHashJoin wall-clock: {time.time() - t0:.2f} s")
# In the Spark UI > SQL tab: a BroadcastExchange on the country branch and NO Exchange on
# the orders branch; the Stages tab shows the big orders shuffle is gone.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. The SQL hint equivalent — `/*+ BROADCAST(country) */`
# MAGIC The DataFrame `broadcast(df)` and the SQL `BROADCAST(t)` hint do the same thing. The
# MAGIC hint goes immediately after `SELECT`. `BROADCAST` has the highest join-hint priority
# MAGIC (over `MERGE`, `SHUFFLE_HASH`, `SHUFFLE_REPLICATE_NL`).

# COMMAND ----------

# Register temp views so we can express the same join in pure SQL.
orders.createOrReplaceTempView("orders_v")
country.createOrReplaceTempView("country_v")

sql_bhj = spark.sql("""
    SELECT /*+ BROADCAST(country_v) */ o.order_id, o.amount, c.country_name
    FROM   orders_v o
    JOIN   country_v c ON o.country_code = c.country_code
""")

# Same evidence as the DataFrame path: BroadcastHashJoin + BroadcastExchange, no orders Exchange.
print("=== SQL hint: /*+ BROADCAST(country_v) */ — expect BroadcastHashJoin ===")
sql_bhj.explain(mode="formatted")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. In-memory partition counts
# MAGIC `getNumPartitions()` reports the in-memory Spark partitions of each input. The shuffle
# MAGIC join repartitions both sides to `spark.sql.shuffle.partitions` (OSS default **200**;
# MAGIC on Databricks can be `auto`); the broadcast join leaves the big `orders` side's
# MAGIC partitioning untouched (no big-side Exchange).

# COMMAND ----------

print("orders  partitions:", orders.rdd.getNumPartitions())
print("country partitions:", country.rdd.getNumPartitions())
print("spark.sql.shuffle.partitions:", spark.conf.get("spark.sql.shuffle.partitions"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Uses, edge cases & limitations
# MAGIC **Uses**
# MAGIC - Big fact ⋈ small dimension → **broadcast** the small side (highest-leverage join tuning).
# MAGIC - Big ⋈ big on a shared key → **SortMergeJoin** (the default; consider bucketing later).
# MAGIC - Let **AQE** flip sort-merge → broadcast at runtime when a side only becomes small
# MAGIC   *after* a filter — instead of hand-forcing.
# MAGIC
# MAGIC **Edge cases**
# MAGIC - `broadcast()` is a *hint*: broadcasting a too-large table builds the whole side on the
# MAGIC   **driver** first → driver OOM / `maxResultSize` exceeded. Only hint the genuinely small side.
# MAGIC - The auto-broadcast decision uses an **estimate**; the result of a wide transformation
# MAGIC   may be estimated as huge even when it's tiny — check `.explain()`, don't assume.
# MAGIC - Outer joins restrict which side may be the broadcast/build side.
# MAGIC
# MAGIC **Limitations**
# MAGIC - BHJ needs the small side to fit in **driver and executor** memory — no "streaming" a
# MAGIC   huge broadcast. Above that budget, SortMergeJoin (or bucketing) is the safe choice.
# MAGIC - `autoBroadcastJoinThreshold` is **10 MB in OSS Spark**; Databricks AQE also uses a
# MAGIC   higher *runtime* switch — state which you mean when quoting a number.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Cleanup — reset conf and drop demo state (rerunnable, leaves nothing behind)

# COMMAND ----------

# Drop the temp views created for the SQL hint demo.
spark.catalog.dropTempView("orders_v")
spark.catalog.dropTempView("country_v")

# Reset the conf we toggled for demonstration so the session is back to its default.
spark.conf.unset("spark.sql.autoBroadcastJoinThreshold")

# Drop the demo schema (and any tables under it). CASCADE in case anything was created.
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")

print("Cleanup complete: temp views dropped, autoBroadcastJoinThreshold reset, demo schema dropped.")
