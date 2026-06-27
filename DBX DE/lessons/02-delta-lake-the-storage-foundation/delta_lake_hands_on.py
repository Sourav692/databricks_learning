# Databricks notebook source
# MAGIC %md
# MAGIC # Delta Lake — Hands-On (Topic 2)
# MAGIC One runnable notebook covering the hands-on subtopics of Topic 2, at the
# MAGIC enterprise depth of the lessons:
# MAGIC - **2.1** Delta tables & the transaction log (commits, stats, snapshots)
# MAGIC - **2.2** MERGE (conditional, dedup, NOT MATCHED BY SOURCE) / INSERT OVERWRITE / CREATE OR REPLACE / CTAS
# MAGIC - **2.3** Managed vs external tables; properties, constraints, generated/identity columns
# MAGIC - **2.4** Time travel (VERSION/TIMESTAMP AS OF, DESCRIBE HISTORY, RESTORE, retention, CDF)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - DBR 14.3 LTS+ or Serverless, Unity Catalog enabled
# MAGIC - `USE CATALOG` + `CREATE SCHEMA`/`CREATE TABLE` grants on the target catalog
# MAGIC - Edit the `catalog`/`schema` widgets below to a sandbox you can write to
# MAGIC
# MAGIC **Scope:** Delta/SQL features only — no Apache Spark core programming.
# MAGIC Run top to bottom; the last cell cleans everything up so it's rerunnable.

# COMMAND ----------

# MAGIC %md ## Setup — parameterize catalog & schema (UC three-level namespace)

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "de_demo_delta", "Schema")
catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")

# Parameterized so the notebook is portable across workspaces/sandboxes.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Using {catalog}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2.1 — Delta tables & the transaction log
# MAGIC Every table on Databricks is Delta by default. Each write appends a commit to
# MAGIC the `_delta_log`, giving ACID + version history. We also enable deletion
# MAGIC vectors (merge-on-read) so later UPDATE/DELETE/MERGE rewrite less data.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- v0: create (Delta by default — no USING needed). TBLPROPERTIES tune behavior.
# MAGIC CREATE OR REPLACE TABLE customers (
# MAGIC   id     INT,
# MAGIC   name   STRING,
# MAGIC   tier   STRING,
# MAGIC   amount DECIMAL(10,2)
# MAGIC ) TBLPROPERTIES (delta.enableDeletionVectors = true);
# MAGIC -- v1: insert
# MAGIC INSERT INTO customers VALUES (1,'Ada','silver',100), (2,'Lin','gold',250);
# MAGIC SELECT * FROM customers ORDER BY id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The log records every version; DESCRIBE DETAIL shows file/stat metadata
# MAGIC -- that powers data skipping (Topic 2.1).
# MAGIC DESCRIBE HISTORY customers;

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE DETAIL customers;   -- numFiles, sizeInBytes, location, format

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2.2 — MERGE / INSERT OVERWRITE / CREATE OR REPLACE / CTAS
# MAGIC **Uses:** MERGE → CDC/upserts/SCD/dedup. **Edge case:** if two source rows
# MAGIC match one target row, MERGE fails — dedup to one row per key first.

# COMMAND ----------

# Build a realistic change feed WITH a duplicate key, then dedup to latest by ts.
from pyspark.sql import functions as F, Window

raw = spark.createDataFrame(
    [(1, "Ada",  "gold", 180.0, "2024-01-02"),   # update Ada
     (1, "Ada",  "gold", 175.0, "2024-01-01"),   # older dup for key 1 -> must drop
     (3, "Sam",  "silver", 90.0, "2024-01-02"),  # new customer
     (2, "Lin",  "gold", 250.0, "2024-01-02")],  # unchanged
    ["id", "name", "tier", "amount", "event_ts"])

w = Window.partitionBy("id").orderBy(F.col("event_ts").desc())
deduped = raw.withColumn("rn", F.row_number().over(w)).filter("rn = 1").drop("rn", "event_ts")
deduped.createOrReplaceTempView("updates")   # exactly one row per key
display(spark.table("updates").orderBy("id"))

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Conditional MERGE: update existing, insert new (one atomic commit).
# MAGIC MERGE INTO customers t
# MAGIC USING updates s
# MAGIC ON t.id = s.id
# MAGIC WHEN MATCHED AND t.amount <> s.amount THEN UPDATE SET *
# MAGIC WHEN NOT MATCHED THEN INSERT *;
# MAGIC SELECT * FROM customers ORDER BY id;

# COMMAND ----------

# Equivalent via the DeltaTable Python API (whenMatched/ whenNotMatched builders)
from delta.tables import DeltaTable
(DeltaTable.forName(spark, f"{catalog}.{schema}.customers").alias("t")
   .merge(spark.table("updates").alias("s"), "t.id = s.id")
   .whenMatchedUpdateAll()
   .whenNotMatchedInsertAll()
   .execute())
display(spark.table("customers").orderBy("id"))

# COMMAND ----------

