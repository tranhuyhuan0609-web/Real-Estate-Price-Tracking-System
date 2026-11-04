"""Dotted / indexed JSON path lookup with comma-separated fallbacks."""

from __future__ import annotations

import re
from typing import Any


def extract_by_json_path(data: Any, path: str) -> Any:
    """Extract nested value by dotted path supporting array indexes like 'a.b[0].c'.
    Also supports comma-separated fallback candidate paths: 'seller_info.full_name, full_name, account_name'.
    """
    if not path or data is None:
        return None

    candidate_paths = [p.strip() for p in path.split(",") if p.strip()]
    for candidate in candidate_paths:
        tokens = re.findall(r"([a-zA-Z0-9_-]+)|\[(\d+)\]", candidate)
        current = data
        for prop, idx in tokens:
            if current is None:
                break
            if prop:
                if isinstance(current, dict):
                    current = current.get(prop)
                else:
                    current = None
                    break
            elif idx:
                index = int(idx)
                if isinstance(current, list) and 0 <= index < len(current):
                    current = current[index]
                else:
                    current = None
                    break
        if current is not None and current != "":
            return current

    return None
