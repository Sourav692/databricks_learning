-- ============================================================================
-- Topic 8.4 — Audit Logs, System Tables & Monitoring (Azure Databricks)
-- Companion SQL for the consolidated Stage 8 lesson.
-- Run top-to-bottom on a UC-enabled SQL warehouse. Grants run as a METASTORE ADMIN.
-- All queries filter on event_date (REQUIRED — system tables reject broad scans).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Least-privilege access to the audit log for a security-analyst group.
--    (Account/metastore admins already have access; everyone else needs grants.)
-- ----------------------------------------------------------------------------
GRANT USE CATALOG ON CATALOG system            TO `secops-analysts`;
GRANT USE SCHEMA  ON SCHEMA  system.access      TO `secops-analysts`;
GRANT SELECT      ON TABLE   system.access.audit TO `secops-analysts`;
-- Repeat USE SCHEMA + SELECT for system.billing / system.query as needed (least privilege).

-- ----------------------------------------------------------------------------
-- 2) Failed-login burst — the canonical security alert.
--    Use as a Databricks SQL Alert: schedule every 5 min, condition failed_logins >= 5.
-- ----------------------------------------------------------------------------
SELECT
  user_identity.email            AS user_email,
  count(*)                       AS failed_logins,
  max(event_time)                AS last_attempt,
  collect_set(source_ip_address) AS source_ips
FROM system.access.audit
WHERE event_date >= current_date() - INTERVAL 1 DAY          -- prune partitions
  AND service_name = 'accounts'
  AND action_name IN ('login','aadBrowserLogin','tokenLogin')
  AND response.statusCode <> 200                             -- 200 = success; non-200 = failed/denied
  AND event_time >= current_timestamp() - INTERVAL 15 MINUTES
GROUP BY user_identity.email
HAVING failed_logins >= 5;                                   -- alert threshold

-- ----------------------------------------------------------------------------
-- 3) Who accessed a sensitive UC table in the last 7 days? (incident response)
-- ----------------------------------------------------------------------------
SELECT event_time, user_identity.email AS actor, action_name,
       request_params['full_name_arg'] AS securable, source_ip_address
FROM system.access.audit
WHERE event_date >= current_date() - INTERVAL 7 DAY
  AND service_name = 'unityCatalog'
  AND action_name IN ('getTable','generateTemporaryTableCredential')
  AND request_params['full_name_arg'] = 'prod.finance.salaries'
ORDER BY event_time DESC;

-- ----------------------------------------------------------------------------
-- 4) Permission / grant changes — privilege-escalation watch.
-- ----------------------------------------------------------------------------
SELECT event_time, user_identity.email AS actor, action_name, request_params
FROM system.access.audit
WHERE event_date >= current_date() - INTERVAL 7 DAY
  AND service_name = 'unityCatalog'
  AND action_name IN ('updatePermissions','updateSharePermissions')
ORDER BY event_time DESC;

-- ----------------------------------------------------------------------------
-- 5) Personal access tokens generated (account-level too: workspace_id = 0).
--    NOTE: account-level rows exist ONLY here — never in Azure diagnostic settings.
-- ----------------------------------------------------------------------------
SELECT event_time, workspace_id, user_identity.email AS actor, action_name
FROM system.access.audit
WHERE event_date >= current_date() - INTERVAL 30 DAY
  AND service_name = 'accounts'
  AND action_name = 'generateDbToken'
ORDER BY event_time DESC;

-- ----------------------------------------------------------------------------
-- 6) ESM agent findings (8.3) — surface file-integrity (capsule8) / antivirus (clamAV)
--    rows for SIEM triage. Databricks emits these; review/triage is the customer's job.
-- ----------------------------------------------------------------------------
SELECT event_time, workspace_id, service_name, action_name, request_params
FROM system.access.audit
WHERE event_date >= current_date() - INTERVAL 7 DAY
  AND (action_name ILIKE '%capsule8%' OR action_name ILIKE '%clamAV%'
       OR service_name IN ('capsule8','clamAVScanService'))
ORDER BY event_time DESC;

-- ----------------------------------------------------------------------------
-- 7) Cost-anomaly watch (system.billing is GA + free) — DBU spend by day/SKU.
-- ----------------------------------------------------------------------------
SELECT usage_date, sku_name, round(sum(usage_quantity), 2) AS dbus
FROM system.billing.usage
WHERE usage_date >= current_date() - INTERVAL 30 DAY
GROUP BY usage_date, sku_name
ORDER BY usage_date DESC, dbus DESC;

-- ============================================================================
-- Verify GA/Preview status and the exact diagnostic-log category list against
-- current docs before contractual commitments — the audit table is Public Preview
-- and column/category sets drift.
-- ============================================================================
