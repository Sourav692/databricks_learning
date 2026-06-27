# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 08 — Liquid clustering
# MAGIC
# MAGIC **Goal:** *See* liquid clustering work — create a `CLUSTER BY` table, write data,
# MAGIC trigger clustering with incremental `OPTIMIZE`, **change the keys with no rewrite**,
# MAGIC recluster history with `OPTIMIZE … FULL`, and inspect the result with
# MAGIC `DESCRIBE DETAIL` (clusteringColumns), `DESCRIBE HISTORY`, and `SHOW TBLPROPERTIES`.
# MAGIC We also show `CLUSTER BY AUTO` and `CLUSTER BY NONE`.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - **DBR 15.4 LTS+** — liquid clustering is GA for Delta at this runtime (not 15.2).
# MAGIC   - DataFrame / `DeltaTable` clustering API: **DBR 14.3 LTS+**.
# MAGIC   - **`OPTIMIZE … FULL` needs DBR 16.0+**; `OPTIMIZE FULL WHERE` and
# MAGIC     `REPLACE PARTITIONED BY WITH CLUSTER BY` need **DBR 18.1+**.
# MAGIC   - `CLUSTER BY AUTO` needs a **UC managed table + predictive optimization**.
# MAGIC - **Unity Catalog** enabled, with `USE CATALOG`/`USE SCHEMA`, `CREATE SCHEMA`,
# MAGIC   and `CREATE TABLE` grants on the target catalog (managed tables recommended).
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. `CLUSTER BY (cols)` colocates rows on up to **4 keys** — the modern replacement
# MAGIC    for partitioning + Z-order (incompatible with both).
# MAGIC 2. Incremental `OPTIMIZE` (cheap) vs `OPTIMIZE … FULL` (full recluster).
# MAGIC 3. **Change keys with `ALTER TABLE … CLUSTER BY` — existing data is NOT rewritten.**
# MAGIC 4. Inspect with `DESCRIBE DETAIL` / `DESCRIBE HISTORY` / `SHOW TBLPROPERTIES`.
# MAGIC 5. `CLUSTER BY AUTO` (automatic key selection) and `CLUSTER BY NONE` (stop).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters (Unity Catalog 3-level names)
# MAGIC Edit the widgets, then run top-to-bottom. Everything is namespaced `catalog.schema.table`.

# COMMAND ----------

# Widgets let you point this at any catalog/schema you can write to.
dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "delta_opt_demo", "Schema")

catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")
table   = f"{catalog}.{schema}.events"   # fully-qualified UC name (managed Delta table)

# Create the schema if needed and set it as the working namespace.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Working in {catalog}.{schema}; demo table = {table}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Measurement helper
# MAGIC `DESCRIBE DETAIL` gives `numFiles`, `sizeInBytes`, and (for clustered tables)
# MAGIC `clusteringColumns`. We reuse this helper to make the numbers move.

# COMMAND ----------

