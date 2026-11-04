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

Migrations in `migrations/versions/` are the only schema source (ADR 0006).

1. Write a new Alembic revision.
2. `make schema` regenerates `sql/init.sql`; commit both. `tests/contract/test_sql_schema.py`
   fails if you forget.
3. Apply: `make up` (the `migrate` job runs `alembic upgrade head` before `config-sync`) or
   `make migrate` from the host.

A fresh volume is initialised from `sql/init.sql`, which already stamps `alembic_version`.

Wipe ingested data, keeping configs and crawl history:
```bash
uv run realestate reset --confirm
```

### Price-history partitions
`listing_price_history` is partitioned by month. The scheduler creates the partitions two months
ahead at startup and every 24 h. To check or run it by hand:
```sql
SELECT ensure_price_history_partitions(2);                 -- returns partitions created
SELECT count(*) FROM listing_price_history_default;        -- should stay 0
-- archive a month: detach it, dump it, then drop it
ALTER TABLE listing_price_history DETACH PARTITION listing_price_history_y2025m01;
```

### Upgrading a database from before migration 002 (portals)
`002_listing_feeds` re-keys `mogi_apartment_*`, `homedy_apartment_*` and `nhatot_hn_*`
listings onto their portal and merges duplicates (ADR 0005). Take a backup first:
```bash
docker compose -f docker/docker-compose.yml exec postgres pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > before_002.dump
```

---

## 6. Monitoring & Health

- Every service exposes Prometheus metrics on `:9100/metrics` inside the compose network, and
  writes a heartbeat. Docker marks a container unhealthy (and restarts it) when its main loop has
  not beaten for 120 s:
  `python -m realestate.entrypoints.healthcheck <scheduler|bronze|silver_gold|dlq>`.
- `make up-monitoring` adds Prometheus on http://localhost:16104 with the rules in
  `docker/monitoring/alerts.yml`:

| Alert | Meaning | First thing to check |
|---|---|---|
| `ProcessStalled` / `TargetDown` | a loop is stuck or the process died | `make logs`, `docker compose ps` |
| `DeadLetterQueueGrowing` | parser or schema drift | `db.dlq_events.find().sort({failed_at:-1})`, then fix and `replay-dlq` |
| `ConsumerRetryLoop` | Postgres/Mongo/Kafka unavailable | health of the database containers |
| `ConsumerLagHigh` | consumers cannot keep up | add replicas (topic has 4 partitions) |
| `FullSweepFailing` | lifecycle is not running for a site | `crawl_runs.error_message`; site blocking us? |
| `MassExpiry` | a sweep expired many listings at once | was the sweep truncated? compare `items_scraped` |

### Telegram alerts (one-time setup)
Telegram bots can only message a **chat id**, not a phone number, and only after you message them.
1. In Telegram open **@BotFather** → `/newbot` → copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Open your new bot and press **Start** (or add it to a group and send a message there).
3. On the host (reads `.env`): `uv run realestate telegram-chat-id`, or without uv
   `./.venv/Scripts/realestate telegram-chat-id` → copy the id into `.env` as `TELEGRAM_CHAT_ID`.
4. `uv run realestate telegram-test` (or `./.venv/Scripts/realestate telegram-test`) → you should receive a message.
5. `dc --profile monitoring up -d` (re)starts Alertmanager with the new values. Its log says
   "sending alerts to Telegram chat ..." (or that alerts are NOT delivered when unset).
   Alertmanager UI: http://localhost:16105 (silence an alert during maintenance there).

Firing alerts repeat every 4 h until resolved; resolved alerts are announced too.

## 7. When a Site Changes Its Layout

The golden tests in `tests/unit/parsers/test_html_site_golden.py` run the production YAML against
captured pages. To refresh them after a layout change:
```bash
curl -sL -A "Mozilla/5.0" https://mogi.vn/mua-nha-dat -o list.html
curl -sL -A "Mozilla/5.0" "<first listing URL>" -o detail.html
uv run python scripts/capture_site_fixture.py mogi list.html detail.html   # trims + anonymises
uv run pytest tests/unit/parsers/test_html_site_golden.py                  # then update EXPECTED
```
`tests/contract/test_site_configs.py` also rejects selectors that do not compile (a broken XPath
otherwise extracts nothing, silently).

## 8. Kafka Security

Local compose runs Kafka on PLAINTEXT, bound to 127.0.0.1. For any broker reachable from outside a
private network set `KAFKA_SECURITY_PROTOCOL=SASL_SSL` plus `KAFKA_SASL_USERNAME` /
`KAFKA_SASL_PASSWORD` (`KAFKA_SASL_MECHANISM` defaults to SCRAM-SHA-512). Every producer, consumer
and replay tool takes these from `Settings.kafka_client_kwargs()`.

## 9. Backups & Restore

`pg-backup` and `mongo-backup` dump daily to `./backups/{postgres,mongo}` on the host and keep
`BACKUP_KEEP_DAYS` (default 7) days. The scheduler exports the newest dump time; the
`BackupStale` alert fires after 2 days without a successful dump.

```powershell
dc run --rm pg-backup once          # backup now
dc run --rm mongo-backup once
# restore Postgres (stop writers first: dc stop crawler-scheduler silver-gold-worker)
docker cp backups/postgres/<file>.dump realestatepricetrackingsystem-postgres-1:/tmp/r.dump
dc exec postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists /tmp/r.dump'
# restore Mongo
docker cp backups/mongo/<file>.archive.gz realestatepricetrackingsystem-mongo-1:/tmp/r.gz
dc exec mongo sh -c 'mongorestore -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --archive=/tmp/r.gz --gzip --drop'
```
Copy `./backups` off the machine (cloud drive, NAS) for real disaster recovery.

## 10. Running Two Schedulers

Schedulers elect a leader with a Postgres advisory lock. Only the leader schedules crawls; others
stand by (healthy, logging "standing by") and take over within one poll (15 s) if the leader dies.
`dc up -d --scale crawler-scheduler=2` is therefore safe.

## 11. Timezones

`source_timezone` (default UTC) is the timezone of `schedule_times`. `content_timezone` (default
`Asia/Ho_Chi_Minh`) is how dates printed on the site ("Ngày đăng 04/11/2026") are read. They are
independent, so moving the schedule never shifts `published_at`.
