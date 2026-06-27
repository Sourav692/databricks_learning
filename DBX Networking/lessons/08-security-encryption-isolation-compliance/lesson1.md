# Topic 8 - Security: Encryption, Isolation & Compliance, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: be able to defend the data-protection story to a customer's security and
> compliance team without memorizing every CMK flag, service tag, or audit column.

---

## The One Mental Model

Earlier topics locked the **doors and hallways** (network paths, SCC, Private Link,
NCC, firewalls). This topic protects **what's inside the rooms** and **installs the
cameras**.

Think of a secure, certified hotel:

- **Encryption (8.1)** = the contents travel in *sealed envelopes* (TLS, in motion)
  and sit in *locked safes* (at rest). **CMK** does not add a second safe. It just
  swaps *whose key* opens the existing one, from Microsoft's to **yours in Azure
  Key Vault**, so you can rotate or revoke it.
- **Isolation (8.2)** = a *hotel where the master key does not exist*. Each
  serverless workload gets a private room and a keycard that opens **only that room
  for one hour**, and the room is wiped at checkout.
- **Compliance (8.3)** = *Russian dolls in a certified building*: `CSP > ESM >
  hardened image`, paid for by the **ESC add-on**. Turning CSP on is a **one-way
  door**.
- **Audit / monitoring (8.4)** = the *CCTV tape plus a central security desk*. **One
  audit stream, two taps**: system tables inside the platform, and Azure diagnostic
  export out to your SIEM.

The one sentence: *Encryption is on by default and CMK only changes who holds the
key; serverless isolation is proven by architecture, not your perimeter; CSP is the
permanent certified posture you scope to regulated workspaces; and you prove all of
it with system tables plus a SIEM export.*

None of these is a fourth network path. They ride on, harden, or watch the three
paths you already know (user to Databricks, compute to control, compute to storage).

---

## 1. Encryption: TLS, CMK & Azure Key Vault

### Simple Explanation

Two different jobs:

- **In transit** scrambles data *while it moves* so a wire-tap sees gibberish.
  Azure Databricks uses **TLS 1.2+** on every hop. Always on, no toggle.
- **At rest** scrambles data *while it sits on disk* so a stolen disk is useless.
  Azure Storage does this automatically with AES-256.

A **key** is the secret that locks and unlocks the at-rest encryption. By default
*Microsoft* holds it. With a **Customer-Managed Key (CMK)** *you* hold it in **Azure
Key Vault**, so you can audit, rotate, or **revoke** it. Revoke it and the data
becomes unreadable, even to Databricks.

### Databricks Meaning

There are **three independent CMK features**, each protecting a different place.
Turning one on does not turn the others on.

| CMK feature | Protects | Where it lives | Compute |
| --- | --- | --- | --- |
| **Managed services** | Notebooks, results, secrets, SQL history, dashboards | Control plane | Classic + serverless |
| **DBFS root** | Workspace system data, job/SQL results, MLflow, revisions | Workspace storage account (your subscription) | Classic + serverless |
| **Managed disks** | Temp / shuffle / spill on VM disks | Compute plane VMs (your subscription) | **Classic only** |

All three need **Premium** plus **Azure Key Vault** in the **same tenant**.

The key never leaves the vault: Databricks generates a data-encryption key (DEK) and
asks Key Vault to **wrap/unwrap** it with your key-encryption key (KEK). That is why
you grant only **Get + Wrap Key + Unwrap Key** (the `Key Vault Crypto Service
Encryption User` role).

### Plain Customer Explanation

"Everything is encrypted in transit with TLS and at rest with AES-256 already, for
free. CMK does not add a second layer of encryption. It hands *you* the key, in your
own Azure Key Vault, so you can rotate it or revoke it as a kill switch. But note: it
covers control-plane data, the workspace root, and VM disks. It does **not** cover
your ADLS Gen2 data lake. You set CMK on that storage account directly."

### What Breaks

- **Login or reads fail after a key change** - the Key Vault key was disabled or
  expired, or the `AzureDatabricks` app / storage identity lost the wrap/unwrap role.
  Databricks can no longer unwrap the DEK.
- **CMK rejected at deploy** - you used `latest` as the key version, a non-RSA key,
  or a non-Premium / cross-tenant vault.
- **Auditor: "the lake isn't customer-keyed"** - CMK was set on the workspace, not on
  the ADLS Gen2 storage account.
- **Worker-to-worker shuffle is unencrypted by default** - close that gap with **Azure
  VNet encryption** (cleaner) or an init-script setting AES over TLS 1.3.

> Big gotchas: managed-services CMK is **irreversible**, double encryption is
> **deploy-time only**, and losing the key with no purge protection **permanently
> bricks** the workspace.

---

## 2. Compute Security & Isolation

### Simple Explanation

**Isolation** is the guarantee that one running workload cannot see, touch, or
impersonate another - another customer, another user, or even Databricks code.

