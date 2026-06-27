# Delta Tables & the Transaction Log

> **Topic 2.1 · Delta Lake — the storage foundation** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code also lives in the consolidated
> Topic 2 notebook; the snippets below are the teaching units for each sub-topic.

## What it is

- **Delta Lake** is an open storage layer that turns a plain directory of
  **Parquet** files in cloud object storage into a reliable **table** by adding a
  **file-based transaction log**.
- The **transaction log** (the `_delta_log/` directory) is the ordered,
  append-only record of every change to the table. It is the single source of
  truth — *the table is the log*, not the set of Parquet files on disk.
- On Databricks, **every table is a Delta table by default** — no `USING DELTA`
  needed.

**Analogy:** the transaction log is the **ledger of a bank account**. The Parquet
files are the cash in the vault; the ledger records every deposit/withdrawal *in
order*, so the balance is always correct, auditable, and replayable — even when
many tellers work at once.

## Why it matters

- It is the reason a cheap pile of files in S3/ADLS/GCS behaves like a warehouse
  table: **ACID transactions, no corruption, full version history, fast scans**.
- It is the most common Delta interview question: *"How does Delta give ACID on
  object storage that has no transactions?"* → **the transaction log + optimistic
  concurrency.** This lesson lets you answer that with mechanism *and* code.

**Real-world use case:** a streaming job and a nightly batch job both write to the
same `orders` table. The log serializes their commits so neither reader ever sees
a half-written result, and the table is never left corrupt.

---

## How it works — deep dive

### 1. Physical layout: Parquet data + `_delta_log/`

A Delta table on disk is just two things: data files and a log directory.

```text
/orders/                                  ← table root
├── part-00000-....snappy.parquet         ← data (immutable Parquet files)
├── part-00001-....snappy.parquet
└── _delta_log/                           ← the transaction log
    ├── 00000000000000000000.json         ← commit 0 (atomic unit)
    ├── 00000000000000000001.json         ← commit 1
    ├── ...
    ├── 00000000000000000010.checkpoint.parquet  ← checkpoint (written periodically)
    └── _last_checkpoint                   ← pointer to newest checkpoint
```

- **Data files are immutable.** Delta never edits a Parquet file in place. An
  "update" writes *new* files and marks old ones removed in the log.
- **Each commit is one JSON file**, zero-padded and monotonically increasing
  (`...0000.json`, `...0001.json`). The filename *is* the version number.

```sql
-- Inspect the log of any table from SQL
DESCRIBE HISTORY main.sales.orders;        -- one row per commit (version, op, user, metrics)
DESCRIBE DETAIL  main.sales.orders;        -- numFiles, sizeInBytes, location, format
```

### 2. What lives inside a commit (the "actions")

Each JSON commit file is a list of **actions** — the diff applied at that version:

| Action | Meaning |
|---|---|
| `add` | A data file added (with size + **column stats**: min/max/nullCount). |
| `remove` | A data file logically removed (tombstone; file still on disk until VACUUM). |
| `metaData` | Schema, partitioning, table properties. |
| `protocol` | Reader/writer protocol versions (which features are required). |
| `commitInfo` | Audit metadata: operation, timestamp, user, metrics. |
| `txn` | Idempotency marker for streaming (set-once per stream batch). |

This "diff of files" model is the key mental shift: **a write is not an edit, it's
a set of add/remove records appended to the log.**

### 3. Snapshot reconstruction (how a read computes "the current table")

To read the table, Delta computes the current set of live files by **replaying the
log**:

1. Read `_last_checkpoint` → jump to the newest **checkpoint** (a Parquet
   snapshot of all actions up to some version — avoids replaying from version 0).
2. Replay the JSON commits *after* the checkpoint.
3. Net the `add` minus `remove` actions → the exact list of Parquet files that
   make up the current version. The query reads only those files.

- **Checkpoints** are written automatically (cadence controlled by
  `delta.checkpointInterval`; the default is **runtime-dependent** — 100 on recent
  Databricks Runtimes, 10 in OSS Delta) so reconstruction stays O(recent commits),
  not O(all history). This is why a table with millions of commits still opens fast.

