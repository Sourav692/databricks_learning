# Database/CDC, REST/JSON & Streaming (Kafka / Event Hubs) Ingestion

> **Topic 4.4 · Ingestion — Auto Loader, COPY INTO & Lakeflow Connect** —
> enterprise deep-dive, interview-focused. Runnable end-to-end code for all of
> Topic 4 lives in the consolidated notebook `ingestion_hands_on.py`; snippets
> below are the teaching units.

## What it is

Beyond files, three more ingestion paths into the lakehouse:

- **Database / CDC** — continuously capture inserts/updates/deletes from
  operational DBs (SQL Server, MySQL, Postgres) via **Lakeflow Connect** database
  connectors (CDC + ingestion gateway, see 4.3).
- **REST API & JSON** — pull from HTTP APIs, land the JSON, then parse it (usually
  Auto Loader on the landed files, or a call → `from_json` → Delta).
- **Streaming from message buses** — read **Apache Kafka** / **Azure Event Hubs**
  with Structured Streaming (`format("kafka")`) → Delta.

**Analogy:** files are **mail delivered to a mailbox**; a **message bus** (Kafka)
is a **live radio feed** you tune into; **CDC** is a **security camera** on a
database recording every change as it happens.

## Why it matters

- Real businesses don't only have files — they have **databases and event
  streams**. Knowing the right ingestion path for each is core DE judgment.
- "How do you get **real-time** events / **database changes** into the lakehouse?"
  → Kafka/Event Hubs streaming and **CDC via Lakeflow Connect** are the answers.

**Real-world use case:** IoT sensors publish to Kafka; a Structured Streaming job
reads the topic, parses the JSON `value`, and writes to a bronze Delta table in
near real-time — while a Lakeflow Connect CDC connector mirrors the orders
database into the same lakehouse.

---

## How it works — deep dive

### 1. Streaming from Kafka / Event Hubs — read the source

```python
df = (spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", "host:9092")
        .option("subscribe", "events")            # or subscribePattern / assign
        .option("startingOffsets", "latest")      # "earliest" to backfill
        .option("maxOffsetsPerTrigger", 500000)   # cap per micro-batch (throughput control)
        .load())
```

```sql
-- SQL streaming equivalent via the read_kafka table-valued function
CREATE OR REFRESH STREAMING TABLE cat.sch.bronze_raw AS
SELECT * FROM STREAM read_kafka(bootstrapServers => 'host:9092', subscribe => 'events');
```

- **`subscribe`** (exact topics) / **`subscribePattern`** (regex) / **`assign`**
  (specific partitions) — pick how you select topics.
- **`startingOffsets`** = `latest` (only new) or `earliest` (full backfill);
  **`maxOffsetsPerTrigger`** caps batch size so a backlog doesn't overwhelm you.

### 2. The Kafka schema is binary — you MUST deserialize

The Kafka source returns a fixed schema: **`key` and `value` are `BINARY`**, plus
`topic`, `partition`, `offset`, `timestamp`, `timestampType`. Reading without
parsing gives unusable bytes.

```python
from pyspark.sql.functions import col, from_json
schema = "user_id INT, event STRING, ts TIMESTAMP"
parsed = (df.select(
            from_json(col("value").cast("string"), schema).alias("d"),  # deserialize value
            col("topic"), col("partition"), col("offset"), col("timestamp"))
          .select("d.*", "topic", "partition", "offset", "timestamp"))
```

- Keep the **offset/partition/timestamp** metadata in bronze — it's gold for
  debugging lag and replay.

### 3. Write to Delta — exactly-once via checkpoint

```python
(parsed.writeStream
   .option("checkpointLocation", "/Volumes/cat/sch/vol/_ck_kafka")  # unique per stream
   .trigger(availableNow=True)        # batch-style catch-up; omit for always-on
   .toTable("cat.sch.bronze_events"))
```

- The **checkpoint** gives **exactly-once** and restart safety (same rule as Auto
  Loader — **one checkpoint per stream**).
- Track lag with the streaming metric **`avgOffsetsBehindLatest`** to know how far
  behind real-time you are.
- **Azure Event Hubs** is consumed through the **Kafka protocol** — same
  `format("kafka")` reader with Event Hubs bootstrap servers + SASL options.

### 4. Database CDC — use Lakeflow Connect, don't hand-code

- Mirror an operational DB's inserts/updates/deletes via a **Lakeflow Connect**
  database connector (ingestion gateway + staging + CDC — see 4.3). It lands the
  change stream; you don't hand-roll change tracking.
- Downstream, apply the changes into silver dimensions with **AUTO CDC /
  `APPLY CHANGES`** in a Lakeflow Declarative Pipeline (covered in 5.4) — the
  standard SCD pattern.

### 5. REST API & JSON — land raw, then parse

- HTTP APIs need **auth, pagination, and rate-limit** handling. The robust pattern
  is **land the raw JSON to a UC Volume**, then ingest with **Auto Loader** (4.1)
  or parse inline with `from_json`.

```python
# After landing raw JSON files to a Volume, parse them incrementally with Auto Loader
raw = (spark.readStream.format("cloudFiles")
         .option("cloudFiles.format", "json")
         .option("cloudFiles.schemaLocation", "/Volumes/cat/sch/vol/_api_schema")
         .load("/Volumes/cat/sch/vol/api_landing"))
```

- Prefer a **Lakeflow Connect** connector if one exists for the API (managed auth,
  monitoring) over hand-built calls.

---

## Comparison: pick the path per source

| Source | Mechanism | Notes |
|---|---|---|
| Files in object storage | **Auto Loader / COPY INTO** (4.1–4.2) | incremental file ingest |
| SaaS app / OLTP database | **Lakeflow Connect** (4.3) | managed connector / CDC |
| Kafka / Event Hubs | **Structured Streaming** `format("kafka")` | near real-time events |
| REST / JSON API | land raw → **Auto Loader / from_json** | handle auth/pagination |

## Uses, edge cases & limitations

- **Uses:** real-time event ingestion (Kafka/Event Hubs), database mirroring (CDC),
  API/JSON pulls into bronze.
- **Edge cases:**
  - Kafka `value` is **binary** — forgetting to deserialize gives unusable bytes.
  - `startingOffsets=earliest` on a huge topic **backfills everything** — costly;
    cap with `maxOffsetsPerTrigger`.
  - REST APIs need **auth, pagination, rate-limit** handling — land raw, then parse.
- **Limitations:** message-bus streaming is for **events**, not large file dumps
  (use Auto Loader); hand-built REST ingestion lacks the monitoring of managed
  connectors — prefer Lakeflow Connect where a connector exists.

## Common gotchas

- ❌ Reading Kafka and not **deserializing** `value` (binary) → garbage columns.
- ❌ Using `earliest` offsets by accident → massive unintended backfill.
- ❌ One checkpoint shared across streams → corrupted state (same rule as Auto
  Loader).
- ❌ Hand-coding DB ingestion when a **Lakeflow Connect CDC** connector exists.
- ❌ Dropping the Kafka offset/partition metadata — keep it in bronze for replay.

## References

- [Stream from Apache Kafka — docs](https://docs.databricks.com/aws/en/connect/streaming/kafka)
- [Stream from Azure Event Hubs](https://docs.databricks.com/aws/en/connect/streaming/event-hubs)
- [read_kafka table-valued function](https://docs.databricks.com/aws/en/sql/language-manual/functions/read_kafka)
- [Lakeflow Connect (database/CDC)](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/)
