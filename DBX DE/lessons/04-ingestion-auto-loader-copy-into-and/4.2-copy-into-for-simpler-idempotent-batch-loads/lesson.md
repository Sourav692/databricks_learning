# COPY INTO

> **Topic 4.2 · Ingestion — Auto Loader, COPY INTO & Lakeflow Connect** —
> enterprise deep-dive, interview-focused. Runnable end-to-end code lives in the
> consolidated Topic 4 notebook (built at the last subtopic); snippets below are
> the teaching units.

## What it is

- **COPY INTO** is a **SQL command** that loads files from cloud storage (or a UC
  Volume) into a Delta table — and **skips files it already loaded**, so it's
  **idempotent and incremental**.
- Think of it as the **batch, SQL-first** sibling of Auto Loader: re-run it safely
  and only new files get added.

**Analogy:** importing bank statements into a spreadsheet. COPY INTO remembers
which statement files it already imported, so running the import again **won't
double-count** — it just adds the new ones.

## Why it matters

- Re-running ingestion is normal (retries, scheduled jobs). Without idempotency
  you'd get **duplicates**; COPY INTO prevents that automatically.
- It's the **simplest path** for periodic/batch file loads — pure SQL, no
  streaming setup. A common "Auto Loader vs COPY INTO?" interview question.

**Real-world use case:** a vendor drops a daily CSV export into a UC Volume. A
scheduled `COPY INTO bronze_sales FROM '...'` loads each new day's file exactly
once — no checkpoint plumbing, just a SQL statement in a job.

---

## How it works — deep dive

### 1. Basic load + the idempotency mechanism

```sql
COPY INTO cat.sch.bronze_sales
FROM '/Volumes/cat/sch/vol/sales/'
FILEFORMAT = CSV
FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'true')   -- parsing options
COPY_OPTIONS  ('mergeSchema' = 'true');                       -- load behavior
```

- COPY INTO **tracks the files it has loaded** (per target table). On the next run
  it **skips already-loaded files** — *even if the file was later modified*. That's
  the idempotency guarantee, and the subtle gotcha (it's tracked by **path**, not
  content).
- **`FORMAT_OPTIONS`** = how to parse (header, delimiter, schema inference);
  **`COPY_OPTIONS`** = how to load (`mergeSchema`, `force`, …).
- Supports CSV, JSON, XML, Avro, ORC, Parquet, text, binary.

### 2. Transform on load with a `SELECT`

Wrap a `SELECT` to **cast / rename / derive** columns *during* ingestion — handy
for typing raw strings or selecting a subset, without a second pass.

```sql
COPY INTO cat.sch.bronze_sales
FROM (
  SELECT
    CAST(order_id AS BIGINT)        AS order_id,
    CAST(amount   AS DECIMAL(10,2)) AS amount,
    to_date(order_date, 'yyyy-MM-dd') AS order_date
  FROM '/Volumes/cat/sch/vol/sales/'
)
FILEFORMAT = CSV
FORMAT_OPTIONS ('header' = 'true');
```

### 3. Target specific files — `PATTERN` / `FILES`

```sql
COPY INTO cat.sch.bronze_sales
FROM '/Volumes/cat/sch/vol/sales/'
FILEFORMAT = CSV
PATTERN = '2026-*.csv';            -- glob: only this year's files

-- or an explicit list (max 1000 files)
-- FILES = ('2026-06-01.csv', '2026-06-02.csv')
```

- `PATTERN` and `FILES` are **mutually exclusive**; `FILES` is capped at 1000.

### 4. Dry-run with `VALIDATE`

Test parsing, schema compatibility, and constraints **without writing** — ideal in
a pre-prod check before a big load.

```sql
COPY INTO cat.sch.bronze_sales
FROM '/Volumes/cat/sch/vol/sales/'
FILEFORMAT = CSV
VALIDATE 50 ROWS;                  -- or VALIDATE ALL
```

### 5. Force a reload + evolve schema

```sql
COPY INTO cat.sch.bronze_sales
FROM '/Volumes/cat/sch/vol/sales/'
FILEFORMAT = CSV
COPY_OPTIONS ('force' = 'true',    -- ignore the loaded-files tracking; reload everything
              'mergeSchema' = 'true');
```

- **`force = true`** disables idempotency for that run (re-loads regardless) — use
  to re-ingest after a fix; **`mergeSchema = true`** lets new columns evolve in.

### 6. Programmatic use (PySpark)

```python
# COPY INTO is a SQL command — run it from Python via spark.sql
spark.sql("""
  COPY INTO cat.sch.bronze_sales
  FROM '/Volumes/cat/sch/vol/sales/'
  FILEFORMAT = CSV
  FORMAT_OPTIONS ('header' = 'true')
""")
```

---

## COPY INTO vs Auto Loader

| | COPY INTO | Auto Loader |
|---|---|---|
| Interface | SQL command | Structured Streaming (`cloudFiles`) |
| Style | Batch / scheduled | Streaming or `availableNow` |
| Scale | Thousands of files | Millions / billions |
| State | Tracks loaded files | Checkpoint (RocksDB) |
| Best for | Simple periodic loads | Large-scale, continuous ingest |

**Decision:** modest, periodic, SQL-only → **COPY INTO**. High file counts /
continuous / needs schema-evolution control → **Auto Loader** (or a streaming
table built on it).

## Uses, edge cases & limitations

- **Uses:** scheduled batch loads of modest file volumes; quick one-off loads;
  SQL-only pipelines; transform-on-load via `SELECT`.
- **Edge cases:**
  - Very large / high-frequency directories → prefer **Auto Loader / streaming
    tables** (more scalable).
  - **A modified-but-same-path file is skipped** (tracked by path) — use `force`
    to re-ingest it.
  - Schema drift needs `mergeSchema`.
- **Limitations:** less scalable than Auto Loader for huge file counts; idempotency
  is **per-target-table** file tracking — loading the same files into a *different*
  table loads them again. It dedups **files**, not **rows**.

## Common gotchas

- ❌ Expecting COPY INTO to scale to millions of files like Auto Loader — it won't.
- ❌ Assuming a re-run re-loads everything — it **skips** already-loaded files
  (that's the feature; use `force=true` to override).
- ❌ Expecting a re-uploaded/edited file to reload — same path = skipped; use
  `force`.
- ❌ Forgetting `mergeSchema` when the incoming schema changes → load errors.
- ❌ Thinking it dedups *rows* — it dedups **files**, not record contents.

## References

- [COPY INTO — Databricks docs](https://docs.databricks.com/aws/en/ingestion/copy-into/)
- [COPY INTO SQL reference](https://docs.databricks.com/aws/en/sql/language-manual/delta-copy-into)
- [Auto Loader](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/)
