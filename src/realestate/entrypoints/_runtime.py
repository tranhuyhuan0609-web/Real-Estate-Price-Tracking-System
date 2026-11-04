"""Process-level setup shared by every entrypoint (scheduler, workers, CLI).

Library modules must never configure logging or the event loop themselves: they
only call ``logging.getLogger(__name__)``. Each process calls ``run_service`` /
``configure_runtime`` exactly once from its ``main()``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from realestate.infrastructure.config.settings import Settings, load_settings
from realestate.infrastructure.observability.metrics import start_metrics_server

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_runtime() -> None:
    """Configure logging from settings and make asyncio work with psycopg on Windows."""
    if sys.platform == "win32":
        # psycopg's async driver cannot run on the default Proactor loop.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8")

    level = load_settings().CRAWLER_LOG_LEVEL
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)


def serve_metrics() -> Settings:
    """Start the Prometheus endpoint for long-running services (not for CLI commands)."""
    settings = load_settings()
    start_metrics_server(settings.METRICS_PORT)
    return settings


def run_service(main_coro: Coroutine[Any, Any, None], on_shutdown: Callable[[], None]) -> None:
    """Run a long-lived service until it returns or SIGINT/SIGTERM is received."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_signal(*_: object) -> None:
        logging.getLogger(__name__).info("Shutdown signal received, exiting gracefully...")
        on_shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:  # Windows event loops
            signal.signal(sig, _handle_signal)

    try:
        loop.run_until_complete(main_coro)
    finally:
        loop.close()
