# What a Lakehouse Is

> **Topic 1.1 · Lakehouse & Medallion Foundations** — enterprise deep-dive,
> interview-focused. Conceptual topic, so each sub-topic pairs the **idea** with a
> concrete SQL/code snippet that makes it real (Delta tables, the transaction log,
> the UC namespace, one copy for BI + ML).

## What it is

- A **lakehouse** is one platform that combines the **cheap, open storage of a data
  lake** with the **reliability and fast queries of a data warehouse**.
- Official definition: *"a data management system that combines the benefits of data
  lakes and data warehouses."*
- On Databricks it's delivered as the **Data Intelligence Platform**, built on two
  pillars: **Delta Lake** (storage) + **Unity Catalog** (governance).

**Analogy:** a data warehouse is a *tidy library* (fast to find books, but only takes
a few formats); a data lake is a *cheap warehouse full of unlabeled boxes* (holds
anything, hard to trust). A **lakehouse is the cheap warehouse with a librarian and a
catalog bolted on** — keep everything, still find and trust it.

## Why it matters

- The old way needed **two systems**: a data lake for raw/ML data + a separate
  warehouse for BI → **copies, extra ETL, drift, and double cost**.
- A lakehouse is **one copy of data, one governance model**, serving BI, data
  engineering, streaming, and ML/AI together.

**Real-world use case:** a retailer lands raw clickstream + orders in cheap cloud
storage, refines it in place, and serves the *same* tables to a BI dashboard, a
forecasting ML model, and ad-hoc SQL — no separate warehouse to copy into.

---

## How it works — deep dive

### 1. The problem — the two-system split

**Mechanism:** classically you kept a **data lake** (cheap object storage, open
formats, good for ML) *and* a separate **data warehouse** (fast BI SQL, ACID), with
ETL copying curated data from lake → warehouse.

**Why it hurts:** two systems means **two copies**, an extra ETL hop, **staleness**
(the warehouse lags the lake), drift between them, and **double cost/governance**.

**Trade-off:** the warehouse gave reliability; the lake gave cost/flexibility — you
couldn't get both without duplicating.

```text
# The old two-system pain (what the lakehouse removes):
S3/ADLS lake (raw, ML)  --nightly ETL copy-->  Warehouse (BI)
   ^ one copy of truth                              ^ a second, stale copy
   = extra pipeline, drift, double storage + double governance
```

### 2. The lakehouse idea — one open storage layer

**Mechanism:** keep data **once** in cheap object storage in an **open table format**
(Parquet files managed by Delta Lake), and run *both* BI SQL and ML directly on it —
no copy into a separate warehouse.

**Why:** one copy = one source of truth, no copy-ETL, no drift, one governance model.

**Trade-off:** you must adopt **Delta + Unity Catalog** to get warehouse behavior;
plain files in a bucket are "just a lake" again.

```sql
-- One governed table in open format — BI and ML both read THIS, no warehouse copy.
CREATE TABLE main.sales.orders (
  order_id BIGINT, region STRING, amount DECIMAL(10,2), order_ts TIMESTAMP
) USING DELTA;                     -- Delta is the default format on Databricks
```

### 3. The enabler — an open table format with a transaction log

**Mechanism:** Delta Lake wraps the Parquet files with a **transaction log** that adds
**ACID transactions** (all-or-nothing writes), **schema enforcement**, and **version
history** — so lake storage behaves like a reliable warehouse table.

**Why:** ACID + the log are *the* differentiators — concurrent writers don't corrupt
data, and you can audit/roll back.

**Trade-off:** the log enables **time travel** (great for audit/recovery) but old
versions consume storage until `VACUUM` (covered in Stage 2/3).

```sql
-- The transaction log gives reliability + time travel for free:
DESCRIBE HISTORY main.sales.orders;                 -- every versioned commit
SELECT * FROM main.sales.orders VERSION AS OF 3;    -- read the table as of version 3
```

### 4. Governance & decoupled compute — Unity Catalog

**Mechanism:** **Unity Catalog** governs every table/file/model with the **3-level
namespace** `catalog.schema.table`, plus lineage and grants. Compute is **decoupled**:
Spark, Photon, and SQL warehouses all read the *same* governed data and scale
independently of storage.

**Why:** one governance model across all engines, and you size compute to the workload
without moving data.

**Trade-off:** governance must be set up (catalogs/schemas/grants) — but it's one model
for the whole estate, not per-system silos.

```sql
-- One governance model over the single copy; engines read the same table.
GRANT SELECT ON TABLE main.sales.orders TO `analysts`;   -- UC access control
-- A SQL warehouse, a Spark job, and an ML notebook all query main.sales.orders.
```

### 5. One copy → BI *and* ML together

**Mechanism:** the same governed Delta table serves a **BI dashboard** (SQL) and a
**model** (DataFrame) — no export, no second system.

**Why:** removes the export/copy step entirely; the model trains on exactly what BI
reports on.

**Trade-off:** analytical sweet spot — for high-frequency single-row OLTP lookups use
an operational store (e.g. Lakebase), not the analytical lakehouse.

```python
# BI does this in SQL:  SELECT region, sum(amount) FROM main.sales.orders GROUP BY region
# ML reads the SAME table — no copy into a separate system:
df = spark.read.table("main.sales.orders")           # one source of truth
features = df.groupBy("region").sum("amount")         # same data BI reports on
```

---

## Lakehouse vs Warehouse vs Lake

| | Data Warehouse | Data Lake | **Lakehouse** |
|---|---|---|---|
| Storage cost | High | Low | **Low** |
| Open formats | Usually no | Yes | **Yes** |
| ACID / reliability | Yes | No | **Yes** |
| BI / fast SQL | Yes | Weak | **Yes** |
| ML / AI on raw data | Weak | Yes | **Yes** |
| Governance | Strong | Weak | **Strong (Unity Catalog)** |

## Uses, edge cases & limitations

- **Uses:** unified BI + ML + streaming on one copy of data; replacing a
  lake-plus-warehouse stack; building the bronze→silver→gold medallion flow (1.3).
- **When NOT to lead with it:** a tiny single-source BI need with no ML/streaming may
  not need the full platform — but on Databricks the lakehouse is the default substrate.
- **Edge cases:** very low-latency single-row lookups (OLTP) are not its sweet spot —
  that's an operational DB job (e.g. Lakebase), not the analytical lakehouse.
- **Limitations:** the benefits depend on using **Delta + Unity Catalog**; dumping
  plain files in object storage with no Delta/UC is "just a data lake" again.

## Common gotchas

- ❌ Calling it "just a data lake with SQL." The differentiators are **ACID +
  governance**, not the query engine.
- ❌ Assuming you must pick lake *or* warehouse — the whole point is **one** system.
- ❌ Expecting lakehouse tables to behave like an OLTP database for high-frequency
  point updates.
- ❌ Forgetting that the value comes from **Delta + UC** — open files alone aren't a
  lakehouse.

## References

- [What is a data lakehouse? — Databricks docs](https://docs.databricks.com/aws/en/lakehouse/)
- [Delta Lake](https://docs.databricks.com/aws/en/delta/)
- [Unity Catalog](https://docs.databricks.com/aws/en/data-governance/unity-catalog/)
- [Work with Delta Lake table history (time travel)](https://docs.databricks.com/aws/en/delta/history)
