# Topic 6 - Security, Identity & Access, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: understand who is on the other end of a Databricks connection, how they
> prove it, and from where they may connect - without memorizing app IDs, sync
> intervals, or every Conditional Access toggle.

---

## The One Mental Model

Think of an Azure Databricks workspace like a **secured office building**.

- **Microsoft Entra ID** is the **central security desk** that issues badges (it is
  the single keymaker for the whole company).
- **SSO** is logging in once at the desk and getting a badge that opens many doors.
- **SCIM / automatic identity management** is the **HR feed** that automatically adds
  new badge-holders and removes people who leave.
- **A service principal** is a **robot's badge** - it lets automation in without a
  human standing there.
- **A token** is a **day-pass** a tool carries instead of the master badge (a PAT is
  a day-pass that never expires; OAuth is one that expires hourly).
- **Conditional Access** is the **badge reader at the door** - right badge, managed
  device, swiped from a trusted place.
- **IP access lists** are the **bouncer reading the return address** on every envelope.
- **Front-end Private Link** is a **private tunnel** that replaces the public door
  entirely - there is no public number left to dial.

Networking (Topics 1-4) decided *which paths* can reach the workspace. This topic
decides *who is on the path, how they prove it, and from where they connect*. What
they are then allowed to *do* (Unity Catalog grants) is the next topic.

The one sentence to remember:

> Define an identity once in Entra ID, prove it via SSO (humans) or short-lived OAuth
> (robots), then gate the door by where the request comes from.

---

## 1. Identity: Who Are You, and How Do You Prove It?

### Simple Explanation

**Authentication** is proving *who you are* (login). It is different from
**authorization**, which is *what you are allowed to do* (that is the next topic).

On Azure, the security desk is **Microsoft Entra ID** (the old name was "Azure AD" or
"AAD" - do not call it that in front of a customer). Azure Databricks logs in against
Entra ID by default. There is nothing to "turn on" for the Microsoft IdP.

### Databricks Meaning

- **Humans** log in with **Entra ID SSO** - one corporate login, no separate
  Databricks password.
- **Automation** (jobs, CI/CD, dbt) logs in as a **service principal** using
  **OAuth M2M** (a client ID + secret that returns a short-lived token).
- Identities are defined **once in the account**, then assigned to workspaces. That
  account-first model is called **identity federation**.

### Plain Customer Explanation

> "Identity is the front door. We make Microsoft Entra ID the single source of truth,
> run every production job as a service principal on short-lived OAuth, and cap or kill
> long-lived tokens - so off-boarding someone in your IdP is the only lever you need."

### What Breaks

- User logs in but has **no access / wrong groups** -> the workspace may be
  non-federated, or the group is a **workspace-local group** (not an account group),
  so it can't receive grants. First check: is the workspace identity-federated?
- **Duplicate users / conflicting permissions** -> the same identity is provisioned by
  **both SCIM and automatic identity management**. First check: pick one source.
- **Job suddenly fails to authenticate (401/403)** -> it ran as a named human who got
  off-boarded, or a PAT that expired. First check: the auth principal and token age.

---

## 2. Provisioning: How People Get Added and Removed

### Simple Explanation

You do not want to add and remove users in Databricks by hand. You want your HR / IdP
system to do it. Two ways to wire that feed:

- **Automatic identity management** - the newer, connector-free way. Databricks reads
  Entra ID directly. It also syncs **nested groups** and **service principals**.
- **SCIM provisioning** - the older standard. It *pushes* users and groups via a
  connector, but does **not** sync nested groups or service principals.

### Databricks Meaning

Centralizing on Entra ID means **off-boarding is a security event you only do once**:
deactivate in the IdP, and access propagates to Databricks automatically.

The honest caveat: **existing PATs are NOT auto-revoked** when a user is deactivated.
Revoke them explicitly.

### Memory Hook

| Question | Automatic identity management | SCIM (legacy) |
| --- | --- | --- |
| Sync users + groups? | Yes | Yes (direct members only) |
| Sync nested groups? | Yes | No |
| Sync service principals? | Yes | No |
| Needs an Entra app / admin role? | No | Yes |
| On by default? | Yes (accounts after 2025-08-01) | No |

### Plain Customer Explanation

> "When someone leaves, does access really disappear? Yes - if Entra ID is your source
> of truth via automatic identity management. The one caveat is existing personal access
> tokens; those we revoke explicitly."

### What Breaks

- **Don't mix the two** on the same identity -> duplicates and permission conflicts.
- **Cross-tenant Entra ID** is not supported by automatic identity management -> use
  SCIM with Entra B2B if you must span tenants.

---

## 3. Service Principals: Robots Should Not Be People

### Simple Explanation

A **service principal** is a non-human "robot user" for jobs, pipelines, and CI/CD, so
automation never runs as a named person.

### Databricks Meaning

