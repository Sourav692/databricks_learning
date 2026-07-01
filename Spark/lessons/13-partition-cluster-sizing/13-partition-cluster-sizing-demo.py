# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 13 — Partition, shuffle, and cluster sizing
# MAGIC
# MAGIC **Goal:** connect partition count to task count, task waves, shuffle size, spill risk,
# MAGIC and cluster utilization.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Setup

# COMMAND ----------

from math import ceil
from pyspark.sql.functions import col, rand

LESSON_ID = "Lesson 13 - Partition and cluster sizing"

def mark_action(label: str) -> None:
    spark.sparkContext.setJobGroup(f"{LESSON_ID}: {label}", f"{LESSON_ID}: {label}", True)
    print(f"\nACTION -> {label}")

def run_noop(df, label: str) -> None:
    mark_action(label)
    df.write.format("noop").mode("overwrite").save()

def show_stage_shape(df, total_executor_cores: int = 64) -> None:
    partitions = df.rdd.getNumPartitions()
    print(f"partitions = {partitions}")
    print(f"approx task waves on {total_executor_cores} cores = {ceil(partitions / total_executor_cores)}")

spark.conf.set("spark.sql.adaptive.enabled", "true")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Partition count becomes task count

# COMMAND ----------

base = (spark.range(0, 10_000_000)
    .withColumn("customer_id", (col("id") % 1_000_000).cast("long"))
    .withColumn("amount", (rand() * 100).cast("double"))
    .select("customer_id", "amount"))

print("Input partitions:")
show_stage_shape(base)

run_noop(base, "01 scan baseline")
# Spark UI -> Stages tab:
# - task count equals the input partition count for this narrow scan stage.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Repartition vs coalesce

# COMMAND ----------

coalesced = base.coalesce(8)
repartitioned = base.repartition(400, "customer_id")

print("coalesced:")
show_stage_shape(coalesced)
coalesced.explain(mode="formatted")
# Usually no Exchange when reducing partitions.

print("repartitioned:")
show_stage_shape(repartitioned)
repartitioned.explain(mode="formatted")
# Look for Exchange hashpartitioning(customer_id, 400).

run_noop(coalesced, "02 coalesce to 8")
run_noop(repartitioned, "03 repartition by key to 400")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Shuffle partitions as a pre-AQE upper bound

# COMMAND ----------

spark.conf.set("spark.sql.shuffle.partitions", 400)
spark.conf.set("spark.sql.adaptive.coalescePartitions.enabled", "true")

agg = base.groupBy("customer_id").sum("amount")
agg.explain(mode="formatted")

run_noop(agg, "04 shuffle aggregate with AQE")
# After action:
# - explain again and look for AdaptiveSparkPlan isFinalPlan=true.
# - Spark UI SQL tab: AQEShuffleRead coalesced if output partitions were tiny.

agg.explain(mode="formatted")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Too few partitions under-use cores

# COMMAND ----------

too_few = base.coalesce(4).groupBy("customer_id").sum("amount")
too_few.explain(mode="formatted")
run_noop(too_few, "05 too few input partitions")
# Spark UI:
# - early stage has only a few tasks, so a large cluster cannot use all cores.
# - later shuffle may still create more tasks, depending on shuffle.partitions/AQE.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Cleanup

# COMMAND ----------

spark.sparkContext.clearJobGroup()
