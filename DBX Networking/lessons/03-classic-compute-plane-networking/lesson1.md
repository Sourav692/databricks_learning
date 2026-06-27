# Topic 3 - Classic Compute Plane Networking, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: understand how classic Databricks compute plugs into the customer's
> Azure network without memorizing every port, CIDR, or service tag.

---

## The One Mental Model

Think of classic compute as **a building you lease land for and fit out yourself**.

Stage 2 said there are only **three doors**: ① users in, ② compute talks to the
control plane, ③ compute reaches your data. Stage 3 is the *construction project*
for the building behind doors ② and ③.

- **Managed VNet vs VNet injection** = picking the lot. A managed VNet is a *locked
  serviced apartment* you cannot remodel. **VNet injection** is *a plot of land you
  build on*. This choice is permanent.
- **Subnet sizing** = pouring the foundation. Two equal "address streets" (host +
  container subnets) that you can never re-pave, so size for **peak occupancy** on day one.
- **SCC / No Public IP** = wiring the intercom. The building always **dials out** to
  the control plane, so there is never an inbound door to guard.
- **Egress (NSG / UDR / NAT)** = building the loading dock. **NSG** decides *if* a
  truck may leave, **UDR** decides *which road*, **NAT gateway** decides *what return
  address* it leaves from.
- **Compute to storage** = running a corridor to the shop. Public endpoint, a
  **Service Endpoint** (a recognized badge over the backbone), or a **Private
  Endpoint** (a branch counter inside your building).

The one sentence to carry into a customer room: *SCC keeps clusters private in both
VNet models. VNet injection is what lets you own and harden every other hop. And
almost every sizing, egress, and storage decision here is a **one-way door** you
must get right on day one.*

---

## 1. Managed VNet vs VNet Injection: Who Owns the Network?

### Simple Explanation

Every classic workspace runs its cluster VMs inside an Azure **VNet** in *your*
subscription. The only question is **who owns and configures it**.

- **Managed VNet** - if you pick no network, Databricks auto-creates a **locked**
  VNet in a managed resource group. Zero config, works out of the box, but you cannot
  size it, edit its NSGs, or add routes and endpoints.
- **VNet injection** - you pre-create the VNet and its two subnets, then deploy the
  workspace into them. Now *you* own the address space, NSG rules, UDRs, endpoints,
  and on-prem connectivity. This is the enterprise baseline.

### Databricks Meaning

The choice is **irreversible** (managed to injection is a rebuild + migration) and it
**gates every advanced control**: NSG egress rules, UDR to firewall, service/private
endpoints to ADLS, back-end Private Link, on-prem routing. All of those require
injection. SCC / No Public IP itself works in **both** models; injection just adds the
customization around it.

Injection requires **subnet delegation** to `Microsoft.Databricks/workspaces` on both
subnets. Delegation lets Databricks deploy NICs and apply its **network intent policy**
- a service-managed set of NSG rules it keeps reconciled. You **add** your own Deny
rules; you never **edit** the managed ones (they revert).

### Plain Customer Explanation

> "The managed VNet is move-in-ready for a sandbox, but it is locked. For anything
> facing a security review, inject into a VNet you own so your IPAM, NSG Deny rules,
> firewall egress, and Private Link are all on the table. The catch is the choice is
> permanent, so we default production-bound workspaces to injection."

### What Breaks

- "We will start managed and inject later." There is no convert. A managed workspace
  that later needs Private Link or a firewall is a **full rebuild**.
- IaC deploy fails (`SubnetMissingRequiredDelegation`-style). A subnet is not delegated.
- "Our NSG tightening doesn't stick." Someone edited a *managed* rule; the intent
  policy reconciled it back. Add your own Deny rule instead.

---

## 2. Subnet Sizing: How Many Nodes Can This Workspace Ever Run?

### Simple Explanation

A VNet-injected workspace needs **two dedicated subnets**:

- **Container subnet** (Portal label "private") - the DBR container for each node gets
  its IP here; this is where Spark runs.
