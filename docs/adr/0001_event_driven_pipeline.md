# ADR 0001: Event-Driven Pipeline using Apache Kafka

## Status
Accepted

## Context
Crawling real estate sites produces high-throughput streams of listings. Processing them synchronously blocks spiders, creates backpressure on target websites, and causes data loss if database transactions fail or lag.

## Decision
Adopt Apache Kafka as an asynchronous streaming message backbone between spiders and storage layers.
- Spiders publish raw listing messages to `realestate.raw_events` keyed by `listing_key`.
- Independent consumer groups (`bds_bronze_mongo_group`, `bds_silver_gold_pg_group`) consume and process listings at their own optimal pace.
- Fatal errors and poison pills are routed to `realestate.dlq_events`.

## Consequences
- **Pros**: Spiders operate at maximum throughput; decoupled storage workers; consumer restarts or spikes do not disrupt crawling.
- **Cons**: Requires Kafka infrastructure operations and consumer lag monitoring.

