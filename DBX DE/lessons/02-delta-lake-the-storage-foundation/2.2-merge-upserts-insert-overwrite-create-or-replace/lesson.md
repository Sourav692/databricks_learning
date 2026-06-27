# MERGE, INSERT OVERWRITE, CREATE OR REPLACE & CTAS

> **Topic 2.2 · Delta Lake — the storage foundation** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code also lives in the consolidated
> Topic 2 notebook; the snippets below are the teaching units for each sub-topic.

## What it is

Four ways to write to a Delta table — pick by **how much** you're changing:

| Statement | What it does | Granularity | Reach for it when |
|---|---|---|---|
| **MERGE INTO** | Upsert: update/insert/delete rows by a match key | **Row-level** | Some rows changed (CDC, SCD, dedup) |
| **INSERT OVERWRITE** | Replace all rows, or a partition, atomically | Table / partition | Full or partition reload |
| **CREATE OR REPLACE TABLE** | Rebuild the table definition + data, keep the name | Whole table | Schema reset / clean rebuild |
| **CTAS** (`CREATE TABLE AS SELECT`) | Create a new table from a query | New table | Deriving a new (e.g. silver/gold) table |

**Analogy (a contacts app):**
- **MERGE** = *sync* — update changed contacts, add new ones, delete removed ones.
- **INSERT OVERWRITE** = *wipe the list and paste a fresh export*.
- **CREATE OR REPLACE** = *reset the whole app's data and set it up again*.
- **CTAS** = *save a filtered copy as a brand-new list*.

## Why it matters

- Picking the wrong one is a classic, expensive mistake: people **full-overwrite**
  when a targeted **MERGE** (touching only changed rows) is cheaper and safer.
- **MERGE is the backbone of CDC and slowly changing dimensions (SCD)** — a
  near-guaranteed interview topic. You should be able to write SCD Type 1 *and*
  Type 2 from memory.

**Real-world use case:** a nightly feed of changed customers → a single **MERGE**
updates existing customers, inserts new ones, and (optionally) expires departed
ones — in one atomic statement, without rewriting the whole table.

---

## How it works — deep dive

### 1. MERGE — the one that matters

MERGE matches a **source** (the incoming changes) against a **target** (the Delta
table) on a key, then applies up to three kinds of clause:

```sql
MERGE INTO customers t                       -- target (the Delta table)
USING customer_updates s                     -- source (incoming changes)
ON t.id = s.id                               -- the match key (the "join")
WHEN MATCHED        THEN UPDATE SET *         -- in both → update existing
WHEN NOT MATCHED    THEN INSERT *             -- source only → add new
WHEN NOT MATCHED BY SOURCE THEN DELETE;       -- target only → remove gone rows
```

- **`WHEN MATCHED`** → row exists in both → `UPDATE` or `DELETE`. Can carry an
  extra condition and you can have multiple matched clauses (first match wins).
- **`WHEN NOT MATCHED`** → in source only → `INSERT`.
- **`WHEN NOT MATCHED BY SOURCE`** → in target only → `UPDATE`/`DELETE`. **Cannot
  reference source columns** (there is no source row). *Requires DBR 12.2 LTS+ /
  Databricks SQL.* This is how you expire/delete rows that disappeared upstream.
- All clauses commit as **one atomic transaction** via the Delta log — no reader
  ever sees a half-applied merge.

**Conditional clauses** let one MERGE do several things at once:

```sql
MERGE INTO customers t
USING customer_updates s
ON t.id = s.id
WHEN MATCHED AND s.op = 'DELETE'        THEN DELETE
WHEN MATCHED AND s.op = 'UPDATE'        THEN UPDATE SET name = s.name, tier = s.tier
WHEN NOT MATCHED AND s.op <> 'DELETE'   THEN INSERT (id, name, tier) VALUES (s.id, s.name, s.tier);
```

#### Gotcha that fails the merge: multiple source matches

