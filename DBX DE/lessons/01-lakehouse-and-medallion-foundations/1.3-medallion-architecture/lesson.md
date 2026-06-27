# Medallion Architecture

> **Topic 1.3 · Lakehouse & Medallion Foundations** — enterprise deep-dive,
> interview-focused. Each layer pairs the **idea** with a representative
> enterprise-shaped snippet (Auto Loader bronze, MERGE/expectations silver, CTAS
> gold) on the UC 3-level namespace. The full hands-on lives in Stages 4–5.

## What it is

- A **layered data design** that improves data quality step by step as it flows
  through three layers: **Bronze → Silver → Gold** (a.k.a. "multi-hop").
- Each layer denotes a **quality level**; you build each on top of the previous.

| Layer | Holds | Quality |
|---|---|---|
| 🥉 **Bronze** | Raw, as-ingested data (from S3, Kafka, connectors) | Unvalidated |
| 🥈 **Silver** | Cleaned, deduped, conformed, joined | Validated |
| 🥇 **Gold** | Business-ready aggregates & dimensional models | Curated |

**Analogy:** an **ore refinery** — bronze is the raw ore dumped from the truck,
silver is the smelted/purified metal, gold is the finished product on the shelf. You
never throw away the ore (you can re-refine it differently later).

## Why it matters

- **Debuggable & trustworthy pipelines:** when gold looks wrong, you can trace back
  through silver to bronze to find where it broke.
- **Reprocessing:** because bronze keeps raw data, you can rebuild silver/gold with
  new logic without re-pulling from the source.
- **Right data for the right user:** engineers work in bronze/silver, analysts and
  execs consume gold.

**Real-world use case:** raw orders + clickstream land in **bronze**; **silver**
dedupes and joins them into a clean per-order table; **gold** aggregates daily
revenue per region for the BI dashboard and the forecasting model.

---

## How it works — deep dive

### 1. Bronze — raw, append-only ingest

**Mechanism:** land data **as-ingested** with minimal transformation — keep fields
permissive so unexpected schema changes don't drop data, and add **provenance**
columns (source file, ingest time). Auto Loader (`cloudFiles`) is the typical
incremental ingester.

**Why:** bronze is your **replayable source of truth** — if downstream logic
changes, you rebuild from bronze instead of re-pulling from the origin.

**Trade-off:** don't clean here — heavy transforms in bronze defeat reprocessing and
auditability.

```python
# Bronze: incremental append ingest with provenance (Auto Loader).
(spark.readStream.format("cloudFiles")
   .option("cloudFiles.format", "json")
   .option("cloudFiles.schemaLocation", "/Volumes/main/raw/_schema")
   .load("/Volumes/main/raw/orders")
   .selectExpr("*", "_metadata.file_path AS src_file", "current_timestamp() AS ingest_ts")
   .writeStream.option("checkpointLocation", "/Volumes/main/raw/_ckpt")
   .toTable("main.bronze.orders"))     # append-only, raw
```

### 2. Silver — cleaned, conformed, deduped, joined

**Mechanism:** cleanse, cast types, handle nulls, **dedupe**, enforce schema, and
join related entities — typically with **`MERGE`** (upsert) so re-runs are
idempotent. Keep at least one **non-aggregated** validated record.

**Why:** silver is the reliable, query-ready base every downstream use case shares.

**Trade-off:** don't aggregate here — aggregates belong in gold so many gold marts
can reuse one silver table.

```sql
-- Silver: idempotent upsert of cleaned, deduped records into a conformed table.
MERGE INTO main.silver.orders t
USING (
  SELECT DISTINCT order_id, region, CAST(amount AS DECIMAL(10,2)) AS amount, order_ts
  FROM main.bronze.orders WHERE order_id IS NOT NULL    -- clean + dedupe
) s
ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;
```

### 3. Gold — business-level aggregates & marts

