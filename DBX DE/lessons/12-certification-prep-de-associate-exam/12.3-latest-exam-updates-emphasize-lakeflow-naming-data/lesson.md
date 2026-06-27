# Latest Exam Updates — Naming, Data-Level Security & Optimization

> **Topic 12.3 · Certification Prep — DE Associate exam** — deep-dive, exam-update
> awareness. Decomposed by **what changed recently**; each change ships a
> **current-naming code snippet** so you recognize the modern form the exam expects.
> Old names below are deliberate **old→new contrasts**, always paired with the current name.

## What it is

The recent (2025–2026) exam guide leans into areas that **trip up people studying
from older material**:

- **Lakeflow naming** — the rebrand of the data-engineering tools.
- **Data-level security** — row filters, column masks, and **ABAC**.
- **Optimization** — OPTIMIZE, **Liquid Clustering**, Predictive Optimization, query profile.
- **PySpark / Spark-SQL ETL** — still core, expressed with current APIs.

**Analogy:** it's the **"what's new in this version" release notes** — if you studied
last year's manual, these are the buttons that got renamed and the features that got
added, so you don't fail on terminology you actually know.

---

## How it works — what changed, with current code

### 1. Lakeflow naming — the rebrand the exam now expects

**Mechanism:** the data-engineering tools are unified under **Lakeflow**: Connect
(ingest) · Spark Declarative Pipelines/SDP (transform) · Designer (no-code) · Jobs
(orchestrate). The Python pipeline API moved to `pyspark.pipelines` (imported as `dp`).

**Why:** exam questions (and interviewers) use **current names**; a right concept with
a stale name can still cost the question.

**Trade-off:** old names still appear in docs/UI — recognize **both**, answer with the
current one.

| Old name (old courses/dumps) | Current name |
|---|---|
| Delta Live Tables (DLT) | **Lakeflow Spark Declarative Pipelines (SDP)** |
| Workflows / Jobs | **Lakeflow Jobs** |
| (managed connectors) | **Lakeflow Connect** |
| (new visual / NL tool) | **Lakeflow Designer** |
| Repos | **Git folders** |
| `apply_changes()` / `APPLY CHANGES INTO` | **`create_auto_cdc_flow()` / AUTO CDC** |

```python
# Current SDP Python — NOT the legacy "import dlt / @dlt.table / dlt.apply_changes":
from pyspark import pipelines as dp

@dp.table()                                   # was @dlt.table
def orders_clean():
    return spark.readStream.table("bronze.orders")

dp.create_auto_cdc_flow(                       # was dlt.apply_changes (APPLY CHANGES INTO)
    target="silver.customers", source="cdc_feed",
    keys=["id"], sequence_by="ts", stored_as_scd_type=2)
```

### 2. Data-level security — row filters & column masks

**Mechanism:** fine-grained access is enforced with a SQL **UDF** attached as a **row
filter** (limits rows) or **column mask** (transforms a column) — cross-ref Stage 8.3.

**Why:** the **Governance domain is 15%** and recently expanded — row/column security
is heavily tested, beyond plain table GRANTs.

**Trade-off:** filters/masks run per query; keep the UDF logic cheap and correct.

```sql
-- Row filter: a UDF returning BOOLEAN, attached to the table.
CREATE FUNCTION sec.region_filter(region STRING)
  RETURN is_account_group_member('admins') OR region = current_user();  -- simplified
ALTER TABLE prod.sales.orders SET ROW FILTER sec.region_filter ON (region);

-- Column mask: a UDF returning the (possibly masked) column value.
CREATE FUNCTION sec.mask_email(e STRING)
  RETURN CASE WHEN is_account_group_member('pii_readers') THEN e ELSE '***' END;
ALTER TABLE prod.sales.customers ALTER COLUMN email SET MASK sec.mask_email;
```

### 3. ABAC — attribute-based access with governed tags

**Mechanism:** **ABAC** policies apply masks/filters by **tag** (e.g. all columns
tagged `pii`) instead of one-by-one — cross-ref Stage 8.4.

**Why:** the exam expects awareness of tag-driven, scalable governance, not just
per-object grants.

