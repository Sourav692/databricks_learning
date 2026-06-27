# Attribute-Based Access Control (ABAC)

> **Topic 8.4 · Unity Catalog — Governance, Security & ABAC** — enterprise
> deep-dive, interview-focused. Runnable end-to-end code lives in the consolidated
> Topic 8 notebook (built at the last subtopic); snippets below are the teaching
> units.

## What it is

- **ABAC** decides access by **attributes** (governed tags) on objects — not by
  configuring each table one by one.
- **Governed tags** mark data (e.g. `pii`, `region`); **ABAC policies** (`CREATE
  POLICY`) attach at **catalog/schema/table** level and **apply automatically to any
  object whose columns match the tag**.
- Policies enforce **row filters** and **column masks** — defined **once by tag**
  instead of per table (the per-table mechanics of 8.3, scaled by attribute).

**Analogy:** instead of locking each filing cabinet individually (per-table
filters/masks from 8.3), you put a **"CONFIDENTIAL" sticker** on documents and
write **one building rule**: "mask anything tagged CONFIDENTIAL." Tag a new doc →
the rule covers it instantly.

## Why it matters

- Per-table masks/filters (8.3) **don't scale** to thousands of tables. ABAC applies
  governance **by tag, hierarchically** — tag a new column `pii` and the mask is
  already enforced.
- "How do you enforce PII masking consistently across a whole catalog?" → **ABAC
  governed tags + policy**, not hand-editing every table.

**Real-world use case:** tag every PII column with `pii` and set **one** column-mask
policy on the `prod` catalog: "mask columns tagged `pii` unless the user is in
`pii_readers`." Every current and future `pii`-tagged column is masked
automatically — no per-table work.

> **Status:** row-filter & column-mask **ABAC policies** are the core offering;
> verify current GA/Preview state in the docs, and note **GRANT policies are Beta**.
> ⚠️ ABAC syntax is evolving — confirm against your workspace's docs.

---

## How it works — deep dive

### 1. Governed tags (the attributes)

- **Governed tags** are defined at the **account level with access controls** (who
  can create/assign them) — unlike ad-hoc *ungoverned* tags. ABAC policies match on
  **governed** tags.
- Apply them to schemas/tables/columns:

```sql
ALTER TABLE prod.sales.customers ALTER COLUMN ssn     SET TAGS ('pii' = 'ssn');
ALTER TABLE prod.sales.customers ALTER COLUMN address SET TAGS ('pii' = 'address');
```

### 2. Column-mask policy (mask any tagged column)

Write the masking UDF once, then a policy that targets the **tag** and applies the
mask to the matched column via `ON COLUMN`:

```sql
CREATE FUNCTION prod.sales.redact_ssn(s STRING) RETURNS STRING
  RETURN CASE WHEN is_account_group_member('pii_readers') THEN s ELSE '***-**-****' END;

CREATE POLICY redact_ssn_policy
  ON SCHEMA prod.sales
  COLUMN MASK prod.sales.redact_ssn
  TO `account users`
  FOR TABLES
  MATCH COLUMNS has_tag_value('pii', 'ssn') AS ssn_col
  ON COLUMN ssn_col;          -- every column tagged pii=ssn in this schema is masked
```

### 3. Row-filter policy (hide rows by tag)

```sql
CREATE POLICY hide_eu_customers
  ON SCHEMA prod.sales
  ROW FILTER prod.sales.is_not_eu_address
  TO `account users`
  FOR TABLES
  MATCH COLUMNS has_tag_value('pii', 'address') AS addr_col
  USING COLUMNS (addr_col);   -- the filter receives the tagged column
```

- **Match conditions** use `has_tag('key')` / `has_tag_value('key','value')`,
  combinable with `AND` / `OR` / `NOT`. Scope a policy `ON CATALOG`, `ON SCHEMA`, or
  `ON TABLE`.

### 4. Cascade & precedence

- A policy on a **catalog/schema cascades** down to all matching tables/columns —
  current *and future*. Tagging a new column instantly brings it under the policy.
- When **multiple policies** could match one object, mind **precedence/overlap** —
  design tags + policies so the intended one wins.

---

## ABAC vs per-table row filters/masks (8.3)

| | Per-table (8.3) | ABAC (8.4) |
|---|---|---|
| Defined | on each table (`ALTER TABLE`) | once, by **tag** (`CREATE POLICY`) |
| Scope | one table | **cascades** catalog/schema → matching objects |
| New table/column | configure again | **auto-covered** if tagged |
| Best for | a few tables | **enterprise-wide** consistency |

## Uses, edge cases & limitations

- **Uses:** org-wide PII masking, region/tenant row isolation by tag, consistent
  governance across large catalogs with minimal admin.
- **Edge cases:** governance is only as good as your **tagging** — an untagged PII
  column won't be masked, so tag hygiene is critical; policy **precedence/overlap**
  needs thought when multiple policies match.
- **Limitations:** built on the **row-filter/column-mask machinery** (8.3), so those
  perf and time-travel/CLONE constraints still apply. **Verify availability** of
  specific features (e.g. **GRANT policies are Beta**); ABAC DDL is still evolving.

## Common gotchas

- ❌ Relying on ABAC while data is **untagged** — no tag, no protection.
- ❌ Re-doing per-table masks when a **tag + policy** would cover all of them.
- ❌ Using **ungoverned** tags for policies — ABAC matches **governed** tags.
- ❌ Assuming every ABAC feature is GA — **GRANT policies are Beta**; confirm in docs.
- ❌ Ignoring policy **overlap/precedence** when multiple policies target an object.

## References

- [ABAC in Unity Catalog — docs](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/)
- [Create & manage row filter / column mask policies (CREATE POLICY)](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policies)
- [Configure ABAC with SQL (tutorial)](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/tutorial-sql)
- [Governed tags](https://docs.databricks.com/aws/en/data-governance/unity-catalog/tags)
