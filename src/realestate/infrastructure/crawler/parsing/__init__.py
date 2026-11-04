"""Vietnamese listing text parsers. ``ListingParser`` turns a raw API/HTML item into a domain item."""

from realestate.infrastructure.crawler.parsing.area import extract_dimensions, parse_vietnamese_area
from realestate.infrastructure.crawler.parsing.datetime_parser import parse_vietnamese_datetime
from realestate.infrastructure.crawler.parsing.html_cleaner import (
    clean_email,
    clean_html,
    decode_cloudflare_email,
    extract_email_from_text,
)
from realestate.infrastructure.crawler.parsing.json_path import extract_by_json_path
from realestate.infrastructure.crawler.parsing.legal_status import extract_legal_status
from realestate.infrastructure.crawler.parsing.listing_parser import ListingParser
from realestate.infrastructure.crawler.parsing.numbers import (
    normalize_vietnamese_number_str,
    safe_float,
    safe_int,
)
from realestate.infrastructure.crawler.parsing.phone import (
    extract_phone_from_text,
    parse_vietnamese_phone,
)
from realestate.infrastructure.crawler.parsing.price import (
    MAX_VALID_PRICE_VND,
    parse_vietnamese_price,
)
from realestate.infrastructure.crawler.parsing.road_width import extract_road_width

__all__ = [
    "MAX_VALID_PRICE_VND",
    "ListingParser",
    "clean_email",
    "clean_html",
    "decode_cloudflare_email",
    "extract_by_json_path",
    "extract_dimensions",
    "extract_email_from_text",
    "extract_legal_status",
    "extract_phone_from_text",
    "extract_road_width",
    "normalize_vietnamese_number_str",
    "parse_vietnamese_area",
    "parse_vietnamese_datetime",
    "parse_vietnamese_phone",
    "parse_vietnamese_price",
    "safe_float",
    "safe_int",
]
