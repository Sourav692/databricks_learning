# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 14 — Reading Spark query plans
# MAGIC
# MAGIC **Goal:** understand the exact order Spark follows when it turns SQL/DataFrame code into
# MAGIC work on executors.
# MAGIC
# MAGIC The mental picture is the same as the query-plan diagram:
# MAGIC
# MAGIC `SQL AST / DataFrame`
# MAGIC → `Unresolved logical plan`
# MAGIC → `Analysis` using the catalog/schema
# MAGIC → `Logical plan`
# MAGIC → `Logical optimization`
# MAGIC → `Optimized logical plan`
# MAGIC → `Physical planning`
# MAGIC → `Candidate physical plans`
# MAGIC → `Cost model`
# MAGIC → `Selected physical plan`
# MAGIC → `Code generation`
# MAGIC → `RDD tasks`.
# MAGIC
# MAGIC **Most important habit:** read `explain(True)` from top to bottom, but remember that the
# MAGIC **Physical Plan at the bottom is what Spark will execute**.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Setup

# COMMAND ----------

import pyspark.sql.functions as F

LESSON_ID = "Lesson 14 - Reading query plans"

def mark_action(label: str) -> None:
    """Label Spark actions so they are easy to find in the Spark UI."""
    spark.sparkContext.setJobGroup(f"{LESSON_ID}: {label}", f"{LESSON_ID}: {label}", True)
    print(f"\nACTION -> {label}")

spark.conf.set("spark.sql.adaptive.enabled", "true")

base_path = "dbfs:/tmp/pyspark_perf_lesson14_query_plans"
customers_path = f"{base_path}/customers"
transactions_path = f"{base_path}/transactions"

raw_customers = spark.createDataFrame(
    [
        ("C001", "Aaron Abbott", "34", "Female", "boston"),
        ("C002", "Bianca Lee", "51", "Female", "chicago"),
        ("C003", "Carlos Diaz", "44", "Male", "boston"),
        ("C004", "Deepa Rao", "57", "Female", "new_york"),
        ("C005", "Evan Smith", "29", "Male", "boston"),
    ],
    "cust_id string, name string, age string, gender string, city string",
)

raw_transactions = spark.createDataFrame(
    [
        ("C001", "T001", "Groceries", 120.0, "boston"),
        ("C001", "T002", "Travel", 50.0, "boston"),
        ("C002", "T003", "Dining", 90.0, "chicago"),
        ("C003", "T004", "Groceries", 20.0, "boston"),
        ("C003", "T005", "Travel", 200.0, "boston"),
        ("C004", "T006", "Dining", 80.0, "new_york"),
        ("C005", "T007", "Groceries", 40.0, "boston"),
    ],
    "cust_id string, txn_id string, expense_type string, amt double, city string",
)

# Write small Parquet files and read them back so the physical plan shows FileScan,
# ReadSchema, PushedFilters, and other scan details like the reference notebook.
raw_customers.write.mode("overwrite").parquet(customers_path)
raw_transactions.write.mode("overwrite").parquet(transactions_path)

customers = spark.read.parquet(customers_path)
transactions = spark.read.parquet(transactions_path)

