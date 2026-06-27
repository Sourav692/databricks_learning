# Map Your Skills to the DE Associate Exam Domains

> **Topic 12.1 · Certification Prep — DE Associate exam** — deep-dive, exam-mapping.
> Decomposed by **exam domain** (not product mechanism): each domain gets its
> **weight**, in-scope skills, the **stage that covers it**, and a **representative
> code snippet** of the kind the exam tests. Verify weights on the official guide
> near your exam date — they change.

## What it is

- The **Databricks Certified Data Engineer Associate** exam is scored across **7
  weighted domains**. Knowing the weights tells you **where to spend study time**.
- This lesson maps each domain to the **topics you've already built** (Stages 1–11)
  and shows one exam-style snippet per domain so you can self-assess fast.

**Analogy:** the exam blueprint is the **grading rubric handed to you in advance** —
you know which sections carry the most points, so you study to the weights instead of
cramming everything equally.

## Logistics (verified — re-check before booking)

- **45 scored questions**, **90 minutes**, multiple choice, **proctored** (online or
  test center). *(Verified on the official exam page.)*
- **Fee $200**; **valid 2 years**; no test aids; languages incl. English/Japanese/
  Portuguese-BR/Korean.
- **Passing grade ≈ 70%** (~32/45) — ⚠️ verify — the official page doesn't publish the
  exact cut score; treat ~70% as a target, not a guarantee.

---

## The 7 domains — weight, coverage & an exam-style snippet

### Domain 1 — Databricks Intelligence Platform · **6%** · (Stage 1)

In scope: Lakehouse concept, workspace/architecture, medallion. Lowest weight — know
the concepts, don't over-invest.

```sql
-- Recognize the 3-level Unity Catalog namespace (a platform fundamental):
SELECT * FROM main.sales.orders;   -- catalog.schema.table
```

### Domain 2 — Data Ingestion and Loading · **21%** · (Stage 4)

In scope: Auto Loader (`cloudFiles`), `COPY INTO`, Lakeflow Connect, incremental
ingestion, schema inference/evolution.

```python
# Auto Loader — incremental file ingestion with schema tracking (high-yield topic):
(spark.readStream.format("cloudFiles")
   .option("cloudFiles.format", "json")
   .option("cloudFiles.schemaLocation", "/Volumes/main/raw/_schema")
   .load("/Volumes/main/raw/orders")
   .writeStream.option("checkpointLocation", "/Volumes/main/raw/_ckpt")
   .toTable("main.bronze.orders"))
```

### Domain 3 — Data Transformation and Modeling · **22%** · (Stages 2, 5, 6)

Highest weight. In scope: Delta tables, `MERGE`, SDP (Lakeflow Declarative
Pipelines), expectations, PySpark/Spark-SQL ETL.

```sql
-- MERGE (upsert) — a classic exam item:
MERGE INTO main.silver.customers t
USING updates s ON t.id = s.id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;
```

### Domain 4 — Working with Lakeflow Jobs · **16%** · (Stage 7)

In scope: tasks & dependencies, scheduling with **Quartz cron**, retries, failure
identification, alerts.

```yaml
# 6-field Quartz cron (NOT 5-field Unix) — frequently tested:
schedule: { quartz_cron_expression: "0 0 2 * * ?", timezone_id: "UTC" }  # 02:00 daily
```

### Domain 5 — Implementing CI/CD · **10%** · (Stage 11)

In scope: Databricks Asset Bundles, Git folders, dev/prod targets, deploy flow.

```bash
databricks bundle validate          # check config (CI on PRs)
databricks bundle deploy -t prod    # promote on merge
```

### Domain 6 — Troubleshooting, Monitoring & Optimization · **10%** · (Stages 3, 7, 10)

In scope: OPTIMIZE/ZORDER/Liquid Clustering, VACUUM, query profile, job run
monitoring, failure diagnosis.

```sql
-- Liquid Clustering — added to the current syllabus (know it):
ALTER TABLE main.silver.events CLUSTER BY (event_date, region);
OPTIMIZE main.silver.events;
```

### Domain 7 — Governance and Security · **15%** · (Stages 8, 9)

In scope: Unity Catalog grants, row filters / column masks, ABAC, Delta Sharing.

```sql
-- A GRANT on the UC 3-level namespace (high-yield governance item):
GRANT SELECT ON TABLE main.sales.orders TO `analysts`;
```

---

## Study-to-the-weights summary

| # | Domain | Weight | Covered by |
|---|---|---|---|
| 1 | Databricks Intelligence Platform | **6%** | Stage 1 |
| 2 | Data Ingestion and Loading | **21%** | Stage 4 |
| 3 | Data Transformation and Modeling | **22%** | Stages 2, 5, 6 |
| 4 | Working with Lakeflow Jobs | **16%** | Stage 7 |
| 5 | Implementing CI/CD | **10%** | Stage 11 |
| 6 | Troubleshooting, Monitoring & Optimization | **10%** | Stages 3, 7, 10 |
| 7 | Governance and Security | **15%** | Stages 8, 9 |

> **Ingestion (21) + Transformation (22) + Jobs (16) = 59%** of the exam — prioritize
> Stages 2, 4, 5, 7. Governance (15%) is the next block. Platform (6%) is last.

## Why it matters

- **Study to the weights:** matching effort to points beats studying everything
  equally — a fixed-time plan should pour the most hours into the 59% core.
- Mapping domains → your stages turns "study everything" into a **targeted checklist**.

**Real-world use case:** with two weeks left, you spend the most time on Stages 2/4/5
(Delta + ingestion + pipelines), a solid block on Stage 8 (governance, 15%), and a
quick pass on Stage 1 (6%) — effort matched to points.

## Uses, edge cases & limitations

- **Uses:** building a weighted study plan; a coverage checklist; deciding what to
  review last.
- **Edge cases:** domain names/weights **change between exam versions** — re-check the
  official guide near your exam date; recent updates added **Liquid Clustering** and
  refreshed **Lakeflow** naming.
- **Limitations:** weightings guide *time allocation*, not depth — a 6% domain can
  still carry a must-get-right question. The exam expects **PySpark/Spark-SQL ETL**
  basics (outside this plan's scope) — review those separately.

## Common gotchas

- ❌ Studying every topic **equally** — weight time to Ingestion + Transformation +
  Jobs (~59%).
- ❌ Trusting old weightings — **verify the current exam guide** before booking.
- ❌ Skipping **PySpark/Spark-SQL ETL** because this plan excludes Spark core — the
  exam still tests it.
- ❌ Ignoring the 15% **Governance** domain — Unity Catalog/security is heavily tested.
- ❌ Writing **5-field Unix cron** for Jobs — Databricks uses **6-field Quartz**.

## References

- [DE Associate certification — official page](https://www.databricks.com/learn/certification/data-engineer-associate)
- [Exam guide PDF (domains & weights — verify current)](https://www.databricks.com/learn/certification/data-engineer-associate)
- [Databricks Data Engineering docs](https://docs.databricks.com/aws/en/data-engineering/)
