# Topic 9 - Synthesis, Best Practices & Interview Prep, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: be able to *assemble and defend* a secure Azure Databricks design at a
> whiteboard - pick a blueprint, recite a checklist, and walk a packet - without
> memorizing every port, FQDN, or quota.

---

## The One Mental Model

Think of securing an Azure Databricks workspace like **fitting locks on a secure
building that has exactly three doors**.

- **The three doors** are the only paths that ever matter:
  - **Door ①** = user -> workspace (the front door / lobby).
  - **Door ②** = compute -> control plane (the staff intercom, outbound-only).
  - **Door ③** = compute -> storage (the loading dock to your data).
- **A blueprint** = a choice of one lock on each door, plus identity, governance,
  and encryption layered on top. Name the lock on each door and you have drawn the
  whole architecture.
- **A checklist** = the same three doors, but for each you know the *floor lock*
  (free, always on) and the *mandated upgrade* (costs money, add only when a
  regulator pays for it).
- **An interview answer** = trace the packet, name the control at each hop, state
  the trade-off (security vs cost vs complexity), and know what breaks.

The one sentence: *Pick the closest of four floor plans, harden the three doors
with the cheapest lock that meets the compliance language, and be able to walk the
packet and name what breaks at each hop.*

For Databricks this matters because you almost never invent a new design. You pick
a known-good reference and adapt it. If you can name the control on each of the
three doors, you can draw the entire architecture from memory.

---

## 1. The Four Reference Architectures (9.1)

### Simple Explanation

A **reference architecture** is a known-good blueprint: "for *this* security
requirement, wire workspace, network, and storage together *this* way." You don't
design from scratch per customer - you pick the closest of four and adapt.

Think of them as floor plans for a secure building:

- **Standard (recommended)** = corporate HQ with a guarded lobby and badge-only
  server rooms. The enterprise default.
- **Simplified** = the same building with fewer rooms, for small or single-region
  estates.
- **Full DEP / isolated-regulated** = Standard plus a mantrap and a guard who
  inspects everything leaving. For banks, healthcare, government.
- **IP-exhaustion architecture** = making sure you built enough parking (IP
  addresses) before move-in, because you can't repave the lot later.

### Databricks Meaning

| Blueprint | The short version |
| --- | --- |
| **Standard** | VNet injection + SCC + back-end Private Link; hub-and-spoke with a separate **transit VNet** holding the front-end / `browser_authentication` endpoints. |
| **Simplified** | Same three controls, fewer VNets/subnets; front-end and back-end both use `databricks_ui_api`; PEs can be shared. |
| **Full DEP** | Standard **plus** an Azure Firewall hub that inspects all egress, storage firewall on ADLS, Public Network Access **Disabled**, CMK, and Compliance Security Profile. |
| **IP-exhaustion** | Size subnets for **peak** autoscaling; CIDR is immutable after deploy, so the only failure mode is *too small*. |

The sizing rule worth remembering: **2 IPs per node** (one host + one container),
**Azure reserves 5 IPs** per subnet, so max nodes is roughly `2^(32-n) - 5` for a
`/n` subnet. VNet `/16`-`/24`; subnets `/26` minimum. Leave headroom for the `/27`
back-end PE subnet and `/28` storage-PE subnet you'll add later.

### Plain Customer Explanation

> "We don't custom-build a design for every customer. We pick the closest of four
> proven references - Standard, Simplified, full-DEP, or the IP-sizing layout - and
> adapt it. That makes the design reviewable and the security review predictable."

### What Breaks

- **Quote Simplified for a bank** -> fails the audit (no DEP firewall, public access
  often left on).
- **Quote full-DEP for a sandbox** -> burns budget on a firewall and Private
  Endpoints nobody needs.
- **Size the subnet for today, not peak** -> "cluster failed to start: insufficient
  IP addresses," and CIDR can't be changed in place (rescue is a Public Preview
  migration, not Terraform).

---

## 2. Walk the Packet (the interview centerpiece)

### Simple Explanation

The single most asked senior question is: *"Walk me through how a query reaches our
private storage without touching the internet."* They want a hop-by-hop trace, not
a feature list. Trace the packet, name the control, say what breaks without it.

### Databricks Meaning - the full-DEP packet walk

