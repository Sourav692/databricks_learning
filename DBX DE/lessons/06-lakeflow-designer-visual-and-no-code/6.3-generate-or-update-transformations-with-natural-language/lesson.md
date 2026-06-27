# Genie Code — Natural-Language Transformations

> **Topic 6.3 · Lakeflow Designer** — enterprise deep-dive, interview-focused.
> AI/UI feature: each section pairs the **prompt** with the **generated code** it
> produces, and stresses the **review-before-ship** discipline.

## What it is

- **Genie Code** is Databricks' **AI-native coding assistant**: you describe a data
  task in **plain language** and it **generates or updates code, pipelines, and
  dashboards**.
- Inside **Lakeflow Designer** it builds/edits transformations from prompts
  (instead of dragging every operator) — and the output is **governed code**.
- It works across the platform (notebooks, SQL editor, **Lakeflow Pipelines**,
  dashboards, MLflow), uses **Unity Catalog metadata** to understand your data, and
  **respects your UC permissions**.

**Analogy:** Genie Code is a **junior data engineer who pair-programs with you** —
you say "give me monthly revenue per region," it writes the transform; you keep it,
tweak it, or say "now add last year for comparison."

## Why it matters

- It collapses "what I want" → "working transform" without hand-writing every step,
  while staying **inside the governed lakehouse**.
- The AI-native, NL-to-pipeline story is a **hot 2026 interview theme** — knowing
  Genie Code (and that it's permission-aware) shows current platform fluency.

**Real-world use case:** an analyst in Designer types "join orders to customers,
keep the last 90 days, and total revenue by region" — Genie Code generates the
transform; they refine with "exclude refunded orders," review it, and ship a
governed pipeline.

---

## How it works — deep dive

### 1. Prompt → transformation (with iterative refinement)

You describe intent; Genie Code generates the transform and **updates it on
follow-up prompts** — conversational, not one-shot.

```sql
-- Prompt: "total revenue by region"
SELECT region, SUM(amount) AS revenue
FROM orders
GROUP BY region;
```

```sql
-- Follow-up: "join customers, only the last 90 days, and exclude refunds"
SELECT region, SUM(amount) AS revenue
FROM orders o JOIN customers c USING (customer_id)
WHERE o.order_date >= current_date() - INTERVAL 90 DAYS
  AND o.status <> 'refunded'
GROUP BY region;
```

```python
# Genie Code can emit the PySpark equivalent (e.g. for a Designer Python operator)
from pyspark.sql import functions as F
(orders.join(customers, "customer_id")
       .where("order_date >= current_date() - INTERVAL 90 DAYS AND status <> 'refunded'")
       .groupBy("region").agg(F.sum("amount").alias("revenue")))
```

### 2. UC-aware & permission-bound

- It reads **Unity Catalog metadata** (table/column names, types, comments) to
  ground suggestions — so prompts can reference your real schema.
- It **only accesses data you're allowed to see** — governance is not bypassed;
  Genie Code can't surface tables/rows your grants don't permit.

### 3. Agent mode & where it works

- **Agent mode** (in Lakeflow Pipelines) can autonomously build an SDP/ETL pipeline
  from a prompt; elsewhere it gives **inline suggestions, error diagnostics, and
  quick fixes**.
- Surfaces: notebooks, SQL editor, Lakeflow Pipelines/Designer, dashboards, MLflow.

### 4. The review discipline (enterprise-critical)

- **Treat generated code like a junior engineer's PR.** AI can produce
  plausible-but-wrong logic — **review before shipping**, especially:
  - **Joins** (wrong key/grain → row explosion or silent dupes),
  - **Aggregations** (double-counting after a fan-out join),
  - **Filters / null handling / time windows** (off-by-one day, timezone),
  - **deletes/overwrites** in generated write steps.
- Because the output is **real, governed code**, you can diff it, review it in a PR,
  and version it in Git — make that part of the workflow, not optional.

### 5. The Genie family (don't conflate)

| Member | For | Does |
|---|---|---|
| **Genie Code** | data engineers / analysts | NL → **code/pipelines/dashboards** (this lesson) |
| **Genie** (AI/BI) | business users | NL **Q&A over data** (analytics) |
| **Genie Spaces** | data teams | configure **trusted metrics/governance** behind the experiences |

---

## Uses, edge cases & limitations

- **Uses:** drafting/editing Designer transforms by prompt; scaffolding SDP
  pipelines; quick dashboard/notebook code; assisted debugging.
- **Edge cases:** AI output **must be reviewed** — treat it like a junior's PR
  (verify logic, especially joins/aggregations and edge cases).
- **Limitations:** it's a **"Designated Service"** with **regional data-residency**
  rules — **features vary by region**; ⚠️ verify availability (preview vs GA) in
  your workspace/docs. Permission-bound, so it can't surface data you can't already
  see.

## Common gotchas

- ❌ Shipping Genie-generated transforms **unreviewed** — always check the logic.
- ❌ Assuming it bypasses governance — it's **UC permission-aware**, not a back door.
- ❌ Expecting identical features everywhere — **availability varies by region**;
  confirm in the docs.
- ❌ Confusing **Genie Code** (build code/pipelines) with **Genie** AI/BI (business
  Q&A) — different parts of the Genie family.

## References

- [Genie Code — Databricks docs](https://docs.databricks.com/aws/en/genie-code/)
- [Lakeflow Designer (Genie integration)](https://docs.databricks.com/aws/en/designer/)
- [Genie (AI/BI)](https://docs.databricks.com/aws/en/genie/)
