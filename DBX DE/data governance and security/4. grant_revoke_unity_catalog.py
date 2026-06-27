# Databricks notebook source
# MAGIC %md
# MAGIC # GRANT & REVOKE in SQL — Unity Catalog
# MAGIC
# MAGIC **Goal:** learn how to give and take away access to data in Databricks, and understand *why `SELECT` alone is never enough*.
# MAGIC
# MAGIC Think of your data platform as an office building:
# MAGIC - **GRANT** = hand someone a key
# MAGIC - **REVOKE** = take the key back
# MAGIC
# MAGIC You never delete the room — you just control who can enter and what they can do inside. The "keys" are **privileges**; the "rooms" are **securable objects** arranged in a hierarchy: `catalog → schema → table`.
# MAGIC
# MAGIC ---
# MAGIC ### Prerequisites
# MAGIC - A **Unity Catalog–enabled** workspace.
# MAGIC - A **SQL warehouse** or a **UC-enabled cluster** (DBR 13.3 LTS or later recommended).
# MAGIC - Privileges to manage the objects you touch: you must be the **object owner**, hold the **`MANAGE`** privilege, or be a **metastore admin**.
# MAGIC - `CREATE SCHEMA` / `CREATE TABLE` on the catalog used below (defaults to `main`).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Set up a demo namespace (runnable)
# MAGIC
# MAGIC We create a throwaway schema and table so every later cell actually runs.
# MAGIC Replace `main` with any catalog where you have `CREATE SCHEMA` if needed.
# MAGIC Delta is the default table format in Unity Catalog — no extra config required.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Three-level namespace: catalog.schema.table
# MAGIC CREATE SCHEMA IF NOT EXISTS main.grant_demo
# MAGIC   COMMENT 'Temporary schema for the GRANT/REVOKE lesson';
# MAGIC
# MAGIC CREATE TABLE IF NOT EXISTS main.grant_demo.orders (
# MAGIC   order_id   BIGINT,
# MAGIC   customer   STRING,
# MAGIC   amount     DECIMAL(10,2),
# MAGIC   order_date DATE
# MAGIC );  -- Delta by default
# MAGIC
# MAGIC INSERT INTO main.grant_demo.orders VALUES
# MAGIC   (1, 'Acme',    120.50, DATE'2026-01-04'),
# MAGIC   (2, 'Globex',  89.00,  DATE'2026-01-05'),
# MAGIC   (3, 'Initech', 240.75, DATE'2026-01-06');
# MAGIC
# MAGIC SELECT * FROM main.grant_demo.orders;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Basic syntax
# MAGIC
# MAGIC ```sql
# MAGIC -- Give a privilege
# MAGIC GRANT  <privilege> ON <object_type> <object_name> TO   `<principal>`;
# MAGIC
# MAGIC -- Take it back
# MAGIC REVOKE <privilege> ON <object_type> <object_name> FROM `<principal>`;
# MAGIC ```
# MAGIC
# MAGIC Three slots to fill in:
# MAGIC
# MAGIC | Slot | Meaning | Examples |
# MAGIC |------|---------|----------|
# MAGIC | **privilege** | *what* they can do | `SELECT`, `MODIFY`, `CREATE TABLE`, `ALL PRIVILEGES` |
# MAGIC | **object** | what they can do it *on* | `CATALOG`, `SCHEMA`, `TABLE`, `VOLUME`, `FUNCTION` |
# MAGIC | **principal** | *who* gets it | a user email, a **group**, or a service principal |
# MAGIC
# MAGIC Notes:
# MAGIC - Wrap the principal in **backticks** — emails and group names contain special characters.
# MAGIC - The only structural difference between the two statements is `TO` vs `FROM`.
# MAGIC - `account users` (used below) is a **built-in group** present in every account, so these cells run as-is. Swap in your real group/user.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. The key concept: access is HIERARCHICAL
# MAGIC
# MAGIC To read `main.grant_demo.orders`, a user needs **the whole chain**, not just `SELECT`:
# MAGIC
# MAGIC ```
# MAGIC   USE CATALOG  on  main                    →  keycard to enter the building
# MAGIC   USE SCHEMA   on  main.grant_demo         →  keycard to reach the floor
# MAGIC   SELECT       on  main.grant_demo.orders  →  key to the actual room (the data)
# MAGIC ```
# MAGIC
# MAGIC - `USE CATALOG` / `USE SCHEMA` let you **reach** an object but grant **no data access** by themselves.
# MAGIC - `SELECT` is the privilege that actually reads the data.
# MAGIC - **Miss any link and the query fails** — this is the #1 cause of "I granted SELECT but it still doesn't work".

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Grant the full chain so the principal can actually query the table
# MAGIC GRANT USE CATALOG ON CATALOG main                   TO `account users`;
# MAGIC GRANT USE SCHEMA  ON SCHEMA  main.grant_demo         TO `account users`;
# MAGIC GRANT SELECT      ON TABLE   main.grant_demo.orders  TO `account users`;

# COMMAND ----------

