# Unity Catalog Functions (UDFs & UDTFs)

> **Topic 8.5 · Unity Catalog — Governance, Security & ABAC** — enterprise
> deep-dive, interview-focused. Runnable end-to-end code for all of Topic 8 lives
> in the consolidated notebook `governance_hands_on.py`; snippets below are the
> teaching units.

## What it is

- **UC functions** are **reusable, governed functions** registered in Unity Catalog
  with a 3-level name `catalog.schema.function`.
- **Scalar UDF** — returns **one value** per row (SQL or Python). Use in
  `SELECT`/`WHERE`.
- **Table function (UDTF)** — `RETURNS TABLE (...)`; returns **a whole table**, used
  in `FROM` like a parameterized view.
- Unlike notebook-scoped UDFs, they **persist** and are **shared** across queries,
  notebooks, and jobs — and governed by **`GRANT EXECUTE`**.

**Analogy:** a UC function is a **saved formula in a shared spreadsheet** — define
`bmi(weight, height)` once, everyone reuses it by name, and you control who can run
it. A **UDTF** is a saved formula that returns a **whole sheet**, not one cell.

## Why it matters

- They put **business logic in one governed place** instead of copy-pasted into
  every notebook — consistent, permissioned, auditable.
- They're the **building block for column masks & row filters** (8.3/8.4) — those
  *are* UC functions. So this ties the governance topic together.

**Real-world use case:** define `prod.util.mask_email(email)` once; reuse it in
queries **and** attach it as a **column mask**. Define a UDTF
`prod.util.top_orders(region, n)` returning a table, and analysts call
`SELECT * FROM prod.util.top_orders('West', 10)`.

---

## How it works — deep dive

### 1. SQL scalar UDF

```sql
CREATE OR REPLACE FUNCTION prod.util.bmi(weight DOUBLE, height DOUBLE)
RETURNS DOUBLE
DETERMINISTIC                         -- same args → same result; helps the optimizer
RETURN weight / (height * height);

SELECT id, prod.util.bmi(weight, height) AS bmi FROM prod.health.patients;
```

### 2. Python UDF (governed, persistent)

For logic SQL can't express — the body is dollar-quoted (`$$ … $$`):

```sql
CREATE OR REPLACE FUNCTION prod.util.normalize_phone(p STRING)
RETURNS STRING
LANGUAGE PYTHON
AS $$
  import re
  return re.sub(r'\D', '', p) if p else None      -- strip non-digits
$$;
```

- Python UDFs can use **PyPI packages / UC volumes**, but run **per row in Python**
  → slower than SQL/built-ins; keep them off hot paths.

### 3. Table function (UDTF) — `RETURNS TABLE`

```sql
CREATE OR REPLACE FUNCTION prod.util.top_orders(region STRING, n INT)
RETURNS TABLE (order_id BIGINT, amount DECIMAL(10,2))
RETURN
  SELECT order_id, amount FROM prod.sales.orders
  WHERE region = top_orders.region          -- params are referenceable by name
  ORDER BY amount DESC LIMIT n;

SELECT * FROM prod.util.top_orders('West', 10);   -- call it in FROM
```

### 4. Governance — `GRANT EXECUTE` + the USE chain

```sql
GRANT EXECUTE ON FUNCTION prod.util.bmi TO `analysts`;
-- caller also needs USE CATALOG prod + USE SCHEMA prod.util (same chain as 8.2)
```

- A 3-level-named function is governed like any UC object. Calling it requires
  `USE CATALOG` + `USE SCHEMA` + **`EXECUTE`**.

### 5. They power masks & filters

- Column masks (8.3) and ABAC policies (8.4) **attach a UC function** to a column /
  table. Writing a good `mask_*` UDF here is exactly what those features consume:

```sql
-- the same kind of function used as a column mask in 8.3 / an ABAC policy in 8.4
CREATE OR REPLACE FUNCTION prod.util.mask_email(e STRING) RETURNS STRING
RETURN CASE WHEN is_account_group_member('pii_readers') THEN e ELSE '***@***' END;
```

---

## Comparison: scalar vs table function vs Python

| | Scalar UDF | Table function (UDTF) | Python UDF |
|---|---|---|---|
| Returns | one value | a table (`RETURNS TABLE`) | one value |
| Used in | `SELECT`/`WHERE` | `FROM` | `SELECT`/`WHERE` |
| Language | SQL or Python | SQL | Python |
| Perf | fast (SQL) | fast (SQL) | slower (per-row Python) |

## Uses, edge cases & limitations

- **Uses:** centralizing business rules; powering **column masks/row filters**;
  parameterized result sets (UDTFs); sharing logic across teams.
- **Edge cases:** Python UDFs are slower than built-ins (per-row Python) — prefer
  SQL/built-ins on hot paths; mark **`DETERMINISTIC`** so the optimizer can reuse
  results (don't mark a function deterministic if it isn't, e.g. uses `current_*`).
- **Limitations:** governed by EXECUTE — callers also need USE on the parent
  catalog/schema (same chain as 8.2). Heavy UDFs in masks/filters hit the perf
  notes from 8.3.

## Common gotchas

- ❌ Re-defining the same logic as a **notebook UDF** everywhere — register it in UC
  once and `GRANT EXECUTE`.
- ❌ Forgetting **EXECUTE** (and USE CATALOG/SCHEMA) → "permission denied" calling it.
- ❌ Using a heavy **Python** UDF on a hot query path where SQL/built-in would do.
- ❌ Confusing a **scalar** UDF (one value, in SELECT) with a **UDTF**
  (`RETURNS TABLE`, in FROM).
- ❌ Marking a non-deterministic function `DETERMINISTIC` → wrong cached results.

## References

- [Unity Catalog functions — docs](https://docs.databricks.com/aws/en/udf/unity-catalog)
- [CREATE FUNCTION (SQL — scalar & table)](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-sql-function)
- [Python UDFs in Unity Catalog](https://docs.databricks.com/aws/en/udf/unity-catalog-python)
