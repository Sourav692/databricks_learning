# Databricks notebook source
# MAGIC %md
# MAGIC # Dynamic Data Masking (Column-Level Security) on Unity Catalog
# MAGIC
# MAGIC **What you'll learn (and actually run):**
# MAGIC 1. What dynamic data masking is and why it's "dynamic"
# MAGIC 2. Create a sample Delta table with sensitive data
# MAGIC 3. Create a **masking function** (SQL UDF)
# MAGIC 4. Apply it to a column with `ALTER TABLE ... SET MASK`
# MAGIC 5. Observe masked vs. unmasked results based on **group membership**
# MAGIC 6. Conditional masking with `USING COLUMNS`
# MAGIC 7. Compare with the older **dynamic views** approach
# MAGIC 8. Clean up safely (drop order matters!)
# MAGIC
# MAGIC > **Concept in one line:** Masking hides a column's *values* from unauthorized users **at query time**, based on *who is asking* — the data on disk is never changed or copied.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prerequisites
# MAGIC
# MAGIC | Requirement | Detail |
# MAGIC |---|---|
# MAGIC | **Workspace** | Unity Catalog enabled |
# MAGIC | **Compute** | A **SQL warehouse**, OR a **Standard** cluster on DBR **12.2 LTS+**, OR a **Dedicated** cluster on DBR **15.4 LTS+**. (You cannot read masked tables on Dedicated DBR 15.3 or below.) Filtering runs on serverless, so the workspace must have serverless enabled. |
# MAGIC | **Privileges** | To create objects: `CREATE SCHEMA`/`CREATE TABLE`/`CREATE FUNCTION`. To attach a mask to an existing table: be the **table owner** or have `MANAGE`, plus `EXECUTE` on the function, `USE SCHEMA`, `USE CATALOG`. |
# MAGIC | **A group** | We use a group named `developers`. Create it in **Settings → Identity and Access → Groups**. Members see real values; everyone else sees the mask. |
# MAGIC
# MAGIC > **Note:** In Unity Catalog, `CREATE TABLE` produces a **Delta** table by default — no `USING DELTA` needed.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration
# MAGIC Set the catalog, schema, and group once here. Every cell below reuses these values, so you only edit them in one place.

# COMMAND ----------

# Create notebook widgets for the three-level namespace + the gating group.
# Edit the defaults to a catalog/schema you have privileges on.
dbutils.widgets.text("catalog", "databricks_orange", "1. Catalog")
dbutils.widgets.text("schema",  "silver",            "2. Schema")
dbutils.widgets.text("group",   "developers",        "3. Gating group")

catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")
group   = dbutils.widgets.get("group")

# Set the working context so SQL cells can use clean, fully-qualified names.
spark.sql(f"CREATE CATALOG {catalog}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE CATALOG {catalog}")
spark.sql(f"USE SCHEMA {schema}")