### 4. ACID via atomic commits + optimistic concurrency

This is the heart of the interview answer.

- **Atomicity** — a commit succeeds only when its single JSON file is written.
  Delta uses a **put-if-absent** (mutual-exclusion) write on the next version
  filename. Either the whole `....N.json` lands or it doesn't → **all-or-nothing**.
  Readers never see a partial commit because they only count *committed* files.
- **Isolation via Optimistic Concurrency Control (OCC):** writers don't lock.
  Each writer (1) reads the current snapshot version, (2) does its work, (3) tries
  to commit as the next version. If someone else committed first, Delta **detects
  the conflict, and the loser retries** against the new snapshot.
- **Conflict types** an interviewer may ask about:
  - `ConcurrentAppendException` — two writers added files that affect the same
    partition/predicate. Narrow each writer with a disjoint predicate to avoid it.
  - `ConcurrentDeleteReadException` / `ConcurrentDeleteDeleteException` — a file
    you read/deleted was deleted by another commit.
  - `MetadataChangedException` — the schema/partitioning changed under you.

```python
# Concurrency in practice: scope writers to disjoint partitions so OCC doesn't
# treat them as conflicting. Each job writes only its own region partition.
(df_us.write.format("delta").mode("append")
   .option("replaceWhere", "region = 'US'")          # disjoint predicate
   .saveAsTable("main.sales.orders"))
# A parallel job using replaceWhere "region = 'EU'" won't conflict.
```

### 5. Schema enforcement (write-time validation)

- Delta **validates the DataFrame schema against the table schema on every write**
  and **rejects** mismatches (wrong types, unknown columns) — protecting every
  downstream consumer from silently corrupted data.
- This is *enforcement* (reject), which is different from *evolution* (opt-in add
  of new columns — covered separately).

```python
# This APPEND fails fast — `bonus` is not a column in the target table.
bad = spark.createDataFrame([(1, "ACME", 99.0)], ["id", "name", "bonus"])
bad.write.format("delta").mode("append").saveAsTable("main.sales.customers")
# AnalysisException: A schema mismatch detected when writing to the Delta table.

# Opt in to evolution only when you *intend* to add the column:
(bad.write.format("delta").mode("append")
    .option("mergeSchema", "true")                    # adds `bonus` to the table
    .saveAsTable("main.sales.customers"))
```

### 6. Data skipping via column statistics (why the log makes reads fast)

- Every `add` action stores **min/max/nullCount** per column (for the first
  **32** columns by default, `delta.dataSkippingNumIndexedCols`).
- A filtered query reads the stats from the log and **skips entire Parquet files**
  whose min/max range can't match the predicate — no file open, no I/O.

```sql
-- Delta reads file-level min/max from the log and skips files where
-- order_date can't fall in range — often 10–100x less data scanned.
SELECT * FROM main.sales.orders
WHERE order_date BETWEEN '2024-01-01' AND '2024-01-07';
```

> File-skipping is *automatic*; `OPTIMIZE`/`ZORDER`/liquid clustering (Topic 3)
> make it dramatically more effective by co-locating related values into the same
> files so min/max ranges are tight.

### 7. Version history & time travel (a free side effect of the log)

Because the log keeps every commit, you can query the table **as of** any past
version or timestamp — within the retention window.

```sql
SELECT * FROM main.sales.orders VERSION AS OF 12;           -- by commit version
SELECT * FROM main.sales.orders TIMESTAMP AS OF '2024-01-15';  -- by wall-clock

-- Roll a bad load back to a known-good version (writes a NEW commit; no data loss)
RESTORE TABLE main.sales.orders TO VERSION AS OF 12;
```

```python
# Same, DataFrame API
df = spark.read.option("versionAsOf", 12).table("main.sales.orders")
```

---

## How to do it — from scratch

