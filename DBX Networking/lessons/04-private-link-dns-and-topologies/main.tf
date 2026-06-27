# =============================================================================
# Goal: Data Exfiltration Protection (DEP) for an Azure Databricks (classic) spoke.
#       Deploy an Azure Firewall in a hub VNet, force all internet-bound egress
#       (0.0.0.0/0) from the Databricks subnets through it via a UDR, and allow
#       ONLY the FQDNs/ports Databricks needs. Control-plane + SCC stay on
#       back-end Private Link and are deliberately NOT routed through the firewall.
#
# Prerequisites:
#   - Azure subscription + resource groups; "Network Contributor" on them.
#   - An existing Databricks SPOKE VNet (VNet injection, host + container subnets
#     delegated to Microsoft.Databricks/workspaces) — see lesson 1.1 / Stage 2.
#   - Back-end Private Link enabled on the workspace (lesson 4.1) so the
#     AzureDatabricks service tag is NOT required in the route table.
#   - All resources co-regional with the workspace. Region values below = East US;
#     LOOK UP your region's exact FQDNs in the IP/domain doc before applying:
#     https://learn.microsoft.com/azure/databricks/resources/ip-domain-region
#
# Provisions: hub VNet + AzureFirewallSubnet + Azure Firewall (Standard) + policy,
#   application + network rule collections (the allowlist), a route table with the
#   0.0.0.0/0 -> firewall UDR (+ backbone carve-outs), and subnet associations.
#
# Cost caveats: Azure Firewall = hourly + per-GB processed. Keep the allowlist
#   tight; NEVER route bulk ADLS data through the firewall (use Private/Service
#   Endpoints). Size/HA (zonal) the firewall so it is not a bottleneck or SPOF.
# =============================================================================

terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 3.0" }
  }
}
provider "azurerm" { features {} }

# ----------------------------------------------------------------------------
# Variables — wire these to your existing spoke / region.
# ----------------------------------------------------------------------------
variable "region"             { default = "eastus" }
variable "hub_rg"             { default = "adb-hub-rg" }
variable "spoke_rg"           { default = "adb-rg" }
variable "spoke_cidr"         { default = "10.179.0.0/16" } # source range allowed to egress
variable "host_subnet_id"     { description = "Resource ID of the Databricks HOST (public) subnet" }
variable "container_subnet_id"{ description = "Resource ID of the Databricks CONTAINER (private) subnet" }

# Region-specific Databricks endpoints (East US example — VERIFY for your region).
variable "metastore_fqdn"  { default = "consolidated-eastus-prod-metastore.mysql.database.azure.com" }
variable "artifact_fqdn"   { default = "dbartifactsprodeastus.blob.core.windows.net" }
variable "systables_fqdn"  { default = "ucstprdeastus.dfs.core.windows.net" }
variable "logblob_fqdn"    { default = "dblogprodeastus.blob.core.windows.net" }
variable "eventhub_fqdn"   { default = "prod-eastus1-observabilityeventhubs.servicebus.windows.net" }

# ----------------------------------------------------------------------------
# Hub VNet + the (required, fixed-name) AzureFirewallSubnet + firewall public IP.
# ----------------------------------------------------------------------------
resource "azurerm_virtual_network" "hub" {
  name                = "adb-hub-vnet"
  location            = var.region
  resource_group_name = var.hub_rg
  address_space       = ["10.180.0.0/24"]
}

resource "azurerm_subnet" "fw" {
  name                 = "AzureFirewallSubnet" # MUST be this exact name; min /26
  resource_group_name  = var.hub_rg
  virtual_network_name  = azurerm_virtual_network.hub.name
  address_prefixes     = ["10.180.0.0/26"]
}

resource "azurerm_public_ip" "fw" {
  name                = "adb-hub-fw-pip"
  location            = var.region
  resource_group_name = var.hub_rg
  allocation_method   = "Static"   # stable egress IP downstream services can allowlist
  sku                 = "Standard"
}

# ----------------------------------------------------------------------------
# Azure Firewall + policy. Standard tier supports FQDN application rules.
# ----------------------------------------------------------------------------
resource "azurerm_firewall_policy" "dep" {
  name                = "adb-dep-policy"
  location            = var.region
  resource_group_name = var.hub_rg
}

resource "azurerm_firewall" "hub" {
  name                = "adb-hub-fw"
  location            = var.region
  resource_group_name = var.hub_rg
  sku_name            = "AZFW_VNet"
  sku_tier            = "Standard"
  firewall_policy_id  = azurerm_firewall_policy.dep.id

  ip_configuration {
    name                 = "fw-ipconfig"
    subnet_id            = azurerm_subnet.fw.id
    public_ip_address_id = azurerm_public_ip.fw.id
  }
}

