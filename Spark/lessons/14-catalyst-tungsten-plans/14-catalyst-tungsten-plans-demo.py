# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 14 — Catalyst, Tungsten, and physical plan nodes
# MAGIC
# MAGIC **Goal:** practice reading Spark plans at multiple levels and connect common physical
# MAGIC plan nodes to the tuning lessons.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Setup

# COMMAND ----------

from pyspark.sql.functions import col, expr, rand, udf
from pyspark.sql.types import BooleanType

LESSON_ID = "Lesson 14 - Catalyst and plans"

def mark_action(label: str) -> None:
    spark.sparkContext.setJobGroup(f"{LESSON_ID}: {label}", f"{LESSON_ID}: {label}", True)
    print(f"\nACTION -> {label}")

def run_noop(df, label: str) -> None:
    mark_action(label)
    df.write.format("noop").mode("overwrite").save()

spark.conf.set("spark.sql.adaptive.enabled", "true")

events = (spark.range(0, 2_000_000)
    .withColumn("customer_id", (col("id") % 250_000).cast("long"))
    .withColumn("amount", (rand() * 100).cast("double"))
    .withColumn("status", expr("CASE WHEN id % 5 = 0 THEN 'ACTIVE' ELSE 'INACTIVE' END"))
    .select("customer_id", "amount", "status"))

customers = (spark.range(0, 250_000)
    .withColumnRenamed("id", "customer_id")
    .withColumn("region", expr("concat('r', cast(customer_id % 12 as string))")))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Extended plan: parsed -> analyzed -> optimized -> physical

# COMMAND ----------

q = (events
    .where(col("status") == "ACTIVE")
    .select("customer_id", "amount")
    .join(customers.select("customer_id", "region"), "customer_id")
    .groupBy("region")
    .sum("amount"))

q.explain(mode="extended")
# Read:
# - Parsed / Analyzed / Optimized logical plans
# - Physical plan with joins, aggregates, and exchanges

run_noop(q, "01 optimized DataFrame plan")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Formatted plan: focus on physical nodes

# COMMAND ----------

q.explain(mode="formatted")
# Look for:
# - Exchange: shuffle boundary
# - BroadcastHashJoin or SortMergeJoin: join strategy
# - HashAggregate: partial/final aggregation
# - AdaptiveSparkPlan and AQEShuffleRead after action

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Native expression vs UDF visibility

# COMMAND ----------

native_filter = events.where(col("status") == "ACTIVE")

@udf(BooleanType())
def is_active(status: str) -> bool:
    return status == "ACTIVE"

udf_filter = events.where(is_active(col("status")))

print("Native filter plan:")
native_filter.explain(mode="formatted")

print("UDF filter plan:")
udf_filter.explain(mode="formatted")

# The native filter is optimizer-visible. The UDF appears as a Python evaluation step,
# so Catalyst cannot inspect the function body for pushdown/pruning opportunities.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Codegen: where Spark emits JVM code

# COMMAND ----------

spark.sql("CREATE OR REPLACE TEMP VIEW lesson14_events AS SELECT * FROM range(1000000)")

spark.sql("""
EXPLAIN CODEGEN
SELECT id % 1000 AS k, count(*) AS n
FROM lesson14_events
GROUP BY id % 1000
""").show(truncate=False)
# Look for WholeStageCodegen sections. Not every operator is codegen-compatible.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Node checklist
# MAGIC
# MAGIC Use this checklist while reading any plan:
# MAGIC - `Exchange` -> shuffle; inspect stages and shuffle metrics.
# MAGIC - `BroadcastExchange` -> driver builds small side; watch driver and executor memory.
# MAGIC - `BroadcastHashJoin` -> no big-side shuffle.
# MAGIC - `SortMergeJoin` -> likely big-vs-big shuffle join.
# MAGIC - `InMemoryTableScan` -> cache reuse.
# MAGIC - `AdaptiveSparkPlan` / `AQEShuffleRead` -> runtime rewrite.

# COMMAND ----------

spark.sparkContext.clearJobGroup()
