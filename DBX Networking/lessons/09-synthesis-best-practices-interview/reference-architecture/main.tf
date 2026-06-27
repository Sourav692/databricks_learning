# =============================================================================
# Topic 9 — Reference Architecture, end-to-end (hands-on companion to 9.1)
#
# Goal: the "Standard" secure Azure Databricks deployment an architect defends in
#       a customer review — VNet injection + Secure Cluster Connectivity (No Public
#       IP) + back-end Private Link + a storage Private Endpoint + a NAT Gateway for
#       stable egress. This is the end-to-end picture the whole track builds toward.
#
# What it provisions:
#   - A /16 VNet with delegated host + container subnets and a /24 Private Endpoint
#     subnet.
#   - NSGs on both workspace subnets (Databricks injects its required rules).
#   - A NAT Gateway on the workspace subnets (stable egress; required since NPIP
#     VMs have no public IP — and since 2026-03-31 new VNets have no default egress).
#   - A PREMIUM workspace with no_public_ip = true and public_network_access = false.
#   - Back-end Private Link: a databricks_ui_api Private Endpoint + the
#     privatelink.azuredatabricks.net Private DNS zone.
#   - An ADLS Gen2 account reached over a Private Endpoint (dfs subresource).
#
# Prerequisites:
#   - Azure subscription; "Contributor" + "Network Contributor" on the RG.
#   - `databricks` provider not required here (pure azurerm); workspace is created
#     by azurerm. Account-level objects (NCC, metastore) are out of scope for this file.
#   - All resources co-regional.
#
# Cost caveats: NAT Gateway (hourly + per-GB), each Private Endpoint (hourly +
#   per-GB), Premium DBUs. This is the "regulated customer" baseline, not the
#   cheapest — see checklist.tf for the free/backbone alternatives.
# =============================================================================

terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 3.0" }
  }
}

provider "azurerm" {
  features {}
}

variable "region" {
  type    = string
  default = "eastus"
}
variable "resource_group_name" {
  type    = string
  default = "adb-refarch-rg"
}
variable "workspace_name" {
  type    = string
  default = "adb-refarch"
}

# Address plan (1.1 sizing): /16 VNet; /18 host + /18 container (room to scale);
# a dedicated /24 for Private Endpoints (PEs must NOT sit on the delegated subnets).
variable "vnet_cidr" {
  type    = string
  default = "10.179.0.0/16"
}
variable "host_subnet_cidr" {
  type    = string
  default = "10.179.0.0/18"
}
variable "container_subnet_cidr" {
  type    = string
  default = "10.179.64.0/18"
}
variable "pe_subnet_cidr" {
  type    = string
  default = "10.179.128.0/24"
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.region
}

resource "azurerm_virtual_network" "this" {
  name                = "vnet-${var.workspace_name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = [var.vnet_cidr]
}

# ---- The two Databricks workspace subnets: delegated + service endpoint to storage.
locals {
  databricks_delegation_actions = [
    "Microsoft.Network/virtualNetworks/subnets/join/action",
    "Microsoft.Network/virtualNetworks/subnets/prepareNetworkPolicies/action",
    "Microsoft.Network/virtualNetworks/subnets/unprepareNetworkPolicies/action",
  ]
}

resource "azurerm_subnet" "host" { # "public" subnet in the Portal label
  name                 = "snet-host"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.host_subnet_cidr]
  service_endpoints    = ["Microsoft.Storage"] # free backbone route to ADLS (Path 3)
  delegation {
    name = "databricks-del"
    service_delegation {
      name    = "Microsoft.Databricks/workspaces" # immutable network intent policy
      actions = local.databricks_delegation_actions
    }
  }
}

resource "azurerm_subnet" "container" { # "private" subnet in the Portal label
  name                 = "snet-container"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.container_subnet_cidr]
  service_endpoints    = ["Microsoft.Storage"]
  delegation {
    name = "databricks-del"
    service_delegation {
      name    = "Microsoft.Databricks/workspaces"
      actions = local.databricks_delegation_actions
    }
  }
}

resource "azurerm_subnet" "pe" {
  name                 = "snet-private-endpoints"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.pe_subnet_cidr]
  # PEs require subnet network policies disabled so the PE NIC can be placed.
  private_endpoint_network_policies = "Disabled"
}

# ---- NSGs on the two workspace subnets (Databricks injects its required rules).
resource "azurerm_network_security_group" "host" {
  name                = "nsg-host"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
}
resource "azurerm_network_security_group" "container" {
  name                = "nsg-container"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
}
resource "azurerm_subnet_network_security_group_association" "host" {
  subnet_id                 = azurerm_subnet.host.id
  network_security_group_id = azurerm_network_security_group.host.id
}
resource "azurerm_subnet_network_security_group_association" "container" {
  subnet_id                 = azurerm_subnet.container.id
  network_security_group_id = azurerm_network_security_group.container.id
}

