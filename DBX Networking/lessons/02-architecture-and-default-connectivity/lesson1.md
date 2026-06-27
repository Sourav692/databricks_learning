# Topic 2 - Architecture & Default Connectivity, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: be able to draw the Azure Databricks architecture on a whiteboard and
> explain who owns what, which way connections point, and where data lives -
> without memorizing every port, service tag, or deadline.

---

## The One Mental Model

Think of Azure Databricks as **an airport with exactly three doors.**

- The **control plane** is the **control tower**. Databricks runs it in *their*
  own Azure subscription. It schedules and directs flights. It never holds your
  cargo.
- The **compute plane** is the **runway and aircraft** where your data is
  actually processed.
  - **Classic** = your runway at *your* airport (your subscription / your VNet).
  - **Serverless** = you rent runway at *Databricks'* airport.
- Your **data** lives in *your* storage (ADLS Gen2 behind Unity Catalog). The
  compute plane reads it directly. It **never** flows through the control tower.

The three doors that carry all the traffic:

1. **User -> Databricks** (the front door).
2. **Compute <-> Control plane** (the staff intercom, outbound-only).
3. **Compute -> Storage** (the loading dock).

The one sentence to remember:

> Databricks runs the brain in its own subscription; your data is processed in
> the compute plane and never leaves your storage; and connections only ever
> point **outward** from compute to control.

Every later lesson (VNet injection, SCC, Private Link, NCC, exfiltration
protection) is just *"how do I lock door number N on this map?"*

---

## 1. Control Plane vs Compute Plane

### Simple Explanation

There are two separate sets of machines, run by two parties, in two
subscriptions.

- **Control plane = the brains.** Web UI, REST APIs, the job/cluster managers,
  Unity Catalog metadata and access decisions, and the **SCC relay**. Databricks
  owns and runs it. You get **no network knobs inside it**.
- **Compute plane = the muscle.** Where clusters and SQL warehouses actually run
  Spark on your data. The only real difference between the two flavors is *whose
  subscription the VMs live in*:
  - **Classic** runs in **your** subscription, in a VNet you can see and control.
  - **Serverless** runs in the **Databricks-owned** subscription, pre-warmed and
    fully managed. You never see the VMs.

### Databricks Meaning

Knowing the plane tells you which toolbox is on the table.

- In **classic**, the compute is in *your* VNet, so you can attach an **NSG**, a
  **UDR**, a firewall, or a **Private Endpoint**.
- In **serverless**, there is **no VNet of yours**, so none of those tools apply.
  That is exactly why **NCC** exists (covered in Stage 5).

### Plain Customer Explanation

> "Two planes. Databricks runs the brain in its own subscription. Your data is
> processed in the compute plane and never leaves your storage. Classic puts
> compute in your VNet so you own the network controls; serverless trades that
> VNet for speed and uses NCC instead."

### What Breaks

- If someone thinks **customer data passes through the control plane**, they
  worry about the wrong thing. Control plane = metadata and orchestration only.
- If someone tries to put an **NSG on serverless**, it cannot work - there is no
  customer VNet. That confusion is the most common serverless mistake.

### Memory Hook

| Dimension | Classic | Serverless |
| --- | --- | --- |
| Runs in | Your subscription / VNet | Databricks-owned subscription |
| Network you control | The VNet (managed or injected) | None |
| Public IPs on compute | No (NPIP) | No |
| Secure egress with | NSG / UDR / NAT / Firewall / Private Endpoint | **NCC** + network policies |
| Startup | Minutes | Seconds (pre-warmed) |

> **Portal naming gotcha:** a classic workspace is labelled a **"Hybrid
> workspace"** in the Azure Portal. "Hybrid" means classic compute in your
> subscription - it does *not* mean hybrid-cloud.

---

## 2. The Secure Boundary: Connections Point Outward

### Simple Explanation

The platform's single most important security property is "**no inbound
ports**." It is not magic. It is a **reversed connection**.

With **Secure Cluster Connectivity (SCC) / No Public IP (NPIP)** - the default
for new classic workspaces:

1. Cluster VMs have **no public IPs**, and your VNet has **no open inbound
   ports**. Nothing on the internet, or even the control plane, can dial *into* a
   cluster.
2. So how does "start this job" arrive? **The cluster dials out first.** At
   startup, each cluster opens an **outbound** connection on **port 443** to the
   **SCC relay**, forming a reverse tunnel.
3. The control plane sends instructions **back down that already-open tunnel**.
   It never initiates inbound.

The phone analogy: *you call support; they answer on your open line. They can
never call your phone.*

### Databricks Meaning

- **Serverless** reaches the same property a different way: serverless VMs also
  have no public IPs, and the compute-to-control path is **always over the
  Microsoft backbone** with TLS. There is no SCC relay because there is no VNet
  of yours.