- **Host subnet** (Portal label "public") - the Azure VM host for each node gets its IP
  here. Under SCC, **both are private** despite the labels.

Think of a cluster node as a **duplex**: the host subnet is the street the building
sits on, the container subnet is the street the mailbox is on. Every node needs an
address on **both** streets, so the streets must be the **same length**.

### Databricks Meaning

*"Subnet CIDR ranges cannot be changed after deployment."* Sizing is **permanent**.
Each node burns **one IP in each subnet**, so a 10-node cluster uses 10 host IPs *and*
10 container IPs, counted across **all running clusters at once**.

- Azure reserves **5 IPs per subnet**, so usable = total - 5.
- Max nodes is roughly `2^(32 - subnet_prefix) - 5`.
- Always leave the **VNet a couple of bits larger** than the subnets so a Private Link
  subnet fits later (Stage 4) without a re-home.

### Memory Hook: Subnet Size to Node Ceiling

| Subnet | Usable IPs | ~Max nodes | Use when |
| --- | --- | --- | --- |
| `/26` (floor) | 59 | ~59 | Dev / small / capped clusters |
| `/24` | 251 | ~251 | Typical team workspace |
| `/22` | 1,019 | ~1,019 | Large shared / big jobs |
| `/20` | 4,091 | ~4,091 | Very large / many concurrent clusters |

### Plain Customer Explanation

> "Sizing is a one-way door. The subnet CIDR is permanent, so we size for your **peak
> concurrent** node count on day one and leave the VNet a couple of bits larger so
> back-end Private Link fits later without a re-home."

### What Breaks

- Cluster won't start / autoscale stalls with insufficient-IP errors. The subnet was
  sized for *today's* cluster, not **peak concurrent** nodes. Check free IPs on **both**
  subnets vs peak nodes x 2, not the size of the one cluster that failed.
- The only resize options (a Public-Preview workspace network migration, or an
  account-team CIDR increase) both involve **downtime**. Size correctly on day one.

---

## 3. SCC / No Public IP: The Cluster Dials Out

### Simple Explanation

**SCC (Secure Cluster Connectivity, a.k.a. No Public IP / NPIP)** means your cluster
VMs get **no public IP** and your VNet has **no open inbound ports**.

Normally a control plane that manages a VM would have to *call into* it (open an
inbound port). SCC **flips the direction**: the cluster places one **outbound** call
to a Databricks endpoint called the **SCC relay**, and the control plane sends all
commands back down that already-open connection.

Analogy - the customer-service callback: instead of the manager knocking on each
cluster's front door, the **cluster phones the manager first** and keeps the line open.
You can't knock on a door that doesn't exist.

### Databricks Meaning

SCC is the recommended baseline and **on by default** for new workspaces
(`enableNoPublicIp` defaults to `true`). The senior nuance: "No Public IP" means the
*VM* has no public IP and there are *no inbound ports*. The cluster's **outbound** call
to the SCC relay still targets a **public IP** control-plane endpoint over the Microsoft
backbone - that is why the `AzureDatabricks` **service tag** is in your egress
allowlist. **Back-end Private Link** (Stage 4) makes that last outbound hop private too.

### Memory Hook: SCC Disabled vs Enabled vs + Private Link

| Aspect | SCC disabled (legacy) | SCC enabled (default) | + back-end Private Link |
| --- | --- | --- | --- |
| Cluster VM public IP | Yes | **No** | No |
| Open inbound ports | Yes | **No** | No |
| Call direction | CP **inbound** | Cluster **outbound** | Cluster to **private** relay |
| CP-side endpoint | Public IP | **Public IP** | **Private IP** |
| Needs `AzureDatabricks` egress tag | Yes | Yes | **No longer required** |

### Plain Customer Explanation

> "SCC flips the call direction. Your cluster phones the control plane outbound, so no
> public IP and no inbound port. It is the secure default at no cost beyond egress. To
> make that outbound hop private too, layer on back-end Private Link."

### What Breaks

- Clusters fail to start / time out. Outbound 443 to `AzureDatabricks` is blocked, or
  there is no NAT gateway on a new VNet. Check egress and NAT, **not** inbound rules.