**Mechanism:** aggregate and model for the business (KPIs, star schemas). Multiple
gold tables read from the same silver; gold is what BI and ML consume.

**Why:** gold serves fast, curated answers without every consumer re-deriving the
same logic.

**Trade-off:** gold is purpose-built per use case — expect several gold tables off
one silver, not one giant table.

```sql
-- Gold: a business-ready aggregate the dashboard/model reads directly.
CREATE OR REPLACE TABLE main.gold.daily_revenue_by_region AS
SELECT region, date(order_ts) AS day, sum(amount) AS revenue, count(*) AS orders
FROM main.silver.orders
GROUP BY region, date(order_ts);
```

### 4. Why layering — refinement, reprocessing, quality gates

**Mechanism:** each hop is a **separation of concerns** with its own quality bar;
**quality gates** (e.g. SDP **expectations**) drop/quarantine bad rows between layers.

**Why:** layering makes pipelines debuggable (trace gold→silver→bronze), reprocessable
(rebuild from bronze), and lets the right user consume the right layer.

**Trade-off:** medallion is an **organizing pattern, not a feature** — it doesn't
enforce quality itself; you implement the gates. More layers = more storage/latency.

```python
# Quality gate between bronze→silver in a Lakeflow SDP pipeline (expectations):
from pyspark import pipelines as dp

@dp.table()
@dp.expect_or_drop("valid_amount", "amount > 0")   # drop bad rows at the gate
def silver_orders():
    return spark.readStream.table("main.bronze.orders")
```

### 5. How it maps to the lakehouse flow

**Mechanism:** every layer is a **Delta table** in the UC namespace
(`catalog.bronze/silver/gold.table`); Auto Loader feeds bronze, SDP/jobs build
silver→gold, and gold serves BI/ML — one copy, one governance model.

**Why:** the pattern rides directly on Delta + Unity Catalog (1.1) and the compute
planes (1.2) — no extra system.

**Trade-off:** not every feed needs all three hops — a simple source may go
bronze→gold; the names are a convention, adapt to freshness/cost.

```sql
-- The three layers are just governed Delta tables in the UC 3-level namespace:
--   main.bronze.orders   (raw, append)     ← Auto Loader
--   main.silver.orders   (clean, deduped)  ← MERGE / SDP + expectations
--   main.gold.daily_revenue_by_region      ← aggregate, serves BI + ML
SELECT * FROM main.gold.daily_revenue_by_region;   -- what the dashboard queries
```

---

## Uses, edge cases & limitations

- **Uses:** the default structure for ETL on the lakehouse; pairs directly with Auto
  Loader (bronze ingest) and Lakeflow Declarative Pipelines (silver/gold).
- **Edge cases:** not every pipeline needs all three layers — a simple feed might go
  bronze→gold; very large orgs may add sub-layers. The names are a convention, not a
  hard rule.
- **Limitations:** medallion is an **organizing pattern, not a feature** — it doesn't
  enforce quality by itself. You still implement validation (e.g. Expectations in
  pipelines). More layers = more storage and latency.

## Common gotchas

- ❌ Aggregating in **silver**. Keep silver non-aggregated; aggregates belong in
  **gold** so many use cases can reuse silver.
- ❌ Over-cleaning **bronze**. Bronze should stay raw/auditable — heavy transforms
  there defeat reprocessing.
- ❌ Treating layer names as mandatory. They're a quality convention; adapt to your
  data freshness and cost needs.
- ❌ Expecting the pattern to enforce quality — you implement the gates (expectations,
  constraints) yourself.

## References

- [Medallion architecture — Databricks docs](https://docs.databricks.com/aws/en/lakehouse/medallion)
- [Delta Lake](https://docs.databricks.com/aws/en/delta/)
- [Auto Loader (`cloudFiles`)](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/)
- [Lakeflow Declarative Pipelines (expectations)](https://docs.databricks.com/aws/en/ldp/)