def describe(table_name):
    """Print numFiles / sizeInBytes / clusteringColumns via DESCRIBE DETAIL."""
    d = (spark.sql(f"DESCRIBE DETAIL {table_name}")
              .select("numFiles", "sizeInBytes", "clusteringColumns").first())
    n, b, clus = d["numFiles"], d["sizeInBytes"], d["clusteringColumns"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    print(f"{table_name}: numFiles={n}, sizeInBytes={b:,} (~{avg_mb:.2f} MB/file), "
          f"clusteringColumns={clus}")
    return n, b, clus

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Create a table with liquid clustering (`CLUSTER BY`)
# MAGIC `CLUSTER BY` goes right after the column list. We pick **two keys** (`event_type`,
# MAGIC `event_date`) — the columns we filter on most. Delta is the default format, so we do
# MAGIC **not** write `USING DELTA`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Clustered Delta table. Up to 4 keys; CLUSTER BY after the column list.
# MAGIC CREATE OR REPLACE TABLE ${catalog}.${schema}.events (
# MAGIC   event_id    BIGINT,
# MAGIC   event_type  STRING,
# MAGIC   event_date  DATE,
# MAGIC   customer_id BIGINT,
# MAGIC   amount      DECIMAL(10,2)
# MAGIC )
# MAGIC CLUSTER BY (event_type, event_date);

# COMMAND ----------

# MAGIC %md
# MAGIC ### PySpark / DeltaTable equivalent (DBR 14.3 LTS+) — for reference
# MAGIC The same table via the builder API. (We keep the SQL table above as the working one.)

# COMMAND ----------

# from delta.tables import DeltaTable
# (DeltaTable.createOrReplace(spark)
#     .tableName(table)
#     .addColumn("event_id", "BIGINT")
#     .addColumn("event_type", "STRING")
#     .addColumn("event_date", "DATE")
#     .addColumn("customer_id", "BIGINT")
#     .addColumn("amount", "DECIMAL(10,2)")
#     .clusterBy("event_type", "event_date")   # the clustering keys
#     .execute())

# Confirm the clustering keys are registered (clusteringColumns) before we write data.
describe(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Stress: write data so there is something to cluster
# MAGIC We generate ~2M synthetic rows across a few event types and dates. Real clustering on
# MAGIC write only fully kicks in past a size threshold (1 key 64 MB … 4 keys 1 GB on UC
# MAGIC managed), so small demo writes may not be perfectly clustered — that's why we run
# MAGIC `OPTIMIZE` next.

# COMMAND ----------

from pyspark.sql import functions as F

rows = 2_000_000
df = (spark.range(rows)
        .withColumn("event_id",    F.col("id"))
        # a handful of event types -> a low/mid-cardinality clustering key
        .withColumn("event_type",  F.element_at(
            F.array(F.lit("click"), F.lit("view"), F.lit("purchase"), F.lit("refund")),
            (F.col("id") % 4 + 1).cast("int")))
        # ~30 distinct dates
        .withColumn("event_date",  F.expr("date_add('2026-01-01', cast(id % 30 as int))"))
        # high-cardinality column (great clustering key, terrible partition key)
        .withColumn("customer_id", (F.col("id") % 500_000))
        .withColumn("amount",      (F.rand() * 500).cast("decimal(10,2)"))
        .drop("id"))

# Append into the clustered table. INSERT/append is a clustering-on-write op.
df.write.mode("append").saveAsTable(table)

print("Loaded rows. Layout BEFORE OPTIMIZE:")
describe(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Apply: trigger clustering with incremental `OPTIMIZE`
# MAGIC Plain `OPTIMIZE` on a clustered table reclusters **only what needs it** (new/changed
# MAGIC data) and bin-packs small files. Cheap — run it frequently (every 1–2 h for
# MAGIC high-churn tables) or let predictive optimization do it on managed tables.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Incremental: only rewrites what's needed.
# MAGIC OPTIMIZE ${catalog}.${schema}.events;

# COMMAND ----------

# PySpark equivalent of incremental OPTIMIZE:
# from delta.tables import DeltaTable
# DeltaTable.forName(spark, table).optimize().executeCompaction()

print("Layout AFTER incremental OPTIMIZE:")
describe(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · The headline: change the clustering keys with NO rewrite
# MAGIC Switch the keys to the high-cardinality `customer_id` plus `event_date`.
# MAGIC `ALTER TABLE … CLUSTER BY` changes **only metadata** — existing data files are **not**
# MAGIC rewritten. New writes and the next incremental `OPTIMIZE` use the new keys.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Metadata-only change. clusteringColumns updates immediately; data is untouched.
# MAGIC ALTER TABLE ${catalog}.${schema}.events CLUSTER BY (customer_id, event_date);

# COMMAND ----------

# clusteringColumns now reflects the NEW keys, but numFiles/sizeInBytes are unchanged
# because no data was rewritten -- this is the no-rewrite benefit in action.
print("After ALTER (metadata only) — clusteringColumns changed, data NOT rewritten:")
describe(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Recluster ALL history to the new keys with `OPTIMIZE … FULL`
# MAGIC `OPTIMIZE … FULL` (**DBR 16.0+**) forces a full recluster so historical data is laid
# MAGIC out to the new keys. Run it after first enabling clustering or after changing keys.
# MAGIC On a big table this can take hours, so schedule it deliberately — not on every run.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Full recluster of ALL data to the new keys (DBR 16.0+).
# MAGIC OPTIMIZE ${catalog}.${schema}.events FULL;

# COMMAND ----------

# MAGIC %md
# MAGIC > **DBR 18.1+ only — partial full recluster of a slice:**
# MAGIC > ```sql
# MAGIC > OPTIMIZE catalog.schema.events FULL WHERE event_date >= '2026-01-15';
# MAGIC > ```

# COMMAND ----------

print("Layout AFTER OPTIMIZE FULL (history reclustered to new keys):")
describe(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Inspect: DESCRIBE DETAIL / HISTORY / SHOW TBLPROPERTIES
# MAGIC The measurement toolkit. `DESCRIBE DETAIL` shows the active `clusteringColumns`;
# MAGIC `DESCRIBE HISTORY` shows the `CLUSTER BY` / `OPTIMIZE` operations and their metrics;
# MAGIC `SHOW TBLPROPERTIES` reveals `clusterByAuto` and the upgraded protocol versions.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE DETAIL ${catalog}.${schema}.events;   -- clusteringColumns = active keys

# COMMAND ----------

# MAGIC %sql
# MAGIC -- See the operations: WRITE, OPTIMIZE (incremental + FULL), CLUSTER BY changes.
# MAGIC DESCRIBE HISTORY ${catalog}.${schema}.events;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- delta.minReaderVersion=3 / minWriterVersion=7 (liquid clustering protocol; no downgrade).
# MAGIC SHOW TBLPROPERTIES ${catalog}.${schema}.events;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · `CLUSTER BY AUTO` — let Databricks choose the keys
# MAGIC `CLUSTER BY AUTO` analyzes query history and picks/maintains keys for you.
# MAGIC **Requires DBR 15.4 LTS+, a UC managed table, and predictive optimization enabled.**
# MAGIC On a workspace without predictive optimization this sets the intent
# MAGIC (`clusterByAuto = true`) but won't actively choose keys until PO is on.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE ${catalog}.${schema}.events CLUSTER BY AUTO;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Look for clusterByAuto = true.
# MAGIC SHOW TBLPROPERTIES ${catalog}.${schema}.events;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · `CLUSTER BY NONE` — stop clustering
# MAGIC Stops clustering future writes. Existing data is left exactly where it is (not rewritten).

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE ${catalog}.${schema}.events CLUSTER BY NONE;

# COMMAND ----------

print("After CLUSTER BY NONE — clusteringColumns cleared, data untouched:")
describe(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Takeaways
# MAGIC - **`CLUSTER BY (cols)`** colocates rows on up to **4 keys** — the modern layout that
# MAGIC   **replaces partitioning AND Z-order** (incompatible with both).
# MAGIC - **Incremental `OPTIMIZE`** reclusters only new/changed data (cheap; run often);
# MAGIC   **`OPTIMIZE … FULL`** (DBR 16.0+) reclusters everything after a key change.
# MAGIC - **Changing keys is metadata-only** — `ALTER TABLE … CLUSTER BY` does **not** rewrite
# MAGIC   existing data; that's the headline benefit partitioning never had.
# MAGIC - **Inspect** with `DESCRIBE DETAIL` (clusteringColumns), `DESCRIBE HISTORY`,
# MAGIC   `SHOW TBLPROPERTIES` (clusterByAuto, protocol v7/v3 — no downgrade).
# MAGIC - **`CLUSTER BY AUTO`** needs DBR 15.4 LTS+, a UC managed table, and predictive
# MAGIC   optimization; **`CLUSTER BY NONE`** stops clustering.
# MAGIC - **GA = DBR 15.4 LTS+**; clustering-on-write only past a size threshold, so run
# MAGIC   `OPTIMIZE` regularly or rely on predictive optimization (Lesson 09).
# MAGIC - **Next:** Lesson 09 — Predictive optimization (auto OPTIMIZE/VACUUM/ANALYZE).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · Cleanup
# MAGIC Drop the demo table so the notebook is rerunnable.

# COMMAND ----------

spark.sql(f"DROP TABLE IF EXISTS {table}")

# Optional: also drop the demo schema (uncomment if you created it solely for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete.")
