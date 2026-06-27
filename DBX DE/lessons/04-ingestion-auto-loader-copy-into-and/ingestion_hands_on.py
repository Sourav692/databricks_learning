# Databricks notebook source
# MAGIC %md
# MAGIC # Ingestion — Hands-On (Topic 4)
# MAGIC One runnable notebook covering the hands-on subtopics of Topic 4, at the
# MAGIC enterprise depth of the lessons:
# MAGIC - **4.1** Auto Loader (cloudFiles) + schema evolution & `_rescued_data` — runnable
# MAGIC - **4.2** COPY INTO — idempotency, transform-on-load, VALIDATE, force — runnable
# MAGIC - **4.3** Lakeflow Connect — UI + Asset Bundle config (reference cell)
# MAGIC - **4.4** Kafka / Event Hubs & CDC — reference cells (need external infra)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - DBR 14.3 LTS+ or Serverless, Unity Catalog enabled
# MAGIC - A UC **Volume** you can write to (for landing sample files)
# MAGIC - `CREATE SCHEMA`/`CREATE TABLE`/`CREATE VOLUME` grants on the target catalog
# MAGIC - Edit the widgets below to your sandbox
# MAGIC
# MAGIC **Scope:** ingestion features only — no Apache Spark core programming.
# MAGIC Run top to bottom; the last cell cleans up so it's rerunnable.

# COMMAND ----------

# MAGIC %md ## Setup — catalog, schema, and a landing Volume (UC three-level namespace)

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "de_demo_ingest", "Schema")
dbutils.widgets.text("volume", "landing", "Volume")
catalog = dbutils.widgets.get("catalog")
schema  = dbutils.widgets.get("schema")
volume  = dbutils.widgets.get("volume")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"USE {catalog}.{schema}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.{volume}")
base = f"/Volumes/{catalog}/{schema}/{volume}"
print("Landing path:", base)

# COMMAND ----------

