"""Vietnamese price parsing ("3 tỷ 500 triệu", "45 triệu/m²", ...)."""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

MAX_VALID_PRICE_VND = 5_000_000_000_000  # 5,000 tỷ VNĐ sanity threshold


def parse_vietnamese_price(raw: Any, area: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
    """Parse real estate price from raw float, int or Vietnamese text (e.g. '5.2 tỷ', '850 triệu', '45 tr/m2').

    Returns:
        Tuple[Optional[float], Optional[float]]: (price_vnd, price_per_m2)
    """
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val <= 0 or val > MAX_VALID_PRICE_VND:
            return None, None
        pm2 = round(val / area, 0) if area and area > 0 else None
        return val, pm2

    text = str(raw).strip().lower()
    if not text or any(k in text for k in ["thỏa thuận", "thoa thuan", "liên hệ", "lien he", "tùy", "đang cập nhật"]):
        return None, None
    if re.match(r"^0+(?:[\.,]0+)?\s*(?:đ|d|đồng|dong|vnd|tỷ|triệu|tr)?$", text):
        return None, None

    # 1. Price per m2: e.g. "45 triệu/m2" or "45 tr/m2" or "1.5 tỷ/m2" or "48tr/m"
    m_per_m2 = re.search(r"([0-9]+(?:[\.,][0-9]+)?)\s*(tỷ|ty|tỉ|ti|triệu|trieu|tr|nghìn|ngan|k)\s*/\s*(?:m[2²]|m\b)", text)
    if m_per_m2:
        num = float(m_per_m2.group(1).replace(",", "."))
        unit = m_per_m2.group(2)
        if any(c in unit for c in ["tỷ", "ty", "tỉ", "ti"]):
            mult = 1_000_000_000
        elif any(c in unit for c in ["triệu", "trieu", "tr"]):
            mult = 1_000_000
        else:
            mult = 1_000
        price_m2 = num * mult
        price_vnd = round(price_m2 * area) if area and area > 0 else None
        if price_vnd and price_vnd > MAX_VALID_PRICE_VND:
            return None, None
        return price_vnd, price_m2

    # Clean rental suffixes e.g. '/tháng', '/th', '/năm'
    text = re.sub(r"/\s*(?:tháng|thang|th|năm|nam)\b", "", text)

    # 2. Compound price:
    # 2a. Tỷ + Triệu (e.g. "1 tỷ 250 triệu", "2 tỷ 100", "2 tỷ 5", "1 tỷ 25 triệu")
    # Negative lookahead ensures that an adjacent area (e.g. "3 tỷ 100 m2") is not mistakenly parsed as millions
    m_ty_tr = re.search(
        r"([0-9]+(?:[\.,][0-9]+)?)\s*(?:tỷ|ty|tỉ|ti)\s*"
        r"([0-9]+(?:[\.,][0-9]+)?)\b"
        r"(?:(?!\s*(?:m[2²]|m\b|mét|ha|sào|phòng|căn|pn|wc)\b)\s*(triệu|trieu|tr)?)",
        text,
    )
    if m_ty_tr:
        ty = float(m_ty_tr.group(1).replace(",", "."))
        tr_raw = float(m_ty_tr.group(2).replace(",", "."))
        has_unit = bool(m_ty_tr.group(3))
        if has_unit:
            tr = tr_raw * 1_000_000
        else:
            if tr_raw < 10 and "." not in m_ty_tr.group(2):
                tr = tr_raw * 100_000_000
            elif tr_raw < 100 and "." not in m_ty_tr.group(2):
                tr = tr_raw * 10_000_000
            else:
                tr = tr_raw * 1_000_000
        total = ty * 1_000_000_000 + tr
        if total > MAX_VALID_PRICE_VND:
            return None, None
        pm2 = round(total / area, 0) if area and area > 0 else None
        return total, pm2

    # 2b. Triệu + Nghìn (e.g. "2 triệu 500 nghìn", "2 triệu 50 nghìn", "2 triệu 5")
    m_tr_k = re.search(
        r"([0-9]+(?:[\.,][0-9]+)?)\s*(?:triệu|trieu|tr)\s*"
        r"([0-9]+(?:[\.,][0-9]+)?)\b"
        r"(?:(?!\s*(?:m[2²]|m\b|mét|ha|sào|phòng|căn|pn|wc)\b)\s*(nghìn|ngan|k)?)",
        text,
    )
    if m_tr_k:
        tr = float(m_tr_k.group(1).replace(",", "."))
        k_raw = float(m_tr_k.group(2).replace(",", "."))
        has_unit = bool(m_tr_k.group(3))
        if has_unit:
            k = k_raw * 1_000
        else:
            if k_raw < 10 and "." not in m_tr_k.group(2):
                k = k_raw * 100_000
            elif k_raw < 100 and "." not in m_tr_k.group(2):
                k = k_raw * 10_000
            else:
                k = k_raw * 1_000
        total = tr * 1_000_000 + k
        if total > MAX_VALID_PRICE_VND:
            return None, None
        pm2 = round(total / area, 0) if area and area > 0 else None
        return total, pm2

    # 3. Single unit: "5,2 tỷ", "850 triệu", "500 k"
    m_single = re.search(r"([0-9]+(?:[\.,][0-9]+)?)\s*(tỷ|ty|tỉ|ti|triệu|trieu|tr|nghìn|ngan|k)\b", text)
    if m_single:
        num = float(m_single.group(1).replace(",", "."))
        unit = m_single.group(2)
        if unit in ("tỷ", "ty", "tỉ", "ti"):
            total = num * 1_000_000_000
        elif unit in ("triệu", "trieu", "tr"):
            total = num * 1_000_000
        else:
            total = num * 1_000
        if total > MAX_VALID_PRICE_VND:
            return None, None
        pm2 = round(total / area, 0) if area and area > 0 else None
        return total, pm2

    # 4. Pure digits: "2500000000"
    clean_digits = re.sub(r"[^\d]", "", text)
    if clean_digits and len(clean_digits) >= 6:
        val = float(clean_digits)
        if val > MAX_VALID_PRICE_VND:
            return None, None
        pm2 = round(val / area, 0) if area and area > 0 else None
        return val, pm2

    return None, None
