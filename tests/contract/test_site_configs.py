"""Contract tests verifying all YAML site configuration files load and validate against CrawlerConfig."""

from pathlib import Path
import pytest
import yaml
from realestate.domain.entities.crawler_config import CrawlerConfig

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