# ----------------------------------------------------------------------------
# The ALLOWLIST.
#  - Application rules: FQDN-based (HTTPS) — artifacts, logs, system tables,
#    library repos, Entra ID. Drop pypi/cran/ubuntu if you pre-load libraries.
#  - Network rules: non-HTTP ports — metastore 3306/TCP, Event Hubs 9093/TCP.
#  NOTE: SCC relay + control plane are NOT here — they go via back-end Private
#  Link (private), deliberately bypassing the firewall (no extra hop / cost).
# ----------------------------------------------------------------------------
resource "azurerm_firewall_policy_rule_collection_group" "rules" {
  name               = "adb-dep-rules"
  firewall_policy_id = azurerm_firewall_policy.dep.id
  priority           = 200

  application_rule_collection {
    name     = "adb-egress-allow"
    priority = 200
    action   = "Allow"

    rule {
      name              = "databricks-artifacts-logs-systables"
      source_addresses  = [var.spoke_cidr]
      destination_fqdns = [
        var.artifact_fqdn,   # DBR images / jars (artifact Blob storage)
        var.logblob_fqdn,    # log / telemetry Blob storage
        var.systables_fqdn,  # system tables storage (dfs)
      ]
      protocols { type = "Https" port = 443 }
    }

    rule {
      name              = "library-repos-and-os-updates" # remove if libs are pre-loaded
      source_addresses  = [var.spoke_cidr]
      destination_fqdns = [
        "*.pypi.org", "*.pythonhosted.org",
        "*.cran.r-project.org",
        "repo1.maven.org", "*.maven.org",
        "*.ubuntu.com",
      ]
      protocols { type = "Https" port = 443 }
    }

    rule {
      name              = "entra-id-auth"
      source_addresses  = [var.spoke_cidr]
      destination_fqdns = ["login.microsoftonline.com", "*.microsoftonline.com"]
      protocols { type = "Https" port = 443 }
    }
  }

  network_rule_collection {
    name     = "adb-net-allow"
    priority = 300
    action   = "Allow"

    rule {
      name              = "metastore-3306" # legacy Hive metastore; omit if Unity-Catalog-only
      source_addresses  = [var.spoke_cidr]
      destination_fqdns = [var.metastore_fqdn]
      destination_ports = ["3306"]
      protocols         = ["TCP"]
    }

    rule {
      name              = "eventhub-9093" # observability Event Hubs
      source_addresses  = [var.spoke_cidr]
      destination_fqdns = [var.eventhub_fqdn]
      destination_ports = ["9093"]
      protocols         = ["TCP"]
    }
  }
}

# ----------------------------------------------------------------------------
# Hub <-> spoke peering. (Spoke side peering + the spoke VNet are assumed to
# exist from the VNet-injection deployment; add the reverse peering there.)
# ----------------------------------------------------------------------------
# resource "azurerm_virtual_network_peering" "hub_to_spoke" { ... allow_forwarded_traffic = true ... }

# ----------------------------------------------------------------------------
# The UDR — the forcing function. 0.0.0.0/0 -> firewall; storage stays on the
# Azure backbone. Associate to BOTH Databricks subnets (host + container).
# ----------------------------------------------------------------------------
resource "azurerm_route_table" "spoke" {
  name                = "adb-spoke-rt"
  location            = var.region
  resource_group_name = var.spoke_rg
}

resource "azurerm_route" "to_firewall" {
  name                   = "to-firewall"
  resource_group_name    = var.spoke_rg
  route_table_name       = azurerm_route_table.spoke.name
  address_prefix         = "0.0.0.0/0"
  next_hop_type          = "VirtualAppliance"
  next_hop_in_ip_address = azurerm_firewall.hub.ip_configuration[0].private_ip_address
}

# Artifact / log Blob storage on the Azure backbone (bypass the firewall, save per-GB).
resource "azurerm_route" "storage_backbone" {
  name                = "storage-backbone"
  resource_group_name = var.spoke_rg
  route_table_name    = azurerm_route_table.spoke.name
  address_prefix      = "Storage.${title(var.region)}" # service tag, e.g. Storage.EastUS
  next_hop_type       = "Internet"
}

# IMPORTANT: with back-end Private Link enabled there is intentionally NO
# "AzureDatabricks -> Internet" route here — SCC + control plane are private.
# WITHOUT Private Link you MUST add it, or the firewall black-holes the relay:
# resource "azurerm_route" "adb_backbone" {
#   address_prefix = "AzureDatabricks"  next_hop_type = "Internet"  ...
# }

resource "azurerm_subnet_route_table_association" "host" {
  subnet_id      = var.host_subnet_id
  route_table_id = azurerm_route_table.spoke.id
}
resource "azurerm_subnet_route_table_association" "container" {
  subnet_id      = var.container_subnet_id
  route_table_id = azurerm_route_table.spoke.id
}

# Apply ORDER matters: ensure the firewall allowlist + Private Link exist BEFORE
# the 0.0.0.0/0 route is associated, or running clusters lose egress / new
# clusters fail to launch. (Terraform's implicit dependency on the firewall's
# private IP gives the right ordering for a fresh apply.)

output "firewall_private_ip" { value = azurerm_firewall.hub.ip_configuration[0].private_ip_address }
output "firewall_public_ip"  { value = azurerm_public_ip.fw.ip_address } # allowlist downstream
