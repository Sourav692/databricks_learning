# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 12 — Spark UI and query plan debugging
# MAGIC
# MAGIC **Goal:** create three common Spark performance fingerprints, then read them in
# MAGIC `df.explain(mode="formatted")` and the Spark UI.
# MAGIC
# MAGIC ## What you will practice
# MAGIC - Finding `Exchange`, join, aggregate, cache, and AQE nodes in the plan.
# MAGIC - Using job groups to find the matching action in the Spark UI.
# MAGIC - Reading Stages tab signals: task count, task-time spread, shuffle, spill, and GC.
# MAGIC - Avoiding `collect()` when benchmarking a full distributed plan.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Setup

# COMMAND ----------

from pyspark.sql.functions import col, expr, rand, when
from pyspark.storagelevel import StorageLevel

LESSON_ID = "Lesson 12 - Spark UI debugging"

def mark_action(label: str) -> None:
    """Label the next Spark action so it is easy to find in the Spark UI."""
    spark.sparkContext.setJobGroup(f"{LESSON_ID}: {label}", f"{LESSON_ID}: {label}", True)
    print(f"\nACTION -> {label}")

def run_noop(df, label: str) -> None:
    """Run the full distributed plan without collecting rows to the driver."""
    mark_action(label)
    df.write.format("noop").mode("overwrite").save()

spark.conf.set("spark.sql.adaptive.enabled", "true")
spark.conf.set("spark.sql.shuffle.partitions", 200)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · A normal shuffle: find `Exchange`

# COMMAND ----------

events = (spark.range(0, 5_000_000)
    .withColumn("customer_id", (col("id") % 500_000).cast("long"))
    .withColumn("amount", (rand() * 100).cast("double"))
    .select("customer_id", "amount"))

by_customer = events.groupBy("customer_id").sum("amount")

by_customer.explain(mode="formatted")
# Look for:
# - HashAggregate partial/final
# - Exchange hashpartitioning(customer_id, 200)
# - AdaptiveSparkPlan / AQEShuffleRead after the action

run_noop(by_customer, "01 aggregate with shuffle")
# Spark UI:
# - SQL tab: Exchange before the final aggregate
# - Stages tab: shuffle read/write metrics for the shuffle stages

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · A skew fingerprint: one task much larger than the rest

# COMMAND ----------

skewed = (spark.range(0, 5_000_000)
    .withColumn(
        "merchant_id",
        when(col("id") < 3_500_000, expr("'HOT'")).otherwise((col("id") % 100_000).cast("string")),
    )
    .withColumn("amount", (rand() * 50).cast("double"))
    .select("merchant_id", "amount"))

skewed_agg = skewed.groupBy("merchant_id").sum("amount")
skewed_agg.explain(mode="formatted")

run_noop(skewed_agg, "02 skewed aggregate")
# Spark UI:
# - Stages tab: compare Max task time vs Median.
# - If one partition owns the HOT key, Max task time and/or shuffle read size will dominate.
# - Lesson 08 shows salting if AQE does not resolve the skew.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Cache validation: look for `InMemoryTableScan`

# COMMAND ----------

filtered = events.where(col("amount") > 10).persist(StorageLevel.MEMORY_AND_DISK)

mark_action("03 materialize cache")
filtered.count()

reused = filtered.groupBy("customer_id").count()
reused.explain(mode="formatted")
# Look for InMemoryTableScan. If it is absent, the cached DataFrame is not being reused.

run_noop(reused, "04 reuse cached DataFrame")
# Spark UI:
# - Storage tab: cached partitions and memory/disk footprint.
# - SQL tab: InMemoryTableScan in the plan.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Environment tab: verify configs

# COMMAND ----------

print("AQE:", spark.conf.get("spark.sql.adaptive.enabled"))
print("shuffle partitions:", spark.conf.get("spark.sql.shuffle.partitions"))
print("broadcast threshold:", spark.conf.get("spark.sql.autoBroadcastJoinThreshold"))

# Spark UI:
# - Environment tab -> Spark Properties.
# - Confirm these are the values you think the job used.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Cleanup

# COMMAND ----------

filtered.unpersist()
spark.sparkContext.clearJobGroup()
