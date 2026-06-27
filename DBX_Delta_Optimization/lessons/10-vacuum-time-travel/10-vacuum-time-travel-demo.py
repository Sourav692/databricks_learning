# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 10 — VACUUM, time travel & retention
# MAGIC
# MAGIC **Goal:** Watch a Delta table accumulate **versions** as you write/update/delete,
# MAGIC **time-travel** to past versions (`VERSION AS OF` / `TIMESTAMP AS OF`,
# MAGIC `DESCRIBE HISTORY`, `RESTORE`), set the **two retention dials**, preview cleanup with
# MAGIC `VACUUM … DRY RUN`, then run `VACUUM` and confirm that time travel to a vacuumed
# MAGIC version now **fails**. This is the "manage the file history" capstone before
# MAGIC Lesson 11 (predictive optimization), which runs VACUUM for you.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - **Unity Catalog** enabled; you create a **UC managed** table (no `LOCATION`).
# MAGIC - A **SQL warehouse** or a cluster on **DBR 12.2 LTS+** (this notebook runs on either).
# MAGIC   - `VACUUM … LITE` is **Public Preview, DBR 16.4 LTS+** (a cell below is optional).
# MAGIC   - The DBR 18.0+ rule (`logRetentionDuration ≥ deletedFileRetentionDuration`) is noted inline.
# MAGIC - Privileges: you must be able to **CREATE/MODIFY** the demo table (e.g. own the schema);
# MAGIC   `RESTORE` needs **MODIFY**; `VACUUM` needs delete rights on the table's storage.
# MAGIC - Delta Lake is the **default** format — we never write `USING DELTA`.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. Every modifying op makes a new **version**; read them with `DESCRIBE HISTORY` (and `LIMIT 1`).
# MAGIC 2. Query past versions: `VERSION AS OF`, `TIMESTAMP AS OF`, the `t@v…` shorthand, and PySpark options.
# MAGIC 3. Fix an accidental `DELETE` with **`RESTORE`** and with a surgical `INSERT` from a past version.
# MAGIC 4. The **two retention dials** (`logRetentionDuration` 30d, `deletedFileRetentionDuration` 7d) and why you raise BOTH.
# MAGIC 5. `VACUUM … DRY RUN` then `VACUUM`; the `retentionDurationCheck` safety floor; LITE vs FULL.
# MAGIC 6. Prove that time travel to a **vacuumed** version FAILS — VACUUM is one-way.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Parameters (Unity Catalog 3-level names)
# MAGIC Edit the widgets, then run top-to-bottom. Everything is namespaced `catalog.schema.table`.

# COMMAND ----------

# Widgets let you point this at any catalog/schema you can CREATE in.
dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "delta_opt_demo", "Schema")
dbutils.widgets.text("table", "orders_tt", "Demo table")

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
# MAGIC ## 1 · Create the table (v0) — UC managed, Delta default
# MAGIC No `LOCATION` = **managed**. `CREATE OR REPLACE` makes a clean version 0 so the
# MAGIC notebook is rerunnable.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- v0: create the table. Delta is the default format (no USING DELTA needed).
# MAGIC CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :schema || '.' || :table) (
# MAGIC   order_id   BIGINT,
# MAGIC   customer   STRING,
# MAGIC   amount     DECIMAL(10,2),
# MAGIC   status     STRING
# MAGIC );

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Make several versions (writes / updates / deletes)
# MAGIC Each statement below commits a **new version**, leaving the previous version's files
# MAGIC in storage — that's exactly what time travel reads and VACUUM later reclaims.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- v1: initial load (5 rows)
# MAGIC INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.' || :table) VALUES
# MAGIC   (1, 'acme',     100.00, 'open'),
# MAGIC   (2, 'globex',   250.50, 'open'),
# MAGIC   (3, 'initech',   75.25, 'open'),
# MAGIC   (4, 'umbrella', 500.00, 'open'),
# MAGIC   (5, 'stark',    320.75, 'open');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- v2: append more rows (5 -> 7)
# MAGIC INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.' || :table) VALUES
# MAGIC   (6, 'wayne',  410.00, 'open'),
# MAGIC   (7, 'oscorp', 180.00, 'open');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- v3: UPDATE (a modifying op -> new version; old files become stale)
# MAGIC UPDATE IDENTIFIER(:catalog || '.' || :schema || '.' || :table)
# MAGIC   SET status = 'shipped'
# MAGIC   WHERE amount >= 300;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- v4: the ACCIDENTAL DELETE we will recover from later (removes 'open' rows)
# MAGIC DELETE FROM IDENTIFIER(:catalog || '.' || :schema || '.' || :table)
# MAGIC   WHERE status = 'open';

# COMMAND ----------

