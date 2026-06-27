# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 11 — Predictive optimization
# MAGIC
# MAGIC **Goal:** Enable **predictive optimization (PO)** the right way and verify it — set
# MAGIC the right VACUUM retention *before* enabling, turn PO on at the **schema** (then
# MAGIC **catalog**) level, confirm the **effective** state with `DESCRIBE … EXTENDED`, and
# MAGIC query the **system table** that logs every PO operation. PO runs OPTIMIZE / VACUUM /
# MAGIC ANALYZE for you on UC **managed** tables, on **serverless**, so there are no
# MAGIC maintenance jobs to schedule. This is the **final** lesson of the track.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Workspace on the **Premium** plan, in a **supported region**.
# MAGIC - A **SQL warehouse** or **DBR 12.2 LTS+** (this notebook runs on either).
# MAGIC - **Unity Catalog** enabled; you create **UC MANAGED** tables here (PO only runs on
# MAGIC   managed tables — never external, hive_metastore, or OpenSharing recipient tables).
# MAGIC - Privilege to flip the toggle at the level you target: **catalog owner** for a
# MAGIC   catalog, **schema owner** for a schema, **account admin** for the account.
# MAGIC - Read access to the `system.storage` schema to query the operations history.
# MAGIC - Delta Lake is the **default** format — we never write `USING DELTA`.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. The account-level toggle is **UI-driven** (accounts console) — exact steps below.
# MAGIC 2. Enable PO at the **schema** and **catalog** level with `ALTER … { ENABLE | DISABLE
# MAGIC    | INHERIT } PREDICTIVE OPTIMIZATION`, and how the **inheritance** model works.
# MAGIC 3. Set `delta.deletedFileRetentionDuration` **BEFORE** enabling PO if you need longer
# MAGIC    time travel (PO's VACUUM uses the default **7 days**).
# MAGIC 4. **Verify** the effective state with `DESCRIBE (CATALOG | SCHEMA | TABLE) EXTENDED`.
# MAGIC 5. **Monitor** PO via `system.storage.predictive_optimization_operations_history`.
# MAGIC 6. The whole-track recap and the one-line modern default.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters (Unity Catalog 3-level names)
# MAGIC Edit the widgets, then run top-to-bottom. Everything is namespaced `catalog.schema.table`.
# MAGIC You must have the **owner** privilege on the catalog/schema you point this at to change PO.

# COMMAND ----------

# Widgets let you point this at any catalog/schema you OWN (PO toggles need ownership).
dbutils.widgets.text("catalog", "main", "Catalog (you must own it to ENABLE PO)")
dbutils.widgets.text("schema", "delta_opt_demo", "Schema")
dbutils.widgets.text("table", "po_events", "Demo table")

catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")
table   = dbutils.widgets.get("table")
fqn     = f"{catalog}.{schema}.{table}"   # fully-qualified table name

# Create the schema if needed and set it as the working namespace.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print("Working in:", f"{catalog}.{schema}", "| demo table:", fqn)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Account-level enablement is UI-driven (accounts console)
# MAGIC
# MAGIC The **account** toggle is **not** SQL — it lives in the **accounts console**. PO is
# MAGIC **enabled by default for accounts created on/after Nov 11, 2024**, with a gradual
# MAGIC rollout to existing accounts (expected complete ~Aug 2026). To set it explicitly:
# MAGIC
# MAGIC **Step-by-step UI actions (account admin):**
# MAGIC 1. Sign in to the **Databricks accounts console** as an **account admin**.
# MAGIC 2. In the left sidebar, click **Settings**.
# MAGIC 3. Open the **Feature enablement** tab.
# MAGIC 4. Find **Predictive optimization**.
# MAGIC 5. Set the enablement state (enable for the account). Save / confirm.
# MAGIC 6. (Optional) Override below per object using the SQL in the next cells
# MAGIC    (`ALTER CATALOG …` / `ALTER SCHEMA …`).
# MAGIC
# MAGIC > Catalog and schema owners can override the inherited account decision in SQL.
# MAGIC > Verify exact labels against the current docs — accounts-console wording drifts.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Create a UC MANAGED demo table (Delta is the default)
# MAGIC PO only runs on **managed** tables. We use liquid clustering because PO's `OPTIMIZE`
# MAGIC does **not** run `ZORDER` — liquid clustering is the layout PO maintains.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Managed table (no LOCATION = managed). Liquid clustering, NOT partitioning/ZORDER,
# MAGIC -- because PO's OPTIMIZE compacts + clusters but ignores Z-ordered files.
# MAGIC CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :schema || '.' || :table) (
# MAGIC   event_id   BIGINT,
# MAGIC   event_type STRING,
# MAGIC   event_ts   TIMESTAMP,
# MAGIC   event_date DATE
# MAGIC )
# MAGIC CLUSTER BY (event_type, event_date);   -- PO maintains this layout; CLUSTER BY AUTO lets PO pick keys

