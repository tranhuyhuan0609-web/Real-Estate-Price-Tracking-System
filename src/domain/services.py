"""Domain services for key generation, deduplication fingerprints, and price change evaluation."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional, Tuple
from src.domain.entities import PriceChangeEvaluation, PriceStatus
from src.domain.entities.listing_key import build_listing_key


def generate_listing_key(
    site_id: str,
    external_code: int | str | None = None,
    external_id: int | str | None = None,
) -> str:
    """Generate canonical listing_key using site_id and external codes/ids."""
    return build_listing_key(site_id, external_code=external_code, external_id=external_id)


def generate_fingerprint(
    *,
    title: str,
    area_m2: Optional[float] = None,
    city_name: Optional[str] = None,
    district_name: Optional[str] = None,
    ward_name: Optional[str] = None,
    advertiser_phone: Optional[str] = None,
    city_code: Optional[str] = None,
    district_code: Optional[str] = None,
    ward_code: Optional[str] = None,
) -> str:
    """Generate SHA-256 property fingerprint for cross-site deduplication and tracking."""
    # Normalize title: lowercase, strip punctuation and collapse whitespace
    norm_title = re.sub(r"[^\w\s]", "", title.lower())
    norm_title = " ".join(norm_title.split())

    norm_area = f"{round(area_m2, 1)}" if area_m2 and area_m2 > 0 else ""
    norm_city = (city_name or city_code or "").strip().lower()
    norm_district = (district_name or district_code or "").strip().lower()
    norm_ward = (ward_name or ward_code or "").strip().lower()
    norm_phone = re.sub(r"\D", "", advertiser_phone or "")

    raw_signature = f"{norm_title}|{norm_area}|{norm_city}|{norm_district}|{norm_ward}|{norm_phone}"
    return hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()


PROVINCE_KEYWORDS = [
    "hồ chí minh", "tp.hcm", "tphcm", "sài gòn", "hà nội", "đà nẵng", "hải phòng", "cần thơ",
    "an giang", "bà rịa - vũng tàu", "bà rịa vũng tàu", "vũng tàu", "bắc giang", "bắc kạn",
    "bạc liêu", "bắc ninh", "bến tre", "bình định", "bình dương", "bình phước", "bình thuận",
    "cà mau", "cao bằng", "đắk lắk", "đắk nông", "điện biên", "đồng nai", "đồng tháp",
    "gia lai", "hà giang", "hà nam", "hà tĩnh", "hải dương", "hậu giang", "hòa bình",
    "hưng yên", "khánh hòa", "kiên giang", "kon tum", "lai châu", "lâm đồng", "lạng sơn",
    "lào cai", "long an", "nam định", "nghệ an", "ninh bình", "ninh thuận", "phú thọ",
    "phú yên", "quảng bình", "quảng nam", "quảng ngãi", "quảng ninh", "quảng trị",
    "sóc trăng", "sơn la", "tây ninh", "thái bình", "thái nguyên", "thanh hóa",
    "thừa thiên huế", "huế", "tiền giang", "trà vinh", "tuyên quang", "vĩnh long",
    "vĩnh phúc", "yên bái"
]

DISTRICT_PREFIXES = r"(?:quận|huyện|thị xã|thành phố|tp\.?|tx\.?|q\.?|h\.?)"
WARD_PREFIXES = r"(?:phường|xã|thị trấn|tt\.?|p\.?|x\.?)"
STREET_PREFIXES = r"(?:đường|phố|ngõ|ngách|hẻm|số|kdc|kđt|khu đô thị|khu dân cư)"


def canonicalize_vietnamese_location(
    city: Optional[str] = None,
    district: Optional[str] = None,
    ward: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Normalize administrative location names into standard canonical representations.

    Examples:
        - 'TP.HCM', 'TP Hồ Chí Minh', 'Sài Gòn' -> 'Thành phố Hồ Chí Minh'
        - 'Hà Nội', 'TP. Hà Nội' -> 'Thành phố Hà Nội'
        - 'Q. 1', 'Q1', 'Quận 01' -> 'Quận 1'
        - 'Q. Bình Thạnh', 'Bình Thạnh' -> 'Quận Bình Thạnh'
        - 'P. 7', 'P.07', 'P7' -> 'Phường 7'
        - 'P. Bến Nghé' -> 'Phường Bến Nghé'
    """
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

            KNOWN_HCM_DISTRICTS = {
                "bình thạnh": "Quận Bình Thạnh",
                "gò vấp": "Quận Gò Vấp",
                "tân bình": "Quận Tân Bình",
                "tân phú": "Quận Tân Phú",
                "phú nhuận": "Quận Phú Nhuận",
                "bình tân": "Quận Bình Tân",
                "thủ đức": "Thành phố Thủ Đức",
                "nhà bè": "Huyện Nhà Bè",
                "hóc môn": "Huyện Hóc Môn",
                "củ chi": "Huyện Củ Chi",
                "bình chánh": "Huyện Bình Chánh",
                "cần giờ": "Huyện Cần Giờ",
            }
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

    # If city or district hints contain multiple segments, fold into raw_address and reset
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
        return {
            "street_name": street,
            "ward_name": ward,
            "district_name": district,
            "city_name": city,
        }

    # Clean raw string
    addr = re.sub(r"\s+", " ", raw_address.strip())
    parts = [p.strip() for p in re.split(r"[,;]|\s+-\s+", addr) if p.strip()]

    if not parts:
        return {
            "street_name": street,
            "ward_name": ward,
            "district_name": district,
            "city_name": city,
        }

    # 1. Identify City/Province (usually rightmost)
    if not city:
        for idx in range(len(parts) - 1, -1, -1):
            p_lower = parts[idx].lower()
            clean_p = re.sub(r"^(?:tỉnh|thành phố|tp\.?)\s+", "", p_lower).strip()
            if any(clean_p == prov or prov in clean_p for prov in PROVINCE_KEYWORDS):
                city = parts.pop(idx)
                break

    # 2. Check remaining structure based on count of remaining parts
    if parts:
        if len(parts) == 1:
            p = parts[0]
            p_lower = p.lower()
            if not city and any(prov in p_lower for prov in PROVINCE_KEYWORDS):
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


