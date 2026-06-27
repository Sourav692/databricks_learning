# Databricks Connect

> **Topic 11.3 · Production Engineering — CI/CD with DABs** — enterprise deep-dive,
> interview-focused. Each sub-topic pairs the **mechanism** with a commented Python
> snippet. This is the topic-final subtopic — the consolidated Topic 11 notebook
> (`production_cicd_hands_on.py`) ties DABs + Git + Connect together.

## What it is

- **Databricks Connect** lets you run **DataFrame/Spark code from a local IDE**
  (VS Code, PyCharm, IntelliJ) or any app **against remote Databricks compute**.
- Since **DBR 13.0** it is built on the open **Spark Connect** protocol (gRPC): a
  thin local client builds DataFrame plans and ships them to the cluster/serverless,
  which executes and returns results.
- You code, debug, and unit-test in your IDE with native tooling — but the heavy
  compute and your governed data stay in Databricks.

**Analogy:** it's a **remote control for a powerful machine**. You hold the remote
(laptop/IDE) and press buttons (write DataFrame code); the actual work runs on the
big machine in the data center (Databricks compute), and results come back.

## Why it matters

- Some teams prefer **IDE-native development** (debuggers, linters, unit tests, git)
  over notebooks — Databricks Connect gives that **without moving data to the laptop**.
- It's a key piece of the **"develop like software" CI/CD story** alongside DABs and
  Git folders — a common "how do you develop/test Databricks code locally?" question.

**Real-world use case:** an engineer builds a transformation in **VS Code** with
breakpoints and pytest, running it against a **dev cluster** via Databricks Connect;
once green, it's committed (Git folders) and deployed (DABs) — never hand-pasted into
a notebook.

---

## How it works — deep dive

### 1. The Spark Connect architecture (v2)

**Mechanism:** modern Databricks Connect (**DBR 13.0+**, distributed as the
`databricks-connect` PyPI package) is a **thin client** that speaks the **Spark
Connect gRPC** protocol. DataFrame/SQL operations are serialized as an unresolved
logical plan and executed on the remote Spark driver; only your general app code runs
locally.

**Why:** the decoupled client/server design means the client is lightweight and
version-independent of your local Spark — a clean break from the legacy
(pre-DBR-13) monolithic Databricks Connect.

**Trade-off:** the legacy and v2 packages are **not** the same; ensure you're on the
Spark-Connect-based `databricks-connect` (13.x+), not the old one.

```python
# The modern client: pip install "databricks-connect"
# It builds DataFrame plans locally and runs them remotely over Spark Connect (gRPC).
from databricks.connect import DatabricksSession   # the v2 entry point
```

### 2. Install & version-match to the cluster

**Mechanism:** install a `databricks-connect` version that **matches the target
cluster's DBR** (available DBR **13.3 LTS+**). Use an isolated venv/Poetry env so it
doesn't clash with a local PySpark install.

**Why:** the client and server share a wire protocol tied to a runtime version —
matching avoids subtle plan/serialization errors.

**Trade-off:** every target DBR may need its own pinned client version; teams often
standardize on one DBR per environment to keep this simple.

```bash
# Isolated env; pin the client to the cluster's DBR (e.g. 15.4 LTS).
python -m venv .venv && source .venv/bin/activate
pip install --upgrade "databricks-connect==15.4.*"   # match the cluster DBR
# ⚠️ verify — confirm the exact version against your cluster's runtime.
```

### 3. Build a `DatabricksSession`

**Mechanism:** `DatabricksSession.builder` replaces `SparkSession.builder`.
`getOrCreate()` reuses a session (or builds one from your default config);
`.serverless()` targets serverless; `.clusterId(...)` targets a specific cluster;
`.remote("sc://...")` takes an explicit Spark Connect connection string.

**Why:** one builder, several ways to point at compute — config-driven for dev, or
explicit for scripts/CI.

**Trade-off:** serverless Spark Connect sessions **expire after 10 min idle** (the
client auto-closes them) — fine for dev, not for long-lived pinning.

```python
from databricks.connect import DatabricksSession

# A) default config (profile/env) — simplest for local dev:
spark = DatabricksSession.builder.getOrCreate()

# B) target serverless or a specific cluster explicitly:
spark = DatabricksSession.builder.serverless().getOrCreate()
# spark = DatabricksSession.builder.clusterId("0712-...-abcd").getOrCreate()
```

