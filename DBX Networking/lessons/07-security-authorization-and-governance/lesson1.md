# Topic 7 - Security, Authorization & Governance, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: understand who can read which data, how Databricks proves who it is to
> your storage, and who can launch compute — without memorizing every privilege,
> JSON key, or RBAC role name.

---

## The One Mental Model

Think of governance as a **secured office building**, and the network you locked
down in earlier topics is just the **wall around it**.

- **Grants (7.1)** are the **keys**. To open a filing cabinet (a table) you need
  the floor key (`USE CATALOG`) *and* the room key (`USE SCHEMA`) *and* the
  cabinet key (`SELECT`) — all at once.
- **FGAC / ABAC (7.2)** is the **frosted glass and the bouncer inside the room**.
  Everyone has the same key, but *which rows you see and how clearly you see each
  value* is decided at query time based on who you are. **ABAC** installs that
  frosted glass *by a label* (`pii=ssn`) so it follows the data everywhere.
- **Storage credential / access connector (7.3)** is the **contractor's badge the
  building issues**. You never hand out a copy of your house key; you authorize a
  managed-identity badge at the front desk (RBAC on ADLS), point one door at it
  (the external location), and revoke it anytime.
- **Policy + access mode + ACL (7.4)** are **three locks on the compute door**:
  the policy is a *vending machine* an admin stocks (you can only pull the levers
  loaded, never above the price cap); the access mode is the *kind of room*
  (shared-but-isolated vs private) that decides whether UC will even trust it; the
  ACL is the *badge reader* on each object.

**The one sentence:** the network decides which packets reach storage; Unity
Catalog decides which principal reads which row; the access connector decides who
Databricks proves it is to ADLS; and policies/access modes/ACLs decide who can
launch the compute. Five locks on the same door — a regulated customer needs all
of them. A Private Endpoint stops the *path*; a missing `GRANT` stops the *query*.

---

## 1. Unity Catalog Grants: Who Can Do What to Which Object

### Simple Explanation

**Unity Catalog (UC)** is the one place, at the account level, where you say "who
can do what to which object." Data lives in a three-level name:
`catalog.schema.object` (a table, view, volume, function, or model).

You control access with two SQL words: **`GRANT`** (give a privilege) and
**`REVOKE`** (take it away). The thing you grant to is a **principal** — a user, a
group, or a service principal.

### Databricks Meaning

The trick most people miss: usage privileges are **gates, not access**. To read
`prod.sales.orders` a user needs **three grants together**:

- `USE CATALOG` on `prod` (enter the floor)
- `USE SCHEMA` on `prod.sales` (enter the room)
- `SELECT` on the table (read the cabinet)

Holding only `SELECT` gets you nowhere. Also remember **inheritance**: a grant on
a catalog or schema cascades to all current *and future* child objects — powerful,
and a footgun, so grant at the narrowest level that works. (Metastore-level grants
are the exception: they do **not** inherit.)

### Plain Customer Explanation

> "Unity Catalog is your single account-level authorization layer. To read a table
> a user needs three things together: permission to enter the catalog, permission
> to enter the schema, and permission to read the table. If you grant `SELECT`
> alone, it still won't work — that missing 'enter the floor' key is the number-one
> reason a grant looks broken."

### What Breaks

- "I granted `SELECT` but they still can't read." → almost always a **missing
  `USE CATALOG`/`USE SCHEMA` gate**, or the catalog isn't **bound** to their
  workspace, or a **view** references a base table the view owner can't read.
- Read works in one workspace, denied in another → the catalog is `ISOLATED` and
  **not bound** to that workspace. **Binding supersedes grants.**
- A cross-workspace grant silently does nothing → it was granted to the
  workspace-local `workspace admins` group instead of an **account group**.

### Where networking meets governance

**Workspace-catalog binding** pins a *data domain* (catalog) to a *processing
environment* (workspace), so prod data is unreachable from a dev workspace **even
with a valid `SELECT`**. This is how you prove environment isolation to auditors.

---

## 2. ABAC, Row Filters & Column Masks: What Bytes Come Back

### Simple Explanation

Once a user has `SELECT`, by default they see **every row and every column** —
often too much. **Fine-grained access control (FGAC)** narrows what `SELECT`
returns at query time, transparently. Three tools, oldest to most scalable:

- **Dynamic views** — a view that filters rows or masks columns based on who's
  asking (`is_account_group_member('grp')`). The "redacted photocopy." Only one
  that can reshape/join data.
- **Table-level row filters & column masks** — attach a **UDF** directly to the
  table. The base table itself enforces it. The "tinted window on the table."
- **ABAC policies** — define a **governed tag** (`pii=ssn`), tag the sensitive
  columns, then write **one policy** that auto-masks every object carrying that
  tag, now and in future. "Label it once, the rule follows the label everywhere."

### Databricks Meaning

