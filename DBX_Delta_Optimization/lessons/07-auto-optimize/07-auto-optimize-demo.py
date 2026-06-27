# Databricks notebook source
# MAGIC %md
# MAGIC # Lesson 07 — Auto optimize (umbrella) & file-size autotuning
# MAGIC
# MAGIC **Goal:** Tie together the two "auto optimize" settings — `optimizeWrite`
# MAGIC (write-time, Lesson 05) + `autoCompact` (post-write, Lesson 06) — and *see* how
# MAGIC Databricks chooses a **target file size**: either an explicit `delta.targetFileSize`
# MAGIC or, when unset, **autotuning by table size** (256 MB → 1 GB). We SET the properties,
# MAGIC then MEASURE / INSPECT them with `DESCRIBE DETAIL`, `DESCRIBE EXTENDED`, and
# MAGIC `SHOW TBLPROPERTIES`.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Any current Databricks Runtime; **DBR 11.3 LTS+** (or a SQL warehouse) for
# MAGIC   automatic file-size tuning on UC managed tables and the `'auto'` autoCompact mode.
# MAGIC - **Unity Catalog** enabled, with `USE CATALOG` / `USE SCHEMA`, `CREATE SCHEMA`,
# MAGIC   and `CREATE TABLE` grants on the target catalog. Prefer **UC managed** tables.
# MAGIC - Delta Lake is the **default** table format — we never write `USING DELTA`.
# MAGIC - No external data needed: we generate synthetic rows.
# MAGIC
# MAGIC **What you'll learn**
# MAGIC 1. "Auto optimize" is **two settings** — `optimizeWrite` + `autoCompact` — and how
# MAGIC    to enable both (table property AND session config) and confirm them.
# MAGIC 2. `delta.targetFileSize` (explicit `'128mb'` or bytes) vs the **default = None**
# MAGIC    autotuning by table size (< 2.56 TB → 256 MB; 2.56–10 TB → ramp; > 10 TB → 1 GB).
# MAGIC 3. Inspect file sizing with `DESCRIBE DETAIL`, `DESCRIBE EXTENDED`, `SHOW TBLPROPERTIES`.
# MAGIC 4. Why auto optimize **reduces but doesn't replace** `OPTIMIZE` — and the > 1 TB rule.
# MAGIC 5. The autotune caveat: a growing target does NOT re-optimize existing files.

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
# MAGIC `DESCRIBE DETAIL` gives `numFiles` / `sizeInBytes` (so we can compute avg file size).
# MAGIC `SHOW TBLPROPERTIES` and `DESCRIBE EXTENDED` show which auto-optimize / targetFileSize
# MAGIC properties are in effect. We reuse these to make the numbers visible.

# COMMAND ----------

def file_stats(table):
    """Print and return (numFiles, sizeInBytes, avg_MB) via DESCRIBE DETAIL."""
    d = (spark.sql(f"DESCRIBE DETAIL {table}")
              .select("numFiles", "sizeInBytes").first())
    n, b = d["numFiles"], d["sizeInBytes"]
    avg_mb = (b / n) / (1024 * 1024) if n else 0
    print(f"{table}: numFiles={n}, sizeInBytes={b:,} (~{avg_mb:.2f} MB/file)")
    return n, b, avg_mb

def show_props(table, only=("autoOptimize", "targetFileSize", "tuneFileSizes")):
    """Print the auto-optimize / file-size table properties currently set."""
    props = spark.sql(f"SHOW TBLPROPERTIES {table}").collect()
    hits = [(r["key"], r["value"]) for r in props if any(o.lower() in r["key"].lower() for o in only)]
    print(f"{table} file-size properties:")
    if hits:
        for k, v in hits:
            print(f"  {k} = {v}")
    else:
        print("  (none set — file size is autotuned by table size)")
    return hits

def autotune_target_mb(size_gb):
    """The documented autotune rule: <2.56 TB -> 256 MB; 2.56-10 TB ramps; >10 TB -> 1 GB.
    Pure illustration of the rule for a hypothetical table size (no rewrite happens)."""
    T1, T2 = 2560, 10000   # 2.56 TB, 10 TB in GB
    if size_gb < T1:
        return 256
    if size_gb > T2:
        return 1024
    return 256 + (size_gb - T1) / (T2 - T1) * (1024 - 256)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · CREATE a UC managed table (autotuning is the DEFAULT)