- A **Databricks-managed SP** authenticates with **OAuth M2M** (client ID + secret) -
  recommended for Databricks-only automation.
- An **Entra ID SP** is an Entra app registration - use it when an Azure resource only
  speaks Entra ID tokens (e.g. Azure DevOps).
- Run **all production jobs as service principals**, and assign access via **groups**.

### Plain Customer Explanation

> "Automation should never be a person. If your production pipeline runs as Jane, the
> day Jane leaves, the pipeline dies. We run it as a robot identity that nobody offboards."

### What Breaks

- **An SP is missing / unusable** -> SPs **provision on first use**, so it must run at
  least once. First check: has the SP authenticated yet, and is automatic identity
  management on?

---

## 4. Tokens: Day-Passes (PAT vs OAuth)

### Simple Explanation

A token is the credential a tool presents instead of a password. Two kinds:

- **PAT (personal access token)** - legacy, long-lived. The classic leak vector.
- **OAuth (U2M / M2M)** - short-lived, auto-refreshed. The recommended path.

### Databricks Meaning

- PATs default to a **max 730-day** lifetime and auto-revoke after **90 days unused**.
  Set a shorter cap with `maxTokenLifetimeDays` (Databricks advises under 90 days).
- An admin can **disable PATs entirely** per workspace - but warning: **Partner Connect
  and some partner tools need PATs ON**.
- A PAT is **not an interactive sign-in**, so **Conditional Access cannot see it** -
  this is the single most-asked interview point.

### Memory Hook

| Credential | Lifetime | Use it when | Watch out |
| --- | --- | --- | --- |
| **PAT** | Max 730 days; revoked after 90d unused | Only when a tool supports nothing else | Leak vector; **bypasses Conditional Access** |
| **OAuth U2M** | Short-lived, auto-refresh | Attended CLI/SDK as yourself | - |
| **OAuth M2M** (SP) | Short-lived | Automation / jobs - recommended | - |

### Plain Customer Explanation

> "A PAT is a house key you cut once and never change - convenient until it is copied.
> OAuth M2M is a smart-lock code that expires hourly and is tied to one robot. Prefer
> the expiring code."

### What Breaks

- A request you expect Conditional Access to block gets through -> the client used a
  **PAT or pre-issued token**. First check: the auth method, not the CA policy.

---

## 5. IP Access Lists: The Bouncer Reading Return Addresses

### Simple Explanation

By default, a user reaches a workspace over the public internet from any IP, any
country. An **IP access list** is an allow/block list of source **IPv4** ranges
permitted to reach the UI/API.

### Databricks Meaning

- Two scopes: **account console** (account admins) and **workspace** (workspace admins).
- **BLOCK beats ALLOW.** When the feature is enabled but no lists exist, **all IPs are
  allowed** - the moment you add an ALLOW list, everything else is blocked.
- **IPv4 only.** No IPv6, no Azure Government, no Azure China.
- Databricks now recommends **context-based ingress control** (account-level, combines
  identity + request type + network source) as the primary method. A workspace IP list
  can only **narrow** it, never widen it - and a request must pass **both**.

### Plain Customer Explanation

> "This pins access to your corporate egress and VPN ranges. It is a bouncer checking the
> return address on every envelope. It is soft - IPs can be spoofed - but it is cheap and
> needs no network re-architecture."

### What Breaks (the #1 self-inflicted outage)

- **Classic clusters won't launch after enabling workspace IP access lists** -> under
  SCC, the compute plane's **egress public IP** also calls the control plane, and that IP
  is not on the ALLOW list. A path-① user control accidentally broke path ② compute.
  First check: is the **SCC/NAT egress IP** on the allow list?
- **Admins locked out** -> the ALLOW list omits your own current IP. First check: hit the
  REST API/CLI as account admin, or disable `enableIpAccessLists`.
- **Lists "do nothing"** -> `enableIpAccessLists` is still `false`, so lists are silently
  ignored.

---

## 6. Conditional Access: The Badge Reader

### Simple Explanation

**Microsoft Entra ID Conditional Access** is an identity-aware sign-in policy. It can
require **MFA**, a **compliant/managed device**, a **trusted named location**, or block
legacy auth.

### Databricks Meaning

- It is configured in **Entra ID**, not Databricks, targeting the **AzureDatabricks**
  enterprise app (`2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`).
- It is evaluated **at sign-in**, before a token is issued.
- It needs **Databricks Premium AND Entra ID Premium (P1/P2)**.
- Key limit (interview favorite): it gates **interactive Entra ID sign-ins only**. It
  does **not** gate a PAT or a pre-issued OAuth token. So if Conditional Access is your
  MFA/location gate, you must also lock down PATs.

### Plain Customer Explanation

> "We're rolling out MFA tenant-wide - does that cover Databricks? Yes, via the
> AzureDatabricks app, automatically. The one warning: PATs and pre-issued tokens are not
> interactive sign-ins, so they bypass it. That is why we also cap or disable PATs."