If **more than one source row matches the same target row**, the merge **errors
out** (it can't decide which update wins). Dedup the source to **one row per key**
first — typically keep the latest by an event timestamp:

```python
from pyspark.sql import functions as F, Window
w = Window.partitionBy("id").orderBy(F.col("event_ts").desc())
deduped = (raw_updates
    .withColumn("rn", F.row_number().over(w))
    .filter("rn = 1").drop("rn"))      # exactly one row per key → safe to MERGE
```

#### Performance: always prune the target

MERGE rewrites the **files** that contain matched rows. On a large table, give it
a predicate so it only touches recent files instead of scanning everything:

```sql
MERGE INTO orders t
USING daily_changes s
ON t.id = s.id
   AND t.order_date >= current_date() - INTERVAL 7 DAYS   -- prunes old files
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;
```

- The `t.order_date >=` predicate lets Delta **file-skip** (see Topic 2.1) so the
  merge reads/rewrites a fraction of the table.
- **Deletion vectors** (merge-on-read, table property `delta.enableDeletionVectors`)
  further cut cost by marking rows deleted instead of rewriting whole files.

#### Schema evolution inside MERGE

By default MERGE enforces schema. To let new source columns flow into the target,
opt in — per statement or via a session conf:

```sql
SET spark.databricks.delta.schema.autoMerge.enabled = true;   -- session-wide
-- ...then run the MERGE; new columns in the source are added to the target.
```

#### The PySpark equivalent (DeltaTable API)

```python
from delta.tables import DeltaTable
tgt = DeltaTable.forName(spark, "main.sales.customers")
(tgt.alias("t")
   .merge(deduped.alias("s"), "t.id = s.id")
   .whenMatchedUpdateAll()
   .whenNotMatchedInsertAll()
   .whenNotMatchedBySourceDelete()
   .execute())
```

#### Worked example: SCD Type 2 (keep history)

SCD2 keeps a full history by **closing** the old row and **inserting** a new
current row. The trick is a two-pass source so the changed key both closes the
old version and opens the new one:

```sql
MERGE INTO dim_customer t
USING (
  -- pass 1: the changed row, keyed normally (closes the old version)
  SELECT id AS merge_key, * FROM customer_updates
  UNION ALL
  -- pass 2: same change with NULL key so it can't match → forces an INSERT
  SELECT NULL AS merge_key, * FROM customer_updates u
  JOIN dim_customer d ON u.id = d.id AND d.is_current = true
  WHERE u.tier <> d.tier
) s
ON t.id = s.merge_key AND t.is_current = true
WHEN MATCHED AND t.tier <> s.tier THEN
  UPDATE SET is_current = false, end_date = current_date()   -- close old version
WHEN NOT MATCHED THEN
  INSERT (id, tier, is_current, start_date, end_date)
  VALUES (s.id, s.tier, true, current_date(), NULL);         -- open new version
```

### 2. INSERT OVERWRITE — full & partition reloads

Atomically swap a table's (or a partition's) contents. The old data is replaced in
a single commit, so readers never see an empty/partial table.

```sql
-- Full table reload (replace everything atomically)
INSERT OVERWRITE main.sales.orders SELECT * FROM staging.orders_full;
```

**Targeted overwrite with `replaceWhere`** — replace just a slice (e.g. one day)
without rewriting the whole table. The data you write **must** satisfy the
predicate or the write fails (a safety guard):

```python
(corrected_day.write.format("delta").mode("overwrite")
   .option("replaceWhere", "order_date = '2024-01-07'")   # only this day is replaced
   .saveAsTable("main.sales.orders"))
# Fails if any row's order_date != 2024-01-07 (constraint check protects you).
```

**Dynamic partition overwrite** — replace only the partitions present in the new
data, leaving others untouched:

```python
spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
(new_partitions.write.format("delta").mode("overwrite")
   .saveAsTable("main.sales.orders"))   # only the partitions in new_partitions change
```

> You can't combine `replaceWhere` and `partitionOverwriteMode=dynamic` in the
> same write — pick one targeting strategy.

### 3. CREATE OR REPLACE TABLE — atomic rebuild