# Write a couple of sample JSON files to ingest
dbutils.fs.put(f"{base}/raw/f1.json", '{"id":1,"event":"click"}\n{"id":2,"event":"view"}', True)
dbutils.fs.put(f"{base}/raw/f2.json", '{"id":3,"event":"click"}', True)
display(dbutils.fs.ls(f"{base}/raw"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4.1 — Auto Loader (cloudFiles)
# MAGIC Incrementally ingest only new files; `availableNow` processes all new then stops.
# MAGIC The checkpoint makes it exactly-once and restart-safe (one checkpoint per stream).

# COMMAND ----------

ckpt = f"{base}/_ckpt_al"
(spark.readStream.format("cloudFiles")
   .option("cloudFiles.format", "json")
   .option("cloudFiles.schemaLocation", ckpt)
   .option("cloudFiles.schemaEvolutionMode", "addNewColumns")   # default; fail+restart on new col
   .load(f"{base}/raw")
 .writeStream
   .option("checkpointLocation", ckpt)
   .trigger(availableNow=True)
   .toTable(f"{catalog}.{schema}.bronze_autoloader")).awaitTermination()

display(spark.table(f"{catalog}.{schema}.bronze_autoloader"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Schema evolution + `_rescued_data`
# MAGIC Land a file with a NEW column (`country`) and a type-mismatched `id`. With the
# MAGIC default `addNewColumns` mode the stream adds the column on restart; values that
# MAGIC don't fit land in `_rescued_data` instead of being dropped.

# COMMAND ----------

# new column `country` + a non-integer id ("X9") to exercise _rescued_data
dbutils.fs.put(f"{base}/raw/f3.json", '{"id":4,"event":"view","country":"US"}\n{"id":"X9","event":"click"}', True)

# Re-run: addNewColumns mode evolves the schema (a job would auto-restart on the
# UnknownFieldException). availableNow + awaitTermination picks up the new file.
(spark.readStream.format("cloudFiles")
   .option("cloudFiles.format", "json")
   .option("cloudFiles.schemaLocation", ckpt)
   .option("cloudFiles.schemaHints", "id int")          # force id to INT so "X9" is rescued
   .load(f"{base}/raw")
 .writeStream
   .option("checkpointLocation", ckpt)
   .trigger(availableNow=True)
   .toTable(f"{catalog}.{schema}.bronze_autoloader")).awaitTermination()

# inspect rescued data — the "X9" id couldn't be parsed as INT and was preserved
display(spark.sql(f"""
  SELECT id, event, country, _rescued_data
  FROM {catalog}.{schema}.bronze_autoloader
  ORDER BY id NULLS LAST"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4.2 — COPY INTO
# MAGIC SQL batch load that skips already-loaded files (idempotent). It dedups
# MAGIC **files**, not rows; a modified same-path file is skipped unless `force=true`.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE IF NOT EXISTS bronze_copyinto (id INT, event STRING, country STRING);
# MAGIC
# MAGIC -- Transform-on-load: cast id to INT during ingestion
# MAGIC COPY INTO bronze_copyinto
# MAGIC FROM (SELECT CAST(id AS INT) AS id, event, country
# MAGIC       FROM '/Volumes/${catalog}/${schema}/${volume}/raw')
# MAGIC FILEFORMAT = JSON
# MAGIC COPY_OPTIONS ('mergeSchema' = 'true');
# MAGIC
# MAGIC SELECT * FROM bronze_copyinto ORDER BY id NULLS LAST;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Re-run is idempotent: 0 new rows loaded (already-loaded files skipped).
# MAGIC COPY INTO bronze_copyinto
# MAGIC FROM (SELECT CAST(id AS INT) AS id, event, country
# MAGIC       FROM '/Volumes/${catalog}/${schema}/${volume}/raw')
# MAGIC FILEFORMAT = JSON;
# MAGIC SELECT count(*) AS rows_after_rerun FROM bronze_copyinto;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- VALIDATE: dry-run a load without writing (checks parsing/schema/constraints).
# MAGIC COPY INTO bronze_copyinto
# MAGIC FROM '/Volumes/${catalog}/${schema}/${volume}/raw'
# MAGIC FILEFORMAT = JSON
# MAGIC VALIDATE 10 ROWS;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4.3 — Lakeflow Connect (reference — UI + Asset Bundle)
# MAGIC Managed connectors (Salesforce, Workday, SQL Server…) are created via the UI
# MAGIC (**Data Ingestion**) or as code in a Databricks Asset Bundle. They run on
# MAGIC serverless + Lakeflow Declarative Pipelines and land data in Delta.
# MAGIC ```yaml
# MAGIC # databricks.yml (resources) — Salesforce ingestion pipeline
# MAGIC resources:
# MAGIC   pipelines:
# MAGIC     pipeline_sfdc:
# MAGIC       name: salesforce_ingest
# MAGIC       catalog: main
# MAGIC       schema: bronze
# MAGIC       ingestion_definition:
# MAGIC         connection_name: salesforce_conn   # UC connection (OAuth)
# MAGIC         objects:
# MAGIC           - table: { source_schema: objects, source_table: Opportunity,
# MAGIC                      destination_catalog: main, destination_schema: bronze }
# MAGIC ```
# MAGIC Deploy with `databricks bundle deploy`; database connectors add an ingestion
# MAGIC gateway + staging for CDC.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4.4 — Kafka / Event Hubs (reference — needs a running broker)
# MAGIC Kafka rows are `key`/`value` BINARY + `topic`/`partition`/`offset`/`timestamp`.
# MAGIC Deserialize `value` before use; keep offsets for replay; checkpoint = exactly-once.
# MAGIC ```python
# MAGIC from pyspark.sql.functions import col, from_json
# MAGIC schema_str = "user_id INT, event STRING, ts TIMESTAMP"
# MAGIC df = (spark.readStream.format("kafka")
# MAGIC         .option("kafka.bootstrap.servers", "host:9092")
# MAGIC         .option("subscribe", "events")
# MAGIC         .option("startingOffsets", "latest")        # "earliest" to backfill
# MAGIC         .option("maxOffsetsPerTrigger", 500000)     # cap per micro-batch
# MAGIC         .load())
# MAGIC parsed = (df.select(from_json(col("value").cast("string"), schema_str).alias("d"),
# MAGIC                     col("topic"), col("partition"), col("offset"), col("timestamp"))
# MAGIC             .select("d.*", "topic", "partition", "offset", "timestamp"))
# MAGIC (parsed.writeStream
# MAGIC    .option("checkpointLocation", f"{base}/_ck_kafka")   # unique per stream
# MAGIC    .toTable(f"{catalog}.{schema}.bronze_events"))
# MAGIC ```
# MAGIC SQL equivalent: `STREAM read_kafka(bootstrapServers => 'host:9092', subscribe => 'events')`.
# MAGIC Database CDC is configured through **Lakeflow Connect** database connectors (4.3),
# MAGIC then applied to silver with AUTO CDC / APPLY CHANGES (5.4). Azure Event Hubs uses
# MAGIC the same Kafka reader (Kafka protocol).

# COMMAND ----------

# MAGIC %md ## Cleanup — drop demo objects so the notebook is rerunnable

# COMMAND ----------

spark.sql(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
print(f"Dropped {catalog}.{schema}")
