-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Row-Level Security (RLS) in Databricks — Hands-on
-- MAGIC
-- MAGIC **What you'll learn:** how to make a single table return *different rows to different users*, using a
-- MAGIC **row filter** + a **mapping table** in Unity Catalog. Same table, different eyes.
-- MAGIC
-- MAGIC > **RLS in one line:** you are not hiding values *inside* a column (that's a *column mask* / dynamic data
-- MAGIC > masking) — you are restricting **which whole rows** come back.
-- MAGIC
-- MAGIC ### Prerequisites
-- MAGIC - A workspace **enabled for Unity Catalog** (row filters are a UC-only feature).
-- MAGIC - Compute that supports row filters: a **SQL warehouse**, *or* **DBR 12.2 LTS+** (standard access mode),
-- MAGIC   *or* **DBR 15.4 LTS+** (dedicated access mode). Older runtimes "fail secure" and return **no data**.
-- MAGIC - Privileges to create a schema/table/function in the catalog you use below
-- MAGIC   (`CREATE SCHEMA`, `CREATE TABLE`, `CREATE FUNCTION`), plus `USE CATALOG` / `USE SCHEMA`.
-- MAGIC
-- MAGIC ### How to run
-- MAGIC Run the cells top-to-bottom. Edit the catalog/schema in the next cell first if needed.
-- MAGIC Everything is **Delta** + **three-level namespacing** (`catalog.schema.table`) by default.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 0 · Set up a sandbox catalog & schema
-- MAGIC Change `databricks_orange` to a catalog where you can create objects (e.g. `main`). We use a fresh
-- MAGIC `rls_demo` schema so we never touch your real data. `USE` sets the default namespace for later cells.

-- COMMAND ----------

-- CREATE SCHEMA IF NOT EXISTS databricks_orange.rls_demo;  -- uncomment if you have CREATE SCHEMA
USE CATALOG databricks_orange;
USE SCHEMA rls_demo;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 1 · Create a source table and load sample data
-- MAGIC This is the table we want to protect. Each store belongs to a `region`. Today, **everyone** can see
-- MAGIC every row — that's what we're about to fix.

-- COMMAND ----------

CREATE OR REPLACE TABLE stores (
  store_id INT,
  store_name STRING,
  region STRING,        -- East / West / North / South
  monthly_sales DOUBLE
) USING DELTA;

INSERT INTO stores VALUES
  (5,  'Store 5',  'East',  120000),
  (8,  'Store 8',  'West',   95000),
  (10, 'Store 10', 'East',  138000),
  (11, 'Store 11', 'North',  87000),
  (12, 'Store 12', 'South', 102000),
  (13, 'Store 13', 'East',  146000),
  (21, 'Store 21', 'West',   78000),
  (24, 'Store 24', 'North', 113000);

SELECT * FROM stores ORDER BY store_id;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 2 · Create the mapping table (access-control list)
-- MAGIC How does Databricks know *which region you own*? You tell it — in a small lookup table that maps a
-- MAGIC **user email → region**. This is the knob you'll turn later to grant/revoke access (no code changes).

-- COMMAND ----------

CREATE OR REPLACE TABLE arls_mapping (
  user_email STRING,
  region     STRING
) USING DELTA;

-- A few static example owners (these emails are illustrative)
INSERT INTO arls_mapping VALUES
  ('east_owner@example.com',  'East'),
  ('west_owner@example.com',  'West'),
  ('south_owner@example.com', 'South'),
  ('north_owner@example.com', 'North');

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Map *yourself* in, so the demo visibly works
-- MAGIC `current_user()` returns the identity running the query. We insert **your** email mapped to **East**,
-- MAGIC so that after we apply the filter you'll see only East stores. Change `'East'` to test other regions.

-- COMMAND ----------

INSERT INTO arls_mapping
SELECT current_user() AS user_email, 'East' AS region;

SELECT * FROM arls_mapping;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 3 · Build the intuition before writing the function
-- MAGIC A row filter is just a function that returns **TRUE/FALSE per row**. Let's prove the lookup logic first.
-- MAGIC `EXISTS(...)` collapses "did I find a matching row in the mapping table?" into a clean boolean —
-- MAGIC exactly what a row filter needs.

-- COMMAND ----------

-- Are YOU mapped to 'East'?  (should return true, because of the cell above)
SELECT EXISTS (
  SELECT 1 FROM arls_mapping
  WHERE user_email = current_user()
    AND lower(region) = lower('East')
) AS can_see_east;

-- COMMAND ----------

