# Auto Loader (cloudFiles)

> **Topic 4.1 · Ingestion — Auto Loader, COPY INTO & Lakeflow Connect** —
> enterprise deep-dive, interview-focused. Runnable end-to-end code lives in the
> consolidated Topic 4 notebook (built at the last subtopic); snippets below are
> the teaching units.

## What it is

- **Auto Loader** incrementally ingests **new files as they land** in cloud
  storage — only the new ones, automatically, without you tracking what's
  already processed.
- It's a **Structured Streaming source** named **`cloudFiles`**, so it inherits
  checkpointing and **exactly-once** processing.
- Built-in **schema inference & evolution** and a **rescued-data column** handle
  messy / changing input.

**Analogy:** a **conveyor belt that only picks up new boxes.** Files keep landing
in a folder; Auto Loader grabs just the ones it hasn't seen, never re-handling old
boxes — even after a restart.

## Why it matters

- The naive approach ("list the folder, read everything") **reprocesses old data**
  and **doesn't scale** to millions of files. Auto Loader solves both.
- It's *the* default ingestion answer for landing raw files into **bronze** — a
  guaranteed interview topic.

**Real-world use case:** clickstream JSON lands in S3 every few minutes. Auto
Loader streams only the new files into a bronze Delta table, evolving the schema
as new fields appear and parking malformed records in `_rescued_data`.

---

## How it works — deep dive

### 1. Core setup (`cloudFiles` source + checkpoint)

```python
df = (spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.schemaLocation", "/Volumes/cat/sch/vol/_schema")  # tracks inferred schema
        .load("/Volumes/cat/sch/vol/landing"))

(df.writeStream
   .option("checkpointLocation", "/Volumes/cat/sch/vol/_ckpt")   # exactly-once state
   .trigger(availableNow=True)                                   # batch-style: all new, then stop
   .toTable("cat.sch.bronze_events"))
```

```sql
-- SQL equivalent via the read_files streaming table-valued function
CREATE OR REFRESH STREAMING TABLE cat.sch.bronze_events AS
SELECT * FROM STREAM read_files('/Volumes/cat/sch/vol/landing', format => 'json');
```

- **`trigger(availableNow=True)`** = process all currently-available files then
  stop (great for scheduled jobs); omit it for an always-on stream.
- Throttle batch size with **`cloudFiles.maxFilesPerTrigger`** /
  **`cloudFiles.maxBytesPerTrigger`** to control cost & memory.

### 2. Schema inference & evolution (the part interviewers probe)

Auto Loader infers the schema (stored at `schemaLocation`) and adapts as input
changes. The behavior is governed by **`cloudFiles.schemaEvolutionMode`**:

| Mode | Behavior |
|---|---|
| **`addNewColumns`** *(default)* | New column → stream **fails with `UnknownFieldException`**, schema is updated; **restart resumes** with the new column. |
| `addNewColumnsWithTypeWidening` | Same, plus widens types (e.g. `int`→`long`). |
| `rescue` | Never evolves, never fails — unexpected data goes to `_rescued_data`. |
| `failOnNewColumns` | Fails and **won't restart** until you update the provided schema. |
| `none` | Ignores new columns, does **not** rescue them. |

- The default **fail-then-restart** is intentional: it forces a clean, auditable
  schema change. In production, run under a **job with retries** so the restart is
  automatic.
- Pin known types with **`cloudFiles.schemaHints`** (e.g. `"id long, ts timestamp"`)
  and enable **`cloudFiles.inferColumnTypes=true`** for real types in JSON/CSV/XML.

```python
.option("cloudFiles.schemaEvolutionMode", "addNewColumns")
.option("cloudFiles.schemaHints", "id long, event_ts timestamp")
.option("cloudFiles.inferColumnTypes", "true")
```

### 3. Rescued data — nothing is silently dropped

- A **`_rescued_data`** column captures values that don't fit the schema (missing
  column, type mismatch, case mismatch) instead of dropping them. Rename it with
  **`cloudFiles.rescuedDataColumn`**.
- **Always inspect `_rescued_data`** — that's where "missing" rows actually went.

```python
.option("cloudFiles.rescuedDataColumn", "_rescued")   # custom name
```

### 4. File detection modes (cost/scale lever)

| Mode | How it finds new files | When |
|---|---|---|
| **Directory listing** *(default)* | lists the input dir | small/simple; minimal setup |
| **File notification** (`cloudFiles.useNotifications=true`) | cloud event queue (SNS/SQS, etc.) | large/busy dirs — avoids repeated listing |
| **File events** (UC external locations) | UC-managed events | **recommended at scale** — most performant & scalable; DBR 14.3 LTS+ |

- On very large directories, repeated **listing gets slow and expensive** → move
  to file notification, or enable **file events** on the UC external location
  (the current best-practice path, no extra cloud permissions to manage).

### 5. Checkpoint & exactly-once

- The **checkpoint** stores seen-file metadata (in RocksDB) → **exactly-once**,
  restart-safe processing. **One checkpoint per stream** — never share.
- Deleting/moving the checkpoint makes Auto Loader **reprocess everything** (use a
  fresh checkpoint deliberately when you *want* a full re-ingest).

---

## Comparison: detection modes at a glance

| | Directory listing | File notification | File events (UC) |
|---|---|---|---|
| Setup | none | cloud queue resources | enable on UC ext. location |
| Cost at scale | high (list calls) | low | **lowest** |
| Recommended | small dirs | large dirs | **default for scale** |

## Uses, edge cases & limitations

- **Uses:** continuous or scheduled (`trigger(availableNow=True)`) bronze ingestion
  of files from S3/ADLS/GCS/UC Volumes; JSON, CSV, Parquet, Avro, XML, etc.
- **Edge cases:**
  - Default **`addNewColumns`** fails-then-restarts on a new column — run under a
    retrying job so it self-heals.
  - **Directory listing** gets slow/expensive on very large dirs → file
    notification or file events.
  - Deleting the **checkpoint** reprocesses everything.
- **Limitations:** it ingests **files** — for message buses (Kafka/Event Hubs) use
  the streaming connectors instead (covered in 4.4). Not for OLTP row writes.

## Common gotchas

- ❌ Pointing two streams at the **same checkpoint** — corrupts state. One
  checkpoint per stream.
- ❌ Expecting it to re-read old files — by design it won't; use a new checkpoint
  to reprocess.
- ❌ Ignoring **`_rescued_data`** — that's where malformed rows land.
- ❌ Using directory listing at massive scale — switch to file notification / file
  events.
- ❌ Assuming a new column "just works" — default mode **fails the batch first**;
  ensure the job restarts.

## References

- [Auto Loader — Databricks docs](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/)
- [Auto Loader schema inference & evolution](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/schema)
- [File detection modes](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/file-detection-modes)
- [read_files table-valued function](https://docs.databricks.com/aws/en/sql/language-manual/functions/read_files)
