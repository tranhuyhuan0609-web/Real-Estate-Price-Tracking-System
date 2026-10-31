"""CLI entrypoint to run Bronze Consumer (Kafka -> MongoDB Bronze raw_listings)."""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from src.infrastructure.messaging.raw_event_consumer import RawEventConsumerService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bronze_consumer")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bronze Consumer Worker (Kafka -> MongoDB)")
    parser.add_argument("--once", action="store_true", help="Process available messages and exit when idle")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for consuming events (default: 50)")
    args = parser.parse_args()

    service = RawEventConsumerService(
        batch_size=args.batch_size,
        run_once=args.once,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal_handler():
        service.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(service.run())
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt. Shutting down Bronze Consumer...")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