def evaluate_price_change(
    old_price: Optional[float | int],
    new_price: Optional[float | int],
) -> PriceChangeEvaluation:
    """Compare old and new price and return difference amount, percentage and status.

    Returns:
        PriceChangeEvaluation:
            - price_status: INITIAL, INCREASED, DECREASED, UNCHANGED, CONTACT
            - price_diff_vnd: numeric difference (new - old)
            - price_diff_percent: percentage difference relative to old_price
    """
    # If no valid new price is provided (e.g. price on agreement)
    if new_price is None or new_price <= 0:
        return PriceChangeEvaluation(
            price_diff_vnd=0.0,
            price_diff_percent=0.0,
            price_status=PriceStatus.CONTACT,
        )

    # Initial price record
    if old_price is None or old_price <= 0:
        return PriceChangeEvaluation(
            price_diff_vnd=0.0,
            price_diff_percent=0.0,
            price_status=PriceStatus.INITIAL,
        )

    new_val = round(float(new_price))
    old_val = round(float(old_price))

    if new_val == old_val:
        return PriceChangeEvaluation(
            price_diff_vnd=0.0,
            price_diff_percent=0.0,
            price_status=PriceStatus.UNCHANGED,
        )

    diff = float(new_val - old_val)
    percent = round((diff / old_val) * 100.0, 2)

    if diff > 0:
        return PriceChangeEvaluation(
            price_diff_vnd=diff,
            price_diff_percent=percent,
            price_status=PriceStatus.INCREASED,
        )
    else:
        return PriceChangeEvaluation(
            price_diff_vnd=diff,
            price_diff_percent=percent,
            price_status=PriceStatus.DECREASED,
        )

