# ADR 0006: Migrations are the only schema source; price history is partitioned

## Status
Accepted (migration `003_partition_history`)

## Context
The schema was written twice, in `sql/init.sql` (run by Postgres on an empty volume) and in the
Alembic baseline `001`. A contract test kept the two in sync by hand. A database created from
`init.sql` had no `alembic_version`, so `make migrate` had to re-run the baseline. Separately,
`listing_price_history` is append-only and grows with every price change.

## Decision
- `migrations/versions/*.py` is the single schema source. `sql/init.sql` is **generated**
  (`make schema` = `alembic upgrade head --sql`). The generated file also creates and stamps
  `alembic_version`, so a database initialised from it continues at the next revision.
  `tests/contract/test_sql_schema.py` fails when the file is stale.
- docker-compose runs a one-shot `migrate` job (`alembic upgrade head`) before `config-sync`, so
  existing volumes are upgraded automatically.
- `listing_price_history` is range-partitioned by month on `recorded_at`. The primary key becomes
  `(history_id, recorded_at)`. `ensure_price_history_partitions(months_ahead)` creates the
  partitions; the scheduler calls it at startup and every 24 h. A DEFAULT partition catches rows
  outside the created range; when their month is created later, those rows are moved out of
  DEFAULT before the partition is attached.

## Consequences
- Adding a schema change means writing a migration, then running `make schema`; CI fails otherwise.
- Old months can be detached or dropped without vacuuming a huge table, and the 7-day view only
  scans recent partitions.
- `history_id` alone is no longer declared unique by the table (the sequence still guarantees
  it); the materialized view keeps its own unique index on `history_id`.