Redefine + repopulate a table in one atomic commit, **keeping the table name and
its history lineage**. Use it to reset schema cleanly or rebuild from source.

```sql
CREATE OR REPLACE TABLE main.sales.dim_date AS
SELECT d AS date_key, year(d) AS yr, month(d) AS mo
FROM (SELECT explode(sequence(DATE'2020-01-01', DATE'2030-12-31', INTERVAL 1 DAY)) AS d);
```

- **Prefer over `DROP TABLE` + `CREATE`**: DROP+CREATE loses the version history
  and breaks anything referencing the old table during the gap; CREATE OR REPLACE
  is atomic and preserves lineage.

### 4. CTAS — derive a new table from a query

`CREATE TABLE AS SELECT` builds a **new** table, inferring schema from the query.
The default workhorse for silver/gold derived tables.

```sql
CREATE TABLE main.sales.gold_daily_revenue AS
SELECT order_date, sum(amount) AS revenue
FROM main.sales.orders
GROUP BY order_date;

-- Want the structure but not the data? Copy the schema only:
CREATE TABLE main.sales.orders_empty LIKE main.sales.orders;
```

---

## Comparison: pick the smallest tool that fits

| | Rows touched | Atomic | Keeps history | Typical use |
|---|---|---|---|---|
| **MERGE** | only matched files | ✅ | ✅ | CDC, SCD, upserts, dedup |
| **INSERT OVERWRITE** | all / a partition | ✅ | ✅ | full / partition reload |
| **CREATE OR REPLACE** | whole table | ✅ | ✅ (vs DROP+CREATE) | schema reset / rebuild |
| **CTAS** | new table | ✅ | n/a (new) | derived silver/gold table |

**Decision rule:** *only some rows changed* → **MERGE**. *Whole partition/table is
stale* → **INSERT OVERWRITE**. *Schema/definition must change* → **CREATE OR
REPLACE**. *Building a new derived table* → **CTAS**.

## Uses, edge cases & limitations

- **Uses:** MERGE → CDC, upserts, SCD1/SCD2, insert-only dedup; INSERT OVERWRITE →
  full/partition reloads & backfills; CREATE OR REPLACE → clean rebuilds; CTAS →
  new derived tables.
- **Edge cases:**
  - **Multiple source rows per key → MERGE fails.** Dedup to one row per key first.
  - **`replaceWhere` data must match the predicate**, or the write errors (guard
    against accidentally clobbering the wrong partition).
  - **Dynamic partition overwrite is per-partition** — a single misplaced row can
    overwrite a whole partition; validate partition values before writing.
- **Limitations:** `WHEN NOT MATCHED BY SOURCE` can't reference source columns and
  needs DBR 12.2 LTS+. INSERT OVERWRITE / CREATE OR REPLACE rewrite far more data
  than a targeted MERGE — don't reach for them when only a few rows changed.

## Common mistakes / gotchas

- ❌ **INSERT OVERWRITE for a small daily delta** — rewrites everything; use MERGE.
- ❌ **Forgetting to dedup the source** before MERGE → "multiple matches" failure.
- ❌ **DROP + CREATE instead of CREATE OR REPLACE** — loses history/lineage and
  leaves a window where the table doesn't exist.
- ❌ **MERGE on a huge table with no predicate** — scans/rewrites the whole table;
  always add a target-pruning predicate.
- ❌ **Assuming schema evolution is on** — MERGE enforces schema by default; you
  must opt in (`spark.databricks.delta.schema.autoMerge.enabled`).

## References

- [MERGE INTO — Databricks docs](https://docs.databricks.com/aws/en/delta/merge)
- [Selectively overwrite data (replaceWhere / dynamic partition overwrite)](https://docs.databricks.com/aws/en/delta/selective-overwrite)
- [Slowly changing dimensions (SCD) with MERGE](https://docs.databricks.com/aws/en/delta/merge#scd-type-2)
- [CREATE TABLE / CTAS — SQL reference](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table)
- [Automatic schema evolution for MERGE](https://docs.databricks.com/aws/en/delta/update-schema#automatic-schema-evolution-for-merge)
