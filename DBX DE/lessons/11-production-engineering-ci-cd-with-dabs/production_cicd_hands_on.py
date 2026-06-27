# Databricks notebook source
# MAGIC %md
# MAGIC # Production Engineering — CI/CD with DABs — Hands-On (Topic 11)
# MAGIC Ties Topic 11 together. Most of CI/CD lives in **files + the CLI**, not in a
# MAGIC notebook, so the bundle / Git / Databricks Connect parts are shown as
# MAGIC **reference** cells; the runnable cells create a tiny UC-safe table that a
# MAGIC bundled job/pipeline (or a Databricks Connect session) would target.
# MAGIC - **11.1** Asset Bundles — `databricks.yml` + a job/pipeline resource (reference)
# MAGIC - **11.2** Git folders — branch → PR → CI deploy flow (reference)
# MAGIC - **11.3** Databricks Connect — local-IDE session example (reference)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - DBR 14.3 LTS+ / Serverless, Unity Catalog enabled; `CREATE`/`SELECT` on the
# MAGIC   target catalog; edit the widgets below.
# MAGIC - The Databricks CLI + a configured profile are needed to actually run the
# MAGIC   `databricks bundle …` commands (do that in a terminal, not here).
# MAGIC
# MAGIC **Scope:** production-engineering tooling only — no Apache Spark core programming.
# MAGIC Run top to bottom; the last cell cleans up so it's rerunnable.

# COMMAND ----------

# MAGIC %md ## Setup — a tiny table a bundled job/pipeline would target (UC 3-level)

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "de_demo_cicd", "Schema")
catalog = dbutils.widgets.get("catalog"); schema = dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
spark.sql("CREATE OR REPLACE TABLE orders (id INT, region STRING, amount INT)")
spark.sql("INSERT INTO orders VALUES (1,'West',100),(2,'East',250),(3,'West',80)")
print(f"Created {catalog}.{schema}.orders — what a deployed job/pipeline would read/write.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11.1 — Asset Bundle (`databricks.yml`) — reference
# MAGIC Keep this at the repo root in Git. `bundle.name` is the only required key;
# MAGIC `targets` give per-environment overrides; `resources` declare what gets deployed.
# MAGIC ```yaml
# MAGIC bundle:
# MAGIC   name: sales_platform
# MAGIC variables:
# MAGIC   warehouse_id: { default: "" }        # overridable per target / via --var
# MAGIC targets:
# MAGIC   dev:  { mode: development, default: true, workspace: { host: https://dev... } }
# MAGIC   prod: { mode: production,  workspace: { host: https://prod... } }
# MAGIC resources:
# MAGIC   jobs:
# MAGIC     sales_etl:
# MAGIC       name: sales-etl
# MAGIC       tasks:
# MAGIC         - task_key: ingest
# MAGIC           notebook_task: { notebook_path: ./src/ingest.py }
# MAGIC   pipelines:
# MAGIC     sales_sdp:
# MAGIC       name: sales-sdp
# MAGIC       libraries: [{ notebook: { path: ./src/pipeline.py } }]
# MAGIC ```
# MAGIC Lifecycle (in a terminal): `databricks bundle init / validate / deploy -t dev /
# MAGIC run sales_etl -t dev / deploy -t prod / destroy -t dev`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11.2 — Git folders → CI → deploy — reference
# MAGIC The bundle lives in a **Git folder**; a feature branch + PR get review, and CI
# MAGIC deploys on merge as a **service principal** (never a personal token).
# MAGIC ```yaml
# MAGIC # .github/workflows/deploy.yml
# MAGIC on: { push: { branches: [ main ] }, pull_request: {} }
# MAGIC jobs:
# MAGIC   bundle:
# MAGIC     runs-on: ubuntu-latest
# MAGIC     env:
# MAGIC       DATABRICKS_HOST: ${{ secrets.DATABRICKS_HOST }}
# MAGIC       DATABRICKS_CLIENT_ID: ${{ secrets.DBX_SP_CLIENT_ID }}     # service principal
# MAGIC       DATABRICKS_CLIENT_SECRET: ${{ secrets.DBX_SP_SECRET }}    # OAuth (M2M)
# MAGIC     steps:
# MAGIC       - uses: actions/checkout@v4
# MAGIC       - uses: databricks/setup-cli@main
# MAGIC       - run: databricks bundle validate                        # every PR
# MAGIC       - if: github.ref == 'refs/heads/main'
# MAGIC         run: databricks bundle deploy -t prod                  # on merge
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11.3 — Databricks Connect (local IDE) — reference
# MAGIC Runs in your **local IDE**, not in this notebook. Match the client to the
# MAGIC cluster DBR (13.3 LTS+); DataFrame ops execute on remote compute.
# MAGIC ```python
# MAGIC # pip install "databricks-connect==15.4.*"   # match the cluster DBR
# MAGIC from databricks.connect import DatabricksSession
# MAGIC spark = DatabricksSession.builder.getOrCreate()      # uses your .databrickscfg profile
# MAGIC # or: DatabricksSession.builder.serverless().getOrCreate()
# MAGIC
# MAGIC df = spark.read.table("prod.sales.orders")           # executes on the cluster
# MAGIC df.groupBy("region").sum("amount").show()            # plan → cluster → results back
# MAGIC ```
# MAGIC The same code is what you'd unit-test with pytest, then deploy via the bundle above.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Runnable check — the transform a bundled job / Connect session would run
# MAGIC This SQL runs here (on this cluster) and is exactly what the deployed `sales_etl`
# MAGIC job — or a Databricks Connect session — would execute against the same UC table.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT region, SUM(amount) AS revenue
# MAGIC FROM ${catalog}.${schema}.orders
# MAGIC GROUP BY region
# MAGIC ORDER BY revenue DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ## The production loop (how 11.1–11.3 fit together)
# MAGIC `Databricks Connect / notebooks` → develop & test → `Git folders` → review →
# MAGIC `Asset Bundles` (CI) → deploy to dev → prod. No manual UI edits in prod.

# COMMAND ----------

# MAGIC %md ## Cleanup — drop the schema so the notebook is rerunnable

# COMMAND ----------

spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print(f"Dropped {catalog}.{schema}")
