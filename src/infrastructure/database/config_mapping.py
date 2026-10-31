from __future__ import annotations
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.application.exceptions.config_exceptions import ConfigCorruptedError
from src.domain.entities.crawler_config import CrawlerConfig

# Stored as real columns of crawler_configs; everything else is config_json.
COLUMN_FIELDS = (
    "site_id",
    "name",
    "enabled",
    "crawler_type",
    "schedule_times",
    "interval_seconds",
)


def row_to_config(row: Mapping[str, Any]) -> CrawlerConfig:
    """Merge the columns and config_json of one row into a CrawlerConfig.

    Columns are the source of truth. A config_json that repeats a column with
    a different value is rejected, because it means the row was edited by hand
    and the two copies have drifted apart.
    """
    site_id = row["site_id"]
    data = row["config_json"]

    if not isinstance(data, dict):
        raise ConfigCorruptedError(
            f"config_json must be an object for site_id '{site_id}'."
        )

    columns = {name: row[name] for name in COLUMN_FIELDS}
    columns["schedule_times"] = list(columns["schedule_times"] or [])

    for name, value in columns.items():
        if name in data and data[name] != value:
            raise ConfigCorruptedError(
                f"Column '{name}' does not match config_json "
                f"for site_id '{site_id}'."
            )

    try:
        return CrawlerConfig.model_validate({**data, **columns})
    except ValidationError as exc:
        raise ConfigCorruptedError(
            f"Invalid stored crawler configuration for site_id '{site_id}'."
        ) from exc


def config_to_row(config: CrawlerConfig) -> dict[str, Any]:
    """Split a CrawlerConfig into column values plus the config_json object."""
    if not isinstance(config, CrawlerConfig):
        raise TypeError("config must be a CrawlerConfig instance.")

    dumped = config.model_dump(mode="json")
    row = {name: dumped.pop(name) for name in COLUMN_FIELDS}
    row["config_json"] = dumped
    return row
