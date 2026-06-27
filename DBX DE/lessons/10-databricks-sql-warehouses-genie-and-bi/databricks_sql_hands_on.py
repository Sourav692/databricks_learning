# Databricks notebook source
# MAGIC %md
# MAGIC # Databricks SQL — Hands-On (Topic 10)
# MAGIC Covers the runnable Topic 10 subtopics; UI features are shown as reference.
# MAGIC - **10.1** SQL Warehouse — where these queries run (reference + tuning notes)
# MAGIC - **10.2** Parameterized queries (`:named` markers), `IDENTIFIER()`, CTEs, caching
# MAGIC - **10.3** Alerts / scheduled queries / AI/BI Dashboards (reference cells)
# MAGIC - **10.4** Genie — a curated space + sample questions it could answer (reference)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - DBR 14.3 LTS+ / Serverless, or a **SQL Warehouse**; Unity Catalog enabled
# MAGIC - `CREATE`/`SELECT` on the target catalog; edit the widgets below
# MAGIC - Note: `:named` parameter markers are a Databricks SQL **editor** feature; in a
# MAGIC   notebook we use `${...}` widgets to parameterize equivalently (also injection-safe).
# MAGIC
# MAGIC **Scope:** Databricks SQL features only — no Apache Spark core programming.
# MAGIC Run top to bottom; the last cell cleans up so it's rerunnable.

# COMMAND ----------

# MAGIC %md ## Setup — a small gold-style table (UC 3-level namespace)

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "de_demo_dbsql", "Schema")
dbutils.widgets.text("start_date", "2026-06-01", "Start date")
dbutils.widgets.text("end_date", "2026-06-30", "End date")
catalog = dbutils.widgets.get("catalog"); schema = dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
spark.sql("CREATE OR REPLACE TABLE orders (id INT, region STRING, amount INT, order_date DATE)")
spark.sql("""INSERT INTO orders VALUES
  (1,'West',100, DATE'2026-06-05'), (2,'East',250, DATE'2026-06-10'),
  (3,'West', 80, DATE'2026-06-20'), (4,'East', -5, DATE'2026-07-02')""")
print(f"Created {catalog}.{schema}.orders")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10.2 — Parameterized query + CTE
# MAGIC In the **SQL editor** you'd use `:start_date` / `:end_date` named markers.
# MAGIC In a notebook, `${...}` reads the widgets — same effect, injection-safe.

# COMMAND ----------

# MAGIC %sql
# MAGIC WITH recent AS (                       -- CTE: name the filtered step
# MAGIC   SELECT * FROM ${catalog}.${schema}.orders
# MAGIC   WHERE order_date BETWEEN DATE'${start_date}' AND DATE'${end_date}'
# MAGIC )
# MAGIC SELECT region, SUM(amount) AS revenue, COUNT(*) AS orders
# MAGIC FROM recent
# MAGIC GROUP BY region
# MAGIC ORDER BY revenue DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10.2 — `IDENTIFIER()`: parameterize the *object name* safely
# MAGIC A value parameter can't sit after `FROM`. `IDENTIFIER()` binds a string as a
# MAGIC table/column **name** — injection-safe dynamic targeting (e.g. dev→prod).

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT region, SUM(amount) AS revenue
# MAGIC FROM IDENTIFIER('${catalog}.${schema}.orders')   -- table name as a parameter
# MAGIC GROUP BY region ORDER BY revenue DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10.2 — Query result caching
# MAGIC The 2nd identical run on unchanged data is a **cache hit** (instant, no compute).
# MAGIC Disable to force a cold run when benchmarking; a write invalidates the cache.

# COMMAND ----------

# MAGIC %sql
# MAGIC SET use_cached_result = false;   -- force a cold run (benchmark)
# MAGIC SELECT region, SUM(amount) AS revenue FROM ${catalog}.${schema}.orders GROUP BY region;
# MAGIC -- Re-run with caching on to see the difference:
# MAGIC SET use_cached_result = true;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10.1 / 10.3 — Reference (configured in the Databricks SQL UI / as bundle code)
# MAGIC - **SQL Warehouse (10.1):** run these on a *serverless* warehouse; size for
# MAGIC   per-query power, raise max clusters for concurrency, enable auto-stop.
# MAGIC - **Alert (10.3):** save `SELECT count(*) AS orders_today FROM orders
# MAGIC   WHERE order_date = current_date()`, then an Alert with condition
# MAGIC   `orders_today = 0` → notify on-call. As code: `resources.alerts` in a bundle.
# MAGIC - **AI/BI Dashboard (10.3):** add the revenue-by-region query as a dataset,
# MAGIC   drop a bar chart, schedule daily refresh + a Slack subscription. As code:
# MAGIC   `resources.dashboards` referencing a `.lvdash.json`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10.4 — Genie (reference): a curated space over this schema
# MAGIC Genie is a UI feature; point a **Genie space** at `${catalog}.${schema}` and add
# MAGIC a certified "revenue" metric. Sample questions it could answer over `orders`:
# MAGIC - "What was total revenue by region?"
# MAGIC - "Which region had the most orders this month?"
# MAGIC - "Show revenue trend by day."
# MAGIC
# MAGIC Programmatically (verified path, API 2.0 — ⚠️ verify version against docs):
# MAGIC `POST /api/2.0/genie/spaces/<space_id>/start-conversation` with
# MAGIC `{"content": "revenue by region"}`, then poll the message until COMPLETED.
# MAGIC Genie runs under **Unity Catalog** — users only see rows/columns they're granted.

# COMMAND ----------

# MAGIC %md ## Cleanup — drop the schema so the notebook is rerunnable

# COMMAND ----------

spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print(f"Dropped {catalog}.{schema}")
