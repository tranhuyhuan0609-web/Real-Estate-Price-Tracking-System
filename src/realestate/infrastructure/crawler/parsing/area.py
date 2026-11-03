from __future__ import annotations
import re
from typing import Any, Optional
from realestate.infrastructure.crawler.parsing.price import normalize_vietnamese_number_str

def parse_vietnamese_area(raw: Any) -> Optional[float]:
    """Parse property area in m2 from float or text like '85.5 m2', '1.200 m2'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if 0 < val <= 5_000_000 else None
    text = str(raw).strip().lower()
    if text.startswith("-"):
        return None
    norm_text = normalize_vietnamese_number_str(text)

    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", norm_text):
        val = float(norm_text)
        return val if 0 < val <= 5_000_000 else None

    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:m[2²]|m\^2|mét\s*vuông)\b", norm_text)
    if m:
        try:
            val = float(m.group(1))
            return val if 0 < val <= 5_000_000 else None
        except ValueError:
            pass

    m2 = re.search(r"([0-9]+(?:[\.,][0-9]+)?)\s*(?:m[2²]|m\^2|mét\s*vuông)\b", text)
    if m2:
        try:
            val_str = normalize_vietnamese_number_str(m2.group(1))
            val = float(val_str)
            return val if 0 < val <= 5_000_000 else None
        except ValueError:
            pass
    return None
