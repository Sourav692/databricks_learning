# Lakeflow Jobs & Tasks

> **Topic 7.1 · Lakeflow Jobs — orchestration** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 7
> notebook (built at the last subtopic); the bundle/config snippets below are the
> teaching units.

## What it is

- **Lakeflow Jobs** is Databricks' **workflow orchestration** — it coordinates and
  runs **multiple tasks** as one larger workflow. (Formerly **Databricks
  Workflows / Jobs**.)
- A **job** is the top-level resource (schedule + run + monitor); a **task** is one
  unit of work inside it.
- Tasks come in **many types** (notebook, SQL, Python, **SDP pipeline**, dbt…) and
  form a **DAG** via `depends_on` dependencies — so you orchestrate notebooks +
  SQL + pipelines **together**.

**Analogy:** a job is a **recipe**; tasks are the **steps**. The DAG is the recipe
order — "make the sauce *before* you plate" — and the job runner makes each step
wait for the ones it depends on.

## Why it matters

- Real pipelines are **multi-step** (ingest → transform → publish → notify). Jobs
  run them **reliably, on a schedule, with dependencies and retries** — not by hand.
- "How do you orchestrate a pipeline on Databricks?" → **Lakeflow Jobs** (and
  knowing it ≠ SDP) is the expected answer.

**Real-world use case:** a nightly job runs task 1 (Auto Loader ingest notebook) →
task 2 (SDP pipeline for silver/gold) → task 3 (SQL data-quality check) → task 4
(notify) — orchestrated as one DAG, retried on failure.

---

## How it works — deep dive

### 1. Job → tasks → DAG

- Define **tasks** and their **dependencies**; the runner executes them in order,
  **in parallel where independent**. The graph must be **acyclic** (a cycle is a
  config error).
- Author in the **visual UI** *or* as **code** (Databricks Asset Bundles) — the
  same job, defined two ways. Production teams use the bundle for CI/CD.

### 2. Task types (mix them in one job)

| Task type | YAML key | Runs |
|---|---|---|
| Notebook | `notebook_task` | a notebook |
| SDP pipeline | `pipeline_task` | a Lakeflow Declarative Pipeline |
| SQL | `sql_task` | a query / file / dashboard / alert on a SQL warehouse |
| Python wheel | `python_wheel_task` | an entry point in a packaged wheel |
| Python file / dbt / JAR / Run-job | `spark_python_task` / `dbt_task` / … | scripts, dbt, nested jobs |

### 3. Config-as-code — a multi-task job (Asset Bundle)

The verified bundle shape: `resources.jobs.<key>.tasks[]`, each with a `task_key`,
a task-type block, and `depends_on` to wire the DAG.

```yaml
# databricks.yml (resources)
resources:
  jobs:
    nightly_etl:
      name: nightly_etl
      tasks:
        - task_key: ingest                          # task 1
          notebook_task:
            notebook_path: ../src/autoloader_ingest.py
          job_cluster_key: etl_cluster

        - task_key: transform                        # task 2: runs an SDP pipeline
          depends_on: [{ task_key: ingest }]
          pipeline_task:
            pipeline_id: ${resources.pipelines.silver_gold.id}

        - task_key: dq_check                         # task 3: SQL on a warehouse
          depends_on: [{ task_key: transform }]
          sql_task:
            warehouse_id: ${var.warehouse_id}
            file: { path: ../src/dq_checks.sql }

        - task_key: notify                           # task 4: notebook
          depends_on: [{ task_key: dq_check }]
          notebook_task: { notebook_path: ../src/notify.py }

      job_clusters:                                  # reusable cluster for the job
        - job_cluster_key: etl_cluster
          new_cluster:
            spark_version: 15.4.x-scala2.12
            node_type_id: i3.xlarge
            num_workers: 2
```

```bash
databricks bundle deploy -t prod        # create/update the job from the bundle
databricks bundle run nightly_etl       # trigger a run
```

### 4. Compute: which cluster runs a task?

- **Job clusters** (`job_clusters` + `job_cluster_key`) — created for the run and
  torn down after; cheapest for scheduled jobs, isolated per run.
- **Serverless** — no cluster to manage; fast start, good default for many tasks.
- **All-purpose (shared) clusters** — avoid for production jobs (cost + noisy
  neighbors); fine for dev.

### 5. Jobs vs SDP (don't confuse them)

| | Lakeflow Jobs | Lakeflow Declarative Pipelines (SDP) |
|---|---|---|
| Role | **Orchestrate** many tasks | **Build** one data pipeline |
| Unit | Task (notebook/SQL/pipeline) | Dataset (streaming table / MV) |
| You define | The DAG of steps (`depends_on`) | The desired tables; SDP derives its DAG |
| Relationship | A job **runs an SDP pipeline** via `pipeline_task` | runs inside / alongside jobs |

---

## Uses, edge cases & limitations

- **Uses:** scheduling end-to-end pipelines; chaining ingest → SDP → checks →
  notify; mixing notebooks, SQL, ML, and pipelines in one workflow; CI/CD via
  Asset Bundles.
- **Edge cases:** the DAG must be **acyclic** — no circular `depends_on`; a failed
  upstream task blocks dependents (configure retries / conditional `if/else` tasks);
  libraries can't be declared on a **shared job cluster** — put them in task settings.
- **Limitations:** Jobs **orchestrate**; they don't replace SDP's incremental
  pipeline engine. Over-stuffing logic into one giant task loses the per-task
  retry/observability benefits — split into tasks.

## Common gotchas

- ❌ Confusing **Jobs** (orchestration) with **SDP** (pipeline building) — a job can
  *run* an SDP pipeline via `pipeline_task`.
- ❌ Calling it "Workflows" as if current — it's **Lakeflow Jobs** now.
- ❌ One monolithic task instead of a task DAG → no granular retries/monitoring.
- ❌ Creating a dependency cycle — the DAG must be acyclic.
- ❌ Running production jobs on an **all-purpose cluster** — use job clusters or
  serverless.

## References

- [Lakeflow Jobs — Databricks docs](https://docs.databricks.com/aws/en/jobs/)
- [Add tasks to jobs (task types)](https://docs.databricks.com/aws/en/dev-tools/bundles/job-task-types)
- [Develop a job with Asset Bundles](https://docs.databricks.com/aws/en/dev-tools/bundles/jobs-tutorial)
- [Configure a task](https://docs.databricks.com/aws/en/jobs/configure-task)
