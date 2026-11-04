# ADR 0004: YAML site configs are the single source of truth

## Status
Accepted

## Context
Site crawler configs existed twice: `configs/sites/*.yaml` and `sql/02_seed_configs.sql` (11
hand-written `INSERT`s of the same JSON). Neither copy reached the database on a fresh `make up`
(Postgres only ran `sql/init.sql`), and nothing in `src/` read the YAML files, although the
runbook said the scheduler picked up YAML changes. The two copies could also drift without any
test noticing, because each was validated separately.

The scheduler reads `crawler_configs` in PostgreSQL every poll. That table is still useful at
runtime: it holds `last_run_at`, it is what `crawl_runs` references, and operators can toggle
`enabled` without a redeploy.

## Decision
- `configs/sites/<site_id>.yaml` is the only place a site config is edited. The file name must
  equal `site_id`.
- `SyncSiteConfigsUseCase` (CLI: `realestate sync-configs`) upserts every YAML file into
  `crawler_configs`. It only changes rows whose config differs and never deletes rows.
- docker-compose runs a one-shot `config-sync` job before the scheduler starts.
- `sql/02_seed_configs.sql` and the split `01_schema.sql` / `03_views.sql` copies of `init.sql`
  are removed.
- Contract tests require every YAML file to survive `config_to_row` → `row_to_config`
  unchanged, so a second sync is always a no-op (also checked against real Postgres in
  `tests/integration`).

## Consequences
- A site is added or changed by editing YAML and running `make sync-configs` (or `make up`).
- A manual `UPDATE crawler_configs SET enabled = false` lasts until the next sync. To disable a
  site for good, set `enabled: false` in its YAML file.
- Removing a YAML file does not delete its row. Disable it in YAML first, or delete the row by hand.