The hard case is **serverless**: it runs in the **Databricks-managed serverless
plane** (Databricks' Azure account), not your VNet. So isolation cannot rely on
*your* network boundary. It is built into the platform.

The key idea is the **ephemeral, scoped credential**: instead of a long-lived secret
on the cluster, each workload gets a **short-lived (~1-hour) token scoped to exactly
that workload's data**, used to talk **directly to your storage**.

### Databricks Meaning

- **Between tenants:** dedicated compute per workload (never shared across customers),
  **compute + disks wiped on completion** (no warm pool), private network with **no
  public IPs**, fresh image with **no customer credentials** baked in.
- **The token:** Unity Catalog is the broker. A **storage credential** wraps an
  **Access Connector** (managed identity) granted **Storage Blob Data Contributor**;
  an **external location** binds it to a path; at query time UC checks the caller's
  `READ`/`WRITE` grant, then **vends a down-scoped, time-bound token** straight to the
  workload. The control plane mints it but never proxies the bytes.
- **Inside a workspace:** **Lakeguard** isolates users on **shared** compute (Spark
  Connect decouples client from driver, container sandbox, UDF isolation). This is
  *why* shared/serverless compute can be UC-compliant.
- **Access modes:** **Standard** (was Shared) = multi-user, Lakeguard-isolated;
  **Dedicated** (was Single user) = isolation by not sharing; **Serverless** =
  Lakeguard + platform isolation.

### Plain Customer Explanation

"If serverless runs in Databricks' account, what stops another tenant - or Databricks
- from reading our data? The compute is dedicated per workload and wiped on
completion, there is no warm pool, the network has no public IPs, and Unity Catalog
vends a one-hour, path-scoped token used directly to your ADLS. A stolen keycard buys
one room for one hour, nothing more."

### What Breaks

- **Serverless 403 to firewalled storage** - the storage firewall uses a stale
  subnet-ID allowlist instead of the regional **`AzureDatabricksServerless.<region>`**
  service tag, or the NSP is in the wrong mode.
- **Permission denied reading a path** - the caller lacks `READ`/`WRITE` on the UC
  securable, or the Access Connector lacks **Storage Blob Data Contributor**.
- **Multi-user job sees another user's data** - compute is **Dedicated misused** or a
  legacy non-UC cluster, not **Standard** (Lakeguard).
- **Whole storage firewall broke other services** - someone flipped the NSP from
  **Transition** to **Enforced**.

> Deadline gotcha: by **2026-06-09**, storage accounts that allowlisted serverless
> **subnet IDs** must move to an **NSP** plus the **`AzureDatabricksServerless`**
> service tag.

---

## 3. Compliance: ESC, ESM & CSP

### Simple Explanation

Three layers regulated customers conflate, nested like Russian dolls:

- **ESC (Enhanced Security & Compliance add-on)** = the **bill**. A paid add-on on top
  of Premium. Enabling ESM or CSP on *any* workspace activates the charge.
- **ESM (Enhanced Security Monitoring)** = the **cameras**. A CIS Level 1 hardened
  image plus three monitoring agents (file-integrity, antivirus, vuln-scan) on the
  **classic compute plane only**.
- **CSP (Compliance Security Profile)** = the **certified building**. Bundles ESM
  *plus* automatic cluster update, TLS 1.2+ everywhere, and lets you attach
  **standards** (HIPAA, PCI-DSS, IRAP, and more).

The nesting: `CSP > ESM > hardened image`.

### Databricks Meaning

- ESC is not a direct toggle; enabling ESM/CSP triggers the charge.
- ESM ships a hardened image and three agents you **cannot disable** (logs go to
  `capsule8` and `clamAV` rows; review is **your** job). **Arm64 and Gen2 VMs are
  unsupported.**
- CSP adds automatic cluster update (permanent), TLS 1.2+ to the metastore, and a
  **restricted feature surface** (GA + named-preview allow-list only). Terraform
  `azurerm` accepts only `HIPAA, PCI_DSS, NONE`; other standards need Portal/CLI/PS/ARM.
- **Port 2443:** on a restricted-egress VNet, CSP **requires outbound TCP 2443** on top
  of normal egress.

### Plain Customer Explanation

"Are you HIPAA/PCI/IRAP? The platform holds the attestations, but to *process*
regulated data you turn on the Compliance Security Profile with the matching standard
**on that workspace**. That hardens the classic compute plane permanently - it is a
one-way door - so we scope it to the workspaces that actually touch regulated data.
ESC is the bill, ESM is the cameras, CSP is the certified building."

### What Breaks

- **CSP compute won't start on a locked-down VNet** - missing the outbound **TCP 2443**
  rule, or Azure VNet encryption off / unsupported VM type.
- **Cluster won't launch on CSP/ESM** - the VM is **Gen2 or Arm64** (both blocked).
- **"Works in dev, blocked in the regulated workspace"** - the feature is a preview not
  on the CSP allow-list.
- **Customer wants to turn CSP off** - there is no toggle; the only path back is
  **delete + rebuild** the workspace.

> Permanence is the headline: CSP + a standard is effectively forever, and ESM is
> classic-only. HIPAA/HITRUST/IRAP become **CSP-mandatory on 2026-09-01**.

---

## 4. Audit Logs, System Tables & Monitoring

### Simple Explanation

An **audit log** is an immutable record of *actions* (login, GRANT, cluster start,
failed permission check) - "who did what, to what, when, from where."

Two ways to read it:

- **System tables** = the tap *inside*. UC-governed Delta tables; the audit log lives
  at **`system.access.audit`**. Queried in SQL. The **only** tap carrying
  **account-level events** (`workspace_id = 0`).
- **Azure diagnostic settings** = the tap *out* to your SIEM (Log Analytics, Storage,
  or Event Hub). **Workspace-level only.**

### Databricks Meaning

- **One stream, two taps.** Account-admin, account-SCIM, and metastore-grant events
  appear **only** in the system table. A SIEM-only strategy has a blind spot. Best
  practice is **both**.
- **Reading rule:** `service_name` + `action_name` identify the event;
  **`response.statusCode != 200`** is your denied/failed filter.
- **Latency:** system tables are **not real-time** (~15 min to hours). For sub-minute
  detection, use Event Hub to Sentinel.
- **Monitoring** = Databricks SQL alerts: a scheduled query that notifies when a
  condition fires.

### Plain Customer Explanation

"Where's your audit trail, and can you feed our SIEM? Both. You query the trail in SQL
through system tables, and you export it natively to Sentinel or Splunk through
diagnostic settings. Run both, because account-level events live **only** in the
system table - the SIEM export is workspace-level."

### What Breaks

- **Query/alert fails "too much data"** - no `event_date` predicate; system tables
  reject broad scans.
- **SIEM missing account-admin / metastore-grant events** - those are `ACCOUNT_LEVEL`
  (`workspace_id = 0`) and never flow to diagnostic settings.
- **Analyst can't see `system.access.audit`** - missing UC grants
  (`USE CATALOG` / `USE SCHEMA` / `SELECT`), not a workspace permission.
- **New event category missing from Log Analytics** - a hard-coded category list
  missed one Databricks added; use a dynamic list.

---

## Memory Hook: The Four Subtopics

| Subtopic | Hotel analogy | The one thing to remember |
| --- | --- | --- |
| 8.1 Encryption | Sealed envelopes + locked safes | CMK changes *who holds the key*, not whether it's encrypted |
| 8.2 Isolation | Hotel with no master key | One-hour, path-scoped token used directly to your storage |
| 8.3 Compliance | Certified building, one-way door | CSP is permanent; scope it to regulated workspaces |
| 8.4 Audit | CCTV tape + security desk | One stream, two taps; account-level lives only in system tables |

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| Login/reads fail after a key change | Is the Key Vault key enabled and does the `AzureDatabricks` app still hold wrap/unwrap? |
| Auditor says the data lake isn't customer-keyed | CMK is on the workspace, not on the ADLS account itself |
| Serverless 403 to firewalled storage | Regional `AzureDatabricksServerless.<region>` tag, NSP in Transition (not a stale subnet-ID rule) |
| Multi-user job sees another user's data | Is the access mode Standard (Lakeguard), not Dedicated-misused/legacy? |
| CSP compute won't start on a locked-down VNet | Outbound TCP 2443 rule (+ Azure VNet encryption / supported VM type) |
| Cluster won't launch on CSP/ESM | Is the VM Gen2 or Arm64? Both are blocked |
| SIEM missing account-admin events | They're `ACCOUNT_LEVEL` (`workspace_id = 0`); query the system table |
| "Too much data" query error | Add an `event_date` predicate |

---

## The Architect One-Liner

"Encryption and TLS are on by default and answer 'is it encrypted?' for free - CMK
just hands you key custody and a kill switch in your own Key Vault. Serverless proves
isolation by architecture, not your perimeter: dedicated compute wiped between
workloads plus short-lived, path-scoped tokens used directly to your ADLS. For
regulated data we turn on the Compliance Security Profile with your standard - it's
the certified posture, and it's permanent, so we scope it. And you prove all of it two
ways: query the audit trail in SQL via system tables, and export it to your SIEM - run
both, because account-level events live only in the system table."

---

## What To Remember

Do not start by memorizing CMK flags or audit columns.

Start by asking:

1. Is it encrypted, and who holds the key?
2. Does the customer need to revoke or rotate that key themselves?
3. Does CMK actually cover the data they care about, or just the workspace root?
4. How does serverless prove isolation without my network boundary?
5. Does this workload truly need regulated certification (CSP), or just a hardened posture?
6. Is CSP scoped only to the workspaces that touch regulated data, given it's permanent?
7. Can I both query the audit trail in SQL and feed the SIEM, with account-level events covered?

If you can answer those seven questions, every CMK flag, service tag, and audit
column becomes much easier to place.