# MAGIC %md
# MAGIC **Shortcut:** grant `SELECT` at the **schema** (or **catalog**) level to cover *all current and future tables* in one statement — handy for read-only analyst roles.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- One grant covers every table in the schema, including ones created later
# MAGIC GRANT SELECT ON SCHEMA main.grant_demo TO `account users`;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. More GRANT examples by privilege type
# MAGIC
# MAGIC | Privilege | What it lets you do | Typical object |
# MAGIC |-----------|---------------------|----------------|
# MAGIC | `USE CATALOG` / `USE SCHEMA` | Enter / see into a container | Catalog / Schema |
# MAGIC | `SELECT` | Read data | Table, view |
# MAGIC | `MODIFY` | Insert, update, delete data | Table |
# MAGIC | `CREATE TABLE` | Create tables in a schema | Schema, catalog |
# MAGIC | `EXECUTE` | Run a function / load a model | Function, model |
# MAGIC | `READ VOLUME` / `WRITE VOLUME` | Read / write files | Volume |
# MAGIC | `ALL PRIVILEGES` | Everything applicable + child objects | Any |

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Let a group create tables in the schema (NOTE: it's CREATE TABLE, not bare CREATE)
# MAGIC GRANT CREATE TABLE ON SCHEMA main.grant_demo TO `account users`;
# MAGIC
# MAGIC -- Let a group write data (insert/update/delete) into the table
# MAGIC GRANT MODIFY ON TABLE main.grant_demo.orders TO `account users`;
# MAGIC
# MAGIC -- Example of granting to a single user instead of a group (edit the email, then uncomment):
# MAGIC -- GRANT SELECT ON TABLE main.grant_demo.orders TO `someone@yourcompany.com`;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. ALL PRIVILEGES
# MAGIC
# MAGIC `ALL PRIVILEGES` grants every privilege applicable to the object **and its children**.
# MAGIC
# MAGIC ⚠️ **Gotcha:** it does **not** include `MANAGE`, `EXTERNAL USE SCHEMA`, or `EXTERNAL USE LOCATION` — grant those explicitly when needed.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Give a group everything on the schema (and its tables, views, etc.)
# MAGIC GRANT ALL PRIVILEGES ON SCHEMA main.grant_demo TO `account users`;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Inspect current access with SHOW GRANTS
# MAGIC
# MAGIC Always verify after granting. `SHOW GRANTS` lists who holds what on an object.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Who has what on the table?
# MAGIC SHOW GRANTS ON TABLE main.grant_demo.orders;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- What can a specific principal do on the schema?
# MAGIC SHOW GRANTS `account users` ON SCHEMA main.grant_demo;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. REVOKE — taking access back
# MAGIC
# MAGIC Same shape as `GRANT`, but `FROM` instead of `TO`.
# MAGIC
# MAGIC ⚠️ **Gotcha:** `REVOKE` only removes a grant made at **that** level. It cannot cancel out a privilege the principal **inherited** from a parent object — to stop inherited access, revoke at the level where it was actually granted.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Remove a single privilege on the table
# MAGIC REVOKE SELECT ON TABLE main.grant_demo.orders FROM `account users`;
# MAGIC
# MAGIC -- Remove everything granted on the schema
# MAGIC REVOKE ALL PRIVILEGES ON SCHEMA main.grant_demo FROM `account users`;
# MAGIC
# MAGIC -- Remove the container keycards
# MAGIC REVOKE USE SCHEMA  ON SCHEMA  main.grant_demo FROM `account users`;
# MAGIC REVOKE USE CATALOG ON CATALOG main           FROM `account users`;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Confirm the grants are gone
# MAGIC SHOW GRANTS ON TABLE main.grant_demo.orders;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Users vs. groups — which to use?
# MAGIC
# MAGIC - **Groups (default):** grant once to a group, then add/remove people without rewriting SQL. This is role-based access and scales well.
# MAGIC - **Individual users:** reserve for genuine one-offs — e.g. a business stakeholder doing a quick ad-hoc analysis where standing up a whole group isn't worth it.
# MAGIC
# MAGIC **Best practice:** follow the *principle of least privilege* — grant the narrowest privilege at the narrowest scope that gets the job done, and prefer groups over individuals.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Common mistakes & gotchas (recap)
# MAGIC
# MAGIC | Symptom | Cause | Fix |
# MAGIC |---------|-------|-----|
# MAGIC | "Granted `SELECT` but user still can't query" | Missing `USE CATALOG` / `USE SCHEMA` | Grant the full hierarchy chain |
# MAGIC | `GRANT CREATE ON SCHEMA` errors | Bare `CREATE` is **legacy Hive metastore** syntax | Use `CREATE TABLE`, `CREATE FUNCTION`, `CREATE VOLUME`, etc. |
# MAGIC | Expected `MANAGE` after `ALL PRIVILEGES` | `ALL PRIVILEGES` excludes `MANAGE` & external privileges | Grant them explicitly |
# MAGIC | `REVOKE` didn't remove access | Privilege was **inherited** from a parent | Revoke at the level it was granted |
# MAGIC | "Insufficient privileges to GRANT" | You're not owner / `MANAGE` / admin | Get ownership or `MANAGE` on the object |

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Cleanup
# MAGIC
# MAGIC Drop the demo objects when you're done.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP SCHEMA IF EXISTS main.grant_demo CASCADE;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Sources
# MAGIC - [Unity Catalog privileges reference](https://docs.databricks.com/aws/en/data-governance/unity-catalog/access-control/privileges-reference)
# MAGIC - [Show, grant, and revoke privileges](https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/)
# MAGIC - [GRANT (SQL language reference)](https://docs.databricks.com/aws/en/sql/language-manual/security-grant)

