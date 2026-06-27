# Databricks notebook source
# MAGIC %md
# MAGIC # 📚 Databricks Data Engineering — Learning Path (Index Notebook)
# MAGIC
# MAGIC **What this is:** Your home base for learning data engineering on Databricks. It lays out *what* to learn, *in what order*, and *why* — with links to the official docs and a checklist you can tick off as you go.
# MAGIC
# MAGIC **How to use it:**
# MAGIC 1. Import this `.py` file into your Databricks workspace (`Workspace ▸ Import ▸ File`). It opens as a notebook.
# MAGIC 2. Read the roadmap below from top to bottom.
# MAGIC 3. Run the small "environment check" cells to confirm your setup works.
# MAGIC 4. Tackle one stage at a time. Ask me for a deep-dive + hands-on notebook for any stage.
# MAGIC
# MAGIC > **A note on naming (important):** In June 2025 Databricks grouped its data engineering tools under one umbrella called **Lakeflow**. Older names still appear everywhere, so here's the translation:
# MAGIC > | Old name | New name (2025+) |
# MAGIC > |---|---|
# MAGIC > | Delta Live Tables (DLT) | **Lakeflow Spark Declarative Pipelines** |
# MAGIC > | Workflows | **Lakeflow Jobs** |
# MAGIC > | (managed ingestion connectors) | **Lakeflow Connect** |
# MAGIC >
# MAGIC > Existing DLT pipelines keep working unchanged — it's mostly a rebranding plus new capabilities.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 🗺️ The Big Picture
# MAGIC
# MAGIC Data engineering on Databricks is about moving data from messy sources into clean, trustworthy, query-ready tables — reliably and at scale. Almost everything is built on two foundations:
# MAGIC
# MAGIC - **Delta Lake** — the storage format that makes your data reliable (think: a spreadsheet that never corrupts, remembers its history, and supports updates/deletes).
# MAGIC - **Unity Catalog** — the governance layer that controls *who can see and do what* across all your data and AI assets.
# MAGIC
# MAGIC Everything else (ingestion, transformation, orchestration) sits on top of these two.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 🪜 The Recommended Learning Path
# MAGIC
# MAGIC Work through these stages in order. Each builds on the previous one.
# MAGIC
# MAGIC ### Stage 1 — Foundations: Lakehouse & the Platform
# MAGIC - What a **Lakehouse** is (data lake + data warehouse in one) and why it replaced the older two-system approach.
# MAGIC - Workspace basics: notebooks, clusters vs. **serverless** compute, the catalog explorer.
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/introduction
# MAGIC
# MAGIC ### Stage 2 — Delta Lake (the storage foundation)
# MAGIC - Tables, ACID transactions (all-or-nothing writes), schema enforcement.
# MAGIC - **Time travel** (query old versions), `MERGE` (upserts), `OPTIMIZE` & `VACUUM` (housekeeping).
# MAGIC - Managed vs. external tables.
# MAGIC - **Liquid clustering** — the modern way to lay data out for fast queries. It *replaces* partitioning and Z-ordering, and you can change the clustering columns later **without rewriting the whole table**. Databricks now recommends it for **all new tables**.
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/delta/
# MAGIC - 📄 Liquid clustering: https://docs.databricks.com/aws/en/delta/clustering
# MAGIC
# MAGIC ### Stage 3 — Ingestion: getting data IN
# MAGIC - **Auto Loader** — incrementally and efficiently loads new files as they land in cloud storage.
# MAGIC - `COPY INTO` and `CREATE TABLE AS SELECT (CTAS)` for simpler/batch loads.
# MAGIC - **Lakeflow Connect** — point-and-click managed connectors (Salesforce, SQL Server, Workday, ServiceNow, etc.).
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/ingestion/
# MAGIC
# MAGIC ### Stage 4 — The Medallion Architecture (how to organize data)
# MAGIC - **Bronze** (raw) → **Silver** (cleaned/conformed) → **Gold** (business-ready aggregates).
# MAGIC - Why this layered design makes pipelines easier to debug and trust.
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/lakehouse/medallion
# MAGIC
# MAGIC ### Stage 5 — Transformation Pipelines: Lakeflow Spark Declarative Pipelines (formerly DLT)
# MAGIC - **Declarative** pipelines: you describe the *result* you want; Databricks figures out the execution.
# MAGIC - Streaming tables, materialized views, and data quality **expectations** (rules that catch bad data).
# MAGIC - **AUTO CDC** for handling slowly changing dimensions (SCD Type 1 & 2).
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/dlt/
# MAGIC
# MAGIC ### Stage 6 — Streaming (real-time data)
# MAGIC - **Structured Streaming** fundamentals: treat a never-ending stream of data like a growing table.
# MAGIC - Checkpoints, triggers, and exactly-once processing.
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/structured-streaming/
# MAGIC
# MAGIC ### Stage 7 — Orchestration: Lakeflow Jobs (formerly Workflows)
# MAGIC - Scheduling, task dependencies, retries, notifications.
# MAGIC - Running notebooks, SQL, and pipelines together as one production workflow.
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/jobs/
# MAGIC
# MAGIC ### Stage 8 — Governance: Unity Catalog
# MAGIC - The 3-level namespace: **`catalog.schema.table`**.
# MAGIC - Permissions, data lineage, and the discovery experience.
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/data-governance/unity-catalog/
# MAGIC
# MAGIC ### Stage 9 — Production Engineering
# MAGIC - **Databricks Asset Bundles (DABs)** for CI/CD (deploy pipelines as code).
# MAGIC - Performance tuning, cost control, and monitoring.
# MAGIC - Data layout for performance: **liquid clustering** vs. legacy partitioning, plus **predictive optimization** (Databricks auto-runs `OPTIMIZE`/`VACUUM` on managed tables).
# MAGIC - 📄 Docs: https://docs.databricks.com/aws/en/dev-tools/bundles/