The key insight: **a filter or mask is just a UDF with an identity check inside.**
Once you see that, dynamic views, table-level bindings, and ABAC policies are the
*same idea attached in three different places* — the view, the table, or the tag.
The identity functions (`is_account_group_member()`, `session_user()`) run as the
**invoker** — the person running the query — which is what makes per-user logic
work. ABAC for row filters/column masks is **generally available on Azure** (verify
the current GA date and region before quoting).

### Plain Customer Explanation

> "Yes, we can mask PII for everyone except a named clearance group and prove it to
> auditors. We tag the sensitive columns once with a governed tag, write one policy
> at the schema level, and every tagged column — including new tables — is masked
> server-side at query time. The clearance group goes in the policy's `EXCEPT`
> list, and every access decision is in the `system.access` audit tables."

### What Breaks

- Query returns no data after enabling FGAC → the **compute floor**: ABAC needs
  serverless or DBR >= 16.4; table-level needs DBR >= 12.2 LTS (15.4 for dedicated).
  It **fails secure** (no data), not loud.
- Mask never applies / filter returns everything → used **`is_member()`**
  (workspace-local) instead of **`is_account_group_member()`** (account); or ANSI
  is off so a type mismatch silently became `NULL`.
- Pipeline refresh / time travel / clone fails → the run-as identity isn't in the
  policy's `EXCEPT`. Exempt the trusted ETL service principal.

### Memory hook: which FGAC mechanism?

| | Dynamic views | Table-level filter/mask | ABAC policies |
| --- | --- | --- | --- |
| Scales to many tables | No | No | **Yes — one policy, by tag** |
| Reshapes / joins data | **Yes** | No | No |
| Users query | The view | The base table | The base table |
| Best for | A redacted layer | One-off table logic | Many tables + central governance |

---

## 3. Storage Credentials, External Locations & the Access Connector

### Simple Explanation

This is the bridge between UC's *logical* permission model and the *physical* ADLS
Gen2 that holds the bytes. Three objects, learned as a chain:

- **Access Connector for Azure Databricks** — a first-party Azure resource holding
  an **Azure managed identity**. This is the identity Databricks authenticates *as*
  when it reaches your storage. **No keys, no secrets, nothing to rotate.**
- **Storage credential** — a UC object that **wraps the access connector's
  resource ID**. UC's record of "here is an auth mechanism I'm allowed to use."
- **External location** — a UC object that binds a **path**
  (`abfss://...`) to a storage credential. It's the thing you actually `GRANT` on.

**The one-line chain:** external location (path + grants) → storage credential
(which identity) → access connector (the managed identity) → RBAC on ADLS Gen2 —
and there is **no key** to copy or rotate anywhere on that chain.

### Databricks Meaning

When a governed job needs data, Databricks authenticates **as the managed
identity** to Microsoft Entra ID, gets a **short-lived OAuth token**, and presents
it to ADLS. ADLS checks that identity against its **RBAC role assignments**
(e.g. `Storage Blob Data Contributor`) and allows or denies. Why managed identity
over a service principal or storage key? No secret to rotate, **and** it works
through a storage firewall — a managed identity can be allowlisted as a trusted
**Resource instance** on ADLS; a service principal cannot. This is the deciding
factor for locked-down storage. Network reachability (Service/Private Endpoint,
NCC) and this credential chain must **both** succeed for a read.

### Plain Customer Explanation

> "There is no storage key, and nothing to rotate. We create an access connector
> that holds a managed identity, grant that identity an RBAC role on your ADLS
> account, and Databricks gets a short-lived token each time. It's also the one
> mechanism that works while your storage is at 'no public access' — the managed
> identity is allowlisted as a trusted resource, which a service principal can't be."

### What Breaks

- **Triage rule — split identity from network first.** Catalog Explorer → external
  location → **Test connection**. Fails on *authorization* → RBAC/identity half.
  Fails on *reachability/timeout* → network half (firewall / private endpoint /
  DNS). Don't chase RBAC for a firewall block.