- For a **stable outbound IP** (so a partner like Salesforce can allowlist you),
  classic routes egress through an **Azure NAT Gateway**. Serverless egress IPs
  are dynamic, so you pivot to the **`AzureDatabricksServerless` service tag** or
  NCC instead.

### Plain Customer Explanation

> "'No inbound ports' is not a feature we toggle - it is a consequence of
> reversing the connection. The cluster dials out to the relay on 443; the
> control plane only ever answers down that tunnel. Nothing outside can open a
> connection to your compute."

### What Breaks

- **Clusters stuck "Pending" on a fresh VNet** -> after 2026-03-31, new Azure
  VNets have no default outbound, so the cluster cannot reach the SCC relay. Add
  a NAT Gateway (or a UDR to a firewall).
- **"We see a public IP" panic** -> under NPIP the VM's Public IP field is empty;
  check the cluster VM in the managed RG.
- **Intermittent control-plane loss after a firewall lockdown** -> they
  allowlisted the SCC relay by raw IP. Databricks rotates the IPs behind the
  relay FQDNs. Allowlist the **domain names**, not IPs.

---

## 3. Backbone vs Internet (The "Is Our Data On The Internet?" Conversation)

### Simple Explanation

The **Microsoft backbone** is Microsoft's private global network. Azure-to-Azure
traffic rides it **without ever touching the public internet**.

A lot of Databricks traffic is *already* off the public internet by default:

- **all** classic compute <-> control-plane traffic rides the backbone - **even
  with SCC disabled**.
- **all** serverless traffic rides the backbone.

### Databricks Meaning

"Public IP" in this context means *routable over the backbone*, not *exposed to
the open internet*. **Private Link** removes the public-IP *hop* the auditor
cares about. It does **not** "get you off the internet," because you mostly
already are.

### Plain Customer Explanation

> "A lot is already off the public internet by default. Private Link and NCC just
> remove the last public-IP hop the auditor cares about. So let's not over-buy
> Private Link you don't actually need."

### What Breaks

If you skip this conversation, customers over-buy Private Link (per-GB cost,
Premium prerequisite, DNS work) to solve a problem they did not have.

---

## 4. The Three Connectivity Paths

This is the scaffold for the entire track. Secure a building and you ask exactly
three questions: who comes in the front door, how staff reach head office, and
how the loading dock is locked. Same three for Databricks.

### Simple Explanation

| # | Path | Who talks to whom | Analogy |
| --- | --- | --- | --- |
| ① | **User -> Databricks** (front-end) | Browser, BI (JDBC/ODBC), REST/CLI to the workspace URL | Front door |
| ② | **Compute <-> Control plane** (back-end) | Cluster phoning home to be managed | Staff intercom |
| ③ | **Compute -> Storage** (data path) | Cluster reading/writing your ADLS Gen2 data | Loading dock |

### Databricks Meaning

- **Path ① (front door)** is identical for classic and serverless - it always
  lands on the control plane over HTTPS 443. Hardened (weakest to strongest) by:
  open public, then **IP access lists**, then **front-end Private Link** with
  public access disabled.
- **Path ② (intercom)** differs most by plane. Classic = SCC relay, outbound 443;
  **back-end Private Link** replaces the public-IP hop with a private one.
  Serverless = always backbone + TLS, no SCC, managed by Databricks.
- **Path ③ (loading dock)** is the one security teams scrutinize most. Classic ->
  ADLS hardens from public endpoint, to **storage firewall + Service Endpoint**
  (free, backbone-private), to **Private Endpoint** (private IP, per-GB cost).
  Serverless -> ADLS uses **NCC**: the `AzureDatabricksServerless.<region>`
  service tag by default, or an NCC private endpoint when mandated.

### Plain Customer Explanation

> "There are only three traffic paths: users in, clusters to control, clusters to
> data. We secure each one independently. When something breaks, *which path*
> failed tells us which control to inspect."

### What Breaks

- **Serverless can't reach firewalled storage** -> someone applied a classic
  "allow my subnet" rule to serverless. Serverless needs the NCC + the
  `AzureDatabricksServerless.<region>` service tag (or an NCC private endpoint).
- **"Private now" but ADLS still public** -> Paths ① and ② were locked and Path ③
  was forgotten. Check the storage account networking is not still "Enabled from
  all networks."

### Memory Hook

| | ① User -> DBX | ② Compute <-> Control | ③ Compute -> Storage |
| --- | --- | --- | --- |
| Direction | Inbound to control plane | Outbound from compute | Outbound from compute |
| Classic control | IP access list / front-end PL | SCC + VNet injection, back-end PL | Storage firewall + Service/Private Endpoint |
| Serverless control | Same (front-end PL / IP ACL) | Managed backbone + TLS (no SCC) | **NCC** service tag / NCC PE |
| Key port | 443 | 443 (SCC) | 443 to storage |

---

## 5. Deployment & Workspace Storage

