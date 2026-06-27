# Azure Databricks Networking & Security — Topic Tracker (consolidated)

> **One consolidated page per topic.** The library was restructured from ~45
> per-subtopic files into **12 topic pages** (one `lesson.md` + one `index.html` +
> one `architecture.svg` per module), built for a **Field Engineer / Resident
> Solutions Architect (FDE / RSA)** audience: explain *what's happening and why*,
> reason about *why an issue occurs*, and defend a recommendation to a customer's
> security team — at architect altitude, not operator-grade build depth.
>
> Each page is a tabbed **"architect view"** (modelled on `zerobus-ingest-architect-view.html`):
> subtopics as tabs, Databricks branding, mental-model + callouts, decision cards,
> customer Q&A, and **one interactive architecture diagram per subtopic** (data-driven
> SVG — click a node for the *what + why*, switchable views). Built by
> **databricks-netsec-tutor** and signed off by **databricks-netsec-reviewer**
> (Check 1 terminology + Check 2 compliance, fix-loop to ✅ Approved).

## How to read a row
- **Subtopics** — the sections/tabs consolidated into the one topic page.
- **Artifacts** — `md` + `html` + `svg` at the module root, plus 🛠 hands-on IaC/SQL where present.
- **Dgms** — count of interactive architecture diagrams (one per subtopic).
- **Status** — reviewer verdict for the consolidated page.

**Progress: 12 / 12 topics ✅ Approved.** 🎉 Full track consolidated, architect-altitude, reviewer-approved.

| # | Topic | Subtopics covered | Artifacts | Dgms | Status |
|---|-------|-------------------|-----------|------|--------|
| 1 | **Networking & Cloud Foundations** | IP/CIDR · VNets & subnets · firewalls/NSGs/routing · DNS & endpoints · topologies | [md](../lessons/01-networking-and-cloud-foundations/lesson.md) · [html](../lessons/01-networking-and-cloud-foundations/index.html) · [svg](../lessons/01-networking-and-cloud-foundations/architecture.svg) · [🛠 main.tf](../lessons/01-networking-and-cloud-foundations/main.tf) | 5 | ✅ |
| 2 | **Architecture & Default Connectivity** | control vs compute plane · three connectivity paths · deployment & workspace storage | [md](../lessons/02-architecture-and-default-connectivity/lesson.md) · [html](../lessons/02-architecture-and-default-connectivity/index.html) · [svg](../lessons/02-architecture-and-default-connectivity/architecture.svg) | 3 | ✅ |
| 3 | **Classic Compute Plane Networking** | default VNet vs injection · subnets & sizing · SCC/No Public IP · egress (NSG/UDR/NAT) · compute→storage endpoints | [md](../lessons/03-classic-compute-plane-networking/lesson.md) · [html](../lessons/03-classic-compute-plane-networking/index.html) · [svg](../lessons/03-classic-compute-plane-networking/architecture.svg) | 5 | ✅ |
| 4 | **Private Link, DNS & Topologies** | Private Link connection types · private DNS · transit/hub-and-spoke · data exfiltration protection | [md](../lessons/04-private-link-dns-and-topologies/lesson.md) · [html](../lessons/04-private-link-dns-and-topologies/index.html) · [svg](../lessons/04-private-link-dns-and-topologies/architecture.svg) · [🛠 main.tf](../lessons/04-private-link-dns-and-topologies/main.tf) | 4 | ✅ |
| 5 | **Serverless Networking** | serverless architecture · NCC · egress control / network policies · storage access patterns | [md](../lessons/05-serverless-networking/lesson.md) · [html](../lessons/05-serverless-networking/index.html) · [svg](../lessons/05-serverless-networking/architecture.svg) · [🛠 main.tf](../lessons/05-serverless-networking/main.tf) | 4 | ✅ |
| 6 | **Security: Identity & Access** | identity & authentication · network access controls for users | [md](../lessons/06-security-identity-and-access/lesson.md) · [html](../lessons/06-security-identity-and-access/index.html) · [svg](../lessons/06-security-identity-and-access/architecture.svg) | 2 | ✅ |
| 7 | **Security: Authorization & Governance** | UC hierarchy & grants · ABAC/row filters/column masks · storage creds & external locations · cluster policies & access modes | [md](../lessons/07-security-authorization-and-governance/lesson.md) · [html](../lessons/07-security-authorization-and-governance/index.html) · [svg](../lessons/07-security-authorization-and-governance/architecture.svg) · [🛠 main.tf](../lessons/07-security-authorization-and-governance/main.tf) · [🛠 sql](../lessons/07-security-authorization-and-governance/governance_demo.sql) | 4 | ✅ |
| 8 | **Security: Encryption, Isolation & Compliance** | encryption (TLS/CMK) · compute security & isolation · compliance (ESC/ESM/CSP) · audit logs & system tables | [md](../lessons/08-security-encryption-isolation-compliance/lesson.md) · [html](../lessons/08-security-encryption-isolation-compliance/index.html) · [svg](../lessons/08-security-encryption-isolation-compliance/architecture.svg) · [🛠 sql](../lessons/08-security-encryption-isolation-compliance/audit_monitoring.sql) | 4 | ✅ |
| 9 | **Synthesis, Best Practices & Interview** | reference architectures end-to-end · networking/security best-practices checklist · customer & interview scenarios | [md](../lessons/09-synthesis-best-practices-interview/lesson.md) · [html](../lessons/09-synthesis-best-practices-interview/index.html) · [svg](../lessons/09-synthesis-best-practices-interview/architecture.svg) · [🛠 ref-arch](../lessons/09-synthesis-best-practices-interview/reference-architecture/main.tf) · [🛠 checklist](../lessons/09-synthesis-best-practices-interview/hardening-checklist/checklist.tf) | 3 | ✅ |
| 10 | **FDE Field Playbooks** | troubleshooting compute/networking startup · connectivity · storage/serverless access · deployment patterns · diagnostics & escalation | [md](../lessons/10-fde-field-playbooks/lesson.md) · [html](../lessons/10-fde-field-playbooks/index.html) · [svg](../lessons/10-fde-field-playbooks/architecture.svg) | 5 | ✅ |
| 11 | **FDE Interview Prep Capstone** | whiteboard scenarios · rapid-fire Q&A bank · defending trade-offs & cost | [md](../lessons/11-fde-interview-prep/lesson.md) · [html](../lessons/11-fde-interview-prep/index.html) · [svg](../lessons/11-fde-interview-prep/architecture.svg) | 3 | ✅ |
| 12 | **Customer-Facing Collateral** | sizing & architecture decision guide · security/networking overview · deployment & hardening checklist | [md](../lessons/12-customer-collateral/lesson.md) · [html](../lessons/12-customer-collateral/index.html) · [svg](../lessons/12-customer-collateral/architecture.svg) | 3 | ✅ |

