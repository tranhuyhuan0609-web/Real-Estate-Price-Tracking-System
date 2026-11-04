"""DLQ worker: Kafka dlq_events -> MongoDB dlq_events (source for `realestate replay-dlq`)."""

from __future__ import annotations

import argparse

from realestate.bootstrap import AppContainer
from realestate.entrypoints._runtime import configure_runtime, run_service


async def _serve(container: AppContainer, worker) -> None:
    async with container:
        await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="DLQ worker (Kafka -> MongoDB)")
    parser.add_argument("--batch-size", type=int, default=50, help="Max messages per poll")
    parser.add_argument("--once", action="store_true", help="Drain the topic and exit when idle")
    args = parser.parse_args()

    configure_runtime()
    container = AppContainer()
    worker = container.dlq_worker(batch_size=args.batch_size, run_once=args.once)
    run_service(_serve(container, worker), worker.stop)


if __name__ == "__main__":
    main()
