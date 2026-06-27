# Data-Quality Expectations

> **Topic 5.3 · Lakeflow Spark Declarative Pipelines** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 5
> notebook (built at the last subtopic); snippets below are the teaching units.

## What it is

- **Expectations** are **data-quality rules** you attach to an SDP dataset: a
  boolean condition each row must satisfy.
- On violation you choose one of **three actions**:
  - **Warn** (default) — keep the bad row, **record a metric**.
  - **Drop** — **remove** the bad row before writing.
  - **Fail** — **abort the update** (transaction rolls back) until fixed.
- You can also **quarantine** bad rows (route them to a separate table) instead of
  dropping/failing — a *pattern*, not a keyword.

**Analogy:** expectations are a **bouncer at the club door** checking IDs. Warn =
let them in but note it in the logbook; Drop = turn them away quietly; Fail = shut
the whole door until the problem's sorted.

## Why it matters

- Bad data silently flowing to gold breaks dashboards and models. Expectations
  **catch it at the pipeline boundary** with visible metrics.
- "How do you enforce data quality in a pipeline?" → **Expectations**
  (warn/drop/fail/quarantine) is the expected SDP answer.

**Real-world use case:** an orders pipeline drops rows with a null `order_id`,
warns on `amount < 0` (tracked so analysts notice), and fails the update if a
critical reference table is empty — stopping a bad load from reaching gold.

---

## How it works — deep dive

### 1. The three actions and what each does

| Action | Bad row is… | Update | Metric recorded? | Use for |
|---|---|---|---|---|
| **Warn** (default) | **kept** & written | continues | ✅ yes | track quality without blocking |
| **Drop** | **removed** before write | continues | ✅ yes | keep the table clean |
| **Fail** | — | **aborts/rolls back** | ❌ no (aborted) | truly critical invariants |

### 2. SQL syntax

```sql
CREATE OR REFRESH STREAMING TABLE silver_orders (
  CONSTRAINT valid_id   EXPECT (order_id IS NOT NULL) ON VIOLATION DROP ROW,   -- drop
  CONSTRAINT valid_amt  EXPECT (amount >= 0),                                  -- warn (default)
  CONSTRAINT has_date   EXPECT (order_date IS NOT NULL) ON VIOLATION FAIL UPDATE -- fail
)
AS SELECT * FROM STREAM bronze_orders;
```

### 3. Python API (current `pyspark.pipelines`)

```python
from pyspark import pipelines as dp

@dp.table
@dp.expect("valid_amt", "amount >= 0")                 # warn (keep + metric)
@dp.expect_or_drop("valid_id", "order_id IS NOT NULL") # drop
@dp.expect_or_fail("has_date", "order_date IS NOT NULL")# fail (abort)
def silver_orders():
    return spark.readStream.table("bronze_orders")
```

Group many rules with the plural forms (they take a **dict** of name → condition):

```python
RULES = {"valid_id": "order_id IS NOT NULL", "valid_amt": "amount >= 0"}

@dp.table
@dp.expect_all_or_drop(RULES)     # drop a row failing ANY rule
def silver_orders():
    return spark.readStream.table("bronze_orders")
# also: @dp.expect_all (warn), @dp.expect_all_or_fail (abort)
```

- Conditions are **standard SQL booleans** — **no Python UDFs, external calls, or
  subqueries referencing other tables**.
- Expectation **names must be unique** per dataset.
- *(Legacy `@dlt.expect*` still works; prefer the `pyspark.pipelines` form.)*

### 4. The quarantine pattern (keep bad rows for review)

Drop discards bad rows; fail halts the pipeline. To **retain** suspect rows
separately, split the stream on the rules — clean rows to silver, bad rows to a
quarantine table:

```python
from pyspark.sql.functions import expr

RULES = {"valid_id": "order_id IS NOT NULL", "valid_amt": "amount >= 0"}
valid_expr     = " AND ".join(RULES.values())
quarantine_expr = f"NOT({valid_expr})"

@dp.table
def silver_orders():                        # clean rows only
    return spark.readStream.table("bronze_orders").filter(expr(valid_expr))

@dp.table
def quarantined_orders():                   # bad rows kept for review/replay
    return spark.readStream.table("bronze_orders").filter(expr(quarantine_expr))
```

### 5. Metrics & monitoring

- **Warn** and **drop** emit per-expectation **metrics** (failing-row counts) to the
  pipeline UI and the **event log** — query the event log to trend quality over
  time or alert on spikes.
- **Fail** aborts the update, so **no metric is recorded** for that run — you see
  the failure, not a count.

---

## Comparison: pick the action

| Need | Action |
|---|---|
| Track quality but don't block | **warn** (default) |
| Keep the output table clean | **drop** |
| Stop a load that violates a hard invariant | **fail** |
| Keep bad rows to inspect/replay | **quarantine** pattern (split stream) |

## Uses, edge cases & limitations

- **Uses:** validating ingest at bronze/silver; tracking quality trends (warn
  metrics); hard-stopping critical loads (fail); quarantining suspect rows for
  review.
- **Edge cases:**
  - **Fail** rolls back the whole update — use sparingly for truly critical rules.
  - **Quarantine** isn't a single keyword — it's a **pattern** (split the stream on
    the rule expression), so you keep bad rows without dropping or failing.
  - A row failing **any** rule in an `expect_all_or_drop`/`_or_fail` dict triggers
    the action — group rules deliberately.
- **Limitations:** SQL-only boolean logic (no UDFs/external calls/subqueries
  referencing other tables); expectations apply **within SDP pipelines**, not
  arbitrary Spark jobs.

## Common gotchas

- ❌ Using **fail** for non-critical rules → one bad row halts the whole pipeline.
- ❌ Expecting **drop** to keep the bad rows somewhere — it discards them; use the
  **quarantine** pattern to retain them.
- ❌ Trying to call a **Python UDF/subquery** in an expectation — only SQL booleans.
- ❌ Forgetting **warn** still writes the bad rows (it only records a metric).
- ❌ Using legacy `@dlt.expect*` in new code — prefer `from pyspark import pipelines`.

## References

- [Expectations (data quality) — SDP docs](https://docs.databricks.com/aws/en/ldp/expectations)
- [Manage data quality with pipeline expectations](https://docs.databricks.com/aws/en/ldp/expectations)
- [Python language reference (pyspark.pipelines)](https://docs.databricks.com/aws/en/ldp/developer/python-ref)
