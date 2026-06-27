# Topic 5 - Serverless Networking, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: understand why serverless networking is different and what replaces the
> classic toolkit, without memorizing every NCC limit, service-tag name, or Azure
> setting.

---

## The One Mental Model

Think of serverless compute like a **ride-share** instead of a car you own.

- **Classic compute** is **your own car**: you know the licence plate (the source
  IP / subnet), you control the garage and the route out, so you secure it by
  **recognising the plate** (allowlist your subnet / NAT IP).
- **Serverless compute** is a **ride-share**: a car shows up on demand and leaves
  after. You never own the plate, so you **cannot allowlist an IP**. You secure it
  by trusting the **service** (the `AzureDatabricksServerless` service tag, or an
  NCC private endpoint) and by controlling **where it is allowed to drive**
  (a deny-by-default egress policy).
- **NCC** is the **building's loading dock and approved-courier list**: how
  serverless reaches *your* resources privately.
- **Network policy (egress control)** is the **exit turnstile**: whether an
  outbound call is allowed at all.
- **Storage firewall + service tag (NSP)** is the **trusted backbone lane** into
  your data lake.

For Databricks, this matters because serverless compute runs in **Databricks'
account, not yours**. So peering, NSGs, UDRs, and a static NAT IP you allowlist
**do not apply**. You secure the one path you still own: compute reaching your
storage.

The one sentence: *In serverless the compute is Databricks', not yours, so you
secure the outbound path by service identity and deny-by-default egress, never by
chasing an IP. The control-plane hop is already private, and not yours to
configure.*

---

## 1. Why Serverless Networking Is Different (5.1)

### Simple Explanation

In classic, the cluster VMs live in **your** Azure subscription, inside the VNet
you injected. In serverless, the VMs live in **Databricks'** account, in a region
that matches your workspace. You submit a query; Databricks spins compute up in
seconds from a warm pool and tears it down after. You never see a VM, subnet, or
IP.

### Databricks Meaning

Every classic networking control assumes you know the compute's **source IP /
subnet**. Serverless has **no fixed IP and no subnet you can peer to**. So:

- you **cannot VNet-peer** to it (nothing of yours to peer to), and
- you **cannot whitelist a single static IP** (the egress IP is one of a dynamic,
  rotating pool).

The construct exists because the IP does not. Connectivity is governed by two
account-level constructs instead: **NCC** (5.2) and **network policies** (5.3).

### The Three Paths

- **(1) user -> Databricks** - *unchanged*. You still log in to the control plane;
  same front-end controls (IP access lists, Microsoft Entra ID conditional access,
  front-end Private Link).
- **(2) compute <-> control plane** - for serverless this is **internal to
  Databricks, always backbone + TLS, never public, not configurable**. The
  reassuring path. Do not go hunting for a toggle.
- **(3) compute -> your storage** - the **only** path you configure. Home of NCC,
  egress control, and the storage patterns.

### Plain Customer Explanation

> "Serverless removes the VNet, CIDR, and IP-allowlisting work entirely. There is
> no cluster IP to give you, because the compute is ours, not yours. You secure it
> by service identity and deny-by-default egress, not by chasing an IP. The
> control-plane hop is already private over the backbone."

### What Breaks

If the customer thinks classic controls apply:

- they try to peer to serverless (nothing to peer to), or
- they hard-code today's egress IPs on their storage firewall (breaks tomorrow).

### Memory Hook

| | Classic compute | Serverless compute |
| --- | --- | --- |
| Where it runs | Your subscription / VNet | Databricks' account |
| Source IP | Yours (subnet + stable NAT) | Dynamic pool, not yours |
| Peer to it? | Yes | **No** |
| Whitelist by IP? | Yes | **No** - service tag or PE |
| Outbound control | NSG + UDR + your firewall | **Network policy** (deny-by-default) |
| IP exhaustion risk | Real (you size CIDR) | **None** |

---

## 2. Network Connectivity Configuration - NCC (5.2)

### Simple Explanation

An **NCC** is an **account-level, regional** Databricks object you create in the
Account Console and then **attach to one or more workspaces**. It is the single
control point for how serverless compute in that region reaches your private Azure
resources.

It does two jobs:

1. **Service tag (backbone model):** serverless egress is recognisable via the
   `AzureDatabricksServerless` service tag you allowlist on a storage firewall,
   over the Azure backbone.
2. **Private endpoints:** you add private endpoint rules to the NCC; Databricks
   provisions Azure Private Endpoints *from its serverless network* into your
   resource (ADLS Gen2, Azure SQL, ...); you approve them on your side.

### Databricks Meaning

