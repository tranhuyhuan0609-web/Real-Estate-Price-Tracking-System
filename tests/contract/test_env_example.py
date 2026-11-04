"""`.env.example` must document exactly the variables that `Settings` reads."""

from pathlib import Path

from realestate.infrastructure.config.settings import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _example_keys() -> set[str]:
    keys = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_matches_settings_fields():
    fields = set(Settings.model_fields)
    keys = _example_keys()
    assert keys - fields == set(), f"documented but not read by Settings: {sorted(keys - fields)}"
    assert fields - keys == set(), f"read by Settings but undocumented: {sorted(fields - keys)}"
