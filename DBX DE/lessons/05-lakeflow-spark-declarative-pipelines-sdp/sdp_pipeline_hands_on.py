# Databricks notebook source
# MAGIC %md
# MAGIC # Lakeflow Spark Declarative Pipelines — Hands-On (Topic 5)
# MAGIC **This notebook is the SOURCE for a Lakeflow Declarative Pipeline (SDP).**
# MAGIC It is **not** run cell-by-cell on an all-purpose cluster — attach it to a
# MAGIC **Lakeflow Declarative Pipeline** (Jobs & Pipelines ▸ Pipelines) and click **Start**.
# MAGIC
# MAGIC Covers Topic 5 hands-on subtopics, with the **current `pyspark.pipelines` API**:
# MAGIC - **5.1/5.2** Streaming tables, materialized views, append flows
# MAGIC - **5.3** Data-quality Expectations (warn / drop / fail; dict form)
# MAGIC - **5.4** AUTO CDC → SCD Type 2 dimension (Python + SQL)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - A Lakeflow Declarative Pipeline configured with this notebook as source
# MAGIC - DBR via the pipeline (SDP-managed); Unity Catalog target catalog/schema set on the pipeline
# MAGIC - Pipeline configuration: set `source_path` to a UC Volume holding sample JSON
# MAGIC
# MAGIC **Scope:** SDP pipeline features only — no Apache Spark core programming.
# MAGIC > No cleanup cell: SDP owns/manages its tables (delete via the pipeline if needed).
# MAGIC > UC three-level namespacing is set by the pipeline's target catalog/schema.

# COMMAND ----------

# MAGIC %md ## 5.1 / 5.2 — Streaming table (bronze ingest) + materialized view

# COMMAND ----------

from pyspark import pipelines as dp        # current API (replaces `import dlt`)
from pyspark.sql.functions import col, current_timestamp, expr

# Streaming table: incremental bronze ingest via Auto Loader.
# source_path is read from PIPELINE CONFIGURATION (parametrized pipeline).
@dp.table(comment="Raw orders ingested incrementally")
def bronze_orders():
    return (spark.readStream.format("cloudFiles")
            .option("cloudFiles.format", "json")
            .load(spark.conf.get("source_path")))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Append flow — fan two regions into one streaming table
# MAGIC Create the target ST explicitly, then attach independent append flows.

# COMMAND ----------

dp.create_streaming_table("all_orders")

@dp.append_flow(target="all_orders")
def us_orders():
    return spark.readStream.table("bronze_orders").where("region = 'US'")

@dp.append_flow(target="all_orders")
def eu_orders():
    return spark.readStream.table("bronze_orders").where("region = 'EU'")

# COMMAND ----------

# MAGIC %md ## 5.3 — Expectations on the silver streaming table (warn / drop / fail)

# COMMAND ----------

@dp.table(comment="Cleaned orders")
@dp.expect("amount_non_negative", "amount >= 0")              # warn (kept + metric)
@dp.expect_or_drop("valid_id", "order_id IS NOT NULL")        # drop bad rows
@dp.expect_or_fail("has_customer", "customer_id IS NOT NULL") # fail update if violated
def silver_orders():
    return spark.readStream.table("bronze_orders").withColumn("ingested_at", current_timestamp())

# COMMAND ----------

# MAGIC %md
# MAGIC ### Quarantine pattern — keep bad rows instead of dropping
# MAGIC Split the stream on the rule expression: clean → silver, bad → quarantine.

# COMMAND ----------

RULES = {"valid_id": "order_id IS NOT NULL", "amount_non_negative": "amount >= 0"}
valid_expr = " AND ".join(RULES.values())

@dp.table(comment="Rows failing any rule, kept for review")
def quarantined_orders():
    return spark.readStream.table("bronze_orders").filter(expr(f"NOT({valid_expr})"))

# COMMAND ----------

# MAGIC %md ## 5.2 — Materialized view (gold aggregate, always correct)

# COMMAND ----------

@dp.materialized_view(comment="Daily revenue by region (always reflects current data)")
def gold_daily_revenue():
    return (spark.read.table("silver_orders")
            .groupBy("region", "order_date").sum("amount"))

# COMMAND ----------

# MAGIC %md ## 5.4 — AUTO CDC → SCD Type 2 dimension (Python)
# MAGIC `create_auto_cdc_flow` (replaces legacy `dlt.apply_changes`) applies a change
# MAGIC feed by KEYS + SEQUENCE BY; SCD Type 2 keeps `__START_AT`/`__END_AT` history.

# COMMAND ----------

# the change feed (e.g. from a Lakeflow Connect CDC connector or read_files)
@dp.table(comment="Raw customer change feed")
def customers_changes():
    return (spark.readStream.format("cloudFiles")
            .option("cloudFiles.format", "json")
            .load(spark.conf.get("cdc_path")))

dp.create_streaming_table("dim_customers")

dp.create_auto_cdc_flow(
    target="dim_customers",
    source="customers_changes",
    keys=["customer_id"],
    sequence_by=col("change_ts"),         # highest sequence wins (handles late data)
    stored_as_scd_type=2,                 # full history; use 1 for current-only
    apply_as_deletes=expr("operation = 'DELETE'"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### 5.4 — Same AUTO CDC in SQL (alternative source)
# MAGIC Add a SQL source to the same pipeline instead of the Python flow above:
# MAGIC ```sql
# MAGIC CREATE OR REFRESH STREAMING TABLE dim_customers;
# MAGIC
# MAGIC CREATE FLOW cdc_flow AS AUTO CDC INTO dim_customers
# MAGIC FROM stream(customers_changes)
# MAGIC KEYS (customer_id)
# MAGIC APPLY AS DELETE WHEN operation = 'DELETE'
# MAGIC SEQUENCE BY change_ts
# MAGIC STORED AS SCD TYPE 2
# MAGIC TRACK HISTORY ON * EXCEPT (last_seen_ts);
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run & monitor
# MAGIC - Start the pipeline in **triggered** mode (batch) or **continuous** (streaming).
# MAGIC - Set pipeline configuration: `source_path`, `cdc_path` (parametrized pipeline).
# MAGIC - Watch the **DAG**, per-dataset row counts, **expectation metrics**, and the
# MAGIC   **event log** in the pipeline UI.
