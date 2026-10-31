"""Parser for extracting structured real estate data using response_mapping and regex heuristics."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin
from src.domain.entities import (
    AdvertiserData,
    AdvertiserKind,
    Category,
    ListingDetailsData,
    ParsedListingItem,
    PropertyType,
)
from src.domain.entities.crawler_config import CrawlerConfig
from src.domain.services import generate_fingerprint, generate_listing_key, parse_vietnamese_address

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

LEGAL_STATUS_PATTERNS = [
    (re.compile(r"(?:sổ\s*đỏ\s*chính\s*chủ|sổ\s*hồng\s*chính\s*chủ)", re.IGNORECASE), "Sổ đỏ/hồng chính chủ"),
    (re.compile(r"(?:sổ\s*đỏ|sổ\s*hồng|sổ\s*riêng|sổ\s*vuông\s*đẹp)", re.IGNORECASE), "Sổ đỏ/hồng"),
    (re.compile(r"(?:hợp\s*đồng\s*mua\s*bán|hđmb)", re.IGNORECASE), "Hợp đồng mua bán"),
    (re.compile(r"(?:giấy\s*tờ\s*hợp\s*lệ|pháp\s*lý\s*chuẩn)", re.IGNORECASE), "Pháp lý hợp lệ"),
    (re.compile(r"(?:chờ\s*sổ|đang\s*chờ\s*cấp\s*sổ)", re.IGNORECASE), "Chờ cấp sổ"),
    (re.compile(r"(?:giấy\s*(?:tờ\s*)?tay|vi\s*bằng)", re.IGNORECASE), "Giấy viết tay/Vi bằng"),
]


def clean_html(raw_html: Optional[str]) -> Optional[str]:
    """Strip HTML tags, unescape entities, and normalize whitespace."""
    if not raw_html:
        return None
    text = html.unescape(raw_html)
    text = re.sub(r"<(?:p|div|br|li|h[1-6]|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned if cleaned else None


def parse_vietnamese_price(raw: Any, area: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
    """Parse real estate price from raw float, int or Vietnamese text (e.g. '5.2 tỷ', '850 triệu', '45 tr/m2').

    Returns:
        Tuple[Optional[float], Optional[float]]: (price_vnd, price_per_m2)
    """
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val <= 0:
            return None, None
        pm2 = round(val / area, 0) if area and area > 0 else None
        return val, pm2

    text = str(raw).strip().lower()
    if not text or any(k in text for k in ["thỏa thuận", "thoa thuan", "liên hệ", "lien he", "tùy", "đang cập nhật"]):
        return None, None
    if re.match(r"^0+(?:[\.,]0+)?\s*(?:đ|d|đồng|dong|vnd|tỷ|triệu|tr)?$", text):
        return None, None

    # 1. Price per m2: e.g. "45 triệu/m2" or "45 tr/m2" or "1.5 tỷ/m2"
    m_per_m2 = re.search(r"([0-9]+(?:[\.,][0-9]+)?)\s*(tỷ|ty|tỉ|ti|triệu|trieu|tr|nghìn|ngan|k)\s*/\s*m[2²]", text)
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
        return price_vnd, price_m2

    # Clean rental suffixes e.g. '/tháng', '/th', '/năm'
    text = re.sub(r"/\s*(?:tháng|thang|th|năm|nam)\b", "", text)

    # 2. Compound price:
    # 2a. Tỷ + Triệu (e.g. "1 tỷ 250 triệu", "2 tỷ 100", "2 tỷ 5", "1 tỷ 25 triệu")
    m_ty_tr = re.search(r"([0-9]+(?:[\.,][0-9]+)?)\s*(?:tỷ|ty|tỉ|ti)\s*([0-9]+(?:[\.,][0-9]+)?)\s*(triệu|trieu|tr)?\b", text)
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
        pm2 = round(total / area, 0) if area and area > 0 else None
        return total, pm2

    # 2b. Triệu + Nghìn (e.g. "2 triệu 500 nghìn", "2 triệu 50 nghìn", "2 triệu 5")
    m_tr_k = re.search(r"([0-9]+(?:[\.,][0-9]+)?)\s*(?:triệu|trieu|tr)\s*([0-9]+(?:[\.,][0-9]+)?)\s*(nghìn|ngan|k)?\b", text)
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
        pm2 = round(total / area, 0) if area and area > 0 else None
        return total, pm2

    # 4. Pure digits: "2500000000"
    clean_digits = re.sub(r"[^\d]", "", text)
    if clean_digits and len(clean_digits) >= 6:
        val = float(clean_digits)
        pm2 = round(val / area, 0) if area and area > 0 else None
        return val, pm2

    return None, None


def parse_vietnamese_area(raw: Any) -> Optional[float]:
    """Parse property area in m2 from float or text like '85.5 m2'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if float(raw) > 0 else None
    text = str(raw).strip().lower().replace(",", ".")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:m[2²]|mét\s*vuông)?", text)
    if m:
        try:
            val = float(m.group(1))
            return val if val > 0 else None
        except ValueError:
            pass
    return None


