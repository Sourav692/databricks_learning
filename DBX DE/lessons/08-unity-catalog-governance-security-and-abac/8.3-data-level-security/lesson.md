# Data-Level Security — Dynamic Views, Row Filters & Column Masks

> **Topic 8.3 · Unity Catalog — Governance, Security & ABAC** — enterprise
> deep-dive, interview-focused. Runnable end-to-end code lives in the consolidated
> Topic 8 notebook (built at the last subtopic); snippets below are the teaching
> units.

## What it is

Object grants (8.2) are all-or-nothing per table. **Data-level security** controls
**which rows** and **which column values** a user sees:

- **Row filter** — a SQL UDF returning **`BOOLEAN`**, applied to a table; rows that
  return `FALSE` are **hidden** from that user.
- **Column mask** — a SQL UDF applied to a column; it **transforms the value**
  (e.g. redact email) based on who's querying.
- **Dynamic view** — a SQL **view** that filters/masks/reshapes base tables; you
  grant access to the view instead of the table.

All three **vary by user** via the built-in context functions `current_user()`,
`is_account_group_member('grp')`, `is_member('grp')` — same query, different
results per person.

**Analogy:** a **redacted document.** Everyone gets "the same file," but a **row
filter** removes whole paragraphs you're not cleared for, and a **column mask**
blacks out specific words (emails) — based on your clearance.

## Why it matters

- Real data has **PII and tenant boundaries** — you can't just GRANT the whole
  table. This is how one table safely serves many audiences.
- "Show analysts orders but mask the email column / hide other regions' rows" →
  **column mask + row filter** is the expected answer.

**Real-world use case:** one `customers` table — support sees all rows but **email
masked**; regional managers see **only their region's rows**; auditors see
everything.

---

## How it works — deep dive

### 1. Row filter (hide rows)

A UDF returning `BOOLEAN`; rows where it's `FALSE` are hidden. Attach with
`SET ROW FILTER … ON (cols)` — the filter's parameters bind to those columns.

```sql
-- Admins see all rows; everyone else only their region's rows.
-- Pattern: drive the rule from GROUP MEMBERSHIP (no invented helper functions).
CREATE FUNCTION region_filter(region STRING) RETURNS BOOLEAN
  RETURN is_account_group_member('admins')
      OR is_account_group_member('region_' || region);   -- e.g. group "region_west"

ALTER TABLE prod.sales.customers SET ROW FILTER region_filter ON (region);
```

- Alternative pattern: join to a **mapping table** of `user → allowed_region` keyed
  by `current_user()` inside the function/view.

### 2. Column mask (redact values)

A UDF that transforms a column value per user; attach with `ALTER COLUMN … SET
MASK`. Use **`USING COLUMNS`** to make the mask depend on *other* columns.

```sql
CREATE FUNCTION mask_email(email STRING) RETURNS STRING
  RETURN CASE WHEN is_account_group_member('pii_readers') THEN email
              ELSE '***@***' END;

ALTER TABLE prod.sales.customers ALTER COLUMN email SET MASK mask_email;

-- Conditional on another column (e.g. only mask EU residents' email):
-- ALTER TABLE ... ALTER COLUMN email SET MASK mask_email USING COLUMNS (region);
```

### 3. Dynamic view (curated, grant the view)

Bake filtering/masking into a **view** and grant the view instead of the base
table — best for sharing a specific, reshaped slice.

```sql
CREATE VIEW prod.sales.cust_safe AS
SELECT id, region,
       CASE WHEN is_account_group_member('pii_readers') THEN email ELSE '***' END AS email
FROM prod.sales.customers
WHERE is_account_group_member('admins') OR is_account_group_member('region_' || region);
-- then: GRANT SELECT ON VIEW prod.sales.cust_safe TO `analysts`;
```

### 4. How it composes with GRANT (8.2)

- Grants decide **whether** you can touch the table; filters/masks decide **what you
  see** once you can. Both apply — a user still needs `USE CATALOG`/`USE SCHEMA`/
  `SELECT`, *and then* the row filter/mask shapes the result.

---

## Row filter / column mask vs dynamic view

| | Row filter / column mask | Dynamic view |
|---|---|---|
| Attaches to | The **base table** (`ALTER TABLE`) | A separate **view** object |
| Users query | The table directly | The **view** (grant on view) |
| Best for | Universal table-level enforcement | Curated/shared data slices |

## Uses, edge cases & limitations

- **Uses:** PII masking, multi-tenant/region row isolation, "same table, different
  view per role."
- **Edge cases:** keep UDFs **simple** — they run per row/query and the optimizer
  "always makes the secure choice," which can cost performance; limit distinct masks.
- **Limitations (verified):** a table with **row filters/column masks does not
  support time travel, CLONE (deep or shallow), or Delta Lake API access**. For
  enterprise scale, prefer **ABAC** (8.4), which applies masks/filters automatically
  by **tag** at catalog/schema level instead of per-table.

## Common gotchas

- ❌ Using **object GRANT** to hide rows/columns — it can't; use row filters /
  column masks / dynamic views.
- ❌ Omitting **`RETURNS BOOLEAN`** (row filter) / **`RETURNS <type>`** (mask) in the
  function signature.
- ❌ Inventing a per-user helper function — drive rules from
  **`is_account_group_member()`** / `current_user()` or a mapping table.
- ❌ Heavy UDF logic in a filter/mask → slow queries (runs per row).
- ❌ Forgetting filters/masks **disable time travel & CLONE** on that table.
- ❌ Hand-managing masks on hundreds of tables when **ABAC by tag** would scale.

## References

- [Row filters & column masks — docs](https://docs.databricks.com/aws/en/tables/row-and-column-filters)
- [Dynamic views](https://docs.databricks.com/aws/en/views/dynamic)
- [Built-in functions: current_user / is_account_group_member](https://docs.databricks.com/aws/en/sql/language-manual/functions/is_account_group_member)
- [ABAC (attribute-based access control)](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/)
