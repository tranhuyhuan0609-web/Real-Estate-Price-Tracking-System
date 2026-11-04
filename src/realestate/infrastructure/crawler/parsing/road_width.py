"""Road / alley width extraction from listing descriptions."""

from __future__ import annotations

import re
from typing import Optional

from realestate.infrastructure.crawler.parsing.numbers import safe_float

ROAD_WIDTH_PATTERNS = [
    re.compile(
        r"(?:đường|ngõ|mặt\s*ngõ|hẻm|lộ(?:\s*giới|\s*nhựa)?)"
        r"(?:\s*(?:trước\s*(?:nhà|đất|mặt|cửa)|vào\s*(?:nhà|đất)))?"
        r"(?:\s*rộng)?"
        r"\s*[:=]?\s*([0-9]+(?:[\.,][0-9]+)?)\s*(?:m|mét)\b(?![2²])",
        re.IGNORECASE,
    ),
    re.compile(
        r"([0-9]+(?:[\.,][0-9]+)?)\s*(?:m|mét)\b(?![2²])\s*(?:đường|ngõ|mặt\s*ngõ|hẻm|lộ|ô\s*tô)",
        re.IGNORECASE,
    ),
    re.compile(
        r"ô\s*tô\s*(?:tránh|đỗ\s*cửa|vào\s*(?:nhà|đất))\s*[,.]?\s*(?:đường|ngõ|hẻm)?\s*([0-9]+(?:[\.,][0-9]+)?)\s*(?:m|mét)\b(?![2²])",
        re.IGNORECASE,
    ),
]


def extract_road_width(description: Optional[str]) -> Optional[float]:
    """Extract road/alley width in meters using regex."""
    if not description:
        return None
    for pattern in ROAD_WIDTH_PATTERNS:
        match = pattern.search(description)
        if match:
            val = safe_float(match.group(1))
            if val is not None and 1.0 <= val <= 100.0:
                return round(val, 2)
    return None
