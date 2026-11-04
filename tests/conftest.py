import json
from pathlib import Path
from typing import Any

import pytest

from realestate.infrastructure.config.site_configs import load_site_config
from realestate.infrastructure.persistence.postgres.config_mapping import config_to_row

ROOT = Path(__file__).resolve().parents[1]
SITES_DIR = ROOT / "configs" / "sites"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Load a JSON (``*.json``) or text (``*.html``) sample from tests/fixtures."""
    path = FIXTURES_DIR / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


@pytest.fixture
def seed_row() -> dict:
    """A crawler_configs row (as psycopg returns it) built from configs/sites/meeyland.yaml.

    Schedule columns are pinned so unit tests do not depend on production schedules.
    """
    row = config_to_row(load_site_config(SITES_DIR / "meeyland.yaml"))
    row["schedule_times"] = []
    row["interval_seconds"] = 300
    return row
