# Git Integration — Databricks Git Folders

> **Topic 11.2 · Production Engineering — CI/CD with DABs** — enterprise deep-dive,
> interview-focused. Part of this is UI-driven, so each section pairs the
> **mechanism** with the **git / Databricks CLI / GitHub Actions** code that makes
> it automatable. The end-to-end CI/CD hands-on lives in the Topic 11 notebook (11.3).

## What it is

- **Databricks Git folders** (formerly **Repos**) is a **visual Git client + API
  built into the workspace** — clone a repo, branch, commit, pull, and push without
  leaving Databricks.
- Connects to **GitHub (incl. the Databricks GitHub app / OAuth), GitLab, Azure
  DevOps, Bitbucket, AWS CodeCommit** (+ self-managed/enterprise variants), via
  **OAuth** or a **personal access token (PAT)**.
- It's how your notebooks/files become **version-controlled source** — the
  foundation for code review, CI/CD, and where your **Asset Bundle** (`databricks.yml`)
  lives.

**Analogy:** Git folders is **"Track Changes + sync to the shared drive" built into
your editor**. You work in the workspace as usual, but every change is versioned and
can be branched, reviewed, and merged like real software.

## Why it matters

- Notebooks edited only in the workspace have **no history, no review, no rollback**.
  Git folders brings **software-engineering discipline** to DE.
- "How do you version-control and collaborate on Databricks code?" → **Git folders +
  a Git provider**, feeding **DABs** for deployment — the expected answer.

**Real-world use case:** a team clones the repo as a **Git folder**; each engineer
works on a **feature branch**, opens a PR on GitHub for review, and on merge a
**GitHub Action** authenticates as a **service principal** and runs `databricks
bundle deploy` — code → review → prod, all versioned.

---

## How it works — deep dive

### 1. Connect a Git provider — OAuth or PAT

**Mechanism:** Databricks needs credentials to act on your behalf. For hosted GitHub,
the **Databricks GitHub app (OAuth 2.0)** is recommended — it auto-renews tokens and
can be scoped to specific repos; other providers use a **PAT**. Credentials register
under your user (or a service principal for automation).

**Why:** OAuth app > PAT for security (auto-renew, encrypted, repo-scoped) and avoids
long-lived secrets in user hands.

**Trade-off:** PATs expire and must be rotated; the GitHub app needs an admin to
install it once per org.

```bash
# Register a PAT credential via the Databricks CLI (git-credentials group).
# Providers: gitHub, gitLab, azureDevOpsServices, bitbucketCloud, awsCodeCommit, …
databricks git-credentials create gitHub \
  --git-provider gitHub \
  --git-username my-user \
  --personal-access-token "$GIT_PAT"
# (Hosted GitHub: prefer the Databricks GitHub app / OAuth over a PAT.)
```

### 2. Clone, branch, commit, push — the in-workspace workflow

**Mechanism:** create a Git folder by cloning a remote repo; then branch / stage /
commit / pull / push and resolve conflicts from the **Git dialog** in the UI. The
same operations are available via the `repos` API/CLI for automation.

**Why:** engineers stay in the workspace but get real Git semantics — isolation per
branch, atomic commits, shareable pushes.

**Trade-off:** the UI Git client is for *development*; production should deploy from a
clean pinned ref (via DABs), not a live-edited dev folder.

```bash
# Programmatic clone (CI/automation) — databricks repos command group:
databricks repos create \
  --url https://github.com/acme/sales-platform \
  --provider gitHub \
  --path /Workspace/Repos/ci/sales-platform
# In day-to-day dev you instead use the workspace Git dialog (branch/commit/push).
```

### 3. Branch-per-feature + PR — the team workflow

**Mechanism:** isolate each change on a **feature branch**, push, and open a **pull
request** on the provider for review; merge to `main` triggers CI. Notebooks are
stored as **source files** (e.g. `.py` with `# Databricks notebook source`), so diffs
are reviewable.

**Why:** review + history + rollback — the core of safe collaboration.

**Trade-off:** notebook **outputs aren't versioned** (only source) — don't rely on Git
to capture results; and merge conflicts in notebooks need care.

```bash
# Local or workspace-terminal git flow that maps to the UI buttons:
git checkout -b feature/add-returns-metric
git add src/ resources/ databricks.yml
git commit -m "Add net-returns metric to sales pipeline"
git push -u origin feature/add-returns-metric
# → open a PR on GitHub; review; merge to main triggers CI.
```

