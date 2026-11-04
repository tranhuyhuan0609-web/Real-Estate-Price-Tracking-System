"""Silver/Gold worker: Kafka raw_events -> PostgreSQL listings + price history."""

from __future__ import annotations

import argparse

from realestate.bootstrap import AppContainer
from realestate.entrypoints._runtime import configure_runtime, run_service, serve_metrics


async def _serve(container: AppContainer, worker) -> None:
    async with container:
        await container.open_pg()
        await container.start_publisher()  # DLQ
        await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Silver/Gold worker (Kafka -> PostgreSQL)")
    parser.add_argument("--batch-size", type=int, default=20, help="Max messages per poll")
    parser.add_argument("--once", action="store_true", help="Drain the topic and exit when idle")
    args = parser.parse_args()

    configure_runtime()
    serve_metrics()
    container = AppContainer()
    worker = container.silver_gold_worker(batch_size=args.batch_size, run_once=args.once)
    run_service(_serve(container, worker), worker.stop)


if __name__ == "__main__":
    main()
