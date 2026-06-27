# Repository Guidelines

## Project Structure & Module Organization

This repository is a local learning and interview-preparation workspace for Databricks, Spark, Delta Lake, and networking topics. Major content areas are organized by subject:

- `DBX DE/`, `DBX Networking/`, `DBX_Delta_Optimization/`, and `Spark/` contain lesson modules, generated `index.html` pages, supporting `lesson.md` files, and hands-on examples.
- `spark-experiments-main/` contains Spark performance notebooks under `spark/`, diagrams and reference material under `docs/`, and sample CSV/Parquet data under `data/`.
- `.databricks/` and `databricks.yml` hold Databricks Asset Bundle configuration. Treat generated or environment-specific bundle output as local unless intentionally shared.

## Build, Test, and Development Commands

There is no single project build system at the repository root. Use targeted commands for the area you change:

- `databricks bundle validate` checks the Databricks bundle configuration.
- `python path/to/demo.py` runs standalone PySpark or Databricks-oriented demo scripts when dependencies and credentials are available.
- `jupyter lab` opens notebooks such as `spark-experiments-main/spark/5_0_partitioning.ipynb` for interactive validation.
- Open static lesson pages directly, for example `DBX Networking/index.html`, to inspect rendered HTML.

## Coding Style & Naming Conventions

Keep Python examples readable and notebook-friendly: 4-space indentation, descriptive snake_case variables, and small transformations with clear intermediate DataFrame names. Use uppercase SQL keywords in `.sql` files. Keep lesson directories numbered with a leading ordinal and kebab-case topic name, such as `07-lakeflow-jobs-orchestration/`. Preserve existing file naming when editing bootcamp notebooks or generated lesson assets.

## Testing Guidelines

No automated test suite is currently defined. Validate changes by running the specific notebook, script, SQL statement, or Terraform example you touched. For Spark examples, confirm the Spark session starts, sample data paths resolve, and displayed row counts or query plans still match the lesson narrative. For HTML/Markdown lessons, preview the page and check links, images, and code blocks.

## Commit & Pull Request Guidelines

This root directory does not expose git history, so no repository-specific commit pattern can be inferred. Use concise, imperative commit messages such as `Update Delta optimization lesson` or `Fix Spark skew notebook paths`. Pull requests should describe the affected lesson or demo, list validation performed, call out Databricks workspace or cluster assumptions, and include screenshots when rendered pages or diagrams change.

## Security & Configuration Tips

Do not commit workspace tokens, cloud credentials, personal cluster IDs, or exported notebooks containing secrets. Prefer placeholders in examples and document required environment variables or Databricks profile names beside the command that needs them.