### 4. Git + Asset Bundles + CI/CD — how it all wires together

**Mechanism:** the repo holds the **source + `databricks.yml`**. On PR, CI runs
`databricks bundle validate`; on merge, CI authenticates as a **service principal**
and runs `databricks bundle deploy -t prod`. This is the production loop.

**Why:** the deployed prod resources always match reviewed, merged code — no manual UI
edits, fully auditable.

**Trade-off:** CI must authenticate **without a human** — use a **service principal**
with its own git credentials and OAuth, never a personal token.

```yaml
# .github/workflows/deploy.yml — GitHub Action calling the Databricks CLI
name: deploy
on:
  push: { branches: [ main ] }
  pull_request: {}
jobs:
  bundle:
    runs-on: ubuntu-latest
    env:
      DATABRICKS_HOST: ${{ secrets.DATABRICKS_HOST }}
      DATABRICKS_CLIENT_ID: ${{ secrets.DBX_SP_CLIENT_ID }}      # service principal
      DATABRICKS_CLIENT_SECRET: ${{ secrets.DBX_SP_SECRET }}     # OAuth (M2M)
    steps:
      - uses: actions/checkout@v4
      - uses: databricks/setup-cli@main
      - run: databricks bundle validate                          # on every PR
      - if: github.ref == 'refs/heads/main'
        run: databricks bundle deploy -t prod                    # on merge to main
```

### 5. Sparse checkout & allow-lists — scaling big monorepos

**Mechanism:** enable **sparse checkout** (cone patterns) when creating a Git folder
so only selected sub-paths sync into the workspace; admins can also **allow-list** Git
URLs/providers at the workspace level.

**Why:** keeps huge monorepos manageable (clone only your project) and enforces that
folders point only at sanctioned remotes.

**Trade-off:** sparse-checkout patterns must be set at clone time and maintained as the
repo layout changes.

```text
# Cone sparse-checkout patterns entered in the "Create Git folder" dialog:
sales-platform/
docs/        # only these sub-trees sync into the workspace Git folder
# (admin: also allow-list permitted Git URLs/providers for the workspace.)
```

---

## Git folders vs Asset Bundles (they pair up)

| | Git folders | Asset Bundles (DABs) |
|---|---|---|
| Role | **Version-control** code in the workspace | **Deploy** resources from code |
| Unit | Branches, commits, PRs | Jobs/pipelines/dashboards as YAML |
| Where | Workspace UI / `repos` API | CLI / CI |
| Together | Holds the source (incl. `databricks.yml`) | CI deploys that source |

## Uses, edge cases & limitations

- **Uses:** source control for notebooks/code, branch-based dev, PR review, the repo
  CI/CD pulls from, automation via the `repos`/`git-credentials` APIs.
- **Edge cases:**
  - **Production jobs should run from a clean, pinned ref** (or a DABs deploy) — not
    an interactively-edited dev Git folder that could be mid-change.
  - Notebook **outputs aren't versioned** (only source) — and large data/output files
    don't belong in Git; version **code**, not data.
  - CI must auth as a **service principal**, not a personal token.
- **Limitations:** Git folders versions **workspace assets**; cloud infra and resource
  *deployment* are DABs/Terraform's job; some file types/sizes have limits.

## Common gotchas

- ❌ Editing prod notebooks directly in the workspace with no Git → no history/rollback.
- ❌ Pointing a **production job** at a dev Git folder that's being edited live.
- ❌ Committing **data/secrets** into Git — version code only; use secret scopes.
- ❌ Using a **personal PAT** in CI instead of a **service principal** credential.
- ❌ Treating Git folders as the *deployer* — it version-controls; **DABs** deploy.

## References

- [Databricks Git folders — docs](https://docs.databricks.com/aws/en/repos/)
- [Connect your Git provider (OAuth / PAT)](https://docs.databricks.com/aws/en/repos/get-access-tokens-from-git-provider)
- [Create & manage Git folders](https://docs.databricks.com/aws/en/repos/git-operations-with-repos)
- [`git-credentials` CLI group](https://docs.databricks.com/aws/en/dev-tools/cli/reference/git-credentials-commands)
- [CI/CD with Git folders & bundles](https://docs.databricks.com/aws/en/dev-tools/bundles/ci-cd)
