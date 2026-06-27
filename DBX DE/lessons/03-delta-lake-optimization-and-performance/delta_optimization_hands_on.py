# Databricks notebook source
# MAGIC %md
# MAGIC # Delta Lake Optimization — Hands-On (Topic 3)
# MAGIC One runnable notebook covering the hands-on subtopics of Topic 3, at the
# MAGIC enterprise depth of the lessons:
# MAGIC - **3.1** OPTIMIZE (bin-packing ~1 GB) & ZORDER; optimized writes / auto compaction
# MAGIC - **3.2** Liquid Clustering (CLUSTER BY, re-key without rewrite, OPTIMIZE FULL)
# MAGIC - **3.3** VACUUM & retention (DRY RUN, RETAIN, safety check)
# MAGIC - **3.4** Deep (incremental) vs shallow CLONE; Predictive Optimization
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - DBR 15.4 LTS+ or Serverless (Liquid Clustering GA needs 15.4 LTS+; `OPTIMIZE FULL` needs 16.4 LTS+), Unity Catalog enabled
# MAGIC - `USE CATALOG` + `CREATE SCHEMA`/`CREATE TABLE` grants on the target catalog
# MAGIC - Edit the `catalog`/`schema` widgets to a sandbox you can write to
# MAGIC
# MAGIC **Scope:** Delta optimization features only — no Apache Spark core programming.
# MAGIC Run top to bottom; the last cell cleans up so it's rerunnable.

# COMMAND ----------

# MAGIC %md ## Setup — parameterize catalog & schema (UC three-level namespace)

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "de_demo_optim", "Schema")
catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Using {catalog}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3.1 — OPTIMIZE & ZORDER
# MAGIC Several small appends simulate the small-file problem. OPTIMIZE bin-packs
# MAGIC toward ~1 GB (`spark.databricks.delta.optimize.maxFileSize`); ZORDER colocates
# MAGIC values on the filter columns so queries skip more files.

# COMMAND ----------

spark.sql("CREATE OR REPLACE TABLE events (id INT, event_date DATE, region STRING, amt INT)")
# several small appends => several small files (one commit each)
for i in range(5):
    spark.sql(f"""INSERT INTO events VALUES
      ({i*2},   date'2026-06-0{i+1}', 'us', {i*10}),
      ({i*2+1}, date'2026-06-0{i+1}', 'eu', {i*10+5})""")
display(spark.sql("DESCRIBE DETAIL events").select("numFiles", "sizeInBytes"))

# COMMAND ----------

# MAGIC %sql
# MAGIC -- compact small files + colocate by frequently-filtered columns (pick 1–3 cols)
# MAGIC OPTIMIZE events ZORDER BY (event_date, region);

# COMMAND ----------

# Prevent small files at write time on churny tables (targets ~128 MB)
spark.sql("""ALTER TABLE events SET TBLPROPERTIES (
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true')""")
display(spark.sql("DESCRIBE DETAIL events").select("numFiles", "sizeInBytes"))  # fewer files after OPTIMIZE

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3.2 — Liquid Clustering (preferred for new tables)
# MAGIC Liquid Clustering is **incompatible** with partitioning/ZORDER, so we use a
# MAGIC separate table. Keys can be changed later **without rewriting** existing data.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Up to 4 keys; simple types only. CLUSTER BY AUTO would let Databricks choose.
# MAGIC CREATE OR REPLACE TABLE events_lc (id INT, event_date DATE, region STRING, amt INT)
# MAGIC   CLUSTER BY (event_date, region);
# MAGIC INSERT INTO events_lc SELECT * FROM events;
# MAGIC -- re-key WITHOUT rewriting existing data (metadata-only; applies going forward):
# MAGIC ALTER TABLE events_lc CLUSTER BY (region);
# MAGIC DESCRIBE DETAIL events_lc;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Optional: force immediate reclustering of legacy data to the new key.
# MAGIC -- Requires DBR 16.4 LTS+ (comment out on older runtimes).
# MAGIC OPTIMIZE events_lc FULL;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3.3 — VACUUM & versioning
# MAGIC Preview with `DRY RUN` first. Default retention (7 days) means nothing is
# MAGIC purged here yet — expected, and it protects time travel + concurrent readers.

# COMMAND ----------

# MAGIC %sql
# MAGIC VACUUM events DRY RUN;          -- preview only; deletes nothing

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Explicit retention; the safety check blocks sub-threshold VACUUM by default.
# MAGIC -- (RETAIN 0 HOURS would require disabling retentionDurationCheck — dangerous.)
# MAGIC VACUUM events RETAIN 168 HOURS;   -- 168h = 7 days

# COMMAND ----------

# DataFrame API equivalent
from delta.tables import DeltaTable
DeltaTable.forName(spark, f"{catalog}.{schema}.events").vacuum(168)  # retain 168 hours

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3.4 — Deep (incremental) vs shallow CLONE
# MAGIC Deep = independent copy; re-running it syncs **only new data** (cheap backups).
# MAGIC Shallow = instant pointer to source files (breaks if you VACUUM the source).

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE events_backup DEEP CLONE events;     -- durable, independent copy
# MAGIC CREATE OR REPLACE TABLE events_dev    SHALLOW CLONE events;  -- instant test copy (depends on source)
# MAGIC SELECT 'deep' AS clone, count(*) AS rows FROM events_backup
# MAGIC UNION ALL
# MAGIC SELECT 'shallow', count(*) FROM events_dev;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Deep clone is INCREMENTAL: add data to source, re-run, only the delta copies.
# MAGIC INSERT INTO events VALUES (999, date'2026-07-01', 'apac', 42);
# MAGIC CREATE OR REPLACE TABLE events_backup DEEP CLONE events;     -- syncs only the new row
# MAGIC SELECT count(*) AS backup_rows FROM events_backup;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Predictive Optimization — automatic maintenance
# MAGIC On UC **managed** tables, PO runs OPTIMIZE/VACUUM/ANALYZE automatically
# MAGIC (enabled by default for newer accounts). Enable explicitly at catalog/schema
# MAGIC level if needed (requires appropriate privileges):

# COMMAND ----------

# MAGIC %sql
# MAGIC -- ALTER CATALOG main ENABLE PREDICTIVE OPTIMIZATION;          -- account/catalog-wide
# MAGIC -- ALTER SCHEMA  ${catalog}.${schema} ENABLE PREDICTIVE OPTIMIZATION;  -- or DISABLE / INHERIT
# MAGIC SELECT 'PO runs OPTIMIZE/VACUUM/ANALYZE on UC managed tables automatically' AS note;

# COMMAND ----------

# MAGIC %md ## Cleanup — drop demo objects so the notebook is rerunnable
# MAGIC Dropping the **managed** tables also deletes their data files. The shallow
# MAGIC clone is dropped with the schema; its source files belong to `events`.

# COMMAND ----------

spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print(f"Dropped {catalog}.{schema}")