- Worked, then stopped after a firewall change. Someone allowlisted the relay by **raw
  IP**; Databricks rotates those. Switch to the `AzureDatabricks` **service tag**.
- VM unexpectedly shows a public IP. SCC didn't apply; check Managed RG -> VM ->
  Networking -> Public IP must be empty.

---

## 4. Egress: NSG, UDR, and NAT Gateway

### Simple Explanation

Once the VMs are private, three Azure primitives control **outbound** traffic from your
cluster subnets:

- **NSG** - a stateful allow/deny list. *Bouncer with a guest list.* Answers: **is this
  allowed?**
- **UDR** - a custom route that overrides Azure's defaults. *Detour sign.* Answers:
  **which path?**
- **NAT Gateway** - gives the subnet a single stable public IP for outbound. *Shared
  mail truck.* Answers: **what public IP does it leave from?**

### Databricks Meaning

Under SCC the VMs are private, so they need an explicit egress path to reach the control
plane / SCC relay, metastore, artifact + log storage, and telemetry - plus data sources
and library repos. Reach them via **service tags**, never raw IPs:

| Destination (service tag) | Port(s) | What for |
| --- | --- | --- |
| `AzureDatabricks` | 443 · 3306 · 8443-8451 | SCC relay + web app/REST, legacy metastore, internal CP calls, UC lineage/logging |
| `Storage` | 443 | Artifact + log Blob storage |
| `EventHub` | 9093 | Telemetry / logging |
| (NFS) | 111 · 2049 | Library installs if you tighten egress |

Databricks **auto-manages** the baseline NSG outbound rules via delegation - do not
edit them. You add Deny rules on *neighbor* subnets. **NSGs are L4 only**: "allow only
pypi.org" needs **Azure Firewall** application rules, not an NSG.

### Plain Customer Explanation

> "NSG decides if a packet may leave, UDR decides where it goes, NAT decides what public
> IP you leave from. Give every injection workspace a NAT for a stable IP, then add NSG
> denies and a firewall UDR only when the security team mandates outbound allowlisting."

### What Breaks

- Clusters never reach Running on a brand-new VNet. After 2026-03-31 there is no implicit
  outbound. Check for a **NAT Gateway on both subnets**.
- UC lineage/logging fails. The managed **8443-8451** outbound rule was deleted or
  shadowed by a higher-priority deny.
- Partner allowlist rejects traffic despite a NAT. A `0.0.0.0/0` UDR to a firewall
  **overrides the NAT** (order: UDR-to-NVA » NAT » instance IP » LB » default route), so
  the **firewall's IP** is the real source. Allowlist that, and keep the service-tag ->
  Internet UDRs so the **relay skips the firewall hop**.

---

## 5. Compute to Storage: Public, Service, or Private Endpoint

### Simple Explanation

When a cluster reads a Delta table it calls **ADLS Gen2** over HTTPS - by default over
its **public endpoint**. A **storage firewall** lets you slam that public door shut, and
you re-open a controlled door one of two ways:

- **Service Endpoint** - flip `Microsoft.Storage` on your subnet so Storage traffic
  leaves over the **Azure backbone** with the subnet's private IP as source, then add a
  **VNet rule** on the storage firewall. *Free, no DNS change, same-region, no on-prem.*
  Analogy: a staff-only backbone corridor; the guard recognizes your building's badge.
- **Private Endpoint** - drop a **NIC with a private IP** inside your VNet (in a
  dedicated `/27`-`/28` subnet, not the delegated ones); the FQDN now resolves to that
  private IP via a **Private DNS Zone** (`privatelink.dfs.core.windows.net`). Then you can
  set Public access = Disabled entirely. *Paid, needs DNS, but works from on-prem.*
  Analogy: the shop opens a private branch counter inside your building.

### Memory Hook: Public vs Service vs Private Endpoint

