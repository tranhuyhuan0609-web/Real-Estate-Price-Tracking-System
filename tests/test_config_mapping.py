import copy
import json

import pytest

from src.application.exceptions.config_exceptions import ConfigCorruptedError
from src.infrastructure.database.config_mapping import (
    COLUMN_FIELDS,
    config_to_row,
    row_to_config,
)


def test_row_to_config(seed_row):
    config = row_to_config(seed_row)
    assert config.site_id == "meeyland"
    assert config.interval_seconds == 300
    assert config.crawler_type == "rest_api"


def test_null_schedule_times_become_empty_list(seed_row):
    seed_row["schedule_times"] = None
    assert row_to_config(seed_row).schedule_times == []


def test_round_trip_keeps_columns_out_of_json(seed_row):
    config = row_to_config(seed_row)
    row = config_to_row(config)
    assert set(COLUMN_FIELDS) <= row.keys()
    assert not set(COLUMN_FIELDS) & row["config_json"].keys()
    json.dumps(row["config_json"])  # must be JSON-serializable

    again = row_to_config(row)
    assert again == config


def test_json_repeating_a_column_must_agree(seed_row):
    seed_row["config_json"]["interval_seconds"] = 300
    row_to_config(seed_row)

    seed_row["config_json"]["interval_seconds"] = 900
    with pytest.raises(ConfigCorruptedError, match="interval_seconds"):
        row_to_config(seed_row)


def test_invalid_config_json_is_corrupted(seed_row):
    row = copy.deepcopy(seed_row)
    del row["config_json"]["response_mapping"]
    with pytest.raises(ConfigCorruptedError, match="meeyland"):
        row_to_config(row)


def test_non_object_config_json_is_corrupted(seed_row):
    seed_row["config_json"] = []
    with pytest.raises(ConfigCorruptedError):
        row_to_config(seed_row)


def test_config_to_row_rejects_non_config():
    with pytest.raises(TypeError):
        config_to_row({"site_id": "x"})
