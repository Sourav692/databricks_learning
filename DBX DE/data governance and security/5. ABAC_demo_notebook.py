# Databricks notebook source
# MAGIC %md
# MAGIC # ABAC — Attribute-Based Access Control (Hands-On)
# MAGIC
# MAGIC End-to-end demo of Unity Catalog **Attribute-Based Access Control**: governed tags, UDFs, and
# MAGIC **row-filter** + **column-mask** policies. Aligned to the lecture, with semantics corrected to match the
# MAGIC official docs.
# MAGIC
# MAGIC **Source of truth:**
# MAGIC - Core concepts: https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/core-concepts
# MAGIC - Create & manage policies: https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policies
# MAGIC
# MAGIC ### Prerequisites
# MAGIC - **Permissions:** Account admin (to define governed tags) **or** `ASSIGN` on the tag + `APPLY TAG` on the
# MAGIC   object; `MANAGE`/ownership on the securable + `EXECUTE` on the UDF to create the policy; `SELECT` to query.
# MAGIC - **Compute:** Serverless, **or** Standard/Dedicated on **DBR 16.4+** (dedicated needs fine-grained access
# MAGIC   control enabled). Older runtimes cannot read ABAC-protected tables.
# MAGIC - **Format:** Delta (default) under Unity Catalog three-level namespacing `catalog.schema.table`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Set your namespace
# MAGIC Change `CATALOG` to a Unity Catalog catalog you own. Everything else is created for you.

# COMMAND ----------

