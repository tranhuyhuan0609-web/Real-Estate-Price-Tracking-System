# System Architecture: Real Estate Price Tracking

## 1. Overview & High-Level Design

The **Real Estate Price Tracking System** is an event-driven, production-grade distributed crawling and analytical platform designed to track real estate listings, detect price changes, flag likely duplicate properties, and compute market aggregates across Vietnam.

The system follows **Clean Hexagonal Architecture** and the **Medallion Architecture** pattern (Bronze, Silver, Gold data layers) with Apache Kafka acting as the asynchronous event backbone.

```mermaid
flowchart TD
    subgraph Schedulers ["Schedulers & Spiders"]
        YAML["configs/sites/*.yaml"] -- "config-sync (one-shot)" --> PG_CFG[("PostgreSQL: crawler_configs")]
        PG_CFG --> SCHED["Crawler Scheduler (Cron / Fixed Slot)"]
        SCHED --> SPIDERS["Generic API & HTML Spiders"]
        SPIDERS --> KAFKA_RAW["Kafka: realestate.raw_events"]
    end

    subgraph BronzeLayer ["Bronze Layer (Raw)"]
        KAFKA_RAW --> BRONZE_CONSUMER["Bronze Mongo Consumer (Replicas)"]
        BRONZE_CONSUMER --> MONGO[("MongoDB: raw_listings & dlq_events")]
    end

    subgraph SilverGoldLayer ["Silver & Gold Layer (Relational & Analytical)"]
        KAFKA_RAW --> CLEAN_CONSUMER["Silver/Gold Consumer (Replicas)"]
        CLEAN_CONSUMER --> PG_LISTINGS[("PostgreSQL: listings & advertisers (Silver)")]
        CLEAN_CONSUMER --> PG_DETAILS[("PostgreSQL: listing_details (Silver)")]
        CLEAN_CONSUMER --> PG_HISTORY[("PostgreSQL: listing_price_history (Gold)")]
        PG_HISTORY --> MV_PRICE[("Materialized Views: mv_ward_avg_price_per_m2 & mv_steep_price_drops_7d (Gold)")]
    end

    subgraph DLQ ["Dead Letter Queue (DLQ)"]
        CLEAN_CONSUMER -. Poison Pills / Errors .-> KAFKA_DLQ["Kafka: realestate.dlq_events"]
        BRONZE_CONSUMER -. Errors .-> KAFKA_DLQ
        KAFKA_DLQ --> DLQ_WORKER["DLQ Consumer"] --> MONGO_DLQ[("MongoDB: dlq_events")]
        MONGO_DLQ -. CLI Replay .-> KAFKA_RAW
    end
```

---

## 2. Medallion Data Architecture

### Bronze Layer (MongoDB)
- **Purpose**: Immutable raw storage of scraped JSON payloads and HTML snippets.
- **Collections**:
  - `raw_listings`: Stores exact payload alongside `listing_key`, `site_id`, `crawl_run_id`, and `scraped_at`.
  - `dlq_events`: Stores unparseable poison pills or system failures with full stack trace and metadata for replay.
- **Guarantee**: Zero data loss even if downstream parsers change or schema migrations occur.

### Silver Layer (PostgreSQL - Cleaned & Structured)
- **Tables**:
  - `listings`: Normalized core listing metadata (price, area, status, fingerprint, last_seen_at).
  - `listing_details`: Rich descriptive attributes, geographical PostGIS points (`geom`), addresses, and media URLs.
  - `advertisers`: De-duplicated brokers and owners identified by unified phone/identity.
  - `listing_feeds`: which crawler feeds have seen each listing (ADR 0005).
- **Portals and feeds** (ADR 0005): `listing_key = <portal>_<id>`. Several feeds (`mogi`, `mogi_apartment`)
  can crawl one portal; an ad they share is one listing. Lifecycle is applied per feed through `listing_feeds`.
- **Property fingerprint** (`domain/services/fingerprint.py`): SHA-256 of
  `title | area_m2 | city | district | ward`. The title is lower-cased with punctuation and extra spaces removed,
  the area is rounded to 0.1 m², and administrative codes are used when names are missing. Coordinates, price and
  advertiser phone are **not** part of it, so revealing a masked phone or changing the price keeps the same
  fingerprint. It is stored in `listings.property_fingerprint` (indexed) for cross-site duplicate analysis.
  Listings are **not** merged automatically: each `listing_key` stays its own row.

