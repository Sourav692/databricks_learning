# Delta Sharing

> **Topic 9.1 · Delta Sharing & Lakehouse Federation** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code lives in the consolidated Topic 9
> notebook (built at the last subtopic); snippets below are the teaching units.

## What it is

- **Delta Sharing** is an **open protocol** for **securely sharing live data with
  other organizations — without copying it**. Recipients get **read-only** access;
  the data stays in the provider's storage.
- **Two models:**
  - **Databricks-to-Databricks (D2D)** — share to another UC-enabled Databricks
    account/cloud; **no token management** (identity flows through the platform).
    Can also share notebooks, volumes, and models.
  - **Open sharing (Databricks-to-Open)** — share **tabular data to any client**
    (Spark, pandas, Power BI…) via a **bearer token** / OIDC.
- Core objects: **Provider** (owner) → **Share** (collection of assets) →
  **Recipient** (the consumer).

**Analogy:** Delta Sharing is a **library card for your data**, not photocopies.
You don't mail copies of the book (no data movement); you issue a **read-only card**
(the recipient) that lets them read the live book where it sits — and you can
**cancel the card** anytime.

## Why it matters

- Traditional sharing = **FTP/export copies** → stale, ungoverned, expensive. Delta
  Sharing is **zero-copy, live, governed, revocable**.
- "Share data with a partner who isn't on Databricks?" → **open sharing**; "with
  another Databricks org?" → **D2D** — a common interview distinction.

**Real-world use case:** a data provider shares a curated `sales` table with a
partner's BI tool via **open sharing** (token), and with a sister business unit's
Databricks workspace via **D2D** — both read the **same live data**, no copies,
access revocable instantly.

---

## How it works — deep dive

### 1. Provider side — build & grant a share

```sql
CREATE SHARE sales_share COMMENT 'Curated sales for partners';
ALTER SHARE sales_share ADD TABLE prod.sales.orders;        -- add assets (+ partitions/schemas)

-- D2D recipient: bind to the partner's Databricks sharing identifier (no tokens)
CREATE RECIPIENT partner_db USING ID 'aws:us-east-1:<sharing-identifier>';

-- Open recipient: omit USING ID → Databricks generates an activation link/token
CREATE RECIPIENT partner_bi;

GRANT SELECT ON SHARE sales_share TO RECIPIENT partner_db;
GRANT SELECT ON SHARE sales_share TO RECIPIENT partner_bi;
```

### 2. Recipient side — consume the share

- **D2D:** the share appears as a **read-only catalog** in the recipient's
  metastore — query it like any UC table:

```sql
-- in the recipient workspace, after the provider's share is mounted as a catalog
SELECT * FROM partner_inbound.sales.orders;
```

- **Open:** the recipient downloads a **credential profile** (from the activation
  link) and reads with any Delta Sharing client:

```python
# any Spark/pandas client — no Databricks account needed
df = (spark.read.format("deltaSharing")
        .load("/path/to/config.share#sales_share.sales.orders"))   # profile#share.schema.table
```

### 3. D2D vs Open sharing

| | Databricks-to-Databricks | Open sharing |
|---|---|---|
| Recipient | another UC Databricks account | **any** client (pandas, Power BI…) |
| Auth | platform identity (**no tokens**) | **bearer token** / OIDC profile |
| Can share | tables, notebooks, volumes, models | tabular data |
| Consumed as | a read-only **catalog** | `deltaSharing` reader / connector |
| Use when | partner is on Databricks | partner is not on Databricks |

### 4. Governance & the ecosystem

- **Fine-grained limits:** the share grants whole tables — layer **dynamic views**
  (8.3) for row/column restrictions before sharing.
- **Revoke** instantly (`REVOKE` / drop recipient); rotate open-sharing **tokens**.
- **Databricks Marketplace** is built on Delta Sharing — publish/consume data
  products publicly. **Clean Rooms** let two parties run joint analysis on shared
  data **without either seeing the other's raw rows**.

---

## Uses, edge cases & limitations

- **Uses:** B2B data exchange, sharing across regions/clouds, internal cross-org
  sharing, distributing curated datasets to non-Databricks tools, Marketplace
  publishing, privacy-preserving collaboration (Clean Rooms).
- **Edge cases:** **open-sharing tokens** must be managed/rotated/secured;
  row/column limits need **dynamic views** layered on the shared table; the D2D
  sharing identifier must be exchanged correctly.
- **Limitations:** recipients get **read-only** access; some asset types
  (notebooks/volumes/models) are **D2D only**. It's a *sharing* mechanism, not a
  replacement for **Lakehouse Federation** (querying external sources — 9.2).

## Common gotchas

- ❌ Thinking it **copies** data — it's **zero-copy**, live access in place.
- ❌ Using **open sharing** for a Databricks recipient when **D2D** is simpler
  (no tokens to manage).
- ❌ Leaking / never-rotating **open-sharing tokens**.
- ❌ Expecting fine-grained row/column control from the share alone — add **dynamic
  views**.
- ❌ Confusing Delta Sharing (share *your* data out) with **Lakehouse Federation**
  (query *external* sources in) — opposite directions.

## References

- [Delta Sharing — Databricks docs](https://docs.databricks.com/aws/en/delta-sharing/)
- [Share data (D2D)](https://docs.databricks.com/aws/en/delta-sharing/share-data-databricks)
- [Open sharing](https://docs.databricks.com/aws/en/delta-sharing/share-data-open)
- [Databricks Marketplace](https://docs.databricks.com/aws/en/marketplace/)
- [Clean Rooms](https://docs.databricks.com/aws/en/clean-rooms/)
