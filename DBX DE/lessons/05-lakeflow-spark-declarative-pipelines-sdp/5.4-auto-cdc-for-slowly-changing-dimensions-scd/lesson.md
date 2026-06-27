# AUTO CDC, SCD 1 & 2, Pipeline Modes & Monitoring

> **Topic 5.4 · Lakeflow Spark Declarative Pipelines** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code for all of Topic 5 lives in the
> consolidated notebook `sdp_pipeline_hands_on.py`; snippets below are the
> teaching units.

## What it is

- **AUTO CDC** applies a **change feed** (inserts/updates/deletes) to a target
  table to build **slowly changing dimensions** — automatically, in order. It's the
  current API that **replaces `APPLY CHANGES INTO`** (same syntax).
- **SCD Type 1** — overwrite: keep only the **current** value (no history).
- **SCD Type 2** — keep **full history**: each change adds a row with
  `__START_AT` / `__END_AT` validity timestamps.
- **`SEQUENCE BY`** orders changes so **late/out-of-order** events apply correctly.

**Analogy:** maintaining a customer's address. **SCD1** = a phone contact —
editing the address **erases** the old one. **SCD2** = an address-history logbook
— each move adds a dated entry so you can see where they lived and when.

## Why it matters

- Hand-coding SCD2 MERGE logic (close old row, open new row, handle late data) is
  notoriously error-prone. **AUTO CDC does it declaratively.**
- "Implement SCD Type 2" is a **classic DE interview task** — on Databricks the
  answer is **AUTO CDC … STORED AS SCD TYPE 2**.

**Real-world use case:** a customers change feed (CDC from the orders DB) feeds a
`dim_customers` table **STORED AS SCD TYPE 2** — every address change is versioned
with start/end timestamps, so historical orders join to the address that was
current at the time.

---

## How it works — deep dive

### 1. The mechanism — KEYS + SEQUENCE BY

- **`KEYS`** = the unique business key (e.g. `customer_id`).
- **`SEQUENCE BY`** = a sortable column (timestamp/version) that decides ordering —
  the **highest sequence wins regardless of arrival order**, so late/out-of-order
  events apply correctly. This is the part that makes AUTO CDC robust vs a naive
  MERGE.

### 2. SCD Type 1 vs Type 2

| | SCD Type 1 | SCD Type 2 |
|---|---|---|
| History | none (overwrite) | full (versioned rows) |
| Extra columns | — | `__START_AT`, `__END_AT` |
| Use for | "current value only" dims | audited / point-in-time joins |
| Table growth | flat | grows (a row per change per key) |

- For SCD2, restrict which columns trigger a new version with
  **`TRACK HISTORY ON * EXCEPT (cols)`** — noisy columns update in place instead of
  creating a new row.

### 3. SQL — `AUTO CDC INTO`

```sql
CREATE OR REFRESH STREAMING TABLE dim_customers;

CREATE FLOW cdc_flow AS AUTO CDC INTO dim_customers
FROM stream(customers_changes)
KEYS (customer_id)
APPLY AS DELETE WHEN operation = 'DELETE'      -- handle deletes explicitly
SEQUENCE BY change_ts                            -- ordering; late events resequenced
COLUMNS * EXCEPT (operation)
STORED AS SCD TYPE 2                             -- or SCD TYPE 1 (overwrite)
TRACK HISTORY ON * EXCEPT (last_seen_ts);        -- don't version on noisy cols
```

- `APPLY AS DELETE WHEN` / `APPLY AS TRUNCATE WHEN` define how delete/truncate
  operations in the feed are applied (omit and deletes are missed).

### 4. Python — `create_auto_cdc_flow`

```python
from pyspark import pipelines as dp
from pyspark.sql.functions import col, expr

dp.create_streaming_table("dim_customers")

dp.create_auto_cdc_flow(                          # replaces legacy dlt.apply_changes
    target="dim_customers",
    source="customers_changes",
    keys=["customer_id"],
    sequence_by=col("change_ts"),
    stored_as_scd_type=2,                          # 1 or 2
    apply_as_deletes=expr("operation = 'DELETE'"))
```

For sources that arrive as **full snapshots** (not a change feed), derive the
changes by comparing snapshots — **Python-only**:

```python
dp.create_auto_cdc_from_snapshot_flow(
    target="dim_customers", source="daily_snapshot",
    keys=["customer_id"], stored_as_scd_type=2)
```

### 5. Parametrized pipelines

Make a pipeline reusable across environments by reading **pipeline configuration**
values (set in the pipeline settings or Asset Bundle) at runtime:

```python
# pipeline setting:  {"source_path": "/Volumes/main/raw/customers"}
src = spark.conf.get("source_path")               # read the parameter

@dp.table
def bronze_customers():
    return spark.readStream.format("cloudFiles").option("cloudFiles.format","json").load(src)
```

```sql
-- in SQL, reference a pipeline config value with ${...}
CREATE OR REFRESH STREAMING TABLE bronze_customers
AS SELECT * FROM STREAM read_files('${source_path}', format => 'json');
```

### 6. Pipeline modes & monitoring

- **Triggered** (batch): runs to completion then stops — compute spins down, cheaper.
- **Continuous** (streaming): keeps running for low-latency updates — higher cost.
- **Monitoring:** the pipeline UI shows the DAG, per-dataset row counts, expectation
  (data-quality) metrics, and the **event log** for debugging failures.

---

## Uses, edge cases & limitations

- **Uses:** building dimension tables from CDC feeds; SCD1 for "current only", SCD2
  for audited history; `create_auto_cdc_from_snapshot_flow` to derive changes by
  comparing snapshots; parametrized pipelines for multi-env reuse.
- **Edge cases:**
  - **`SEQUENCE BY` must be sortable**; a bad sequence column → wrong winner on
    out-of-order data.
  - **Deletes need `APPLY AS DELETE WHEN`**, or they're silently missed.
  - SCD2 with `TRACK HISTORY ON *` versions on *every* column change — exclude noisy
    columns or the table explodes.
- **Limitations:** triggered vs continuous trade latency for cost (continuous keeps
  compute running). SCD2 tables grow over time. `FROM SNAPSHOT` is Python-only.

## Common gotchas

- ❌ Using **SCD1** when the business needs history (audits/point-in-time joins) →
  use **SCD2**.
- ❌ Omitting/choosing a bad **`SEQUENCE BY`** → late events overwrite newer data.
- ❌ Forgetting **`APPLY AS DELETE WHEN`** → deletes in the feed are ignored.
- ❌ Running **continuous** mode when **triggered** (batch) would do → wasted cost.
- ❌ Re-implementing SCD2 with a manual `MERGE` when **AUTO CDC** handles it.
- ❌ Using legacy `dlt.apply_changes` / `APPLY CHANGES INTO` in new code — prefer
  **AUTO CDC** / `create_auto_cdc_flow`.

## References

- [AUTO CDC (replaces APPLY CHANGES) — SDP docs](https://docs.databricks.com/aws/en/ldp/cdc)
- [SCD Type 1 & 2 with AUTO CDC](https://docs.databricks.com/aws/en/ldp/cdc)
- [Pipeline update modes (triggered vs continuous)](https://docs.databricks.com/aws/en/ldp/pipeline-mode)
- [Configure pipeline parameters](https://docs.databricks.com/aws/en/ldp/parameters)
- [Monitor pipelines / event log](https://docs.databricks.com/aws/en/ldp/observability)
