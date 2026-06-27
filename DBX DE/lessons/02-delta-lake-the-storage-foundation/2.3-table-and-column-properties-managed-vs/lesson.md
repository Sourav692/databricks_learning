# Table & Column Properties; Managed vs External Tables

> **Topic 2.3 · Delta Lake — the storage foundation** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code also lives in the consolidated
> Topic 2 notebook; the snippets below are the teaching units for each sub-topic.

## What it is

Two ideas bundled together:

1. **Managed vs external tables** — *who owns the data files*.
   - **Managed (default & recommended):** Unity Catalog owns the storage location,
     optimization, and lifecycle. **DROP deletes the data files.**
   - **External:** *you* specify the storage `LOCATION`; UC governs **access only**,
     not lifecycle. **DROP removes only the metadata — files stay.**
2. **Table & column properties** — metadata you attach to a table: `TBLPROPERTIES`
   (key-value settings, including Delta feature flags), column **comments**,
   **constraints** (`NOT NULL`, `CHECK`), **generated/identity columns**, and
   **tags** for discovery/governance.

**Analogy:** a **managed** table is a hotel room — the hotel cleans it and, when
you check out, clears it. An **external** table is a rented apartment with your
own lock — the landlord (UC) controls *who enters*, but your furniture (data)
stays when the lease ends.

## Why it matters

- **The DROP behavior is the #1 interview gotcha:** dropping a managed table
  *deletes data*; dropping an external table *keeps it*.
- Managed tables get **Predictive Optimization** (auto `OPTIMIZE`/`VACUUM`/
  `ANALYZE`) — better performance and lower cost with zero ops. External tables
  do **not**, so you own maintenance.

**Real-world use case:** use **managed** tables for everything you build inside
Databricks; use an **external** table only when the files are shared with other
engines/tools or already live in a fixed cloud location you must keep.

---

## How it works — deep dive

### 1. Managed tables (the default)

`CREATE TABLE` with **no `LOCATION`** → UC places the files in managed storage and
owns their full lifecycle.

```sql
-- Managed: no LOCATION. UC owns storage + lifecycle.
CREATE TABLE main.sales.orders (
  order_id BIGINT,
  amount   DECIMAL(10,2),
  order_ts TIMESTAMP
);

DROP TABLE main.sales.orders;     -- ⚠️ deletes the underlying data files too
```

```python
# PySpark equivalent — no path → managed
(df.write.format("delta").saveAsTable("main.sales.orders"))
```

- **Recoverable for 7 days** via `UNDROP` (default window; configurable per
  catalog/schema). Restores metadata, privileges, properties — but **not** PK/FK
  constraints (recreate those manually).

```sql
UNDROP TABLE main.sales.orders;   -- within the 7-day recovery window
```

### 2. External tables (you own the files)

`CREATE TABLE ... LOCATION` → UC records governance metadata only; the path must
sit under a **Unity Catalog external location** (backed by a storage credential).

```sql
-- External: explicit LOCATION under a UC external location.
CREATE TABLE main.sales.orders_ext (
  order_id BIGINT, amount DECIMAL(10,2), order_ts TIMESTAMP
)
LOCATION 's3://acme-lake/sales/orders/';

DROP TABLE main.sales.orders_ext;  -- ✅ removes metadata only; files REMAIN
```

```python
# PySpark equivalent — supplying a path makes it external
(df.write.format("delta").option("path", "s3://acme-lake/sales/orders/")
   .saveAsTable("main.sales.orders_ext"))
```

- **Formats supported:** Delta, Parquet, CSV, JSON, Avro, ORC.
- **No Predictive Optimization** — you must schedule `OPTIMIZE`/`VACUUM` yourself.

### 3. Managed vs external — the decision

| | Managed | External |
|---|---|---|
| Storage location | UC-managed | **You specify** (`LOCATION`) |
| Lifecycle / optimization | Unity Catalog (Predictive Optimization) | You / external system |
| **DROP deletes data?** | **Yes** | **No** (metadata only) |
| Governance (access, lineage) | UC | UC |
| Best for | **Default — most tables** | Files shared across engines / fixed location |

**Decision rule:** default to **managed**. Choose **external** only when another
engine/tool reads the same files, or the data must live at a fixed, externally
managed path.

### 4. Table properties — `TBLPROPERTIES`