Because serverless runs on **dynamic IPs in the Databricks account**, the NCC is
the *only* supported way to give that compute private/firewalled access to your
data. It is an account/regional boundary: one NCC can serve a whole business unit's
workspaces in a region, or you split NCCs for logical isolation.

Key operational facts:

- Created by an **account admin**, scoped to **one region**; only workspaces in
  the **same region** can attach.
- After attaching, **wait ~10 minutes** and **restart running serverless
  resources**.
- ADLS Gen2 needs the right `group_id`: **`dfs`** for the data path (and UC model
  logging), **`blob`** for blob access (model-serving artifacts) - one per rule.
- Private endpoint approval is a handshake: rule starts `PENDING`; you **approve**
  on your resource -> `ESTABLISHED`. It **expires after 14 days** if left
  unapproved.

### Plain Customer Explanation

> "Classic VNet injection is your house with your own locks. Serverless is a
> serviced office building you don't own. The NCC is the building's loading dock
> and approved-courier list: you can't fit your own door, but you register which
> couriers (private endpoints) and backbone lanes (service tag) may reach your
> stockroom."

### What Breaks

- NCC missing from the workspace dropdown -> check **NCC region vs workspace
  region** (must be co-regional).
- PE never connects -> the rule is stuck `PENDING` because nobody approved it on
  the storage side (or it `EXPIRED` after 14 days).
- Rules applied but serverless still fails -> serverless compute was not
  **restarted** after the attach/change.
- Cost creeping up -> private endpoints bill **per hour per rule regardless of
  state**. Delete unused rules.

---

## 3. Serverless Egress Control - Network Policies (5.3)

### Simple Explanation

**Egress control** lets an account admin say: "serverless notebooks, jobs, SQL,
pipelines, model serving and Apps may only make outbound connections to *these*
destinations - block everything else." It is configured through a **network
policy** with a network access mode:

- **Full access** - unrestricted outbound internet (the open default).
- **Restricted access** - **deny-by-default**: outbound blocked unless the
  destination is a UC external location, or an FQDN / Azure storage account you
  explicitly listed.

### Databricks Meaning

Serverless runs in Databricks' account - you **cannot** put an NSG or Azure
Firewall in front of it. The network policy **is** your egress firewall for that
plane, and the primary **data-exfiltration control**.

In restricted mode, outbound is limited to:

1. **Unity Catalog external locations** - allowed by default (UC region must equal
   the storage account region).
2. **Explicitly enumerated FQDNs** (max 100).
3. **Explicitly enumerated Azure storage accounts**.
4. **Same-workspace workspace APIs** - cross-workspace is denied.

Implicitly allowed even in restricted mode: workspace storage, essential system
tables, and read-only sample datasets.

The exfil-critical rule: **direct cloud storage access from user code (UDFs,
REPLs, notebook Python) is prohibited by default**. Route reads through UC
securables. The escape hatch is adding the storage account's **exact FQDN** -
never the base domain like `*.dfs.core.windows.net` (that opens every storage
account in the region).

Useful modes:

- **Dry-run** - log violations without blocking. Violations land in
  `system.access.outbound_network` with `access_type = DRY_RUN_DENIAL`; real blocks
  show `DROP`.
- **Block internet destinations (Public Preview)** - stay in Full access but block
  specific bad FQDNs. Always enforced; takes precedence over allowed destinations.

### Plain Customer Explanation

> "NCC is the private hallway to your storage. The restricted policy is the
> building's exit turnstile: by default nobody leaves, and only names on the
> approved list get through. It is what stops a careless UDF from POSTing your data
> to evil.example.com."

### What Breaks

- Jobs fail outbound after enforcement -> query `system.access.outbound_network`
  for `access_type = 'DROP'`; it names the exact FQDN/storage to allowlist (or
  shows you skipped the compute restart).
- A PE does not bypass the policy -> PE traffic is **still** subject to the
  allowlist. **NCC = can I reach it privately? Network policy = am I allowed to
  reach it at all? Both must say yes.**
- Account-level network-policy APIs reject your PAT -> they need an account-admin
  **OAuth** token.

---

## 4. Serverless Storage Access Patterns (5.4)

### Simple Explanation

When serverless reaches your **ADLS Gen2**, traffic crosses from Databricks'
network into yours, and your storage firewall has to be told to trust it. Three
patterns, least to most locked-down:

1. **Public (no firewall)** - storage accepts any network. Simplest, least secure
   (dev/PoC only).
2. **Storage firewall + service tag via NSP (recommended default)** - storage
   stays firewalled; you allowlist `AzureDatabricksServerless.<region>` via a
   **Network Security Perimeter**. Traffic rides the **Azure backbone** - no public
   internet, no per-GB charge.
3. **Private Endpoints via NCC** - Databricks raises a private endpoint into your
   storage; the FQDN resolves to a **private IP**. Use when a private-link mandate
   requires it.

