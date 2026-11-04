"""Vietnamese price parsing ("3 tỷ 500 triệu", "45 triệu/m²", ...)."""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

MAX_VALID_PRICE_VND = 5_000_000_000_000  # 5,000 tỷ VNĐ sanity threshold

Price = Tuple[Optional[float], Optional[float]]  # (price_vnd, price_per_m2)

_NUM = r"([0-9]+(?:[\.,][0-9]+)?)"
_TY = r"(?:tỷ|ty|tỉ|ti)"
_TRIEU = r"(?:triệu|trieu|tr)"
_NGHIN = r"(?:nghìn|ngan|k)"
_UNITS = {
    1_000_000_000: ("tỷ", "ty", "tỉ", "ti"),
    1_000_000: ("triệu", "trieu", "tr"),
    1_000: ("nghìn", "ngan", "k"),
}
# Words after the second number that mean it is not part of the price ("3 tỷ 100 m2").
_NOT_PRICE_AFTER = r"(?!\s*(?:m[2²]|m\b|mét|ha|sào|phòng|căn|pn|wc)\b)"
_NEGOTIABLE = ("thỏa thuận", "thoa thuan", "liên hệ", "lien he", "tùy", "đang cập nhật")

_PER_M2_RE = re.compile(rf"{_NUM}\s*({_TY}|{_TRIEU}|{_NGHIN})\s*/\s*(?:m[2²]|m\b)")
_RENT_SUFFIX_RE = re.compile(r"/\s*(?:tháng|thang|th|năm|nam)\b")
_COMPOUNDS = (  # (major unit regex, major multiplier, minor unit regex)
    (_TY, 1_000_000_000, _TRIEU),
    (_TRIEU, 1_000_000, _NGHIN),
)
_SINGLE_RE = re.compile(rf"{_NUM}\s*(tỷ|ty|tỉ|ti|triệu|trieu|tr|nghìn|ngan|k)\b")
_ZERO_RE = re.compile(r"^0+(?:[\.,]0+)?\s*(?:đ|d|đồng|dong|vnd|tỷ|triệu|tr)?$")


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _multiplier(unit: str) -> int:
    return next(mult for mult, names in _UNITS.items() if unit in names)


def _result(total: float, area: Optional[float]) -> Price:
    if total > MAX_VALID_PRICE_VND:
        return None, None
    return total, (round(total / area, 0) if area and area > 0 else None)


def parse_vietnamese_price(raw: Any, area: Optional[float] = None) -> Price:
    """Parse a price from a number or Vietnamese text ("5.2 tỷ", "850 triệu", "45 tr/m2").

    ``area`` (m²) is used to derive the other half of the (total, per-m²) pair.
    """
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        return (None, None) if raw <= 0 else _result(float(raw), area)

    text = str(raw).strip().lower()
    if not text or any(k in text for k in _NEGOTIABLE) or _ZERO_RE.match(text):
        return None, None

    for parse in (_per_m2, _compound, _single, _digits):
        result = parse(text, area)
        if result is not None:
            return result
    return None, None


def _per_m2(text: str, area: Optional[float]) -> Optional[Price]:
    """ "45 triệu/m2", "1.5 tỷ/m2", "48tr/m" """
    m = _PER_M2_RE.search(text)
    if not m:
        return None
    price_m2 = _num(m.group(1)) * _multiplier(m.group(2))
    total = round(price_m2 * area) if area and area > 0 else None
    if total and total > MAX_VALID_PRICE_VND:
        return None, None
    return total, price_m2


def _compound(text: str, area: Optional[float]) -> Optional[Price]:
    """ "1 tỷ 250 triệu", "2 tỷ 5" (= 2.5 tỷ), "2 triệu 500 nghìn".

    Without a minor unit the second number is read like a decimal: "2 tỷ 5" -> 500 triệu,
    "2 tỷ 25" -> 250 triệu, "2 tỷ 250" -> 250 triệu.
    """
    text = _RENT_SUFFIX_RE.sub("", text)
    for major_re, major, minor_re in _COMPOUNDS:
        m = re.search(
            rf"{_NUM}\s*{major_re}\s*{_NUM}\b(?:{_NOT_PRICE_AFTER}\s*({minor_re})?)", text
        )
        if not m:
            continue
        minor_raw, minor_text = _num(m.group(2)), m.group(2)
        if m.group(3) or "." in minor_text:
            minor = minor_raw * (major // 1000)
        elif minor_raw < 10:
            minor = minor_raw * (major // 10)
        elif minor_raw < 100:
            minor = minor_raw * (major // 100)
        else:
            minor = minor_raw * (major // 1000)
        return _result(_num(m.group(1)) * major + minor, area)
    return None


def _single(text: str, area: Optional[float]) -> Optional[Price]:
    """ "5,2 tỷ", "850 triệu", "500 k" """
    m = _SINGLE_RE.search(_RENT_SUFFIX_RE.sub("", text))
    return _result(_num(m.group(1)) * _multiplier(m.group(2)), area) if m else None


def _digits(text: str, area: Optional[float]) -> Optional[Price]:
    """ "2500000000", "2.500.000.000 đ" """
    digits = re.sub(r"[^\d]", "", _RENT_SUFFIX_RE.sub("", text))
    return _result(float(digits), area) if len(digits) >= 6 else None
