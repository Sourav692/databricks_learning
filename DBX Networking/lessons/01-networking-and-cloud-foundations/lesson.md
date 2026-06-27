# Topic 1 — Networking & Cloud Foundations (Azure-first)

> **Stage 1 · Azure Databricks Networking & Security** — for the **FDE / RSA /
> Solutions Architect** who has to *explain* this to a customer's network/security
> team, not hand-configure it. This is the **address book and the toolbox** the
> whole track is written in: before you can secure a single Databricks traffic
> path (Stage 2's three doors), you need the five primitives below — *how addresses
> are written, where they live, what allows/steers them, how a name becomes an IP,
> and the shapes real enterprises build.*
>
> **This one page covers all five subtopics:**
> - **1.1 — IP addressing & CIDR** (the address vocabulary; sizing that can't be undone)
> - **1.2 — VNets, subnets & the cloud network model** (the container addresses live in)
> - **1.3 — Firewalls, NSGs & routing** (what's *allowed*, *where* it goes, how it gets *out*)
> - **1.4 — DNS, endpoints & name resolution** (how a name becomes a private IP)
> - **1.5 — Common network topologies** (hub-and-spoke — the shape every secure deployment lands in)
>
> Companion interactive page: `index.html` (tabbed, one interactive architecture
> diagram per subtopic). Static topology: `architecture.svg`.

---

## 🧠 Topic mental model (hold this in your head)

> **Stage 1 is building the city before you install the locks.**
>
> - **CIDR (1.1)** is the **address book** — how every street and house number is
>   written. You allocate it *once, on paper*, before any building exists, because
>   you cannot renumber the streets after move-in.
> - **The VNet (1.2)** is the **gated estate you own**, and **subnets** are the
>   **streets** inside it. Databricks rents **two same-sized streets** — a *host*
>   street and a *container* street — and every cluster node needs **one house on
>   each** (1 host IP + 1 container IP).
> - **NSG / UDR / NAT (1.3)** are the **bouncer, the GPS, and the loading dock**:
>   the NSG decides *whether* a packet may travel, the UDR decides *which road* it
>   takes, the NAT gateway gives it *one stable way out*.
> - **DNS (1.4)** is the **phonebook** that turns a name into one of those
>   addresses — and a **Private DNS Zone** is the *internal company directory* that
>   makes a public name resolve to a *private* IP.
> - **Topology (1.5)** is the **city plan** — **hub-and-spoke**: shared services
>   (firewall, on-prem gateway, DNS) in one central hub, each workload in its own
>   spoke, all routed through the hub.
>
> **The one sentence:** *Stage 1 is the address book (CIDR), the streets (VNet/
> subnets), the bouncer-GPS-dock (NSG/UDR/NAT), the phonebook (DNS), and the city
> plan (hub-and-spoke) — get these right and every Databricks control in Stage 2+
> is just "harden one hop on a map you already own."*
>
> **Where it sits in the three-path scaffold (Stage 2.2):** Stage 1 is *pre-path*.
> It builds the **container and vocabulary** that the three Databricks connectivity
> paths — ① user→Databricks, ② compute↔control, ③ compute→storage — are later
> expressed and secured in. Path ② lives in the **subnets** you size in 1.1/1.2;
> the controls on ②/③ are **NSG rules + UDRs + NAT** (1.3); making any path
> *private* is a **DNS** job (1.4); and the whole thing lands in a **hub-and-spoke**
> (1.5).

---

## Why this topic matters to an architect

- **It's the vocabulary of every later control.** NSG rules, UDRs, Private
  Endpoints, IP access lists, and NCC allowlists are *all written in CIDR* and
  *all bind to a subnet*. You can't read a firewall rule, a route table, or a
  Private Link design without Stage 1.
- **It contains the one decision you can't undo cheaply.** Subnet CIDR (and
  delegation) is **immutable after the workspace deploys** — under-size it and the
  fix is a workspace rebuild. Getting the address plan right on day one is the
  cheapest insurance in the whole engagement.
- **It explains the failure modes before they happen.** "Clusters won't start"
  (subnet exhaustion or no egress), "workspace won't load over Private Link"
  (DNS), "the firewall broke everything" (a `0.0.0.0/0` UDR with no bypass),
  "spoke A can't reach spoke B" (non-transitive peering) — every one is a Stage 1
  cause-and-effect chain.
- **It's the opener in every security review.** Before "walk me through the
  Databricks architecture," the customer's network team wants to know you speak
  *their* language: CIDR, VNet/subnet, NSG/UDR/NAT, DNS, hub-and-spoke. Get the
  fundamentals fluent and you've earned the right to talk Databricks.

---

## Terms used here (define-before-use)

Stage 1 *owns* most of these terms, so the deep dive is in the section noted.
A few are borrowed from later modules — here's the 2–3 line gloss so the page
reads top-to-bottom, plus where the full treatment lives.

| Term | Plain-language gloss | Owning section / module |
| --- | --- | --- |
| **IP address** | The unique number a machine uses to be found on a network — IPv4 looks like `10.179.64.12`. | **1.1** |
| **CIDR** | Shorthand for a *range* of IPs, e.g. `10.179.0.0/16`; the `/n` says how many addresses. | **1.1** |
| **RFC 1918 / RFC 6598** | The private (`10/8`, `172.16/12`, `192.168/16`) and shared-CGNAT (`100.64/10`) address ranges safe to use inside a VNet. | **1.1** |
| **VNet** (virtual network) | Your private, isolated network in Azure — the address space your resources live in and the boundary you attach controls to. | **1.2** |
| **Subnet** | A CIDR slice of a VNet; resources attach here, and NSGs/UDRs bind *per subnet*. Databricks uses a **host** and a **container** subnet. | **1.2** |
| **Subnet delegation** | Handing a subnet to the `Microsoft.Databricks/workspaces` service so Databricks auto-manages its NSG rules + network intent. | **1.2** |
| **NIC** (network interface card) | The virtual adapter that gives a VM/endpoint an IP on a subnet; a Databricks node has a host NIC and a container NIC. | **1.2 / 1.3** |
| **NSG** (network security group) | A *stateful* allow/deny firewall on a subnet/NIC, filtering by 5-tuple + service tag. | **1.3** |
| **UDR** (user-defined route) | A custom route that overrides Azure's defaults — e.g. send `0.0.0.0/0` to a firewall. | **1.3** |
| **NAT Gateway** | An Azure egress device giving outbound traffic a **stable public source IP** (for allowlists), with no inbound. | **1.3** |
| **Service tag** | A Microsoft-maintained, auto-updated **named bucket of IP ranges** (e.g. `AzureDatabricks`, `Storage`) used in rules instead of raw IPs. | **1.3** |
| **SNAT** (source NAT) | Rewriting a packet's private source IP to a shared public IP on the way out, so replies find their way back. | **1.3** |
| **DNS / FQDN** | DNS turns a name (FQDN, e.g. `adb-….azuredatabricks.net`) into an IP; the phonebook of the network. | **1.4** |
| **Private DNS Zone** | A DNS phonebook only VNets you link can read — overrides a public name to a *private* IP. | **1.4** |
| **Private Endpoint / Service Endpoint** | A private IP in your VNet for an Azure service (per-GB) vs a free backbone route allowlisted by subnet. | **1.3/1.4**; deep dive **Stage 2.5/3** |
| **VNet peering** | The backbone wire joining two VNets so they route privately; non-transitive. | **1.5** |
| **ExpressRoute / VPN Gateway** | Private circuit / encrypted tunnel from on-prem into Azure; terminate in the hub's `GatewaySubnet`. | **1.5** |
| **SCC** (Secure Cluster Connectivity) / No Public IP | The default mode where cluster VMs have **no public IP** and dial *out* to the control plane. | Forward-ref **Stage 2.3** |
| **Private Link** | The Azure feature behind private endpoints; for Databricks it privatises the front-end and back-end paths. | Forward-ref **Stage 3/4** |
| **NCC** (Network Connectivity Configuration) | The account-level object giving *serverless* compute its egress/private-connectivity rules. | Forward-ref **Stage 4a/5** |
| **DEP** (Data Exfiltration Protection) | Force-tunnelling all egress through an Azure Firewall for inspection/allowlisting. | Forward-ref **Stage 4** |

---

# 1.1 — IP Addressing & CIDR

## What it is (plain language)

- An **IP address** is the unique number a machine uses to be found on a network —
  a postal address for a computer. IPv4 looks like `10.179.64.12`.
- **CIDR** (Classless Inter-Domain Routing, "cider") is shorthand for a *range* of
  addresses, e.g. `10.179.0.0/16`. The `/16` says how many addresses are in the
  block.
- A **subnet** is a smaller slice carved out of a larger range, used to group and
  isolate machines.

**Analogy:** a VNet's CIDR block is a *postcode area*; each subnet is a *street*;
each IP is a *house number*. CIDR (`/n`) is how you say *how many house numbers a
street has* — and the trap is **bigger `/n` = smaller street** (`/26` = 64,
`/16` = 65,536).

**Why an architect cares:** when Databricks deploys into the customer's network
(**VNet injection**, Stage 2/3), *they* choose the CIDR blocks. Pick them too
small and clusters fail to start when they run out of addresses ("IP exhaustion") —
and you **cannot resize a subnet after the workspace deploys**. CIDR is the single
most common, most expensive networking mistake this track exists to prevent.

## How it works — the math that matters

- IPv4 is a **32-bit** number in four octets (`0–255`). An address splits into a
  **network part** and a **host part**; CIDR's `/n` says where the split falls.
- **Total addresses in a block = 2^(32 − n).** The ones to memorise:

  | CIDR | Total | CIDR | Total |
  | --- | --- | --- | --- |
  | `/16` | 65,536 | `/24` | 256 |
  | `/20` | 4,096 | `/26` | 64 |
  | `/21` | 2,048 | `/28` | 16 |

- **Smaller `/n` = bigger block.** A `/16` contains 256 `/24`s; a `/24` contains
  4 `/26`s. Subnets must fit inside the VNet and must **not overlap**.
- **Public vs private:** public IPs are globally routable; **private** IPs
  (**RFC 1918**: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) are not
  internet-routable and reused inside private networks. **RFC 6598**
  (`100.64.0.0/10`, CGNAT space) is also non-routable and supported by Databricks
  VNet injection when RFC 1918 is exhausted.
- **Azure reserves 5 IPs in every subnet** — the first four (`.0` network, `.1`
  gateway, `.2`/`.3` DNS) and the last (`.broadcast`). So **usable = total − 5**:
  a `/26` gives **59**, not 64.

## Sizing an Azure Databricks VNet (the payoff) — verified limits

- **VNet CIDR: `/16` to `/24`.**
- **Two dedicated subnets** — a **host** subnet and a **container** subnet (older
  Portal labels: "public"/"private", misleading since under SCC *both* are
  private).
- **Each subnet at least `/26`** (technical min `/28`, but Databricks does not
  recommend smaller than `/26`); the two subnets should be the **same size**.
- **2 IPs per cluster node** — one host IP + one container IP. So **max cluster
  nodes ≈ usable IPs in one subnet = 2^(32−n) − 5**.

  | Subnet | Usable (−5) | ≈ Max nodes |
  | --- | --- | --- |
  | `/26` | 59 | ~59 |
  | `/24` | 251 | ~251 |
  | `/22` | 1,019 | ~1,019 |

**Worked example:** VNet `10.179.0.0/16`, host `10.179.0.0/18`, container
`10.179.64.0/18` (16,379 usable each) — far more than any one workspace needs, and
it **leaves headroom** in the `/16` for a small `/27`–`/28` Private Link subnet
later (Stage 4). That headroom is *why* you size the VNet a couple of bits larger
than the subnets.

## WHY IT BREAKS (cause → effect)

- **Subnet too small → "cluster failed to start, insufficient IP addresses."** A
  busy autoscaling workspace needs `2 × nodes` IPs against *one* subnet's
  `total − 5`. Under-size it and scale-out hits a wall. **Effect:** clusters won't
  launch, and the only fixes are the Public-Preview *Update network configuration*
  flow or an account-team CIDR increase — **neither instant**, because subnet CIDR
  is immutable.
- **Overlapping CIDR with on-prem/peered ranges → silent routing blackhole.**
  Routing becomes ambiguous and peering refuses to form (see 1.5). **Effect:**
  traffic to those ranges silently fails. Always reserve Databricks space from the
  customer's central IPAM plan.

## 1.1 illustrative config (sizing is the whole point)

```hcl
# Illustrative — VNet + two correctly-sized, delegated subnets. CIDR is PERMANENT
# once the workspace deploys; size for PEAK nodes + leave headroom for Private Link.
resource "azurerm_virtual_network" "adb" {
  name          = "adb-vnet"
  address_space = ["10.179.0.0/16"]   # /16 VNet: room for big subnets + future PE subnet
  # ... location / resource_group_name ...
}
resource "azurerm_subnet" "container" {
  name             = "adb-container"                  # runs the Databricks Runtime
  address_prefixes = ["10.179.64.0/18"]               # ~16k usable; MUST match host subnet size
  delegation { name = "adb"; service_delegation { name = "Microsoft.Databricks/workspaces" } }
}
# 10.179.128.0/17 left FREE in the /16 — headroom for a /27 Private Link subnet (Stage 4).
```

**Azure Portal:** Create a resource → Virtual network → **IP addresses** (set
`10.179.0.0/16`) → **+ Add subnet** twice (host + container, each ≥ `/26`,
non-overlapping). Delegation + NSG rules are auto-added at workspace creation.

> ⚠️ Subnet CIDR ranges **cannot be changed after the workspace deploys**. Size for
> peak node count up front.

## Comparison — picking a private range

| Range | Size | When to use | Watch out for |
| --- | --- | --- | --- |
| `10.0.0.0/8` | 16.7M | Default for enterprise ADB VNets | Coordinate with IPAM — overlap breaks routing |
| `172.16.0.0/12` | 1M | Mid-size, or when `10/8` is taken | Range is `172.16`–`172.31`, not all `172.x` |
| `192.168.0.0/16` | 65k | Test only | Too small; clashes with home/VPN ranges |
| `100.64.0.0/10` (RFC 6598) | 4.2M | RFC 1918 exhausted | Confirm peers/firewalls tolerate CGNAT space |

---

# 1.2 — VNets, Subnets & the Cloud Network Model

## What it is (plain language)

- A **Virtual Network (VNet)** is your own private, isolated slice of Azure — a
  software-defined network you own, with an address space *you* choose. Nothing
  outside it can initiate a connection inward unless you allow it.
- A **subnet** is a CIDR block *inside* a VNet; you place resources into subnets,
  and you attach **security and routing rules per subnet**.
- A VNet-injected **Azure Databricks workspace** lives in a VNet with **exactly
  two dedicated subnets** — a **host** subnet and a **container** subnet.

**Analogy:** a VNet is a *gated office building you own*; subnets are the *floors*.
A workspace rents **two adjacent same-sized floors** (host + container), and every
cluster node needs **one desk on each floor at once** (1 host IP + 1 container IP).
You can't re-floor after move-in (subnet CIDR + delegation are immutable), so you
measure for peak occupancy before you sign.

**Why an architect cares:** the VNet/subnet layout is the **first design decision**
of any secure classic deployment. Number of subnets, sizes, delegation, and which
NSG/route table attaches all flow from here — and several choices are **immutable**.
Get the model right and VNet injection, SCC, and Private Link slot in cleanly.

## How it works — deep dive

- **A VNet is isolated and regional.** Scoped to one region + one subscription
  (spans all availability zones). Default: nothing on the public internet can
  initiate inbound; resources *can* reach the internet outbound (changing — see the
  March-31-2026 gotcha). Classic Databricks compute gets **natural isolation
  because it runs in the customer's own subscription/VNet** — and internal policies
  even block cross-cluster/cross-workspace traffic in the same VNet.
- **Subnets are where controls attach.** NSGs and route tables (UDRs) bind **to a
  subnet**, not a VNet or VM. The subnet boundary is the unit of security/routing
  policy.
- **The Databricks two-subnet model (mandatory & shaped):**

  | Subnet | Older label | What runs there |
  | --- | --- | --- |
  | **Host** | "public subnet" | One **host** NIC per node — **no public IP under SCC** (label is historical) |
  | **Container** | "private subnet" | One **container** NIC per node — runs the Databricks Runtime (Spark driver/executors) |

  Both **delegated** to `Microsoft.Databricks/workspaces` (lets Databricks
  auto-provision NSG rules + network intent); **2 IPs/node**; subnets **can't be
  shared** and **no other resources** allowed in them; **CIDR + delegation
  immutable** after deploy.
- **The managed resource group.** Every workspace has a Databricks-owned, **locked**
  managed RG. At cluster start the **VMs, disks, NICs** are created there — but with
  VNet injection the **NICs land in *your* two subnets**. You don't create VMs in it.

## WHY IT BREAKS (cause → effect)

- **`/26` subnet + scale-out → IP exhaustion.** 2 IPs/node × 59 usable = ~59 nodes
  max. **Effect:** "cluster failed to start — insufficient IP addresses." Fix = CIDR
  increase / *Update network configuration* (Public Preview); not instant.
- **VNet in the wrong region/subscription → it won't appear in the deployment
  dropdown.** The workspace and VNet must be **co-regional and same-subscription**.
- **Service Endpoint *Policies* won't attach to the delegated subnets.** The
  immutable network intent policy blocks them. **Effect:** a storage-allowlisting
  approach that "won't stick" — pivot to Private Endpoint / NCC (Stage 2+).

## 1.2 illustrative config (the two delegated subnets)

```hcl
# Illustrative — the two delegated subnets a VNet-injection workspace requires.
resource "azurerm_subnet" "host" {
  name             = "adb-host"
  address_prefixes = ["10.179.0.0/18"]
  delegation {
    name = "adb"
    service_delegation {
      name    = "Microsoft.Databricks/workspaces"          # hands subnet mgmt to Databricks
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action",
                 "Microsoft.Network/virtualNetworks/subnets/prepareNetworkPolicies/action",
                 "Microsoft.Network/virtualNetworks/subnets/unprepareNetworkPolicies/action"]
    }
  }
}
# Container subnet identical shape, same size, no overlap — runs the Databricks Runtime.
```

**Azure Portal:** Create the VNet + two subnets (1.1), then Create a resource →
Azure Databricks → **Networking** tab → "Deploy in your own VNet" = **Yes** →
select VNet + the two subnets. **Verify:** VNet → Subnets → both show an **NSG
attached** and **Delegation = `Microsoft.Databricks/workspaces`**.

## Comparison — managed (default) VNet vs VNet injection

| | Default (managed) VNet | VNet injection (bring your own) |
| --- | --- | --- |
| Who owns the VNet | Databricks (managed RG) | **You** (your RG) |
| NSG / UDR control | None | **Full** |
| Private Link / peering / on-prem | Not supported | **Supported** (Stages 3/4) |
| CIDR control | Databricks chooses | **You choose** (`/16`–`/24`, subnets ≥ `/26`) |
| When to use | Quick dev/test, no net requirements | **Any production / regulated** — the baseline |

> **One-line cross-cloud map:** Azure VNet ≈ AWS VPC ≈ GCP VPC; Azure NSG ≈ AWS
> security group + NACL. One gotcha: an **Azure subnet spans all AZs** in a region;
> an **AWS subnet is pinned to one AZ** — so the "two subnets" here are host/container,
> *not* multi-AZ spread.

---

# 1.3 — Firewalls, NSGs & Routing

## What it is (plain language)

- A **Network Security Group (NSG)** is Azure's **stateful** packet filter — a
  distributed firewall on a subnet/NIC, holding prioritized allow/deny rules for
  inbound and outbound.
- A **route table / UDR** holds routes — "to reach X, send to next-hop Y." A
  **User-Defined Route** *overrides* Azure's invisible system routes.
- A **NAT gateway** gives a subnet a **stable public IP for outbound** — and
  **nothing inbound**.
- There is **no standalone "internet gateway" resource** on Azure — outbound is the
  implicit system route `0.0.0.0/0 → Internet`, made stable by a NAT gateway.

**Analogies:** NSG = the *bouncer with a guest list* (whether — first match wins,
stateful so replies are auto-allowed); UDR = the *GPS* (where — which road, not
if); NAT gateway = the *single shipping dock* (one return address out, nothing
unsolicited in).

**The one sentence:** *NSG decides **whether**, UDR decides **where**, NAT gives a
stable **way out** — and the #1 way to break a workspace is a `0.0.0.0/0` UDR that
reroutes control-plane traffic into a black hole.*

## How it works — deep dive

### NSG — stateful, prioritized, 5-tuple

- Rules match a **5-tuple** (source, src port, dest, dest port, protocol) +
  direction + action. **Priority 100–4096; lower = higher; first match wins.**
- **Stateful:** allow an outbound flow and the **return packets are auto-allowed** —
  *this is why SCC works with zero inbound rules*: clusters dial out, replies come
  back on the established flow.
- **Default rules** (65000+, undeletable): inbound is **deny-by-default**, outbound
  is **allow-to-Internet** by default. Hardening = adding higher-priority **deny**
  rules to clamp egress.
- **Service tags** (`AzureDatabricks`, `Storage`, `Sql`, `EventHub`,
  `VirtualNetwork`, `Internet`) = Microsoft-managed buckets of IP prefixes — write
  one rule against the name, Microsoft keeps the IPs current.

### The Databricks-managed NSG rules (VNet injection) — verified verbatim

Auto-provisioned via the subnet delegation; **do not edit/delete**:

| Direction | Protocol | Source | Dest | Dest Port | Purpose |
| --- | --- | --- | --- | --- | --- |
| Inbound | Any | `VirtualNetwork` | `VirtualNetwork` | Any | Intra-VNet (worker↔driver) |
| Inbound | TCP | `AzureDatabricks` | `VirtualNetwork` | **22** | SSH — **only if SCC disabled** |
| Inbound | TCP | `AzureDatabricks` | `VirtualNetwork` | **5557** | Internal proxy — **only if SCC disabled** |
| Outbound | TCP | `VirtualNetwork` | `AzureDatabricks` | **443, 3306, 8443-8451** | Control plane / SCC relay / webapp / metastore |
| Outbound | TCP | `VirtualNetwork` | `Storage` | **443** | Artifact + log blob storage |
| Outbound | Any | `VirtualNetwork` | `VirtualNetwork` | Any | Intra-VNet |
| Outbound | TCP | `VirtualNetwork` | `EventHub` | **9093** | Logging/telemetry to Event Hubs |
| Outbound | TCP | `VirtualNetwork` | `Sql` | **3306** | Legacy Hive metastore (being phased out) |

Port breakdown for the `AzureDatabricks` outbound rule: **443** infra/data/library,
**3306** metastore, **8443** compute→control API, **8444** Unity Catalog
logging/lineage, **8445–8451** reserved. (If you restrict outbound, also open
**111**/**2049** for some library installs.) With **SCC enabled** (the default)
there are **no inbound `AzureDatabricks` rules** — clusters have no public IP and
never accept inbound.

**Your job:** add higher-priority **deny** rules to clamp egress, and Deny rules on
*neighbouring* subnets so nothing else reaches the compute. Use a **unique NSG per
workspace**.

### Route tables, system routes & UDRs

- System routes: `VirtualNetwork → VirtualNetwork`, `0.0.0.0/0 → Internet`,
  service-specific → `VirtualNetwork` when a Service Endpoint is on.
- A **UDR** overrides for the same/more-specific prefix. **Longest-prefix-match
  wins**, then route-type precedence (UDR > BGP > system). Next-hop types:
  `VirtualAppliance` (firewall/NVA), `VirtualNetworkGateway` (VPN/ExpressRoute),
  `Internet`, `VirtualNetwork`, `None` (blackhole).
- **DEP pattern (Stage 4 preview):** `0.0.0.0/0 → VirtualAppliance (Azure Firewall
  private IP)` on both subnets forces all egress through the firewall.

### NAT gateway & egress

- Does **SNAT**: rewrites private source IP → stable public IP, lets replies back on
  the flow; **outbound-only**; auto-scales SNAT ports (up to 16 public IPs).
- **Precedence:** *UDR-to-NVA/gateway » NAT gateway » instance public IP » LB
  outbound » default system route.* So a DEP firewall UDR **overrides** the NAT
  gateway, by design.
- **Why ADB recommends it:** a **stable egress IP** for partner/IP-access-list
  allowlisting — and since **March 31, 2026**, new Azure VNets default to **no
  outbound internet**, so a NAT gateway (or other explicit egress) is now
  *required* for a new workspace.

## WHY IT BREAKS (cause → effect)

- **`0.0.0.0/0 → firewall` UDR with no bypass routes → blackholed control plane.**
  The catch-all swallows SCC-relay / metastore / artifact / log / Event Hubs
  traffic. **Effect:** clusters won't launch, no logs. *Fix:* add explicit bypass
  UDRs (next-hop `Internet`) for `AzureDatabricks` / `Storage` / `EventHub`
  (+ metastore/log per-region IPs); with Private Link, drop the `AzureDatabricks`
  route. **#1 DEP mistake.**
- **Hard-coded control-plane IPs in UDRs → outage when Databricks rotates them.**
  **Effect:** intermittent control-plane loss. *Fix:* use **service tags** so
  Microsoft keeps prefixes current.
- **Editing the Databricks-managed NSG rules → broken workspace.** They're required.
  Layer your own higher-priority rules instead.

## 1.3 illustrative config (the rules that teach the point)

```hcl
# Illustrative — harden egress + force-tunnel to a firewall WITHOUT blackholing the CP.
resource "azurerm_route" "dep_default" {                 # DEP catch-all → Azure Firewall
  address_prefix         = "0.0.0.0/0"
  next_hop_type          = "VirtualAppliance"
  next_hop_in_ip_address = "10.10.0.4"                    # firewall PRIVATE IP (hub)
  # ... route_table_name / resource_group_name ...
}
resource "azurerm_route" "bypass_adb" {                  # WITHOUT this, the CP is blackholed
  address_prefix = "AzureDatabricks"                      # service tag (OMIT if Private Link on)
  next_hop_type  = "Internet"
  # repeat for "Storage" and "EventHub"
}
```

**Azure Portal:** route table → Routes → add `0.0.0.0/0 → Virtual appliance`
(firewall private IP) + bypass routes per service tag (next hop **Internet**) →
associate to **both** subnets. NAT gateway → Outbound IP (a Standard public IP =
your stable egress IP) → Subnet (both ADB subnets).

> ⚠️ A `0.0.0.0/0 → Virtual appliance` UDR **overrides the NAT gateway** — keep the
> NAT for non-tunnelled egress, or place it on the firewall's hub subnet.

## Comparison — NSG vs UDR vs NAT vs Firewall

| | NSG | Route table / UDR | NAT gateway | Azure Firewall (Stage 4) |
| --- | --- | --- | --- | --- |
| **Decides** | *Whether* (allow/deny) | *Where* (next hop) | Outbound source IP | Allow/deny + inspect (L3–L7, FQDN) |
| **Stateful** | Yes | n/a | Yes | Yes |
| **Direction** | In + out | Egress routing | **Outbound only** | Both |
| **ADB role** | Managed rules + your deny | DEP force-tunnel + bypass | Stable / mandatory egress | DEP inspection point |
| **Cost** | Free | Free | Hourly + per-GB | Highest |

---

# 1.4 — DNS, Endpoints & Name Resolution

## What it is (plain language)

- **DNS** is the **phonebook of the network**: it turns a name (`adb-….azuredatabricks.net`)
  into the IP a machine connects to. People remember names; machines route on
  numbers; DNS is the lookup in between.
- An **endpoint** is a named destination that resolves to an IP and accepts
  connections (FQDN + port + protocol).
- A **Private DNS Zone** is a phonebook **only machines in linked VNets can read** —
  letting you override a public name so it points at a *private* IP.

**Analogy:** DNS is a phonebook. Public DNS is the *published* book everyone reads;
an Azure **Private DNS Zone** is the *internal company directory* — same name, but
callers inside the building get a direct internal extension (private IP) instead of
the public switchboard.

**Why an architect cares:** when you lock a workspace down with **Private Link**
(Stage 3), the workspace URL doesn't change — but it must now resolve to a **private
IP** inside the VNet. That swap is done *entirely in DNS*, via the Private DNS Zone
`privatelink.azuredatabricks.net`. **In a private Databricks deployment, "it's
broken" usually means "it's DNS."**

## How it works — deep dive

- **The three endpoint shapes resolve differently:**

  | Shape | Name resolves to | Path | DNS change? |
  | --- | --- | --- | --- |
  | **Public endpoint** | Public IP | Out to internet/public front door | No |
  | **Service Endpoint** | **Public IP (unchanged)** | Azure backbone, trusted subnet source | **No** |
  | **Private Endpoint** | **Private IP** in your VNet | Fully private / backbone | **Yes — Private DNS Zone** |

  The crucial insight: a **Service Endpoint does NOT change DNS** (only route +
  source identity); a **Private Endpoint requires** a DNS change — that's the whole
  point of the Private DNS Zone. (Classic interview probe.)
- **Auto-registration:** create a Private Endpoint with a **Private DNS Zone
  Group** and Azure **auto-writes the A record** (name → endpoint's private IP). The
  zone name must be the **exact prescribed `privatelink.*` value** or it silently
  doesn't fire.
- **The Databricks payoff — `privatelink.azuredatabricks.net`** (verified Commercial
  zone; Gov = `privatelink.databricks.azure.us`). Sub-resources:
  **`databricks_ui_api`** (UI, REST, back-end SCC relay) and
  **`browser_authentication`** (SSO/OAuth callback — one per region). The resolution
  chain for a private deployment:

  ```
  adb-….azuredatabricks.net                           (URL — UNCHANGED)
      │  public DNS returns a CNAME →
      ▼
  adb-….privatelink.azuredatabricks.net               (CNAME into the privatelink zone)
      │  your Private DNS Zone holds the A record →
      ▼
  10.10.4.5                                            (PRIVATE IP of the databricks_ui_api endpoint)
  ```

  The public CNAME resolves anywhere and leaks nothing; the **private A record**
  (only visible to linked VNets) is the step that makes traffic go private.
- **Storage resolves the same way:** ADLS Gen2 `<account>.dfs.core.windows.net` →
  Private DNS Zone **`privatelink.dfs.core.windows.net`** (Blob =
  `privatelink.blob.core.windows.net`).
- **Public network access is a separate control.** DNS decides *where the name
  points*; `Allow Public Network Access = Disabled` decides *whether the public door
  answers at all*. Don't conflate them.
- **Custom DNS / hub-and-spoke (the enterprise wrinkle):** most enterprises run a
  resolver in a **hub** VNet; spokes **conditionally forward** the
  `privatelink.azuredatabricks.net` / `*.dfs.core.windows.net` zones to it. If
  forwarding points at public DNS, you get the public IP and Private Link silently
  fails.

## WHY IT BREAKS (cause → effect)

- **Custom DNS not forwarding the privatelink zone → workspace resolves public.**
  Endpoint + zone are correct, but the query never reaches the resolver holding the
  private record. **Effect:** workspace won't load / `Control Plane Request Failure`
  / SSO fails. The **most common Private Link incident.**
- **Misnamed zone → auto-registration silently skipped.** A typo
  (`privatelink.azuredatabricks.com`) means no A record is ever written. **Effect:**
  name resolves public.
- **Long TTL during cutover → "we fixed DNS but it's still broken."** Stale public
  answers linger in caches. **Effect:** correct fix looks broken; flush caches / use
  short TTLs.

## 1.4 illustrative config (the DNS half of Private Link)

```hcl
# Illustrative — zone + VNet link; the A record is AUTO-created by the PE's zone group.
resource "azurerm_private_dns_zone" "adb" {
  name = "privatelink.azuredatabricks.net"   # EXACT name or auto-registration won't fire (Gov: .databricks.azure.us)
  # ... resource_group_name ...
}
resource "azurerm_private_dns_zone_virtual_network_link" "adb" {
  private_dns_zone_name = azurerm_private_dns_zone.adb.name
  virtual_network_id    = azurerm_virtual_network.adb.id   # without this, the VNet never sees the private A record
  registration_enabled  = false                            # this is an override zone, not auto-reg
}
# The private_endpoint's private_dns_zone_group then auto-writes: adb-<id> -> 10.10.4.5
```

**Verify from a VM inside the linked VNet:**
```bash
nslookup adb-1234567890123456.7.azuredatabricks.net
#  ✓ a 10.x private IP (via …privatelink.azuredatabricks.net)
#  ✗ a public 20.x/40.x IP = zone not linked or custom DNS not forwarding
```

**Azure Portal:** Private DNS zones → + Create → name **exactly**
`privatelink.azuredatabricks.net` → Virtual network links → + Add (your VNet/hub).
When the workspace Private Endpoint is created with "Integrate with private DNS
zone = Yes," the A record auto-populates.

## Comparison — how each endpoint shape resolves

| | Public | Service Endpoint | Private Endpoint |
| --- | --- | --- | --- |
| **Resolves to** | Public IP | Public IP (unchanged) | **Private IP** |
| **DNS change** | No | **No** | **Yes — Private DNS Zone** |
| **Traffic path** | Public internet | Backbone, trusted subnet | Fully private |
| **Cost** | Free | Free | Per-endpoint hourly + per-GB |
| **From on-prem** | n/a | No | Yes (with DNS forwarding) |

---

# 1.5 — Common Network Topologies

## What it is (plain language)

- A **network topology** is the *shape* of how VNets connect — to each other, to
  on-prem, to the internet.
- **Hub-and-spoke** is the dominant enterprise shape: one central **hub VNet** holds
  shared services (firewall, on-prem gateway, DNS, private endpoints); many **spoke
  VNets** connect *only to the hub*, not to each other.
- **VNet peering** is the backbone wire joining two VNets (no gateway, no public
  internet).
- **On-prem connectivity:** an encrypted **VPN Gateway** tunnel over the internet,
  or a private dedicated **ExpressRoute** circuit.

**Analogy:** the **hub** is an airport hub; **spokes** are regional airports. You
never fly regional→regional direct — you route through the hub, where security
(firewall) and customs (on-prem gateway) live. **Peering** is the runway joining
each regional airport to the hub. No runway *between* regionals = peering being
**non-transitive** (a feature, not a bug).

**Why an architect cares:** every secure ADB deployment of any size lands in
hub-and-spoke. The **transit VNet** hosting the front-end Private Endpoint *is the
hub*; the **workspace VNet** is a *spoke*. On-prem analysts reach the workspace
privately via ExpressRoute/VPN → hub → peering → the workspace's private IP. If you
can't draw this shape, you can't design or debug a private Databricks workspace.

## How it works — deep dive

- **Hub VNet** hosts: **Azure Firewall** (subnet must be `AzureFirewallSubnet`,
  ≥ `/26`, no NSG); a **gateway** (VPN or ExpressRoute) in a subnet named exactly
  **`GatewaySubnet`** (≥ `/26`); shared **DNS** (Private Resolver/forwarders) and
  shared **Private Endpoints**.
- **Spoke VNets** peer to the hub (not each other), carry **no gateway of their
  own** (borrow the hub's via *gateway transit*), and send egress to the hub
  firewall via a `0.0.0.0/0` UDR.
- **VNet peering** stitches two VNets into one flat routing domain over the backbone.
  Key properties: **non-overlapping address spaces mandatory**; **non-transitive**;
  up to **500** peers/VNet (1,000 via Azure Virtual Network Manager); **billed
  per-GB both directions**. Two settings make hub-and-spoke work: **Allow gateway
  transit** (hub) + **Use remote gateway** (spoke), and **Allow forwarded traffic**
  (both, for spoke→hub-NVA→spoke service chaining).
- **On-prem:** **VPN Gateway** = encrypted IPsec over the public internet (quick,
  cheap, ~up to 10 Gbps, good for dev/test/backup); **ExpressRoute** = private
  dedicated circuit, consistent low latency, SLA, 50 Mbps–10 Gbps (Direct
  100/400 Gbps), best for production/regulated/steady high-volume loads into ADLS.
  Common pattern: ExpressRoute primary + VPN failover.
- **The Databricks mapping (the payoff):** hub = **transit VNet** (front-end
  `databricks_ui_api` + `browser_authentication` PE, gateway, firewall, DNS
  forwarders); spoke = **workspace VNet** (VNet-injected host/container subnets,
  back-end PE). DNS must follow: the `privatelink.azuredatabricks.net` zone must
  resolve to the PE's private IP for every VNet that needs it (linked or forwarded
  via the hub).

## WHY IT BREAKS (cause → effect)

- **Expecting peering to be transitive → spoke A can't reach spoke B.** A→hub and
  B→hub does **not** chain. **Effect:** "they can't talk." *Fix:* UDR via the hub
  NVA, or a direct A↔B peering. *First check:* effective routes on the spoke NIC.
- **Overlapping CIDR between hub and spoke → peering refuses to create.** *Fix:*
  coordinate ranges via central IPAM (1.1) before deploying the spoke.
- **Spoke with both its own gateway and `use_remote_gateways` → apply/peering
  fails.** Mutually exclusive.
- **`browser_authentication` SPOF.** Only **one per region + DNS zone**; deleting
  its host workspace **breaks web SSO region-wide**. Host it in a dedicated
  browser-auth workspace in the transit VNet.

## 1.5 illustrative config (hub-and-spoke peering with gateway transit)

```hcl
# Illustrative — hub offers its gateway; spoke borrows it. Address spaces must NOT overlap.
resource "azurerm_virtual_network_peering" "hub_to_spoke" {
  virtual_network_name      = azurerm_virtual_network.hub.name
  remote_virtual_network_id = azurerm_virtual_network.spoke.id
  allow_forwarded_traffic   = true      # let hub forward spoke-origin traffic
  allow_gateway_transit     = true      # share the hub's on-prem gateway
}
resource "azurerm_virtual_network_peering" "spoke_to_hub" {
  virtual_network_name      = azurerm_virtual_network.spoke.name
  remote_virtual_network_id = azurerm_virtual_network.hub.id
  allow_forwarded_traffic   = true
  use_remote_gateways       = true      # use hub's VPN/ExpressRoute gateway (must exist first)
}
# Plus a spoke UDR: 0.0.0.0/0 -> VirtualAppliance (hub firewall private IP) to force-tunnel egress.
```

**Azure Portal:** Hub VNet → Peerings → + Add → check **Allow gateway transit**
(hub side) + **Use remote gateway** (spoke side) + **Allow forwarded traffic** (both)
→ Add (creates both directions). Then add the spoke `0.0.0.0/0 → firewall` UDR.

## Comparison — topology shapes & on-prem connectivity

| Topology | Connects via | Pros | Cons |
| --- | --- | --- | --- |
| **Hub-and-spoke** | Spokes peer to a central hub | Central firewall/gateway/DNS; scales to 100s; enterprise default | Hub is a shared bottleneck & SPOF |
| **Full mesh** | Every VNet peers every other | Lowest latency | N² peerings; no central inspection |
| **Single flat VNet** | One VNet, many subnets | Simplest | No workload isolation; one blast radius |

| | VPN Gateway | ExpressRoute |
| --- | --- | --- |
| Path | Encrypted IPsec over internet | Private dedicated circuit |
| Latency | Variable | Consistent, low |
| Use when | Dev/test, branch, backup | Production, regulated, steady high-volume to ADLS |

---

## Decision guide (what an architect recommends)

| Situation | Recommend | Why |
| --- | --- | --- |
| Any production / autoscaling workspace | `10.0.0.0/8`, `/16` VNet + equal `/18`–`/22` subnets, **+ headroom** | Subnet CIDR is immutable; size for peak + a future PE subnet |
| RFC 1918 exhausted | **RFC 6598 `100.64.0.0/10`** (after confirming peers/firewalls) | Supported by VNet injection when `10/8` is gone |
| Any production / regulated workload | **VNet injection** (not managed VNet) | Unlocks NSG/UDR control, Private Link, peering, on-prem |
| Every classic workspace | **NAT gateway** on both subnets | Stable egress IP + mandatory egress post-2026-03-31 |
| Regulated profile mandating egress inspection | **UDR → Azure Firewall (DEP)** + control-plane bypass routes | Highest cost — only when the regulatory profile demands it |
| Private workspace / private ADLS | **Private Endpoint + Private DNS Zone** | DNS is the enabling step; without it the PE exists but is unused |
| Cost-sensitive, no "no-public-IP" mandate | **Service Endpoint** for compute→storage | Free, backbone-private, **no DNS change** |
| Production / multi-workspace / on-prem | **Hub-and-spoke** (reuse the customer's landing-zone hub) | Azure default; prerequisite shape for front/back-end Private Link |
| Single dev/PoC, no on-prem | Single VNet + NAT + IP access lists | Don't over-build a hub before it's needed |

**Start free, step up only when mandated:** sizing right, VNet injection, NSG deny
rules, Service Endpoints, and SCC cost nothing and are backbone-private. Add NAT
(now mandatory), then Private Endpoints / DEP firewall / hub-and-spoke only when a
regulator or on-prem reach demands it (endpoint + per-GB + firewall cost).

---

## Uses, edge cases & limitations

- **Uses:** the address-and-toolbox foundation for every later Databricks control;
  a triage map for connectivity bugs (CIDR/subnet → exhaustion; NSG/UDR/NAT →
  egress; DNS → "private but won't load"; peering → "spokes can't talk").
- **Edge cases:** the **5 reserved IPs** make a `/26` 59 usable, not 64; **RFC 6598**
  is supported but unfamiliar (confirm peers); **Service Endpoint Policies don't
  attach** to delegated Databricks subnets; **region-scoped service tags** miss
  secondary-region artifact storage (e.g. a Japan East workspace also needs Japan
  West); **NAT gateway is overridden** by a `0.0.0.0/0 → NVA` UDR; **custom-DNS
  forwarding** is the top Private Link failure; **non-transitive peering** surprises
  people; **`browser_authentication`** is one per region+zone (SPOF); **third-party
  SaaS** (Power BI service) often can't enter via the transit VNet/PE.
- **Limitations:** VNet **`/16`–`/24`**, subnets **≥ `/26`**; **exactly two**
  delegated subnets, **immutable** CIDR + delegation, can't be shared; NSG priority
  **100–4096** (defaults 65000+ undeletable); **one NAT gateway per subnet**, no
  inbound; a spoke has **only one** gateway (local or remote); one hub is
  **regional**; after **2026-03-31** new VNets have **no default outbound**.

## FDE field notes

**Common customer asks (security/network team):**
- *"How big do we size the VNet/subnets — can we change it later?"* — `/16`–`/24`
  VNet, subnets ≥ `/26`, sized for peak; **no, subnet CIDR is immutable** post-deploy.
- *"We're short on `10/8` — can Databricks live in a smaller or CGNAT block?"* —
  Yes: `/16`–`/24`, and **RFC 6598** is supported when RFC 1918 is exhausted.
- *"Will this overlap our on-prem / hub ranges?"* — Their **IPAM** team owns the
  allocation; get the block from them (overlap breaks peering and routing).
- *"If we force all egress through our Azure Firewall, will it break the
  workspace?"* — Only if you forget the **control-plane bypass UDRs**.
- *"Can you give us a stable egress IP to allowlist?"* — Yes, a **NAT gateway**
  Standard public IP.
- *"If we turn on Private Link, does the workspace URL change?"* — **No** — the FQDN
  is unchanged; only its DNS *answer* flips to a private IP.
- *"We already run an Azure Landing Zone hub — is the workspace just a spoke?"* —
  Yes; reuse their hub, firewall, gateway, and DNS.

**Talk-track (positioning):** *"Stage 1 is the foundation we get right before we
touch Databricks: we size the address space once — off your IPAM, for peak nodes
plus headroom, because subnet CIDR can't change after deploy. We put the compute in
your own VNet (VNet injection) so your NSGs, routes, and Private Link apply. The
NSG decides whether a packet is allowed, the UDR decides where it goes, and a NAT
gateway gives you a stable egress IP — and since March 2026 it's mandatory. When you
go private, nothing in your apps changes; the Private DNS Zone just makes the same
name resolve to a private IP. And it all drops into the hub-and-spoke you already
run."*

**What breaks in the field + FIRST diagnostic check:**
- *"Cluster failed to start — insufficient IP addresses"* → check the **container
  subnet's free IPs** vs `2 × nodes` (remember −5 reserved); it's subnet exhaustion,
  not quota.
- *New workspace clusters stuck "Pending" on a fresh VNet (post-2026-03-31)* → check
  there's an **explicit egress** (NAT gateway / UDR→firewall) on both subnets.
- *Peered/on-prem routes silently blackhole* → check for **CIDR overlap** first.
- *Clusters won't launch / no logs after enabling DEP* → check the route table for a
  `0.0.0.0/0 → VirtualAppliance` UDR **missing the bypass routes**; verify via
  **effective routes** on the subnet NIC.
- *Intermittent control-plane loss after firewall lockdown* → check whether the SCC
  relay was allowlisted by **raw IP** instead of **FQDN/service tag**.
- *Workspace won't load / `Control Plane Request Failure` / SSO fails after a Private
  Link cutover* → from a VM **inside the workspace VNet**, `nslookup` the workspace
  URL; a public `20.x/40.x` answer = zone not linked or custom DNS not forwarding.
  **Don't touch NSGs/firewall until the name resolves private.**
- *"We fixed DNS but it's still broken"* → **TTL/caching**; flush / wait out the TTL.
- *Spoke A can't reach spoke B / on-prem can't reach the workspace* → **effective
  routes** on the spoke NIC; peering is **non-transitive** — need a UDR via the hub
  NVA or a direct peer.
- *Web SSO breaks region-wide* → check whether the host workspace holding the single
  `browser_authentication` endpoint was deleted/changed.

**Decision rule for the engagement:** see the Decision guide. Default to VNet
injection + correctly-sized `10/8` address space + NAT gateway + NSG deny rules
(all free or cheap, backbone-private) for any production workload; step up to DEP
firewall, Private Endpoints + Private DNS, and a full hub-and-spoke only when the
customer's regulatory profile or on-prem reach demands it.

---

## Common mistakes / gotchas

- **Reading `/n` backwards** — bigger number = smaller block.
- **Forgetting Azure's 5 reserved IPs** when sizing for a node count.
- **Maxing out the VNet** so there's no room for a Stage-4 Private Link subnet.
- **Sizing for today, not peak** — you can't resize the subnet later.
- **Calling the host subnet "public"** and assuming public IPs — under SCC both are
  private.
- **Putting the VNet in a different region/subscription** than the workspace.
- **A `0.0.0.0/0 → firewall` UDR with no bypass routes** — #1 DEP break.
- **Editing the Databricks-managed NSG rules** — breaks the workspace; layer your own.
- **Reading NSG priority backwards** — lower number wins, first match wins.
- **Hard-coding control-plane IPs in UDRs** — use service tags.
- **Assuming the NAT gateway gives inbound** — it's outbound-only.
- **Thinking the URL must change for Private Link** — only its DNS *answer* changes.
- **Misnaming the Private DNS Zone** or **forgetting the VNet link** — name resolves
  public; auto-registration silently skips.
- **Confusing Service Endpoint (route, no DNS change) with Private Endpoint (DNS to
  private IP).**
- **Expecting peering to be transitive**; **overlapping address spaces**; **naming
  the gateway/firewall subnets wrong** (`GatewaySubnet` / `AzureFirewallSubnet`).
- **Routing the SCC relay through the hub firewall** — needless hop; back-end
  Private Link is the right tool (Stage 3).

---

## References

- [Deploy Azure Databricks in your Azure VNet (VNet injection)](https://learn.microsoft.com/azure/databricks/security/network/classic/vnet-inject) — VNet `/16`–`/24`, subnets ≥ `/26` (min `/28`), 5 reserved IPs, 2 IPs/node, delegation to `Microsoft.Databricks/workspaces`, immutable subnet CIDR, the **Databricks-managed NSG rules** (ports 22/5557 inbound only if SCC off; outbound 443/3306/8443-8451 → `AzureDatabricks`, 443 → `Storage`, 9093 → `EventHub`, 3306 → `Sql`), NAT-gateway recommendation, **March 31 2026** default-outbound change. (Updated 2026-06-25.)
- [User-defined route settings for Azure Databricks](https://learn.microsoft.com/azure/databricks/security/network/classic/udr) — required UDRs, service-tag vs IP routes, control-plane/SCC/metastore/log/Event Hubs bypass, Private Link reduces required routes, secondary-region caveat.
- [Azure network security groups overview](https://learn.microsoft.com/azure/virtual-network/network-security-groups-overview) — stateful 5-tuple rules, priority 100–4096, default rules, service tags.
- [What is Azure NAT Gateway?](https://learn.microsoft.com/azure/nat-gateway/nat-overview) — SNAT, outbound-only, precedence vs UDR/LB/instance IP, the 2026-03-31 private-subnet default.
- [Azure Private Endpoint private DNS zone values](https://learn.microsoft.com/azure/private-link/private-endpoint-dns) — Azure Databricks zone `privatelink.azuredatabricks.net` (Gov `privatelink.databricks.azure.us`), sub-resources `databricks_ui_api` / `browser_authentication`; ADLS Gen2 `privatelink.dfs.core.windows.net`, Blob `privatelink.blob.core.windows.net`. (Updated 2026-05-07.)
- [What is Azure Private DNS?](https://learn.microsoft.com/azure/dns/private-dns-overview) — Private DNS Zones, virtual network links, auto-registration.
- [Hub-spoke network topology in Azure](https://learn.microsoft.com/azure/architecture/networking/architecture/hub-spoke) — hub components, `GatewaySubnet`/`AzureFirewallSubnet` (≥ /26), non-transitive peering, SPOF/per-region-hub guidance.
- [Azure Virtual Network peering](https://learn.microsoft.com/azure/virtual-network/virtual-network-peering-overview) — local vs global, non-overlapping spaces, gateway transit / use-remote-gateways / allow-forwarded-traffic, 500/1000 limit, pricing.
- [Azure ExpressRoute overview](https://learn.microsoft.com/azure/expressroute/expressroute-introduction) · [Azure VPN Gateway overview](https://learn.microsoft.com/azure/vpn-gateway/vpn-gateway-about-vpngateways).
- [RFC 1918](https://datatracker.ietf.org/doc/html/rfc1918) · [RFC 6598](https://datatracker.ietf.org/doc/html/rfc6598).

> Verified against current Microsoft Learn docs on **2026-06-26**: VNet-injection
> page (updated 2026-06-25) confirms CIDR limits, 2 IPs/node, the managed NSG rule
> ports, and the **2026-03-31** default-outbound change; the Private Endpoint DNS
> page (updated 2026-05-07) confirms the Databricks and ADLS Private DNS zone names
> and sub-resources. CIDR limits, ports, service tags, DNS zone names, peering
> limits, ExpressRoute tiers, and the 2026-03-31 change are version/cloud-sensitive —
> reconfirm before quoting to a customer.

> **Hands-on artifact decision:** Stage 1 is conceptual + structural foundations
> (vocabulary, sizing, and primitives that are *configured* in later stages). The
> illustrative snippets above are sized for an architect to read and explain; the
> full apply-ready IaC (the deployable VNet-injection + NSG/UDR/NAT + Private Link
> hub-and-spoke module) earns its own file in **Stage 2–4**, where the workspace is
> actually built. **No separate IaC file is created for this module** — forcing one
> here would duplicate Stage 2–4's deployable artifact without adding value.