### What Breaks

- "Conditional Access isn't blocking a tool" -> the tool authenticated with a token, not
  an interactive sign-in. First check: the auth method.

---

## 7. Front-End Private Link: Removing the Public Door

### Simple Explanation

This is the strongest control. It gives the workspace a **private IP inside your VNet**
and (optionally) **disables public network access**, so there is no internet-facing front
door at all - an unlisted private phone line.

### Databricks Meaning

Two private-endpoint sub-resources, with exact names that matter:

- **`databricks_ui_api`** - the inbound user path: workspace UI, REST API, Databricks
  Connect.
- **`browser_authentication`** - the SSO/OAuth browser callback. Without it, the login
  redirect can't complete privately. Host it on a **dedicated, delete-locked web-auth
  workspace** so deleting a prod workspace doesn't break regional SSO.

Both integrate with the fixed Private DNS zone **`privatelink.azuredatabricks.net`**.
Requires **Premium + VNet injection**.

Two public-access models: **Disabled** (the hard, fully-private posture) or **Hybrid**
(Private Link active but public still on, gated by ingress controls).

### Plain Customer Explanation

> "Does any user traffic ever cross the public internet? Only front-end Private Link with
> Public Network Access = Disabled gives you a clean 'no.' We give the workspace a private
> address in your VNet and brick over the public door."

### What Breaks

- **SSO redirect fails after enabling front-end Private Link** -> the
  `browser_authentication` endpoint or its DNS forwarding for `*.pl-auth.azuredatabricks.net`
  is missing. First check: `nslookup` the regional SSO URL - a public IP or NXDOMAIN means
  the web-auth endpoint/DNS is missing, not `databricks_ui_api`.

---

## How These Pieces Fit Together

### The Story

1. An identity is defined **once in Entra ID** (the security desk).
2. It syncs into the **Databricks account** via automatic identity management.
3. It is assigned to **identity-federated workspaces**.
4. **Humans** prove it via SSO; **robots** prove it via OAuth M2M as a service principal.
5. At sign-in, **Conditional Access** checks MFA / device / location.
6. On the request, **IP access lists / context-based ingress** check the source IP.
7. **Front-end Private Link** can replace the public door entirely.

### The Architect One-Liner

> "Identity is the front door and network access controls are the layers around it.
> Microsoft Entra ID is the single keymaker. The workspace front door has a bouncer
> (IP access lists), a badge-reader (Conditional Access), and an optional private tunnel
> (front-end Private Link) that can brick the public door entirely. Get identity right
> first, then layer network location from cheap to hard."

---

## The Three Front-Door Controls at a Glance

| Control | What it checks | Strength | The catch |
| --- | --- | --- | --- |
| **IP access list** | Source IPv4 | Soft | IPv4 only; must allow the SCC/NAT egress IP |
| **Conditional Access** | Identity, MFA, device, location | Medium-strong | Interactive sign-in only - **PATs bypass it** |
| **Front-end Private Link** | Arrives on a private IP in your VNet | Strongest | Needs VNet injection + `browser_authentication` PE + DNS |

They **stack** - a mature deployment runs all three. You do not choose one.

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| User logs in but has no access | Is the workspace federated, and is the group an account group? |
| Duplicate users / conflicting permissions | Provisioned by both SCIM and automatic identity management? |
| Service principal invisible / unusable | Has it run once? SPs provision on first use. |
| Job fails to authenticate (401/403) | Named human off-boarded, or PAT expired? |
| Classic clusters won't launch after IP lists | Is the SCC/NAT egress IP on the allow list? |
| Admins locked out | Did the ALLOW list omit your own IP? |
| IP lists "do nothing" | Is `enableIpAccessLists` set to true? |
| Conditional Access isn't blocking a tool | Did the tool use a PAT / pre-issued token? |
| SSO redirect fails after Private Link | Is `browser_authentication` PE + `*.pl-auth` DNS in place? |

---

## What To Remember

Do not start by memorizing app IDs or sync intervals.

Start by asking, in order:

1. Where is identity defined? (Should be Entra ID, once, account-level.)
2. How are users provisioned and off-boarded? (Automatic identity management.)
3. What runs production jobs? (A service principal, not a person.)
4. What credential do tools use? (Short-lived OAuth, not a long-lived PAT.)
5. Is sign-in gated? (Conditional Access: MFA, device, location.)
6. Is the source network gated? (IP access lists / context-based ingress.)
7. Does the public door still exist? (Front-end Private Link can remove it.)

**Rule of thumb: identity first, then network location.** Get Entra ID and service
principals right before you touch any network control, then layer the controls from
cheap (Conditional Access) to soft (IP lists) to hard (front-end Private Link), only as
far as the regulatory bar demands.
