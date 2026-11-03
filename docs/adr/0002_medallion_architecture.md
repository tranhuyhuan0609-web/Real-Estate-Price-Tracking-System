# ADR 0002: Medallion Architecture (Bronze, Silver, Gold)

## Status
Accepted

## Context
Raw scraped HTML/JSON varies drastically by website and changes over time. Storing only cleaned relational tables risks losing original attributes when parsing logic is refined later.

## Decision
Implement a three-tier Medallion architecture:
1. **Bronze (MongoDB)**: Stores unmutated raw JSON/HTML payloads with audit metadata (`crawl_run_id`, timestamp).
2. **Silver (PostgreSQL)**: Normalized tables (`listings`, `listing_details`, `advertisers`) with parsed types, geo-points, and deduplication keys.
3. **Gold (PostgreSQL & Materialized Views)**: Append-only price history (`listing_price_history`) and aggregated market metrics (`mv_ward_avg_price_per_m2`).

## Consequences
- **Pros**: Complete audit trail; ability to re-parse historical Bronze data with new rules without re-crawling; fast analytical queries on Gold layer.
- **Cons**: Storage overhead across both MongoDB and PostgreSQL.