| Hop | What happens | Control | What breaks without it |
| --- | --- | --- | --- |
| 1. User -> workspace | URL resolves via `privatelink.azuredatabricks.net` to the front-end PE | Front-end PL; IP access list or public access Disabled | URL resolves to a public IP - anyone reaches the login page |
| 2. SSO callback | Entra ID callback returns via `browser_authentication` | Web-auth PE (one per region/DNS zone) | On a fully-private network SSO has no route - login fails region-wide |
| 3. CP schedules cluster | VMs in container subnet, no public IPs | SCC/NPIP; VNet injection | VMs get public IPs + open inbound ports |
| 4. Cluster -> control plane | Dials outbound to SCC relay over back-end PE | Back-end PL; SCC reversed call; `NoAzureDatabricksRules` | Outbound rides the public internet to the relay |
| 5. Cluster -> ADLS | Reaches `*.dfs.core.windows.net` via SE or PE; storage firewall denies the rest | Storage firewall; SE/PE; UC external location + access connector | Data path on public internet; storage open to other networks |
| 6. Any other egress | `0.0.0.0/0` routed by UDR to Azure Firewall; only allowlisted FQDNs pass | UDR + Azure Firewall application rules | A compromised notebook can exfiltrate to any host |

### Plain Customer Explanation

> "The user hits a private endpoint resolved by a private DNS zone, SSO returns
> through the web-auth endpoint, SCC gives the cluster no public IP and dials the
> control plane outbound over back-end Private Link, the cluster reaches
> Unity-Catalog-governed ADLS through a storage private endpoint, and any other
> egress is forced through an Azure Firewall that only allows Databricks' own
> FQDNs."

### What Breaks

The "without it" column above is the whole point: each missing control opens one
specific hole. If you can name the hole, you understand the control.

---

## 3. The Best-Practices Checklist (9.2)

### Simple Explanation

A **checklist** is the short list of settings a secure workspace should have on day
one - what a security architect asks about and an auditor checks. Picture a pilot's
**pre-flight checklist**: each item is small, but skip one and the whole flight is
at risk.

The mental trick: the **MUST** items are the airframe (fly without one and you
crash). The **when-mandated** items are bolt-ons you add *only when the regulatory
profile pays for the cost*.

### Databricks Meaning - floor vs mandated upgrade

| Door / theme | Floor control (free, always on) | Mandated upgrade (costs money) |
| --- | --- | --- |
| ① User -> workspace | IP access lists | Front-end PL + public access Disabled |
| ② Compute -> control plane | SCC + VNet injection | Back-end Private Link |
| ③ Compute -> storage | Storage firewall + **Service Endpoints** | **Private Endpoints** (per-GB) |
| Egress | NAT Gateway (stable egress IP) | UDR `0.0.0.0/0` -> Azure Firewall (DEP) |
| Encryption | TLS 1.2+ in transit | CMK (Key Vault) |
| Plan | Premium (the security floor) | CSP / ESM (regulated profiles) |
| Identity | SSO + SCIM + MFA + separate admins | - |
| Data | Unity Catalog for all governed data | - |
| Audit / IaC | `system.access.audit` + Terraform | - |
| Serverless | Bind an NCC + egress policy (default-deny) | NCC private endpoints |

### Plain Customer Explanation

> "When you ask 'is this workspace secure?', I walk the three doors. At each door I
> name the free floor control and the paid upgrade. We start with the free,
> backbone-private controls and only step up to Private Link or a firewall where
> your regulation actually requires no public-IP hop."

### What Breaks

- **Public access left Enabled** on a "fully private" design - front-end PL alone
  doesn't disable the public path.
- **IP access lists created but `enableIpAccessLists` never turned on** - stored but
  not enforced.
- **Storage firewall turned on without the SE/PE allow rule** - locks the cluster
  out of its own data.
- **Legacy "No Isolation Shared" access mode** - CAN ATTACH users can read keys
  from driver logs; require Standard (shared) or Dedicated.

---

## 4. SCC vs Private Link - don't conflate them

### Simple Explanation

This is the most common interview trap. They are different layers:

- **SCC (Secure Cluster Connectivity / No Public IP)** removes the **inbound**
  attack surface: cluster VMs get no public IP and no open inbound ports. The
  cluster dials **out** to the SCC relay on 443; the control plane sends commands
  back down that reverse tunnel.
- **Back-end Private Link** removes the last **public hop**: under plain SCC the
  CP<->DP traffic still rides public IPs on the Microsoft backbone (TLS, but not
  private-IP). Back-end PL makes it private IP end to end.

### Databricks Meaning

> SCC = "no public IP / no inbound." Private Link = "no public hop." Say both. SCC
> alone does *not* protect Door ① (user) or Door ③ (storage), and it does not make
> CP<->DP traffic private-IP - it just reverses the call direction.

### Plain Customer Explanation

> "SCC means your cluster VMs have no public address and accept no inbound
> connections - they call out to us. Private Link is the next step: it takes that
> outbound call off the public backbone and onto a private IP path. They solve
> different problems, so for a fully-private answer you want both."

### What Breaks

