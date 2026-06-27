# Declarative Pipelines (SDP, was DLT)

> **Topic 5.1 · Lakeflow Spark Declarative Pipelines** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 5
> notebook (built at the last subtopic); snippets below are the teaching units.

## What it is

- **Lakeflow Spark Declarative Pipelines (SDP)** is a framework for building
  **batch and streaming pipelines in SQL or Python** where you **describe the
  result** and Databricks handles **execution and orchestration**.
- It's the **current name for Delta Live Tables (DLT)** — same idea, rebranded and
  extended. Existing DLT pipelines and `import dlt` code keep working.
- You define **datasets** (streaming tables, materialized views) and **flows**;
  SDP resolves dependencies, order, and incremental processing.

**Analogy:** **declarative = a GPS destination, not turn-by-turn directions.** You
say "I want a clean silver orders table from this bronze source"; SDP plans the
route (what to run, in what order, incrementally) instead of you hand-coding each
step and its schedule.

## Why it matters

- Hand-built pipelines mean **you write the orchestration**: task order, retries,
  incremental logic, checkpoints. SDP removes that boilerplate.
- "DLT vs Lakeflow Declarative Pipelines?" and "imperative job vs declarative
  pipeline?" are common interview questions — know the rename and the model.

**Real-world use case:** a medallion pipeline (bronze → silver → gold). You declare
each table and its query; SDP builds the dependency graph, runs them in the right
order, and incrementally updates only what changed on each run.

---

## How it works — deep dive

### 1. The two dataset types — streaming table vs materialized view

| | **Streaming table** (ST) | **Materialized view** (MV) |
|---|---|---|
| Reads | a **streaming** source (`readStream`) | a **batch** query (`read`) |
| Semantics | append/incremental; processes each row once | recomputed to match the query result |
| Use for | bronze/silver ingestion & incremental transforms | gold aggregates / dimensions |
| Reprocess on change | only new data | refreshes the result (incrementally where possible) |

- **Rule of thumb:** ingesting or incrementally transforming new rows → **streaming
  table**; an aggregate/joined result that must always equal its query → **MV**.

### 2. Author in Python (current API)

The Python API now lives in **`pyspark.pipelines`** (import it explicitly).
`import dlt` still works but `pipelines` is the recommended form.

```python
from pyspark import pipelines as dp     # current API (replaces `import dlt`)

@dp.table                                # streaming table — reads a stream
def bronze_orders():
    return (spark.readStream.format("cloudFiles")
              .option("cloudFiles.format", "json")
              .load("/Volumes/main/raw/orders"))

@dp.table
def silver_orders():
    # reference another dataset by name; readStream = incremental
    return spark.readStream.table("bronze_orders").dropDuplicates(["order_id"])

@dp.materialized_view                    # batch MV — reads a snapshot
def gold_daily_revenue():
    return (spark.read.table("silver_orders")
              .groupBy("order_date").sum("amount"))
```

### 3. Author in SQL

```sql
-- streaming table: incremental ingest from files (STREAM = streaming read)
CREATE OR REFRESH STREAMING TABLE bronze_orders
AS SELECT * FROM STREAM read_files('/Volumes/main/raw/orders', format => 'json');

CREATE OR REFRESH STREAMING TABLE silver_orders
AS SELECT * FROM STREAM bronze_orders;          -- depends on bronze → SDP orders it

-- materialized view: a gold aggregate kept in sync with its query
CREATE OR REFRESH MATERIALIZED VIEW gold_daily_revenue
AS SELECT order_date, sum(amount) AS revenue FROM silver_orders GROUP BY order_date;
```

- SDP reads `bronze_orders` inside `silver_orders` → it **derives the DAG and run
  order** automatically. You never write a scheduler.

### 4. How SDP runs it

- **Dependency graph:** SDP parses every dataset's query, builds the DAG, and runs
  datasets in dependency order — across SQL *and* Python in one pipeline.
- **Incremental by default:** streaming tables process only new data;
  materialized views refresh (incrementally when the engine can).
- **Update modes:** a normal **update** processes new data; a **full refresh**
  recomputes from scratch (re-reads all source data — costlier, resets ST state).
- **Development vs production mode:** dev reuses the cluster and doesn't retry (fast
  iteration); production restarts/retries on failure.
- **Serverless** pipelines are the default target; reads from cloud storage **and**
  message buses (Kafka, Kinesis, Pub/Sub, Event Hubs), writes Delta.

### 5. SDP vs Lakeflow Jobs

| | Lakeflow Spark Declarative Pipelines | Lakeflow Jobs (Topic 7) |
|---|---|---|
| Purpose | **build** an ETL pipeline (datasets + flows) | **orchestrate** tasks |
| Unit | streaming tables / materialized views | tasks (notebook, SQL, pipeline, …) |
| Ordering | derived from data dependencies | you wire task dependencies |
| Together | a Job can run an SDP pipeline as one task | calls pipelines + notebooks + SQL |

---

## Uses, edge cases & limitations

- **Uses:** medallion ETL, incremental batch + streaming transforms, pipelines with
  data-quality rules and SCD (later subtopics 5.2–5.4).
- **Edge cases:**
  - Declarative ≠ magic — a wrong dependency or a non-incremental query still costs
    you; **full refresh vs incremental update** behave very differently (full
    refresh re-reads all source data and resets streaming state).
  - Streaming-table source must be append-only for clean incremental semantics;
    updates/deletes upstream may force a full refresh.
- **Limitations:** it's a **framework with its own pipeline runtime/modes** — not
  every arbitrary Spark job fits; for plain orchestration of notebooks/SQL use
  **Lakeflow Jobs** (Topic 7). Docs/UI may still say "DLT" in places.

## Common gotchas

- ❌ Calling it "DLT" as if it's different — **DLT = Lakeflow Spark Declarative
  Pipelines** now; mention the current name.
- ❌ Using `import dlt` in new code — prefer **`from pyspark import pipelines`**
  (the old import still runs).
- ❌ Expecting it to replace **Lakeflow Jobs** — SDP builds the *pipeline*; Jobs
  *orchestrate* pipelines + notebooks + SQL together.
- ❌ Picking a materialized view where you need append/incremental (use a streaming
  table) — or a streaming table for an aggregate that must equal its query (use MV).
- ❌ Writing imperative orchestration inside a declarative pipeline — let SDP derive
  the order from your dataset definitions.

## References

- [Lakeflow Spark Declarative Pipelines — docs](https://docs.databricks.com/aws/en/ldp/)
- [What happened to Delta Live Tables (DLT)?](https://docs.databricks.com/aws/en/ldp/concepts/where-is-dlt)
- [Python language reference (pyspark.pipelines)](https://docs.databricks.com/aws/en/ldp/developer/python-ref)
- [Pipeline concepts](https://docs.databricks.com/aws/en/ldp/concepts/)