# ---- NAT Gateway: NPIP clusters have no public IP, so egress needs a path out;
#      this also gives a STABLE outbound IP for partner allow-lists.
resource "azurerm_public_ip" "nat" {
  name                = "pip-nat-${var.workspace_name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  allocation_method   = "Static"
  sku                 = "Standard"
}
resource "azurerm_nat_gateway" "this" {
  name                = "natgw-${var.workspace_name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku_name            = "Standard"
}
resource "azurerm_nat_gateway_public_ip_association" "this" {
  nat_gateway_id       = azurerm_nat_gateway.this.id
  public_ip_address_id = azurerm_public_ip.nat.id
}
resource "azurerm_subnet_nat_gateway_association" "host" {
  subnet_id      = azurerm_subnet.host.id
  nat_gateway_id = azurerm_nat_gateway.this.id
}
resource "azurerm_subnet_nat_gateway_association" "container" {
  subnet_id      = azurerm_subnet.container.id
  nat_gateway_id = azurerm_nat_gateway.this.id
}

# ---- The workspace: VNet-injected, NPIP, public access OFF (back-end PL only).
resource "azurerm_databricks_workspace" "this" {
  name                = var.workspace_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "premium" # Private Link / CMK / IP ACLs all need Premium

  public_network_access_enabled         = false                    # front door closed; reach via Private Link
  network_security_group_rules_required = "NoAzureDatabricksRules" # pairs with back-end Private Link

  custom_parameters {
    no_public_ip                                         = true # Secure Cluster Connectivity (NPIP)
    virtual_network_id                                   = azurerm_virtual_network.this.id
    public_subnet_name                                   = azurerm_subnet.host.name
    private_subnet_name                                  = azurerm_subnet.container.name
    public_subnet_network_security_group_association_id  = azurerm_subnet_network_security_group_association.host.id
    private_subnet_network_security_group_association_id = azurerm_subnet_network_security_group_association.container.id
  }
}

# ---- Back-end Private Link: databricks_ui_api PE + the workspace Private DNS zone.
resource "azurerm_private_dns_zone" "adb" {
  name                = "privatelink.azuredatabricks.net"
  resource_group_name = azurerm_resource_group.this.name
}
resource "azurerm_private_dns_zone_virtual_network_link" "adb" {
  name                  = "link-adb"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.adb.name
  virtual_network_id    = azurerm_virtual_network.this.id
}
resource "azurerm_private_endpoint" "backend" {
  name                = "pe-${var.workspace_name}-uiapi"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.pe.id

  private_service_connection {
    name                           = "psc-uiapi"
    private_connection_resource_id = azurerm_databricks_workspace.this.id
    subresource_names              = ["databricks_ui_api"] # the back-end (SCC relay + REST) subresource
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "adb-dns"
    private_dns_zone_ids = [azurerm_private_dns_zone.adb.id]
  }
}

# ---- ADLS Gen2 reached over a Private Endpoint (dfs), default-deny on the account.
resource "azurerm_storage_account" "data" {
  name                          = "adbrefarchdata01" # globally unique, 3-24 lowercase alnum
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  account_tier                  = "Standard"
  account_replication_type      = "ZRS"
  is_hns_enabled                = true  # ADLS Gen2 (hierarchical namespace)
  public_network_access_enabled = false # private-only
}
resource "azurerm_private_dns_zone" "dfs" {
  name                = "privatelink.dfs.core.windows.net"
  resource_group_name = azurerm_resource_group.this.name
}
resource "azurerm_private_dns_zone_virtual_network_link" "dfs" {
  name                  = "link-dfs"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.dfs.name
  virtual_network_id    = azurerm_virtual_network.this.id
}
resource "azurerm_private_endpoint" "adls" {
  name                = "pe-adls-dfs"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.pe.id

  private_service_connection {
    name                           = "psc-adls-dfs"
    private_connection_resource_id = azurerm_storage_account.data.id
    subresource_names              = ["dfs"]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "dfs-dns"
    private_dns_zone_ids = [azurerm_private_dns_zone.dfs.id]
  }
}

output "workspace_url" { value = azurerm_databricks_workspace.this.workspace_url }
output "nat_egress_ip" {
  value       = azurerm_public_ip.nat.ip_address
  description = "Stable egress IP for partner/storage allow-lists."
}

# NEXT (out of scope for this file):
#   - Front-end Private Link (databricks_ui_api + browser_authentication PEs in a
#     transit VNet) for private USER access — see Stage 4.
#   - Unity Catalog metastore + Access Connector (account-level) — see Stage 7.
#   - CMK, audit log delivery, Compliance Security Profile — see Stage 8 / checklist.tf.
