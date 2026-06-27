# =============================================================================
# Topic 9 — Networking & Security best-practices checklist, AS CODE (companion to 9.2)
#
# This is a GUARDRAILS overlay: the recommended hardening baseline expressed as
# Terraform, with each resource/argument mapped to a checklist item. Apply it
# alongside the reference architecture in main.tf (it takes the workspace, storage,
# and Log Analytics IDs as inputs so it can be used standalone or layered on).
#
#   CHECKLIST ITEM                                  ENFORCED BY
#   --------------------------------------------    ---------------------------------
#   [N1] No Public IP / Secure Cluster Connectivity custom_parameters.no_public_ip   (main.tf)
#   [N2] VNet injection (own subnets/NSG/UDR)       custom_parameters.virtual_network (main.tf)
#   [N3] Public network access disabled             public_network_access_enabled=false (main.tf)
#   [N4] Back-end Private Link                       databricks_ui_api PE             (main.tf)
#   [N5] Stable egress via NAT Gateway              azurerm_nat_gateway              (main.tf)
#   [N6] Storage default-deny + backbone/private    THIS FILE (azurerm_storage_account_network_rules)
#   [S1] Premium tier                                sku="premium"                    (main.tf)
#   [S2] Customer-Managed Keys (services + disks)    THIS FILE (commented CMK block)
#   [S3] Infrastructure (double) encryption          infrastructure_encryption_enabled (CMK block)
#   [S4] Audit + diagnostic logging to a SIEM        THIS FILE (diagnostic setting)
#   [S5] IP access lists for the front door          databricks provider / CLI (note below)
#   [S6] Unity Catalog for governed data             account-level (Stage 7)
#
# Prerequisites: an existing workspace, ADLS account, and Log Analytics workspace
#   (pass their resource IDs). "Monitoring Contributor" + storage RBAC on the RG.
# Cost caveat: Log Analytics ingestion/retention is billed per-GB.
# =============================================================================

terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 3.0" }
  }
}

variable "workspace_resource_id" {
  type        = string
  description = "Resource ID of the azurerm_databricks_workspace to harden/monitor."
}
variable "storage_account_id" {
  type        = string
  description = "Resource ID of the ADLS Gen2 account to lock down (Path 3)."
}
variable "workspace_subnet_ids" {
  type        = list(string)
  description = "The host + container subnet IDs allowed through the storage firewall."
}
variable "log_analytics_workspace_id" {
  type        = string
  description = "Log Analytics workspace ID for audit/diagnostic log delivery."
}

# ---------------------------------------------------------------------------
# [N6] Storage default-deny + allow only the workspace subnets over the backbone.
#      The FREE alternative to a Private Endpoint — backbone-private, no per-GB PE
#      cost. (For serverless, allow the AzureDatabricksServerless service tag via
#      an NSP instead — see Stage 5; can't be expressed as a subnet rule.)
# ---------------------------------------------------------------------------
resource "azurerm_storage_account_network_rules" "lockdown" {
  storage_account_id         = var.storage_account_id
  default_action             = "Deny"                   # default-deny is the baseline
  bypass                     = ["AzureServices"]        # let trusted Azure services through
  virtual_network_subnet_ids = var.workspace_subnet_ids # only the workspace subnets
}

# ---------------------------------------------------------------------------
# [S4] Audit + diagnostic logging to Log Analytics (feed your SIEM).
#      Databricks emits many diagnostic categories; enable the security-relevant
#      ones at minimum. (Account-level audit logs / system.access tables are the
#      richer source — Stage 8 — but workspace diagnostic logs are the Azure-native
#      baseline.)
# ---------------------------------------------------------------------------
resource "azurerm_monitor_diagnostic_setting" "workspace" {
  name                       = "adb-audit-to-law"
  target_resource_id         = var.workspace_resource_id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log { category = "accounts" }     # login / identity events
  enabled_log { category = "clusters" }     # cluster create/edit/delete
  enabled_log { category = "secrets" }      # secret-scope access
  enabled_log { category = "unityCatalog" } # UC grants / data access decisions
  enabled_log { category = "workspace" }    # workspace config changes
  # Add the remaining categories (jobs, notebook, sqlPermissions, ...) per your
  # retention policy — verify the current category list in the Azure Monitor docs.
}

# ---------------------------------------------------------------------------
# [S2]/[S3] Customer-Managed Keys + infrastructure encryption (COMMENTED — needs
#           real Key Vault keys with purge protection + soft delete). These args
#           go on the azurerm_databricks_workspace itself; shown here as the
#           checklist reference. Uncomment and wire to your Key Vault keys.
# ---------------------------------------------------------------------------
#   infrastructure_encryption_enabled = true   # [S3] double encryption at rest
#   customer_managed_key_enabled      = true   # [S2] enable CMK on the workspace
#   managed_services_cmk_key_vault_key_id = azurerm_key_vault_key.services.id  # notebooks/secrets
#   managed_disk_cmk_key_vault_key_id     = azurerm_key_vault_key.disks.id     # cluster VM disks
#   managed_disk_cmk_rotation_to_latest_version_enabled = true

# ---------------------------------------------------------------------------
# [S5] IP access lists for the front door are a Databricks workspace object, not
#      an azurerm resource. With the databricks provider:
#        resource "databricks_ip_access_list" "corp" {
#          label = "corp-vpn"; list_type = "ALLOW"; ip_addresses = ["203.0.113.0/24"]
#        }
#      or CLI: databricks ip-access-lists create --json '{...}'
# ---------------------------------------------------------------------------

output "hardening_applied" {
  value = "Storage default-deny + diagnostic logging applied. Verify CMK / IP-ACL items per the comments."
}
