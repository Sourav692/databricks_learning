# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 06 — Auto compaction
# MAGIC
# MAGIC **Goal:** *See* auto compaction work — write a table in many tiny batches to create a
# MAGIC small-file problem, turn on `delta.autoOptimize.autoCompact`, and watch the
# MAGIC **post-commit, synchronous** compaction merge those small files into fewer, right-sized
# MAGIC ones. We measure every step with `DESCRIBE DETAIL` (numFiles / sizeInBytes) and
# MAGIC `DESCRIBE HISTORY` (the auto OPTIMIZE / compaction entries).
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Any current Databricks Runtime (DBR 13.3 LTS+ recommended); serverless is fine.
# MAGIC   - The `auto` value for `autoCompact` autotunes the target size; `true`/`legacy`
# MAGIC     pin a fixed 128 MB target.
# MAGIC - **Unity Catalog** enabled, with `USE CATALOG` / `USE SCHEMA`, `CREATE SCHEMA`,
# MAGIC   and `CREATE TABLE` grants on the target catalog.
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. How small batched writes create a small-file problem (the "before").
# MAGIC 2. Auto compaction runs **synchronously on the write cluster, post-commit** — it
# MAGIC    merges small files *within partitions* after a write succeeds.
# MAGIC 3. The values `auto` (recommended) vs `true` / `legacy` (fixed 128 MB) vs `false`,
# MAGIC    and the table property vs session config control surfaces.
# MAGIC 4. The `minNumFiles` / `maxFileSize` tuning knobs.
# MAGIC 5. Auto compaction + optimized writes are **always on for MERGE/UPDATE/DELETE**.
# MAGIC 6. It does NOT replace `OPTIMIZE` on big tables; how to migrate off the legacy config.

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