# COMMAND ----------

# Seed a little data so VACUUM/OPTIMIZE/ANALYZE have something to act on later.
from pyspark.sql import functions as F

(spark.range(0, 100_000)
   .withColumn("event_type", F.element_at(F.array(F.lit("click"), F.lit("view"), F.lit("buy")),
                                          (F.col("id") % 3 + 1).cast("int")))
   .withColumn("event_ts", F.current_timestamp())
   .withColumn("event_date", F.current_date())
   .selectExpr("id AS event_id", "event_type", "event_ts", "event_date")
   .write.mode("append").saveAsTable(fqn))

print("Rows:", spark.table(fqn).count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Set `delta.deletedFileRetentionDuration` BEFORE enabling PO
# MAGIC PO's `VACUUM` deletes unreferenced files older than this window (**default 7 days**),
# MAGIC which bounds how far back you can **time travel**. If you need longer time travel,
# MAGIC **raise this first** — otherwise PO can delete files you expected to reach.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Need 30 days of time travel? Raise retention BEFORE enabling PO on this table.
# MAGIC ALTER TABLE IDENTIFIER(:catalog || '.' || :schema || '.' || :table)
# MAGIC   SET TBLPROPERTIES ('delta.deletedFileRetentionDuration' = '30 days');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Confirm the property took effect (look for delta.deletedFileRetentionDuration = 30 days).
# MAGIC SHOW TBLPROPERTIES IDENTIFIER(:catalog || '.' || :schema || '.' || :table);

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Enable PO at the SCHEMA level (then CATALOG) — the inheritance model
# MAGIC Enablement flows **account → catalog → schema → table**. Lower levels **INHERIT** by
# MAGIC default; a table's effective state is the **nearest explicit ENABLE/DISABLE above it**.
# MAGIC **A child DISABLE wins over a parent ENABLE.**

# COMMAND ----------

# MAGIC %sql
# MAGIC -- SCHEMA level: turn PO on for this schema; its tables inherit unless overridden.
# MAGIC -- Requires SCHEMA OWNER. (SCHEMA and DATABASE are synonyms here.)
# MAGIC ALTER SCHEMA IDENTIFIER(:catalog || '.' || :schema) ENABLE PREDICTIVE OPTIMIZATION;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- CATALOG level: you can instead govern the whole catalog at once. Requires CATALOG OWNER.
# MAGIC -- (Run only if you own the catalog; schemas/tables inherit unless they override.)
# MAGIC ALTER CATALOG IDENTIFIER(:catalog) ENABLE PREDICTIVE OPTIMIZATION;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- INHERITANCE DEMO: a DISABLE at a lower level beats an ENABLE above it.
# MAGIC -- (Uncomment to see the table go OFF even though the catalog/schema are ENABLED.)
# MAGIC -- ALTER TABLE IDENTIFIER(:catalog || '.' || :schema || '.' || :table) DISABLE PREDICTIVE OPTIMIZATION;
# MAGIC -- Then re-cover it by inheriting the parent decision again:
# MAGIC -- ALTER TABLE IDENTIFIER(:catalog || '.' || :schema || '.' || :table) INHERIT PREDICTIVE OPTIMIZATION;
# MAGIC SELECT 'inheritance demo — see commented ALTER statements above' AS note;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Verify the EFFECTIVE state with `DESCRIBE … EXTENDED`
# MAGIC Because of inheritance + overrides, the effective state isn't always obvious.
# MAGIC `DESCRIBE … EXTENDED` is the source of truth — look for the **"Predictive
# MAGIC Optimization"** field at each level.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE CATALOG EXTENDED IDENTIFIER(:catalog);

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE SCHEMA EXTENDED IDENTIFIER(:catalog || '.' || :schema);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Table-level: confirm the "Predictive Optimization" field reflects the inherited/explicit state.
# MAGIC DESCRIBE TABLE EXTENDED IDENTIFIER(:catalog || '.' || :schema || '.' || :table);

# COMMAND ----------

# MAGIC %md
# MAGIC > **Uses, edge cases & limitations — enablement & inheritance**
# MAGIC > - **Uses:** set PO once at the catalog/schema and let thousands of tables inherit.
# MAGIC > - **Edge cases:** a child `DISABLE` overrides a parent `ENABLE`; set back to
# MAGIC >   `INHERIT`/`ENABLE` to re-cover. Account toggle is UI-only; catalog/schema are SQL.
# MAGIC > - **Limitations:** **UC managed tables only** — not external, hive_metastore, or
# MAGIC >   OpenSharing recipient tables. Needs Premium + supported region + SQL warehouse or
# MAGIC >   DBR 12.2 LTS+. Changing PO requires the **owner** at that level.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Monitor PO with the system table
# MAGIC PO runs **asynchronously on serverless** (serverless jobs SKU billing). Every
# MAGIC operation it performs is logged here, so this is how you audit *what ran* and
# MAGIC *what it cost*. (Rows appear after PO actually runs maintenance — which may be a
# MAGIC while after enablement, since PO decides timing itself.)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- What has PO done lately? Filter to our schema; widen the window as needed.
# MAGIC SELECT operation_type, catalog_name, schema_name, table_name,
# MAGIC        operation_status, start_time
# MAGIC FROM system.storage.predictive_optimization_operations_history
# MAGIC WHERE catalog_name = :catalog
# MAGIC   AND schema_name  = :schema
# MAGIC   AND start_time >= current_timestamp() - INTERVAL 7 DAYS
# MAGIC ORDER BY start_time DESC;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Account-wide audit: which operations ran most, last 7 days (cost/usage shape).
# MAGIC SELECT operation_type, operation_status, count(*) AS ops
# MAGIC FROM system.storage.predictive_optimization_operations_history
# MAGIC WHERE start_time >= current_timestamp() - INTERVAL 7 DAYS
# MAGIC GROUP BY operation_type, operation_status
# MAGIC ORDER BY ops DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC > **Uses, edge cases & limitations — monitoring & what PO runs**
# MAGIC > - **Uses:** the system table is your audit + cost view for OPTIMIZE/VACUUM/ANALYZE.
# MAGIC > - **Edge cases:** no rows ≠ PO is off — PO decides *when* to run; it may not have
# MAGIC >   acted yet. Also, PO's `OPTIMIZE` **does not run `ZORDER`** (it ignores Z-ordered
# MAGIC >   files) — use liquid clustering for colocation.
# MAGIC > - **Limitations:** PO bills **serverless jobs DBUs**; VACUUM is bound by
# MAGIC >   `delta.deletedFileRetentionDuration` (**default 7 days**).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Whole-track recap — the decision ladder
# MAGIC Each rung solves a problem the previous rung exposed:
# MAGIC 1. **Traditional write** (L01) — baseline + the **small-file problem**.
# MAGIC 2. **Partitioning** (L02) — old layout; only large (>1 TB), low-cardinality tables.
# MAGIC 3. **Data skipping & Z-ordering** (L03) — *why* layout matters (skip files via stats).
# MAGIC 4. **OPTIMIZE / compaction** (L04) — the **manual** fix for small files.
# MAGIC 5. **Optimized writes + auto compaction** (L05–06) — **automatic** file sizing.
# MAGIC 6. **Auto optimize** (L07) — the umbrella + target-size autotuning.
# MAGIC 7. **Liquid clustering** (L08) — the **modern layout**; change keys with no rewrite.
# MAGIC 8. **Deletion vectors** (L09) — **merge-on-read**: mark rows deleted, no file rewrite.
# MAGIC 9. **VACUUM, time travel & retention** (L10) — manage the file history; reclaim storage.
# MAGIC 10. **Predictive optimization** (L11 — this one) — **fully managed maintenance**.
# MAGIC
# MAGIC ### The one-line modern default
# MAGIC > **UC managed table + liquid clustering + predictive optimization** — and let the
# MAGIC > platform do it. Reach for partitioning / Z-order / hand-scheduled maintenance only
# MAGIC > for legacy or external tables, or genuinely special cases.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Cleanup (rerunnable)
# MAGIC Drops the demo table. Leaves the PO schema/catalog toggle as-is (changing governance
# MAGIC back is a deliberate admin decision); uncomment the `INHERIT` line to undo the schema
# MAGIC toggle if this was a throwaway schema you own.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP TABLE IF EXISTS IDENTIFIER(:catalog || '.' || :schema || '.' || :table);
# MAGIC -- Optional: revert the schema PO toggle to inherit its parent again (you must own it):
# MAGIC -- ALTER SCHEMA IDENTIFIER(:catalog || '.' || :schema) INHERIT PREDICTIVE OPTIMIZATION;
