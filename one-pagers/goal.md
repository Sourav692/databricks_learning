# Goal — Databricks Tech Peer Interview One-Pagers

## Goal
Produce a library of clean, single-page HTML **one-pagers** — one per Databricks
feature — for fast revision before the tech peer interview. Each is a 30-second
recall aid: key concepts + ONE small snippet + ONE interactive diagram. **Not**
a deep dive.

## Definition of done (per one-pager)
A page passes only if it satisfies the **databricks-one-pager** skill's contract
and verification checklist:
- Built with the `databricks-one-pager` skill, from `references/style-template.html`.
- Grounded in official docs (`docs.databricks.com`) — every fact cited, GA/Preview
  correct, rebrands handled (Lakeflow ← DLT/Workflows).
- The nine blocks, in order: Hero → In one line → Why it matters → Core concepts
  (3–6) → Interactive diagram (1) → Minimal example (≤15 lines) → Gotchas & limits
  → Interview soundbites → References.
- Self-contained `.html` at `one-pagers/<NN>-<slug>.html`; whole page ~1–1.5 pages.
- A card for it added to `one-pagers/index.html`.

## Definition of done (overall)
- Every backlog item below is `[x]`.
- `one-pagers/index.html` links all pages and matches the house style.

## Per-iteration instruction (what `/loop` runs)
> Read `one-pagers/goal.md`. Pick the **first `[ ]` (unbuilt)** item in the
> backlog. Invoke the **databricks-one-pager** skill to build it end-to-end
> (ground in the listed docs, fill the nine blocks, add the interactive diagram,
> run the verification checklist, update `one-pagers/index.html`). Then mark the
> item `[x]` here with the file path. Build exactly ONE per iteration. If every
> item is `[x]`, stop and report that the library is complete.

## Backlog (status-tracked — edit freely)

### Core platform set
- [x] 01 · Lakehouse & Medallion architecture — `01-lakehouse-medallion.html`
- [x] 02 · Unity Catalog — `02-unity-catalog.html`
- [x] 03 · Delta Lake (ACID, transaction log, time travel) — `03-delta-lake.html`
- [x] 04 · Auto Loader — `04-auto-loader.html`
- [x] 05 · Lakeflow Declarative Pipelines (DLT) — `05-lakeflow-declarative-pipelines.html`
- [x] 06 · Lakeflow Jobs (Workflows) — `06-lakeflow-jobs.html`
- [x] 07 · Spark architecture & execution — `07-spark-architecture.html`
- [x] 08 · Spark performance (AQE, shuffle, skew, caching) — `08-spark-performance.html`
- [x] 09 · Delta optimization & Liquid Clustering — `09-delta-optimization.html`
- [x] 10 · Structured Streaming — `10-structured-streaming.html`
- [x] 11 · Databricks SQL & Photon — `11-dbsql-photon.html`
- [x] 12 · Compute & cluster types — `12-compute-clusters.html`

### AI / ML & sharing
- [x] 13 · Mosaic AI Model Serving + MLflow — `13-model-serving-mlflow.html`
- [x] 14 · Vector Search — `14-vector-search.html`
- [x] 15 · Genie / AI-BI (natural-language to SQL) — `15-genie-aibi.html`
- [x] 16 · Delta Sharing & Marketplace — `16-delta-sharing.html`
- [x] 17 · Lakebase (managed Postgres / OLTP) — `17-lakebase.html`
- [x] 18 · Databricks Asset Bundles (DABs) — `18-asset-bundles.html`

### Agent & GenAI (added)
- [x] 19 · Agent Bricks — `19-agent-bricks.html`
- [x] 20 · Mosaic AI Agent Framework — `20-agent-framework.html`
- [x] 21 · GenAI evaluation (MLflow 3 / Agent Evaluation) — `21-genai-evaluation.html`
- [x] 22 · LLMOps on Databricks — `22-llmops.html`

### MLOps & Apps (added round 2)
- [x] 23 · MLOps on Databricks (classic ML lifecycle) — `23-mlops.html`
- [x] 24 · Authentication in Databricks Apps — `24-databricks-apps-auth.html`

> Doc pages to ground each item are in
> `.claude/skills/databricks-one-pager/references/curriculum.md`.
