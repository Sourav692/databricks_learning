# Databricks notebook source
# MAGIC %md
# MAGIC # ABAC — Attribute-Based Access Control (Hands-On)
# MAGIC
# MAGIC End-to-end demo of Unity Catalog **Attribute-Based Access Control**: governed tags, UDFs, and
# MAGIC **row-filter** + **column-mask** policies. Aligned to the lecture, with semantics corrected to match the
# MAGIC official docs.
# MAGIC
# MAGIC **Source of truth:**
# MAGIC - Core concepts: https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/core-concepts
# MAGIC - Create & manage policies: https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policies
# MAGIC
# MAGIC ### Prerequisites
# MAGIC - **Permissions:** Account admin (to define governed tags) **or** `ASSIGN` on the tag + `APPLY TAG` on the
# MAGIC   object; `MANAGE`/ownership on the securable + `EXECUTE` on the UDF to create the policy; `SELECT` to query.
# MAGIC - **Compute:** Serverless, **or** Standard/Dedicated on **DBR 16.4+** (dedicated needs fine-grained access
# MAGIC   control enabled). Older runtimes cannot read ABAC-protected tables.
# MAGIC - **Format:** Delta (default) under Unity Catalog three-level namespacing `catalog.schema.table`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Set your namespace
# MAGIC Change `CATALOG` to a Unity Catalog catalog you own. Everything else is created for you.

# COMMAND ----------

