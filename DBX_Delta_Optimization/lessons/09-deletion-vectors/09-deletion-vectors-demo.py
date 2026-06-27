# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 09 — Deletion vectors
# MAGIC
# MAGIC **Goal:** *See* merge-on-read work — create a table, enable
# MAGIC `delta.enableDeletionVectors`, run a few `DELETE`/`UPDATE` operations, and prove via
# MAGIC `DESCRIBE HISTORY` (small `numRemovedFiles` / no full rewrite) and `DESCRIBE DETAIL`
# MAGIC that the big Parquet files were **not** rewritten. Then physically apply the soft
# MAGIC deletes with `REORG TABLE … APPLY (PURGE)` and note `VACUUM` to remove old files.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - **DBR 14.3 LTS+** to **write** with all deletion-vector optimizations; **DBR 12.2
# MAGIC   LTS+** to **read** a DV-enabled table.
# MAGIC   - Row-level concurrency with DVs: DBR 14.2+.
# MAGIC   - Non-Photon write floors: `DELETE` 12.2 LTS+, `UPDATE` 14.1+, `MERGE` 14.3 LTS+.
# MAGIC     On **Photon** all three are supported from 12.2 LTS+.
# MAGIC   - `DROP FEATURE deletionVectors` (protocol downgrade): DBR 14.1+.
# MAGIC - **Unity Catalog** enabled, with `USE CATALOG`/`USE SCHEMA`, `CREATE SCHEMA`, and
# MAGIC   `CREATE TABLE` grants on the target catalog (managed tables recommended).
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. Default Delta is **copy-on-write**: deleting a row rewrites the WHOLE Parquet file.
# MAGIC 2. `delta.enableDeletionVectors = true` switches to **merge-on-read**: `DELETE`/`UPDATE`/
# MAGIC    `MERGE` mark rows as soft-deleted in a tiny side-file — no full-file rewrite.
# MAGIC 3. Prove it with `DESCRIBE HISTORY` operationMetrics (`numRemovedFiles`,
# MAGIC    `numDeletionVectorsAdded`) and `DESCRIBE DETAIL` (numFiles unchanged).
# MAGIC 4. Soft-deletes are physically applied by `OPTIMIZE` / `REORG TABLE … APPLY (PURGE)`;
# MAGIC    then `VACUUM` removes the now-unreferenced old files.
# MAGIC 5. Enabling DVs **upgrades the protocol** (writer v7 / reader v3); `DROP FEATURE` to
# MAGIC    downgrade. **Liquid clustering enables DVs by default** (Lesson 08).

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
table   = f"{catalog}.{schema}.customers"   # fully-qualified UC name (managed Delta table)

# Create the schema if needed and set it as the working namespace.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Working in {catalog}.{schema}; demo table = {table}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Measurement helpers
# MAGIC `DESCRIBE DETAIL` gives `numFiles` / `sizeInBytes`. `DESCRIBE HISTORY` exposes
# MAGIC `operationMetrics` — for a `DELETE` we watch `numRemovedFiles` (files rewritten) and
# MAGIC `numDeletionVectorsAdded` (soft deletes). With deletion vectors ON, `numRemovedFiles`
# MAGIC stays small (often 0) while a DV is added — the proof of merge-on-read.

# COMMAND ----------

