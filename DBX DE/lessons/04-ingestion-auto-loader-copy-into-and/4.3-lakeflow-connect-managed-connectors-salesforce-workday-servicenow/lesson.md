# Lakeflow Connect — Managed & Standard Connectors

> **Topic 4.3 · Ingestion — Auto Loader, COPY INTO & Lakeflow Connect** —
> enterprise deep-dive, interview-focused. This is a UI/config-driven topic, so
> each section pairs the **Databricks UI steps** with the **equivalent
> config-as-code** (UC connection, Asset Bundle, CLI).

## What it is

- **Lakeflow Connect** is Databricks' built-in way to **ingest from SaaS apps and
  databases** with little or no code.
- **Managed connectors** — fully-managed, **UI-based** ingestion from sources like
  **Salesforce, Workday, ServiceNow, HubSpot, Jira, SQL Server, MySQL,
  PostgreSQL**. Governed by Unity Catalog, **serverless**, built on **Lakeflow
  Spark Declarative Pipelines (SDP)**.
- **Standard / community connectors** — reach a **wider range of sources** from
  inside your pipelines when no managed connector exists (community ones are
  open-source, best-effort).

**Analogy:** managed connectors are **direct flights** — Databricks runs the whole
trip (Salesforce → your lakehouse) with monitoring and recovery built in.
Standard/community connectors are **connecting flights you book yourself** — more
reach, but you wire up more of the journey.

## Why it matters

- Hand-building API ingestion (auth, pagination, CDC, retries, schema) is **weeks
  of brittle work**. Managed connectors make it a **point-and-click** pipeline with
  monitoring and failure recovery.
- "How would you ingest Salesforce data?" → "**Lakeflow Connect managed
  connector**" is the modern, expected interview answer.

**Real-world use case:** finance needs Salesforce opportunities in the lakehouse
daily. A **managed connector** ingests them incrementally into bronze — no custom
API code, governed by Unity Catalog, monitored automatically.

---

## How it works — deep dive

### 1. The three connector tiers

| Tier | What | Monitoring/recovery | Coverage |
|---|---|---|---|
| **Managed** | Databricks-built, UI, serverless, UC-governed | **built-in** | specific (growing) list |
| **Standard** | used inside your pipelines for more sources | you implement | broader |
| **Community** | open-source, community-maintained | best-effort | long tail |

- Managed connector categories today: **SaaS** (Salesforce, Workday, ServiceNow,
  HubSpot, Jira), **Database/CDC** (SQL Server, MySQL, PostgreSQL), **File**
  (SharePoint, Google Drive), plus query-based and streaming sources.

### 2. SaaS connector flow (Salesforce, Workday…)

A simple **connection → ingestion pipeline → UC table** flow; most read only
new/changed records (**incremental**, often via cursor columns).

**UI steps:** **Data Ingestion** → pick the source (e.g. **Salesforce**) →
create/select a **connection** (OAuth) → choose **catalog/schema** + objects →
create the **ingestion pipeline** → set a schedule.

```text
Salesforce ──▶ UC Connection (OAuth) ──▶ Ingestion pipeline (SDP) ──▶ Bronze Delta
```

### 3. Database connectors & CDC (SQL Server, MySQL, Postgres)

Heavier architecture: changes are captured via **CDC** through an **ingestion
gateway** (its own pipeline) that lands changes in **staging storage**, then an
**ingestion pipeline** writes them to UC tables.

```text
SQL Server ─CDC─▶ Ingestion gateway ──▶ Staging ──▶ Ingestion pipeline ──▶ Bronze Delta
```

- This is the main difference interviewers probe: **SaaS = connection + pipeline;
  database = connection + gateway + staging + pipeline.**

### 4. Configure as code (the part that makes it production-grade)

Everything the UI does is reproducible as code for CI/CD.

**The Unity Catalog connection** (securable storing auth; SaaS connections are
typically created via UI OAuth, databases via `CREATE CONNECTION`):

```sql
-- Example: a database connection (SaaS connectors usually use UI OAuth instead)
CREATE CONNECTION sqlserver_conn TYPE sqlserver
OPTIONS (host 'db.corp.net', port '1433',
         user secret('kv','sql_user'), password secret('kv','sql_pwd'));
```

**The ingestion pipeline as a Databricks Asset Bundle** (verified field names):

```yaml
# databricks.yml (resources)
resources:
  pipelines:
    pipeline_sfdc:
      name: salesforce_ingest
      catalog: main                       # destination catalog
      schema: bronze                      # destination schema
      ingestion_definition:
        connection_name: salesforce_conn  # the UC connection
        objects:
          - table:
              source_schema: objects
              source_table: Account       # Salesforce object → bronze table
              destination_catalog: main
              destination_schema: bronze
          - table:
              source_schema: objects
              source_table: Opportunity
              destination_catalog: main
              destination_schema: bronze
```

**Deploy & run with the CLI:**

```bash
databricks bundle deploy -t prod      # create/update the pipeline from the bundle
databricks bundle run pipeline_sfdc   # trigger an ingestion run
```

**Schedule it** (a job triggering the pipeline) — bundle snippet:

```yaml
  jobs:
    sfdc_daily:
      name: salesforce_daily
      trigger: { periodic: { interval: 1, unit: DAYS } }
      tasks:
        - task_key: ingest
          pipeline_task: { pipeline_id: ${resources.pipelines.pipeline_sfdc.id} }
```

---

## Uses, edge cases & limitations

- **Uses:** pulling enterprise SaaS/db data (CRM, HR, ITSM, OLTP DBs) into bronze
  without custom code; CDC from operational databases; CI/CD via Asset Bundles.
- **Edge cases:**
  - **Database connectors need an ingestion gateway** + staging — more setup than
    SaaS connectors.
  - Not every source has a **managed** connector → use a **standard/community**
    connector (less built-in support/monitoring).
  - SaaS connections use **OAuth via the UI**; you can't always fully script the
    auth handshake.
- **Limitations:** managed connectors cover a **specific (growing) list** of
  sources; community connectors rely on volunteer maintenance and may lack
  official support. **Verify current source availability in the docs** before
  promising a source.

## Common gotchas

- ❌ Assuming **every** SaaS/db has a managed connector — verify the supported list.
- ❌ Forgetting database connectors require an **ingestion gateway** (heavier than
  SaaS connectors).
- ❌ Hand-rolling API ingestion when a managed connector already exists — extra
  toil and no built-in monitoring/recovery.
- ❌ Confusing **managed** (Databricks-maintained) with **community** (open-source,
  best-effort) connectors.
- ❌ Clicking the pipeline together in the UI for prod and never capturing it as a
  **bundle** — you lose CI/CD and reproducibility.

## References

- [Lakeflow Connect — managed connectors](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/)
- [Salesforce ingestion pipeline](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/salesforce-pipeline)
- [SQL Server ingestion (gateway + CDC)](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/)
- [Ingestion overview](https://docs.databricks.com/aws/en/ingestion/)
- [Unity Catalog connections (CREATE CONNECTION)](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-connection)
