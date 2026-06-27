# Access Control — GRANT/REVOKE, Users & Groups

> **Topic 8.2 · Unity Catalog — Governance, Security & ABAC** — enterprise
> deep-dive, interview-focused. Runnable end-to-end code lives in the consolidated
> Topic 8 notebook (built at the last subtopic); snippets below are the teaching
> units.

## What it is

- UC access control is **GRANT/REVOKE privileges on securable objects** to
  **principals** — **account-level groups**, users, or service principals.
- **Inheritance:** privileges flow **down** the hierarchy (metastore → catalog →
  schema → table). To read a table you need **`USE CATALOG`** on its catalog **and**
  **`USE SCHEMA`** on its schema — **plus** `SELECT` on the table.
- **Ownership:** every object has an **owner** with full rights; owners (and
  **MANAGE** holders / admins) can grant.

**Analogy:** an **office building with keycards**. `USE CATALOG` = badge into the
building; `USE SCHEMA` = badge onto the floor; `SELECT` = key to a specific room.
You need all three — and you issue keycards to **teams (groups)**, not individuals.

## Why it matters

- This is **the** day-to-day governance skill — most "permission denied" bugs are a
  **missing USE CATALOG / USE SCHEMA**, not the table grant.
- "How do you give a team read access to a schema?" → GRANT to a **group**, with the
  USE-chain — a guaranteed interview question.

**Real-world use case:** the analytics team gets `USE CATALOG prod`,
`USE SCHEMA prod.sales`, and `SELECT` on `prod.sales.*` — granted to the
`analysts` **group**, so onboarding/offboarding is just group membership.

---

## How it works — deep dive

### 1. The grant statement & the USE chain

```sql
-- Pattern: GRANT <privilege> ON <securable> <name> TO <principal>
GRANT USE CATALOG ON CATALOG prod              TO `analysts`;   -- badge: building
GRANT USE SCHEMA  ON SCHEMA  prod.sales        TO `analysts`;   -- badge: floor
GRANT SELECT      ON TABLE   prod.sales.orders TO `analysts`;   -- key: the room
```

- **All three** are required to read the table — the #1 interview gotcha. Grant to
  **groups** (account identities synced via SCIM/identity federation), not users.

### 2. The privilege catalog (the ones you actually use)

| Category | Privileges |
|---|---|
| Read/write data | `SELECT`, `MODIFY` |
| Traverse | `USE CATALOG`, `USE SCHEMA`, `USE CONNECTION` |
| Create | `CREATE TABLE`, `CREATE SCHEMA`, `CREATE VOLUME`, `CREATE FUNCTION`, `CREATE MATERIALIZED VIEW` |
| Functions | `EXECUTE` (run a UC function) |
| Volumes/files | `READ VOLUME`, `WRITE VOLUME` |
| Discovery / governance | `BROWSE` (see metadata), `APPLY TAG` |
| Admin | `MANAGE` (delegate grants), `ALL PRIVILEGES` |

```sql
GRANT EXECUTE     ON FUNCTION prod.sales.mask_email TO `analysts`;  -- UC function
GRANT READ VOLUME ON VOLUME   prod.sales.landing    TO `loaders`;   -- files
GRANT MODIFY      ON TABLE     prod.sales.orders     TO `data_eng`;  -- write
```

### 3. Inheritance — grant once, cover many

A grant at a **higher** level covers children, so you rarely grant table-by-table:

```sql
-- Everyone in analysts can read ALL current & future tables in the schema
GRANT SELECT ON SCHEMA prod.sales TO `analysts`;     -- + USE CATALOG/USE SCHEMA
-- Or the whole catalog:
GRANT SELECT ON CATALOG prod TO `analysts`;
```

### 4. Ownership, MANAGE & who can grant

```sql
ALTER TABLE prod.sales.orders OWNER TO `data_eng`;   -- transfer ownership (to a group)
GRANT MANAGE ON SCHEMA prod.sales TO `sales_admins`; -- delegate grant authority
```

- **Who can grant:** the object **owner**, holders of **`MANAGE`**, or
  metastore/account admins. Some actions (DROP/ALTER) require **ownership**.

### 5. Audit grants with `SHOW GRANTS` & REVOKE

```sql
SHOW GRANTS ON TABLE prod.sales.orders;              -- who can do what
SHOW GRANTS `analysts` ON SCHEMA prod.sales;         -- a principal's grants
REVOKE SELECT ON TABLE prod.sales.orders FROM `analysts`;
```

---

## Comparison: grant scope

| Grant level | Covers | Use when |
|---|---|---|
| Table/view | one object | fine-grained, exceptional access |
| Schema | all (current + future) objects in it | a team owns a subject area |
| Catalog | everything in the catalog | broad env/domain access |

## Uses, edge cases & limitations

- **Uses:** team read/write access; least-privilege by group; delegating admin via
  MANAGE/ownership; granting `EXECUTE`/`READ VOLUME` for functions/files.
- **Edge cases:**
  - **Missing USE CATALOG / USE SCHEMA** is the #1 cause of "permission denied"
    even when SELECT is granted.
  - Granting to **individual users** instead of groups → unmanageable sprawl.
  - Ownership matters: dropping/altering often requires owner or MANAGE.
- **Limitations:** GRANT/REVOKE controls **whole-object** access; **row/column-level**
  control needs row filters & column masks (8.3), and attribute-based control needs
  **ABAC** (8.4).

## Common gotchas

- ❌ Granting `SELECT` but forgetting **`USE CATALOG`/`USE SCHEMA`** → still denied.
- ❌ Granting to **users** instead of **groups** → onboarding/offboarding pain.
- ❌ Assuming admin = owner — some actions require **ownership** or **MANAGE**.
- ❌ Expecting GRANT to hide **rows/columns** — that's row filters / column masks
  (8.3), not object grants.
- ❌ Forgetting `EXECUTE`/`READ VOLUME` — tables aren't the only securables.

## References

- [Manage privileges in Unity Catalog — docs](https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/)
- [Privileges and securable objects](https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/privileges)
- [SHOW GRANTS](https://docs.databricks.com/aws/en/sql/language-manual/security-show-grant)
- [Manage groups & identity federation](https://docs.databricks.com/aws/en/admin/users-groups/groups)