```python
catalog, schema = "main", "de_demo"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")

# Create a Delta table (Delta is the default — no USING DELTA needed)
spark.sql("""
  CREATE TABLE IF NOT EXISTS orders (
    order_id   BIGINT,
    customer   STRING,
    amount     DECIMAL(10,2),
    order_date DATE
  )
  TBLPROPERTIES (delta.checkpointInterval = 10)   -- explicitly set checkpoint cadence
""")

# Each write = a new version = a new JSON commit in _delta_log/
spark.sql("INSERT INTO orders VALUES (1,'ACME', 250.00, DATE'2024-01-03')")
spark.sql("INSERT INTO orders VALUES (2,'Globex', 80.50, DATE'2024-01-04')")

spark.sql("DESCRIBE HISTORY orders").show(truncate=False)  # see versions 0,1,2...
```

---

## Comparison: Delta vs. plain Parquet/CSV "tables"

| Capability | Plain Parquet/CSV dir | **Delta table** |
|---|---|---|
| Atomic multi-file writes | ❌ readers can see partial output | ✅ all-or-nothing commit |
| Concurrent writers | ❌ corruption / lost data | ✅ OCC with conflict detection |
| Schema enforcement | ❌ anything lands | ✅ validated on write |
| Update/delete/upsert | ❌ rewrite by hand | ✅ `MERGE`/`UPDATE`/`DELETE` |
| Time travel / audit | ❌ none | ✅ `DESCRIBE HISTORY`, `VERSION AS OF` |
| Fast filtered reads | ❌ scan everything | ✅ file-skipping via log stats |

---

## Uses, edge cases & limitations

- **Uses:** every bronze/silver/gold table; concurrent batch + streaming ETL;
  anything needing reliable upserts, audits, or rollbacks; CDC targets.
- **Edge cases:**
  - **Small-files / tiny-commits problem** — committing per-row or per-tiny-file
    bloats the log and creates thousands of small Parquet files → slow reads. Fix
    with batching, `OPTIMIZE`/compaction (Topic 3), and Auto Loader-style
    micro-batching, not per-record commits.
  - **Concurrent writers to the same files** raise a conflict exception that the
    writer must **retry** (or avoid via disjoint `replaceWhere` predicates).
  - **Checkpoint dependency** — deleting checkpoint files breaks fast snapshot
    reconstruction; never hand-edit `_delta_log/`.
- **Limitations:**
  - **Time travel isn't forever** — `VACUUM` physically deletes files older than
    the retention window (default **7 days**), after which old versions are no
    longer queryable.
  - Delta is built for **analytical** workloads, **not** high-frequency OLTP
    single-row point writes — use a transactional DB (e.g. Lakebase) for that.

## Common mistakes / gotchas

- ❌ **Hand-editing or deleting files in `_delta_log/`** — corrupts the table.
  Always go through Delta operations (`INSERT`/`MERGE`/`RESTORE`/`VACUUM`).
- ❌ **Assuming time travel is permanent** — `VACUUM` + retention decide how far
  back you can go. Don't rely on it for long-term archival.
- ❌ **Confusing enforcement with evolution** — enforcement *rejects* mismatches
  by default; evolution (`mergeSchema`/`autoMerge`) *adds* columns and is opt-in.
- ❌ **Committing per row** in a loop — explodes commit + small-file count. Write
  in batches.
- ❌ Saying "Delta locks the table for writers" in an interview — it's
  **optimistic** concurrency (no locks; detect-and-retry).

## References

- [What is Delta Lake? — docs](https://docs.databricks.com/aws/en/delta/)
- [Delta Lake transaction log & concurrency control](https://docs.databricks.com/aws/en/delta/concurrency-control)
- [Delta table history / time travel](https://docs.databricks.com/aws/en/delta/history)
- [Data skipping with Delta Lake](https://docs.databricks.com/aws/en/delta/data-skipping)
- [Diving into Delta Lake: Unpacking the Transaction Log (Databricks blog, third-party-style deep dive)](https://www.databricks.com/blog/2019/08/21/diving-into-delta-lake-unpacking-the-transaction-log.html)