-- Are YOU mapped to 'West'?  (should return false)
SELECT EXISTS (
  SELECT 1 FROM arls_mapping
  WHERE user_email = current_user()
    AND lower(region) = lower('West')
) AS can_see_west;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 4 · Create the row-filter function (UDF)
-- MAGIC Now wrap that logic in a **scalar SQL UDF** that takes the row's `region` as a parameter `p_region`.
-- MAGIC
-- MAGIC - The return type **BOOLEAN** is *inferred* from `EXISTS`, so we write `RETURN EXISTS(...)` —
-- MAGIC   **not** `RETURN SELECT ...` (a scalar function returns one value, not a result set).
-- MAGIC - `lower()` on both sides makes the match **case-insensitive** (a classic gotcha).
-- MAGIC - **Rule:** if the function returns `FALSE` **or** `NULL`, the row is filtered out.

-- COMMAND ----------

CREATE OR REPLACE FUNCTION arls_region_filter(p_region STRING)
RETURN EXISTS (
  SELECT 1 FROM arls_mapping
  WHERE user_email = current_user()
    AND lower(region) = lower(p_region)
);

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 5 · Apply the filter to the table
-- MAGIC Bind the function to the `region` column. From now on, every query against `stores` runs this filter
-- MAGIC per row, automatically. (A table can have **only one** row filter.)

-- COMMAND ----------

ALTER TABLE stores
SET ROW FILTER arls_region_filter ON (region);

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 6 · See RLS in action 🎯
-- MAGIC Run the same `SELECT *` as in step 1 — but now you only get the region(s) **you** are mapped to (East).
-- MAGIC You didn't change the query; the data layer enforced access for you.

-- COMMAND ----------

SELECT * FROM stores ORDER BY store_id;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Even aggregates respect the filter
-- MAGIC The filter applies *as the rows are fetched*, so this count/sum only reflect rows you're allowed to see.

-- COMMAND ----------

SELECT region, COUNT(*) AS num_stores, SUM(monthly_sales) AS total_sales
FROM stores
GROUP BY region;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 7 · Change access by editing the mapping table (no code change)
-- MAGIC Grant yourself **North** too, then re-query. Access is data-driven — the function never changes.

-- COMMAND ----------

INSERT INTO arls_mapping
SELECT current_user(), 'North';

SELECT * FROM stores ORDER BY store_id;   -- now East + North rows appear

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 8 · What happens at query time (recap)
-- MAGIC For **every row** read from `stores`:
-- MAGIC 1. The row's `region` is passed into `arls_region_filter(p_region)`.
-- MAGIC 2. The function runs `EXISTS(... user_email = current_user() AND region = p_region ...)` against `arls_mapping`.
-- MAGIC 3. Mapped → **TRUE** → row returned.  Not mapped → **FALSE/NULL** → row dropped (you never see it).
-- MAGIC
-- MAGIC | Concept | Row-Level Security (this notebook) | Dynamic Data Masking |
-- MAGIC |---|---|---|
-- MAGIC | Controls | **which rows** return | **values inside a column** |
-- MAGIC | Feature | `SET ROW FILTER` | `SET MASK` |
-- MAGIC | Function returns | a **BOOLEAN** | a **masked value** |
-- MAGIC
-- MAGIC ### Common gotchas
-- MAGIC - **One row filter per table** — combine conditions inside the single function.
-- MAGIC - **Case sensitivity** — `'East'` ≠ `'east'`; normalize with `lower()`.
-- MAGIC - **`NULL` filters the row out**, same as `FALSE`.
-- MAGIC - **Type mismatch** can silently become `NULL` (ANSI mode off) → unexpected drops. Match parameter types.
-- MAGIC - **Drop order matters** (see cleanup below) or the table becomes inaccessible.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 9 · Inspect & manage the filter

-- COMMAND ----------

-- Where is the filter defined on the table? Look under "Row Filter" in the details.
DESCRIBE EXTENDED stores;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 10 · Cleanup — ⚠️ correct order matters
-- MAGIC You **must** `DROP ROW FILTER` *before* `DROP FUNCTION`. If you drop the function first, the table
-- MAGIC becomes **inaccessible** until you drop the orphaned filter reference.

-- COMMAND ----------

ALTER TABLE stores DROP ROW FILTER;   -- 1) detach the filter first
DROP FUNCTION IF EXISTS arls_region_filter;  -- 2) now safe to drop the function

-- Optional: remove demo tables entirely
-- DROP TABLE IF EXISTS stores;
-- DROP TABLE IF EXISTS arls_mapping;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Sources (official Databricks docs)
-- MAGIC - Manually apply row filters and column masks: https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/manually-apply
-- MAGIC - `ROW FILTER` clause (SQL reference): https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-row-filter
-- MAGIC - Row filters and column masks (overview): https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/

