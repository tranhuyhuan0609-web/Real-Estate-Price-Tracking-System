from __future__ import annotations
import re
from typing import Optional, Tuple, Dict
from .administrative_data import (
    DISTRICT_PREFIXES,
    WARD_PREFIXES,
    STREET_PREFIXES,
    KNOWN_HCM_DISTRICTS,
    is_province_token,
)

def canonicalize_vietnamese_location(
    city: Optional[str] = None,
    district: Optional[str] = None,
    ward: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Normalize administrative location names into standard canonical representations."""
    canon_city = city
    if city:
        c_clean = re.sub(r"[.\s]", "", city.strip().lower())
        if c_clean in ("hồchíminh", "tphcm", "tphồchíminh", "thànhphốhồchíminh", "hcm", "hcmc", "sàigòn", "saigon"):
            canon_city = "Thành phố Hồ Chí Minh"
        elif c_clean in ("hànội", "tphànội", "thànhphốhànội", "hn"):
            canon_city = "Thành phố Hà Nội"
        elif c_clean in ("đànẵng", "tpđànẵng", "thànhphốđànẵng"):
            canon_city = "Thành phố Đà Nẵng"
        elif c_clean in ("hảiphòng", "tphảiphòng", "thànhphốhảiphòng"):
            canon_city = "Thành phố Hải Phòng"
        elif c_clean in ("cầnthơ", "tpcầnthơ", "thànhphốcầnthơ"):
            canon_city = "Thành phố Cần Thơ"
        elif c_clean in ("bàrịa-vũngtàu", "bàrịavũngtàu", "vũngtàu", "tỉnhbàrịavũngtàu"):
            canon_city = "Bà Rịa - Vũng Tàu"
        elif c_clean in ("thừathiênhuế", "huế", "tỉnhthừathiênhuế"):
            canon_city = "Thừa Thiên Huế"
        else:
            canon_city = city.strip()

    canon_district = district
    if district:
        d_str = district.strip()
        m_num = re.match(r"^(?:quận|q\.?)\s*0?(\d+)$", d_str, re.IGNORECASE)
        if m_num:
            canon_district = f"Quận {m_num.group(1)}"
        else:
            d_norm = re.sub(r"^q\.?\s+", "Quận ", d_str, flags=re.IGNORECASE)
            d_norm = re.sub(r"^h\.?\s+", "Huyện ", d_norm, flags=re.IGNORECASE)
            d_norm = re.sub(r"^tx\.?\s+", "Thị xã ", d_norm, flags=re.IGNORECASE)
            d_norm = re.sub(r"^tp\.?\s+", "Thành phố ", d_norm, flags=re.IGNORECASE)

            d_lower = d_norm.lower().strip()
            if d_lower in KNOWN_HCM_DISTRICTS and not re.match(r"^(?:quận|huyện|thị xã|thành phố)\s+", d_norm, re.IGNORECASE):
                canon_district = KNOWN_HCM_DISTRICTS[d_lower]
            else:
                canon_district = d_norm

    canon_ward = ward
    if ward:
        w_str = ward.strip()
        m_wnum = re.match(r"^(?:phường|p\.?)\s*0?(\d+)$", w_str, re.IGNORECASE)
        if m_wnum:
            canon_ward = f"Phường {m_wnum.group(1)}"
        else:
            w_norm = re.sub(r"^p\.?\s+", "Phường ", w_str, flags=re.IGNORECASE)
            w_norm = re.sub(r"^x\.?\s+", "Xã ", w_norm, flags=re.IGNORECASE)
            w_norm = re.sub(r"^tt\.?\s+", "Thị trấn ", w_norm, flags=re.IGNORECASE)
            canon_ward = w_norm

    return canon_city, canon_district, canon_ward

def parse_vietnamese_address(
    raw_address: Optional[str] = None,
    city_hint: Optional[str] = None,
    district_hint: Optional[str] = None,
    ward_hint: Optional[str] = None,
    canonicalize: bool = True,
) -> dict[str, Optional[str]]:
    """Parse a Vietnamese address string into structured components:
    street_name, ward_name, district_name, city_name.
    """
    street = None
    ward = ward_hint.strip() if ward_hint else None
    district = district_hint.strip() if district_hint else None
    city = city_hint.strip() if city_hint else None

    if city and ("," in city or " - " in city):
        raw_address = f"{raw_address}, {city}" if raw_address else city
        city = None
    if district and ("," in district or " - " in district):
        raw_address = f"{raw_address}, {district}" if raw_address else district
        district = None
    if ward and ("," in ward or " - " in ward):
        raw_address = f"{raw_address}, {ward}" if raw_address else ward
        ward = None

    if not raw_address:
        if canonicalize:
            city, district, ward = canonicalize_vietnamese_location(city, district, ward)
        return {
            "street_name": street,
            "ward_name": ward,
            "district_name": district,
            "city_name": city,
        }

    addr = re.sub(r"\s+", " ", raw_address.strip())
    parts = [p.strip() for p in re.split(r"[,;]|\s+-\s+", addr) if p.strip()]

    if not parts:
        if canonicalize:
            city, district, ward = canonicalize_vietnamese_location(city, district, ward)
        return {
            "street_name": street,
            "ward_name": ward,
            "district_name": district,
            "city_name": city,
        }

    # 1. Identify City/Province (usually rightmost)
    if not city:
        for idx in range(len(parts) - 1, -1, -1):
            if is_province_token(parts[idx]):
                city = parts.pop(idx)
                break

    # 2. Check remaining structure based on count of remaining parts
    if parts:
        if len(parts) == 1:
            p = parts[0]
            p_lower = p.lower()
            if not city and is_province_token(p):
                city = parts.pop(0)
            elif not district and (re.match(rf"^{DISTRICT_PREFIXES}\s+", p_lower) or not ward):
                district = parts.pop(0)
            elif not ward and re.match(rf"^{WARD_PREFIXES}\s+", p_lower):
                ward = parts.pop(0)
            elif not street:
                street = parts.pop(0)
            else:
                district = district or parts.pop(0)
        elif len(parts) == 2:
            p0, p1 = parts[0], parts[1]
            p0_lower = p0.lower()
            if not district:
                district = parts.pop(1)
            if not ward and not re.match(rf"^{STREET_PREFIXES}\s+", p0_lower):
                ward = parts.pop(0)
            elif not street:
                street = parts.pop(0)
        elif len(parts) == 3:
            if not district:
                district = parts.pop(2)
            if not ward:
                ward = parts.pop(1)
            if not street and parts:
                street = parts.pop(0)
        else: # >= 4 parts
            if not district:
                district = parts.pop(-1)
            if not ward:
                ward = parts.pop(-1)
            if parts:
                street = ", ".join(parts).strip()

    if canonicalize:
        city, district, ward = canonicalize_vietnamese_location(city, district, ward)

    return {
        "street_name": street,
        "ward_name": ward,
        "district_name": district,
        "city_name": city,
    }
