# Databricks notebook source
# MAGIC %md
# MAGIC ## Auto Loader — Hands-On Demo (Day 1 → Day 2 → Day 3)
# MAGIC
# MAGIC This notebook implements the architecture we covered:
# MAGIC **idempotency (exactly once)**, **schema inference**, and **schema evolution**.
# MAGIC
# MAGIC **Part A** simulates three days of files and watches Auto Loader in the default
# MAGIC **`addNewColumns`** mode:
# MAGIC 1. **Day 1** – infer the schema from the first file and write to a Delta table.
# MAGIC 2. **Day 2** – skip the processed file and read only the new one.
# MAGIC 3. **Day 3** – detect a new column, update the schema location, stop with
# MAGIC    `UnknownFieldException`, then **self-heal on restart**.
# MAGIC
# MAGIC **Part B** repeats the evolved-file scenario in **`rescue`** mode — the stream never stops,
# MAGIC extra columns land in `_rescued_data`, and we extract them with `from_json`.
# MAGIC
# MAGIC ### Prerequisites
# MAGIC - **DBR 13.3 LTS or later** (any current runtime). Serverless or a standard cluster both work.
# MAGIC - **Unity Catalog enabled.** You need a catalog you can write to and `CREATE` privileges
# MAGIC   on the schema/volume.
# MAGIC - We use a **Unity Catalog Volume** for the source files, checkpoint, and schema location —
# MAGIC   the recommended pattern instead of DBFS.
# MAGIC
# MAGIC > Edit the three widgets / variables in the **Config** cell to point at a catalog & schema you own.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Config — set your catalog and schema
# MAGIC Three-level Unity Catalog namespace: `catalog.schema.table`.

# COMMAND ----------

# Change these to a catalog / schema you have CREATE rights on.
CATALOG = "main"
SCHEMA  = "autoloader_demo"
VOLUME  = "al_demo"                      # a managed UC volume we create below
TABLE   = f"{CATALOG}.{SCHEMA}.orders_bronze"

# Volume-backed paths (recommended over DBFS for UC workloads)
VOLUME_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
SOURCE_PATH = f"{VOLUME_ROOT}/landing"        # where raw files arrive
SCHEMA_PATH = f"{VOLUME_ROOT}/_schema"        # cloudFiles.schemaLocation
CHKPT_PATH  = f"{VOLUME_ROOT}/_checkpoint"    # checkpointLocation (holds RocksDB state)

print("Source     :", SOURCE_PATH)
print("Schema loc :", SCHEMA_PATH)
print("Checkpoint :", CHKPT_PATH)
print("Target tbl :", TABLE)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Create the catalog objects (schema, volume) and clean up any prior run
# MAGIC Re-running this notebook from scratch? This cell resets the demo so results are reproducible.

# COMMAND ----------

SCHEMA

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")

# Fresh start: remove old files, checkpoint, schema, and table so the demo is repeatable.
dbutils.fs.rm(SOURCE_PATH, True)
dbutils.fs.rm(SCHEMA_PATH, True)
dbutils.fs.rm(CHKPT_PATH,  True)
dbutils.fs.mkdirs(SOURCE_PATH)
spark.sql(f"DROP TABLE IF EXISTS {TABLE}")

