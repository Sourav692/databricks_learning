# Topic 4 - Private Link, DNS & Topologies, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: understand how Private Link, DNS, hub-and-spoke, and data exfiltration
> protection fit together for Azure Databricks - without memorizing every port,
> `groupId`, or FQDN.

---

## The One Mental Model

Think of your Databricks workspace like an **office building you already own**.
Private Link is a **retrofit**, not a new building.

The building already has three doors and is reachable over the **Microsoft
backbone**, but it still has a **public phone number** anyone can dial:

- **Door ① - users coming in** (people, BI tools, REST).
- **Door ② - the cluster room calling out** to head office (the control plane).
- **Door ③ - the cluster room reaching the warehouse** (your storage, ADLS).

Now map the four subtopics onto that building:

- **Private Link (4.1)** installs **three private internal extensions** - one per
  direction. **Front-end** = the line staff dial *in* on. **Back-end** = the line
  the cluster room dials *out* on. **Web-auth** = the receptionist's line that
  confirms a caller is allowed in (SSO).
- **Private DNS (4.2)** is the **company directory edit**. It quietly reroutes
  anyone who dials the public number to the private extension. Without the edit,
  the private line exists but nobody lands on it.
- **Hub-and-spoke (4.3)** is **one guarded lobby** (the transit VNet) with the
  front desk, the directory, and the switchboard. Every floor (workspace spoke)
  wires back to the lobby - you build the private front door once and reuse it.
- **Data exfiltration protection / DEP (4.4)** puts a **customs officer on the
  single exit** (Azure Firewall). The only way data leaves to the public street
  is past a short printed guest list (an FQDN allowlist). Everything else is
  turned away.

**The one sentence:** Private Link swaps the public hop on each path for a private
IP; DNS decides whether anyone lands on it; hub-and-spoke decides where the
endpoints physically live; and DEP forces the leftover internet egress through
one inspected chokepoint. Flip **Public Network Access = Disabled** once all three
paths are private.

---

## 1. Private Link Connection Types: Three Private Extensions

### Simple Explanation

**Azure Private Link** lets a service that normally lives at a *public* address be
reached over a **private IP inside your VNet**, across the Microsoft backbone. The
concrete thing it creates is a **Private Endpoint (PE)** - really just a network
card (NIC) with a private IP in your subnet that maps to a target service.

For Azure Databricks there are **three connection types**, one per path:

- **Back-end** (Azure now calls it *classic compute plane*) - clusters in your
  VNet calling out to the **control plane** (the SCC relay + workspace REST/web app).
- **Front-end** (Azure now calls it *inbound*) - users / BI / REST reaching the
  workspace UI and API.
- **Web-auth** (*browser authentication*) - the SSO/OAuth login callback from
  **Microsoft Entra ID**, made to work over the private path. **One per region.**

### Databricks Meaning

Two facts anchor everything:

1. **Only two `groupId`s exist:** `databricks_ui_api` (used by **both** front-end
   and back-end) and `browser_authentication` (SSO only). What makes a
   `databricks_ui_api` endpoint "front-end" vs "back-end" is **the direction and
   which VNet you place it in** - not a different sub-resource.
2. **Private Link does not replace SCC.** SCC already removed public IPs and
   reversed the call direction (clusters dial *out*); back-end Private Link just
   removes the **last public hop** on that outbound call.

### Plain Customer Explanation

> "Your workspace ships with a public phone number. Private Link gives it unlisted
> internal extensions instead - one for users dialing in, one for clusters dialing
> out, and one for the SSO check. The public number stays printed on the card until
> we flip Public Network Access to Disabled; then only the private lines work."

### What Breaks

- **Forget web-auth entirely** -> REST works, but **browser login spins/fails
  region-wide** because the OAuth callback is still public. This is the single most
  common Private Link outage.
- **Wrong NSG mode** -> back-end needs `NoAzureDatabricksRules`; a hybrid public
  front-end needs `AllRules`. Mix them up and cluster connectivity drops.
- **Delete the web-auth host workspace** -> the one regional
  `browser_authentication` endpoint vanishes -> **every workspace in the region
  loses browser login.**

---

## 2. Private DNS: The Switch That Makes Anyone Use the Private IP

### Simple Explanation

**DNS** turns a name like `adb-1234….7.azuredatabricks.net` into an IP. By default
that name resolves to a **public** control-plane IP. Once you create a Private
Endpoint (a private IP), you must **override DNS** so the *same name* resolves to
the **private IP** - otherwise traffic keeps going out the public door.

The override is a chain: the public zone hands out a **CNAME** to the
`privatelink.azuredatabricks.net` name; your **private zone** holds the **A record**
to the private IP.

The Databricks Private DNS Zone name is **always**
`privatelink.azuredatabricks.net` (fixed - you cannot rename it).

### Databricks Meaning

