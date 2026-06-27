# Databricks Genie

> **Topic 10.4 · Databricks SQL — Warehouses, Genie & BI** — enterprise deep-dive,
> interview-focused. Genie is a UI feature, so the deep dive pairs each mechanism
> with the **governance SQL** and the **verified Conversation API** that make it
> programmable. Hands-on SQL lives in the consolidated Topic 10 notebook
> (`databricks_sql_hands_on.py`).

## What it is

- **AI/BI Genie** lets **business users ask data questions in plain English**; it
  generates the **SQL**, returns result tables, and can chart them — all over
  **governed Unity Catalog** data.
- You set up a **Genie space**: a domain-scoped chat over chosen datasets, with
  **curation** (instructions, example SQL, certified/sample questions, defined
  joins & metrics) that grounds Genie in *your* business terminology.
- It's the **business-user** member of the Genie family (vs **Genie Code** for
  developers, **Genie One** for cross-asset chat).

**Analogy:** Genie is a **bilingual analyst on call** — you speak "business," it
speaks "SQL," and it translates your question into a query against the warehouse.
The **curation** is the glossary you hand that analyst so it uses *your* definition
of "active customer" or "revenue."

## Why it matters

- It **democratizes data** — non-SQL users self-serve answers — while staying
  **inside UC governance** (they only ever see what they're permitted to).
- "How do non-technical users query the lakehouse safely?" → **Genie space** — and
  *curation is what makes it accurate* is the nuance interviewers probe for.
- Genie is **programmable** via the Conversation API, so it can power apps and
  agents — not just the chat box.

**Real-world use case:** a sales-ops team has a **Genie space** over the gold sales
tables; a manager asks "top 5 regions by revenue last quarter," Genie writes the
SQL and charts it — governed by UC, grounded by the space's certified "revenue"
metric so the number matches finance.

---

## How it works — deep dive

### 1. The Genie space — scope + datasets

**Mechanism:** a Genie space is bound to a **set of UC tables/views** and runs on a
**SQL Warehouse**. The space is the unit of scope, curation, and permission — you
share a *space*, not raw tables.

**Why:** scoping to a clean domain (a handful of gold tables) is what keeps answers
relevant and the model's search space small.

**Trade-off:** too many tables (or messy bronze) dilutes accuracy; aim a space at a
focused, well-named gold domain.

```sql
-- A space points at curated gold objects. Prep them so columns are self-describing.
CREATE OR REPLACE VIEW gold.sales_fact AS
SELECT order_id, region, net_revenue, fiscal_quarter, order_date
FROM prod.sales.orders_enriched;   -- clean, business-named columns Genie can reason over
COMMENT ON COLUMN gold.sales_fact.net_revenue IS 'Revenue after returns/discounts (finance definition)';
```

### 2. Natural language → SQL

**Mechanism:** Genie maps a question to SQL using the space's table **schemas +
column comments + curation**, runs it on the warehouse, and returns the SQL *and*
the result so users can verify and refine.

**Why:** showing the generated SQL is the trust mechanism — it's assistive, not a
black box.

**Trade-off:** ambiguity in the question or schema → a plausible-but-wrong query;
the fix is curation (next), not hoping the model guesses your business rules.

```sql
-- "revenue by region last quarter" → Genie generates (and shows) something like:
SELECT region, sum(net_revenue) AS revenue
FROM gold.sales_fact
WHERE fiscal_quarter = '2026-Q1'
GROUP BY region
ORDER BY revenue DESC;
```

### 3. Curation & trust — instructions, example SQL, metrics

**Mechanism:** you curate a space with **general instructions** (business rules),
**example SQL queries** (patterns to imitate), **certified/sample questions**, and
**defined joins & metrics** so terms like "revenue" resolve to one definition.

**Why:** curation is the single biggest driver of accuracy — a curated space answers
"active customer" the way *your* org defines it, every time.

**Trade-off:** curation is ongoing work (it must track schema/business changes) —
but it's what turns a demo into a trustworthy tool.

```sql
-- Encode the canonical metric as a governed object so Genie (and everyone) reuses it,
-- instead of re-deriving "active" differently each time:
CREATE OR REPLACE VIEW gold.active_customers AS
SELECT * FROM prod.sales.customers
WHERE status = 'ACTIVE' AND last_seen >= current_date() - INTERVAL 90 DAYS;
-- In the space's instructions: "‘active customer’ = rows in gold.active_customers."
```

### 4. Governance — UC grants, row filters, column masks

**Mechanism:** Genie queries run as the **asking user** through **Unity Catalog** —
so existing **grants, row filters, and column masks** apply automatically. Genie
cannot surface data the user couldn't already query.

**Why:** one governance model — you don't re-implement security inside the chat
tool; UC enforces it.

**Trade-off:** users see *different* answers based on their grants (a regional rep
sees only their region) — correct, but worth explaining to stakeholders.

```sql
-- Grant access to the curated domain; UC row-filter scopes rows per user.
GRANT SELECT ON VIEW gold.sales_fact TO `sales-ops`;
-- A row filter (8.3) already on the base table still applies inside Genie:
--   ALTER TABLE prod.sales.orders_enriched SET ROW FILTER catalog.sec.region_filter ON (region);
```

### 5. Genie is programmable — the Conversation API

**Mechanism:** the **Genie Conversation API** drives a space from code: start a
conversation, then send follow-up messages and poll the message until it completes.
⚠️ verify the current API version against docs before relying on exact paths.

**Why:** lets you embed Genie in apps/agents, or script evaluation — Genie becomes a
service, not just a UI.

**Trade-off:** start a **new conversation per session** (reusing threads across
sessions reduces accuracy from unintended context reuse); poll on a budget
(every 1–5 s, cap ~10 min).

```bash
# Start a conversation in a space (verified path, API 2.0):
curl -X POST \
  https://<workspace-host>/api/2.0/genie/spaces/<space_id>/start-conversation \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -d '{"content": "What was revenue by region last quarter?"}'

# Follow-up in the same conversation:
#   POST /api/2.0/genie/spaces/<space_id>/conversations/<conversation_id>/messages
# Poll the message until status is COMPLETED / FAILED / CANCELLED.
```

### 6. Genie vs a generic LLM chatbot

**Mechanism:** a generic chatbot answers from training data / free text; **Genie is
grounded** — it only answers by generating SQL over *your* governed tables, returns
the query, and respects UC permissions.

**Why:** that grounding + governance is the difference between a plausible
hallucination and an auditable, permission-safe answer.

**Trade-off:** Genie won't answer questions its data can't support — by design. For
key decisions, review the generated SQL.

| | Databricks Genie | Generic LLM chatbot |
|---|---|---|
| Source of truth | your **UC tables** (live) | training data / prompt |
| Output | **SQL + result** (verifiable) | prose (opaque) |
| Governance | **UC grants/filters/masks** | none |
| Accuracy lever | **curation** (metrics, instructions) | prompt engineering |

---

## Uses, edge cases & limitations

- **Uses:** self-serve analytics for business users, ad-hoc Q&A over curated
  domains, cutting the analyst request queue, embedding Q&A in apps via the API.
- **Edge cases:**
  - **Accuracy depends on curation** — a thin space (no metrics/instructions) gives
    vaguer/wrong answers; invest in metrics + example SQL + certified questions.
  - Ambiguous business terms ("active user") need a **defined metric/view** or Genie
    may guess differently than finance.
  - Per-user governance means **different users get different rows** — expected.
- **Limitations:** it's **assistive** — review generated SQL for important
  decisions; quality tracks data-model clarity; best over **clean, well-named gold**
  tables, not raw bronze; availability/features vary by region.

## Common gotchas

- ❌ Expecting great answers from an **uncurated** space — curation (metrics, sample
  queries, instructions) is what drives accuracy.
- ❌ Pointing Genie at messy **bronze** tables — aim it at clean **gold**.
- ❌ Treating output as always-correct — it's assistive; verify the SQL for key numbers.
- ❌ Confusing **Genie** (business Q&A) with **Genie Code** (build code/pipelines).
- ❌ Reusing one API **conversation** across unrelated sessions — start a new one to
  avoid context bleed.

## References

- [AI/BI Genie — docs](https://docs.databricks.com/aws/en/genie/)
- [Set up & curate a Genie space](https://docs.databricks.com/aws/en/genie/set-up)
- [Genie Conversation API (guide)](https://docs.databricks.com/aws/en/genie/conversation-api)
- [Genie API reference — start conversation](https://docs.databricks.com/api/workspace/genie/startconversation)
- [AI/BI Dashboards (companion)](https://docs.databricks.com/aws/en/dashboards/)