# Show the row count now (after the bad delete) so the recovery is visible later.
print("Rows after the accidental DELETE (v4):", spark.table(fqn).count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Inspect history — every op is a version
# MAGIC `DESCRIBE HISTORY` lists versions **newest first**: `version`, `timestamp`,
# MAGIC `operation`, `operationMetrics`, `isolationLevel`, `isBlindAppend`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Full history, newest first.
# MAGIC DESCRIBE HISTORY IDENTIFIER(:catalog || '.' || :schema || '.' || :table);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Just the latest commit (current version number / last operation).
# MAGIC DESCRIBE HISTORY IDENTIFIER(:catalog || '.' || :schema || '.' || :table) LIMIT 1;

# COMMAND ----------

# PySpark equivalent + the session's last commit version (no extra query).
from delta.tables import DeltaTable

hist = DeltaTable.forName(spark, fqn).history()
hist.select("version", "timestamp", "operation", "operationMetrics").show(truncate=False)
print("lastCommitVersionInSession:",
      spark.conf.get("spark.databricks.delta.lastCommitVersionInSession"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Time travel — query past versions
# MAGIC `VERSION AS OF` / `TIMESTAMP AS OF`, the `t@v…` shorthand, and the PySpark options.
# MAGIC The pre-delete version is **v3** (5 + 2 = 7 rows, with statuses updated).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The full pre-delete snapshot (v3): should show 7 rows.
# MAGIC SELECT * FROM IDENTIFIER(:catalog || '.' || :schema || '.' || :table) VERSION AS OF 3
# MAGIC ORDER BY order_id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The @v shorthand reaches the same snapshot (note: @ syntax needs a literal name).
# MAGIC -- Replace the name if you changed the widgets. (@v3 = version 3; @<ts> also works.)
# MAGIC SELECT count(*) AS rows_at_v3 FROM main.delta_opt_demo.orders_tt@v3;

# COMMAND ----------

# PySpark: versionAsOf / timestampAsOf read options.
df_v3 = spark.read.option("versionAsOf", 3).table(fqn)
print("Rows at v3 (PySpark versionAsOf):", df_v3.count())

# timestampAsOf example — resolve the timestamp of v3 from history, then read as of it.
ts_v3 = (DeltaTable.forName(spark, fqn).history()
         .where("version = 3").select("timestamp").first()["timestamp"])
df_ts = spark.read.option("timestampAsOf", str(ts_v3)).table(fqn)
print("Rows as of v3 timestamp", ts_v3, ":", df_ts.count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Recover from the accidental DELETE
# MAGIC Two ways: **`RESTORE`** the whole table to v3, or a **surgical** `INSERT`/`MERGE`
# MAGIC of only the lost rows from the past version. `RESTORE` is data-changing and needs
# MAGIC **MODIFY**; it commits a NEW version (it does not erase v4).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Surgical fix (preferred when newer good data exists): re-insert only the deleted rows
# MAGIC -- from the pre-delete version, skipping any order_id already present now.
# MAGIC INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.' || :table)
# MAGIC SELECT past.*
# MAGIC FROM IDENTIFIER(:catalog || '.' || :schema || '.' || :table) VERSION AS OF 3 AS past
# MAGIC WHERE past.order_id NOT IN (
# MAGIC   SELECT order_id FROM IDENTIFIER(:catalog || '.' || :schema || '.' || :table)
# MAGIC );

# COMMAND ----------

print("Rows after surgical recovery (should be back to 7):", spark.table(fqn).count())

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Whole-table rollback alternative (data-changing; needs MODIFY). Commits a NEW version.
# MAGIC -- Here it makes the table identical to v3 again.
# MAGIC RESTORE TABLE IDENTIFIER(:catalog || '.' || :schema || '.' || :table) TO VERSION AS OF 3;

# COMMAND ----------

# PySpark RESTORE API equivalent (by version or timestamp).
dt = DeltaTable.forName(spark, fqn)
dt.restoreToVersion(3)        # same as the SQL RESTORE above
# dt.restoreToTimestamp(str(ts_v3))   # by timestamp
print("Rows after RESTORE to v3:", spark.table(fqn).count())

# COMMAND ----------

# MAGIC %md
# MAGIC > **Uses, edge cases & limitations — time travel & RESTORE**
# MAGIC > - **Uses:** recover bad deletes/updates, audit, reproduce past reports, snapshot isolation.
# MAGIC > - **Edge cases:** `RESTORE` is a **write** (downstream streams may reprocess); you
# MAGIC >   **cannot** restore to a version whose files were **vacuumed** (see §8).
# MAGIC > - **Limitations:** time travel is **not** a backup — only reaches versions whose
# MAGIC >   **files AND log** still exist (bounded by the two retention dials below).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · The two retention dials — raise BOTH for long time travel
# MAGIC To time-travel N days back you need **both** the log entry (`logRetentionDuration`,
# MAGIC default **30 days**) AND the data files (`deletedFileRetentionDuration`, default
# MAGIC **7 days**). Effective travel ≈ **min** of the two — raising only one doesn't help.
# MAGIC **DBR 18.0+:** `logRetentionDuration` must be **≥** `deletedFileRetentionDuration`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Raise BOTH dials to reliably keep 30 days of time travel (data files AND log).
# MAGIC ALTER TABLE IDENTIFIER(:catalog || '.' || :schema || '.' || :table) SET TBLPROPERTIES (
# MAGIC   'delta.deletedFileRetentionDuration' = 'interval 30 days',  -- keep DATA FILES 30 days (VACUUM threshold)
# MAGIC   'delta.logRetentionDuration'         = 'interval 30 days'   -- keep HISTORY/LOG 30 days (>= data on DBR 18.0+)
# MAGIC );

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Confirm the dials (look for the two retention properties).
# MAGIC SHOW TBLPROPERTIES IDENTIFIER(:catalog || '.' || :schema || '.' || :table);

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · VACUUM — preview with DRY RUN, then reclaim
# MAGIC `VACUUM … DRY RUN` lists the files VACUUM **would** delete and deletes **nothing** —
# MAGIC always preview first. A plain `VACUUM` deletes unreferenced files older than the
# MAGIC retention window. With the 30-day retention we just set, a normal `VACUUM` here
# MAGIC removes nothing yet (all stale files are < 30 days old) — that's expected.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Preview: which files WOULD be deleted? (Deletes nothing.)
# MAGIC VACUUM IDENTIFIER(:catalog || '.' || :schema || '.' || :table) DRY RUN;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Real VACUUM at the table's retention (30 days). Nothing expires yet on a fresh demo.
# MAGIC VACUUM IDENTIFIER(:catalog || '.' || :schema || '.' || :table);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- LITE vs FULL (optional). LITE = Public Preview, DBR 16.4 LTS+ (uses the txn log; faster).
# MAGIC -- FULL is the default (lists all files; cleans aborted-txn leftovers).
# MAGIC -- VACUUM IDENTIFIER(:catalog || '.' || :schema || '.' || :table) LITE;   -- DBR 16.4 LTS+
# MAGIC VACUUM IDENTIFIER(:catalog || '.' || :schema || '.' || :table) FULL DRY RUN;

# COMMAND ----------

# MAGIC %md
# MAGIC > **Uses, edge cases & limitations — VACUUM**
# MAGIC > - **Uses:** cut storage cost; physically purge soft-deleted data (compliance).
# MAGIC > - **Edge cases:** `RETAIN < 7 days` is **blocked** by `retentionDurationCheck`
# MAGIC >   (disabling it can delete files a long-running concurrent job still needs); phase 2
# MAGIC >   deletes from the **driver** (single node) — size it up for huge deletes.
# MAGIC > - **Limitations:** **irreversible**; ignores dirs starting with `_`/`.`
# MAGIC >   (`_delta_log`, `_checkpoints`). `LITE` is Public Preview (DBR 16.4 LTS+).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Prove it: time travel to a VACUUMED version FAILS
# MAGIC To force expiry on this fresh demo we **lower retention to 0 hours** and bypass the
# MAGIC safety floor. **This is for demonstration only** — never disable
# MAGIC `retentionDurationCheck` in production. After this VACUUM, every superseded file is
# MAGIC gone, so reading an old version (e.g. v3) **errors**.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- DEMO ONLY: lower the data-file retention so old files expire immediately.
# MAGIC ALTER TABLE IDENTIFIER(:catalog || '.' || :schema || '.' || :table)
# MAGIC   SET TBLPROPERTIES ('delta.deletedFileRetentionDuration' = 'interval 0 hours');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- DEMO ONLY: bypass the 7-day safety floor, VACUUM RETAIN 0, then re-enable the guard.
# MAGIC SET spark.databricks.delta.retentionDurationCheck.enabled = false;

# COMMAND ----------

# MAGIC %sql
# MAGIC VACUUM IDENTIFIER(:catalog || '.' || :schema || '.' || :table) RETAIN 0 HOURS;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Re-enable the safety check immediately (production hygiene).
# MAGIC SET spark.databricks.delta.retentionDurationCheck.enabled = true;

# COMMAND ----------

# Now time travel to a vacuumed version FAILS — the data files are gone.
# We catch the error so the notebook keeps running and the lesson is explicit.
try:
    n = spark.read.option("versionAsOf", 3).table(fqn).count()
    print("Unexpected: v3 still readable with", n, "rows (files may not have expired on this runtime).")
except Exception as e:
    print("EXPECTED FAILURE — time travel to vacuumed v3 errored:")
    print(str(e).splitlines()[0])

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The current version still reads fine (its files are live); only OLD versions are lost.
# MAGIC SELECT count(*) AS current_rows
# MAGIC FROM IDENTIFIER(:catalog || '.' || :schema || '.' || :table);

# COMMAND ----------

# MAGIC %md
# MAGIC > **The capstone takeaway.** VACUUM is **one-way**. It reclaims storage by deleting the
# MAGIC > very files time travel depends on, so set retention **before** you need a long
# MAGIC > window. In Lesson 11, **predictive optimization** runs VACUUM for you on UC managed
# MAGIC > tables — raise `delta.deletedFileRetentionDuration` **before** enabling it if you
# MAGIC > need longer time travel.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · Cleanup (rerunnable)
# MAGIC Drops the demo table so the notebook can be run again from a clean state.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP TABLE IF EXISTS IDENTIFIER(:catalog || '.' || :schema || '.' || :table);
