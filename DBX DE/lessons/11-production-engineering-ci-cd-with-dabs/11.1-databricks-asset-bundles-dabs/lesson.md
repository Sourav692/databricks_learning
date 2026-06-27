# Databricks Asset Bundles (DABs)

> **Topic 11.1 · Production Engineering — CI/CD with DABs** — enterprise deep-dive,
> interview-focused. Each sub-topic pairs the **mechanism** with a commented,
> production-shaped `databricks.yml` / CLI snippet. The end-to-end CI/CD hands-on
> lives in the consolidated Topic 11 notebook (built at 11.3).

## What it is

- **Databricks Asset Bundles (DABs)** are **infrastructure-as-code for the
  lakehouse**: you declare jobs, pipelines, dashboards, alerts, warehouses, and more
  in **YAML** (`databricks.yml`) and deploy them with the **Databricks CLI**.
- **Targets** (dev / staging / prod) let one bundle deploy to many environments with
  per-environment overrides and **deployment modes** (`development` / `production`).
- **Lifecycle:** `bundle init` → `validate` → `deploy` → `run` → `destroy`.
- Brings **source control, code review, testing, and CI/CD** to Databricks projects.

**Analogy:** a bundle is the **blueprint + flat-pack instructions** for your project.
Instead of hand-assembling furniture in every room (clicking jobs/pipelines together
in each workspace), you ship one labeled box that builds the **same setup** in dev
and prod from code.

## Why it matters

- Click-built jobs/pipelines **drift** between dev and prod and can't be reviewed or
  rolled back. DABs make deployments **reproducible, versioned, and automated**.
- "How do you promote a pipeline from dev to prod / do CI-CD on Databricks?" →
  **Asset Bundles** is the modern, expected answer.

**Real-world use case:** a team keeps `databricks.yml` in Git; a GitHub Action runs
`databricks bundle validate` on PRs and `databricks bundle deploy -t prod` on merge —
so the prod job/pipeline always matches reviewed code, with zero manual UI edits.

---

## How it works — deep dive

### 1. The `databricks.yml` skeleton — top-level mappings

**Mechanism:** one root file defines the bundle. The top-level mappings are
`bundle`, `include`, `variables`, `artifacts`, `resources`, `targets`, `sync`,
`run_as`, `workspace`, and `permissions`. `bundle.name` is the only strictly
required mapping; everything else composes around it.

**Why:** a single declarative entry point means the whole project is one reviewable,
diffable artifact.

**Trade-off:** a big monolithic file gets unwieldy — split resources into separate
files and pull them in with `include` (next).

```yaml
# databricks.yml — the root file (only `bundle.name` is strictly required)
bundle:
  name: sales_platform        # logical name; namespaces deployed resources
variables:
  warehouse_id:
    description: SQL warehouse for SQL tasks
    default: ""               # overridable per target / via --var
```

### 2. Structure for scale — `include`, `artifacts`, `sync`

**Mechanism:** `include` globs in extra YAML files (one per resource area);
`artifacts` declares things to build at deploy time (e.g. a Python wheel); `sync`
controls which local paths upload to the workspace.

**Why:** keeps the root file small, lets teams own resource files independently, and
ships built code with the config.

**Trade-off:** `include` paths are relative to the root and only valid as a top-level
mapping — mis-scoped globs silently include nothing.

```yaml
include:
  - resources/*.yml           # jobs.yml, pipelines.yml, dashboards.yml …
artifacts:
  sales_wheel:
    type: whl
    path: ./libs/sales        # built and uploaded on deploy
sync:
  paths: [ ./src ]            # local code synced to the workspace
```

### 3. Resources — declare what gets deployed

**Mechanism:** under `resources`, each supported type (`jobs`, `pipelines`,
`dashboards`, `alerts`, `sql_warehouses`, `experiments`, `models`, …) holds named
declarations that map to the corresponding REST API object.

**Why:** the thing you'd click to create (a job, a pipeline, a dashboard) becomes a
versioned, reviewable block.

**Trade-off:** the bundle owns these resources — editing them in the UI then
re-deploying reverts your change (the bundle is the source of truth).

```yaml
resources:
  jobs:
    sales_etl:
      name: sales-etl
      tasks:
        - task_key: ingest
          notebook_task: { notebook_path: ./src/ingest.py }
        - task_key: report
          sql_task:
            warehouse_id: ${var.warehouse_id}
            file: { path: ./sql/report.sql }
          depends_on: [{ task_key: ingest }]
  pipelines:
    sales_sdp:
      name: sales-sdp
      libraries: [{ notebook: { path: ./src/pipeline.py } }]
```