CATALOG = "databricks_ansh"   # <-- change to your catalog
SCHEMA  = "customers"
spark.conf.set("demo.catalog", CATALOG)
spark.conf.set("demo.schema", SCHEMA)
print(f"Using {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Create schema + a sample table, then load data

# COMMAND ----------

# MAGIC %sql
# MAGIC USE CATALOG ${demo.catalog};
# MAGIC CREATE SCHEMA IF NOT EXISTS ${demo.schema};
# MAGIC USE SCHEMA ${demo.schema};
# MAGIC
# MAGIC CREATE OR REPLACE TABLE profiles (
# MAGIC   first_name STRING,
# MAGIC   last_name  STRING,
# MAGIC   phone      STRING,
# MAGIC   address    STRING,
# MAGIC   ssn        STRING
# MAGIC ) USING DELTA;
# MAGIC
# MAGIC INSERT INTO profiles VALUES
# MAGIC   ('Ava','Stone','555-0101','New York, USA','111-11-1111'),
# MAGIC   ('Liam','Reed','555-0102','California, USA','222-22-2222'),
# MAGIC   ('Noah','Khan','555-0103','Texas, USA','333-33-3333'),
# MAGIC   ('Mia','Lopez','555-0104','Florida, USA','444-44-4444'),
# MAGIC   ('Emma','Cruz','555-0105','Berlin, Europe','555-55-5555'),
# MAGIC   ('Luca','Rossi','555-0106','Rome, Europe','666-66-6666'),
# MAGIC   ('Sara','Meyer','555-0107','Paris, Europe','777-77-7777'),
# MAGIC   ('Omar','Ali','555-0108','Chicago, USA','888-88-8888');
# MAGIC
# MAGIC SELECT * FROM profiles;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Governed tag (account-level)
# MAGIC Governed tags are defined **once at the account level** with allowed values.
# MAGIC
# MAGIC **UI step (do this first):** Catalog → **Govern → Governed tags → Create tag**.
# MAGIC Create key `pii` with allowed values: `address`, `ssn`.
# MAGIC
# MAGIC Then **apply** the tag to the column. (Columns do **not** inherit tags from the table — apply directly.)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Tag the address column with pii = address
# MAGIC ALTER TABLE profiles ALTER COLUMN address SET TAGS ('pii' = 'address');
# MAGIC
# MAGIC -- Inspect column tags
# MAGIC SELECT * FROM information_schema.column_tags
# MAGIC WHERE schema_name = '${demo.schema}' AND table_name = 'profiles';

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. ROW FILTER — show only NON-EU rows
# MAGIC
# MAGIC **Rule that trips everyone up:** a row-filter UDF returns `BOOLEAN`.
# MAGIC `TRUE` = **keep** the row, `FALSE` = **hide** it. So to hide EU rows, return `FALSE` for them.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Returns TRUE for rows to KEEP (non-EU), FALSE for EU rows (hidden)
# MAGIC CREATE OR REPLACE FUNCTION non_eu_filter(addr STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN NOT (addr ILIKE '%europe%' OR addr ILIKE '%.eu%' OR addr ILIKE '%e.u%');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Attach a ROW FILTER policy at the SCHEMA scope.
# MAGIC -- It applies to every table in the schema that has a column tagged pii=address.
# MAGIC CREATE OR REPLACE POLICY hide_eu_customers
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COMMENT 'Hide EU customer rows from non-exempt users'
# MAGIC ROW FILTER non_eu_filter
# MAGIC TO `account users`
# MAGIC -- EXCEPT `admins`        -- uncomment to let an admin group see raw data
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS has_tag_value('pii','address') AS addr_col
# MAGIC USING COLUMNS (addr_col);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- EU rows (Europe) should now be filtered out for non-exempt users.
# MAGIC SELECT * FROM profiles;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Inheritance demo — a new table is auto-protected
# MAGIC No new function, no new policy. Create a table in the same schema, tag its address column, and the
# MAGIC existing policy applies automatically.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE profiles_2 (
# MAGIC   first_name STRING, last_name STRING, phone STRING, address STRING, ssn STRING
# MAGIC ) USING DELTA;
# MAGIC
# MAGIC INSERT INTO profiles_2 VALUES
# MAGIC   ('Kai','Wong','555-0201','Seattle, USA','121-21-2121'),
# MAGIC   ('Nina','Petrov','555-0202','Madrid, Europe','343-43-4343');
# MAGIC
# MAGIC -- Only tagging is required for the schema-level policy to take effect.
# MAGIC ALTER TABLE profiles_2 ALTER COLUMN address SET TAGS ('pii' = 'address');
# MAGIC
# MAGIC SELECT * FROM profiles_2;   -- EU (Madrid) row is filtered out automatically

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. COLUMN MASK — hide SSN values
# MAGIC The mask UDF receives the column value (bound automatically by `ON COLUMN`) and returns the masked value.
# MAGIC Return type must match/cast to the column's type.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Tag the ssn column (different value of the same governed tag key)
# MAGIC ALTER TABLE profiles ALTER COLUMN ssn SET TAGS ('pii' = 'ssn');
# MAGIC
# MAGIC -- Masking UDF
# MAGIC CREATE OR REPLACE FUNCTION mask_ssn(ssn STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN '***-**-****';

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Column-mask policy: any column tagged pii=ssn is masked for non-exempt users.
# MAGIC CREATE OR REPLACE POLICY data_mask_ssn
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COMMENT 'Mask SSN columns'
# MAGIC COLUMN MASK mask_ssn
# MAGIC TO `account users`
# MAGIC -- EXCEPT `admins`
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS has_tag_value('pii','ssn') AS ssn_col
# MAGIC ON COLUMN ssn_col;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- SSN now shows ***-**-**** ; EU rows still filtered by the row policy.
# MAGIC SELECT * FROM profiles;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Inspect & manage policies

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW POLICIES ON SCHEMA ${demo.catalog}.${demo.schema};

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Cleanup (optional)
# MAGIC Drop policies first, then tags/tables.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP POLICY IF EXISTS hide_eu_customers ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC DROP POLICY IF EXISTS data_mask_ssn     ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC
# MAGIC ALTER TABLE profiles   ALTER COLUMN address UNSET TAGS ('pii');
# MAGIC ALTER TABLE profiles   ALTER COLUMN ssn     UNSET TAGS ('pii');
# MAGIC ALTER TABLE profiles_2 ALTER COLUMN address UNSET TAGS ('pii');
# MAGIC
# MAGIC -- DROP TABLE IF EXISTS profiles;
# MAGIC -- DROP TABLE IF EXISTS profiles_2;
# MAGIC -- DROP FUNCTION IF EXISTS non_eu_filter;
# MAGIC -- DROP FUNCTION IF EXISTS mask_ssn;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Gotchas recap
# MAGIC - **Row filter polarity:** UDF returns `TRUE` = keep, `FALSE` = hide.
# MAGIC - **Columns don't inherit tags** — tag each sensitive column directly.
# MAGIC - **Wrong tag value** = policy silently doesn't apply (no error, data unprotected).
# MAGIC - **One row filter per table** and **one mask per column** per user — overlaps throw
# MAGIC   `UC_ABAC_MULTIPLE_ROW_FILTERS` / `MULTIPLE_MASKS` and block the table.
# MAGIC - **Quotas:** 10 policies/catalog, 10/schema, 5/table, 20 principals/policy.
# MAGIC - **ABAC grants no access** — users still need `SELECT`.
# MAGIC - `has_tag` / `has_tag_value` (snake_case) preferred; camelCase `hasTag`/`hasTagValue` deprecated for new policies.