# COMMAND ----------

# MAGIC %md
# MAGIC ## ✅ Progress Checklist
# MAGIC
# MAGIC Copy this into your notes and tick items as you finish. Ask me to generate a focused notebook for any unchecked item.
# MAGIC
# MAGIC - [ ] Stage 1 — Lakehouse & Platform basics
# MAGIC - [ ] Stage 2 — Delta Lake
# MAGIC - [ ] Stage 3 — Ingestion (Auto Loader, COPY INTO, Lakeflow Connect)
# MAGIC - [ ] Stage 4 — Medallion Architecture
# MAGIC - [ ] Stage 5 — Lakeflow Spark Declarative Pipelines
# MAGIC - [ ] Stage 6 — Structured Streaming
# MAGIC - [ ] Stage 7 — Lakeflow Jobs (orchestration)
# MAGIC - [ ] Stage 8 — Unity Catalog governance
# MAGIC - [ ] Stage 9 — Production engineering (DABs, tuning, monitoring)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 🔧 Environment Check
# MAGIC
# MAGIC Run the cells below to confirm your workspace is ready. These are read-only and safe.

# COMMAND ----------

# Confirm Spark is available and check the Databricks Runtime version.
print("Spark version:", spark.version)
print("Databricks Runtime:", spark.conf.get("spark.databricks.clusterUsageTags.sparkVersion", "unknown (likely serverless)"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Check Unity Catalog access
# MAGIC This lists the catalogs you can see. If you get a permissions error, ask your workspace admin for access to a catalog (or use the default `workspace` / `main` catalog if available).

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW CATALOGS;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Set your working namespace
# MAGIC Edit the two values below to a catalog and schema you have **write** access to. We'll use these in the hands-on notebooks. Creating a personal sandbox schema is a good habit.

# COMMAND ----------

# 👉 EDIT THESE to match a catalog/schema you can write to.
CATALOG = "workspace"          # e.g., "main", "workspace", or your team catalog
SCHEMA  = "de_learning_sandbox"  # your personal practice schema

# Create the schema if it doesn't exist, then select it as the default.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

print(f"✅ Working in: {CATALOG}.{SCHEMA}")
print("Current catalog:", spark.catalog.currentCatalog())
print("Current schema :", spark.catalog.currentDatabase())

# COMMAND ----------

# MAGIC %md
# MAGIC ### Quick Delta smoke test (optional)
# MAGIC Creates a tiny Delta table, reads it back, then cleans up. Confirms end-to-end write/read works. Comment out the final `DROP` if you want to keep it.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE _smoke_test (id INT, label STRING);
# MAGIC INSERT INTO _smoke_test VALUES (1, 'hello'), (2, 'databricks');
# MAGIC SELECT * FROM _smoke_test ORDER BY id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Delta time travel: every table keeps a version history.
# MAGIC DESCRIBE HISTORY _smoke_test;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Cleanup. Remove this line if you want to keep the test table.
# MAGIC DROP TABLE IF EXISTS _smoke_test;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 🧊 Spotlight: Liquid Clustering (hands-on)
# MAGIC
# MAGIC This is a feature you'll use constantly, so here's a focused intro you can run now.
# MAGIC
# MAGIC ### What problem does it solve?
# MAGIC When a table gets large, a query like `WHERE customer_id = 123` shouldn't have to scan the whole table. The trick is to physically group related rows together on disk so the engine can **skip** the files that can't contain your answer (this is called *data skipping*). The old ways to do this were **partitioning** and **Z-ordering**.
# MAGIC
# MAGIC ### Why liquid clustering is better than the old ways
# MAGIC - **No upfront guessing.** With partitioning, you had to pick the right column at creation — and changing it later meant rewriting the entire table. With liquid clustering you can **change the clustering columns anytime without rewriting existing data**.
# MAGIC - **No "too many small files" / skew problems.** Partitioning on a high-cardinality column (like `customer_id`) creates a mess of tiny partitions. Liquid clustering handles high-cardinality columns and data skew gracefully.
# MAGIC - **It's incremental.** `OPTIMIZE` only reorganizes new/changed data, so routine maintenance stays cheap.
# MAGIC - **Databricks recommends it for ALL new tables** — including streaming tables and materialized views.
# MAGIC
# MAGIC ### The one rule to remember
# MAGIC > Liquid clustering is **not compatible** with partitioning or `ZORDER`. You use it *instead of* them — never together.
# MAGIC
# MAGIC ### Two flavors
# MAGIC | Flavor | Syntax | When to use |
# MAGIC |---|---|---|
# MAGIC | **Manual** | `CLUSTER BY (col1, col2)` | You know which columns you filter on most. Up to **4** columns. |
# MAGIC | **Automatic** | `CLUSTER BY AUTO` | Let Databricks analyze your query history and pick the keys. Needs a Unity Catalog **managed** table + **predictive optimization** (DBR 15.4 LTS+). |
# MAGIC
# MAGIC ### Good clustering-key choices
# MAGIC - Columns you **filter on most often** (in `WHERE` / `JOIN` / `GROUP BY`).
# MAGIC - High-cardinality columns are fine (that's a strength here).
# MAGIC - Keys must be columns that have **statistics collected** (by default, the first 32 columns of the table).
# MAGIC - Supported types: date, timestamp, string, the integer family, and the float/decimal family.
# MAGIC
# MAGIC *Verified against https://docs.databricks.com/aws/en/delta/clustering (doc last updated April 2026). GA for Delta tables on Databricks Runtime 15.2+.*

# COMMAND ----------

# MAGIC %md
# MAGIC ### Hands-on 1: Create a table WITH liquid clustering
# MAGIC `CLUSTER BY` goes right after the table name. Below we cluster on `country` and `signup_date` — pretend these are our most common filters.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE lc_demo_customers (
# MAGIC   customer_id BIGINT,
# MAGIC   country     STRING,
# MAGIC   signup_date DATE,
# MAGIC   lifetime_value DECIMAL(12,2)
# MAGIC )
# MAGIC CLUSTER BY (country, signup_date);

# COMMAND ----------

# Insert some sample rows so we have data to cluster.
from pyspark.sql import functions as F

df = (spark.range(0, 100000)
      .withColumn("customer_id", F.col("id"))
      .withColumn("country", F.element_at(
          F.array(*[F.lit(c) for c in ["IN", "US", "UK", "DE", "SG"]]),
          (F.col("id") % 5 + 1).cast("int")))
      .withColumn("signup_date", F.expr("date_add('2023-01-01', cast(id % 900 as int))"))
      .withColumn("lifetime_value", (F.rand() * 10000).cast("decimal(12,2)"))
      .drop("id"))

df.write.mode("append").saveAsTable("lc_demo_customers")
print("Rows written:", spark.table("lc_demo_customers").count())

# COMMAND ----------

# MAGIC %md
# MAGIC ### Hands-on 2: Confirm clustering is set
# MAGIC `DESCRIBE DETAIL` shows the clustering columns under the `clusteringColumns` field.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE DETAIL lc_demo_customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Hands-on 3: Trigger clustering with OPTIMIZE
# MAGIC Clustering is **incremental** — `OPTIMIZE` only rewrites what's needed. Run this after big inserts (or let **predictive optimization** do it automatically on managed tables).

# COMMAND ----------

# MAGIC %sql
# MAGIC OPTIMIZE lc_demo_customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Hands-on 4: A query that benefits
# MAGIC Filtering on a clustering key lets Delta skip irrelevant files. (On a small demo table the speed-up is tiny — the benefit grows with table size.)

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT country, COUNT(*) AS customers, ROUND(AVG(lifetime_value), 2) AS avg_ltv
# MAGIC FROM lc_demo_customers
# MAGIC WHERE country = 'IN' AND signup_date >= '2024-01-01'
# MAGIC GROUP BY country;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Hands-on 5: Change clustering keys later (no rewrite needed)
# MAGIC This is the superpower partitioning never had. New writes use the new keys; to also reorganize *existing* data, run `OPTIMIZE ... FULL`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Switch to clustering on customer_id instead
# MAGIC ALTER TABLE lc_demo_customers CLUSTER BY (customer_id);
# MAGIC
# MAGIC -- Reorganize ALL existing data to match the new keys (can be slow on huge tables)
# MAGIC OPTIMIZE lc_demo_customers FULL;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Hands-on 6 (optional): Let Databricks pick the keys automatically
# MAGIC Requires a Unity Catalog **managed** table with **predictive optimization** enabled (DBR 15.4 LTS+). If your workspace doesn't have it enabled, this cell may no-op or error — that's fine, just skip it.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE lc_demo_customers CLUSTER BY AUTO;
# MAGIC -- Check it: look for clusterByAuto = true
# MAGIC SHOW TBLPROPERTIES lc_demo_customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Cleanup
# MAGIC Remove the demo table when you're done.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP TABLE IF EXISTS lc_demo_customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 📌 Where to go next
# MAGIC
# MAGIC You're set up. Pick **Stage 1** or **Stage 2** and ask me for the deep-dive lesson + hands-on notebook.
# MAGIC
# MAGIC **Official docs starting point:** https://docs.databricks.com/aws/en/data-engineering
# MAGIC
# MAGIC *Last verified against Databricks docs: May 2026. Lakeflow naming reflects the June 2025 Data + AI Summit announcements.*