print("Clean slate ready.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Helper to drop a JSON file into the landing folder
# MAGIC In real life your upstream system writes these. Here we generate them.

# COMMAND ----------

import json

def land_file(name: str, records: list):
    """Write one JSON file (one record per line) into the landing folder."""
    path = f"{SOURCE_PATH}/{name}"
    body = "\n".join(json.dumps(r) for r in records)
    dbutils.fs.put(path, body, overwrite=True)
    print(f"Landed {name} with {len(records)} record(s)")

def show_landing():
    files = [f.name for f in dbutils.fs.ls(SOURCE_PATH)]
    print("Files in landing:", files)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. The Auto Loader read+write function
# MAGIC
# MAGIC Key options:
# MAGIC - `cloudFiles.format` – the input file format (`json` here).
# MAGIC - `cloudFiles.schemaLocation` – where the inferred/evolved schema is stored (`_schemas` folder).
# MAGIC - `cloudFiles.inferColumnTypes` – infer real types instead of reading everything as strings.
# MAGIC - `cloudFiles.schemaEvolutionMode` – defaults to `addNewColumns`; shown explicitly for clarity.
# MAGIC - `_rescued_data` – auto-added column that catches anything that doesn't fit the schema.
# MAGIC
# MAGIC We use `trigger(availableNow=True)`: process everything available right now, then stop —
# MAGIC the Databricks-recommended way to run Auto Loader as an incremental batch job.

# COMMAND ----------

def run_autoloader(evolution_mode: str = "addNewColumns"):
    """Reads new files from SOURCE_PATH and appends them to the Delta table.
       Returns when all currently-available files are processed (availableNow)."""
    stream = (
        spark.readStream
            .format("cloudFiles")
            .option("cloudFiles.format", "json")
            .option("cloudFiles.schemaLocation", SCHEMA_PATH)
            .option("cloudFiles.inferColumnTypes", "true")
            .option("cloudFiles.schemaEvolutionMode", evolution_mode)
            .load(SOURCE_PATH)
    )

    query = (
        stream.writeStream
            .option("checkpointLocation", CHKPT_PATH)   # RocksDB / idempotency lives here
            .option("mergeSchema", "true")              # allow the Delta sink to widen
            .trigger(availableNow=True)
            .toTable(TABLE)                             # write to a UC managed Delta table
    )
    query.awaitTermination()
    print("Run complete.")

def show_table():
    display(spark.table(TABLE).orderBy("order_id"))

def row_count():
    print("Rows in table:", spark.table(TABLE).count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## ── DAY 1 ── First file arrives → schema is inferred
# MAGIC Auto Loader samples the file (up to **50 GB or 1,000 files, whichever first**), stores the
# MAGIC schema in `_schema`, writes rows to the table, and records the file as processed in RocksDB.

# COMMAND ----------

land_file("orders_2026-05-28.json", [
    {"order_id": 1, "amount": 120.50, "customer": "Asha"},
    {"order_id": 2, "amount": 75.00,  "customer": "Ben"},
])
show_landing()

# COMMAND ----------

run_autoloader()
row_count()           # expect 2
show_table()

# COMMAND ----------

# MAGIC %md
# MAGIC **Look at the schema location** — Auto Loader wrote the inferred schema here.

# COMMAND ----------

print(dbutils.fs.head(dbutils.fs.ls(f"{SCHEMA_PATH}/_schemas")[0].path))

# COMMAND ----------

# MAGIC %md
# MAGIC ## ── DAY 2 ── A second file → old file skipped (idempotency / exactly once)
# MAGIC RocksDB already knows `orders_2026-05-28.json` is processed, so it is ignored.
# MAGIC Only the new file is read.

# COMMAND ----------

land_file("orders_2026-05-29.json", [
    {"order_id": 3, "amount": 49.99,  "customer": "Chen"},
    {"order_id": 4, "amount": 210.00, "customer": "Dev"},
])
show_landing()

# COMMAND ----------

run_autoloader()
row_count()           # expect 4 — only the 2 NEW rows were added
show_table()

# COMMAND ----------

# MAGIC %md
# MAGIC ## ── DAY 3 ── A file with a NEW column (`email`) → stream stops
# MAGIC With the default `addNewColumns` mode, Auto Loader:
# MAGIC 1. infers the new column,
# MAGIC 2. **appends it to the schema location**, then
# MAGIC 3. stops with `UnknownFieldException` (the file is **not** written yet).
# MAGIC
# MAGIC We wrap it in try/except so the notebook keeps running and you can see the exception.

# COMMAND ----------

land_file("orders_2026-05-30.json", [
    {"order_id": 5, "amount": 15.25, "customer": "Eve", "email": "eve@example.com"},
])
show_landing()

# COMMAND ----------

try:
    run_autoloader()                       # default addNewColumns mode
except Exception as e:
    name = type(e).__name__
    print(f"Stream stopped as expected: {name}")
    print(str(e)[:600])

# COMMAND ----------

# MAGIC %md
# MAGIC Notice the row count is still 4 — Day 3's row hasn't landed in the table yet.
# MAGIC But the **schema location now includes `email`** (it was updated *before* the error).

# COMMAND ----------

row_count()           # still 4
print(dbutils.fs.head(dbutils.fs.ls(f"{SCHEMA_PATH}/_schemas")[-1].path))   # newest schema has 'email'

# COMMAND ----------

# MAGIC %md
# MAGIC ## ── DAY 3 (restart) ── Self-heal
# MAGIC Just **re-run** the stream. Auto Loader reloads the updated schema from `_schema`,
# MAGIC processes the new file, and continues normally. In a Lakeflow Job this restart is automatic.

# COMMAND ----------

run_autoloader()
row_count()           # expect 5
show_table()          # 'email' column now present; older rows show null

# COMMAND ----------

# MAGIC %md
# MAGIC # Part B — `rescue` mode (the alternative to `addNewColumns`)
# MAGIC Part A above used the **default `addNewColumns`** mode: new columns become real columns in
# MAGIC the table, but the stream stops with `UnknownFieldException` and needs a restart.
# MAGIC
# MAGIC In **`rescue`** mode the behavior is different:
# MAGIC - The stream **does not stop** on a new column.
# MAGIC - Unknown/extra columns are captured in the `_rescued_data` column (a **STRING** holding JSON).
# MAGIC - The destination table's columns never change, so **no `mergeSchema` is needed**.
# MAGIC
# MAGIC We use fresh, separate paths so Part A stays untouched.

# COMMAND ----------

# Separate paths + table for the rescue demo (keeps Part A intact)
SOURCE_R = f"{VOLUME_ROOT}/landing_rescue"
SCHEMA_R = f"{VOLUME_ROOT}/_schema_rescue"
CHKPT_R  = f"{VOLUME_ROOT}/_checkpoint_rescue"
TABLE_R  = f"{CATALOG}.{SCHEMA}.orders_bronze_rescue"

for p in (SOURCE_R, SCHEMA_R, CHKPT_R):
    dbutils.fs.rm(p, True)
dbutils.fs.mkdirs(SOURCE_R)
spark.sql(f"DROP TABLE IF EXISTS {TABLE_R}")
print("Rescue demo paths ready.")

# COMMAND ----------

import json

def land_rescue(name: str, records: list):
    dbutils.fs.put(f"{SOURCE_R}/{name}", "\n".join(json.dumps(r) for r in records), overwrite=True)
    print("Landed", name)

def run_rescue():
    """Same as run_autoloader, but schemaEvolutionMode = 'rescue' and NO mergeSchema."""
    stream = (
        spark.readStream
            .format("cloudFiles")
            .option("cloudFiles.format", "json")
            .option("cloudFiles.schemaLocation", SCHEMA_R)
            .option("cloudFiles.inferColumnTypes", "true")
            .option("cloudFiles.schemaEvolutionMode", "rescue")   # <-- the key difference
            .load(SOURCE_R)
    )
    query = (
        stream.writeStream
            .option("checkpointLocation", CHKPT_R)
            # NOTE: no .option("mergeSchema", "true") — rescue never changes the table's columns
            .trigger(availableNow=True)
            .toTable(TABLE_R)
    )
    query.awaitTermination()
    print("Rescue run complete.")

# COMMAND ----------

# DBTITLE 1,Cell 29
# Step 1: Land the INITIAL file only — schema is inferred from this.
land_rescue("o1.json", [
    {"order_id": 1, "amount": 120.50, "customer": "Asha"},
    {"order_id": 2, "amount": 75.00,  "customer": "Ben"},
])

# Run once to lock in the schema (amount, customer, order_id only)
run_rescue()
print("Schema inferred from o1.json — no discount/payment_method yet.")
display(spark.table(TABLE_R).orderBy("order_id"))

# COMMAND ----------

# DBTITLE 1,Cell 30
# Step 2: Land the EVOLVED file (adds discount + payment_method).
# These columns are unknown to the already-inferred schema, so they go into _rescued_data.
land_rescue("o2.json", [
    {"order_id": 5, "amount": 15.25, "customer": "Eve",
     "discount": 5.0, "payment_method": "card"},
])

run_rescue()   # stream does NOT stop — rescue mode silently captures the extras
display(spark.table(TABLE_R).orderBy("order_id"))    # _rescued_data now populated for order 5

# COMMAND ----------

# MAGIC %md
# MAGIC ### Extract columns out of `_rescued_data`
# MAGIC `_rescued_data` is a **STRING** containing JSON (the rescued fields + the source file path).
# MAGIC It only *looks* like a struct — so parse it with `from_json` and a schema you define, then
# MAGIC pick the fields you actually need.

# COMMAND ----------

from pyspark.sql.functions import from_json, col
from pyspark.sql.types import StructType, StructField, DoubleType, StringType

rescued_schema = StructType([
    StructField("discount",       DoubleType(), True),
    StructField("payment_method", StringType(), True),
])

parsed = (
    spark.table(TABLE_R)
        .withColumn("r", from_json(col("_rescued_data"), rescued_schema))
        .withColumn("discount",       col("r.discount"))
        .withColumn("payment_method", col("r.payment_method"))
        .drop("r")
)
display(parsed.orderBy("order_id"))   # discount/payment_method only populated for the evolved row

# COMMAND ----------

# MAGIC %md
# MAGIC ### addNewColumns vs rescue — what you just saw
# MAGIC
# MAGIC | | `addNewColumns` (Part A) | `rescue` (Part B) |
# MAGIC |---|---|---|
# MAGIC | Stream stops on new column? | Yes → `UnknownFieldException`, restart | No |
# MAGIC | New columns go to… | real columns in the table | `_rescued_data` (STRING/JSON) |
# MAGIC | Need `mergeSchema` on the sink? | **Yes** | No |
# MAGIC | Access new fields | direct columns | `from_json` on demand |

# COMMAND ----------

# MAGIC %md
# MAGIC ### What each location holds (recap)
# MAGIC
# MAGIC | Location | Option | Holds |
# MAGIC |---|---|---|
# MAGIC | Checkpoint | `checkpointLocation` | Stream offsets + **RocksDB** file-tracking → idempotency |
# MAGIC | Schema | `cloudFiles.schemaLocation` | The `_schemas` folder with the inferred/evolved schema |
# MAGIC
# MAGIC **Gotchas:** deleting the checkpoint reprocesses everything; `addNewColumns` stops once per new
# MAGIC column (design for restart); a provided schema defaults to `none` (use schema *hints* to keep
# MAGIC evolution); check `_rescued_data` so you don't silently lose mismatched records.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cleanup (optional)
# MAGIC Uncomment to remove everything this demo created.

# COMMAND ----------

spark.sql(f"DROP TABLE IF EXISTS {TABLE}")
dbutils.fs.rm(VOLUME_ROOT, True)
spark.sql(f"DROP VOLUME IF EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")
print("Cleaned up.")

# COMMAND ----------