# MAGIC We create a small managed events table. We set **nothing** about file size yet, so
# MAGIC the target is **autotuned by table size** (this small table → 256 MB target band).
# MAGIC Note we do NOT write `USING DELTA` — Delta is the default.

# COMMAND ----------

from pyspark.sql import functions as F

ROWS = 2_000_000  # small enough to run fast; produces several files when capped per file

events = (spark.range(0, ROWS)
    .withColumn("event_type", F.element_at(
        F.array(*[F.lit(t) for t in ["click", "view", "purchase", "signup", "error"]]),
        (F.col("id") % 5 + 1).cast("int")))
    .withColumn("user_id", (F.col("id") % 100000).cast("long"))
    .withColumn("amount", (F.rand(seed=7) * 500).cast("double"))
    .withColumn("event_date", F.expr("date_add(DATE'2026-06-01', cast(id % 30 as int))")))

# Cap rows/file to deliberately create SEVERAL small files (the condition auto optimize fixes).
(events.write
   .option("maxRecordsPerFile", 100_000)   # ~20 files for 2M rows
   .mode("overwrite")
   .saveAsTable(f"{catalog}.{schema}.events"))

file_stats(f"{catalog}.{schema}.events")
show_props(f"{catalog}.{schema}.events")   # expect: none set -> autotuned

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Enable BOTH halves of "auto optimize" (the umbrella)
# MAGIC `optimizeWrite` (write-time) + `autoCompact` (post-write) are independent settings;
# MAGIC "auto optimize" is the umbrella name for the pair. We set the **table properties**
# MAGIC (persist on the table) — and below also show the **session configs**.
# MAGIC
# MAGIC > `autoCompact = 'auto'` autotunes the compaction target; `'true'` pins a 128 MB
# MAGIC > target with no dynamic sizing; `'false'` is off.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Enable BOTH halves of auto optimize on the table (persisted as TBLPROPERTIES).
# MAGIC ALTER TABLE events
# MAGIC   SET TBLPROPERTIES (
# MAGIC     'delta.autoOptimize.optimizeWrite' = 'true',   -- write-time: fewer, larger files (Lesson 05)
# MAGIC     'delta.autoOptimize.autoCompact'   = 'auto'    -- post-write: compact small files; 'auto' autotunes
# MAGIC   );

# COMMAND ----------