### 4. Authentication — profiles & config

**Mechanism:** Connect uses the standard Databricks SDK auth. With a configured
`.databrickscfg` **profile** (host + token/OAuth) or env vars, `getOrCreate()` picks
it up automatically. For a fully explicit connection you can pass a Spark Connect
string to `.remote(...)`.

**Why:** the same auth model as the CLI/SDK — no bespoke credential handling, and
secrets stay out of code.

**Trade-off:** an explicit token in a `.remote("sc://...;token=...")` string is handy
for a one-off but should never be committed; prefer a profile/OAuth.

```python
# Explicit Spark Connect connection string (verified form):
spark = DatabricksSession.builder.remote(
    f"sc://{workspace_host}:443/;token={token};x-databricks-cluster-id={cluster_id}"
).getOrCreate()
# Prefer a .databrickscfg profile / OAuth so no token lives in code.
```

### 5. What runs locally vs remotely

**Mechanism:** general Python (loops, app logic, your test harness) runs **locally**;
the moment you touch a **DataFrame/SQL** operation, it becomes a plan executed on the
**remote** cluster against your governed UC data. UDFs are serialized from the client
and run on the cluster.

**Why:** you get local control flow + IDE tooling, with Spark scale and data
locality remote.

**Trade-off:** **UDF dependencies must exist on the cluster**, not just locally —
split your requirements (general = local, UDF libs = installed on the cluster).

```python
# local control flow + remote Spark execution in one script:
for region in ["West", "East"]:                 # runs LOCALLY
    df = spark.read.table("prod.sales.orders")  # executes on the CLUSTER
    total = df.filter(df.region == region).agg({"amount": "sum"})  # remote plan
    print(region, total.collect()[0][0])        # result returned to the IDE
```

### 6. Local testing & debugging — and the limits

**Mechanism:** because it's a normal Python process, you can set **breakpoints**, run
**pytest**, and use linters/type-checkers against real remote data — then ship the
same code via DABs.

**Why:** testable, debuggable Spark code is what makes "develop like software"
possible on Databricks.

**Trade-off:** not every notebook-only feature maps 1:1 (some `dbutils`/`display`
behaviors differ); it's a **dev/app** tool — scheduled prod still runs as
**Jobs/pipelines** deployed via DABs.

```python
# A pytest unit test that exercises a transform against the remote session:
def test_revenue_by_region(spark):                # spark = DatabricksSession fixture
    out = revenue_by_region(spark.read.table("prod.sales.orders"))
    assert "region" in out.columns and out.count() > 0
```

---

## Uses, edge cases & limitations

- **Uses:** local IDE development/debugging, unit-testing transformations, building
  data apps that talk to Databricks, IDE-native git workflows.
- **Edge cases:**
  - **Version match matters** — the Connect client should match the target **DBR**;
    mismatches cause errors.
  - **Dependency split** — UDF libraries must exist on the cluster, not just locally.
  - **Serverless sessions expire after 10 min idle** — re-create as needed.
- **Limitations:** it executes **DataFrame/Spark Connect** workloads — not every
  notebook-only feature (some `dbutils`/display behaviors) maps 1:1; it's a **dev/app**
  tool, while scheduled prod work runs as **Jobs/pipelines** (deployed via DABs).

## Common gotchas

- ❌ **Version mismatch** between the Connect client and the cluster's DBR.
- ❌ Installing the **legacy** pre-DBR-13 package instead of the Spark-Connect-based
  `databricks-connect` (13.x+).
- ❌ Expecting a **UDF's local libraries** to be present on the cluster — install them
  there too.
- ❌ Thinking it runs Spark **on your laptop** — compute is remote; only app code is local.
- ❌ Using it as the *deployment* mechanism — it's for **development**; deploy with **DABs**.

## References

- [Databricks Connect — docs](https://docs.databricks.com/aws/en/dev-tools/databricks-connect/)
- [Databricks Connect for Python](https://docs.databricks.com/aws/en/dev-tools/databricks-connect/python/)
- [Advanced usage (connection string)](https://docs.databricks.com/aws/en/dev-tools/databricks-connect/advanced)
- [Databricks Connect release notes](https://docs.databricks.com/aws/en/release-notes/dbconnect/)
- [Spark Connect](https://docs.databricks.com/aws/en/spark/connect)
