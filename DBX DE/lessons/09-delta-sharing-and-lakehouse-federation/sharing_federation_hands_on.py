# Databricks notebook source
# MAGIC %md
# MAGIC # Delta Sharing & Lakehouse Federation — Hands-On (Topic 9)
# MAGIC Covers Topic 9 hands-on subtopics:
# MAGIC - **9.1** Delta Sharing — create a share, add a table, create a recipient, grant
# MAGIC - **9.2** Lakehouse Federation — connection + foreign catalog + federated query (reference)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - DBR 14.3 LTS+ or Serverless, Unity Catalog enabled
# MAGIC - To create shares/recipients you need metastore-level privileges
# MAGIC   (`CREATE SHARE`, `CREATE RECIPIENT`); federation needs reachable external
# MAGIC   DB credentials. Cells that need external systems are marked REFERENCE.
# MAGIC - Edit the `catalog`/`schema` widgets to a sandbox you can write to
# MAGIC
# MAGIC **Scope:** sharing/federation features only — no Apache Spark core programming.
# MAGIC Run top to bottom; the last cell cleans up so it's rerunnable.

# COMMAND ----------

# MAGIC %md ## Setup — a small table to share (UC 3-level namespace)

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "de_demo_share", "Schema")
catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
spark.sql("CREATE OR REPLACE TABLE sales (id INT, region STRING, amount INT)")
spark.sql("INSERT INTO sales VALUES (1,'West',100),(2,'East',250)")
print(f"Created {catalog}.{schema}.sales")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9.1 — Delta Sharing: share → add table → recipient → grant
# MAGIC Requires metastore privileges. Comment out if you lack them.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE SHARE IF NOT EXISTS demo_sales_share COMMENT 'Curated sales for partners';
# MAGIC ALTER SHARE demo_sales_share ADD TABLE ${catalog}.${schema}.sales;
# MAGIC
# MAGIC -- open sharing recipient (generates an activation link/token):
# MAGIC CREATE RECIPIENT IF NOT EXISTS demo_partner;
# MAGIC -- D2D instead would be: CREATE RECIPIENT demo_partner USING ID '<sharing-id>';
# MAGIC
# MAGIC GRANT SELECT ON SHARE demo_sales_share TO RECIPIENT demo_partner;
# MAGIC SHOW GRANTS ON SHARE demo_sales_share;

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW ALL IN SHARE demo_sales_share;     -- inspect what's in the share

# COMMAND ----------

# MAGIC %md
# MAGIC ### Recipient side (reference)
# MAGIC - **D2D:** the share mounts as a read-only catalog → `SELECT * FROM <inbound>.<schema>.sales`.
# MAGIC - **Open:** download the credential profile from the activation link, then any client:
# MAGIC ```python
# MAGIC df = (spark.read.format("deltaSharing")
# MAGIC         .load("/path/config.share#demo_sales_share.de_demo_share.sales"))  # profile#share.schema.table
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9.2 — Lakehouse Federation (REFERENCE — needs an external DB)
# MAGIC Query an external Postgres **in place** (no copy), and join it to lakehouse data.
# MAGIC ```sql
# MAGIC -- 1) connection (credentials) + 2) foreign catalog (maps remote namespace)
# MAGIC CREATE CONNECTION pg_conn TYPE postgresql
# MAGIC   OPTIONS (host '<host>', port '5432', user '<u>', password secret('<scope>','<key>'));
# MAGIC CREATE FOREIGN CATALOG pg USING CONNECTION pg_conn OPTIONS (database 'appdb');
# MAGIC
# MAGIC -- 3) query foreign table; filter pushes down to Postgres
# MAGIC SELECT * FROM pg.public.orders WHERE order_date > current_date() - 7;
# MAGIC
# MAGIC -- 4) cross-source join: live Postgres + native Delta in one query
# MAGIC SELECT o.order_id, o.amount, c.segment
# MAGIC FROM pg.public.orders o
# MAGIC JOIN ${catalog}.${schema}.sales c ON o.region = c.region;
# MAGIC
# MAGIC -- 5) governance: foreign tables are UC objects
# MAGIC GRANT USE CATALOG ON CATALOG pg TO `analysts`;
# MAGIC GRANT SELECT ON TABLE pg.public.orders TO `analysts`;
# MAGIC -- cleanup (if you ran the above): DROP FOREIGN CATALOG pg; DROP CONNECTION pg_conn;
# MAGIC ```
# MAGIC Federation = read others' DBs in place; Delta Sharing (9.1) = share yours out — opposite directions.

# COMMAND ----------

# MAGIC %md ## Cleanup — drop share/recipient/schema so the notebook is rerunnable

# COMMAND ----------

spark.sql("DROP SHARE IF EXISTS demo_sales_share")
spark.sql("DROP RECIPIENT IF EXISTS demo_partner")
spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleaned up share, recipient, and schema. (Drop any pg foreign catalog/connection if created.)")