# DBTITLE 1,Cell 3
CATALOG = "databricks_ansh"   # <-- change to your catalog
SCHEMA  = "customers"
dbutils.widgets.text("demo.catalog", CATALOG)
dbutils.widgets.text("demo.schema", SCHEMA)
print(f"Using {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Create schema + a sample table, then load data

# COMMAND ----------

# DBTITLE 1,Cell 5
spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
spark.sql(f"USE SCHEMA {SCHEMA}")

spark.sql("""
CREATE OR REPLACE TABLE profiles (
  first_name STRING,
  last_name  STRING,
  phone      STRING,
  address    STRING,
  ssn        STRING
) USING DELTA
""")

spark.sql("""
INSERT INTO profiles VALUES
  ('Ava','Stone','555-0101','New York, USA','111-11-1111'),
  ('Liam','Reed','555-0102','California, USA','222-22-2222'),
  ('Noah','Khan','555-0103','Texas, USA','333-33-3333'),
  ('Mia','Lopez','555-0104','Florida, USA','444-44-4444'),
  ('Emma','Cruz','555-0105','Berlin, Europe','555-55-5555'),
  ('Luca','Rossi','555-0106','Rome, Europe','666-66-6666'),
  ('Sara','Meyer','555-0107','Paris, Europe','777-77-7777'),
  ('Omar','Ali','555-0108','Chicago, USA','888-88-8888')
""")

display(spark.sql("SELECT * FROM profiles"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Governed tag (account-level)
# MAGIC Governed tags are defined **once at the account level** with allowed values.
# MAGIC
# MAGIC **UI step (do this first):** Catalog → **Govern → Governed tags → Create tag**.
# MAGIC Create key `pii` with allowed values: `address`, `ssn`.
# MAGIC
# MAGIC Then **apply** the tag to the column. (Columns do **not** inherit tags from the table — apply directly.)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Tag the address column with pii = address
# MAGIC ALTER TABLE profiles ALTER COLUMN address SET TAGS ('pii' = 'address');
# MAGIC
# MAGIC -- Inspect column tags
# MAGIC SELECT * FROM information_schema.column_tags
# MAGIC WHERE schema_name = '${demo.schema}' AND table_name = 'profiles';

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. ROW FILTER — show only NON-EU rows
# MAGIC
# MAGIC **Rule that trips everyone up:** a row-filter UDF returns `BOOLEAN`.
# MAGIC `TRUE` = **keep** the row, `FALSE` = **hide** it. So to hide EU rows, return `FALSE` for them.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Returns TRUE for rows to KEEP (non-EU), FALSE for EU rows (hidden)
# MAGIC CREATE OR REPLACE FUNCTION non_eu_filter(addr STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN NOT (addr ILIKE '%europe%' OR addr ILIKE '%.eu%' OR addr ILIKE '%e.u%');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Attach a ROW FILTER policy at the SCHEMA scope.
# MAGIC -- It applies to every table in the schema that has a column tagged pii=address.
# MAGIC CREATE OR REPLACE POLICY hide_eu_customers
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COMMENT 'Hide EU customer rows from non-exempt users'
# MAGIC ROW FILTER non_eu_filter
# MAGIC TO `account users`
# MAGIC -- EXCEPT `admins`        -- uncomment to let an admin group see raw data
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS has_tag_value('pii','address') AS addr_col
# MAGIC USING COLUMNS (addr_col);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- EU rows (Europe) should now be filtered out for non-exempt users.
# MAGIC SELECT * FROM profiles;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Inheritance demo — a new table is auto-protected
# MAGIC No new function, no new policy. Create a table in the same schema, tag its address column, and the
# MAGIC existing policy applies automatically.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE profiles_2 (
# MAGIC   first_name STRING, last_name STRING, phone STRING, address STRING, ssn STRING
# MAGIC ) USING DELTA;
# MAGIC
# MAGIC INSERT INTO profiles_2 VALUES
# MAGIC   ('Kai','Wong','555-0201','Seattle, USA','121-21-2121'),
# MAGIC   ('Nina','Petrov','555-0202','Madrid, Europe','343-43-4343');
# MAGIC
# MAGIC -- Only tagging is required for the schema-level policy to take effect.
# MAGIC ALTER TABLE profiles_2 ALTER COLUMN address SET TAGS ('pii' = 'address');
# MAGIC
# MAGIC SELECT * FROM profiles_2;   -- EU (Madrid) row is filtered out automatically

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. COLUMN MASK — hide SSN values
# MAGIC The mask UDF receives the column value (bound automatically by `ON COLUMN`) and returns the masked value.
# MAGIC Return type must match/cast to the column's type.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Tag the ssn column (different value of the same governed tag key)
# MAGIC ALTER TABLE profiles ALTER COLUMN ssn SET TAGS ('pii' = 'ssn');
# MAGIC
# MAGIC -- Masking UDF
# MAGIC CREATE OR REPLACE FUNCTION mask_ssn(ssn STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN '***-**-****';

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Column-mask policy: any column tagged pii=ssn is masked for non-exempt users.
# MAGIC CREATE OR REPLACE POLICY data_mask_ssn
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COMMENT 'Mask SSN columns'
# MAGIC COLUMN MASK mask_ssn
# MAGIC TO `account users`
# MAGIC -- EXCEPT `admins`
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS has_tag_value('pii','ssn') AS ssn_col
# MAGIC ON COLUMN ssn_col;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- SSN now shows ***-**-**** ; EU rows still filtered by the row policy.
# MAGIC SELECT * FROM profiles;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Inspect & manage policies

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW POLICIES ON SCHEMA ${demo.catalog}.${demo.schema};

# COMMAND ----------

# MAGIC %md
# MAGIC # Part B — Multi-domain masking + Groups & Access
# MAGIC
# MAGIC One `employees` table, three audiences, different visibility for each — driven by **group membership**
# MAGIC checked *inside* the UDF.
# MAGIC
# MAGIC | Group | Sees `name`? | Sees `salary`? |
# MAGIC |---|---|---|
# MAGIC | **HR** | yes | masked |
# MAGIC | **Finance** | masked | yes |
# MAGIC | **Others** | masked | masked |
# MAGIC
# MAGIC **Prerequisite (UI):** create two **account-level** groups `hr_group` and `finance_group`
# MAGIC (Settings -> Identity and access -> Groups) and add a user to each.
# MAGIC `is_account_group_member()` only checks **account-level** membership — workspace-local groups are ignored.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Sample employees table
# MAGIC CREATE OR REPLACE TABLE employees (
# MAGIC   emp_id    INT,
# MAGIC   name      STRING,
# MAGIC   dept      STRING,
# MAGIC   salary    DOUBLE
# MAGIC ) USING DELTA;
# MAGIC
# MAGIC INSERT INTO employees VALUES
# MAGIC   (1,'Ava Stone','Engineering', 145000),
# MAGIC   (2,'Liam Reed','Sales',        98000),
# MAGIC   (3,'Mia Lopez','Marketing',   112000),
# MAGIC   (4,'Omar Ali','Engineering',  160000);
# MAGIC
# MAGIC SELECT * FROM employees;

# COMMAND ----------

# MAGIC %md
# MAGIC ## B1. Tag the sensitive columns
# MAGIC Add allowed values `name` and `salary` to the `pii` governed tag (Govern -> Governed tags) first, then apply.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- One ALTER per column (you cannot tag multiple columns in a single statement)
# MAGIC ALTER TABLE employees ALTER COLUMN name   SET TAGS ('pii' = 'name');
# MAGIC ALTER TABLE employees ALTER COLUMN salary SET TAGS ('pii' = 'salary');

# COMMAND ----------

# MAGIC %md
# MAGIC ## B2. Grant table access to the groups
# MAGIC ABAC grants no access on its own — the groups still need read privileges.

# COMMAND ----------

# MAGIC %sql
# MAGIC GRANT USE CATALOG ON CATALOG ${demo.catalog}                TO `hr_group`;
# MAGIC GRANT USE SCHEMA  ON SCHEMA  ${demo.catalog}.${demo.schema} TO `hr_group`;
# MAGIC GRANT SELECT      ON SCHEMA  ${demo.catalog}.${demo.schema} TO `hr_group`;
# MAGIC
# MAGIC GRANT USE CATALOG ON CATALOG ${demo.catalog}                TO `finance_group`;
# MAGIC GRANT USE SCHEMA  ON SCHEMA  ${demo.catalog}.${demo.schema} TO `finance_group`;
# MAGIC GRANT SELECT      ON SCHEMA  ${demo.catalog}.${demo.schema} TO `finance_group`;

# COMMAND ----------

# MAGIC %md
# MAGIC ## B3. Group-aware mask UDFs + policies
# MAGIC Each UDF reveals the value only to its domain group; everyone else (including "Others") gets a masked value.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- NAME: visible only to HR
# MAGIC CREATE OR REPLACE FUNCTION mask_name(name STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN CASE WHEN is_account_group_member('hr_group') THEN name ELSE '***' END;
# MAGIC
# MAGIC CREATE OR REPLACE POLICY mask_employee_name
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COMMENT 'Reveal employee name only to HR'
# MAGIC COLUMN MASK mask_name
# MAGIC TO `account users`
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS has_tag_value('pii','name') AS n
# MAGIC ON COLUMN n;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- SALARY: visible only to Finance. Return type must cast to DOUBLE (use NULL, not a string).
# MAGIC CREATE OR REPLACE FUNCTION mask_salary(salary DOUBLE)
# MAGIC RETURNS DOUBLE
# MAGIC RETURN CASE WHEN is_account_group_member('finance_group') THEN salary ELSE NULL END;
# MAGIC
# MAGIC CREATE OR REPLACE POLICY mask_employee_salary
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COMMENT 'Reveal salary only to Finance'
# MAGIC COLUMN MASK mask_salary
# MAGIC TO `account users`
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS has_tag_value('pii','salary') AS s
# MAGIC ON COLUMN s;

# COMMAND ----------

# MAGIC %md
# MAGIC ## B4. Test
# MAGIC Run as yourself, then log in as the HR user and the Finance user (separate sessions) and compare.
# MAGIC As HR you should see real names but `NULL` salary; as Finance, masked names but real salary.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT current_user() AS me,
# MAGIC        is_account_group_member('hr_group')      AS in_hr,
# MAGIC        is_account_group_member('finance_group') AS in_finance;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM employees;

# COMMAND ----------

# MAGIC %md
# MAGIC ## B5. Managing tags & policies
# MAGIC **Unset a tag** (key only — no value), and **drop a policy** (from its exact scope).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- List policies attached to the schema
# MAGIC SHOW POLICIES ON SCHEMA ${demo.catalog}.${demo.schema};

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Unset a single column tag (key only)
# MAGIC ALTER TABLE employees ALTER COLUMN name UNSET TAGS ('pii');
# MAGIC
# MAGIC -- Drop a policy by name from the scope it is attached to
# MAGIC DROP POLICY IF EXISTS mask_employee_name ON SCHEMA ${demo.catalog}.${demo.schema};

# COMMAND ----------

# MAGIC %md
# MAGIC # Part C — Multi-domain capstone (two tags x two sensitivities)
# MAGIC
# MAGIC One `employee_records` table. Two governed tags work together:
# MAGIC
# MAGIC | Tag | Allowed values | Meaning |
# MAGIC |---|---|---|
# MAGIC | `domain` | `HR`, `finance` | which team owns the column |
# MAGIC | `sensitivity` | `internal`, `confidential` | how hard to mask: internal -> partial, confidential -> full |
# MAGIC
# MAGIC **Prerequisite (UI):** create governed tags `domain` (HR, finance) and `sensitivity` (internal, confidential),
# MAGIC and account-level groups `hr_grp` and `finance_grp`. Grant the groups SELECT (see Part B).

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE employee_records (
# MAGIC   emp_id        INT,
# MAGIC   employee_name STRING,
# MAGIC   ssn           STRING,
# MAGIC   email         STRING,
# MAGIC   cost_center   STRING,
# MAGIC   salary_band   STRING,
# MAGIC   region        STRING,
# MAGIC   department    STRING
# MAGIC ) USING DELTA;
# MAGIC
# MAGIC INSERT INTO employee_records VALUES
# MAGIC   (1,'Ava Stone','111-11-1111','ava@co.com','CC-100','Band-5','US','Engineering'),
# MAGIC   (2,'Liam Reed','222-22-2222','liam@co.com','CC-200','Band-3','US','Sales'),
# MAGIC   (3,'Mia Lopez','333-33-3333','mia@co.com','CC-100','Band-4','EU','Marketing'),
# MAGIC   (4,'Omar Ali','444-44-4444','omar@co.com','CC-300','Band-6','US','Engineering'),
# MAGIC   (5,'Sara Meyer','555-55-5555','sara@co.com','CC-200','Band-2','EU','Support');
# MAGIC
# MAGIC SELECT * FROM employee_records;

# COMMAND ----------

# MAGIC %md
# MAGIC ## C1. Two reusable mask UDFs

# COMMAND ----------

# MAGIC %sql
# MAGIC -- internal => partial reveal (first char + stars)
# MAGIC CREATE OR REPLACE FUNCTION partial_mask(val STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN CONCAT(LEFT(val, 1), '****');
# MAGIC
# MAGIC -- confidential => full mask
# MAGIC CREATE OR REPLACE FUNCTION full_mask(val STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN '****';

# COMMAND ----------

# MAGIC %md
# MAGIC ## C2. Tag columns with BOTH tags
# MAGIC Multiple tags = a **comma-separated tuple**, NOT `AND`. One `ALTER` per column.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE employee_records ALTER COLUMN employee_name
# MAGIC   SET TAGS ('domain' = 'HR', 'sensitivity' = 'internal');
# MAGIC
# MAGIC ALTER TABLE employee_records ALTER COLUMN ssn
# MAGIC   SET TAGS ('domain' = 'HR', 'sensitivity' = 'confidential');
# MAGIC
# MAGIC ALTER TABLE employee_records ALTER COLUMN cost_center
# MAGIC   SET TAGS ('domain' = 'finance', 'sensitivity' = 'internal');
# MAGIC
# MAGIC ALTER TABLE employee_records ALTER COLUMN salary_band
# MAGIC   SET TAGS ('domain' = 'finance', 'sensitivity' = 'confidential');

# COMMAND ----------

# MAGIC %md
# MAGIC ## C3. Four policies = domain x sensitivity
# MAGIC Each policy applies to everyone EXCEPT the owning group, and combines both tags in MATCH COLUMNS with AND.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- HR / internal -> partial mask (everyone except HR)
# MAGIC CREATE OR REPLACE POLICY mask_internal_hr
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COLUMN MASK partial_mask
# MAGIC TO `account users` EXCEPT `hr_grp`
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS (has_tag_value('domain','HR') AND has_tag_value('sensitivity','internal')) AS c
# MAGIC ON COLUMN c;
# MAGIC
# MAGIC -- HR / confidential -> full mask (everyone except HR)
# MAGIC CREATE OR REPLACE POLICY mask_confidential_hr
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COLUMN MASK full_mask
# MAGIC TO `account users` EXCEPT `hr_grp`
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS (has_tag_value('domain','HR') AND has_tag_value('sensitivity','confidential')) AS c
# MAGIC ON COLUMN c;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- finance / internal -> partial mask (everyone except finance)
# MAGIC CREATE OR REPLACE POLICY mask_internal_finance
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COLUMN MASK partial_mask
# MAGIC TO `account users` EXCEPT `finance_grp`
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS (has_tag_value('domain','finance') AND has_tag_value('sensitivity','internal')) AS c
# MAGIC ON COLUMN c;
# MAGIC
# MAGIC -- finance / confidential -> full mask (everyone except finance)
# MAGIC CREATE OR REPLACE POLICY mask_confidential_finance
# MAGIC ON SCHEMA ${demo.catalog}.${demo.schema}
# MAGIC COLUMN MASK full_mask
# MAGIC TO `account users` EXCEPT `finance_grp`
# MAGIC FOR TABLES
# MAGIC MATCH COLUMNS (has_tag_value('domain','finance') AND has_tag_value('sensitivity','confidential')) AS c
# MAGIC ON COLUMN c;

# COMMAND ----------

# MAGIC %md
# MAGIC ## C4. Test by persona
# MAGIC Expected: HR sees name+SSN raw but finance cols masked; finance sees cost_center+salary_band raw but HR cols
# MAGIC masked; "other" users see everything masked (internal -> `X****`, confidential -> `****`).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Who am I, and which policies apply to this table for me?
# MAGIC SELECT current_user() AS me,
# MAGIC        is_account_group_member('hr_grp')      AS in_hr,
# MAGIC        is_account_group_member('finance_grp') AS in_finance;

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW EFFECTIVE POLICIES ON TABLE ${demo.catalog}.${demo.schema}.employee_records;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM employee_records;

# COMMAND ----------

# MAGIC %md
# MAGIC ## C5. Common multi-domain bugs (debugging checklist)
# MAGIC - **Still see raw data?** A `MATCH COLUMNS` tag key that doesn't exist on the column silently matches nothing
# MAGIC   (no error). Confirm key names: `SHOW TAGS` / query `information_schema.column_tags`.
# MAGIC - **"Principal does not exist"** = group-name typo (e.g. `hr_group` vs `hr_grp`). Check Settings -> groups.
# MAGIC - **"Unknown tag policy key"** = tag key mismatch (e.g. `department` vs `domain`).
# MAGIC - Re-runnable: always use `CREATE OR REPLACE POLICY`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Verify the tags actually landed on the columns
# MAGIC SELECT column_name, tag_name, tag_value
# MAGIC FROM information_schema.column_tags
# MAGIC WHERE schema_name = '${demo.schema}' AND table_name = 'employee_records'
# MAGIC ORDER BY column_name, tag_name;

# COMMAND ----------

# MAGIC %md
# MAGIC # Part D — Extra inspection & masking patterns
# MAGIC A few more runnable bits we discussed: inspecting a single policy, scope variants for drop/unset, and
# MAGIC cast-compatible / multi-type masking.

# COMMAND ----------

# MAGIC %md
# MAGIC ## D1. Inspect a single policy
# MAGIC `DESCRIBE POLICY <name> ON {CATALOG|SCHEMA|TABLE} <securable>` shows the policy's properties (principals,
# MAGIC conditions, function, timestamps). `SHOW EFFECTIVE POLICIES` shows what actually resolves for a table+user.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE POLICY mask_internal_hr ON SCHEMA ${demo.catalog}.${demo.schema};

# COMMAND ----------

# MAGIC %md
# MAGIC ## D2. Drop / unset — scope & form variants
# MAGIC Policies are dropped from the **same level** they were attached. Tags can also be unset via the
# MAGIC `UNSET TAG ON COLUMN` statement form (fully-qualified column path, key only).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- DROP POLICY scope variants (drop from wherever the policy was attached):
# MAGIC --   DROP POLICY my_policy ON CATALOG ${demo.catalog};
# MAGIC --   DROP POLICY my_policy ON TABLE   ${demo.catalog}.${demo.schema}.profiles;
# MAGIC
# MAGIC -- Equivalent UNSET TAG statement form (key only, fully-qualified column):
# MAGIC --   UNSET TAG ON COLUMN ${demo.catalog}.${demo.schema}.profiles.address pii;
# MAGIC SELECT 'scope/form variants shown as comments above' AS note;

# COMMAND ----------

# MAGIC %md
# MAGIC ## D3. Cast-compatible masking (numeric) + quick test
# MAGIC A mask UDF's return must cast to the column type. For a numeric column, return numbers (or `NULL`) in
# MAGIC **every** branch — never a string like `'CONFIDENTIAL'`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Salary is DOUBLE: every branch returns a DOUBLE-compatible value
# MAGIC CREATE OR REPLACE FUNCTION mask_salary_role(salary DOUBLE, role STRING)
# MAGIC RETURNS DOUBLE
# MAGIC RETURN CASE
# MAGIC   WHEN role IN ('admin','hr') THEN salary
# MAGIC   WHEN role = 'manager'       THEN ROUND(salary / 1000) * 1000   -- coarse-grained
# MAGIC   ELSE 0.0
# MAGIC END;
# MAGIC
# MAGIC -- Quick cast test (run before attaching to a policy)
# MAGIC SELECT
# MAGIC   CAST(mask_salary_role(145000, 'hr')      AS DOUBLE) AS as_hr,
# MAGIC   CAST(mask_salary_role(145000, 'manager') AS DOUBLE) AS as_manager,
# MAGIC   CAST(mask_salary_role(145000, 'viewer')  AS DOUBLE) AS as_viewer;

# COMMAND ----------

# MAGIC %md
# MAGIC ## D4. One mask for many column types — VARIANT
# MAGIC Instead of a separate UDF per numeric precision, accept/return `VARIANT`; Databricks auto-casts the output
# MAGIC to the target column's type. Reduces the number of UDFs and policies.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Masks INT / DOUBLE / DECIMAL columns with a single function
# MAGIC CREATE OR REPLACE FUNCTION mask_numeric(val VARIANT)
# MAGIC RETURNS VARIANT
# MAGIC DETERMINISTIC
# MAGIC RETURN 0::VARIANT;
# MAGIC
# MAGIC -- Type-aware variant (branch on the value's type)
# MAGIC CREATE OR REPLACE FUNCTION flexible_mask(data VARIANT)
# MAGIC RETURNS VARIANT
# MAGIC RETURN CASE
# MAGIC   WHEN schema_of_variant(data) = 'INT'    THEN 0::VARIANT
# MAGIC   WHEN schema_of_variant(data) = 'DOUBLE' THEN 0.00::VARIANT
# MAGIC   WHEN schema_of_variant(data) = 'DATE'   THEN DATE'1970-01-01'::VARIANT
# MAGIC   ELSE NULL::VARIANT
# MAGIC END;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Cleanup (optional)
# MAGIC Drop policies first, then tags/tables. (Dropping a column that still has a governed tag fails — unset first.)

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP POLICY IF EXISTS hide_eu_customers   ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC DROP POLICY IF EXISTS data_mask_ssn        ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC DROP POLICY IF EXISTS mask_employee_name   ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC DROP POLICY IF EXISTS mask_employee_salary     ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC DROP POLICY IF EXISTS mask_internal_hr          ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC DROP POLICY IF EXISTS mask_confidential_hr      ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC DROP POLICY IF EXISTS mask_internal_finance     ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC DROP POLICY IF EXISTS mask_confidential_finance ON SCHEMA ${demo.catalog}.${demo.schema};
# MAGIC
# MAGIC ALTER TABLE profiles   ALTER COLUMN address UNSET TAGS ('pii');
# MAGIC ALTER TABLE profiles   ALTER COLUMN ssn     UNSET TAGS ('pii');
# MAGIC ALTER TABLE profiles_2 ALTER COLUMN address UNSET TAGS ('pii');
# MAGIC ALTER TABLE employees  ALTER COLUMN salary  UNSET TAGS ('pii');
# MAGIC -- (employees.name tag was already unset in B5)
# MAGIC
# MAGIC ALTER TABLE employee_records ALTER COLUMN employee_name UNSET TAGS ('domain', 'sensitivity');
# MAGIC ALTER TABLE employee_records ALTER COLUMN ssn           UNSET TAGS ('domain', 'sensitivity');
# MAGIC ALTER TABLE employee_records ALTER COLUMN cost_center   UNSET TAGS ('domain', 'sensitivity');
# MAGIC ALTER TABLE employee_records ALTER COLUMN salary_band   UNSET TAGS ('domain', 'sensitivity');
# MAGIC
# MAGIC -- DROP TABLE IF EXISTS profiles;
# MAGIC -- DROP TABLE IF EXISTS profiles_2;
# MAGIC -- DROP TABLE IF EXISTS employees;
# MAGIC -- DROP TABLE IF EXISTS employee_records;
# MAGIC -- DROP FUNCTION IF EXISTS non_eu_filter;
# MAGIC -- DROP FUNCTION IF EXISTS mask_ssn;
# MAGIC -- DROP FUNCTION IF EXISTS mask_name;
# MAGIC -- DROP FUNCTION IF EXISTS mask_salary;
# MAGIC -- DROP FUNCTION IF EXISTS partial_mask;
# MAGIC -- DROP FUNCTION IF EXISTS full_mask;
# MAGIC -- DROP FUNCTION IF EXISTS mask_salary_role;
# MAGIC -- DROP FUNCTION IF EXISTS mask_numeric;
# MAGIC -- DROP FUNCTION IF EXISTS flexible_mask;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Gotchas recap
# MAGIC - **Row filter polarity:** UDF returns `TRUE` = keep, `FALSE` = hide.
# MAGIC - **Columns don't inherit tags** — tag each sensitive column directly.
# MAGIC - **Wrong tag value** = policy silently doesn't apply (no error, data unprotected).
# MAGIC - **One row filter per table** and **one mask per column** per user — overlaps throw
# MAGIC   `UC_ABAC_MULTIPLE_ROW_FILTERS` / `MULTIPLE_MASKS` and block the table.
# MAGIC - **Quotas:** 10 policies/catalog, 10/schema, 5/table, 20 principals/policy.
# MAGIC - **ABAC grants no access** — users still need `SELECT`.
# MAGIC - `has_tag` / `has_tag_value` (snake_case) preferred; camelCase `hasTag`/`hasTagValue` deprecated for new policies.

# COMMAND ----------

# MAGIC %md
# MAGIC # Reference — Do's & Don'ts (UDF performance)
# MAGIC The policy UDF runs for **every row** (filters) or **every matching value** (masks), so keep it cheap.
# MAGIC These are strong guidelines, not hard rules.
# MAGIC
# MAGIC **✅ Do**
# MAGIC - Keep UDFs simple — favor basic `CASE` statements and simple boolean expressions.
# MAGIC - Reference **only the target table's columns** (enables predicate pushdown).
# MAGIC - If you must reference an external table, keep it **small enough to broadcast**; partition it to match the
# MAGIC   access pattern (e.g. by username).
# MAGIC - Prefer **SQL UDFs** over Python UDFs; mark functions `DETERMINISTIC`.
# MAGIC - Use error-safe `try_cast` / `try_divide` so the optimizer can reorder/push filters.
# MAGIC - **Reuse** one mask function across columns; mask only truly sensitive columns.
# MAGIC
# MAGIC **❌ Avoid**
# MAGIC - External **API calls** or cross-database lookups (latency, timeouts).
# MAGIC - Complex **subqueries / joins** against large tables (forces slow nested-loop joins).
# MAGIC - Heavy **regex** on large text fields (XML/JSON blobs) — scans the whole payload per row.
# MAGIC - Multi-level **nesting** and unnecessary function calls.
# MAGIC - **Per-row metadata lookups** (e.g. querying `information_schema`).
# MAGIC - Non-deterministic functions (`rand()`, `now()`) — they block result caching.
# MAGIC
# MAGIC **SecureView barrier:** any policy on a table blocks side-effecting predicates from being pushed to storage,
# MAGIC so `WHERE func(col)=…` forces a full scan while `WHERE col='x'` still prunes partitions. Favor simple
# MAGIC equality predicates; `EXCEPT` removes the barrier for exempt users. **Test on ≥1M rows** before production.

# COMMAND ----------

# MAGIC %md
# MAGIC # Reference — When to use ABAC vs table-level RLS/CLS
# MAGIC Both sit **on top of** grants (neither grants access). The deciding factor is **scale + governance**.
# MAGIC
# MAGIC | Consideration | ABAC policies | Table-level RLS/CLS |
# MAGIC |---|---|---|
# MAGIC | Syntax | `CREATE POLICY … ON CATALOG/SCHEMA/TABLE` | `ALTER TABLE … SET ROW FILTER` / `SET MASK` |
# MAGIC | Scope | Catalog/schema/table + descendants; new tagged tables auto-covered | One table; configured individually |
# MAGIC | Matching | Dynamic, by governed tags (`has_tag`/`has_tag_value`) | Bound to specific tables/columns |
# MAGIC | Governance | Set by catalog/schema owner; table owners **cannot bypass** | Managed by table owner (who can also remove it) |
# MAGIC | Maintenance | Define once, reuse everywhere | Per-table logic to maintain |
# MAGIC
# MAGIC **Use ABAC when:** many tables need consistent rules, duties are separated (taggers vs policy authors), the
# MAGIC estate is growing (auto-coverage of new tagged tables), or admins must enforce rules owners can't circumvent.
# MAGIC
# MAGIC **Use table-level RLS/CLS when:** a small, stable set of tables each needs bespoke logic that doesn't
# MAGIC generalize, and table owners should manage their own protection without a central tag system.
# MAGIC
# MAGIC They can **coexist** (still one row filter per table & one mask per column per user). A third option,
# MAGIC **dynamic views**, fits control spanning multiple source tables, but views lack tag-based auditing and the
# MAGIC anti-probing `SecureView` barrier. **One line:** many tables + central governance → ABAC; few, bespoke,
# MAGIC stable tables → table-level.
# MAGIC
# MAGIC **Sources:** [Performance](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/performance) ·
# MAGIC [ABAC vs RLS/CM](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/abac-vs-rls-cm) ·
# MAGIC [Create & manage policies](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policies)