- Reads fail 403 though the credential validates → RBAC role is on the **wrong
  identity** (the workspace managed-RG identity, not the access connector's).
- Validation passes, first read fails → **HNS not enabled** on the storage account.
- Dev workspace can reach prod storage → the credential/external location is
  **unbound** (metastore-wide).

---

## 4. Cluster Policies & Access Modes: Who Launches Compute

### Simple Explanation

Three distinct controls that decide **who can spin up compute, what that compute
can be, and who can touch the objects around it**. Keep them separate:

- **Cluster (compute) policy** — a template + rulebook an admin writes that
  constrains what a user can configure (node type, max workers, runtime, and a
  hard cap on $/hour via **DBUs**). No policy access → can't create compute.
  *A corporate travel tool that only shows economy flights under a price cap.*
- **Access mode** — a security property of each compute resource: **Standard**
  (`USER_ISOLATION`, multi-user, isolated) or **Dedicated** (`SINGLE_USER`, one
  user or group). UC requires one of these. *Shared hot-desk with locked drawers
  vs a private office.*
- **Workspace-object ACL** — a permission list on each object (cluster, job, secret
  scope, notebook) saying who gets which verbs. *The per-door badge reader.*

### Databricks Meaning

The policy is **JSON** mapping an attribute to a limitation (`fixed`, `range`,
`allowlist`, etc.). The two levers that matter most: **`dbus_per_hour`** (a range
with `maxValue` — your cost cap) and **`data_security_mode`** fixed to
`USER_ISOLATION`/`SINGLE_USER` (how you *force* UC compliance). Only **Standard**
and **Dedicated** support Unity Catalog — legacy "No Isolation Shared"/"Custom"
modes are not UC-compliant. Note the names: the UI says **Standard** (was "Shared")
and **Dedicated** (was "Single user"), but REST/Terraform still use
`USER_ISOLATION` and `SINGLE_USER`. Know both.

### Plain Customer Explanation

> "To stop a six-figure GPU cluster, we fix the node types and put a hard
> `dbus_per_hour` cap in a cluster policy, then grant CAN USE narrowly. To
> guarantee every cluster uses Unity Catalog, we fix the access mode to Standard
> or Dedicated in that same policy. The two are separate controls — one bounds
> cost, the other forces governance — and ACLs decide who can attach to or manage
> each object."

### What Breaks

- "The policy isn't in my dropdown / I can't create a cluster." → no **CAN USE** on
  the policy, or `cluster_type` doesn't allow `all-purpose`.
- "UC data is inaccessible from this cluster." → it's a legacy access mode, not
  UC-compliant by design. Check `data_security_mode`.
- "We edited the policy but clusters still run the old config." → policy edits
  don't auto-restart running compute. Check the **Compliance** column.
- "A low-priv analyst ran a job as a high-priv identity." → **Run Now executes as
  the job owner**, not the clicker.
- "A secret got exposed." → **READ on a secret scope reveals every secret in it.**

### Memory hook: Standard vs Dedicated

| Dimension | **Standard** (`USER_ISOLATION`) | **Dedicated** (`SINGLE_USER`) |
| --- | --- | --- |
| Shared by | Many users, isolated | One user or one group |
| Unity Catalog | Yes | Yes |
| ML / GPU / R / RDD | No | Yes |
| Best for | Default, BI, collaborative | ML, R, single-team |

---

## How These Pieces Fit Together for Azure Databricks

### The Story

1. The network (Topics 2-6) decides which packets can reach storage.
2. UC grants decide which principal can do what to which object.
3. FGAC / ABAC decide which rows and column values come back at query time.
4. The access connector decides who Databricks proves it is to ADLS — keylessly.
5. Cluster policies, access modes, and ACLs decide who launches compute and what
   it can be.
6. Binding pins a data domain to an environment so dev can't touch prod.

### The Architect One-Liner

"Governance rides on top of the network you already secured: the network decides
which packets reach storage, Unity Catalog decides which principal reads which row,
row filters and ABAC decide what bytes come back, the access connector is the
keyless identity that reaches ADLS through a locked firewall, and cluster policies
plus access modes plus ACLs are the cost-and-compliance guardrail on the compute —
five locks on the same door, and a regulated customer needs all of them."

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| Granted `SELECT`, still can't read | Are `USE CATALOG`/`USE SCHEMA` gates missing? |
| Read works one workspace, denied in another | Is the catalog ISOLATED and unbound there? |
| FGAC returns no data after enabling | Is the compute below the DBR floor? (fails secure) |
| Mask never applies | Used `is_member()` instead of `is_account_group_member()`? |
| Storage read fails | Run Test connection — split identity (RBAC/HNS) from network |
| 403 though credential validates | Is RBAC on the connector identity, not the managed-RG one? |
| Dev workspace reaches prod storage | Is the credential/external location unbound? |
| Policy edited but clusters over budget | Check the Compliance column; enforce on restart |
| Job ran as a high-priv identity | Run Now runs as the job owner — check the owner |
| Secret got exposed | READ on a secret scope reveals the whole scope |

---

## What To Remember

Do not start by memorizing privileges or JSON keys.

Start by asking:

1. Can this principal even enter the catalog and schema (the three-grant gate)?
2. Once in, which rows and column values should come back for *this* user?
3. Is the catalog bound to the workspace the user is in?
4. How does Databricks prove who it is to ADLS — is RBAC on the right identity?
5. Is the storage read failing on identity or on network? (Test connection first.)
6. Who is allowed to launch compute, and is its cost capped?
7. Is the access mode UC-compliant, and who can touch each object via ACLs?

If you can answer those seven questions, the detailed grants, policies, and RBAC
roles become much easier to reason about.