customers.createOrReplaceTempView("lesson14_customers")
transactions.createOrReplaceTempView("lesson14_transactions")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · The full query-plan pipeline
# MAGIC
# MAGIC Read this as a left-to-right assembly line:
# MAGIC
# MAGIC | Order | Stage | Plain English |
# MAGIC | --- | --- | --- |
# MAGIC | 1 | SQL/DataFrame | Your code: `filter`, `select`, `join`, `groupBy`, SQL text |
# MAGIC | 2 | Unresolved logical plan | Spark has a syntax tree, but names like `city` may not be fully resolved |
# MAGIC | 3 | Analysis | Spark checks the catalog/schema and resolves tables, columns, functions, and types |
# MAGIC | 4 | Logical plan | A valid logical description of what result you want |
# MAGIC | 5 | Logical optimization | Catalyst simplifies the plan: prune columns, push filters, combine projections |
# MAGIC | 6 | Optimized logical plan | Same result, less unnecessary work |
# MAGIC | 7 | Physical planning | Spark creates executable plan options: scan, filter, join, aggregate, exchange |
# MAGIC | 8 | Cost model | Spark chooses between candidate physical plans using stats and rules |
# MAGIC | 9 | Selected physical plan | The winner: this is what Spark will run |
# MAGIC | 10 | Code generation / RDD tasks | Spark emits efficient JVM code where possible and runs tasks on executors |
# MAGIC
# MAGIC In `df.explain(True)`, Spark shows the most useful checkpoints:
# MAGIC `Parsed`, `Analyzed`, `Optimized`, and `Physical`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Narrow transformations: filter/select/withColumn
# MAGIC
# MAGIC Narrow transformations usually stay in one stage. No row has to move to a different
# MAGIC executor just to apply a filter or add a column.
# MAGIC
# MAGIC **Read the plan like this:**
# MAGIC - `Parsed Logical Plan`: Spark records the operations in the order you wrote them.
# MAGIC - `Analyzed Logical Plan`: column names/types are resolved.
# MAGIC - `Optimized Logical Plan`: Catalyst collapses repeated projections and pushes filters down.
# MAGIC - `Physical Plan`: Spark chooses actual operators like `FileScan`, `Filter`, and `Project`.

# COMMAND ----------

narrow_plan = (
    customers
    .filter(F.col("city") == "boston")
    .withColumn("first_name", F.split("name", " ").getItem(0))
    .withColumn("last_name", F.split("name", " ").getItem(1))
    .withColumn("age_plus_5", F.col("age").cast("int") + F.lit(5))
    .select("cust_id", "first_name", "last_name", "age_plus_5", "gender")
)

narrow_plan.show(truncate=False)
narrow_plan.explain(True)
# Notice:
# - Optimized plan keeps only needed columns.
# - Physical plan has Filter/Project, but no Exchange.
# - No Exchange means no shuffle.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Wide transformation: `repartition()`
# MAGIC
# MAGIC `repartition(n)` means: "make a new set of partitions and spread rows across them."
# MAGIC That requires a shuffle, so the physical plan shows an `Exchange`.

# COMMAND ----------

transactions.repartition(8).explain(True)
# Notice:
# - Logical plan says Repartition 8, true.
# - Physical plan says Exchange RoundRobinPartitioning(8).
# - Exchange = shuffle/data movement.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Narrow-ish partition change: `coalesce()`
# MAGIC
# MAGIC `coalesce(n)` reduces partitions by merging existing ones. It usually avoids a shuffle.
# MAGIC That is why the physical plan shows `Coalesce`, not `Exchange`.

# COMMAND ----------

transactions.coalesce(2).explain(True)
# Notice:
# - Logical plan says Repartition 2, false.
# - Physical plan says Coalesce 2.
# - No Exchange because Spark is not redistributing all rows.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Join: physical planning chooses a strategy
# MAGIC
# MAGIC A join is where the image's **Physical Planning → Cost Model → Selected Physical Plan**
# MAGIC part matters.
# MAGIC
# MAGIC Spark may consider strategies like broadcast hash join and sort-merge join. The selected
# MAGIC physical plan tells you what it chose. Here we disable broadcast so the plan clearly shows
# MAGIC a shuffle sort-merge join.

# COMMAND ----------

old_broadcast_threshold = spark.conf.get("spark.sql.autoBroadcastJoinThreshold")
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", -1)

