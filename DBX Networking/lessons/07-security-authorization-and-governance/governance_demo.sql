-- =============================================================================
-- Topic 7 — Security, Authorization & Governance — runnable SQL companion (Azure-first)
-- =============================================================================
-- ONE SQL companion for the whole topic. Covers:
--   * 7.1 — Unity Catalog hierarchy + least-privilege grants (+ workspace binding)
--   * 7.2 — FGAC: dynamic view, table-level row filter/column mask, ABAC policies
--   * 7.3 — Storage credential + external location (the keyless ADLS Gen2 chain)
--
-- Goal: build a small catalog.schema.table hierarchy, apply grants exactly as you
--       would in a customer engagement, demonstrate all three FGAC mechanisms, wire
--       up the access-connector credential chain, then clean up.
--
-- Prerequisites:
--   * A Unity Catalog-enabled workspace + a UC-enabled SQL warehouse or cluster
--     (Standard access mode). Workspaces created after 2023-11-09 are auto-enabled.
--   * You can CREATE a catalog (default workspace admins have CREATE CATALOG on the
--     metastore) OR run inside an existing catalog you own.
--   * For the ABAC section (7.2): use SERVERLESS or DBR >= 16.4 (standard/dedicated).
--     Table-level filters/masks need DBR >= 12.2 LTS (standard) / >= 15.4 LTS (dedicated).
--   * ACCOUNT GROUPS named below must already exist (synced from Microsoft Entra ID via
--     SCIM). Replace data-analysts / data-engineers / us_analysts / admins / etl_sp etc.
--   * 7.3 assumes an Access Connector for Azure Databricks already exists and its
--     managed identity holds an RBAC role (e.g. Storage Blob Data Contributor) on ADLS.
--   * Workspace-catalog binding is NOT settable in SQL — see the CLI block in section 7.1.
--   * Governed tags need account-level CREATE (account/workspace admin); CREATE GOVERNED
--     TAG SQL needs DBR 18.1+, otherwise create the tag in the Account Console UI.
--
-- Run top-to-bottom; the final section drops everything created here.
-- Privilege names use SPACES in SQL (USE CATALOG), UNDERSCORES in Terraform.
-- Recommended: run with ANSI mode ON so UDF type mismatches error loudly.
SET spark.sql.ansi.enabled = true;


-- #############################################################################
-- # 7.1 — UNITY CATALOG HIERARCHY & GRANTS
-- #############################################################################

-- ── 1. Build the three-level namespace: catalog -> schema -> table/view/volume ──
CREATE CATALOG IF NOT EXISTS demo_gov
  COMMENT 'Topic 7 demo catalog (a data DOMAIN).';

CREATE SCHEMA IF NOT EXISTS demo_gov.sales
  COMMENT 'A project/use-case grouping (a "room").';

CREATE TABLE IF NOT EXISTS demo_gov.sales.orders (
  order_id   BIGINT,
  customer   STRING,
  region     STRING,
  amount     DECIMAL(12,2),
  ssn        STRING            -- pretend-sensitive column, masked via the view below
) COMMENT 'Managed table — UC owns the storage lifecycle in your ADLS Gen2.';

INSERT INTO demo_gov.sales.orders VALUES
  (1,'Contoso','eastus',1200.00,'111-22-3333'),
  (2,'Fabrikam','westus', 880.50,'444-55-6666');

-- A VIEW that hides the sensitive column. The VIEW OWNER's privileges resolve the
-- base table, so a viewer needs SELECT on the view but NOT on demo_gov.sales.orders.
CREATE OR REPLACE VIEW demo_gov.sales.orders_safe AS
  SELECT order_id, customer, region, amount FROM demo_gov.sales.orders;

-- ── 2. Least-privilege READ for the analysts team (grant to a GROUP) ──────────
-- A reader needs ALL THREE gates together: enter catalog, enter schema, read object.
GRANT USE CATALOG ON CATALOG demo_gov              TO `data-analysts`;  -- enter floor
GRANT USE SCHEMA  ON SCHEMA  demo_gov.sales        TO `data-analysts`;  -- enter room
GRANT SELECT      ON VIEW    demo_gov.sales.orders_safe TO `data-analysts`; -- masked read only
-- NOTE: analysts get NO grant on demo_gov.sales.orders — they can never see `ssn`.

