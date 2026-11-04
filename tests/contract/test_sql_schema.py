"""Keep sql/init.sql (generated from migrations) and the code that names tables in agreement."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INIT_SQL = ROOT / "sql" / "init.sql"


def _norm(stmt: str) -> str:
    stmt = re.sub(r"--[^\n]*", "", stmt)
    return re.sub(r"\s+", " ", stmt).strip().rstrip(";").lower()


def _statements(sql_text: str) -> set[str]:
    return {n for n in (_norm(s) for s in sql_text.split(";")) if n}


def _init_statements() -> set[str]:
    return _statements(INIT_SQL.read_text(encoding="utf-8-sig"))


def _tables() -> set[str]:
    return set(re.findall(r"create table if not exists (\w+)", " ".join(_init_statements())))


def test_init_sql_is_generated_from_migrations():
    """Migrations are the only schema source; init.sql must be their rendered output."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/generate_init_sql.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cli_reset_only_truncates_existing_tables():
    from realestate.entrypoints.cli import RESET_TABLES

    assert set(RESET_TABLES) <= _tables()


def test_schema_needs_postgis_image():
    """init.sql enables PostGIS, so compose must run an image that ships it."""
    compose = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "create extension if not exists postgis" in _init_statements()
    assert re.search(r"image:\s*postgis/postgis", compose)
