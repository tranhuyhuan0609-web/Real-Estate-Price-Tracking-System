"""Area and plot-dimension parsing ("85,5 m²", "5m x 16m")."""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from realestate.infrastructure.crawler.parsing.numbers import (
    normalize_vietnamese_number_str,
)


def parse_vietnamese_area(raw: Any) -> Optional[float]:
    """Parse property area in m2 from float or text like '85.5 m2', '1.200 m2'.

    Requires explicit area units (m2, m², mét vuông) or a purely numeric string
    to avoid falsely extracting non-area numbers (e.g. frontage, bedroom count).
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if 0 < val <= 5_000_000 else None
    text = str(raw).strip().lower()
    if text.startswith("-"):  # "-50 m2" is not an area; the unit regex below would read 50
        return None
    norm_text = normalize_vietnamese_number_str(text)

    # 1. Pure numeric string
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", norm_text):
        val = float(norm_text)
        return val if 0 < val <= 5_000_000 else None
    # 2. String containing explicit area unit (m2, m², m^2, mét vuông)
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:m[2²]|m\^2|mét\s*vuông)\b", norm_text)
    if m:
        try:
            val = float(m.group(1))
            return val if 0 < val <= 5_000_000 else None
        except ValueError:
            pass
    # Fallback search in raw text
    m2 = re.search(r"([0-9]+(?:[\.,][0-9]+)?)\s*(?:m[2²]|m\^2|mét\s*vuông)\b", text)
    if m2:
        try:
            val_str = normalize_vietnamese_number_str(m2.group(1))
            val = float(val_str)
            return val if 0 < val <= 5_000_000 else None
        except ValueError:
            pass
    return None


def extract_dimensions(text: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """Extract facade (mặt tiền / ngang) and depth (chiều sâu / dài) from text in meters.

    Matches:
        - Dimension multiplication: '5x20m', '5 x 20m', '4.5*18', '3,5 x 15', 'DT: 5x20'
        - Explicit keyword patterns: 'mặt tiền 5m dài 20m', 'ngang 4.5m sâu 18m'

    Returns:
        Tuple[Optional[float], Optional[float]]: (facade_m, depth_m)
    """
    if not text:
        return None, None

    facade: Optional[float] = None
    depth: Optional[float] = None

    # 1. Explicit keyword matching
    m_facade = re.search(
        r"(?:mặt\s*tiền|ngang|bề\s*ngang)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:m|mét)?\b(?![2²])",
        text,
        re.IGNORECASE,
    )
    if m_facade:
        try:
            val = float(m_facade.group(1).replace(",", "."))
            if 0 < val < 500:
                facade = val
        except ValueError:
            pass

    m_depth = re.search(
        r"(?:chiều\s*dài|chiều\s*sâu|dài|sâu)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:m|mét)?\b(?![2²])",
        text,
        re.IGNORECASE,
    )
    if m_depth:
        try:
            val = float(m_depth.group(1).replace(",", "."))
            if 0 < val < 1000:
                depth = val
        except ValueError:
            pass

    # 2. Multiplication format: e.g. "5x20m", "5 x 20m", "4.5*18", "3,5 x 15", "DT: 5x20"
    if facade is None or depth is None:
        m_mult = re.search(
            r"(?:\b|dt|diện tích|kích thước|dt:)?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:m|mét)?\s*[xX*×]\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:m|mét)?\b(?![2²])",
            text,
            re.IGNORECASE,
        )
        if m_mult:
            try:
                w = float(m_mult.group(1).replace(",", "."))
                d = float(m_mult.group(2).replace(",", "."))
                if 0 < w < 500 and 0 < d < 1000:
                    if facade is None:
                        facade = w
                    if depth is None:
                        depth = d
            except ValueError:
                pass

    return facade, depth