Two records, two sub-resources:

- `databricks_ui_api` -> A record named `adb-<id>.<n>` -> private IP of the
  workspace/back-end PE.
- `browser_authentication` -> A record named `<region>.pl-auth` (e.g.
  `westus.pl-auth`) -> private IP of the web-auth PE. **This is the one people forget.**

Enterprises running **custom / on-prem DNS** don't get Azure's automatic
integration. They use **conditional forwarding** of `*.azuredatabricks.net`,
`*.privatelink.azuredatabricks.net`, and `*.databricksapps.com` to Azure's
resolver `168.63.129.16`.

### Plain Customer Explanation

> "Private Link installs the private phone line; Private DNS is the directory edit
> that reroutes the public number to it. If a Private Link deployment 'doesn't
> work,' we suspect the directory before the phone line. From a VM in the VNet,
> `nslookup` of the workspace URL must return the private IP, with the public name
> shown as an alias."

### What Breaks

- **PE created but zone not linked / no record** -> clients still resolve the
  **public** IP -> the PE is invisible -> `Control Plane Request Failure` on
  cluster start.
- **Workspace record present, `pl-auth` forgotten** -> workspace loads, then the
  **SSO callback fails** - login page appears, then bounces.
- **Full FQDN put in the A-record name field** -> the name is just the **host
  label** (`adb-<id>.<n>` or `<region>.pl-auth`); the zone supplies the suffix.
  Wrong name = no resolution.

---

## 3. Transit / Hub-and-Spoke: Build the Private Front Door Once

### Simple Explanation

A **hub-and-spoke** network is a central **hub VNet** that every other VNet
(**spokes**) peers to. Spokes don't talk directly - they route through the hub,
where shared services live (firewall, DNS, VPN/ExpressRoute gateway, and shared
private endpoints).

- The **transit VNet** is the hub all user/client traffic enters through. It holds
  the **front-end** PE and the **web-auth** PE.
- The **workspace VNet** is the spoke you inject each workspace into (host +
  container subnets). It holds the **back-end** PE.

### Databricks Meaning

The big decision is **Standard vs Simplified** - blast radius vs cost/effort:

- **Standard (recommended, production):** a dedicated transit VNet plus a dedicated,
  **locked** web-auth workspace (`WEB_AUTH_DO_NOT_DELETE_<region>`) whose only job
  is to host the regional `browser_authentication` PE. Isolates the
  single-point-of-failure SSO endpoint from any real workspace.
- **Simplified (non-prod / single workspace per region):** host
  `browser_authentication` on an **existing** workspace. Less infra - but deleting
  that host breaks SSO for every workspace in the region.

### Plain Customer Explanation

> "The transit hub is the guarded lobby of an office tower - everyone enters through
> one reception desk where security, the directory, and the switchboard live. Each
> team's floor wires back to the lobby, never to each other. We build the private
> front door, DNS, and egress firewall once, then peer every workspace spoke to it."

### What Breaks

The key gotcha:

> **VNet peering gives IP reachability only - not DNS.**

Peering is a hallway; it is not the directory. You still must link the
`privatelink.azuredatabricks.net` zone to **both** VNets (or conditional-forward),
or clusters and users resolve the **public** IP and Private Link silently does
nothing. Other traps: forget **port 6666** on the PE-subnet NSG and clusters fail;
under-size the transit PE subnet and you exhaust IPs as you add workspaces.

---

## 4. Data Exfiltration Protection (DEP): One Inspected Exit

### Simple Explanation

**Data exfiltration** = data leaving to somewhere it shouldn't (a malicious tunnel,
a notebook quietly `POST`ing a dataset to a random host). **DEP** is the
*architecture pattern* that makes that almost impossible: put compute in a
locked-down VNet, force **every** outbound packet through a **single inspected
chokepoint** (an **Azure Firewall** in a central hub), and only allow a short,
explicit **FQDN allowlist** out.

DEP is not one switch. It is the combination of VNet injection + SCC + **back-end
Private Link** + a **UDR** (`0.0.0.0/0 -> firewall`) + an **FQDN allowlist**.

### Databricks Meaning

Four flows - and **two of them bypass the firewall by design**:

1. **Control plane + SCC** -> over **back-end Private Link** (private, bypasses the
   firewall). With Private Link on, the `AzureDatabricks` **service tag isn't
   required** in the UDR - it drops out entirely.
2. **Internet-bound `0.0.0.0/0`** -> via UDR -> **Azure Firewall** -> allowlist only.
3. **ADLS / your storage** -> Private Endpoint or Service Endpoint (bypasses the
   firewall; routing data through it would be ruinous per-GB).
4. **Everything else** -> firewall **default-deny** -> exfil dropped at the exit.

Always **allowlist by FQDN / service tag, not raw IP** - Azure rotates the IPs.
Allowlist the SCC relay by FQDN (`tunnel.<region>.azuredatabricks.net`).