- Saying "SCC privatizes CP<->DP" - it's still public IP on the backbone until
  back-end PL is on.
- Forgetting SCC does nothing for the user door or the storage door.

---

## 5. Serverless Reaches Private ADLS via NCC

### Simple Explanation

Serverless compute runs in **Databricks' own account** with **dynamic IPs**. You
can't peer it or allowlist a static IP. The bridge is an **NCC (Network
Connectivity Configuration)** - an account-level, regional object you create and
attach to workspaces.

### Databricks Meaning

- For private ADLS, add a **private endpoint rule** on the NCC: sub-resource `dfs`
  for ADLS Gen2 data, `blob` for blob / Model Serving artifacts (you may need
  both). Databricks raises the PE request; **you approve it on the storage
  account**; status goes PENDING -> ESTABLISHED.
- If you only need firewall allowlisting, the NCC also exposes stable
  **service-endpoint subnet IDs**.
- Quotas to remember: **10 NCCs/region, 100 PEs/region, 50 workspaces/NCC**.

### Plain Customer Explanation

> "Serverless has no static IP to allowlist, so the answer is never 'whitelist the
> IPs.' You create one NCC for the region, attach it to the workspace, add a private
> endpoint rule pointing at your storage, then approve that endpoint on your storage
> account. After that, serverless reaches your private data over a private path."

### What Breaks

- "Just whitelist the serverless IPs" - they're dynamic; it's NCC.
- Serverless query hangs/denied -> the NCC private endpoint rule is still PENDING
  (never Approved on the storage account), or wrong `dfs` vs `blob` group ID.

---

## Memory Hook

| Concept | Simple Question | Everyday Analogy |
| --- | --- | --- |
| Reference architecture | Which floor plan fits? | Pick a proven blueprint, don't design from scratch |
| Three doors | Which path is this? | Front door, staff intercom, loading dock |
| SCC | Any public IP / inbound? | Lock the doors, only call out |
| Private Link | Any public hop? | Use the private hallway, not the street |
| Service Endpoint | Free backbone door? | Trusted subnet, public FQDN |
| Private Endpoint | Private IP door (paid)? | Your own private mailbox for the service |
| NCC | How does serverless reach private data? | A regional bridge into the customer network |
| DEP (UDR + Firewall) | Can a notebook exfiltrate? | A guard inspecting everything that leaves |

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| SSO down for every workspace in a region | Was the shared `browser_authentication` host workspace deleted? Does `*.pl-auth...` resolve to the PE IP? |
| "Private" workspace URL resolves to a public IP | Does custom DNS conditional-forward `*.privatelink.azuredatabricks.net` to Azure DNS? |
| "Cluster failed to start: insufficient IP addresses" | Container subnet at the `2^(32-n) - 5` ceiling? CIDR is immutable - confirm before promising a fix. |
| Cluster can't read its own ADLS after firewall on | Is the subnet (SE) or PE in the storage allow rules with `default_action = Deny`? |
| Serverless query to private storage hangs/denied | Is the NCC private endpoint rule still PENDING (not Approved)? Right `dfs` vs `blob`? |
| Library install fails after adding the firewall | Are the Databricks FQDNs allowlisted? Is SCC/back-end-PL wrongly routed through the firewall? |
| Partner sees our egress IP "change" | Is a NAT Gateway attached for a stable egress IP? Pin one, give the SOC that. |
| IP access list "isn't blocking anything" | Is `enableIpAccessLists` actually `true`? Private-endpoint traffic is never IP-filtered. |
| New workspace has no outbound internet (post 2026-03-31) | New Azure VNets default to no outbound - needs an explicit NAT Gateway or firewall/UDR. |

---

## The Architect One-Liner

"We pick the closest of four references - Standard, Simplified, full-DEP, or the
IP-sizing layout - and adapt it. Every network question reduces to three paths:
user->workspace, compute->control, compute->data. So I trace the packet on each,
name the control, state the security-vs-cost-vs-complexity trade-off, default to
the cheapest control that meets your compliance language, and only escalate to
Private Link or a firewall where the regulation actually requires it."

---

## What To Remember

Do not start by memorizing FQDNs or quotas.

Start by asking:

1. Which of the four reference architectures is closest to this customer?
2. What is the lock on each of the three doors - user, control plane, storage?
3. For each door, what is the free floor control and the paid mandated upgrade?
4. Can I walk the packet and name what breaks at each hop?
5. Is this the *cheapest* control that still meets the compliance language?
6. Did I size the subnets for peak (CIDR is immutable)?
7. Is anything I'm quoting still Public Preview rather than GA?

If you can answer those seven, you can defend a design in a security review without
memorizing the settings.
