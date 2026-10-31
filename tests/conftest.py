import json
from pathlib import Path

import pytest

SEED = Path(__file__).resolve().parents[1] / "sql" / "02_seed_configs.sql"


@pytest.fixture
def seed_config_json() -> dict:
    """The config_json object exactly as inserted by the seed file."""
    text = SEED.read_text(encoding="utf-8")
    marker = '{\n      "request": {'
    start_idx = text.find(marker)
    assert start_idx != -1, f"'{marker}' not found in seed file"
    end_idx = text.find("}'::jsonb", start_idx)
    assert end_idx != -1, "'}'::jsonb not found in seed file"
    json_str = text[start_idx : end_idx + 1]
    return json.loads(json_str)


@pytest.fixture
def seed_row(seed_config_json) -> dict:
    """A crawler_configs row as psycopg would return it."""
    return {
        "site_id": "meeyland",
        "name": "Meeyland - Mua bán BĐS",
        "enabled": True,
        "crawler_type": "rest_api",
        "schedule_times": [],
        "interval_seconds": 300,
        "config_json": seed_config_json,
    }
