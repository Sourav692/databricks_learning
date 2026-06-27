# =============================================================================
# Goal: Give Azure Databricks SERVERLESS compute private/firewalled access to your
#       Azure resources via a Network Connectivity Configuration (NCC).
#
# What it provisions:
#   - An account-level, REGIONAL NCC.
#   - NCC private endpoint rules into ADLS Gen2 (dfs + blob subresources).
#   - (Optional) a private endpoint rule into an Azure SQL logical server.
#   - A binding of the NCC to a workspace.
#
# Prerequisites:
#   - Account & workspace on the PREMIUM plan.
#   - `databricks` provider configured at ACCOUNT scope (account_id + account host
#     https://accounts.azuredatabricks.net) with an account-admin principal.
#   - The NCC region MUST equal the workspace's Azure region (co-regional).
#   - You still APPROVE each private endpoint on the resource side (portal/az);
#     Terraform creates the rule (PENDING) but cannot approve it for you.
#
# Verified limits (Azure, mid-2026 docs — reconfirm before quoting):
#   - <= 10 NCCs per region per account
#   - <= 100 private endpoints per region (across 1-10 NCCs)
#   - <= 50 workspaces per NCC
#
# Cost caveat: EACH private endpoint rule is billed by Azure per HOUR regardless of
#   connection state. The free alternative is the AzureDatabricksServerless service
#   tag via an Azure Network Security Perimeter (NSP) — see the lesson, configured
#   on the storage side, not here.
# =============================================================================

terraform {
  required_providers {
    databricks = { source = "databricks/databricks", version = "~> 1.0" }
    azurerm    = { source = "hashicorp/azurerm", version = "~> 3.0" }
  }
}

# Account-scoped provider (NCC objects are account-level, not workspace-level).
provider "databricks" {
  alias      = "account"
  host       = "https://accounts.azuredatabricks.net"
  account_id = var.databricks_account_id
}

variable "databricks_account_id" { type = string }
variable "region" {
  type    = string
  default = "eastus" # MUST match the workspace + target resources' Azure region
}
variable "workspace_id" {
  type        = number
  description = "Numeric Databricks workspace ID to bind the NCC to."
}
variable "adls_resource_id" {
  type        = string
  description = "Azure resource ID of the ADLS Gen2 storage account."
}
variable "sql_server_resource_id" {
  type        = string
  default     = "" # leave empty to skip the Azure SQL private endpoint
  description = "Azure resource ID of the Azure SQL logical server (optional)."
}

# ---------------------------------------------------------------------------
# 1) The NCC — one per business unit + region is the recommended pattern.
# ---------------------------------------------------------------------------
resource "databricks_mws_network_connectivity_config" "this" {
  provider = databricks.account
  name     = "ncc-${var.region}-bu1" # 3-30 chars: alphanumerics, hyphens, underscores
  region   = var.region              # regional object; workspaces must share this region
}

# ---------------------------------------------------------------------------
# 2) Private endpoint rules into ADLS Gen2.
#    A rule is PER-SUBRESOURCE (group_id). ADLS commonly needs BOTH:
#      - "dfs"  : the hierarchical-namespace data path (and UC model logging
#                 from serverless notebooks)
#      - "blob" : blob-path access (e.g. model serving downloading artifacts)
# ---------------------------------------------------------------------------
resource "databricks_mws_ncc_private_endpoint_rule" "adls_dfs" {
  provider                       = databricks.account
  network_connectivity_config_id = databricks_mws_network_connectivity_config.this.id
  resource_id                    = var.adls_resource_id
  group_id                       = "dfs"
}

resource "databricks_mws_ncc_private_endpoint_rule" "adls_blob" {
  provider                       = databricks.account
  network_connectivity_config_id = databricks_mws_network_connectivity_config.this.id
  resource_id                    = var.adls_resource_id
  group_id                       = "blob"
}

# ---------------------------------------------------------------------------
# 3) (Optional) Azure SQL Database private endpoint (group_id = sqlServer).
#    Useful for Lakehouse Federation / external queries from serverless.
# ---------------------------------------------------------------------------
resource "databricks_mws_ncc_private_endpoint_rule" "sql" {
  count                          = var.sql_server_resource_id == "" ? 0 : 1
  provider                       = databricks.account
  network_connectivity_config_id = databricks_mws_network_connectivity_config.this.id
  resource_id                    = var.sql_server_resource_id
  group_id                       = "sqlServer"
}

# ---------------------------------------------------------------------------
# 4) Bind the NCC to a workspace (<= 50 workspaces per NCC; co-regional).
#    After apply: wait ~10 min and restart running serverless services.
# ---------------------------------------------------------------------------
resource "databricks_mws_ncc_binding" "bind" {
  provider                       = databricks.account
  network_connectivity_config_id = databricks_mws_network_connectivity_config.this.id
  workspace_id                   = var.workspace_id
}

output "ncc_id" {
  value       = databricks_mws_network_connectivity_config.this.id
  description = "NCC ID — use it to approve the PENDING private endpoints on each resource."
}

# NEXT (manual, cannot be done in Terraform):
#   Azure portal -> each resource -> Networking -> Private endpoint connections
#   -> Approve. Rules then move PENDING -> ESTABLISHED. Optionally set the
#   resource's "Public network access" = Disabled to enforce private-only access.
