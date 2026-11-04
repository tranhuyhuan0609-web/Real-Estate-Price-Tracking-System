"""Hexagonal dependency rule (ADR 0003): imports may only point inwards.

entrypoints/bootstrap -> infrastructure -> application -> domain
"""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "src" / "realestate"

FORBIDDEN = {
    "domain": ("application", "infrastructure", "entrypoints", "bootstrap"),
    "application": ("infrastructure", "entrypoints", "bootstrap"),
    "infrastructure": ("entrypoints", "bootstrap"),
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_only_imports_inwards(layer: str):
    violations = []
    for path in sorted((PKG / layer).rglob("*.py")):
        for name in _imports(path):
            parts = name.split(".")
            if parts[0] == "realestate" and len(parts) > 1 and parts[1] in FORBIDDEN[layer]:
                violations.append(f"{path.relative_to(PKG)} imports {name}")
    assert not violations, "\n".join(violations)