-- ── 3. READ+WRITE + create rights for the engineers team ─────────────────────
GRANT USE CATALOG ON CATALOG demo_gov        TO `data-engineers`;
-- Inheritance: granting on the SCHEMA cascades to ALL current + FUTURE objects in it.
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE
  ON SCHEMA demo_gov.sales TO `data-engineers`;

-- ── 4. Discovery without data access (BROWSE is catalog-level only) ──────────
GRANT BROWSE ON CATALOG demo_gov TO `account users`;

-- ── 5. (Optional) functions/models use EXECUTE; volumes use READ/WRITE VOLUME ─
-- GRANT EXECUTE ON FUNCTION demo_gov.sales.some_udf TO `ml-team`;
-- CREATE VOLUME demo_gov.sales.raw_files;
-- GRANT READ VOLUME ON VOLUME demo_gov.sales.raw_files TO `data-analysts`;

-- ── 6. Inspect what is granted (run as owner / MANAGE / metastore admin) ─────
SHOW GRANTS ON SCHEMA demo_gov.sales;            -- all grants on the schema
SHOW GRANTS ON TABLE  demo_gov.sales.orders;     -- confirm analysts are NOT here
DESCRIBE SCHEMA EXTENDED demo_gov.sales;         -- shows the owner

-- ── 7. Transfer ownership to a GROUP (collaborative admin; least-privilege) ──
-- ALTER SCHEMA demo_gov.sales OWNER TO `sales-admins`;

-- ── 8. Revoke (idempotent — succeeds even if the grant was not present) ──────
-- REVOKE SELECT ON VIEW demo_gov.sales.orders_safe FROM `data-analysts`;

-- =============================================================================
-- WORKSPACE-CATALOG BINDING is not a SQL operation. Run via the Databricks CLI:
--
--   databricks catalogs update demo_gov --isolation-mode ISOLATED --profile <p>
--   databricks workspace-bindings update-bindings catalog demo_gov \
--     --json '{"add":[{"workspace_id":<id>,"binding_type":"BINDING_TYPE_READ_WRITE"}]}' \
--     --profile <p>
--
-- After binding, unbound workspaces are DENIED even for users with a valid SELECT.
-- =============================================================================


-- #############################################################################
-- # 7.2 — ABAC, ROW FILTERS & COLUMN MASKS (three FGAC mechanisms)
-- #############################################################################

-- ---------------------------------------------------------------------
-- 7.2.0 — Sample data
-- ---------------------------------------------------------------------
CREATE SCHEMA  IF NOT EXISTS demo_gov.customers;
CREATE SCHEMA  IF NOT EXISTS demo_gov.governance;   -- home for reusable policy UDFs

CREATE OR REPLACE TABLE demo_gov.customers.accounts (
  name        STRING,
  geo_region  STRING,      -- 'us' / 'eu'
  ssn         STRING,
  balance     DECIMAL(12,2)
);
INSERT INTO demo_gov.customers.accounts VALUES
  ('Alice','us','123-45-6789', 5000.00),
  ('Bob',  'eu','987-65-4321', 9000.00),
  ('Chen', 'us','555-12-3456', 1500.00),
  ('Dana', 'eu','222-33-4444', 7200.00);

-- =====================================================================
-- MECHANISM 1 — DYNAMIC VIEW  (curated/transformed secure layer)
-- =====================================================================
-- Mask the SSN for everyone except the `auditors` account group, and filter EU
-- rows for non-`managers`. Users get SELECT on the VIEW, never the base table.
CREATE OR REPLACE VIEW demo_gov.customers.accounts_secure AS
SELECT
  name,
  geo_region,
  CASE WHEN is_account_group_member('auditors')           -- account group, NOT is_member()
       THEN ssn ELSE '***-**-****' END AS ssn,
  balance
FROM demo_gov.customers.accounts
WHERE CASE WHEN is_account_group_member('managers') THEN TRUE
           ELSE geo_region = 'us' END;                      -- non-managers: US rows only

-- GRANT SELECT ON VIEW demo_gov.customers.accounts_secure TO `us_analysts`;  -- view only!

-- =====================================================================
-- MECHANISM 2 — TABLE-LEVEL ROW FILTER + COLUMN MASK  (one table)
-- =====================================================================
-- 2a. Row filter UDF — RETURNS BOOLEAN. admins see all; others limited to US.
CREATE OR REPLACE FUNCTION demo_gov.governance.us_only(region STRING)
  RETURN IF(is_account_group_member('admins'), true, region = 'us');