print(f"Working in: {catalog}.{schema}  |  gating group: {group}")
print(f"You are signed in as: {spark.sql('SELECT current_user()').collect()[0][0]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 0 · Create a sample table with sensitive data
# MAGIC A small `customers` Delta table. The `email` column is the one we'll protect.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE ${catalog}.${schema}.customers (
# MAGIC   customer_id INT,
# MAGIC   name        STRING,
# MAGIC   email       STRING,   -- sensitive: we'll mask this
# MAGIC   country     STRING
# MAGIC );
# MAGIC
# MAGIC INSERT INTO ${catalog}.${schema}.customers VALUES
# MAGIC   (1, 'Alice',   'alice@acme.com',   'US'),
# MAGIC   (2, 'Bob',     'bob@acme.com',     'UK'),
# MAGIC   (3, 'Charlie', 'charlie@acme.com', 'FR');
# MAGIC
# MAGIC SELECT * FROM ${catalog}.${schema}.customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 · Create the masking function
# MAGIC
# MAGIC A masking function is a small **SQL UDF** (user-defined function = a reusable named SQL expression).
# MAGIC The logic: *if the caller is in the group, return the real value; otherwise return a masked value.*
# MAGIC
# MAGIC The decision is made by the built-in function **`is_account_group_member('group')`**, which returns
# MAGIC `true`/`false` for the **current user** running the query.
# MAGIC
# MAGIC > **Rules to remember:** the function's **first parameter type must match the column type** (`STRING` here),
# MAGIC > and a `CASE` expression **must** be closed with `END`.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION ${catalog}.${schema}.dynamic_mask(p_email STRING)
# MAGIC RETURN
# MAGIC   CASE
# MAGIC     WHEN is_account_group_member('${group}') THEN p_email   -- in the group  -> real value
# MAGIC     ELSE '***'                                              -- not in group  -> masked
# MAGIC   END;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 · Attach the mask to the column
# MAGIC Binding the function to the column is what makes masking happen automatically on every query.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE ${catalog}.${schema}.customers
# MAGIC   ALTER COLUMN email
# MAGIC   SET MASK ${catalog}.${schema}.dynamic_mask;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 · Query as normal — masking is automatic
# MAGIC
# MAGIC Run the same query you always would. What you get back depends on **whether you're in `developers`**:
# MAGIC - **Member** → real emails (`alice@acme.com`, ...)
# MAGIC - **Not a member** → `***`
# MAGIC
# MAGIC You did **not** change the query — the table itself now enforces the policy.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT customer_id, name, email, country
# MAGIC FROM ${catalog}.${schema}.customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Why am I seeing (or not seeing) the data?
# MAGIC The two functions below explain the result. `is_account_group_member` is the exact check the mask uses.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   current_user()                          AS i_am,
# MAGIC   is_account_group_member('${group}')     AS in_gating_group;
# MAGIC -- in_gating_group = true  -> you see real emails
# MAGIC -- in_gating_group = false -> you see ***

# COMMAND ----------

# MAGIC %md
# MAGIC > **Gotcha — group membership is cached.** If you add yourself to the group and the result doesn't change
# MAGIC > immediately, wait ~1 minute and re-run. This is exactly the "it's just a refresh delay" moment from the lesson.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 · Conditional masking with `USING COLUMNS`
# MAGIC
# MAGIC Sometimes the mask depends on **another column**. `USING COLUMNS` passes extra columns/literals into the function.
# MAGIC
# MAGIC **Rule:** the **first** parameter is always the masked column; everything else comes from `USING COLUMNS`.
# MAGIC
# MAGIC Here we redact `email` unless the user belongs to that customer's **country-specific** viewer group
# MAGIC (e.g. a `US` row is visible only to members of `US_email_viewers`).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 1=masked column, 2=another column (country), 3=group suffix literal
# MAGIC CREATE OR REPLACE FUNCTION ${catalog}.${schema}.mask_email_by_country(
# MAGIC   email STRING, country STRING, group_suffix STRING DEFAULT '_email_viewers')
# MAGIC RETURN IF(
# MAGIC   is_account_group_member(country || group_suffix),  -- e.g. 'US_email_viewers'
# MAGIC   email,
# MAGIC   'REDACTED'
# MAGIC );

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Swap the simple mask for the conditional one. (Re-attaching replaces the previous mask on this column.)
# MAGIC ALTER TABLE ${catalog}.${schema}.customers
# MAGIC   ALTER COLUMN email
# MAGIC   SET MASK ${catalog}.${schema}.mask_email_by_country
# MAGIC   USING COLUMNS (country, '_email_viewers');
# MAGIC
# MAGIC SELECT * FROM ${catalog}.${schema}.customers;
# MAGIC -- A member of 'US_email_viewers' sees the US email but REDACTED for UK/FR rows.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 · The older alternative — a dynamic view
# MAGIC
# MAGIC Before native column masks, the same effect was achieved with a **dynamic view**: a view that wraps the
# MAGIC table and decides per-column what to return. You then grant users the **view**, not the base table.
# MAGIC Still valid (and handy when you also reshape/join data), but column masks are the current best practice
# MAGIC because the policy lives **on the table** and users query the real table name.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE VIEW ${catalog}.${schema}.customers_secure_vw AS
# MAGIC SELECT
# MAGIC   customer_id,
# MAGIC   name,
# MAGIC   CASE WHEN is_account_group_member('${group}') THEN email ELSE '***' END AS email,
# MAGIC   country
# MAGIC FROM ${catalog}.${schema}.customers;
# MAGIC
# MAGIC -- NOTE: read from the BASE table here returns the *already-masked* column too,
# MAGIC -- because a column mask is still attached from Step 4. In a pure dynamic-view
# MAGIC -- pattern you would NOT attach a column mask and would grant only this view.

# COMMAND ----------

# MAGIC %sql
# MAGIC
# MAGIC SELECT * FROM ${catalog}.${schema}.customers_secure_vw

# COMMAND ----------

# MAGIC %md
# MAGIC ## Common mistakes & gotchas
# MAGIC
# MAGIC - **Group changes aren't instant** — membership is cached; wait a minute and re-run.
# MAGIC - **Compute matters** — SQL warehouse, Standard (DBR 12.2 LTS+), or Dedicated (DBR 15.4 LTS+). Dedicated ≤15.3 can't read masked tables.
# MAGIC - **Drop order is strict** — always `ALTER TABLE ... DROP MASK` **before** `DROP FUNCTION`, or the table becomes inaccessible.
# MAGIC - **`CREATE OR REPLACE TABLE` keeps the mask** if a same-named column exists — convenient, but it can persist silently.
# MAGIC - **First parameter type = column type** — mismatches break `INSERT`/`MERGE`/`UPDATE`.
# MAGIC - **Python logic needs a SQL wrapper** — you can't attach a Python UDF directly (you'd get `[ROUTINE_NOT_FOUND]`); wrap it in a SQL UDF (see optional cell below).
# MAGIC - **Definer vs invoker** — masks run with the definer's rights, but `is_account_group_member()` / `session_user()` evaluate as the *invoker*, which is why per-user masking works.
# MAGIC - **Inspect masks** in **Catalog Explorer** → the table → the column shows an `fx Column mask` badge.

# COMMAND ----------

# MAGIC %md
# MAGIC ### (Optional) Masking with Python logic — wrap a Python UDF in a SQL UDF
# MAGIC Attach the **SQL wrapper** as the mask, never the Python UDF directly.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Step A: Python UDF that stars out the local part of an email (before the @)
# MAGIC CREATE OR REPLACE FUNCTION ${catalog}.${schema}.email_mask_python(email STRING)
# MAGIC RETURNS STRING
# MAGIC LANGUAGE PYTHON
# MAGIC AS $$
# MAGIC import re
# MAGIC return re.sub(r'^[^@]+', lambda m: '*' * len(m.group()), email)
# MAGIC $$;
# MAGIC
# MAGIC -- Step B: SQL wrapper (this is what you attach as the mask)
# MAGIC CREATE OR REPLACE FUNCTION ${catalog}.${schema}.email_mask_sql(email STRING)
# MAGIC RETURN ${catalog}.${schema}.email_mask_python(email);

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cleanup (run in this order)
# MAGIC Drop the **mask first**, then the functions. Dropping a function while it's still attached makes the table inaccessible.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 1) Detach the mask from the column
# MAGIC ALTER TABLE ${catalog}.${schema}.customers ALTER COLUMN email DROP MASK;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 2) Now it's safe to drop the functions, view, and table
# MAGIC DROP FUNCTION IF EXISTS ${catalog}.${schema}.dynamic_mask;
# MAGIC DROP FUNCTION IF EXISTS ${catalog}.${schema}.mask_email_by_country;
# MAGIC DROP FUNCTION IF EXISTS ${catalog}.${schema}.email_mask_sql;
# MAGIC DROP FUNCTION IF EXISTS ${catalog}.${schema}.email_mask_python;
# MAGIC DROP VIEW     IF EXISTS ${catalog}.${schema}.customers_secure_vw;
# MAGIC DROP TABLE    IF EXISTS ${catalog}.${schema}.customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Recap
# MAGIC
# MAGIC 1. **Group** decides who sees real data.
# MAGIC 2. **Masking function** (SQL UDF) encodes the rule with `is_account_group_member()`.
# MAGIC 3. **`ALTER TABLE ... SET MASK`** binds the rule to a column.
# MAGIC 4. Queries are unchanged — the table enforces masking dynamically, per user, at query time.
# MAGIC
# MAGIC **Docs:**
# MAGIC - Row filters and column masks — https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/
# MAGIC - Manually apply row filters and column masks — https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/manually-apply
# MAGIC - Unity Catalog ABAC (tag-based masking at scale) — https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/
