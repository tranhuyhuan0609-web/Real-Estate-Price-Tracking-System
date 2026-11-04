"""Parser for extracting structured real estate data using response_mapping and regex heuristics."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from realestate.domain.entities import (
    AdvertiserData,
    AdvertiserKind,
    Category,
    ListingDetailsData,
    ParsedListingItem,
    PropertyType,
)
from realestate.domain.entities.crawler_config import CrawlerConfig
from realestate.domain.services import (
    generate_fingerprint,
    generate_listing_key,
    parse_vietnamese_address,
)
from realestate.infrastructure.crawler.parsing.area import (
    extract_dimensions,  # noqa: F401  (re-exported)
    parse_vietnamese_area,  # noqa: F401  (re-exported)
)

# Helpers live in sibling modules; re-exported here for existing importers.
from realestate.infrastructure.crawler.parsing.datetime_parser import (  # noqa: F401
    parse_vietnamese_datetime,
)
from realestate.infrastructure.crawler.parsing.html_cleaner import (
    PLATFORM_SUPPORT_DOMAINS,  # noqa: F401  (re-exported)
    PLATFORM_SUPPORT_PREFIXES,  # noqa: F401  (re-exported)
    clean_email,  # noqa: F401  (re-exported)
    clean_html,  # noqa: F401  (re-exported)
    decode_cloudflare_email,  # noqa: F401  (re-exported)
    extract_email_from_text,  # noqa: F401  (re-exported)
)
from realestate.infrastructure.crawler.parsing.json_path import (  # noqa: F401
    extract_by_json_path,
)
from realestate.infrastructure.crawler.parsing.legal_status import (
    LEGAL_STATUS_PATTERNS,  # noqa: F401  (re-exported)
    extract_legal_status,  # noqa: F401  (re-exported)
)
from realestate.infrastructure.crawler.parsing.numbers import (
    normalize_vietnamese_number_str,  # noqa: F401  (re-exported)
    safe_float,  # noqa: F401  (re-exported)
    safe_int,  # noqa: F401  (re-exported)
)
from realestate.infrastructure.crawler.parsing.phone import (
    extract_phone_from_text,  # noqa: F401  (re-exported)
    parse_vietnamese_phone,  # noqa: F401  (re-exported)
)
from realestate.infrastructure.crawler.parsing.price import (
    MAX_VALID_PRICE_VND,  # noqa: F401  (re-exported)
    parse_vietnamese_price,  # noqa: F401  (re-exported)
)
from realestate.infrastructure.crawler.parsing.road_width import (
    ROAD_WIDTH_PATTERNS,  # noqa: F401  (re-exported)
    extract_road_width,  # noqa: F401  (re-exported)
)


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

        # Override category if title explicitly specifies rental (e.g. "Cho thuê nhà phố...")
        title_lower = title.lower()
        if category == Category.SALE and re.search(r"\b(cho thuê|cho thue)\b", title_lower) and not re.search(r"\b(bán|ban)\b", title_lower):
            category = Category.RENT

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

        legal_status: Optional[str]
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

        # Coordinates with Vietnam bounding box sanity check (lat: 8..24, lng: 102..110.5)
        lng = safe_float(self._extract_val(raw_item, "lng"))
        lat = safe_float(self._extract_val(raw_item, "lat"))
        if lat is not None and lng is not None:
            if not (8.0 <= lat <= 24.0 and 102.0 <= lng <= 110.5):
                lat, lng = None, None

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
                dist_lower = (addr_parts["district_name"] or "").lower()
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
        adv_email_raw = self._extract_val(raw_item, "advertiser_email")
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

        # Multi-tier email extraction & unmasking
        parsed_email = clean_email(adv_email_raw)
        if not parsed_email:
            # Fallback 1: scan description and title for unmasked email
            text_context = f"{title} {description_str or ''}"
            parsed_email = extract_email_from_text(text_context)
        if not parsed_email:
            # Fallback 2: scan raw_item values for any email, avatar, or Cloudflare encoded string
            for k, v in raw_item.items():
                if isinstance(v, str) and ("@" in v or "cfemail" in k.lower() or len(v) >= 16):
                    cand = clean_email(v)
                    if cand:
                        parsed_email = cand
                        break

        display_phone = parsed_phone[:20] if parsed_phone else None

        if parsed_phone:
            advertiser_id = f"adv_{self.config.site_id}_{parsed_phone[:20]}"
        elif adv_id:
            advertiser_id = str(adv_id).strip()[:64]
        elif adv_name and str(adv_name).strip():
            adv_clean = str(adv_name).strip()
            name_hash = hashlib.md5(adv_clean.lower().encode("utf-8")).hexdigest()[:12]
            advertiser_id = f"adv_{self.config.site_id}_{name_hash}"
        else:
            advertiser_id = None

        advertiser = AdvertiserData(
            advertiser_id=advertiser_id,
            name=str(adv_name).strip()[:100] if adv_name else None,
            phone=display_phone,
            email=parsed_email[:100] if parsed_email else None,
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