-- 2b. Column mask UDF — returns the column type (STRING). HR sees raw SSN.
CREATE OR REPLACE FUNCTION demo_gov.governance.ssn_mask(ssn STRING)
  RETURN CASE WHEN is_account_group_member('HumanResourceDept') THEN ssn
              ELSE '***-**-****' END;

-- 2c. Bind them to the base table.
ALTER TABLE demo_gov.customers.accounts
  SET ROW FILTER demo_gov.governance.us_only ON (geo_region);
ALTER TABLE demo_gov.customers.accounts
  ALTER COLUMN ssn SET MASK demo_gov.governance.ssn_mask;

-- Test: SELECT * FROM demo_gov.customers.accounts;   -- result varies by caller

-- 2d. Unbind (MUST drop binding BEFORE the function, or the table locks up).
ALTER TABLE demo_gov.customers.accounts DROP ROW FILTER;
ALTER TABLE demo_gov.customers.accounts ALTER COLUMN ssn DROP MASK;

-- =====================================================================
-- MECHANISM 3 — ABAC  (governed tag + one policy, scales across tables)
-- =====================================================================
-- 3a. Governed tags (DBR 18.1+ for SQL; else create in Account Console UI).
CREATE GOVERNED TAG pii
  DESCRIPTION 'Kind of personal data the column holds'
  VALUES ('ssn','ccn','dob');
CREATE GOVERNED TAG sensitivity_level VALUES ('low','medium','high');

-- 3b. Tag the data (the "attributes"). Schema/catalog tags inherit to tables;
--     columns must be tagged explicitly.
ALTER TABLE  demo_gov.customers.accounts ALTER COLUMN ssn SET TAGS ('pii' = 'ssn');
ALTER TABLE  demo_gov.customers.accounts SET TAGS ('sensitivity_level' = 'high');
ALTER TABLE  demo_gov.customers.accounts ALTER COLUMN geo_region SET TAGS ('geo_region' = 'true');

-- 3c. Reusable policy UDFs.
CREATE OR REPLACE FUNCTION demo_gov.governance.ssn_to_last_nr(ssn STRING, nr INT)
  RETURN right(ssn, nr);
CREATE OR REPLACE FUNCTION demo_gov.governance.non_eu_region(geo_region STRING)
  RETURNS BOOLEAN RETURN geo_region <> 'eu';

-- 3d. COLUMN MASK policy — auto-masks EVERY pii=ssn column in the schema.
--     us_analysts get last-4; admins & the ETL service principal are EXEMPT (raw).
CREATE OR REPLACE POLICY mask_ssn
ON SCHEMA demo_gov.customers
COMMENT 'Mask pii=ssn columns to last 4 for analysts'
COLUMN MASK demo_gov.governance.ssn_to_last_nr
TO us_analysts EXCEPT admins, etl_sp
FOR TABLES
MATCH COLUMNS has_tag_value('pii','ssn') AS ssn
ON COLUMN ssn
USING COLUMNS (4);

-- 3e. ROW FILTER policy — hide EU rows on tables tagged sensitivity_level=high.
--     etl_sp is EXEMPT so pipeline refresh / time travel / clone still work.
CREATE OR REPLACE POLICY hide_eu_customers
ON SCHEMA demo_gov.customers
COMMENT 'Exclude EU rows from high-sensitivity tables for analysts'
ROW FILTER demo_gov.governance.non_eu_region
TO us_analysts EXCEPT etl_sp
FOR TABLES
WHEN has_tag_value('sensitivity_level','high')      -- which TABLES
MATCH COLUMNS has_tag('geo_region') AS region       -- which COLUMN feeds the UDF
USING COLUMNS (region);

-- 3f. Inspect.
SHOW EFFECTIVE POLICIES ON SCHEMA demo_gov.customers;   -- includes parent-scope (inherited)
DESCRIBE POLICY hide_eu_customers ON SCHEMA demo_gov.customers;

-- 3g. Audit: who created/changed policies and tag assignments (system tables).
SELECT event_time, action_name, user_identity.email AS actor,
       request_params.name AS policy_name, response.status_code