### Simple Explanation

Azure Databricks is a **first-party Azure service**
(`Microsoft.Databricks/workspaces`) - sold, billed, and RBAC'd by Azure itself.

One `create` lands resources in **two resource groups**:

1. **Your RG** - holds the workspace object you manage.
2. **The managed RG** - Databricks-created/managed, but still **in your
   subscription**. Holds the **workspace storage account**, the managed identity
   plumbing, the **managed VNet** (only if you did not inject your own), and
   cluster disks. **Viewable, not editable - lock it, never hand-edit it.**

### Databricks Meaning

- The **workspace storage account** is Databricks' housekeeping: job results,
  notebook revisions, cluster logs, plus the legacy **DBFS root**. It is **not
  your data lake**.
- The governance backbone is the **account -> workspace -> metastore** spine:
  - **Account** holds identity, billing, and Unity Catalog metastores - many
    workspaces.
  - **Workspace** is the runtime/collaboration unit in one region.
  - **Metastore** is the **account-level, regional** governance root - **one per
    region**, shared by that region's workspaces.

### Plain Customer Explanation

> "Housekeeping goes in workspace storage; governed data goes in ADLS Gen2 behind
> Unity Catalog - never the DBFS root. And there are two separate role systems:
> Azure RBAC creates the workspace resource, but a Databricks account admin
> governs the account and metastore. Being Azure Owner does not make you a
> Databricks account admin."

### What Breaks

- **Deleting the managed RG / workspace storage account** makes the workspace
  **unrecoverable**. Lock it.
- **Jobs fail on `dbfs:/` paths** -> new accounts ship without a DBFS root.
  Migrate to a UC volume or external location; do not re-enable DBFS.
- **"I'm Owner but can't admin the metastore"** -> wrong role system. Azure RBAC
  creates the resource; a Databricks account admin governs the account.

### Memory Hook

| Concern | Workspace storage / DBFS root | Unity Catalog + ADLS Gen2 |
| --- | --- | --- |
| Governance | Workspace-scoped, coarse | Central metastore, fine-grained grants |
| Lineage & audit | None for raw paths | Full lineage + `system.access` audit |
| Status | Deprecated / legacy | Recommended, GA |

> **Tier reality:** **Standard tier is EOL 2026-10-01.** Treat **Premium** as
> baseline - Private Link, CMK, IP access lists, and Compliance Security Profile
> are all Premium-only.

---

## How These Pieces Fit Together for Azure Databricks

### The Story

1. Databricks runs the **control plane** (brain) in its own subscription.
2. Your **compute plane** processes data - classic in your VNet, serverless in
   Databricks'.
3. The secure boundary means connections point **outward** (SCC relay, 443).
4. Most traffic is already on the **Microsoft backbone**, not the open internet.
5. Only **three paths** carry traffic; each is secured independently.
6. One `create` lands **two resource groups**; governed data lives in **UC +
   ADLS Gen2**, never the DBFS root.

### The Architect One-Liner

> "Two planes: Databricks runs the brain in its own subscription; your data is
> processed in the compute plane and never leaves your storage. There are only
> three traffic paths - users in, clusters to control, clusters to data - and we
> secure each one independently. A lot is already off the public internet by
> default; Private Link and NCC just remove the last public-IP hop the auditor
> cares about."

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| Clusters stuck "Pending" on a fresh VNet | Is there outbound egress (NAT Gateway / UDR to firewall)? New VNets have no default outbound after 2026-03-31. |
| "We see a public IP" panic | Check the cluster VM in the managed RG - under NPIP the Public IP field is empty. |
| Intermittent control-plane loss after firewall lockdown | Did they allowlist the SCC relay by raw IP instead of FQDN? |
| Serverless can't reach firewalled storage | Is the NCC bound, with the `AzureDatabricksServerless.<region>` service tag or NCC PE allowed? |
| "Private now" but ADLS still public | Path ③ forgotten - is storage still "Enabled from all networks"? |
| Jobs/notebooks fail on `dbfs:/` paths | New accounts ship without DBFS root - migrate to a UC volume / external location. |
| "I'm Owner but can't admin the account/metastore" | Wrong role system - Azure RBAC creates the resource; a Databricks account admin governs the account. |

---

## What To Remember

Do not start by memorizing ports or service tags.

Start by asking:

1. Which plane is the compute - classic (your VNet) or serverless (no VNet)?
2. Which direction does the connection point? (Compute always dials *out*.)
3. Is this traffic on the backbone or genuinely on the public internet?
4. Which of the three paths is involved - front door, intercom, or loading dock?
5. Where does the data actually live - UC + ADLS Gen2, or (wrongly) the DBFS root?
6. Which resource group owns this - my RG, or the locked managed RG?
7. Which role system is in play - Azure RBAC, or Databricks account admin?

If you can answer those seven questions, every later control is just "harden one
hop on this map."