### Gold Layer (PostgreSQL - Analytical & History)
- **Tables & Views**:
  - `listing_price_history`: Append-only, monthly-partitioned (ADR 0006) chronological record of detected price changes with `price_diff_vnd`, `price_diff_percent`, and `price_status`.
  - `mv_ward_avg_price_per_m2`: Ward/commune level real-time median and average price per $m^2$ metrics.
  - `mv_steep_price_drops_7d`: Automated identification of properties with price drops $\ge 5\%$ over the past 7 days.

---

## 3. Component Breakdown

| Component | Responsibility | Scaling Pattern |
|:---|:---|:---|
| **Config sync** | One-shot job: upserts `configs/sites/*.yaml` into `crawler_configs` before the scheduler starts (ADR 0004) | Runs once per deploy |
| **Scheduler** | Polls `crawler_configs` in PostgreSQL, checks due runs, triggers `CrawlSiteUseCase` | Single coordinator / Leader |
| **Spiders** | Generic REST and HTML parsers streaming listing items through async HTTP | Partitioned by site / concurrent workers |
| **Bronze Worker** | Consumes from `realestate.raw_events` and bulk-upserts into MongoDB | Horizontally scalable (Kafka consumer group) |
| **Silver/Gold Worker** | Consumes events, evaluates price delta, inserts transactional silver/gold records | Horizontally scalable (Kafka consumer group) |
| **DLQ Worker** (`entrypoints/dlq_worker.py`) | Archives `realestate.dlq_events` into MongoDB `dlq_events` | Single consumer group |
| **CLI** (`realestate ...`) | `sync-configs`, `crawl`, `replay-dlq --source mongo\|kafka`, `reset` | Operator-driven |


---

## 4. Wiring & Process Model

- `src/realestate/bootstrap.py` (`AppContainer`) is the composition root for every process: the scheduler,
  the three workers (`bronze_worker()`, `silver_gold_worker()`, `dlq_worker()`) and the CLI. It builds connections
  lazily, so each process opens only what it uses.
- `src/realestate/entrypoints/_runtime.py` sets up each process: logging level from `CRAWLER_LOG_LEVEL`, the selector event
  loop on Windows (needed by psycopg async), and SIGINT/SIGTERM handling.
- `infrastructure/messaging/base_consumer.py` owns the consume loop shared by the three consumers:
  decode → batch store → sequential fallback → DLQ (`forward_to_dlq`) for poison pills → seek back for
  anything neither stored nor parked → commit. Consumers only implement `decode`, `process_batch`,
  `process_one`, `is_poison_pill` and optional `setup` / `after_batch`.
- Scheduling policy is pure domain code (`domain/services/scheduling.py::decide_run`). Full-sweep slots are
  read in each site's `source_timezone` through `CrawlerConfig.latest_scheduled_slot`.

## 5. Ordering Guarantees (Silver/Gold)

Kafka redelivery, DLQ replays and backlog catch-up can deliver an observation older than the one already
applied. `PgListingRepository` treats an event with `event_time < listings.last_seen_at` as **stale**. A stale
event never changes price, status, `missing_count`, advertiser or details, and never writes price history.
It can only widen the observed window (`first_seen_at = LEAST(...)`, which also keeps
`chk_listing_time_order` valid) and fill a missing `published_at`.

## 6. Observability

Metrics (`infrastructure/observability/metrics.py`, `/metrics` on `METRICS_PORT`): consumer outcomes
(`stored`/`dlq`/`retry`), batch latency, partition lag, crawl results and durations, lifecycle expiries,
and a heartbeat per process. The heartbeat file also backs the Docker healthchecks. Alert rules live in
`docker/monitoring/alerts.yml` and are delivered to Telegram by Alertmanager; see the runbook (section 6).
Daily Postgres/Mongo dumps go to `./backups` (runbook section 9). The scheduler is leader-elected through a
Postgres advisory lock (`infrastructure/persistence/postgres/leader_lock.py`), so a second instance is a hot standby.
