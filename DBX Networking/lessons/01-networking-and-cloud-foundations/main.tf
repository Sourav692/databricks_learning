# =============================================================================
# Topic 1 — Networking & Cloud Foundations (hands-on companion)
#
# Goal: stand up the core Azure networking primitives this topic teaches — a VNet
#       with sized subnets, an NSG with explicit allow/deny rules, a route table
#       with a UDR forcing egress, a NAT Gateway for a stable outbound IP, and a
#       Private DNS zone — so an architect can SEE how CIDR sizing (1.1), VNets &
#       subnets (1.2), NSGs & routing (1.3), DNS (1.4) and a hub-style topology
#       (1.5) fit together. This is generic Azure networking (no Databricks yet).
#
# What it provisions:
#   - A /16 VNet with a workload subnet and a hub/firewall-style subnet (1.1/1.2).
#   - An NSG (default-deny inbound + an explicit corp-CIDR allow) on the workload
#     subnet (1.3).
#   - A route table + UDR sending 0.0.0.0/0 to a next hop (1.3 routing).
#   - A NAT Gateway giving the workload subnet a STABLE egress public IP (1.3).
#   - A Private DNS zone + VNet link (1.4 name resolution).
#
# Prerequisites:
#   - Azure subscription + the "Network Contributor" role on the target RG.
#   - Region chosen up front; all resources are co-regional.
#
# Cost caveat: the NAT Gateway and its Public IP bill hourly + per-GB egress.
#   The Private DNS zone is near-free. No compute is created here.
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
  default = "netfoundations-rg"
}

# 1.1 — CIDR sizing. A /16 VNet (65 536 addresses) carved into /18 subnets
#       (~16 379 usable each after Azure's 5 reserved IPs per subnet).
variable "vnet_cidr" {
  type    = string
  default = "10.10.0.0/16"
}
variable "workload_subnet_cidr" {
  type    = string
  default = "10.10.0.0/18"
}
variable "hub_subnet_cidr" {
  type    = string
  default = "10.10.64.0/24"
}

# 1.3 — the only public CIDR allowed to reach the workload on 443 (e.g. corp VPN egress).
variable "corp_allowed_cidr" {
  type    = string
  default = "203.0.113.0/24"
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.region
}

# ---------------------------------------------------------------------------
# 1.2 — VNet + subnets. The VNet is your private address space; subnets slice it.
# ---------------------------------------------------------------------------
resource "azurerm_virtual_network" "this" {
  name                = "vnet-foundations"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = [var.vnet_cidr]
}

resource "azurerm_subnet" "workload" {
  name                 = "snet-workload"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.workload_subnet_cidr]
}

# A small hub/"firewall" subnet — illustrates the 1.5 hub-and-spoke pattern where
# egress is funnelled through a central appliance.
resource "azurerm_subnet" "hub" {
  name                 = "snet-hub"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.hub_subnet_cidr]
}

# ---------------------------------------------------------------------------
# 1.3 — NSG: a stateful allow/deny firewall on the subnet. Azure has implicit
#        default rules; these OVERRIDE them. Lower priority number = evaluated first.
# ---------------------------------------------------------------------------
resource "azurerm_network_security_group" "workload" {
  name                = "nsg-workload"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  # Allow inbound HTTPS ONLY from the corporate CIDR (everything else falls through
  # to the deny rule below). Stateful: the return traffic is allowed automatically.
  security_rule {
    name                       = "allow-corp-https-in"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = var.corp_allowed_cidr
    destination_address_prefix = "*"
  }

  # Explicit default-deny inbound (priority just below Azure's 65000 defaults) so
  # the intent is visible in code, not implicit.
  security_rule {
    name                       = "deny-all-inbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "workload" {
  subnet_id                 = azurerm_subnet.workload.id
  network_security_group_id = azurerm_network_security_group.workload.id
}

# ---------------------------------------------------------------------------
# 1.3 — Routing: a route table with a UDR. Here it sends all egress (0.0.0.0/0)
#        to a virtual appliance (e.g. Azure Firewall) in the hub — the pattern
#        used later for data-exfiltration protection (Stage 4). Swap next_hop to
#        "Internet" to route straight out instead.
# ---------------------------------------------------------------------------
resource "azurerm_route_table" "workload" {
  name                = "rt-workload"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  route {
    name                   = "default-via-hub-firewall"
    address_prefix         = "0.0.0.0/0"
    next_hop_type          = "VirtualAppliance"               # forces egress through the hub appliance
    next_hop_in_ip_address = cidrhost(var.hub_subnet_cidr, 4) # the appliance's private IP
  }
}

resource "azurerm_subnet_route_table_association" "workload" {
  subnet_id      = azurerm_subnet.workload.id
  route_table_id = azurerm_route_table.workload.id
}

# ---------------------------------------------------------------------------
# 1.3 — NAT Gateway: gives no-public-IP VMs a STABLE outbound public IP that
#        partners can allow-list, while permitting NO inbound. (Same primitive
#        Databricks SCC egress relies on later.)
# ---------------------------------------------------------------------------
resource "azurerm_public_ip" "nat" {
  name                = "pip-nat"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_nat_gateway" "this" {
  name                = "natgw-foundations"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku_name            = "Standard"
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  nat_gateway_id       = azurerm_nat_gateway.this.id
  public_ip_address_id = azurerm_public_ip.nat.id
}

resource "azurerm_subnet_nat_gateway_association" "workload" {
  subnet_id      = azurerm_subnet.workload.id
  nat_gateway_id = azurerm_nat_gateway.this.id
}

# ---------------------------------------------------------------------------
# 1.4 — DNS: a Private DNS zone linked to the VNet so private names resolve
#        inside it (the foundation for resolving Private Endpoints in Stage 4,
#        e.g. privatelink.azuredatabricks.net).
# ---------------------------------------------------------------------------
resource "azurerm_private_dns_zone" "internal" {
  name                = "internal.example.com"
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "internal" {
  name                  = "link-foundations"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.internal.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
}

output "vnet_id" { value = azurerm_virtual_network.this.id }
output "nat_egress_ip" {
  value       = azurerm_public_ip.nat.ip_address
  description = "Stable outbound IP for partner/storage allow-lists."
}
