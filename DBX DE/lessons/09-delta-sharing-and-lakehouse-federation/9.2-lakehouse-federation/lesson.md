# Lakehouse Federation

> **Topic 9.2 · Delta Sharing & Lakehouse Federation** — enterprise deep-dive,
> interview-focused. Runnable end-to-end code for all of Topic 9 lives in the
> consolidated notebook `sharing_federation_hands_on.py`; snippets below are the
> teaching units.

## What it is

- **Lakehouse Federation** lets you **query external databases in place** — MySQL,
  PostgreSQL, SQL Server, Snowflake, Redshift, BigQuery, etc. — **without
  ingesting/copying** the data into Databricks.
- You create a **connection** to the source, then a **foreign catalog** that
  surfaces its tables as **foreign tables** you query with normal SQL — governed by
  **Unity Catalog**, **read-only**.
- **Query pushdown:** Databricks pushes filters/aggregations to the remote DB so it
  does the work and less data moves.

**Analogy:** federation is a **library inter-loan**. Instead of buying a copy of
every book (ingestion), your card lets you **read books that live in other
libraries** through one catalog — they stay on their own shelves.

## Why it matters

- Not all data should be **copied** into the lakehouse. Federation gives **live,
  governed access** for ad-hoc queries and exploration without ETL.
- "Query a Postgres table without building a pipeline?" → **Lakehouse Federation**;
  vs "land it for repeated heavy use?" → **ingest** (Lakeflow Connect). Knowing
  *federate vs ingest* is the interview point.

**Real-world use case:** analysts need to join a live operational **Postgres**
orders table with lakehouse customer data for a one-off report — a **foreign
catalog** exposes Postgres as `pg.public.orders`, queried directly, no pipeline.

---

## How it works — deep dive

### 1. Connection + foreign catalog

```sql
-- 1) connection holds credentials/host (a UC securable; reuse it)
CREATE CONNECTION pg_conn TYPE postgresql
  OPTIONS (host 'db.corp.net', port '5432',
           user secret('kv','pg_user'), password secret('kv','pg_pwd'));

-- 2) foreign catalog maps the remote namespace into UC 3-level naming
CREATE FOREIGN CATALOG pg USING CONNECTION pg_conn OPTIONS (database 'appdb');
```

- The **connection** is created once and reused; the **foreign catalog** exposes the
  source's schemas/tables as `pg.<schema>.<table>`.

### 2. Supported sources (connector `TYPE`)

| Source | `TYPE` |
|---|---|
| PostgreSQL | `postgresql` |
| MySQL | `mysql` |
| SQL Server | `sqlserver` |
| Snowflake | `snowflake` |
| Redshift | `redshift` |
| Google BigQuery | `bigquery` |

*(Connector list grows — verify the current set + options per source in the docs.)*

### 3. Query in place + pushdown + cross-source join

```sql
-- read-only; supported predicates/aggregations push down to Postgres
SELECT region, count(*) FROM pg.public.orders
WHERE order_date > current_date() - 7
GROUP BY region;

-- the real power: join a LIVE external table to a lakehouse table in one query
SELECT o.order_id, o.amount, c.segment
FROM pg.public.orders o                       -- federated (Postgres, live)
JOIN main.sales.dim_customer c                -- native Delta
  ON o.customer_id = c.id;
```

### 4. Governance — foreign tables are UC objects

```sql
-- grant on the foreign catalog/schema like any UC object (read-only)
GRANT USE CATALOG ON CATALOG pg TO `analysts`;
GRANT USE SCHEMA  ON SCHEMA  pg.public TO `analysts`;
GRANT SELECT      ON TABLE   pg.public.orders TO `analysts`;
```

- Foreign tables get **UC grants, lineage, and discovery** like native tables —
  governance doesn't stop at the lakehouse boundary.

### 5. Federation vs ingestion vs Delta Sharing

| | Lakehouse Federation | Ingestion (Lakeflow Connect) | Delta Sharing |
|---|---|---|---|
| Data moves? | **No** — query in place | **Yes** — copied to Delta | No — shared in place |
| Direction | you read **others' DBs** | pull **into** your lakehouse | you share **out** to recipients |
| Best for | ad-hoc / live queries | repeated heavy use | cross-org data sharing |

**Decision rule:** **federate** for exploration, one-off joins, and "do I even need
this?"; **ingest** once a query is frequent/heavy (cheaper + faster + insulates the
OLTP source); **share** when the goal is giving *your* data to someone else.

---

## Uses, edge cases & limitations

- **Uses:** ad-hoc reporting on operational DBs, incremental migration, hybrid
  architectures, exploring before deciding to ingest, joining live external data to
  lakehouse tables.
- **Edge cases:** queries hit the **live source** — heavy/repeated queries can
  **load the operational DB**; **pushdown coverage varies by connector**, so some
  queries pull more data than you'd expect (check the query profile).
- **Limitations:** **read-only**; performance depends on the remote system &
  network. For frequent, heavy workloads, **ingesting** is usually faster/cheaper
  than repeatedly federating.

## Common gotchas

- ❌ Confusing **federation** (query others' DBs, no copy) with **Delta Sharing**
  (share your data out) — opposite directions.
- ❌ Hammering a production OLTP DB with heavy federated analytics queries.
- ❌ Expecting **writes** — federation is read-only.
- ❌ Federating a table you query constantly when **ingesting** it once would be
  cheaper/faster.
- ❌ Assuming full pushdown — complex expressions may execute in Databricks after
  pulling rows; verify with the query profile.

## References

- [Lakehouse Federation (query federation) — docs](https://docs.databricks.com/aws/en/query-federation/)
- [Create a connection](https://docs.databricks.com/aws/en/query-federation/#connection)
- [Foreign catalogs](https://docs.databricks.com/aws/en/query-federation/foreign-catalogs)
- [CREATE CONNECTION (SQL)](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-connection)