def parse_vietnamese_phone(raw: Any) -> Optional[str]:
    """Extract and normalize standard Vietnamese phone number (pure digits, starting with 0)."""
    if not raw:
        return None
    text = str(raw).strip()
    text = re.sub(r"^(?:tel:|phone:|callto:)\s*", "", text, flags=re.IGNORECASE)
    m = re.search(r"(?:/moi-gioi/|tel:|phone:|zalo\.me/)?(?:\+?84|0)?([35789]\d{8})", text)
    if m:
        return "0" + m.group(1)
    clean = re.sub(r"\D", "", text)
    if clean.startswith("84") and len(clean) in (11, 12):
        clean = "0" + clean[2:]
    elif not clean.startswith("0") and len(clean) == 9:
        clean = "0" + clean
    if len(clean) in (10, 11) and clean.startswith("0") and not clean.startswith("000"):
        return clean
    if len(clean) >= 9 and not clean.startswith("0000"):
        return clean
    return None


def extract_phone_from_text(text: Optional[str]) -> Optional[str]:
    """Scan arbitrary text (title, description) for Vietnamese mobile phone numbers."""
    if not text:
        return None
    # Normalize common obfuscations: replace 'o' or 'O' with '0' in phone-like tokens
    cleaned = re.sub(r"\b[oO](?=[35789])", "0", text)
    cleaned = re.sub(r"(\d)[oO](\d)", r"\g<1>0\g<2>", cleaned)
    cleaned = re.sub(r"(\d)[oO]\b", r"\g<1>0", cleaned)
    m = re.search(r"(?:\+?84|0)([35789](?:[.\s-]{0,2}\d){8})\b", cleaned)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 9:
            return "0" + digits
    return None