### Databricks Meaning

On top of *all three* patterns, **authorization is separate**. There are two locks:

- **Network reachability** - the storage firewall trusts serverless (public / NSP
  service tag / private endpoint).
- **Authorization** - a **managed identity** (the **Access Connector for Azure
  Databricks**) registered in Unity Catalog, granted `Storage Blob Data
  Contributor`, with the actual call using a **short-lived 1-hour scoped
  credential** vended per-workload.

Why managed identity over a service principal: no secrets to rotate, and a managed
identity can reach storage **protected by network rules** (trusted as a resource
instance), which a service principal cannot.

For the NSP, stay in **Transition mode** (evaluates NSP rules first, falls back to
the storage firewall on no-match). **Enforced** mode bypasses the storage firewall
for *every* service, breaking anything not also onboarded to the NSP.

### Plain Customer Explanation

> "Two doors, two keys. The network pattern only opens the front door
> (reachability). The managed identity plus the 1-hour scoped token is the keycard
> (authorization - which floors you may enter, expiring in an hour). Open the wrong
> lock and you still can't get in."

### What Breaks

The #1 serverless ticket: "my serverless SQL warehouse can't read my firewalled
ADLS, but my classic cluster can." The cause is almost always the **network lock,
not the grant** - the storage firewall was never told to trust serverless, even
though the managed-identity grant is fine.

### Memory Hook

| | A. Public | B. Service tag (NSP) | C. Private endpoint (NCC) |
| --- | --- | --- | --- |
| Storage firewall | Off | On (NSP, Transition) | On (PE-only optional) |
| What you allowlist | Nothing | `AzureDatabricksServerless.<region>` | A dedicated private endpoint |
| Path | Public IP | **Azure backbone** | Private IP (FQDN -> PE) |
| Networking cost | Lowest | Low (no data-proc charge) | Highest (per-hour per-rule + per-GB) |
| When | Dev / PoC | **Default for prod** | Private-link mandate only |

---

## How These Pieces Fit Together

### The Story

1. Serverless compute runs in Databricks' account - no VNet, no CIDR, no IP to
   allowlist.
2. Path (1) user login and path (2) compute<->control are unchanged or already
   private; you don't configure them.
3. For path (3), the **NCC** governs *how* serverless reaches your storage
   privately (service tag over backbone, or private endpoint).
4. The **network policy** governs *whether* an outbound call is allowed at all
   (deny-by-default in restricted mode).
5. The **storage pattern** (public / NSP service tag / private endpoint) opens the
   network door; the **managed identity + 1-hour token** is the auth key.

### The Architect One-Liner

"Serverless compute is Databricks', not yours, so the classic toolkit (peering,
NSG, UDR, static NAT IP) does not apply. You secure the one path you own - compute
to your storage - with NCC for the private path, a restricted network policy for
deny-by-default egress, and a storage firewall that trusts the
`AzureDatabricksServerless` service tag. The control-plane hop is already backbone
+ TLS. Access needs both locks: network reachability and a UC-governed managed
identity."

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| Classic reads firewalled ADLS, serverless can't | Check the **network lock**, not the grant - is the storage on an NSP trusting `AzureDatabricksServerless.<region>` (or an `ESTABLISHED` PE)? |
| NCC private endpoint never connects | Is the PE rule still `PENDING`? Nobody approved it on the storage side (expires after 14 days). |
| Rules applied but serverless still fails | Was serverless compute **restarted** after the NCC attach/change (~10 min propagation)? |
| NCC missing from workspace dropdown | Is the **NCC region == workspace region**? |
| Jobs/notebooks fail outbound after enforcement | Query `system.access.outbound_network` for `DROP` - it names the exact FQDN/storage to allowlist. |
| Model Serving won't deploy / can't log models | Check `ML Build` denials (allowlist repos) and the PE `group_id` - serving needs both `blob` and `dfs`. |
| UDF reading storage directly fails | Expected in restricted mode - move the read to a **UC securable**, don't widen the allowlist. |
| Azure bill creeping up | PEs bill **per hour per rule regardless of state** - delete unused rules. |

---

## What To Remember

Do not start by asking for the cluster IP.

Start by asking:

1. Whose network is the compute in? (Serverless = Databricks', not yours.)
2. Which path am I configuring? (Only path 3, compute -> storage.)
3. How does serverless reach my storage privately? (NCC: service tag or PE.)
4. Am I allowed to make this outbound call at all? (Network policy.)
5. Does the storage firewall trust serverless? (Network lock.)
6. Is the managed identity granted and UC-governed? (Auth lock.)
7. Did I restart serverless and keep everything co-regional?

If you can answer those seven, the detailed NCC limits and NSP settings become much
easier to understand.