Key-value metadata on the table. The high-value ones in production are **Delta
feature flags and retention settings**:

```sql
ALTER TABLE main.sales.orders SET TBLPROPERTIES (
  'delta.enableDeletionVectors' = 'true',   -- merge-on-read: cheaper UPDATE/DELETE/MERGE
  'delta.deletedFileRetentionDuration' = 'interval 7 days',  -- VACUUM horizon
  'delta.logRetentionDuration' = 'interval 30 days',         -- history horizon
  'quality' = 'gold'                          -- free-form business tag
);

SHOW TBLPROPERTIES main.sales.orders;        -- inspect current settings
```

- Flags change table behavior (deletion vectors, column mapping, append-only);
  free-form keys document intent. Inspect with `SHOW TBLPROPERTIES` / `DESCRIBE
  DETAIL`.

### 5. Column properties — comments, constraints, generated & identity columns

These make a table self-documenting and self-validating — exactly what an
interviewer means by "data quality at the table level":

```sql
CREATE TABLE main.sales.orders (
  order_id   BIGINT   GENERATED ALWAYS AS IDENTITY,        -- auto surrogate key
  customer   STRING   COMMENT 'FK to dim_customer.id',     -- column documentation
  amount     DECIMAL(10,2) NOT NULL,                       -- reject NULLs on write
  order_ts   TIMESTAMP,
  order_date DATE     GENERATED ALWAYS AS (CAST(order_ts AS DATE))  -- derived col
)
CLUSTER BY (order_date);                                   -- liquid clustering

-- CHECK constraints are added via ALTER TABLE (not inline in CREATE):
ALTER TABLE main.sales.orders
  ADD CONSTRAINT amount_positive CHECK (amount >= 0);      -- enforced on every write
```

- **`NOT NULL` / `CHECK`** reject bad rows at write time (a write violating them
  fails). **Generated columns** compute values automatically; **`IDENTITY`** gives
  auto-incrementing surrogate keys.

### 6. Discovery & governance — comments + tags

Comments and tags power Catalog Explorer search and governance policies:

```sql
COMMENT ON TABLE main.sales.orders IS 'Curated order facts (gold).';
ALTER TABLE main.sales.orders SET TAGS ('domain' = 'sales', 'pii' = 'false');
ALTER TABLE main.sales.orders ALTER COLUMN customer SET TAGS ('pii' = 'true');
```

> Column-level **masking / row filters** for security live in Topic 8 (data-level
> security) — here, tags/comments are about documentation and discovery.

---

## Uses, edge cases & limitations

- **Uses:** managed = default for new tables; external = files shared across
  engines or pinned to a location; properties = enabling Delta features,
  enforcing column quality, and documenting/discovering schemas.
- **Edge cases:**
  - Dropping a **managed** table is destructive — data is gone (recoverable only
    via `UNDROP` within ~7 days; PK/FK not restored).
  - Two external tables pointing at the **same path** → overlapping/confusing
    governance and lifecycle.
  - Adding `NOT NULL`/`CHECK` to an existing table validates current data — it
    fails if existing rows violate the constraint.
- **Limitations:** external tables miss Predictive Optimization (you run
  maintenance yourself). Some newer features assume managed tables. Generated and
  identity columns have restrictions (e.g. you can't directly `INSERT` arbitrary
  values into an `ALWAYS` identity column).

## Common gotchas

- ❌ Dropping a **managed** table expecting the files to survive — they don't.
- ❌ Choosing **external** "to be safe" everywhere — you lose Predictive
  Optimization and add ops burden. Prefer **managed** unless you have a reason.
- ❌ Hand-deleting files under an **external** table — the metastore still thinks
  they exist; queries break.
- ❌ Expecting a `CHECK` constraint inline in `CREATE TABLE` — add it with
  `ALTER TABLE ... ADD CONSTRAINT`.

## References

- [Managed tables (default & recommended)](https://docs.databricks.com/aws/en/tables/managed)
- [Managed vs external assets in Unity Catalog](https://docs.databricks.com/aws/en/data-governance/unity-catalog/managed-versus-external)
- [Work with external tables](https://docs.databricks.com/aws/en/tables/external)
- [UNDROP TABLE](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-undrop-table)
- [Predictive Optimization](https://docs.databricks.com/aws/en/optimizations/predictive-optimization)
- [CREATE TABLE (constraints, generated & identity columns)](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-using)
