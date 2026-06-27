# Job Control Flow — If/Else, For-Each, Task Values

> **Topic 7.2 · Lakeflow Jobs — orchestration** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 7
> notebook (built at the last subtopic); the bundle/Python snippets below are the
> teaching units.

## What it is

Ways to make a job **dynamic** instead of a fixed straight line:

- **If/else task** (`condition_task`) — branch: run different downstream tasks
  based on a condition (compare task values / job params with `==,!=,>,>=,<,<=`).
- **For-each task** (`for_each_task`) — loop: run a nested task **once per item** in
  a list, passing different params each iteration.
- **Task values** — `dbutils.jobs.taskValues.set/get` pass small data **between
  tasks** (no external storage).
- **Run-if** (`run_if`) — control when a task runs based on upstream outcomes.

**Analogy:** if a job (7.1) is a recipe, control flow adds **"if the dough is too
dry, add water"** (if/else), **"repeat for each tray"** (for-each), a **sticky note
passed between cooks** (task values), and **"only plate if the main didn't burn"**
(run-if).

## Why it matters

- Production pipelines need **branching, looping, and recovery** — not just a fixed
  sequence. Control flow is how one job handles many cases.
- "How would you process N tables in one job?" → **for-each**; "skip publish if
  validation failed?" → **if/else + run-if** — common interview probes.

**Real-world use case:** a job uses **for-each** to ingest 12 source tables in
parallel, sets a row-count **task value**, then an **if/else** publishes to gold
only if counts look sane — and a **run-if "at least one failed"** task fires an alert.

---

## How it works — deep dive

### 1. Task values — pass small data between tasks

```python
# upstream task ("ingest"): publish a value
dbutils.jobs.taskValues.set(key="row_count", value=1234)

# downstream task: read it
n = dbutils.jobs.taskValues.get(taskKey="ingest", key="row_count", default=0)
```

- Reference a task value **in a condition / param** with
  `{{tasks.ingest.values.row_count}}`. Values are **small** (counts/flags) — for
  big data, write a table and pass a *pointer*.

### 2. If/else — `condition_task`

The condition task evaluates `left <op> right`; downstream tasks attach to the
`true` or `false` **outcome**.

```yaml
tasks:
  - task_key: gate
    condition_task:
      op: GREATER_THAN                 # EQUAL_TO, NOT_EQUAL, GREATER_THAN_OR_EQUAL, LESS_THAN, ...
      left: "{{tasks.ingest.values.row_count}}"
      right: "100"
  - task_key: publish_gold             # runs only when gate == true
    depends_on: [{ task_key: gate, outcome: "true" }]
    notebook_task: { notebook_path: ../src/publish.py }
  - task_key: quarantine               # runs only when gate == false
    depends_on: [{ task_key: gate, outcome: "false" }]
    notebook_task: { notebook_path: ../src/quarantine.py }
```

### 3. For-each — `for_each_task`

Loop a nested task over a list (often from a task value); iterations can run in
parallel. Reference the current item with `{{input}}`.

```yaml
tasks:
  - task_key: ingest_all
    for_each_task:
      inputs: '["sales","orders","users"]'   # or a task value / job param
      concurrency: 4                          # bound the parallelism
      task:
        task_key: ingest_one
        notebook_task:
          notebook_path: ../src/ingest_table.py
          base_parameters: { table: "{{input}}" }   # current item
```

### 4. Run-if — conditional execution on outcomes

`run_if` decides whether a task runs given its dependencies' results:

| `run_if` value | Runs when… | Use for |
|---|---|---|
| `ALL_SUCCESS` *(default)* | every dependency succeeded | normal flow |
| `AT_LEAST_ONE_SUCCESS` | ≥1 dependency succeeded | best-effort merge |
| `NONE_FAILED` | none failed (skips allowed) | tolerant continue |
| `ALL_DONE` | all finished (success or fail) | **cleanup** |
| `AT_LEAST_ONE_FAILED` | ≥1 dependency failed | **alerting** |
| `ALL_FAILED` | all failed | fallback path |

```yaml
  - task_key: alert_on_failure
    depends_on: [{ task_key: ingest_all }, { task_key: publish_gold }]
    run_if: AT_LEAST_ONE_FAILED          # fire only when something upstream failed
    notebook_task: { notebook_path: ../src/alert.py }
```

---

## Uses, edge cases & limitations

- **Uses:** data-driven branching, fan-out over many inputs (for-each), passing
  counts/flags between tasks, conditional alerting/cleanup with run-if.
- **Edge cases:**
  - **Task values are small** — for big data, write to a table and pass a pointer,
    not the data itself.
  - For-each parallelism is **bounded** (`concurrency` + workspace limits) — huge
    lists need batching.
  - **`run_if` defaults to `ALL_SUCCESS`** — a cleanup/alert task won't run after a
    failure unless you set `ALL_DONE` / `AT_LEAST_ONE_FAILED`.
- **Limitations:** control flow lives at the **job/task** level — fine-grained row
  logic belongs in the task code/SDP, not the DAG. Over-using if/else makes a job
  hard to read; sometimes separate jobs are clearer.

## Common gotchas

- ❌ Stuffing **large data** into a task value — it's for small values; use a table.
- ❌ Forgetting **run_if** defaults to `ALL_SUCCESS` — your cleanup/alert task won't
  run after a failure unless set to `AT_LEAST_ONE_FAILED` / `ALL_DONE`.
- ❌ Using a notebook `if` in code when you need **task-level** branching (so the
  DAG/monitoring reflects it) — use the **`condition_task`**.
- ❌ Unbounded **for-each** over thousands of items without batching / `concurrency`.

## References

- [Control flow in jobs — docs](https://docs.databricks.com/aws/en/jobs/control-flow)
- [If/else (condition) task](https://docs.databricks.com/aws/en/jobs/conditional-tasks)
- [For each task](https://docs.databricks.com/aws/en/jobs/for-each)
- [Share information between tasks (task values)](https://docs.databricks.com/aws/en/jobs/task-values)
