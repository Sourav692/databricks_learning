# Schedules, Triggers, CRON, Retries, Monitoring & Notifications

> **Topic 7.3 · Lakeflow Jobs — orchestration** — enterprise deep-dive,
> interview-focused. Runnable demo code lives in the consolidated Topic 7 notebook
> in the topic folder; the bundle snippets below are the teaching units.

## What it is

How a job **starts**, **recovers**, and **tells you what happened**:

- **Triggers** — what kicks off a run: **Scheduled** (time-based, **CRON**),
  **File arrival**, **Table update**, **Continuous** (auto-restart), **Periodic**,
  plus **manual** "Run now".
- **Retries & timeouts** — auto-retry failed tasks; cap runtime with timeouts;
  **re-run only the failed subset** instead of the whole job.
- **Monitoring** — the runs UI / run history (DAG, durations, logs, event log).
- **Notifications & health** — email / webhook (Slack, PagerDuty) on
  start / success / failure / **duration-SLA breach**.

**Analogy:** triggers are the **alarm clock** (time) or **doorbell** (an event)
that starts the job; retries are **"try the key again if the door sticks"**;
notifications are the **text message** telling you it worked or broke.

## Why it matters

- A pipeline that doesn't **start automatically**, **recover from blips**, and
  **alert on failure** isn't production-ready. This is the ops layer.
- "Run a job when files land" → **file-arrival trigger**; "every day at 2am" →
  **CRON schedule**; "alert on failure" → **notifications** — classic interview Qs.

**Real-world use case:** a gold pipeline runs on a **CRON** schedule, **retries**
transient failures twice, **re-runs only failed tasks** on manual fix, and
**emails + Slack-webhooks** the on-call if the run fails or runs too long.

---

## How it works — deep dive

### 1. Schedule as code — Quartz CRON

Databricks uses **Quartz cron** (`quartz_cron_expression`) — **6 fields**
(`seconds minutes hours day-of-month month day-of-week`), *not* the 5-field Unix
cron. Always set `timezone_id`.

```yaml
resources:
  jobs:
    nightly_etl:
      schedule:
        quartz_cron_expression: "0 0 2 * * ?"   # 02:00 every day (sec min hr dom mon dow)
        timezone_id: "America/New_York"          # DST-aware; use "UTC" to avoid DST drift
        pause_status: UNPAUSED                    # or PAUSED
```

### 2. Event & periodic triggers

```yaml
      # Fire when new files land in a UC location (great upstream of Auto Loader)
      trigger:
        file_arrival:
          url: "/Volumes/main/raw/orders/"

      # ...or run on a fixed interval (alternative to cron)
      # trigger:
      #   periodic: { interval: 6, unit: HOURS }
```

- Other options: **table-update** triggers (run when a source table changes) and
  **continuous** (`continuous: { pause_status: UNPAUSED }`) for always-on jobs.

### 3. Reliability — retries, timeouts, concurrency

```yaml
      timeout_seconds: 7200            # job-level kill switch (2h)
      max_concurrent_runs: 1           # default 1; raise only if overlap is safe
      tasks:
        - task_key: ingest
          max_retries: 2                       # retry transient failures twice
          min_retry_interval_millis: 60000     # wait 60s between retries
          retry_on_timeout: true               # also retry if the task times out
          notebook_task: { notebook_path: ../src/ingest.py }
```

- **Retries fix *transient* failures** (a flaky API, a node loss) — not
  deterministic bugs (bad code). On a fix, **re-run only the failed tasks** (repair
  run), not the whole job.

### 4. Notifications & health (SLA)

```yaml
      email_notifications:
        on_failure: ["oncall@acme.com"]
        on_duration_warning_threshold_exceeded: ["oncall@acme.com"]
      webhook_notifications:
        on_failure: [{ id: "${var.slack_webhook_id}" }]   # Slack / PagerDuty destination
      health:
        rules:
          - metric: RUN_DURATION_SECONDS      # SLA: warn if the run exceeds 1h
            op: GREATER_THAN
            value: 3600
```

- Configure **webhook destinations** once (workspace admin) and reference them.
  Health rules turn "it's slow" into an **alert**, not a silent miss.

### 5. Monitoring

- The **Runs** tab shows each run's DAG, per-task duration, logs, and outcome;
  the **event log** and **system tables** (`system.lakeflow.*`) let you trend job
  health and cost across the workspace.

---

## Uses, edge cases & limitations

- **Uses:** scheduled batch, event-driven ingestion, always-on jobs, alerting,
  partial re-runs after a fix, duration SLAs.
- **Edge cases:**
  - **Default `max_concurrent_runs` = 1** — a long run can cause the next scheduled
    run to **skip/queue**; raise only if overlap is safe.
  - **Quartz CRON is in the job's timezone** — DST and TZ mistakes cause "ran an
    hour off" bugs; use `UTC` to avoid DST drift.
  - File-arrival triggers watch **UC locations**, not arbitrary paths.
- **Limitations:** continuous jobs keep compute running (cost); **retries don't fix
  deterministic failures** (bad code) — only transient ones.

## Common gotchas

- ❌ Using **5-field Unix cron** — Databricks uses **6-field Quartz**
  (`quartz_cron_expression`, e.g. `0 0 2 * * ?`).
- ❌ Re-running the **whole job** after one task fails — **re-run only the failed
  tasks** (repair run).
- ❌ Wrong **CRON timezone** → off-by-hours schedules (watch DST).
- ❌ Expecting overlapping runs by default — `max_concurrent_runs` is 1 unless raised.
- ❌ No **failure notification / health rule** → silent failures nobody notices.
- ❌ Relying on retries to mask a **deterministic** bug.

## References

- [Triggers & schedules — docs](https://docs.databricks.com/aws/en/jobs/triggers)
- [Run jobs on a schedule (Quartz cron)](https://docs.databricks.com/aws/en/jobs/scheduled)
- [Retries & notifications](https://docs.databricks.com/aws/en/jobs/notifications)
- [Monitor jobs / runs & health rules](https://docs.databricks.com/aws/en/jobs/monitor)