## Hands-on artifacts (Terraform / SQL)

Illustrative config lives **inline** in each lesson; full apply-ready artifacts sit alongside the topic page:

| Topic | Artifact | What it does |
|---|---|---|
| 1 | `main.tf` | Foundational Azure networking primitives (VNet, subnets, NSG, UDR, NAT Gateway, Private DNS). |
| 4 | `main.tf` | Private Link / Private DNS deployment. |
| 5 | `main.tf` | Serverless NCC + private endpoint rules + workspace binding (account-scoped `databricks` provider). |
| 7 | `main.tf` · `governance_demo.sql` | Cluster policy + ACLs; Unity Catalog grants / ABAC / masking demo. |
| 8 | `audit_monitoring.sql` | Audit-log / `system.access` monitoring queries. |
| 9 | `reference-architecture/main.tf` · `hardening-checklist/checklist.tf` | End-to-end secure baseline (VNet injection + SCC + back-end Private Link + storage PE + NAT); hardening guardrails as code (storage default-deny, audit logging, CMK reference). |

> All Terraform validated with `tofu validate` (azurerm/databricks providers) and `tofu fmt`. Files are variable-driven, realistic CIDRs, commented, with cost caveats; no running compute is created by default. Topic 9's two artifacts are separate sub-module folders (each a standalone `terraform`-block module).

## Notes
- **Structure:** the old per-subtopic folders (e.g. `1.1-…/`, `2.1-…/`) were removed during consolidation; content is merged into the single topic `lesson.md` and the tabbed `index.html`. One page per topic — no per-subtopic files.
- **Skill spec:** authored to **databricks-netsec-tutor v1.2** (architect altitude, one page per topic, interactive architecture diagram per subtopic, illustrative config) and reviewed by **databricks-netsec-reviewer v1.1**.
- **Currency:** Azure-first; current naming (Microsoft Entra ID, workspace storage, SCC/NPIP, NCC, host/container subnet, Lakeflow, ADLS Gen2). Time-sensitive facts (default-outbound retirement 2026-03-31, NSP/service-tag migration 2026-06-09, Standard-tier EOL 2026-10-01) are flagged in-lesson — reconfirm against docs before quoting a customer.
