# Databricks notebook source
# MAGIC %md
# MAGIC # Hands-On: Databricks Workspace Access Control
# MAGIC
# MAGIC A practical, runnable lab for the two layers of access control:
# MAGIC
# MAGIC | Layer | What it controls | Can you do it in this notebook? |
# MAGIC |---|---|---|
# MAGIC | **1. Workspace access (entitlements)** | Login + which *features* a user sees (notebooks, jobs, SQL) | **No** — UI / Account console / SCIM only. We explain the steps. |
# MAGIC | **2. Data access (Unity Catalog privileges)** | Which *data* a user can see and act on | **Yes** — `GRANT` / `REVOKE` / `SHOW GRANTS` below |
# MAGIC
# MAGIC This notebook builds a small **sandbox** (catalog → schema → table → volume → function), then walks you through granting the
# MAGIC **BROWSE → Data Reader → Data Editor** levels and verifying each one — exactly the escalation you use when onboarding a new teammate.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prerequisites
# MAGIC
# MAGIC - A **Unity Catalog–enabled** workspace.
# MAGIC - Compute: any current **SQL warehouse** *or* an interactive cluster on **DBR 13.3 LTS or later** (UC-enabled). Both run the `%sql` cells below.
# MAGIC - **Permission to create a catalog** (metastore admin, or `CREATE CATALOG` on the metastore).
# MAGIC   - *No catalog-create rights?* Skip the `CREATE CATALOG` cell, set the sandbox to an existing catalog where you have `CREATE SCHEMA`, and adjust names throughout.
# MAGIC - For **Layer 1** (adding users / entitlements): you must be a **workspace admin**.
# MAGIC
# MAGIC > **▶ Before you run the grant cells:** replace every `new_analyst@example.com` with the email of a **real test user** (or a group name).
# MAGIC > Use *Edit → Find and Replace* in the notebook toolbar to swap them all at once. Granting to a principal that doesn't exist will error.

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Part A — Build the sandbox
# MAGIC We create one catalog (`acl_sandbox`) with a schema, a Delta table, a volume, and a function so we have something to grant access *to*.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create the practice catalog (requires CREATE CATALOG on the metastore)
# MAGIC CREATE CATALOG IF NOT EXISTS acl_sandbox
# MAGIC   COMMENT 'Sandbox for practicing Unity Catalog access control';
# MAGIC
# MAGIC -- Make it the active catalog so later names can be shorter
# MAGIC USE CATALOG acl_sandbox;
# MAGIC
# MAGIC CREATE SCHEMA IF NOT EXISTS onboarding_demo
# MAGIC   COMMENT 'Objects a new analyst will be granted access to';
# MAGIC
# MAGIC USE SCHEMA onboarding_demo;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- A Delta table (Delta is the default UC managed-table format)
# MAGIC CREATE TABLE IF NOT EXISTS acl_sandbox.onboarding_demo.customers (
# MAGIC   id      INT,
# MAGIC   name    STRING,
# MAGIC   email   STRING,
# MAGIC   country STRING
# MAGIC ) USING DELTA;
# MAGIC
# MAGIC INSERT INTO acl_sandbox.onboarding_demo.customers VALUES
# MAGIC   (1, 'Asha Rao',     'asha@example.com',  'IN'),
# MAGIC   (2, 'Liam Murphy',  'liam@example.com',  'IE'),
# MAGIC   (3, 'Mei Tanaka',   'mei@example.com',   'JP');
# MAGIC
# MAGIC -- A managed volume (for the READ VOLUME / WRITE VOLUME privileges)
# MAGIC CREATE VOLUME IF NOT EXISTS acl_sandbox.onboarding_demo.files
# MAGIC   COMMENT 'Demo volume for file-level privileges';
# MAGIC
# MAGIC -- A scalar function (for the EXECUTE privilege)
# MAGIC CREATE OR REPLACE FUNCTION acl_sandbox.onboarding_demo.full_label(first STRING, country STRING)
# MAGIC   RETURNS STRING
# MAGIC   RETURN concat(first, ' (', country, ')');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Confirm the sandbox is populated
# MAGIC SELECT * FROM acl_sandbox.onboarding_demo.customers;

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Part B — Layer 1: Workspace entitlements (UI / admin only)
# MAGIC
# MAGIC These steps **cannot** be done from a notebook — they live in admin settings. Do them once, then come back for Part C.
# MAGIC
# MAGIC **Add a test user**
# MAGIC 1. Profile icon → **Settings**
# MAGIC 2. **Identity and access** tab
# MAGIC 3. Next to **Users** → **Manage** → **Add user** → enter the email → confirm
# MAGIC
# MAGIC **What they get automatically:** every user joins the built-in `users` group, which grants two **entitlements**:
# MAGIC
# MAGIC - **Workspace access** → may *use* notebooks, jobs, models, pipelines (the feature — **not** any specific notebook)
# MAGIC - **Databricks SQL access** → dashboards, queries, SQL warehouses
# MAGIC
# MAGIC **Key idea — why your notebooks stay hidden:** an *entitlement* is permission to use a *feature*, not access to a specific object.
# MAGIC Notebooks/folders have their **own** object permissions and are private by default, so a new user sees an **empty Workspace** until something is shared.
# MAGIC
# MAGIC > **Entitlement ≠ object permission.** Layer 1 lets them in the door; Layer 2 (below) decides what data they can touch.
# MAGIC >
# MAGIC > *Automating Layer 1:* user/entitlement management can also be scripted via the **Account SCIM API**, the **Databricks CLI**, or **Terraform** — but never from notebook SQL.

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Part C — Layer 2: Unity Catalog privileges (runnable)
# MAGIC
# MAGIC General form:
# MAGIC ```sql
# MAGIC GRANT  <privilege> ON <securable-type> <name> TO   `principal`;
# MAGIC REVOKE <privilege> ON <securable-type> <name> FROM `principal`;
# MAGIC ```
# MAGIC - **principal** = a user email, a group, or a service principal. Wrap names with special characters (like `@`) in backticks.
# MAGIC - Privileges granted **on a catalog inherit downward** to every current and future schema/table/volume/function inside it — that's how we mimic the UI presets catalog-wide.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Stage 1 — `BROWSE` only ("look, don't touch")
# MAGIC Lets the user **discover objects and view metadata** (names, comments, column names) and request access — but **no data**.
# MAGIC `BROWSE` is granted at the **catalog** level and needs no `USE CATALOG` / `USE SCHEMA`.

