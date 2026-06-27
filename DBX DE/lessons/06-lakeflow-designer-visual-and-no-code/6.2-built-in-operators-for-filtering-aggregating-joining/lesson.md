# Lakeflow Designer — Built-in Operators

> **Topic 6.2 · Lakeflow Designer** — enterprise deep-dive, interview-focused.
> UI/no-code tool: each operator family below pairs the **canvas action** with the
> **generated code** (SQL + PySpark) it produces, so you can reason about exactly
> what a visual flow does.

## What it is

- **Operators** are the **building blocks you drag onto the Designer canvas** —
  each is a transformation step, wired source → operators → output.
- The common built-ins map to familiar transforms (Filter, Aggregate, Join,
  Pivot, Transform, Combine, Sort, Limit), with **SQL** and **Python** operators
  (and user-defined operators) as escape hatches.

**Analogy:** operators are **LEGO bricks for data** — each brick does one shaped
job (filter, join, group), and you snap them together into the pipeline you want.

## Why it matters

- Knowing operators map to **standard SQL/PySpark transforms** means you can reason
  about what a visual flow actually does — and when to drop to the **SQL/Python**
  operator for things the visual blocks can't express.
- Shows you understand Designer is a **real transformation tool**, not a toy.

**Real-world use case:** build a gold table visually — **Join** orders to
customers, **Filter** to the last 90 days, **Aggregate** revenue by region, **Sort**
descending, **Limit** to top 10 — no code, but it compiles to a governed pipeline.

---

## How it works — deep dive (operator → generated code)

### 1. Filter — keep matching rows  (≈ `WHERE`)

```sql
SELECT * FROM orders WHERE amount > 0 AND order_date >= current_date() - INTERVAL 90 DAYS;
```
```python
df.filter("amount > 0 AND order_date >= current_date() - INTERVAL 90 DAYS")
```

### 2. Join — combine two tables on a key  (≈ `JOIN`)

Choose the type (inner / left / right / full) and the key/expression in the panel.

```sql
SELECT o.*, c.segment FROM orders o LEFT JOIN customers c ON o.customer_id = c.id;
```
```python
orders.join(customers, orders.customer_id == customers.id, "left")
```

### 3. Aggregate — group + compute  (≈ `GROUP BY` + agg fns)

```sql
SELECT region, sum(amount) AS revenue, count(*) AS n FROM orders GROUP BY region;
```
```python
from pyspark.sql import functions as F
orders.groupBy("region").agg(F.sum("amount").alias("revenue"), F.count("*").alias("n"))
```

### 4. Pivot / Transform / Combine — reshape & shape

```sql
-- Pivot: rows ↔ columns  (≈ PIVOT)
SELECT * FROM orders PIVOT (sum(amount) FOR region IN ('US','EU','APAC'));

-- Transform: select / rename / derive  (≈ SELECT + calculated columns)
SELECT order_id, amount, amount * 0.1 AS tax, upper(region) AS region FROM orders;

-- Combine: union / intersect / except  (≈ set ops)
SELECT * FROM orders_us UNION ALL SELECT * FROM orders_eu;
```

### 5. Custom (SQL / Python) operators & UDFs

When the visual blocks can't express something, use the **SQL** or **Python**
operator inline, or register a **user-defined operator**:

```python
# Python operator: arbitrary PySpark on the upstream DataFrame
from pyspark.sql import functions as F
def transform(df):
    return df.withColumn("clean_email", F.lower(F.trim("email")))
```

---

## The operator cheat-sheet

| Operator | SQL equivalent | Does |
|---|---|---|
| **Filter** | `WHERE` | keep matching rows |
| **Aggregate** | `GROUP BY` + agg fns | summarize groups |
| **Join** | `JOIN` | combine two tables on a key |
| **Pivot** | `PIVOT` / unpivot | rows ↔ columns |
| **Transform** | `SELECT` / derived cols | pick/rename/compute columns |
| **Combine** | `UNION` / `INTERSECT` / `EXCEPT` | merge tables |
| **Sort / Limit** | `ORDER BY` / `LIMIT` | order / cap rows |
| **SQL / Python** | raw query / PySpark | escape hatch for the rest |

## Uses, edge cases & limitations

- **Uses:** assembling filter→join→aggregate flows visually; quick reshaping;
  letting analysts express transforms without writing SQL.
- **Edge cases:** transforms the visual operators don't cover → use the **SQL** or
  **Python** operator, or a **user-defined operator**.
- **Limitations:** heavy/complex logic can become clearer in code than as a long
  operator chain; operators inherit SDP/Designer constraints underneath. **Verify
  the exact operator list/availability in the docs (it evolves).**

## Common gotchas

- ❌ Hunting for a visual operator that doesn't exist — drop to the **SQL/Python**
  operator instead of forcing it.
- ❌ Confusing **Pivot** (reshape rows↔columns) with **Aggregate** (group+compute)
  — pivot reshapes, aggregate summarizes.
- ❌ Confusing **Join** (match on key) with **Combine/union** (stack tables).
- ❌ Building a 20-operator chain when a short **SQL** operator is clearer.

## References

- [Built-in operators — Lakeflow Designer docs](https://docs.databricks.com/aws/en/designer/built-in-operators)
- [Build a transformation](https://docs.databricks.com/aws/en/designer/build-transformation)
- [Lakeflow Designer overview](https://docs.databricks.com/aws/en/designer/)