# MAGIC %sql
# MAGIC -- INSERT OVERWRITE: atomically replace ALL rows (full reload).
# MAGIC INSERT OVERWRITE customers
# MAGIC   SELECT * FROM VALUES (1,'Ada','gold',180.0),(2,'Lin','gold',250.0),(3,'Sam','silver',90.0)
# MAGIC   AS t(id,name,tier,amount);
# MAGIC SELECT * FROM customers ORDER BY id;

# COMMAND ----------

# Targeted overwrite with replaceWhere — replace only the 'silver' slice.
# The written data MUST satisfy the predicate or the write fails (safety guard).
silver_fix = spark.createDataFrame([(3, "Sam", "silver", 95.0)], ["id","name","tier","amount"])
(silver_fix.write.format("delta").mode("overwrite")
   .option("replaceWhere", "tier = 'silver'")
   .saveAsTable(f"{catalog}.{schema}.customers"))
display(spark.table("customers").orderBy("id"))

# COMMAND ----------

# MAGIC %sql
# MAGIC -- CTAS: build a derived (gold-style) aggregate table from a query.
# MAGIC CREATE OR REPLACE TABLE revenue_by_tier AS
# MAGIC   SELECT tier, sum(amount) AS revenue FROM customers GROUP BY tier;
# MAGIC SELECT * FROM revenue_by_tier ORDER BY tier;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2.3 — Managed vs external + properties & constraints
# MAGIC `customers` is **managed** (UC owns the files; DROP deletes them; gets
# MAGIC Predictive Optimization). External tables add `LOCATION '…'` and keep their
# MAGIC files on DROP. Below: constraints + generated/identity columns enforce and
# MAGIC document quality at the table level.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- A quality-enforced dimension: identity key, generated column, CHECK constraint.
# MAGIC CREATE OR REPLACE TABLE orders (
# MAGIC   order_id   BIGINT GENERATED ALWAYS AS IDENTITY,        -- auto surrogate key
# MAGIC   customer   STRING COMMENT 'FK to customers.id',
# MAGIC   amount     DECIMAL(10,2) NOT NULL,                    -- reject NULLs on write
# MAGIC   order_ts   TIMESTAMP,
# MAGIC   order_date DATE GENERATED ALWAYS AS (CAST(order_ts AS DATE))  -- derived
# MAGIC ) CLUSTER BY (order_date);                               -- liquid clustering
# MAGIC ALTER TABLE orders ADD CONSTRAINT amount_pos CHECK (amount >= 0);
# MAGIC INSERT INTO orders (customer, amount, order_ts)
# MAGIC   VALUES ('1', 120.00, TIMESTAMP'2024-01-03T10:00:00'),
# MAGIC          ('3',  60.00, TIMESTAMP'2024-01-04T11:30:00');
# MAGIC SELECT * FROM orders ORDER BY order_id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Properties + tags for discovery/governance (masking/row filters -> Topic 8).
# MAGIC ALTER TABLE orders SET TBLPROPERTIES ('quality' = 'gold', 'domain' = 'sales');
# MAGIC COMMENT ON TABLE orders IS 'Curated order facts (gold).';
# MAGIC DESCRIBE EXTENDED orders;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2.4 — Time travel
# MAGIC Query old versions by number/timestamp; roll back with `RESTORE` (a new,
# MAGIC data-changing commit). Bounded by retention: `deletedFileRetentionDuration`
# MAGIC (files, 7d) + `logRetentionDuration` (history, 30d).

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE HISTORY customers;   -- find the version you want

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Read an earlier version (clause + @ shorthand both work)
# MAGIC SELECT * FROM customers VERSION AS OF 1 ORDER BY id;

# COMMAND ----------

# DataFrame API equivalent (versionAsOf)
df_v1 = spark.read.option("versionAsOf", 1).table(f"{catalog}.{schema}.customers")
display(df_v1.orderBy("id"))

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Roll back to version 1 (history preserved — RESTORE is itself a new commit)
# MAGIC RESTORE TABLE customers TO VERSION AS OF 1;
# MAGIC SELECT * FROM customers ORDER BY id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Change Data Feed: row-level diffs for incremental downstream propagation.
# MAGIC -- Must be enabled BEFORE the changes you want to read (not retroactive).
# MAGIC ALTER TABLE customers SET TBLPROPERTIES (delta.enableChangeDataFeed = true);
# MAGIC UPDATE customers SET amount = amount + 10 WHERE id = 1;   -- a change to capture

# COMMAND ----------

# Read the change feed from the version where CDF was enabled onward.
cdf_start = spark.sql("SELECT max(version) AS v FROM (DESCRIBE HISTORY customers)").first()["v"]
changes = (spark.read.option("readChangeFeed", "true")
                     .option("startingVersion", cdf_start)
                     .table(f"{catalog}.{schema}.customers"))
# _change_type: insert | update_preimage | update_postimage | delete (+ _commit_version/_timestamp)
display(changes.orderBy("_commit_version"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cleanup — drop demo objects so the notebook is rerunnable
# MAGIC Dropping the **managed** tables also deletes their data files.

# COMMAND ----------

spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print(f"Dropped {catalog}.{schema}")