### 4. Targets & deployment modes — dev vs prod

**Mechanism:** `targets` declares each environment (host, mode, overrides). `mode:
development` namespaces resources per user and pauses schedules/triggers so dev is
safe to iterate; `mode: production` deploys with real names and active schedules. One
target is the default (`default: true`).

**Why:** one bundle, many environments — no copy-pasted prod configs.

**Trade-off:** deploying to the wrong target is easy to do — dev mode *renames*
resources, so always check `-t`.

```yaml
targets:
  dev:
    mode: development          # prefixes names, pauses schedules — safe to iterate
    default: true
    workspace: { host: https://dev.cloud.databricks.com }
  prod:
    mode: production           # real names, schedules active
    workspace: { host: https://prod.cloud.databricks.com }
    run_as: { service_principal_name: ${var.sp_app_id} }  # who prod runs as
```

### 5. Variables & per-target overrides

**Mechanism:** `variables` set defaults at the top level; any target can override
them, and `--var` overrides at the CLI. References use `${var.name}` (and
`${resources....id}` to wire resources together).

**Why:** the same resource definition adapts per environment (a small dev warehouse,
a big prod one) without duplicating the resource block.

**Trade-off:** undefined/empty variables surface only at `validate`/`deploy` — set
sensible defaults and validate in CI.

```yaml
# top-level default above; override inside a target:
targets:
  prod:
    variables:
      warehouse_id: 0a1b2c3d4e5f6789   # prod SQL warehouse
# deploy-time override: databricks bundle deploy -t prod --var="warehouse_id=..."
```

### 6. The CLI lifecycle

**Mechanism:** `init` scaffolds from a template; `validate` checks config (and warns
on unknown properties); `summary`/`plan` preview identity and pending changes;
`deploy -t <target>` pushes; `run <resource> -t <target>` executes; `destroy -t
<target>` tears down what the bundle created.

**Why:** a predictable, scriptable flow — the same commands run locally and in CI.

**Trade-off:** `destroy` permanently deletes deployed jobs/pipelines/artifacts and
can't be undone — guard it in automation.

```bash
databricks bundle init                       # scaffold from a template
databricks bundle validate                   # check config (run in CI on PRs)
databricks bundle deploy -t dev              # push to the dev target
databricks bundle run sales_etl -t dev       # execute a deployed job
databricks bundle deploy -t prod             # promote (usually CI on merge)
databricks bundle destroy -t dev             # tear down dev (irreversible)
```

---

## Uses, edge cases & limitations

- **Uses:** promoting dev→prod consistently, CI/CD via GitHub Actions/Azure DevOps,
  versioning jobs/pipelines/dashboards, team collaboration with code review,
  importing existing resources via `bundle generate` + `deployment bind`.
- **Edge cases:**
  - **Secrets/host config** differ per target — use target overrides + secret
    scopes, never hard-code prod creds.
  - `development` vs `production` **mode** changes behavior (dev prefixes names,
    pauses schedules) — know which target you're deploying to.
  - `run_as` controls the identity prod runs under — set a service principal for prod.
- **Limitations:** DABs deploy **Databricks resources**, not general cloud infra (use
  Terraform for VPCs/buckets); if people also click-edit deployed resources you get
  drift — treat the bundle as the single source of truth.

## Common gotchas

- ❌ Click-editing a deployed job in the UI → next `deploy` overwrites it; the
  **bundle is the source of truth**.
- ❌ Hard-coding prod host/secrets instead of using **targets** + variables + secret
  scopes.
- ❌ Deploying to the wrong **target** (dev mode renames resources) — check `-t`.
- ❌ Skipping `validate` in CI → broken config reaches the workspace.
- ❌ Running `destroy` against prod by habit — it's irreversible; gate it.

## References

- [Databricks Asset Bundles — docs](https://docs.databricks.com/aws/en/dev-tools/bundles/)
- [Bundle configuration (`databricks.yml`)](https://docs.databricks.com/aws/en/dev-tools/bundles/settings)
- [Configuration reference (all keys)](https://docs.databricks.com/aws/en/dev-tools/bundles/reference)
- [`bundle` CLI command group](https://docs.databricks.com/aws/en/dev-tools/cli/bundle-commands)
- [CI/CD with bundles](https://docs.databricks.com/aws/en/dev-tools/bundles/ci-cd)
