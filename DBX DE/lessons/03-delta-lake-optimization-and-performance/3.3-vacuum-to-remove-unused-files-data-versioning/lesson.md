# VACUUM & Data Versioning / Time-Travel UNDO

> **Topic 3.3 · Delta Lake Optimization & Performance** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 3
> notebook (built at the last subtopic); snippets below are the teaching units.

## What it is

- **VACUUM** = housekeeping: **permanently delete data files no longer referenced**
  by the table that are **older than the retention threshold** (default **7 days**).
- It frees storage and makes removed records truly unrecoverable — the flip side
  of **time travel**: VACUUM is exactly what *ends* your ability to travel back
  past the retention window.

**Analogy:** VACUUM is **emptying the recycle bin**. A `DELETE` just moves old
files to "trash" (still time-travelable); VACUUM empties the bin for anything
older than the retention period — space reclaimed, but no more undo for those.

## Why it matters

- Without VACUUM, every rewrite (OPTIMIZE, MERGE, DELETE, UPDATE) **leaves stale
  files** → storage grows forever and file listing slows down.
- The retention/time-travel trade-off is a **classic interview question**: *"Why
  can't I VACUUM with 0 retention?"* → it would break time travel **and** can
  corrupt **in-flight concurrent readers/writers** still pointing at those files.

**Real-world use case:** a heavily-updated silver table balloons in storage. A
scheduled `VACUUM` (default 7-day retention) clears stale files nightly — keeping
a week of time travel for rollbacks while controlling cost.

---

## How it works — deep dive

### 1. The delete → vacuum lifecycle

A row removal happens in **two stages** — this is the mental model interviewers
want:

1. **Logical removal** — `DELETE`/`UPDATE`/`MERGE`/`OPTIMIZE` writes new files and
   marks old ones **removed in the log** (tombstones). The old files **stay on
   disk** → still time-travelable, **no space freed yet**.
2. **Physical removal** — `VACUUM` deletes those tombstoned files **once they're
   older than the retention window**. Now space is reclaimed and the old versions
   are gone for good.

```sql
DELETE FROM main.sales.events WHERE event_date < '2026-01-01';  -- stage 1: logical
VACUUM main.sales.events;                                       -- stage 2: physical (past retention)
```

### 2. Syntax & modes

```sql
VACUUM main.sales.events;                       -- delete files older than retention (default 7 days)
VACUUM main.sales.events RETAIN 168 HOURS;      -- explicit retention (168h = 7 days)
VACUUM main.sales.events DRY RUN;               -- preview what WOULD be deleted; deletes nothing
VACUUM main.sales.events LITE;                  -- log-only scan, faster (Public Preview, DBR 16.4 LTS+)
```

```python
# PySpark — DeltaTable API
from delta.tables import DeltaTable
dt = DeltaTable.forName(spark, "main.sales.events")
dt.vacuum()            # default retention
dt.vacuum(168)         # retain 168 hours
```

- **FULL** (default) lists the storage location to find unreferenced files;
  **LITE** uses only the transaction log to identify them — faster, but needs a
  recent successful VACUUM within log retention.
- **Always `DRY RUN` first** on important tables to see exactly what would go.

### 3. The retention safety check (the interview trap)

- Files are eligible for VACUUM only past **`delta.deletedFileRetentionDuration`**
  (default **7 days**).
- Databricks **blocks** a VACUUM below the threshold with a safety check. You can
  disable it, but only when you're certain no operation runs longer than your
  chosen window:

```sql
-- ⚠️ Only if you understand the risk — this can break time travel AND corrupt
-- concurrent readers/writers still referencing those files.
SET spark.databricks.delta.retentionDurationCheck.enabled = false;
VACUUM main.sales.events RETAIN 0 HOURS;
```

- **Why the guard exists:** a long-running query started before the VACUUM may
  still need files you'd delete → it would fail or read corrupt data. Keep
  retention ≥ your longest job runtime.

### 4. Tune retention & let Databricks run it

```sql
-- Keep a longer rollback window (more time travel, more storage)
ALTER TABLE main.sales.events
  SET TBLPROPERTIES ('delta.deletedFileRetentionDuration' = 'interval 30 days');
```

- **Predictive Optimization** runs `VACUUM` (and `OPTIMIZE`/`ANALYZE`)
  **automatically on UC managed tables** — so you often don't schedule it. It does
  **not** run automatically otherwise (external tables → you schedule it).

### 5. Compliance angle (right-to-be-forgotten)

- For GDPR/CCPA deletion, a `DELETE` alone isn't enough — the data still exists in
  old files until **VACUUM** physically removes it past retention. The pattern is
  `DELETE` the records, then ensure `VACUUM` runs so the files are actually gone.

---

## Comparison: logical vs physical removal

| | DELETE / MERGE / OPTIMIZE | VACUUM |
|---|---|---|
| Effect | tombstones old files (logical) | physically deletes them |
| Space freed | ❌ not yet | ✅ (files past retention) |
| Time travel to old version | ✅ still works | ❌ gone after VACUUM |
| Automatic? | n/a | only via Predictive Optimization (managed) |

## Uses, edge cases & limitations

- **Uses:** reclaim storage, finalize compliance deletions, routine maintenance of
  churny (heavily updated) tables.
- **Edge cases:**
  - VACUUM **shrinks the time-travel window** — versions older than retention are
    no longer queryable afterward.
  - **LITE mode** (Public Preview, DBR 16.4 LTS+) is faster but needs a prior
    successful VACUUM within log retention; otherwise use FULL.
  - Always run **`DRY RUN`** first on important tables.
- **Limitations:** VACUUM only removes files **past retention** — it won't
  instantly reclaim space from a delete you did 5 minutes ago. Lowering retention
  to force this trades away time travel and risks concurrent readers.

## Common gotchas

- ❌ Setting retention to 0 (or very low) to "save space now" — breaks time travel
  and can corrupt concurrent reads; Databricks blocks it by default for a reason.
- ❌ Expecting time travel to still reach old versions **after** VACUUM — it can't.
- ❌ Running VACUUM and being surprised storage didn't drop — only files **older
  than retention** are removed.
- ❌ Assuming `DELETE` alone satisfies GDPR — you must VACUUM to physically remove.
- ❌ Manually scheduling VACUUM on managed tables that **Predictive Optimization**
  already handles.

## References

- [VACUUM — Databricks docs](https://docs.databricks.com/aws/en/delta/vacuum)
- [VACUUM (SQL reference)](https://docs.databricks.com/aws/en/sql/language-manual/delta-vacuum)
- [Table history & time travel](https://docs.databricks.com/aws/en/delta/history)
- [Predictive optimization](https://docs.databricks.com/aws/en/optimizations/predictive-optimization)