| Dimension | Public | Service Endpoint | Private Endpoint |
| --- | --- | --- | --- |
| Path | NAT to public IP | **Backbone**, private src IP | **Private Link**, private IP |
| New private IP / DNS change? | No / No | No / **No** | **Yes / Yes** |
| Cost | Egress only | **Free** | **Hourly + per-GB** |
| Reaches on-prem? | Yes (public) | **No** | **Yes** |
| Public access can be Disabled? | No | No | **Yes** |

### Plain Customer Explanation

> "With a Service Endpoint, cluster-to-storage stays on the Microsoft backbone with a
> private source IP, for free, no DNS change. With a Private Endpoint, it is private
> end-to-end, reachable from on-prem, and we can set Public access = Disabled - but it
> costs per-account and needs a DNS override."

### What Breaks

- "PE created but reads still fail." The VNet is not linked to
  `privatelink.dfs.core.windows.net`. From a cluster, `nslookup` the FQDN; a public IP
  means DNS is the problem.
- **Service Endpoint Policies silently break the workspace.** A SEP is an allow-only list
  of accounts a subnet may reach, but it is **unsupported on subnets delegated to a
  managed service** - and because it denies anything unlisted, it blocks the managed
  infra storage. Use a Private Endpoint + Disabled, or Azure Firewall + UDR FQDN
  allowlist (Section 4) for exfiltration control.

---

## How These Pieces Fit Together

### The Story

1. Pick the lot: managed VNet (locked) vs **VNet injection** (yours). Permanent.
2. Pour the foundation: two equal subnets sized for **peak** nodes; leave VNet headroom.
3. Wire the intercom: **SCC** makes clusters dial out, so no public IP and no inbound port.
4. Build the loading dock: **NSG** (allowed?) + **UDR** (which path?) + **NAT** (what IP?).
5. Run a corridor to the shop: storage via public, **Service Endpoint**, or **Private
   Endpoint**.

### The Architect One-Liner

"Clusters run in *your* VNet with no public IPs and no open inbound ports - they dial out
to the control plane. Egress leaves on a stable NAT IP you can allowlist, and your data
stays in your firewalled ADLS Gen2 reached over the Microsoft backbone. We inject and
size on day one because most of these are one-way doors; we add Private Link / Private
Endpoints only when a regulator demands no public-IP hop."

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| Clusters stuck Pending on a fresh VNet | Is a NAT Gateway attached to both subnets? (post-2026-03-31 no implicit outbound) |
| IaC deploy fails on the subnets | Are both subnets delegated to `Microsoft.Databricks/workspaces`? |
| Cluster won't start / autoscale stalls (no IPs) | Free IPs on both subnets vs peak nodes x 2? |
| Clusters fail to reach control plane | Is outbound 443 to the `AzureDatabricks` tag allowed? |
| UC lineage / logging fails after hardening | Was the managed 8443-8451 rule deleted or shadowed? |
| Partner allowlist rejects despite a NAT | Does a `0.0.0.0/0` UDR to a firewall override the NAT? |
| Relay worked then stopped after a firewall change | Was the relay allowlisted by raw IP instead of service tag? |
| "PE created but storage reads still fail" | Does the FQDN resolve to a private IP (DNS / Private DNS zone)? |
| Attaching a Service Endpoint Policy broke the workspace | SEPs are unsupported on delegated Databricks subnets |
| "Can we change managed to injection later?" | No - it is a rebuild + migration |

---

## What To Remember

Do not start by memorizing ports or CIDR math.

Start by asking:

1. Who owns the VNet - did we inject or accept the managed (locked) one?
2. Are the subnets sized for **peak concurrent** nodes (2 IPs per node), and is there
   VNet headroom for a Private Link subnet later?
3. Is SCC on, so clusters have no public IP and no inbound port?
4. Is egress allowed (NSG), routed correctly (UDR), and stable (NAT)?
5. How does compute reach storage - public, Service Endpoint, or Private Endpoint?
6. Is there a regulatory mandate for *no public-IP hop*, or is backbone-private enough?
7. Which of these decisions is a one-way door we must get right today?

If you can answer those seven questions, every detailed setting in `lesson.md` becomes
much easier to place.
