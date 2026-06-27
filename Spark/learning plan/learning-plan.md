# DBX PySpark Performance — Learning Plan

An eleven-lesson path through how Apache Spark actually runs your PySpark — the
execution model, joins, memory, runtime adaptivity, caching, pruning, skew, shared
variables, GC, and bucketing. Built from the official **Apache Spark** docs and the
**Azure Databricks** docs (verified June 2026). Each lesson is a self-contained
interactive HTML page + a markdown companion + a runnable Databricks notebook.

> **The one-line goal:** finish able to *design* a fast PySpark job, *defend* the
> tuning choices in an interview, and *debug* a slow one from the plan and the Spark UI.

## How to use this plan

- Go in order — each lesson builds on the last (it's one continuous story).
- For each lesson: read the markdown or open the interactive page, play with the
  diagrams, then run the notebook on a small cluster and **watch the engine react** in
  the Spark UI (the SQL DAG, Exchange nodes, spill, GC time, task-time skew) and in
  `df.explain(mode="formatted")`.
- After each lesson, answer its self-check questions below without looking.

## The story arc

> Your code becomes **jobs → stages → tasks** → **joins** and wide ops trigger the
> **shuffle** → **memory** decides what runs, spills, or OOMs → **AQE** adapts the plan
> at runtime → **caching** reuses work → **pruning** reads less → **skew** is the
> recurring villain (salting/hints) → **shared variables** move data efficiently →
> **GC** and **bucketing** are the last-mile levers.

```mermaid
flowchart LR
  A[01 Architecture<br/>driver · executors · shuffle] --> B[02 Joins]
  B --> C[03 Driver memory]
  C --> D[04 Executor memory & spill]
  D --> E[05 AQE]
  E --> F[06 Cache & persist]
  F --> G[07 Partition pruning & DPP]
  G --> H[08 Skew: salting & hints]
  H --> I[09 Broadcast vars & accumulators]
  I --> J[10 Garbage collection]
  J --> K[11 Bucketing]
```

## Lessons, timings & self-check

| # | Lesson | Focus | Est. time | Self-check question |
| --- | --- | --- | --- | --- |
| 01 | Spark architecture & the execution model | How a job runs on a cluster | 35 min | What's the difference between client and cluster deploy modes, and what turns a transformation into a shuffle? |
| 02 | Joins: Sort-Merge vs Shuffle-Hash vs Broadcast | The three join strategies | 40 min | When does Spark pick a broadcast join, and how do you confirm it in `.explain()`? |
| 03 | Driver memory & driver OOM | The one node that can sink the job | 30 min | Name three things that live on the driver, and how `collect()` causes a driver OOM. |
| 04 | Executor memory: unified model, spill & OOM | Where the data lives | 45 min | What are the regions of the unified memory model, and why can execution evict storage but not vice-versa? |
| 05 | Adaptive Query Execution (AQE) | The plan that rewrites itself | 35 min | What three things does AQE do at runtime, and since which Spark version is it on by default? |
| 06 | Cache & persist | Stop recomputing a DataFrame | 30 min | What's the default storage level for a DataFrame vs an RDD, and when should you NOT cache? |
| 07 | Partition pruning & dynamic partition pruning | Read fewer files | 35 min | What's the difference between static pruning and DPP, and what does DPP need to fire? |
| 08 | Data skew: salting & SQL hints | When one key has all the rows | 40 min | How do you spot skew in the Spark UI, and how does salting a join key fix it? |
| 09 | Broadcast variables & accumulators | The two shared-variable types | 30 min | How is a broadcast *variable* different from a broadcast *join*, and why are accumulators only reliable inside actions? |
| 10 | Garbage-collection tuning | When the JVM pauses, tasks pause | 30 min | What causes a stop-the-world GC pause, and name three ways to reduce GC time. |
| 11 | Bucketing to eliminate the shuffle | Pre-shuffle once on write | 35 min | How do two bucketed tables join without an Exchange, and how is bucketing different from partitioning? |

Total: roughly **6 hours** of focused study, plus notebook runtime.

## The decision method to memorize

> **Slow Spark job?** Read the plan (`.explain()`) and the Spark UI first. Then, in order:
>
> 1. **Read less** — partition pruning / DPP, column pruning, predicate pushdown.
> 2. **Avoid or repair the shuffle** — broadcast the small side, bucket repeated joins,
>    let AQE coalesce post-shuffle partitions.
> 3. **Fix skew** — AQE skew join first, then salting for what's left.
> 4. **Reuse work** — cache only DataFrames reused across actions, with the right storage
>    level; `unpersist()` when done.
> 5. **Size memory & GC last** — understand the unified model, cure spill/OOM at the right
>    region (driver vs executor), tune off-heap and G1GC.
>
> Always **verify the change in the plan / Spark UI** — never assume.

## Key numbers worth memorizing (verified June 2026)

- **Broadcast threshold:** `spark.sql.autoBroadcastJoinThreshold` = **10 MB** (OSS); set `-1`
  to disable. Databricks AQE also uses a **30 MB** runtime switch
  (`spark.databricks.adaptive.autoBroadcastJoinThreshold`).
- **Shuffle partitions:** `spark.sql.shuffle.partitions` = **200** (OSS) / `auto` (Databricks).
- **Unified memory:** reserved **300 MiB**; `spark.memory.fraction` = **0.6**;
  `spark.memory.storageFraction` = **0.5**. Execution can evict storage; storage cannot evict execution.
- **Overhead:** `spark.executor.memoryOverheadFactor` = **0.10**, minimum **384 MB**.
  Python workers live outside the JVM heap (counted against overhead when unset).
- **Driver:** `spark.driver.memory` = **1g**; `spark.driver.maxResultSize` = **1g**.
- **AQE:** on by default since **Spark 3.2.0** (introduced 1.6 — not the same thing).
  Advisory partition size **64 MB**; skew factor **5** × median **and** > **256 MB**.
- **Cache defaults:** RDD `cache()` = **MEMORY_ONLY**; DataFrame `cache()` = **MEMORY_AND_DISK**.
  In PySpark, objects are always serialized.
- **DPP:** `spark.sql.optimizer.dynamicPartitionPruning.enabled` = **true** (since Spark 3.0).
- **Bucketing:** `spark.sql.sources.bucketing.enabled` = **true**;
  `spark.sql.bucketing.coalesceBucketsInJoin.enabled` = **false** (since 3.1); must `saveAsTable()`.
- **GC:** **G1GC** is the default since **Spark 4.0** (JDK 17); opt-in before via `-XX:+UseG1GC`.

## References (official Apache Spark + Azure Databricks docs)

- SQL performance tuning (AQE, joins, hints) — https://spark.apache.org/docs/latest/sql-performance-tuning.html
- Tuning (memory management, GC) — https://spark.apache.org/docs/latest/tuning.html
- Configuration (all config keys) — https://spark.apache.org/docs/latest/configuration.html
- RDD programming guide (persist, shared variables) — https://spark.apache.org/docs/latest/rdd-programming-guide.html
- SQL join/partitioning hints — https://spark.apache.org/docs/latest/sql-ref-syntax-qry-select-hints.html
- Cluster overview / deploy modes — https://spark.apache.org/docs/latest/cluster-overview.html
- Azure Databricks — Adaptive Query Execution — https://learn.microsoft.com/en-us/azure/databricks/optimizations/aqe
- Azure Databricks — compute / driver node — https://learn.microsoft.com/en-us/azure/databricks/compute/configure
