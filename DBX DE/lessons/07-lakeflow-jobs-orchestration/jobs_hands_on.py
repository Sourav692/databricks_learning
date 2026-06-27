# Databricks notebook source
# MAGIC %md
# MAGIC # Lakeflow Jobs — Hands-On (Topic 7)
# MAGIC Jobs are configured in the **Jobs & Pipelines** UI or as code (YAML via
# MAGIC Databricks Asset Bundles — Topic 11). This notebook shows the **runnable
# MAGIC building blocks** a job orchestrates, plus a full **reference job definition**
# MAGIC covering tasks, control flow, schedules, retries, notifications & health.
# MAGIC
# MAGIC Covers Topic 7 hands-on subtopics:
# MAGIC - **7.1** Tasks (a notebook task body), task types, job clusters
# MAGIC - **7.2** Control flow — task values (set/get), condition_task, for_each_task, run_if
# MAGIC - **7.3** Schedules/triggers (Quartz cron / file arrival), retries, notifications, health
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - DBR 14.3 LTS+ or Serverless; Unity Catalog enabled
# MAGIC - To run as a job: create a Job (Jobs & Pipelines ▸ Jobs) with this notebook
# MAGIC   as a task, or deploy the YAML below via a Databricks Asset Bundle
# MAGIC
# MAGIC **Scope:** orchestration features only — no Apache Spark core programming.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7.1 / 7.2 — A task body that SETS a task value
# MAGIC This cell is what an "ingest" task might run. It writes a row count as a
# MAGIC **task value** for a downstream condition_task (if/else) to read. UC
# MAGIC three-level namespacing via widgets makes it portable across workspaces.

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "de_demo_jobs", "Schema")
catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

# pretend we ingested some rows
df = spark.range(150)
df.write.mode("overwrite").saveAsTable(f"{catalog}.{schema}.bronze_demo")
row_count = df.count()

# set a task value for downstream tasks (7.2 control flow)
dbutils.jobs.taskValues.set(key="row_count", value=row_count)
print(f"Ingested {row_count} rows; set task value row_count={row_count}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7.2 — A downstream task body that GETS the task value
# MAGIC In a real job this runs as a separate task with a dependency on "ingest".
# MAGIC A `condition_task` would compare `{{tasks.ingest.values.row_count}}` to a
# MAGIC literal and branch. (Shown here with a safe default so the notebook also
# MAGIC runs standalone.)

# COMMAND ----------

try:
    n = dbutils.jobs.taskValues.get(taskKey="ingest", key="row_count", default=row_count)
except Exception:
    n = row_count  # standalone fallback
print("row_count from upstream task:", n, "→ branch:", "publish" if n >= 100 else "quarantine")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7.1–7.3 — Reference job definition (Asset Bundle YAML)
# MAGIC Tasks, control flow, schedule/trigger, retries, notifications, and health are
# MAGIC **job config**, not notebook code. Deploy with a Databricks Asset Bundle
# MAGIC (Topic 11): `databricks bundle deploy -t prod` then `databricks bundle run`.
# MAGIC ```yaml
# MAGIC resources:
# MAGIC   jobs:
# MAGIC     demo_job:
# MAGIC       name: de-demo-job
# MAGIC       # --- 7.3 trigger: file arrival (alt: schedule below / periodic) ---
# MAGIC       trigger:
# MAGIC         file_arrival: { url: /Volumes/main/de_demo_jobs/landing/ }
# MAGIC       schedule:                                   # Quartz cron (6-field!), job timezone
# MAGIC         quartz_cron_expression: "0 0 2 * * ?"     # 02:00 daily
# MAGIC         timezone_id: "UTC"
# MAGIC         pause_status: UNPAUSED
# MAGIC       # --- 7.3 reliability ---
# MAGIC       timeout_seconds: 7200
# MAGIC       max_concurrent_runs: 1
# MAGIC       # --- 7.3 notifications + health (SLA) ---
# MAGIC       email_notifications:
# MAGIC         on_failure: ["oncall@example.com"]
# MAGIC         on_duration_warning_threshold_exceeded: ["oncall@example.com"]
# MAGIC       health:
# MAGIC         rules:
# MAGIC           - metric: RUN_DURATION_SECONDS
# MAGIC             op: GREATER_THAN
# MAGIC             value: 3600
# MAGIC       job_clusters:                               # 7.1 reusable job cluster
# MAGIC         - job_cluster_key: etl
# MAGIC           new_cluster: { spark_version: 15.4.x-scala2.12, node_type_id: i3.xlarge, num_workers: 2 }
# MAGIC       tasks:
# MAGIC         - task_key: ingest                        # 7.1 notebook task
# MAGIC           job_cluster_key: etl
# MAGIC           notebook_task: { notebook_path: ./jobs_hands_on }
# MAGIC           max_retries: 2                          # 7.3 retries
# MAGIC           min_retry_interval_millis: 60000
# MAGIC           retry_on_timeout: true
# MAGIC         - task_key: gate                          # 7.2 if/else (condition_task)
# MAGIC           depends_on: [{ task_key: ingest }]
# MAGIC           condition_task:
# MAGIC             op: GREATER_THAN
# MAGIC             left: "{{tasks.ingest.values.row_count}}"
# MAGIC             right: "100"
# MAGIC         - task_key: publish                       # runs only when gate == true
# MAGIC           depends_on: [{ task_key: gate, outcome: "true" }]
# MAGIC           notebook_task: { notebook_path: ./jobs_hands_on }
# MAGIC         - task_key: alert                         # 7.2 run_if on failure
# MAGIC           depends_on: [{ task_key: ingest }, { task_key: publish }]
# MAGIC           run_if: AT_LEAST_ONE_FAILED
# MAGIC           notebook_task: { notebook_path: ./jobs_hands_on }
# MAGIC ```

# COMMAND ----------

# MAGIC %md ## Cleanup — drop demo objects so the notebook is rerunnable

# COMMAND ----------

spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print(f"Dropped {catalog}.{schema}")