joined = transactions.join(customers, on="cust_id", how="inner")
joined.explain(True)
# Notice:
# - Optimized plan adds isnotnull(cust_id) filters on both sides.
# - Physical plan chooses SortMergeJoin.
# - Each side has Exchange hashpartitioning(cust_id, 200) + Sort.
# - That means both sides shuffle by join key before the join.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · GroupBy: partial aggregate, shuffle, final aggregate
# MAGIC
# MAGIC A `groupBy` on a key usually needs all rows for the same key together. Spark does this in
# MAGIC two phases:
# MAGIC
# MAGIC 1. **Partial aggregate** near the data.
# MAGIC 2. **Exchange** to move same-key rows together.
# MAGIC 3. **Final aggregate** after the shuffle.

# COMMAND ----------

city_counts = transactions.groupBy("city").count()
city_counts.explain(True)
# Notice:
# - Optimized plan reads only the city column.
# - Physical plan has HashAggregate -> Exchange -> HashAggregate.
# - The lower aggregate is partial; the upper aggregate is final.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Count distinct: more expensive physical plan
# MAGIC
# MAGIC `countDistinct` often needs more plan steps than a simple count because Spark must first
# MAGIC deduplicate `(group_key, distinct_column)` pairs and then count them.

# COMMAND ----------

distinct_cities = transactions.groupBy("cust_id").agg(F.countDistinct("city").alias("city_count"))
distinct_cities.explain(True)
# Notice:
# - More HashAggregate and Exchange nodes than simple count.
# - More Exchanges usually means more network and stage boundaries.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Predicate pushdown: why Filter can appear twice
# MAGIC
# MAGIC In the optimized/physical plan, Spark may push a filter into the file scan as
# MAGIC `PushedFilters`. You can still see a `Filter` operator above the scan.
# MAGIC
# MAGIC That is normal. Spark keeps the filter as a correctness check because not every data
# MAGIC source can fully apply every pushed predicate.

# COMMAND ----------

pushdown_candidate = customers.filter(F.col("city") == "boston")
pushdown_candidate.explain(True)
# Look for:
# - PushedFilters in a file-backed plan (when reading Parquet/Delta).
# - A Filter operator may still remain above the scan for correctness.

cast_filter = customers.filter(F.col("age").cast("int") > 50)
cast_filter.explain(True)
# Notice:
# - Cast expressions are harder to push down fully.
# - The physical Filter remains important.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · Code generation: last step before execution
# MAGIC
# MAGIC After Spark has selected a physical plan, compatible operators can be fused into generated
# MAGIC JVM code. This is the right side of the image: **Selected Physical Plan → Code Generation**.

# COMMAND ----------

spark.sql("""
EXPLAIN CODEGEN
SELECT city, count(*) AS n
FROM lesson14_transactions
GROUP BY city
""").show(truncate=False)
# Notice:
# - EXPLAIN CODEGEN shows generated-code sections for codegen-compatible parts.
# - Not every operator participates in whole-stage codegen.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10 · Final checklist for reading any query plan
# MAGIC
# MAGIC Read in this order:
# MAGIC
# MAGIC 1. **Parsed**: what did Spark think I wrote?
# MAGIC 2. **Analyzed**: did Spark resolve columns/tables/types correctly?
# MAGIC 3. **Optimized**: did Catalyst prune columns, push filters, and simplify projections?
# MAGIC 4. **Physical**: what will Spark actually run?
# MAGIC 5. **In the physical plan, hunt for expensive nodes**:
# MAGIC    - `Exchange` = shuffle / stage boundary.
# MAGIC    - `SortMergeJoin` = big join with shuffle and sort.
# MAGIC    - `BroadcastHashJoin` = small side broadcast, usually no big-side shuffle.
# MAGIC    - `HashAggregate` around `Exchange` = partial/final aggregation.
# MAGIC    - `Coalesce` = fewer partitions without full shuffle.
# MAGIC    - `AdaptiveSparkPlan` / `AQEShuffleRead` = AQE can change the final runtime plan.

# COMMAND ----------

spark.conf.set("spark.sql.autoBroadcastJoinThreshold", old_broadcast_threshold)
spark.sparkContext.clearJobGroup()

if "dbutils" in globals():
    dbutils.fs.rm(base_path, recurse=True)
