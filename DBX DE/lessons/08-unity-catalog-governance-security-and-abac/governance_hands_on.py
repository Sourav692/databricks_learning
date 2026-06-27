# Databricks notebook source
# MAGIC %md
# MAGIC # Unity Catalog Governance & Security — Hands-On (Topic 8)
# MAGIC One runnable notebook covering the hands-on subtopics of Topic 8, at the
# MAGIC enterprise depth of the lessons:
# MAGIC - **8.1** 3-level namespace, a Volume, information_schema discovery
# MAGIC - **8.2** GRANT / REVOKE (the USE chain) + SHOW GRANTS
# MAGIC - **8.3** Row filter & column mask (data-level security)
# MAGIC - **8.5** Unity Catalog functions — scalar SQL UDF, Python UDF, UDTF
# MAGIC - **8.4** ABAC — verified CREATE POLICY reference (governed tags + policies)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - DBR 14.3 LTS+ or Serverless, Unity Catalog enabled
# MAGIC - Privileges to create a schema/tables/functions and GRANT in the target catalog
# MAGIC - A group `analysts` exists for the GRANT demo; otherwise comment out the GRANT cell
# MAGIC - Edit the `catalog`/`schema` widgets to a sandbox you can write to
# MAGIC
# MAGIC **Scope:** governance features only — no Apache Spark core programming.
# MAGIC Run top to bottom; the last cell cleans up so it's rerunnable.

# COMMAND ----------

# MAGIC %md ## Setup — 3-level namespace + Volume (8.1)

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "de_demo_gov", "Schema")
catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.files")   # 8.1 Volume for files
print(f"Using {catalog}.{schema}; volume at /Volumes/{catalog}/{schema}/files")

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE customers (id INT, region STRING, email STRING, phone STRING);
# MAGIC INSERT INTO customers VALUES
# MAGIC   (1,'West','ada@corp.com','(415) 555-0101'),
# MAGIC   (2,'East','lin@corp.com','212.555.0102'),
# MAGIC   (3,'West','sam@corp.com','4155550103');
# MAGIC SELECT * FROM customers ORDER BY id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 8.1 discovery: programmatic metadata via information_schema
# MAGIC SELECT column_name, data_type
# MAGIC FROM ${catalog}.information_schema.columns
# MAGIC WHERE table_schema = '${schema}' AND table_name = 'customers';

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8.5 — Unity Catalog functions: SQL scalar UDF, Python UDF, UDTF
# MAGIC Governed, reusable functions. The mask UDF is reused as a column mask below.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- scalar SQL UDF (also used as a column mask in 8.3)
# MAGIC CREATE OR REPLACE FUNCTION mask_email(e STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN CASE WHEN is_account_group_member('pii_readers') THEN e ELSE '***@***' END;
# MAGIC
# MAGIC -- Python UDF for logic SQL can't easily express
# MAGIC CREATE OR REPLACE FUNCTION normalize_phone(p STRING)
# MAGIC RETURNS STRING LANGUAGE PYTHON
# MAGIC AS $$
# MAGIC   import re
# MAGIC   return re.sub(r'\D', '', p) if p else None
# MAGIC $$;
# MAGIC
# MAGIC SELECT id, mask_email(email) AS email, normalize_phone(phone) AS phone FROM customers ORDER BY id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- table function (UDTF): returns a table; call it in FROM
# MAGIC CREATE OR REPLACE FUNCTION customers_in(reg STRING)
# MAGIC RETURNS TABLE (id INT, email STRING)
# MAGIC RETURN SELECT id, email FROM customers WHERE region = customers_in.reg;
# MAGIC
# MAGIC SELECT * FROM customers_in('West') ORDER BY id;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8.3 — Data-level security: column mask + row filter
# MAGIC Attach the UDF as a column mask; add a row filter so non-admins see only West.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- column mask (reuses the UC function above)
# MAGIC ALTER TABLE customers ALTER COLUMN email SET MASK mask_email;
# MAGIC
# MAGIC -- row filter: admins see all, others only their region (demo: West)
# MAGIC CREATE OR REPLACE FUNCTION region_filter(region STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN is_account_group_member('admins') OR region = 'West';
# MAGIC ALTER TABLE customers SET ROW FILTER region_filter ON (region);
# MAGIC
# MAGIC SELECT * FROM customers ORDER BY id;   -- result varies by your group membership

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8.2 — GRANT / REVOKE (the USE chain) + SHOW GRANTS
# MAGIC Give a group read access. Comment out if the `analysts` group doesn't exist.

# COMMAND ----------

# MAGIC %sql
# MAGIC GRANT USE CATALOG ON CATALOG ${catalog} TO `analysts`;
# MAGIC GRANT USE SCHEMA  ON SCHEMA  ${catalog}.${schema} TO `analysts`;
# MAGIC GRANT SELECT      ON TABLE   ${catalog}.${schema}.customers TO `analysts`;
# MAGIC GRANT EXECUTE     ON FUNCTION ${catalog}.${schema}.mask_email TO `analysts`;
# MAGIC SHOW GRANTS ON TABLE ${catalog}.${schema}.customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8.4 — ABAC (verified CREATE POLICY reference)
# MAGIC ABAC applies row filters / column masks **by governed tag** across a catalog,
# MAGIC so you don't ALTER each table. Pattern (⚠️ verify availability; GRANT policies
# MAGIC are Beta):
# MAGIC ```sql
# MAGIC -- 1) tag the column with a governed tag
# MAGIC ALTER TABLE customers ALTER COLUMN email SET TAGS ('pii' = 'email');
# MAGIC
# MAGIC -- 2) one policy on the schema masks every column tagged pii=email
# MAGIC CREATE POLICY redact_email_policy
# MAGIC   ON SCHEMA ${catalog}.${schema}
# MAGIC   COLUMN MASK ${catalog}.${schema}.mask_email
# MAGIC   TO `account users`
# MAGIC   FOR TABLES
# MAGIC   MATCH COLUMNS has_tag_value('pii', 'email') AS c
# MAGIC   ON COLUMN c;
# MAGIC ```

# COMMAND ----------

# MAGIC %md ## Cleanup — drop demo objects so the notebook is rerunnable

# COMMAND ----------

spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print(f"Dropped {catalog}.{schema}")