FROM   system.access.audit
WHERE  service_name = 'unityCatalog'
  AND  action_name IN ('createPolicy','deletePolicy',
                       'createEntityTagAssignment','deleteEntityTagAssignment')
ORDER  BY event_time DESC
LIMIT  50;


-- #############################################################################
-- # 7.3 — STORAGE CREDENTIALS, EXTERNAL LOCATIONS & THE ACCESS CONNECTOR
-- #############################################################################
-- The keyless chain to ADLS Gen2. Order is always: (1) Access Connector ->
-- (2) RBAC on ADLS -> (3) storage credential -> (4) external location -> (5) grants.
-- Steps (1)-(2) are Azure-side (Portal / az CLI); (3)-(5) are the SQL below.
-- Replace <sub>/<rg>/<name>/<storage account>/<container> with your values.

-- 1) Storage credential wraps the access connector's managed identity. No secret stored.
CREATE STORAGE CREDENTIAL IF NOT EXISTS `finance_mi`
  WITH AZURE_MANAGED_IDENTITY
    ACCESS_CONNECTOR_ID = '/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Databricks/accessConnectors/<name>'
    -- MANAGED_IDENTITY_ID = '…/userAssignedIdentities/<uami>'   -- ONLY if user-assigned
  COMMENT 'MI for the finance ADLS account';

-- 2) External location binds an abfss:// path to that credential. This is what you GRANT on.
CREATE EXTERNAL LOCATION IF NOT EXISTS `finance_lake`
  URL 'abfss://finance@mystorageacct.dfs.core.windows.net/'
  WITH (STORAGE CREDENTIAL `finance_mi`)
  COMMENT 'Governed path into the finance ADLS Gen2 container (HNS enabled).';

-- 3) Path-scoped grants — what enforcement keys on.
GRANT READ FILES, WRITE FILES ON EXTERNAL LOCATION `finance_lake` TO `data-engineers`;
GRANT CREATE EXTERNAL TABLE    ON EXTERNAL LOCATION `finance_lake` TO `data-engineers`;

-- 4) Validate identity vs network split (run from Catalog Explorer → Test connection,
--    or inspect here). Authorization failures => RBAC/identity half; timeouts => network.
DESCRIBE EXTERNAL LOCATION `finance_lake`;

-- Lock ADLS to ONLY this connector (storage-firewall pattern) is an Azure-side step:
--   az storage account network-rule add \
--     --resource-id ".../Microsoft.Databricks/accessConnectors/<name>" \
--     --tenant-id "<tenant>" -g "<storage-rg>" --account-name "<storage-account>"
-- Then CLEAR "Allow Azure services on the trusted services list…" so ONLY the named
-- connector (a Resource instance) is trusted — not every Azure service.


-- #############################################################################
-- # CLEANUP — remove everything this script created
-- #############################################################################
-- 7.2 policies + FGAC objects
DROP POLICY IF EXISTS mask_ssn          ON SCHEMA demo_gov.customers;
DROP POLICY IF EXISTS hide_eu_customers ON SCHEMA demo_gov.customers;
DROP VIEW   IF EXISTS demo_gov.customers.accounts_secure;
DROP TABLE  IF EXISTS demo_gov.customers.accounts;
DROP FUNCTION IF EXISTS demo_gov.governance.us_only;
DROP FUNCTION IF EXISTS demo_gov.governance.ssn_mask;
DROP FUNCTION IF EXISTS demo_gov.governance.ssn_to_last_nr;
DROP FUNCTION IF EXISTS demo_gov.governance.non_eu_region;

-- 7.3 storage objects (drop external location BEFORE its credential).
DROP EXTERNAL LOCATION IF EXISTS `finance_lake`;
DROP STORAGE CREDENTIAL IF EXISTS `finance_mi`;

-- 7.1 namespace
DROP VIEW   IF EXISTS demo_gov.sales.orders_safe;
DROP TABLE  IF EXISTS demo_gov.sales.orders;
DROP SCHEMA IF EXISTS demo_gov.governance;
DROP SCHEMA IF EXISTS demo_gov.customers;
DROP SCHEMA IF EXISTS demo_gov.sales;
DROP CATALOG IF EXISTS demo_gov;   -- add CASCADE if non-empty objects remain

-- Governed tags are account-level; drop only if you created them for this demo:
-- DROP GOVERNED TAG pii;
-- DROP GOVERNED TAG sensitivity_level;