# COMMAND ----------

# MAGIC %sql
# MAGIC GRANT BROWSE ON CATALOG acl_sandbox TO `new_analyst@example.com`;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Verify: what does this principal have on the catalog?
# MAGIC SHOW GRANTS `new_analyst@example.com` ON CATALOG acl_sandbox;

# COMMAND ----------

# MAGIC %md
# MAGIC **Try it as the test user** (separate browser / incognito, logged in as `new_analyst@example.com`):
# MAGIC they can now *see* `acl_sandbox` in Catalog Explorer and read column names, but **`SELECT * FROM acl_sandbox.onboarding_demo.customers` fails** — no data access yet.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Stage 2 — "Data Reader" (read & query)
# MAGIC The UI preset **Data Reader** is a *bundle* of privileges. There's no `GRANT \`Data Reader\`` in SQL — you grant the underlying privileges.
# MAGIC Granting them **on the catalog** gives read access across everything inside it.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- "Data Reader", reproduced in SQL (catalog-wide via inheritance)
# MAGIC GRANT USE CATALOG ON CATALOG acl_sandbox TO `new_analyst@example.com`;
# MAGIC GRANT USE SCHEMA  ON CATALOG acl_sandbox TO `new_analyst@example.com`;
# MAGIC GRANT SELECT      ON CATALOG acl_sandbox TO `new_analyst@example.com`;
# MAGIC GRANT READ VOLUME ON CATALOG acl_sandbox TO `new_analyst@example.com`;
# MAGIC GRANT EXECUTE     ON CATALOG acl_sandbox TO `new_analyst@example.com`;

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW GRANTS `new_analyst@example.com` ON CATALOG acl_sandbox;