def parse_vietnamese_datetime(raw: Any) -> Optional[datetime]:
    """Parse timestamps from ISO strings, UNIX epoch (sec/ms), or Vietnamese relative/absolute text."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw

    # 1. Numeric UNIX timestamps
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    text = str(raw).strip()
    if text.isdigit() and len(text) in (10, 13):
        ts = float(text)
        if len(text) == 13:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    # 2. Vietnamese relative strings
    now = datetime.now(timezone.utc)
    text_lower = text.lower()

    m_sec = re.search(r"(\d+)\s*(?:giây|giay|s)\s*trước", text_lower)
    if m_sec:
        return now - timedelta(seconds=int(m_sec.group(1)))

    m_min = re.search(r"(\d+)\s*(?:phút|phut|p)\s*trước", text_lower)
    if m_min:
        return now - timedelta(minutes=int(m_min.group(1)))

    m_hour = re.search(r"(\d+)\s*(?:giờ|gio|h)\s*trước", text_lower)
    if m_hour:
        return now - timedelta(hours=int(m_hour.group(1)))

    m_day = re.search(r"(\d+)\s*(?:ngày|ngay)\s*trước", text_lower)
    if m_day:
        return now - timedelta(days=int(m_day.group(1)))

    if "hôm nay" in text_lower or "hom nay" in text_lower:
        m_time = re.search(r"(\d{1,2}):(\d{2})", text_lower)
        if m_time:
            h, mi = int(m_time.group(1)), int(m_time.group(2))
            return now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return now

    if "hôm qua" in text_lower or "hom qua" in text_lower:
        m_time = re.search(r"(\d{1,2}):(\d{2})", text_lower)
        yest = now - timedelta(days=1)
        if m_time:
            h, mi = int(m_time.group(1)), int(m_time.group(2))
            return yest.replace(hour=h, minute=mi, second=0, microsecond=0)
        return yest

    # 3. Standard DD/MM/YYYY or DD-MM-YYYY
    m_date = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if m_date:
        d, m, y = int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3))
        h = int(m_date.group(4)) if m_date.group(4) else 0
        mi = int(m_date.group(5)) if m_date.group(5) else 0
        s = int(m_date.group(6)) if m_date.group(6) else 0
        try:
            return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)
        except ValueError:
            pass

    # 4. ISO fallback
    try:
        clean_str = text.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except (ValueError, TypeError):
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
                l = float(m_mult.group(2).replace(",", "."))
                if 0 < w < 500 and 0 < l < 1000:
                    if facade is None:
                        facade = w
                    if depth is None:
                        depth = l
            except ValueError:
                pass

    return facade, depth


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


def extract_by_json_path(data: Any, path: str) -> Any:
    """Extract nested value by dotted path supporting array indexes like 'a.b[0].c'."""
    if not path or data is None:
        return None

    tokens = re.findall(r"([a-zA-Z0-9_-]+)|\[(\d+)\]", path)
    current = data

    for prop, idx in tokens:
        if current is None:
            return None
        if prop:
            if isinstance(current, dict):
                current = current.get(prop)
            else:
                return None
        elif idx:
            index = int(idx)
            if isinstance(current, list) and 0 <= index < len(current):
                current = current[index]
            else:
                return None

    return current


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


def extract_legal_status(description: Optional[str]) -> Optional[str]:
    """Extract legal status from description using keyword patterns."""
    if not description:
        return None
    for pattern, label in LEGAL_STATUS_PATTERNS:
        if pattern.search(description):
            return label
    return None


def infer_property_type(text: str) -> PropertyType:
    """Infer property type from listing title, breadcrumbs or URL slug if not explicitly mapped."""
    t = text.lower()
    if re.search(r"\b(căn hộ|can ho|chung cư|chung cu|ccmn|chcc|penthouse|duplex|officetel|tập thể|tap the|[1-5]\s*pn|phòng ngủ)\b", t) or "-can-ho-" in t or "-chung-cu-" in t or "/can-ho-" in t or "/chung-cu-" in t:
        return PropertyType.APARTMENT
    if re.search(r"\b(biệt thự|biet thu|villa|dinh thự|dinh thu)\b", t) or "-biet-thu-" in t or "-villa-" in t or "/biet-thu-" in t:
        return PropertyType.VILLA
    if re.search(r"\b(shophouse|nhà phố thương mại|nha pho thuong mai)\b", t) or "-shophouse-" in t or "/shophouse-" in t:
        return PropertyType.SHOPHOUSE
    if re.search(r"\b(kho|xưởng|nhà xưởng|văn phòng|van phong|mặt bằng|mat bang|khách sạn|hotel|resort|kios|ki-ốt|tmdv|thương mại dịch vụ|sàn chân đế)\b", t) or "-kho-" in t or "-xuong-" in t or "-van-phong-" in t or "-mat-bang-" in t:
        return PropertyType.COMMERCIAL
    if re.search(r"\b(đất|dat|đất nền|dat nen|thổ cư|tho cu|lô đất|lo dat|mảnh đất|thửa đất|lô góc|lo goc|vườn|sào|hecta|ha)\b", t) or "-dat-" in t or "-dat-nen-" in t or "/ban-dat" in t:
        return PropertyType.LAND
    if re.search(r"\b(nhà|nha|nhà riêng|nha rieng|nhà phố|nha pho|nhà mặt tiền|nha mat tien|nhà hẻm|nha hem|nhà ngõ|nha ngo|nhà cấp 4|liền kề|lien ke|hxh|hxt|bán nr|\bnr\b|\d+\s*tầng)\b", t) or "-nha-" in t or "/ban-nha" in t:
        return PropertyType.HOUSE
    return PropertyType.OTHER


def infer_category(text: str) -> Category:
    """Infer transaction category (sale/rent/project) from title or URL slug if not mapped."""
    t = text.lower()
    if re.search(r"\b(cho thuê|cho thue|thuê|thue)\b", t) or "/cho-thue-" in t or "/thue-" in t or "-cho-thue-" in t:
        return Category.RENT
    if re.search(r"\b(dự án|du an)\b", t) or "/du-an-" in t or "-du-an-" in t:
        return Category.PROJECT
    return Category.SALE


class ListingParser:
    """Parses raw API JSON records into standardized domain ParsedListingItem."""

    def __init__(self, config: CrawlerConfig) -> None:
        self.config = config
        self.mapping = config.response_mapping
        self.fields = self.mapping.fields
        self.val_maps = self.mapping.value_mappings

    def _map_enum_value(self, field_name: str, raw_value: Any, default_val: Any) -> Any:
        if raw_value is None:
            return default_val
        val_str = str(raw_value).strip()
        field_map = self.val_maps.get(field_name, {})
        mapped = field_map.get(val_str, field_map.get("_default", val_str))
        return mapped

    def _extract_val(self, raw_item: Dict[str, Any], field_name: str) -> Any:
        """Extract field value from raw_item using mapped path first, then fallback to direct key."""
        path = self.fields.get(field_name, "")
        if path:
            val = extract_by_json_path(raw_item, path)
            if val is not None:
                return val
        if field_name in raw_item:
            return raw_item[field_name]
        return None

    def parse_item(self, raw_item: Dict[str, Any]) -> ParsedListingItem:
        """Parse a single raw listing dictionary according to CrawlerConfig."""
        # 1. External IDs (avoid str(None) -> 'None')
        ext_id_val = self._extract_val(raw_item, "external_id")
        external_id = str(ext_id_val).strip() if ext_id_val is not None and str(ext_id_val).strip() else None

        ext_code_val = self._extract_val(raw_item, "external_code")
        external_code = safe_int(ext_code_val)

        # Clean external_id if it's a URL path (e.g. "...-id22756927" or "...-es2367045" or "...-3191063.html" or "...-p519188.html")
        if external_id and ("/" in external_id or "-id" in external_id or "-es" in external_id or "-p" in external_id or ".html" in external_id or ".htm" in external_id):
            m = re.search(r"-id(\d+)(?:[/?#]|$)|-es(\d+)(?:[/?#]|$)|-p(\d+)\.html|-(\d+)\.html|/(\d+)(?:\.htm)?(?:[/?#]|$)", external_id)
            if m:
                external_id = m.group(1) or m.group(2) or m.group(3) or m.group(4) or m.group(5)

        listing_key = generate_listing_key(
            self.config.site_id,
            external_code=external_code,
            external_id=external_id,
        )

        # 2. Dimensions & Price
        area_val = self._extract_val(raw_item, "area_m2")
        area_m2 = parse_vietnamese_area(area_val)

        price_val = self._extract_val(raw_item, "price_vnd")
        current_price_vnd, current_price_per_m2 = parse_vietnamese_price(price_val, area_m2)
        if current_price_vnd is not None:
            current_price_vnd = float(round(current_price_vnd))
        if current_price_per_m2 is not None:
            current_price_per_m2 = float(round(current_price_per_m2))
        elif current_price_vnd and area_m2 and area_m2 > 0:
            current_price_per_m2 = float(round(current_price_vnd / area_m2, 0))

        facade_val = self._extract_val(raw_item, "facade_m")
        parsed_facade = safe_float(facade_val)
        facade_m = parsed_facade if parsed_facade is not None and parsed_facade > 0 else None

        depth_val = self._extract_val(raw_item, "depth_m")
        parsed_depth = safe_float(depth_val)
        depth_m = parsed_depth if parsed_depth is not None and parsed_depth > 0 else None

        # 3. Category & Property Type Enums
        title = str(self._extract_val(raw_item, "title") or "").strip()
        raw_url = str(self._extract_val(raw_item, "url") or "").strip()
        breadcrumb = str(self._extract_val(raw_item, "breadcrumb") or "").strip()
        context_hint = f"{title} {breadcrumb} {raw_url}"

        raw_category = self._extract_val(raw_item, "category")
        if raw_category is not None:
            category_str = self._map_enum_value("category", raw_category, Category.SALE.value)
            try:
                category = Category(category_str)
            except ValueError:
                category = infer_category(context_hint)
        else:
            category = infer_category(context_hint)

        raw_prop_type = self._extract_val(raw_item, "property_type")
        if raw_prop_type is not None:
            prop_type_str = self._map_enum_value("property_type", raw_prop_type, PropertyType.OTHER.value)
            try:
                property_type = PropertyType(prop_type_str)
                if property_type == PropertyType.OTHER:
                    property_type = infer_property_type(context_hint)
            except ValueError:
                property_type = infer_property_type(context_hint)
        else:
            property_type = infer_property_type(context_hint)

        # 4. Descriptive details & regex extraction
        description = self._extract_val(raw_item, "description")
        description_str = clean_html(str(description)) if description is not None else None

        # Fallback dimension extraction from title and description if not provided in raw item
        if facade_m is None or depth_m is None:
            text_context = f"{title} {description_str or ''}"
            f_ext, d_ext = extract_dimensions(text_context)
            if facade_m is None:
                facade_m = f_ext
            if depth_m is None:
                depth_m = d_ext

        raw_legal = self._extract_val(raw_item, "legal_status")
        if raw_legal is not None and str(raw_legal).strip():
            legal_status = str(self._map_enum_value("legal_status", raw_legal, str(raw_legal).strip()))
        else:
            legal_status = extract_legal_status(description_str)

        # Build absolute URL using urljoin
        raw_url = str(self._extract_val(raw_item, "url") or "").strip()
        if raw_url:
            base_url = str(self.config.request.url)
            url = urljoin(base_url, raw_url)
        elif self.config.site_id == "nhatot" and external_id:
            url = f"https://www.nhatot.com/{external_id}.htm"
        else:
            url = ""

        # Coordinates
        lng = safe_float(self._extract_val(raw_item, "lng"))
        lat = safe_float(self._extract_val(raw_item, "lat"))

        # Published date parsing
        pub_raw = self._extract_val(raw_item, "published_at")
        published_at = parse_vietnamese_datetime(pub_raw)

        # Media list: extract string URLs only
        raw_media = self._extract_val(raw_item, "media_list")
        media_list: Optional[List[str]] = None
        if isinstance(raw_media, list):
            urls: List[str] = []
            for m in raw_media:
                if isinstance(m, str) and m.strip():
                    urls.append(m.strip())
                elif isinstance(m, dict):
                    m_url = m.get("url") or m.get("uri") or m.get("src")
                    if m_url and isinstance(m_url, str) and m_url.strip():
                        urls.append(m_url.strip())
            media_list = urls if urls else None

        # 4.5. Structured Address Parsing (city_name, district_name, ward_name, street_name)
        addr_val = self._extract_val(raw_item, "address")
        city_raw = self._extract_val(raw_item, "city_name")
        district_raw = self._extract_val(raw_item, "district_name")
        ward_raw = self._extract_val(raw_item, "ward_name")

        addr_parts = parse_vietnamese_address(
            raw_address=str(addr_val).strip() if addr_val else None,
            city_hint=str(city_raw).strip() if city_raw else None,
            district_hint=str(district_raw).strip() if district_raw else None,
            ward_hint=str(ward_raw).strip() if ward_raw else None,
        )

        if not addr_parts.get("city_name"):
            url_lower = url.lower()
            if any(k in url_lower for k in ["ho-chi-minh", "tphcm", "tp-hcm"]):
                addr_parts["city_name"] = "Thành phố Hồ Chí Minh"
            elif any(k in url_lower for k in ["ha-noi", "hanoi"]):
                addr_parts["city_name"] = "Thành phố Hà Nội"
            elif any(k in url_lower for k in ["da-nang", "danang"]):
                addr_parts["city_name"] = "Thành phố Đà Nẵng"
            elif any(k in url_lower for k in ["binh-duong", "binhduong"]):
                addr_parts["city_name"] = "Tỉnh Bình Dương"
            elif any(k in url_lower for k in ["dong-nai", "dongnai"]):
                addr_parts["city_name"] = "Tỉnh Đồng Nai"
            elif addr_parts.get("district_name"):
                dist_lower = addr_parts["district_name"].lower()
                hcm_districts = ["quận 1", "quận 2", "quận 3", "quận 4", "quận 5", "quận 6", "quận 7", "quận 8", "quận 9", "quận 10", "quận 11", "quận 12", "bình thạnh", "gò vấp", "tân bình", "tân phú", "phú nhuận", "bình tân", "thủ đức", "hóc môn", "củ chi", "bình chánh", "nhà bè", "cần giờ"]
                hanoi_districts = ["ba đình", "hoàn kiếm", "tây hồ", "long biên", "cầu giấy", "đống đa", "hai bà trưng", "hoàng mai", "thanh xuân", "nam từ liêm", "bắc từ liêm", "hà đông", "sơn tây", "ba vì", "phúc thọ", "đan phượng", "hoài đức", "quốc oai", "thạch thất", "chương mỹ", "thanh oai", "thường tín", "phú xuyên", "ứng hòa", "mê linh", "đông anh", "gia lâm", "sóc sơn", "thanh trì"]
                if any(d in dist_lower for d in hcm_districts):
                    addr_parts["city_name"] = "Thành phố Hồ Chí Minh"
                elif any(d in dist_lower for d in hanoi_districts):
                    addr_parts["city_name"] = "Thành phố Hà Nội"

        details = ListingDetailsData(
            url=url,
            title=title,
            description=description_str,
            city_name=addr_parts["city_name"],
            district_name=addr_parts["district_name"],
            ward_name=addr_parts["ward_name"],
            lng=lng,
            lat=lat,
            legal_status=legal_status[:50] if legal_status else None,
            thumbnail_url=self._extract_val(raw_item, "thumbnail_url"),
            media_list=media_list,
        )

        # 5. Advertiser
        adv_id = self._extract_val(raw_item, "advertiser_id")
        adv_phone = self._extract_val(raw_item, "advertiser_phone")
        adv_name = self._extract_val(raw_item, "advertiser_name")
        if adv_name and str(adv_name).strip():
            raw_name = str(adv_name).strip()
            words = raw_name.split()
            if len(words) >= 2 and len(words) % 2 == 0:
                mid = len(words) // 2
                if words[:mid] == words[mid:]:
                    raw_name = " ".join(words[:mid])
            adv_name = raw_name
        adv_email = self._extract_val(raw_item, "advertiser_email")
        raw_adv_kind = self._extract_val(raw_item, "advertiser_kind")
        adv_kind_str = self._map_enum_value("advertiser_kind", raw_adv_kind, AdvertiserKind.UNKNOWN.value)
        try:
            adv_kind = AdvertiserKind(adv_kind_str)
        except ValueError:
            adv_kind = AdvertiserKind.UNKNOWN

        adv_ver_raw = self._extract_val(raw_item, "advertiser_is_verified")
        if adv_ver_raw is not None:
            if isinstance(adv_ver_raw, bool):
                is_verified = adv_ver_raw
            elif isinstance(adv_ver_raw, (int, float)):
                is_verified = bool(adv_ver_raw)
            else:
                is_verified = str(adv_ver_raw).strip().lower() in ("true", "1", "yes")
        else:
            is_verified = False

        # Prioritize real contact phone to prevent clobbering brokers into aggregator crawl accounts
        parsed_phone = parse_vietnamese_phone(adv_phone)
        if not parsed_phone:
            # Fallback: scan title and description for phone number
            text_context = f"{title} {description_str or ''}"
            parsed_phone = extract_phone_from_text(text_context)

        # Fallback: scan description for email if adv_email not provided
        if not adv_email and description_str:
            m_email = re.search(r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b", description_str)
            if m_email:
                adv_email = m_email.group(1).lower()

        if parsed_phone:
            advertiser_id = f"adv_{self.config.site_id}_{parsed_phone[:20]}"
            display_phone = parsed_phone[:20]
        elif adv_id:
            advertiser_id = str(adv_id).strip()[:64]
            display_phone = None
        elif adv_name and str(adv_name).strip():
            adv_clean = str(adv_name).strip()
            name_hash = hashlib.md5(adv_clean.lower().encode("utf-8")).hexdigest()[:12]
            advertiser_id = f"adv_{self.config.site_id}_{name_hash}"
            display_phone = None
        else:
            advertiser_id = None
            display_phone = None

        advertiser = AdvertiserData(
            advertiser_id=advertiser_id,
            name=str(adv_name).strip()[:100] if adv_name else None,
            phone=display_phone,
            email=str(adv_email).strip()[:100] if adv_email else None,
            kind=adv_kind,
            is_verified=is_verified,
        )

        # 6. Fingerprint for cross-listing deduplication
        fingerprint = generate_fingerprint(
            title=title,
            area_m2=area_m2,
            city_name=details.city_name,
            district_name=details.district_name,
            ward_name=details.ward_name,
            advertiser_phone=advertiser.phone,
        )

        return ParsedListingItem(
            listing_key=listing_key,
            source=self.config.site_id,
            external_id=external_id,
            external_code=external_code,
            property_fingerprint=fingerprint,
            category=category,
            property_type=property_type,
            area_m2=area_m2,
            facade_m=facade_m,
            depth_m=depth_m,
            current_price_vnd=current_price_vnd,
            current_price_per_m2=current_price_per_m2,
            published_at=published_at,
            details=details,
            advertiser=advertiser,
        )
