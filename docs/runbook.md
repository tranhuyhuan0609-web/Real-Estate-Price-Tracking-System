# Real Estate Tracker Operations Runbook

## 1. Quick Start (5 Minutes)

### Prerequisites
- Docker Engine >= 24.0 and Docker Compose v2
- uv (installs Python 3.13 from `.python-version`)

### Starting Services
```bash
# 1. Copy environment template
cp .env.example .env

# 2. Start all infrastructure and workers
make up
# or: docker compose -f docker/docker-compose.yml up -d --build

# 3. Check container status
make ps
```

---

## 2. Managing Crawl Runs & Site Configurations

### Triggering Manual Crawl via CLI
```bash
# Incremental crawl (latest listings only)
uv run realestate crawl --site meeyland --mode incremental

# Full sweep crawl (entire catalogue for lifecycle expiry check)
uv run realestate crawl --site nhatot --mode full_sweep
```

### Adding or Updating a Site Config
`configs/sites/<site_id>.yaml` is the source of truth (ADR 0004). The scheduler reads the
`crawler_configs` table, so YAML changes take effect only after a sync:
1. Edit or create `configs/sites/<site_id>.yaml` (the file name must equal `site_id`).
2. Validate: `make test` (or `uv run pytest tests/contract/test_site_configs.py`).
3. Preview, then apply:
   ```bash
   uv run realestate sync-configs --dry-run
   make sync-configs            # or: make up  (the config-sync job runs before the scheduler)
   ```
   The scheduler sees the new row on its next poll (15 s). To disable a site for good, set
   `enabled: false` in YAML. A manual SQL toggle lasts only until the next sync.

---

## 3. Dead Letter Queue (DLQ) & Replay

Bronze and Silver/Gold workers publish records they cannot process to the Kafka topic
`realestate.dlq_events`. The `dlq-worker` container archives that topic into MongoDB `dlq_events`.

### Inspecting DLQ Records
Connect to MongoDB:
```bash
mongosh "mongodb://mongo_user:mongo_secure_password@127.0.0.1:16102/realestate_raw?authSource=admin"
```
Query recent failures:
```javascript
db.dlq_events.find().sort({ failed_at: -1 }).limit(5)
```

### Replaying DLQ Back into Pipeline
Once parser bug or schema is fixed:
```bash
# Replay 50 messages for a specific site
uv run realestate replay-dlq --site-id meeyland --limit 50

# Replay all DLQ messages from MongoDB, deleting the ones that were re-published
uv run realestate replay-dlq --limit 500 --delete

# Replay straight from the Kafka DLQ topic (e.g. if dlq-worker was down)
uv run realestate replay-dlq --source kafka --limit 500
```

---

## 4. Lifecycle & Expiry Operations

During a `full_sweep` run:
1. `CrawlSiteUseCase` crawls all listing pages.
2. Ingestion gate waits until Silver/Gold consumers have processed $\ge 95\%$ of items.
3. `ApplyLifecycleUseCase` compares active listings against the run's `started_at`:
   - Listings not seen in this run have their `missing_count` incremented.
   - When `missing_count >= 3`, listings are automatically marked as `expired`.
4. Analytical materialized views are refreshed concurrently (`REFRESH MATERIALIZED VIEW CONCURRENTLY`).


---

## 5. Schema Changes & Resetting Data

- `sql/init.sql` runs **only** when the Postgres volume is empty. Ship later schema changes as
  Alembic revisions in `migrations/versions/` and apply them with `make migrate`. On a database
  created by `init.sql`, the first `make migrate` re-applies the idempotent baseline `001` and stamps it.
- `tests/contract/test_sql_schema.py` fails if `init.sql` and the `001` baseline drift apart.
- Wipe ingested data, keeping configs and crawl history:
  ```bash
  uv run realestate reset --confirm
  ```