# COMMAND ----------

# MAGIC %md
# MAGIC **Try it as the test user:** `SELECT` now works, they can read the volume and run the function — but **`INSERT` / `UPDATE` / `DELETE` still fail** (no `MODIFY`), and they **cannot create or drop** anything (no DDL). This is the ideal default for a new joiner.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Stage 3 — "Data Editor" (read & write)
# MAGIC Adds write privileges on top of Data Reader. Reserve for trusted contributors.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- "Data Editor" = Data Reader + write privileges
# MAGIC GRANT MODIFY       ON CATALOG acl_sandbox TO `new_analyst@example.com`;
# MAGIC GRANT WRITE VOLUME ON CATALOG acl_sandbox TO `new_analyst@example.com`;

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW GRANTS `new_analyst@example.com` ON CATALOG acl_sandbox;

# COMMAND ----------

# MAGIC %md
# MAGIC **Try it as the test user:** `INSERT` / `UPDATE` / `DELETE` now succeed. Note they *still* can't create/drop catalogs/schemas as owner or grant privileges to others — that needs ownership or `MANAGE`/DDL privileges you deliberately withhold.

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Part D — Inspect & revoke
# MAGIC See grants from the object's side, then walk access back down.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- All grants on the catalog, regardless of principal
# MAGIC SHOW GRANTS ON CATALOG acl_sandbox;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Grants inherited / set on a specific table
# MAGIC SHOW GRANTS ON TABLE acl_sandbox.onboarding_demo.customers;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Demote Data Editor back to Data Reader: remove only the write privileges
# MAGIC REVOKE MODIFY       ON CATALOG acl_sandbox FROM `new_analyst@example.com`;
# MAGIC REVOKE WRITE VOLUME ON CATALOG acl_sandbox FROM `new_analyst@example.com`;
# MAGIC
# MAGIC -- (REVOKE is idempotent — it succeeds even if the privilege wasn't present.)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Remove all access for the principal in one shot (full off-boarding)
# MAGIC REVOKE ALL PRIVILEGES ON CATALOG acl_sandbox FROM `new_analyst@example.com`;
# MAGIC
# MAGIC SHOW GRANTS `new_analyst@example.com` ON CATALOG acl_sandbox;  -- expect: empty

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Bonus — discoverability for everyone
# MAGIC Databricks recommends granting `BROWSE` to the **All account users** group so data is discoverable org-wide without exposing it.
# MAGIC The cleanest way is **Catalog Explorer → catalog → Permissions → Grant → principal `All account users` → BROWSE**.
# MAGIC (Doing this in the UI avoids guessing the exact system-group identifier.)

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Cleanup
# MAGIC Run this when you're done to remove the sandbox entirely.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP CATALOG IF EXISTS acl_sandbox CASCADE;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Recap
# MAGIC - **Layer 1 (entitlements, UI/admin):** login + feature access; granted via the `users` group. *Not* the same as data or notebook access.
# MAGIC - **Layer 2 (UC privileges, SQL):** `GRANT`/`REVOKE` on `catalog.schema.object`.
# MAGIC - **Onboarding escalation:** `BROWSE` → **Data Reader** (`USE CATALOG`+`USE SCHEMA`+`SELECT`+`READ VOLUME`+`EXECUTE`) → **Data Editor** (`+MODIFY`+`WRITE VOLUME`).
# MAGIC - **Verify** with `SHOW GRANTS`; **off-board** with `REVOKE ALL PRIVILEGES`.
# MAGIC - **Least privilege:** start at `BROWSE`, promote only as needed; use **groups** instead of per-user grants in production.

