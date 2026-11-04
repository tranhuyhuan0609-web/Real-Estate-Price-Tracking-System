"""Container liveness probe: healthy while the process main loop keeps beating.

    python -m realestate.entrypoints.healthcheck <process> [--max-age 120]

Exit 0 = healthy, 1 = stale or missing heartbeat (docker restarts the container).
"""

from __future__ import annotations

import argparse
import sys

from realestate.infrastructure.config.settings import load_settings
from realestate.infrastructure.observability.metrics import heartbeat_age


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Heartbeat liveness check")
    parser.add_argument("process", help="scheduler | bronze | silver_gold | dlq")
    parser.add_argument("--max-age", type=float, default=120.0, help="Seconds before unhealthy")
    args = parser.parse_args(argv)

    age = heartbeat_age(args.process, load_settings().HEARTBEAT_DIR)
    if age is None:
        print(f"{args.process}: no heartbeat yet", file=sys.stderr)
        return 1
    if age > args.max_age:
        print(
            f"{args.process}: last heartbeat {age:.0f}s ago (> {args.max_age:.0f}s)",
            file=sys.stderr,
        )
        return 1
    print(f"{args.process}: ok ({age:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
