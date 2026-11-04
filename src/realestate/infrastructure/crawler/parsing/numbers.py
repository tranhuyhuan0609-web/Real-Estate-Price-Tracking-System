"""Number helpers shared by the Vietnamese text parsers."""

from __future__ import annotations

import re
from typing import Any, Optional


def normalize_vietnamese_number_str(s: str) -> str:
    """Normalize a Vietnamese numeric string with possible thousand/decimal separators.

    Examples:
        '1.200' -> '1200'
        '1,596' -> '1596'
        '1.200,5' -> '1200.5'
        '1,200.5' -> '1200.5'
        '85.5' -> '85.5'
        '85,5' -> '85.5'
    """
    s = s.strip()
    if "." in s and "," in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s:
        if re.search(r"^\d{1,3}(?:\.\d{3})+$", s):
            s = s.replace(".", "")
        elif re.search(r"\b\d{1,3}\.\d{3}(?!\d)", s):
            s = re.sub(r"(?<=\d)\.(\d{3})(?!\d)", r"\1", s)
    elif "," in s:
        if re.search(r"^\d{1,3}(?:,\d{3})+$", s):
            s = s.replace(",", "")
        elif re.search(r"\b\d{1,3},(\d{3})(?!\d)", s):
            s = re.sub(r"(?<=\d),(\d{3})(?!\d)", r"\1", s)
        else:
            s = s.replace(",", ".")
    return s


def safe_float(val: Any) -> Optional[float]:
    """Safely convert a value to float without raising ValueError."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = str(val).strip().replace(",", ".")
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def safe_int(val: Any) -> Optional[int]:
    """Safely convert a value to int without raising ValueError."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None