### Plain Customer Explanation

> "DEP is an airport with one exit and a customs officer. People move freely inside
> the terminal, but the only way out to the street is past the officer, who checks a
> printed guest list - pypi, the artifact store, Entra ID - and turns away anyone
> not on it. Your control plane and your data use private tunnels that never touch
> that public door, so you pay to inspect a trickle of genuine internet egress, not
> your data."

### What Breaks

- **Attach the `0.0.0.0/0` -> firewall UDR *before* allowlisting (or Private
  Link)** -> clusters can't reach the relay/artifacts -> **clusters won't launch.**
  Sequence: allowlist / Private Link **first**, then attach the route.
- **Route SCC through the firewall** -> extra hop, per-GB cost, fragile FQDN rules;
  keep it on Private Link.
- **Route table on only one subnet** -> the un-routed subnet's egress **escapes the
  firewall** (an exfil leak). Associate it with **both** host and container subnets.
- **Assume DEP covers serverless** -> it doesn't. Serverless egress is **NCC +
  network policies** (Stage 5), not your firewall.

---

## Memory Hook: The Three Connection Types

| | Back-end (classic) | Front-end (inbound) | Web-auth (browser) |
| --- | --- | --- | --- |
| Secures | Cluster -> control plane (SCC relay + REST) | User/BI/REST -> workspace | SSO/OAuth login callback |
| Direction | Outbound (DP -> CP) | Inbound (user -> CP) | Inbound (auth callback) |
| `groupId` | `databricks_ui_api` | `databricks_ui_api` | `browser_authentication` |
| Lives in | **Workspace** VNet | **Transit** VNet | **Transit** VNet (web-auth ws) |
| How many | 1 per workspace VNet | 1 per transit VNet | **1 per region / DNS zone** |
| NSG mode | `NoAzureDatabricksRules` | `AllRules` (hybrid) | `NoAzureDatabricksRules` |

## Memory Hook: Four Subtopics, One Question Each

| Subtopic | The question it answers |
| --- | --- |
| Private Link (4.1) | Is each path on a private IP instead of a public one? |
| Private DNS (4.2) | Does the name actually resolve to that private IP? |
| Hub-and-spoke (4.3) | Where do the endpoints live, and is the zone linked everywhere? |
| DEP (4.4) | Does leftover internet egress pass one inspected, default-deny door? |

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| Browser login spins/fails but REST works | Is web-auth missing, or does `<region>.pl-auth` not resolve to the private IP? |
| Cluster start fails `Control Plane Request Failure` | Is the private DNS zone linked to the workspace VNet, and does the URL resolve privately from a cluster subnet? |
| Connectivity drops after enabling back-end | Wrong NSG mode - `NoAzureDatabricksRules` for back-end? |
| Region-wide login outage | Was the web-auth host workspace deleted? Does `WEB_AUTH_DO_NOT_DELETE_<region>` still have a Delete lock? |
| Clusters won't launch after the UDR goes on | Was the UDR attached before allowlisting / Private Link? Check firewall deny logs. |
| Egress "leaks" past the firewall | Is the route table on **both** host and container subnets? |
| Private Link configured but traffic seems public | Is the zone linked to this VNet, or is custom DNS forwarding missing? |
| "Does this protect our serverless?" | No - serverless egress is NCC + network policies, not DEP. |

---

## The Architect One-Liner

"Private Link puts every Databricks path - user-in, cluster-out, and the SSO
callback - on a private IP inside your VNet, so we set Public Network Access to
Disabled and answer 'nothing touches the internet.' DNS is the switch that actually
moves traffic onto those private IPs. We build the private front door, DNS, and one
inspected egress firewall once in a transit hub and reuse it across every workspace
spoke. The control plane and your storage stay on private paths that bypass the
firewall - so you pay to inspect a trickle of genuine internet egress, not your
data - and web-auth is the shared region-wide piece we protect with a delete lock."

---

## What To Remember

Don't start by memorizing ports or FQDNs. Start by asking:

1. Is each of the three paths on a **private IP** yet (front-end, back-end, web-auth)?
2. Does **DNS** resolve the name to that private IP - including the `pl-auth` SSO name?
3. Did we **link the private DNS zone** to every VNet that must resolve privately?
4. Are the endpoints placed correctly - **front-end/web-auth in the transit hub,
   back-end in the workspace spoke**?
5. Is the **web-auth workspace locked** so an accidental delete can't take down a region?
6. Does internet egress pass **one inspected firewall** with an FQDN allowlist?
7. Do the **control plane and storage bypass** that firewall on private paths?
8. Is this **classic compute** (DEP applies) or **serverless** (NCC + network policies)?

If you can answer those, the detailed `groupId`s, ports, and Terraform fall into
place much more easily.
