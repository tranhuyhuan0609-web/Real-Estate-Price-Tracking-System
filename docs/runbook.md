# Real Estate Tracker Operations Runbook

## 1. Quick Start (5 Minutes)

### Prerequisites
- Docker Engine >= 24.0 and Docker Compose v2
- Python >= 3.11 (Python 3.13 recommended)

### Starting Services
```bash
# 1. Copy environment template
cp .env.example .env

# 2. Start all infrastructure and workers
make up
# or: docker compose -f docker/docker-compose.yml up -d --build

# 3. Check container status
docker compose ps
```

---

## 2. Managing Crawl Runs & Site Configurations

### Triggering Manual Crawl via CLI
```bash
# Incremental crawl (latest listings only)
python -m realestate.entrypoints.cli crawl --site meeyland --mode incremental

# Full sweep crawl (entire catalogue for lifecycle expiry check)
python -m realestate.entrypoints.cli crawl --site nhatot --mode full_sweep
```

### Adding or Updating a Site Config
Site configurations are stored in YAML files under `configs/sites/<site_id>.yaml`.
To adjust pagination budget, schedule times, or response mappings:
1. Edit or create `configs/sites/<site_id>.yaml`.
2. Run config validation test:
   ```bash
   pytest tests/contract/test_site_configs.py
   ```
3. The scheduler picks up changes on the next poll cycle or container reload.

---

## 3. Dead Letter Queue (DLQ) & Replay

When unparseable records or schema discrepancies occur, messages are safely routed to `dlq_events` in MongoDB.

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
python -m realestate.entrypoints.cli replay-dlq --site-id meeyland --limit 50

# Replay all DLQ messages from MongoDB
python -m realestate.entrypoints.cli replay-dlq --limit 500
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

