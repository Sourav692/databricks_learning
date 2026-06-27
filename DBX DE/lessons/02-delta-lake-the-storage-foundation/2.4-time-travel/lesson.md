# Time Travel

> **Topic 2.4 · Delta Lake — the storage foundation** — enterprise deep-dive,
> interview-focused. Hands-on, end-to-end code for all of Topic 2 lives in the
> consolidated notebook `delta_lake_hands_on.py` in the topic folder.

## What it is

- **Time travel** = query or restore an **earlier version** of a Delta table,
  using the transaction log's version history (Topic 2.1).
- Point at the past two ways:
  - **`VERSION AS OF <n>`** — a specific commit number.
  - **`TIMESTAMP AS OF '<ts>'`** — the table as it looked at a moment in time.
- Inspect history with **`DESCRIBE HISTORY`**; roll back with **`RESTORE TABLE`**.

**Analogy:** it's the **"undo history" / Google Docs version history** for a
table — jump to how it looked yesterday, or restore that version as the current
one (but only as far back as retention keeps the files).

## Why it matters

- **Recover from mistakes fast** — a bad write or accidental delete is one
  `RESTORE` away, no backups needed.
- **Reproducibility & audit** — re-run a report exactly as of month-end, or prove
  what the data showed on a given day.

**Real-world use case:** an ETL job corrupts the orders table at 2am. You
`DESCRIBE HISTORY`, find the last good version, and `RESTORE TABLE orders TO
VERSION AS OF 41` — fixed in seconds, history preserved.

---

## How it works — deep dive

### 1. Querying the past — `VERSION` / `TIMESTAMP AS OF`

Every write created a version; time travel reads the snapshot the log describes
for that version. Two SQL forms, plus an `@` shorthand and PySpark read options.

```sql
-- Explicit clauses
SELECT * FROM main.sales.orders VERSION AS OF 41;
SELECT * FROM main.sales.orders TIMESTAMP AS OF '2026-06-20T09:00:00Z';

-- @ shorthand: @v<version> or @<yyyyMMddHHmmssSSS>
SELECT * FROM main.sales.orders@v41;
SELECT * FROM main.sales.orders@20260620090000000;
```

```python
# PySpark read options
v_old = spark.read.option("versionAsOf", 41).table("main.sales.orders")
t_old = spark.read.option("timestampAsOf", "2026-06-20").table("main.sales.orders")
```

- **Use case:** audits and reproducible reporting — pin a dashboard/notebook to a
  fixed version so re-runs return identical numbers.

### 2. `DESCRIBE HISTORY` — find the version you want

The history is itself an audit log: one row per commit with the operation, user,
timestamp, and metrics. It's your starting point before any rollback.

```sql
DESCRIBE HISTORY main.sales.orders;     -- newest first: version, timestamp, operation, operationMetrics, userName
```

- Read `operationMetrics` to see rows written/updated/deleted per commit — useful
  to spot the exact bad write before restoring.

### 3. `RESTORE` — roll the table back

`RESTORE` resets the table's data to a chosen version. Crucially it is a
**new data-changing commit** (its log entries have `dataChange = true`) — it does
**not** erase history, so you can always roll forward again.

```sql
RESTORE TABLE main.sales.orders TO VERSION AS OF 41;
RESTORE TABLE main.sales.orders TO TIMESTAMP AS OF '2026-06-20T09:00:00Z';
```

```python
from delta.tables import DeltaTable
DeltaTable.forName(spark, "main.sales.orders").restoreToVersion(41)
```

- **Streaming caveat:** because `RESTORE` is a data change, a downstream
  Structured Streaming reader of this table sees new data → can cause **duplicate
  reprocessing**. Plan for idempotent downstream writes (e.g. MERGE) when you
  restore a streaming source.

### 4. Retention — how far back you can actually go

Time travel is bounded by **two** independent retention settings. Both must cover
the window you want, or old versions become unqueryable:

| Property | Controls | Default |
|---|---|---|
| `delta.deletedFileRetentionDuration` | how long removed **data files** are kept (VACUUM horizon) | **7 days** |
| `delta.logRetentionDuration` | how long **commit/history metadata** is kept | **30 days** |

```sql
-- Extend BOTH together to keep, e.g., 60 days of time travel (higher storage cost)
ALTER TABLE main.sales.orders SET TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 60 days',
  'delta.logRetentionDuration'         = 'interval 60 days'
);
```

- After `VACUUM` removes files past `deletedFileRetentionDuration`, versions that
  referenced them **can't be queried** even if the log entry still exists.

### 5. Diffing versions — Change Data Feed (CDF)

Time travel reads a snapshot; **CDF** answers *"what rows changed between two
versions?"* — the enterprise pattern for incremental downstream propagation.

```sql
ALTER TABLE main.sales.orders SET TBLPROPERTIES (delta.enableChangeDataFeed = true);

-- Row-level changes from v41 onward, tagged with the change type
SELECT * FROM table_changes('main.sales.orders', 41);   -- adds _change_type, _commit_version, _commit_timestamp
```

```python
changes = (spark.read.option("readChangeFeed", "true")
                     .option("startingVersion", 41)
                     .table("main.sales.orders"))
# _change_type ∈ {insert, update_preimage, update_postimage, delete}
```

---

## Comparison: which tool for "the past"?

| Need | Use |
|---|---|
| See the table as it *was* | `VERSION` / `TIMESTAMP AS OF` (read-only) |
| Undo a bad write / delete | `RESTORE TABLE` (new commit) |
| Find the right version to use | `DESCRIBE HISTORY` |
| Know *which rows* changed between versions | Change Data Feed (`table_changes`) |

## Uses, edge cases & limitations

- **Uses:** rollback/undo, auditing, reproducing historical analyses, debugging
  pipelines, version diffs (CDF), reproducible ML/reporting snapshots.
- **Edge cases:**
  - **Retention limits how far back you can go** — `VACUUM` removes files past
    `delta.deletedFileRetentionDuration` (default 7 days); history metadata past
    `delta.logRetentionDuration` (default 30 days).
  - **`RESTORE` on a streaming source** is a data change → may cause duplicate
    reprocessing downstream; make downstream writes idempotent.
  - **CDF must be enabled *before*** the changes you want to read; it isn't
    retroactive.
- **Limitations:** time travel is **not a backup/DR strategy** — it's bounded by
  retention. Extend both retention properties together if you need more history,
  at higher storage cost.

## Common gotchas

- ❌ Assuming you can travel back arbitrarily far — **VACUUM + retention** cap it.
- ❌ Treating time travel as backup/DR — it's bounded by retention.
- ❌ Raising `deletedFileRetentionDuration` without also raising
  `logRetentionDuration` — both must cover the window you want.
- ❌ Forgetting `RESTORE` adds a **new version** (history preserved) and counts as
  a write for downstream streaming consumers.
- ❌ Expecting CDF to show changes from *before* you enabled it — it's not
  retroactive.

## References

- [Delta table history & time travel — docs](https://docs.databricks.com/aws/en/delta/history)
- [VACUUM](https://docs.databricks.com/aws/en/delta/vacuum)
- [RESTORE](https://docs.databricks.com/aws/en/sql/language-manual/delta-restore)
- [Change Data Feed](https://docs.databricks.com/aws/en/delta/delta-change-data-feed)
