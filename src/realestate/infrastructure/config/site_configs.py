"""Load declarative site configs from ``configs/sites/*.yaml``."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.domain.exceptions import ConfigCorruptedError
from realestate.infrastructure.config.base import BASE_DIR

DEFAULT_SITES_DIR = BASE_DIR / "configs" / "sites"


def load_site_config(path: Path) -> CrawlerConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigCorruptedError(f"{path.name}: top level must be a mapping.")
    try:
        config = CrawlerConfig(**data)
    except ValidationError as exc:
        raise ConfigCorruptedError(f"{path.name}: {exc}") from exc
    if config.site_id != path.stem:
        raise ConfigCorruptedError(
            f"{path.name}: site_id '{config.site_id}' must match the file name '{path.stem}'."
        )
    return config


def load_site_configs(directory: Path = DEFAULT_SITES_DIR) -> list[CrawlerConfig]:
    """Load and validate every ``*.yaml`` / ``*.yml`` file, sorted by file name."""
    if not directory.is_dir():
        raise FileNotFoundError(f"Site config directory not found: {directory}")
    paths = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    return [load_site_config(p) for p in paths]
