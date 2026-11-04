"""Render sql/init.sql from the Alembic migrations (the single schema source).

    uv run python scripts/generate_init_sql.py          # rewrite sql/init.sql
    uv run python scripts/generate_init_sql.py --check  # exit 1 if it is out of date (CI)

The output is `alembic upgrade head --sql`: it also creates and stamps alembic_version, so a
database initialised from init.sql continues with `make migrate` at the next revision.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT_SQL = ROOT / "sql" / "init.sql"
HEADER = (
    "-- GENERATED FILE - do not edit. Source of truth: migrations/versions/*.py\n"
    "-- Regenerate with: make schema  (scripts/generate_init_sql.py)\n"
    "-- Postgres runs this once when its data volume is empty; later changes ship as migrations.\n\n"
)


def render() -> str:
    # Offline mode never connects; Settings only needs syntactically valid values.
    env = {
        **os.environ,
        "POSTGRES_USER": "offline",
        "POSTGRES_DB": "offline",
        "POSTGRES_PASSWORD": "",
    }
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return HEADER + out.replace("\r\n", "\n")


def main() -> int:
    sql = render()
    if "--check" in sys.argv:
        current = INIT_SQL.read_text(encoding="utf-8") if INIT_SQL.exists() else ""
        if current != sql:
            print(
                "sql/init.sql is out of date with migrations/. Run `make schema`.", file=sys.stderr
            )
            return 1
        return 0
    INIT_SQL.write_text(sql, encoding="utf-8", newline="\n")
    print(f"wrote {INIT_SQL.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