def detail(table_name):
    """Print numFiles / sizeInBytes via DESCRIBE DETAIL."""
    d = (spark.sql(f"DESCRIBE DETAIL {table_name}")
              .select("numFiles", "sizeInBytes").first())
    n, b = d["numFiles"], d["sizeInBytes"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    print(f"{table_name}: numFiles={n}, sizeInBytes={b:,} (~{avg_mb:.2f} MB/file)")
    return n, b

def last_op_metrics(table_name):
    """Show the most recent operation + key metrics from DESCRIBE HISTORY."""
    h = (spark.sql(f"DESCRIBE HISTORY {table_name}")
              .select("version", "operation", "operationMetrics")
              .orderBy("version", ascending=False).first())
    m = h["operationMetrics"] or {}
    keys = ["numRemovedFiles", "numAddedFiles", "numDeletionVectorsAdded",
            "numDeletionVectorsRemoved", "numTargetRowsDeleted", "numCopiedRows"]
    picked = {k: m.get(k) for k in keys if k in m}
    print(f"v{h['version']} {h['operation']} -> {picked}")
    return h["operation"], picked

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Create a table and load data (no deletion vectors yet)
# MAGIC We start in the **default copy-on-write** world so the contrast is visible. We force a
# MAGIC few separate files (one per write) so a later `DELETE` clearly targets specific files.
# MAGIC Delta is the default format, so we do **not** write `USING DELTA`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Plain Delta table; deletion vectors NOT enabled yet (copy-on-write baseline).
# MAGIC CREATE OR REPLACE TABLE ${catalog}.${schema}.customers (
# MAGIC   customer_id BIGINT,
# MAGIC   email       STRING,
# MAGIC   region      STRING,
# MAGIC   signup_date DATE
# MAGIC );

# COMMAND ----------

from pyspark.sql import functions as F

# Write 4 separate batches so we get ~4 files. Each batch is its own commit/file,
# which makes the copy-on-write rewrite obvious when we delete from one of them.
for batch in range(4):
    (spark.range(batch * 250_000, (batch + 1) * 250_000)
        .withColumn("customer_id", F.col("id"))
        .withColumn("email",       F.concat(F.lit("user"), F.col("id"), F.lit("@example.com")))
        .withColumn("region",      F.element_at(
            F.array(F.lit("US"), F.lit("EU"), F.lit("APAC"), F.lit("LATAM")),
            (F.col("id") % 4 + 1).cast("int")))
        .withColumn("signup_date", F.expr("date_add('2026-01-01', cast(id % 60 as int))"))
        .drop("id")
        .write.mode("append").saveAsTable(table))

print("Loaded 1,000,000 rows across ~4 files. Layout:")
detail(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Baseline: a DELETE under copy-on-write rewrites a whole file
# MAGIC With deletion vectors **off**, deleting even one row forces Delta to read the entire
# MAGIC file holding it and write a new file without that row. Watch `numRemovedFiles` > 0 and
# MAGIC `numCopiedRows` (rows copied into the rewritten file) in the history metrics.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- One row -> the WHOLE file containing it is rewritten (copy-on-write).
# MAGIC DELETE FROM ${catalog}.${schema}.customers WHERE customer_id = 42;

# COMMAND ----------

# Copy-on-write fingerprint: numRemovedFiles >= 1 (a file was rewritten),
# numCopiedRows > 0 (surviving rows copied into the new file), no DV added.
print("Copy-on-write DELETE metrics:")
last_op_metrics(table)
detail(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Enable deletion vectors (switch to merge-on-read)
# MAGIC Set `delta.enableDeletionVectors = true`. This is the opt-in for Delta tables (Apache
# MAGIC Iceberg v3 tables get DVs by default). It **upgrades the table protocol** (writer v7 /
# MAGIC reader v3), so clients below DBR 12.2 LTS can no longer read the table.
# MAGIC
# MAGIC > **Cannot** run this `ALTER` on a **materialized view** or **streaming table**.
# MAGIC > **Liquid clustering** (Lesson 08) enables deletion vectors by default via the same protocol.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Opt in to merge-on-read. (At creation you'd use TBLPROPERTIES in CREATE TABLE.)
# MAGIC ALTER TABLE ${catalog}.${schema}.customers
# MAGIC   SET TBLPROPERTIES ('delta.enableDeletionVectors' = true);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Confirm the property and the upgraded protocol (delta.minReaderVersion=3 / minWriterVersion=7).
# MAGIC SHOW TBLPROPERTIES ${catalog}.${schema}.customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Merge-on-read: DELETE / UPDATE now write a tiny vector, not a rewrite
# MAGIC Re-run a `DELETE` and an `UPDATE`. The proof is in the metrics: `numRemovedFiles` stays
# MAGIC small (often **0**) and `numDeletionVectorsAdded` appears — the big files are untouched.
# MAGIC `DESCRIBE DETAIL` `numFiles` should not jump the way it would under copy-on-write.

# COMMAND ----------

n_before, _ = detail(table)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Soft delete: writes a deletion vector; the Parquet file is NOT rewritten.
# MAGIC DELETE FROM ${catalog}.${schema}.customers WHERE customer_id IN (88, 130, 275, 4001);

# COMMAND ----------

# Merge-on-read fingerprint: numRemovedFiles small/0, numDeletionVectorsAdded >= 1.
print("Merge-on-read DELETE metrics:")
last_op_metrics(table)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- UPDATE behaves the same way: soft-delete old rows + write new values, no full rewrite.
# MAGIC UPDATE ${catalog}.${schema}.customers SET region = 'EU' WHERE customer_id BETWEEN 500 AND 800;

# COMMAND ----------

print("Merge-on-read UPDATE metrics:")
last_op_metrics(table)
n_after, _ = detail(table)
print(f"\nnumFiles before DV deletes={n_before}, after={n_after} "
      f"(no full-table rewrite — that's merge-on-read).")

# COMMAND ----------

# MAGIC %md
# MAGIC ### PySpark / DeltaTable API equivalent — for reference
# MAGIC The same soft-delete / soft-update via the API. (We keep the SQL path above as the working one.)

# COMMAND ----------

# from delta.tables import DeltaTable
# dt = DeltaTable.forName(spark, table)
# dt.delete("customer_id = 999")                       # writes a deletion vector
# dt.update("customer_id = 1234", {"region": "'EU'"})  # soft-delete old + write new value
print("Reads automatically apply the deletion vectors — deleted rows are gone:")
print("rows where customer_id IN (88, 130, 275, 4001):",
      spark.sql(f"SELECT count(*) c FROM {table} WHERE customer_id IN (88,130,275,4001)").first()["c"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Physically apply the soft-deletes: `REORG TABLE … APPLY (PURGE)`
# MAGIC Soft-deletes only *hide* rows; the dead rows still live inside the Parquet files. To
# MAGIC fold them in (rewrite files without the dead rows), run `REORG … APPLY (PURGE)`
# MAGIC (`OPTIMIZE` / auto compaction also apply DVs as they rewrite files).
# MAGIC
# MAGIC > On a large table, set `spark.databricks.delta.reorg.purgeMode = rows` so the purge
# MAGIC > only scans files that actually have soft-deletes (default `all` scans every footer).

# COMMAND ----------

# Faster purge on large tables: only touch files that carry soft-deletes (vs default 'all').
spark.conf.set("spark.databricks.delta.reorg.purgeMode", "rows")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Rewrite all files that have DV-recorded changes, removing the dead rows for real.
# MAGIC REORG TABLE ${catalog}.${schema}.customers APPLY (PURGE);

# COMMAND ----------

# After PURGE: deletion vectors are consumed (numDeletionVectorsRemoved) and files rewritten.
print("REORG ... APPLY (PURGE) metrics:")
last_op_metrics(table)
detail(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Then physically remove old files: `VACUUM` (full coverage in Lesson 10)
# MAGIC `REORG`/`OPTIMIZE` only **replace** files — the old files still sit in storage until
# MAGIC `VACUUM` deletes them, and only after they pass the retention window
# MAGIC (`delta.deletedFileRetentionDuration`, default **7 days**). This is the step that
# MAGIC actually reclaims space and makes a GDPR delete physical.
# MAGIC
# MAGIC > `DRY RUN` previews what *would* be deleted without deleting anything. Recently
# MAGIC > rewritten files are still inside the 7-day window, so a normal `VACUUM` here will
# MAGIC > typically remove nothing yet — that's expected and safe.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Preview only: lists files older than the retention threshold; deletes nothing.
# MAGIC VACUUM ${catalog}.${schema}.customers DRY RUN;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Real VACUUM (removes files past the default 7-day retention). Safe to run; see Lesson 10.
# MAGIC VACUUM ${catalog}.${schema}.customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Inspect history & (optional) downgrade the protocol
# MAGIC `DESCRIBE HISTORY` shows the full story: the copy-on-write `DELETE`, the merge-on-read
# MAGIC `DELETE`/`UPDATE`, the `REORG`, and `VACUUM`. To restore readability for old clients you
# MAGIC can drop the feature (DBR 14.1+) — a deliberate, rarely-needed step.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Compare operationMetrics across versions: copy-on-write (numRemovedFiles>0) vs
# MAGIC -- merge-on-read (numDeletionVectorsAdded>0, numRemovedFiles small/0).
# MAGIC DESCRIBE HISTORY ${catalog}.${schema}.customers;

# COMMAND ----------

# MAGIC %md
# MAGIC > **Optional — downgrade (DBR 14.1+).** Drops the deletion-vectors feature so older
# MAGIC > clients can read again. Not allowed on materialized views / streaming tables.
# MAGIC > ```sql
# MAGIC > ALTER TABLE catalog.schema.customers DROP FEATURE deletionVectors;
# MAGIC > ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## Takeaways
# MAGIC - **Default = copy-on-write:** deleting a row rewrites the WHOLE file
# MAGIC   (`numRemovedFiles` > 0, `numCopiedRows` > 0) — cost scales with file size, not rows.
# MAGIC - **`delta.enableDeletionVectors = true` = merge-on-read:** `DELETE`/`UPDATE`/`MERGE`
# MAGIC   mark rows in a tiny vector (`numDeletionVectorsAdded`), big files untouched
# MAGIC   (`numRemovedFiles` small/0). Cheap write, small read-time DV-apply cost.
# MAGIC - **Soft delete ≠ physical delete:** rows stay in files until `OPTIMIZE` /
# MAGIC   `REORG TABLE … APPLY (PURGE)` rewrites them; then **`VACUUM`** removes old files
# MAGIC   after retention (Lesson 10). Use `purgeMode = rows` to purge large tables faster.
# MAGIC - **Enabling upgrades the protocol** (writer v7 / reader v3) — read floor DBR 12.2 LTS+;
# MAGIC   `DROP FEATURE deletionVectors` (DBR 14.1+) downgrades normal tables.
# MAGIC - **Liquid clustering enables DVs by default** (Lesson 08); **predictive optimization**
# MAGIC   (Lesson 11) applies them automatically on UC managed tables.
# MAGIC - **Next:** Lesson 10 — VACUUM, time travel & retention (the physical-cleanup half).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Cleanup
# MAGIC Drop the demo table so the notebook is rerunnable.

# COMMAND ----------

spark.sql(f"DROP TABLE IF EXISTS {table}")

# Optional: also drop the demo schema (uncomment if you created it solely for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete.")
