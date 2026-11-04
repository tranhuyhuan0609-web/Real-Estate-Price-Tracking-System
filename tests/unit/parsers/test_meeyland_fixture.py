"""Golden test: a captured (anonymised) Meeyland API item parsed with the production YAML config."""

from datetime import datetime, timezone

from realestate.domain.entities import Category, PropertyType
from realestate.infrastructure.config.site_configs import load_site_config
from realestate.infrastructure.crawler.parsing.listing_parser import ListingParser
from tests.conftest import SITES_DIR, load_fixture


def test_parse_captured_meeyland_item():
    parser = ListingParser(load_site_config(SITES_DIR / "meeyland.yaml"))
    item = parser.parse_item(load_fixture("meeyland_item.json"))

    assert item.listing_key == "meeyland_307624752"
    assert item.external_code == 307624752
    assert item.category is Category.SALE
    assert item.property_type is PropertyType.LAND
    assert (item.area_m2, item.facade_m, item.depth_m) == (80.0, 5.0, 16.0)
    assert item.current_price_vnd == 1_850_000_000
    assert item.current_price_per_m2 == 23_125_000
    assert item.published_at == datetime(2026, 10, 12, 9, 5, 28, 363000, tzinfo=timezone.utc)

    d = item.details
    assert d.url == "https://meeyland.com/ban-dat-can-giuoc-tay-ninh-p1122/307624752"
    assert (d.city_name, d.district_name, d.ward_name) == (
        "Tỉnh Tây Ninh",
        "Cần Giuộc",
        "Xã Cần Giuộc",
    )
    assert d.legal_status == "Sổ đỏ/hồng"
    assert 8 < d.lat < 24 and 102 < d.lng < 110
    assert item.advertiser is not None and item.advertiser.phone == "0900000001"