# Create the schema if needed and set it as the working namespace.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
print(f"Working in {catalog}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Measurement helpers
# MAGIC `DESCRIBE DETAIL` gives `numFiles` / `sizeInBytes`. `DESCRIBE HISTORY` shows the
# MAGIC operations Delta ran — including the **auto OPTIMIZE / compaction** entries that
# MAGIC auto compaction adds after a write. We reuse these to make the numbers move.

# COMMAND ----------

from pyspark.sql import functions as F

def file_stats(table):
    """Print and return (numFiles, sizeInBytes) via DESCRIBE DETAIL."""
    d = (spark.sql(f"DESCRIBE DETAIL {table}")
              .select("numFiles", "sizeInBytes").first())
    n, b = d["numFiles"], d["sizeInBytes"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    print(f"{table}: numFiles={n}, sizeInBytes={b:,} (~{avg_mb:.2f} MB/file)")
    return n, b

def show_compaction_history(table):
    """Print the recent history rows, flagging auto OPTIMIZE / compaction commits.

    Auto compaction commits an OPTIMIZE-style operation AFTER the write. The
    operationParameters often carry an 'auto' = true marker; the operation is
    typically reported as OPTIMIZE. We surface version / operation / metrics.
    """
    hist = (spark.sql(f"DESCRIBE HISTORY {table}")
                 .select("version", "operation", "operationParameters", "operationMetrics")
                 .orderBy(F.col("version").desc()))
    rows = hist.limit(12).collect()
    print(f"--- history for {table} (latest first) ---")
    for r in rows:
        params = r["operationParameters"] or {}
        is_auto = str(params.get("auto", "")).lower() == "true"
        flag = "  <-- AUTO COMPACTION" if (r["operation"] == "OPTIMIZE" and is_auto) else ""
        print(f"  v{r['version']:>3}  {r['operation']:<12}{flag}")
    return hist

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE + STRESS (auto compaction OFF) — make the small-file problem
# MAGIC We write a table in **many tiny batches** with auto compaction explicitly **off**, so
# MAGIC each small append stays as small files and they pile up. This is the "before".

# COMMAND ----------

baseline = f"{catalog}.{schema}.events_no_autocompact"
spark.sql(f"DROP TABLE IF EXISTS {baseline}")

# Make sure auto compaction is OFF for this baseline session so files stay small.
spark.conf.set("spark.databricks.delta.autoCompact.enabled", "false")
spark.conf.set("spark.databricks.delta.optimizeWrite.enabled", "false")  # isolate the small-file effect

def make_batch(batch_id, rows=20_000):
    """A small synthetic batch of events in one partition (event_date)."""
    return (spark.range(0, rows)
        .withColumn("event_id", F.col("id") + F.lit(batch_id * 1_000_000))
        .withColumn("event_type", F.element_at(
            F.array(*[F.lit(t) for t in ["click", "view", "purchase", "scroll"]]),
            (F.col("id") % 4 + 1).cast("int")))
        .withColumn("amount", (F.rand(seed=batch_id) * 500).cast("double"))
        .withColumn("event_date", F.lit("2026-06-01").cast("date"))   # single partition
        .drop("id"))

# Append 12 tiny batches -> ~12+ small files accumulate (no compaction).
for b in range(12):
    (make_batch(b).write
        .mode("overwrite" if b == 0 else "append")
        .partitionBy("event_date")
        .saveAsTable(baseline))

n_before, _ = file_stats(baseline)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · MEASURE the "before" — many small files, no compaction entries
# MAGIC `DESCRIBE DETAIL` shows a high `numFiles` with a small average size. `DESCRIBE HISTORY`
# MAGIC shows only WRITE operations — there are **no auto OPTIMIZE / compaction** commits.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE DETAIL events_no_autocompact;

# COMMAND ----------

_ = show_compaction_history(baseline)   # expect only WRITE rows, no AUTO COMPACTION flag

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · APPLY — turn ON auto compaction and write the same way
# MAGIC Now create a second table with `delta.autoOptimize.autoCompact = 'auto'` and append the
# MAGIC same 12 tiny batches. After (some of) the writes commit, auto compaction runs
# MAGIC **synchronously on this cluster, post-commit**, merging the new small files within the
# MAGIC partition. We expect a **lower** `numFiles` and **OPTIMIZE (auto)** entries in history.

# COMMAND ----------

# MAGIC %md
# MAGIC ### The four values — `auto` / `true` / `legacy` / `false`
# MAGIC - `auto`   — recommended: on, **autotunes** the target file size.
# MAGIC - `true`   — on, **fixed 128 MB** target, no dynamic sizing.
# MAGIC - `legacy` — **alias for `true`** (same fixed 128 MB behavior).
# MAGIC - `false`  — off.

# COMMAND ----------

compacted = f"{catalog}.{schema}.events_autocompact"
spark.sql(f"DROP TABLE IF EXISTS {compacted}")

# Create the table first, then set the TABLE PROPERTY so every writer compacts.
spark.sql(f"""
  CREATE TABLE {compacted} (
    event_id   BIGINT,
    event_type STRING,
    amount     DOUBLE,
    event_date DATE
  )
  PARTITIONED BY (event_date)
""")

# 'auto' = on + autotuned target size (recommended). This is the table-level switch.
spark.sql(f"""
  ALTER TABLE {compacted}
  SET TBLPROPERTIES ('delta.autoOptimize.autoCompact' = 'auto')
""")

# Tune the trigger: only compact once enough small files exist (avoid firing on tiny writes).
# These are SESSION configs (cluster/session scope) -- the value vocabulary mirrors the property.
spark.conf.set("spark.databricks.delta.autoCompact.minNumFiles", "8")   # min small files to trigger
# (Optional) steer the compacted output size; auto autotunes, this overrides the target if set.
# spark.conf.set("spark.databricks.delta.autoCompact.maxFileSize", str(128 * 1024 * 1024))

# Append the same 12 tiny batches into the compaction-enabled table.
for b in range(12):
    (make_batch(b)
        .select("event_id", "event_type", "amount", "event_date")
        .write.mode("append").saveAsTable(compacted))

n_after, _ = file_stats(compacted)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · MEASURE the "after" — fewer files + AUTO COMPACTION entries in history
# MAGIC `DESCRIBE DETAIL` should report a **lower `numFiles`** (larger average size). The key
# MAGIC evidence is in `DESCRIBE HISTORY`: post-write **OPTIMIZE** commits with the `auto`
# MAGIC marker — these are auto compaction running synchronously after the writes.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE DETAIL events_autocompact;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Look for OPTIMIZE rows interleaved with the WRITEs (operationParameters.auto = true).
# MAGIC DESCRIBE HISTORY events_autocompact;

# COMMAND ----------

_ = show_compaction_history(compacted)   # expect OPTIMIZE rows flagged AUTO COMPACTION

# COMMAND ----------

# MAGIC %md
# MAGIC ### Side-by-side — the numbers that matter
# MAGIC Same data, same batch pattern; the only difference is auto compaction.

# COMMAND ----------

import pandas as pd

rows = []
for name in [baseline, compacted]:
    n, b = file_stats(name)
    rows.append((name.split(".")[-1], n, round(b / (1024 * 1024), 1),
                 round((b / n) / (1024 * 1024), 2) if n else 0))

summary = pd.DataFrame(rows, columns=["table", "numFiles", "sizeMB", "avgFileMB"])
display(spark.createDataFrame(summary))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Uses, edge cases & limitations — auto compaction
# MAGIC - **Uses:** streaming / micro-batch ingestion and MERGE-heavy pipelines that commit
# MAGIC   small files frequently; pair with optimized writes for both pre- and post-write hygiene.
# MAGIC - **Edge cases:** latency-critical micro-batches (it's *synchronous* — adds write-tail
# MAGIC   latency); `minNumFiles` too low (fires constantly) or too high (small files linger);
# MAGIC   mixing the table property and session config (confusing behavior).
# MAGIC - **Limitations:** a *local, per-write* sweep — it does NOT replace `OPTIMIZE` on
# MAGIC   tables > 1 TB and doesn't cluster by query keys; only compacts files NOT previously
# MAGIC   compacted; `legacy` is just an alias for `true` (fixed 128 MB).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Always on for MERGE / UPDATE / DELETE (can't be disabled)
# MAGIC Auto compaction **and** optimized writes are forced on for `MERGE`/`UPDATE`/`DELETE`,
# MAGIC even when the session/table would otherwise have them off. These ops rewrite affected
# MAGIC files (prime small-file producers), so Delta sweeps up automatically. Below we MERGE
# MAGIC into the BASELINE table (which had autoCompact OFF) to prove the guardrail.

# COMMAND ----------

# Build a small set of updates + new rows to merge in.
updates = (make_batch(99, rows=10_000)
    .select("event_id", "event_type", "amount", "event_date")
    .withColumn("amount", F.col("amount") + F.lit(1000.0)))   # bump amount to show an UPDATE
updates.createOrReplaceTempView("staged_updates")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- MERGE rewrites affected files; auto compaction + optimized writes are ALWAYS ON here
# MAGIC -- (can't be disabled) even though events_no_autocompact had autoCompact = false.
# MAGIC MERGE INTO events_no_autocompact AS t
# MAGIC USING staged_updates AS s
# MAGIC   ON t.event_id = s.event_id
# MAGIC WHEN MATCHED THEN UPDATE SET *
# MAGIC WHEN NOT MATCHED THEN INSERT *;

# COMMAND ----------

# The MERGE commit should be followed by an OPTIMIZE (auto) entry despite autoCompact=false.
_ = show_compaction_history(baseline)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Auto compaction does NOT replace OPTIMIZE on big tables
# MAGIC Auto compaction is a per-write, local sweep. For large tables (> 1 TB) you still
# MAGIC schedule a full `OPTIMIZE` (or let predictive optimization run it) to further
# MAGIC consolidate, and use liquid clustering for query-key skipping. Here is the explicit
# MAGIC command you would still schedule.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Still run OPTIMIZE periodically on large tables; auto compaction complements it.
# MAGIC OPTIMIZE events_autocompact;
# MAGIC DESCRIBE DETAIL events_autocompact;   -- confirm numFiles after a full compaction pass

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Migrating off the legacy config
# MAGIC To move from the older fixed (`legacy`/`true`) behavior to the platform default,
# MAGIC **clear the session config** and **unset the table property**.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- (1) clear the session config, (2) unset the table property so it uses the default.
# MAGIC SET spark.databricks.delta.autoCompact.enabled = false;
# MAGIC ALTER TABLE events_autocompact
# MAGIC   UNSET TBLPROPERTIES (delta.autoOptimize.autoCompact);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Confirm the property is gone (delta.autoOptimize.autoCompact should not be listed).
# MAGIC SHOW TBLPROPERTIES events_autocompact;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Takeaways
# MAGIC - **Auto compaction merges small files WITHIN partitions AFTER a write succeeds**,
# MAGIC   **synchronously on the write cluster, post-commit** — no schedule needed.
# MAGIC - It only compacts **files not previously compacted**, so it's incremental and cheap
# MAGIC   to leave on; the evidence is the **OPTIMIZE (auto)** entries in `DESCRIBE HISTORY`.
# MAGIC - Values: **`auto`** (recommended, autotuned target) · `true`/`legacy` (fixed 128 MB) ·
# MAGIC   `false` (off). Control via the **table property** or the **session config**.
# MAGIC - Tune with **`minNumFiles`** (min small files to trigger) and **`maxFileSize`** (target).
# MAGIC - **Always on for MERGE/UPDATE/DELETE** (can't disable). Independent of predictive
# MAGIC   optimization (write-cluster/synchronous vs serverless/asynchronous).
# MAGIC - It does **not replace `OPTIMIZE`** on > 1 TB tables; measure with `DESCRIBE DETAIL`
# MAGIC   + `DESCRIBE HISTORY`.
# MAGIC - **Previous:** Lesson 05 — Optimized writes (pre-commit sizing). **Next:** Lesson 07 —
# MAGIC   Auto optimize & file-size autotuning.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Cleanup
# MAGIC Drop the demo tables so the notebook is rerunnable.

# COMMAND ----------

for name in ["events_no_autocompact", "events_autocompact"]:
    spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.{name}")

# Optional: also drop the demo schema (uncomment if you created it solely for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete.")