**Trade-off:** ⚠️ verify — manual check required: ABAC **GRANT/CREATE POLICY** features
have been in **Beta**; confirm GA status against docs before assuming exam coverage.

```sql
-- ABAC: one policy masks every column carrying the 'pii' tag value 'email'.
CREATE POLICY mask_pii_email
  ON SCHEMA prod.sales
  COLUMN MASK sec.mask_email
  TO `analysts`
  FOR TABLES
  MATCH COLUMNS hasTagValue('pii', 'email') AS c
  ON COLUMN c;   -- ⚠️ verify exact syntax/GA against current docs
```

### 4. Liquid Clustering — added to the syllabus

**Mechanism:** **Liquid Clustering** replaces hive partitioning + ZORDER with a single
`CLUSTER BY` you can change later — cross-ref Stage 3.

**Why:** it's now the **recommended** layout and shows up in optimization questions
*instead of* partitioning/ZORDER.

**Trade-off:** great default, but it's a clustering strategy — still run `OPTIMIZE`
(or rely on Predictive Optimization) to compact.

```sql
-- Liquid Clustering — the modern layout choice (not PARTITIONED BY / ZORDER):
ALTER TABLE prod.silver.events CLUSTER BY (event_date, region);
OPTIMIZE prod.silver.events;        -- compaction; clustering is applied here
```

### 5. PySpark / Spark-SQL ETL — still core

**Mechanism:** the exam still tests DataFrame/Spark-SQL ETL — reads, transforms,
writes to Delta with the 3-level UC namespace.

**Why:** even with declarative pipelines, the Transformation domain (22%) assumes you
can read/transform/write in PySpark.

**Trade-off:** this plan scopes out Spark *core internals* — review basic
DataFrame/Spark-SQL ETL separately if rusty.

```python
# Bread-and-butter ETL the exam assumes you can write:
df = spark.read.table("main.bronze.orders").filter("amount > 0")
(df.groupBy("region").sum("amount")
   .write.mode("overwrite").saveAsTable("main.silver.revenue_by_region"))
```

---

## Why it matters

- Exam questions use **current names**; old dumps use old ones — a right concept with a
  stale name can still cost you the question.
- The **data-level security (15%)** and **optimization (10%)** domains are heavily
  weighted and recently expanded — exactly what Stages 3 and 8 cover.

**Real-world use case:** a question describes "APPLY CHANGES INTO for SCD2" — you
recognize it as **AUTO CDC** (`create_auto_cdc_flow`) in Lakeflow SDP (Stage 5.4) and
answer correctly, even though the wording mixes old and new terms.

## Uses, edge cases & limitations

- **Uses:** a final "naming & recent-emphasis" pass before the exam; sanity-checking
  that old notes use current terms.
- **Edge cases:** **DLT** still appears in docs/UI and some questions — know it equals
  **Lakeflow SDP**; **ABAC GRANT/CREATE POLICY** has been **Beta** — ⚠️ verify GA.
- **Limitations:** exact emphasis/weights **shift between versions** — this reflects
  the current guide; re-verify on the official exam guide near your date.

## Common gotchas

- ❌ Answering with **old names** (DLT/Workflows/Repos/`apply_changes`) — know the
  current ones.
- ❌ Under-studying **governance (15%)** and **optimization (10%)** — both recently
  emphasized.
- ❌ Treating **Liquid Clustering** as optional — it's the recommended layout and shows
  up over partitioning/ZORDER.
- ❌ Assuming every new feature is GA (e.g. ABAC policies have been Beta) — ⚠️ verify.

## References

- [DE Associate exam guide — official (verify current)](https://www.databricks.com/learn/certification/data-engineer-associate)
- [What happened to Delta Live Tables (DLT)?](https://docs.databricks.com/aws/en/ldp/concepts/where-is-dlt)
- [SDP Python reference (`pyspark.pipelines`)](https://docs.databricks.com/aws/en/ldp/developer/python-ref)
- [`create_auto_cdc_flow` (AUTO CDC)](https://docs.databricks.com/aws/en/ldp/developer/ldp-python-ref-apply-changes)
- [Databricks Data Engineering (Lakeflow)](https://docs.databricks.com/aws/en/data-engineering/)