# Session-level equivalents (apply to writes from THIS Spark session, any table).
# Use these when you can't / don't want to set per-table properties.
spark.conf.set("spark.databricks.delta.optimizeWrite.enabled", "true")   # write-time half
spark.conf.set("spark.databricks.delta.autoCompact.enabled",  "auto")    # post-write half ('auto' autotunes)
print("optimizeWrite.enabled =", spark.conf.get("spark.databricks.delta.optimizeWrite.enabled"))
print("autoCompact.enabled   =", spark.conf.get("spark.databricks.delta.autoCompact.enabled"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Confirm the properties are in effect
# MAGIC `SHOW TBLPROPERTIES` lists what's set on the table; `DESCRIBE EXTENDED` shows the
# MAGIC full property set plus the Predictive Optimization field on UC managed tables.

# COMMAND ----------

show_props(f"{catalog}.{schema}.events")   # expect optimizeWrite=true, autoCompact=auto

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Full table metadata incl. properties + "Predictive Optimization" (UC managed).
# MAGIC DESCRIBE EXTENDED events;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Set an explicit target file size — and inspect it
# MAGIC `delta.targetFileSize` pins the size each file aims for. Accepts a readable size
# MAGIC (`'128mb'`) or raw bytes (`134217728`). **Default is None** (autotuned). When set,
# MAGIC it's honored by OPTIMIZE, liquid clustering, auto compaction, and optimized writes.
# MAGIC
# MAGIC > **Managed-table caveat:** on UC managed tables w/ a SQL warehouse or DBR 11.3 LTS+,
# MAGIC > only **OPTIMIZE** respects `targetFileSize` (write / auto-compaction paths autosize).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Pin a 128 MB target (readable form). 128 MB = 134217728 bytes (equivalent below).
# MAGIC ALTER TABLE events
# MAGIC   SET TBLPROPERTIES ('delta.targetFileSize' = '128mb');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Equivalent: the same target expressed as a raw byte count.
# MAGIC ALTER TABLE events
# MAGIC   SET TBLPROPERTIES ('delta.targetFileSize' = '134217728');

# COMMAND ----------

show_props(f"{catalog}.{schema}.events")   # now includes delta.targetFileSize

# COMMAND ----------

# MAGIC %md
# MAGIC ### Apply the target to EXISTING files with OPTIMIZE, then MEASURE
# MAGIC Setting the property alone does not rewrite existing files (and on managed tables only
# MAGIC OPTIMIZE respects it). Run `OPTIMIZE` so files are rewritten toward the 128 MB target,
# MAGIC then compare avg file size before/after.

# COMMAND ----------

print("Before OPTIMIZE:")
n0, b0, avg0 = file_stats(f"{catalog}.{schema}.events")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Compact toward the configured targetFileSize. Bin-packing is idempotent.
# MAGIC OPTIMIZE events;

# COMMAND ----------

print("After OPTIMIZE (files rewritten toward 128 MB target):")
n1, b1, avg1 = file_stats(f"{catalog}.{schema}.events")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- See the OPTIMIZE commit + its operationMetrics (files added/removed).
# MAGIC DESCRIBE HISTORY events;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Autotuning by table size (the rule, illustrated)
# MAGIC When `delta.targetFileSize` is UNSET, the target is autotuned by table size:
# MAGIC
# MAGIC | Table size | Autotuned target |
# MAGIC | --- | --- |
# MAGIC | &lt; 2.56 TB | **256 MB** |
# MAGIC | 2.56–10 TB | grows **linearly 256 MB → 1 GB** |
# MAGIC | &gt; 10 TB | **1 GB** |
# MAGIC
# MAGIC Our demo table is tiny (well under 2.56 TB), so its autotune band is **256 MB**.
# MAGIC The helper below shows what the target WOULD be at several hypothetical sizes — the
# MAGIC rule itself, without creating multi-TB data.

# COMMAND ----------

for tb in [0.5, 1, 2.56, 5, 10, 30]:
    gb = tb * 1000
    mb = autotune_target_mb(gb)
    target = f"{mb/1024:.2f} GB" if mb >= 1024 else f"{int(round(mb))} MB"
    print(f"table ~{tb:>5} TB  ->  autotuned target ~ {target}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Revert to autotuning (unset the explicit target)
# MAGIC To go back to autotuned sizing, UNSET the property. The next writes/OPTIMIZE then
# MAGIC size by table size again. (We re-pin a fixed target in the next cell to demonstrate
# MAGIC the "avoid the growing-target caveat" pattern for large tables.)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Remove the explicit target -> revert to autotuning by table size.
# MAGIC ALTER TABLE events UNSET TBLPROPERTIES IF EXISTS ('delta.targetFileSize');

# COMMAND ----------

show_props(f"{catalog}.{schema}.events")   # targetFileSize gone -> autotuned again

# COMMAND ----------

# MAGIC %md
# MAGIC ### The autotune caveat (interview gotcha)
# MAGIC A **growing target does NOT re-optimize existing files.** As a table crosses size
# MAGIC thresholds the autotune target rises, but already-written files keep their old size —
# MAGIC so large tables may keep small files. Fix by pinning a fixed `targetFileSize` and
# MAGIC running `OPTIMIZE` to rewrite existing files to it.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- For a very large table, pin a fixed target so old small files get rewritten...
# MAGIC ALTER TABLE events SET TBLPROPERTIES ('delta.targetFileSize' = '512mb');
# MAGIC -- ...then OPTIMIZE rewrites existing files toward that fixed target.
# MAGIC OPTIMIZE events;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · `tuneFileSizesForRewrites` (rewrite-heavy tables)
# MAGIC For tables with frequent MERGE/UPDATE/DELETE, bias sizing toward rewrite-friendly
# MAGIC (smaller) files so each rewrite is cheaper. Niche — most tables don't need it.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE events
# MAGIC   SET TBLPROPERTIES ('delta.tuneFileSizesForRewrites' = 'true');

# COMMAND ----------

show_props(f"{catalog}.{schema}.events")   # now includes tuneFileSizesForRewrites

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Reduce, don't replace OPTIMIZE — the > 1 TB rule
# MAGIC Auto optimize keeps each write's files healthy, but it does NOT globally consolidate
# MAGIC the table or give skipping locality. For tables **> 1 TB**, schedule `OPTIMIZE`; for
# MAGIC NEW tables, prefer **liquid clustering** (Lesson 08) so data is laid out for fast
# MAGIC filtering. On UC managed tables, **predictive optimization** (Lesson 09) can run
# MAGIC OPTIMIZE for you.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Whole-table (or scoped) consolidation for large tables — beyond what auto optimize does.
# MAGIC OPTIMIZE events WHERE event_date >= DATE'2026-06-01';   -- scope to recent data to keep it cheap

# COMMAND ----------

# MAGIC %md
# MAGIC ### Forward reference — liquid clustering for layout (Lesson 08)
# MAGIC For new tables, lay data out for fast filtering with `CLUSTER BY`. The same `OPTIMIZE`
# MAGIC then triggers clustering incrementally. (Shown for reference; covered fully in L08.)
# MAGIC
# MAGIC ```sql
# MAGIC ALTER TABLE events CLUSTER BY (event_type, event_date);   -- DBR 15.4 LTS+ (GA)
# MAGIC OPTIMIZE events;                                          -- triggers clustering incrementally
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ### Uses, edge cases & limitations — auto optimize & target file size
# MAGIC - **Uses:** enable BOTH settings on streaming / frequent MERGE-UPDATE-DELETE tables;
# MAGIC   set `targetFileSize` when you know the size or to pin a huge table; leave it unset
# MAGIC   (autotune) for almost everything; `tuneFileSizesForRewrites` for rewrite-heavy tables.
# MAGIC - **Edge cases:** growing autotune target does NOT re-optimize existing files (old
# MAGIC   small files remain); on UC managed + SQL warehouse / DBR 11.3 LTS+, only OPTIMIZE
# MAGIC   respects `targetFileSize`; tiny tables still target 256 MB; > 1 TB tables need
# MAGIC   scheduled OPTIMIZE; MERGE/UPDATE/DELETE have optimizeWrite + autoCompact always on.
# MAGIC - **Limitations:** reduces but does NOT replace OPTIMIZE (no whole-table consolidation
# MAGIC   or skipping locality); default `targetFileSize` is None (autotune, not a fixed
# MAGIC   128 MB); automatic file-size tuning by default is a UC managed-table behavior.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Side-by-side summary — properties + file stats
# MAGIC Final view of the file-size properties now in effect and the table's file stats.

# COMMAND ----------

print("=== Final file-size properties ===")
show_props(f"{catalog}.{schema}.events")
print("\n=== Final file stats ===")
file_stats(f"{catalog}.{schema}.events")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Takeaways
# MAGIC - **"Auto optimize" = two settings:** `optimizeWrite` (write-time, L05) +
# MAGIC   `autoCompact` (post-write, L06). Set via TBLPROPERTIES or session config.
# MAGIC - **`delta.targetFileSize`** pins file size (`'128mb'` or bytes); **default is None**
# MAGIC   → **autotuned by table size**: < 2.56 TB → 256 MB; 2.56–10 TB ramps to 1 GB; > 10 TB → 1 GB.
# MAGIC - **A growing autotune target does NOT re-optimize existing files** — pin a fixed
# MAGIC   `targetFileSize` and `OPTIMIZE` to rewrite old small files on large tables.
# MAGIC - **On UC managed + SQL warehouse / DBR 11.3 LTS+, only OPTIMIZE respects `targetFileSize`**.
# MAGIC - **Auto optimize REDUCES but doesn't REPLACE OPTIMIZE** — for tables **> 1 TB**
# MAGIC   schedule OPTIMIZE; prefer **liquid clustering** for skipping; predictive optimization
# MAGIC   can automate it on managed tables.
# MAGIC - **Inspect with** `DESCRIBE DETAIL` (numFiles/sizeInBytes), `SHOW TBLPROPERTIES`,
# MAGIC   and `DESCRIBE EXTENDED`.
# MAGIC - **Next:** Lesson 08 — Liquid clustering.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · Cleanup
# MAGIC Drop the demo table so the notebook is rerunnable.

# COMMAND ----------

spark.sql(f"DROP TABLE IF EXISTS {catalog}.{schema}.events")

# Optional: also drop the demo schema (uncomment if you created it solely for this lesson).
# spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print("Cleanup complete.")
