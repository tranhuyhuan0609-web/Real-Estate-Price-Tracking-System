"""Parser for extracting structured real estate data using response_mapping and regex heuristics."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple
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
from realestate.domain.location.administrative_data import (
    KNOWN_HANOI_DISTRICT_NAMES,
    KNOWN_HCM_DISTRICT_NAMES,
)
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
    if (
        re.search(
            r"\b(căn hộ|can ho|chung cư|chung cu|ccmn|chcc|penthouse|duplex|officetel|tập thể|tap the|[1-5]\s*pn|phòng ngủ)\b",
            t,
        )
        or "-can-ho-" in t
        or "-chung-cu-" in t
        or "/can-ho-" in t
        or "/chung-cu-" in t
    ):
        return PropertyType.APARTMENT
    if (
        re.search(r"\b(biệt thự|biet thu|villa|dinh thự|dinh thu)\b", t)
        or "-biet-thu-" in t
        or "-villa-" in t
        or "/biet-thu-" in t
    ):
        return PropertyType.VILLA
    if (
        re.search(r"\b(shophouse|nhà phố thương mại|nha pho thuong mai)\b", t)
        or "-shophouse-" in t
        or "/shophouse-" in t
    ):
        return PropertyType.SHOPHOUSE
    if (
        re.search(
            r"\b(kho|xưởng|nhà xưởng|văn phòng|van phong|mặt bằng|mat bang|khách sạn|hotel|resort|kios|ki-ốt|tmdv|thương mại dịch vụ|sàn chân đế)\b",
            t,
        )
        or "-kho-" in t
        or "-xuong-" in t
        or "-van-phong-" in t
        or "-mat-bang-" in t
    ):
        return PropertyType.COMMERCIAL
    if (
        re.search(
            r"\b(đất|dat|đất nền|dat nen|thổ cư|tho cu|lô đất|lo dat|mảnh đất|thửa đất|lô góc|lo goc|vườn|sào|hecta|ha)\b",
            t,
        )
        or "-dat-" in t
        or "-dat-nen-" in t
        or "/ban-dat" in t
    ):
        return PropertyType.LAND
    if (
        re.search(
            r"\b(nhà|nha|nhà riêng|nha rieng|nhà phố|nha pho|nhà mặt tiền|nha mat tien|nhà hẻm|nha hem|nhà ngõ|nha ngo|nhà cấp 4|liền kề|lien ke|hxh|hxt|bán nr|\bnr\b|\d+\s*tầng)\b",
            t,
        )
        or "-nha-" in t
        or "/ban-nha" in t
    ):
        return PropertyType.HOUSE
    return PropertyType.OTHER


def infer_category(text: str) -> Category:
    """Infer transaction category (sale/rent/project) from title or URL slug if not mapped."""
    t = text.lower()
    if (
        re.search(r"\b(cho thuê|cho thue|thuê|thue)\b", t)
        or "/cho-thue-" in t
        or "/thue-" in t
        or "-cho-thue-" in t
    ):
        return Category.RENT
    if re.search(r"\b(dự án|du an)\b", t) or "/du-an-" in t or "-du-an-" in t:
        return Category.PROJECT
    return Category.SALE


_CITY_URL_HINTS = (
    (("ho-chi-minh", "tphcm", "tp-hcm"), "Thành phố Hồ Chí Minh"),
    (("ha-noi", "hanoi"), "Thành phố Hà Nội"),
    (("da-nang", "danang"), "Thành phố Đà Nẵng"),
    (("binh-duong", "binhduong"), "Tỉnh Bình Dương"),
    (("dong-nai", "dongnai"), "Tỉnh Đồng Nai"),
)


def infer_city(url: str, district_name: Optional[str]) -> Optional[str]:
    """Last-resort city guess from URL slugs, then from well-known HCM / Hà Nội district names."""
    url_lower = (url or "").lower()
    for hints, city in _CITY_URL_HINTS:
        if any(h in url_lower for h in hints):
            return city
    dist_lower = (district_name or "").lower()
    if dist_lower:
        if any(d in dist_lower for d in KNOWN_HCM_DISTRICT_NAMES):
            return "Thành phố Hồ Chí Minh"
        if any(d in dist_lower for d in KNOWN_HANOI_DISTRICT_NAMES):
            return "Thành phố Hà Nội"
    return None


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
        external_id, external_code = self._external_ids(raw_item)
        listing_key = generate_listing_key(
            self.config.portal_id, external_code=external_code, external_id=external_id
        )

        title = self._text(raw_item, "title")
        description = self._extract_val(raw_item, "description")
        description_str = clean_html(str(description)) if description is not None else None
        text_context = f"{title} {description_str or ''}"

        area_m2 = parse_vietnamese_area(self._extract_val(raw_item, "area_m2"))
        price_vnd, price_per_m2 = self._price(raw_item, area_m2)
        facade_m, depth_m = self._dimensions(raw_item, text_context)
        category, property_type = self._classify(raw_item, title)

        url = self._url(raw_item, external_id, external_code)
        lat, lng = self._coordinates(raw_item)
        address = self._address(raw_item, url)
        details = ListingDetailsData(
            url=url,
            title=title,
            description=description_str,
            city_name=address["city_name"],
            district_name=address["district_name"],
            ward_name=address["ward_name"],
            lng=lng,
            lat=lat,
            legal_status=self._legal_status(raw_item, description_str),
            thumbnail_url=self._extract_val(raw_item, "thumbnail_url"),
            media_list=media_urls(self._extract_val(raw_item, "media_list")),
        )

        return ParsedListingItem(
            listing_key=listing_key,
            source=self.config.portal_id,
            external_id=external_id,
            external_code=external_code,
            property_fingerprint=generate_fingerprint(
                title=title,
                area_m2=area_m2,
                city_name=details.city_name,
                district_name=details.district_name,
                ward_name=details.ward_name,
            ),
            category=category,
            property_type=property_type,
            area_m2=area_m2,
            facade_m=facade_m,
            depth_m=depth_m,
            current_price_vnd=price_vnd,
            current_price_per_m2=price_per_m2,
            published_at=parse_vietnamese_datetime(
                self._extract_val(raw_item, "published_at"),
                default_tz=self.config.content_zoneinfo(),
            ),
            details=details,
            advertiser=self._advertiser(raw_item, text_context),
        )

    # ------------------------------------------------------------------ steps
    def _text(self, raw_item: Dict[str, Any], field: str) -> str:
        return str(self._extract_val(raw_item, field) or "").strip()

    def _external_ids(self, raw_item: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
        raw_id = self._extract_val(raw_item, "external_id")
        external_id = str(raw_id).strip() if raw_id is not None and str(raw_id).strip() else None
        return clean_external_id(external_id), safe_int(
            self._extract_val(raw_item, "external_code")
        )

    def _price(
        self, raw_item: Dict[str, Any], area_m2: Optional[float]
    ) -> Tuple[Optional[float], Optional[float]]:
        price_vnd, per_m2 = parse_vietnamese_price(
            self._extract_val(raw_item, "price_vnd"), area_m2
        )
        if price_vnd is not None:
            price_vnd = float(round(price_vnd))
        if per_m2 is not None:
            per_m2 = float(round(per_m2))
        elif price_vnd and area_m2 and area_m2 > 0:
            per_m2 = float(round(price_vnd / area_m2, 0))
        return price_vnd, per_m2

    def _dimensions(
        self, raw_item: Dict[str, Any], text_context: str
    ) -> Tuple[Optional[float], Optional[float]]:
        facade = positive(safe_float(self._extract_val(raw_item, "facade_m")))
        depth = positive(safe_float(self._extract_val(raw_item, "depth_m")))
        if facade is None or depth is None:
            f_ext, d_ext = extract_dimensions(text_context)
            facade = facade if facade is not None else f_ext
            depth = depth if depth is not None else d_ext
        return facade, depth

    def _classify(self, raw_item: Dict[str, Any], title: str) -> Tuple[Category, PropertyType]:
        hint = f"{title} {self._text(raw_item, 'breadcrumb')} {self._text(raw_item, 'url')}"

        category: Optional[Category] = None
        raw_category = self._extract_val(raw_item, "category")
        if raw_category is not None:
            try:
                category = Category(
                    self._map_enum_value("category", raw_category, Category.SALE.value)
                )
            except ValueError:
                category = None
        if category is None:
            category = infer_category(hint)
        # A title that says "cho thuê" (and not "bán") is a rental whatever the source category says.
        title_lower = title.lower()
        if (
            category == Category.SALE
            and re.search(r"\b(cho thuê|cho thue)\b", title_lower)
            and not re.search(r"\b(bán|ban)\b", title_lower)
        ):
            category = Category.RENT

        property_type = PropertyType.OTHER
        raw_type = self._extract_val(raw_item, "property_type")
        if raw_type is not None:
            try:
                property_type = PropertyType(
                    self._map_enum_value("property_type", raw_type, PropertyType.OTHER.value)
                )
            except ValueError:
                property_type = PropertyType.OTHER
        if property_type == PropertyType.OTHER:
            property_type = infer_property_type(hint)
        return category, property_type

    def _legal_status(self, raw_item: Dict[str, Any], description: Optional[str]) -> Optional[str]:
        raw = self._extract_val(raw_item, "legal_status")
        if raw is not None and str(raw).strip():
            status: Optional[str] = str(self._map_enum_value("legal_status", raw, str(raw).strip()))
        else:
            status = extract_legal_status(description)
        return status[:50] if status else None

    def _url(
        self, raw_item: Dict[str, Any], external_id: Optional[str], external_code: Optional[int]
    ) -> str:
        raw_url = self._text(raw_item, "url")
        if raw_url:
            return urljoin(str(self.config.request.url), raw_url)
        if self.mapping.url_template and (external_id or external_code):
            return self.mapping.url_template.format(
                external_id=external_id or "", external_code=external_code or ""
            )
        return ""

    def _coordinates(self, raw_item: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
        lat = safe_float(self._extract_val(raw_item, "lat"))
        lng = safe_float(self._extract_val(raw_item, "lng"))
        if (
            lat is not None
            and lng is not None
            and not (8.0 <= lat <= 24.0 and 102.0 <= lng <= 110.5)
        ):
            return None, None  # outside Vietnam's bounding box
        return lat, lng

    def _address(self, raw_item: Dict[str, Any], url: str) -> Dict[str, Optional[str]]:
        def hint(field: str) -> Optional[str]:
            value = self._extract_val(raw_item, field)
            return str(value).strip() if value else None

        parts = parse_vietnamese_address(
            raw_address=hint("address"),
            city_hint=hint("city_name"),
            district_hint=hint("district_name"),
            ward_hint=hint("ward_name"),
        )
        if not parts.get("city_name"):
            parts["city_name"] = infer_city(url, parts.get("district_name"))
        return parts

    def _advertiser(self, raw_item: Dict[str, Any], text_context: str) -> AdvertiserData:
        name = dedupe_repeated_name(self._extract_val(raw_item, "advertiser_name"))

        try:
            kind = AdvertiserKind(
                self._map_enum_value(
                    "advertiser_kind",
                    self._extract_val(raw_item, "advertiser_kind"),
                    AdvertiserKind.UNKNOWN.value,
                )
            )
        except ValueError:
            kind = AdvertiserKind.UNKNOWN

        # The contact phone wins over aggregator account phones; fall back to the text.
        phone = parse_vietnamese_phone(
            self._extract_val(raw_item, "advertiser_phone")
        ) or extract_phone_from_text(text_context)
        email = (
            clean_email(self._extract_val(raw_item, "advertiser_email"))
            or extract_email_from_text(text_context)
            or first_email_in_values(raw_item)
        )

        raw_id = self._extract_val(raw_item, "advertiser_id")
        advertiser_id: Optional[str]
        if phone:
            advertiser_id = f"adv_{self.config.portal_id}_{phone[:20]}"
        elif raw_id:
            advertiser_id = str(raw_id).strip()[:64]
        elif name:
            name_hash = hashlib.md5(name.lower().encode("utf-8")).hexdigest()[:12]
            advertiser_id = f"adv_{self.config.portal_id}_{name_hash}"
        else:
            advertiser_id = None

        return AdvertiserData(
            advertiser_id=advertiser_id,
            name=name[:100] if name else None,
            phone=phone[:20] if phone else None,
            email=email[:100] if email else None,
            kind=kind,
            is_verified=parse_bool(self._extract_val(raw_item, "advertiser_is_verified")),
        )


# ---------------------------------------------------------------------- helpers
_EXTERNAL_ID_IN_URL = re.compile(
    r"-id(\d+)(?:[/?#]|$)|-es(\d+)(?:[/?#]|$)|-p(\d+)\.html|-(\d+)\.html|/(\d+)(?:\.htm)?(?:[/?#]|$)"
)


def clean_external_id(external_id: Optional[str]) -> Optional[str]:
    """Pull the numeric id out of a listing URL ("...-id22756927", "...-es123", "...-3191063.html")."""
    if not external_id or not any(
        token in external_id for token in ("/", "-id", "-es", "-p", ".html", ".htm")
    ):
        return external_id
    m = _EXTERNAL_ID_IN_URL.search(external_id)
    if not m:
        return external_id
    return next(g for g in m.groups() if g)


def positive(value: Optional[float]) -> Optional[float]:
    return value if value is not None and value > 0 else None


def dedupe_repeated_name(raw: Any) -> Optional[str]:
    """A name scraped twice from one node ("Nguyễn An Nguyễn An") is collapsed."""
    if not raw or not str(raw).strip():
        return None
    name = str(raw).strip()
    words = name.split()
    half = len(words) // 2
    if len(words) >= 2 and len(words) % 2 == 0 and words[:half] == words[half:]:
        return " ".join(words[:half])
    return name


def parse_bool(raw: Any) -> bool:
    if raw is None:
        return False
    if isinstance(raw, (bool, int, float)):
        return bool(raw)
    return str(raw).strip().lower() in ("true", "1", "yes")


def media_urls(raw: Any) -> Optional[List[str]]:
    """Image URLs from a list of strings or of {url|uri|src} objects."""
    if not isinstance(raw, list):
        return None
    urls: List[str] = []
    for m in raw:
        if isinstance(m, dict):
            m = m.get("url") or m.get("uri") or m.get("src")
        if isinstance(m, str) and m.strip():
            urls.append(m.strip())
    return urls or None


def first_email_in_values(raw_item: Dict[str, Any]) -> Optional[str]:
    """Last resort: any value that cleans to an e-mail (avatar URL, Cloudflare hex, ...)."""
    for key, value in raw_item.items():
        if isinstance(value, str) and (
            "@" in value or "cfemail" in key.lower() or len(value) >= 16
        ):
            email = clean_email(value)
            if email:
                return email
    return None
