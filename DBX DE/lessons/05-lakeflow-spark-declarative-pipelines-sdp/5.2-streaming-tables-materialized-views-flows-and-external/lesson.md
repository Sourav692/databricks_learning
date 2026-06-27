# Streaming Tables, Materialized Views, Flows & Sinks

> **Topic 5.2 · Lakeflow Spark Declarative Pipelines** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 5
> notebook (built at the last subtopic); snippets below are the teaching units.

## What it is

The four building blocks you declare inside an SDP pipeline:

- **Streaming table (ST)** — a Delta table for **incremental/streaming** processing;
  each input row is processed **once** (append-mostly). Great for ingestion.
- **Materialized view (MV)** — **cached, precomputed query results** that
  **refresh** when source data changes. Great for joins/aggregations that must
  stay correct.
- **Flow** — the query that **moves/transforms data into** a streaming table or
  sink. One ST can have **multiple flows** (fan-in).
- **Sink** — an **external destination** (Kafka, Event Hubs, or a Delta/UC table)
  a flow writes to.

**Analogy:** a **streaming table** is a **security camera recording** — it keeps
appending new footage and never re-films the past. A **materialized view** is a
**live scoreboard** — it recomputes the totals whenever the underlying scores
change so it's always correct.

## Why it matters

- **ST vs MV is the #1 SDP interview question.** Pick wrong and you either
  reprocess everything (slow) or serve stale joins (wrong).
- Knowing **flows** and **sinks** shows you understand SDP is more than two table
  types — it can fan multiple inputs into one table and write out to Kafka.

**Real-world use case:** `bronze_events` (ST) ingests clickstream incrementally;
`dim_users` changes daily, so `user_activity` is an **MV** that re-joins events to
current users; an **append flow** also unions a second region's events into one ST,
and a **sink** writes enriched events to Kafka for a downstream service.

---

## How it works — deep dive

### 1. Streaming table vs materialized view

| | Streaming table | Materialized view |
|---|---|---|
| Processing | incremental, row processed **once** | recomputes/refreshes results |
| Reads | a **stream** (`readStream`) | a **batch** snapshot (`read`) |
| Data shape | append-mostly (ingest, streams) | any (joins, aggregations) |
| Latency | **low** | higher (refresh) |
| Correctness on dim changes | may miss late dim updates | **always reflects current dims** |
| Pick when | fast ingest, append-only | joins/aggregates must be correct |

```python
from pyspark import pipelines as dp

@dp.table                                  # streaming table — incremental ingest
def bronze_events():
    return (spark.readStream.format("cloudFiles")
              .option("cloudFiles.format", "json").load(src))

@dp.materialized_view                      # MV — always-correct join to current dim
def user_activity():
    return (spark.read.table("bronze_events")
              .join(spark.read.table("dim_users"), "user_id"))
```

```sql
CREATE OR REFRESH STREAMING TABLE bronze_events
AS SELECT * FROM STREAM read_files('/Volumes/main/raw/events', format => 'json');

CREATE OR REFRESH MATERIALIZED VIEW user_activity
AS SELECT e.*, u.segment FROM bronze_events e JOIN dim_users u USING (user_id);
```

### 2. Flows — and fan-in with append flows

A **flow** is the query feeding a target. The default flow is the one inside a
`@dp.table`. To union **multiple sources into one** streaming table, create the ST
explicitly and attach **append flows**:

```python
dp.create_streaming_table("all_events")    # the target ST (no query of its own)

@dp.append_flow(target="all_events")       # flow 1: US region
def us_events():
    return spark.readStream.table("bronze_events_us")

@dp.append_flow(target="all_events")       # flow 2: EU region — same target
def eu_events():
    return spark.readStream.table("bronze_events_eu")
```

- Each append flow processes **incrementally and independently**; together they
  feed one ST. This is the clean way to combine many topics/regions without a
  giant `UNION` that reprocesses everything.

### 3. Sinks — write out to Kafka / Event Hubs / Delta

A **sink** lets a flow write to an external system. Create it with `create_sink`,
then point an append flow at it:

```python
dp.create_sink(
    name="events_kafka",
    format="kafka",                         # also: "delta", Azure Event Hubs (via Kafka), custom
    options={"kafka.bootstrap.servers": "host:9092", "topic": "enriched_events"})

@dp.append_flow(target="events_kafka")      # write the stream to the sink
def publish_events():
    return spark.readStream.table("user_activity").selectExpr(
        "CAST(user_id AS STRING) AS key", "to_json(struct(*)) AS value")
```

- **Sinks are Python-only** (no SQL) and accept **streaming queries only** —
  remember this constraint; it's a common gotcha.

### 4. Stateful streams need watermarks

- Streaming aggregations/joins keep **state**. Without a **watermark** to bound how
  late data can arrive, that state grows unbounded → memory failures.

```python
@dp.table
def events_per_min():
    from pyspark.sql.functions import window, col
    return (spark.readStream.table("bronze_events")
              .withWatermark("event_ts", "10 minutes")   # bound late data → bounded state
              .groupBy(window(col("event_ts"), "1 minute")).count())
```

---

## Uses, edge cases & limitations

- **Uses:** STs for bronze/silver ingest; MVs for gold aggregates and
  dimension-joined tables; append flows to fan many sources into one ST; sinks to
  push results to external systems.
- **Edge cases:**
  - STs are **append-mostly** — schema changes / reprocessing often need a **full
    refresh** (re-reads all source data, resets state).
  - Unbounded streaming aggregations/joins need **watermarks**, or state grows
    until OOM.
  - A streaming table is **owned/updated by a single pipeline** — don't write to it
    from elsewhere.
- **Limitations:** MV refresh has **latency/cost**; STs can serve "fast-but-wrong"
  if a dimension changed after the row was processed (that's why joins often belong
  in MVs). **Sinks: Python-only, streaming-only.**

## Common gotchas

- ❌ Using a **streaming table** for a dimension join that must stay current → use
  an **MV**.
- ❌ Using an **MV** for high-volume append ingest → use a **streaming table**.
- ❌ Trying to define a **sink in SQL** — sinks are Python-only, streaming-only.
- ❌ Forgetting **watermarks** on unbounded streams → unbounded state / OOM.
- ❌ Writing to a streaming table from **two** pipelines → conflicts (single owner).

## References

- [Streaming tables (SDP concepts) — docs](https://docs.databricks.com/aws/en/ldp/concepts/streaming-tables)
- [Materialized views](https://docs.databricks.com/aws/en/ldp/concepts/materialized-views)
- [Flows (incl. append flows)](https://docs.databricks.com/aws/en/ldp/concepts/flows)
- [Sinks (Kafka / Event Hubs / Delta)](https://docs.databricks.com/aws/en/ldp/concepts/sinks)
- [Python language reference (pyspark.pipelines)](https://docs.databricks.com/aws/en/ldp/developer/python-ref)
