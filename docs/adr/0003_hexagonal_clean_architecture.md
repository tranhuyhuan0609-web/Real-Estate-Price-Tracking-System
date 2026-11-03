# ADR 0003: Hexagonal Clean Architecture

## Status
Accepted

## Context
Previous iterations mixed database queries, Kafka producer logic, and HTTP client code directly inside spiders and use cases. This made unit testing difficult and caused tight coupling to PostgreSQL, MongoDB, and aiokafka.

## Decision
Refactor codebase into explicit Hexagonal layers:
- `realestate.domain`: Pure business rules, entities, value objects, and domain services. Zero dependencies on infrastructure or external libraries (except standard typing and pydantic models).
- `realestate.application`: Orchestration use cases (`crawl_site`, `ingest_silver_gold`, `store_bronze`, `apply_lifecycle`) interacting only via `ports` (Protocols).
- `realestate.infrastructure`: Concrete implementations of ports (PostgreSQL repos, Mongo repos, Kafka publishers/consumers, HTTP spiders).
- `realestate.entrypoints`: Thin composition runners (CLI, scheduler, workers) wire dependencies using `bootstrap.py`.

## Consequences
- **Pros**: Domain and use cases can be 100% unit tested in milliseconds without database or broker containers; infrastructure adapters can be swapped or upgraded independently.
- **Cons**: Additional interface protocols and adapter mappings required.

