"""Contract tests verifying all YAML site configuration files load and validate against CrawlerConfig."""

from pathlib import Path

import pytest
import yaml

from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.infrastructure.config.site_configs import load_site_config, load_site_configs
from realestate.infrastructure.persistence.postgres.config_mapping import (
    config_to_row,
    row_to_config,
)

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "sites"


def test_site_configs_directory_exists():
    assert CONFIGS_DIR.exists() and CONFIGS_DIR.is_dir(), f"Config directory {CONFIGS_DIR} not found"


def get_all_yaml_files():
    return sorted(list(CONFIGS_DIR.glob("*.yaml")) + list(CONFIGS_DIR.glob("*.yml")))


@pytest.mark.parametrize("config_path", get_all_yaml_files(), ids=lambda p: p.stem)
def test_yaml_config_contract(config_path: Path):
    content = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    assert isinstance(data, dict), f"Config in {config_path.name} is not a valid YAML mapping"
    assert "site_id" in data, f"{config_path.name} missing 'site_id'"
    assert "name" in data, f"{config_path.name} missing 'name'"
    assert "request" in data, f"{config_path.name} missing 'request'"
    assert "response_mapping" in data, f"{config_path.name} missing 'response_mapping'"

    # Contract: Must instantiate cleanly into domain CrawlerConfig model
    config = CrawlerConfig(**data)
    assert config.site_id == data["site_id"]
    assert config.name == data["name"]

    # Budget invariants
    assert config.pagination.max_pages_full_sweep >= config.pagination.max_pages_incremental
    # Politeness invariants
    assert config.rate_limit.download_delay_sec > 0
    assert config.rate_limit.timeout_sec > 0



def test_loader_accepts_every_site_config():
    """The same loader used by `realestate sync-configs` must accept the whole directory."""
    configs = load_site_configs(CONFIGS_DIR)
    site_ids = [c.site_id for c in configs]
    assert len(site_ids) == len(set(site_ids)), "duplicate site_id across YAML files"
    assert {"meeyland", "nhatot", "nhatot_hn"} <= set(site_ids)


@pytest.mark.parametrize("config_path", get_all_yaml_files(), ids=lambda p: p.stem)
def test_yaml_config_survives_db_round_trip(config_path: Path):
    """YAML -> crawler_configs row -> CrawlerConfig must be lossless, otherwise sync would loop."""
    config = load_site_config(config_path)
    assert row_to_config(config_to_row(config)) == config
