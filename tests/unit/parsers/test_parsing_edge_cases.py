"""Edge cases for JSON-path candidates and compound Vietnamese prices."""

from realestate.infrastructure.crawler.parsing.listing_parser import (
    extract_by_json_path,
    parse_vietnamese_price,
)


def test_extract_by_json_path_fallback_candidates():
    data = {
        "account_name": "John Doe",
        "nested": {"val": 123},
    }
    # First candidate is missing, second candidate is present
    val = extract_by_json_path(data, "seller_info.full_name, full_name, account_name")
    assert val == "John Doe"

    # First candidate is present
    val2 = extract_by_json_path(data, "nested.val, account_name")
    assert val2 == 123


def test_parse_vietnamese_price_compound_vs_area():
    # "3 tỷ 100 m2" with area=100 -> price should be 3 tỷ (3_000_000_000), not 3.1 tỷ
    total, pm2 = parse_vietnamese_price("3 tỷ 100 m2", area=100)
    assert total == 3_000_000_000.0
    assert pm2 == 30_000_000.0

    # Genuine compound price: "3 tỷ 500 triệu"
    total_cmp, pm2_cmp = parse_vietnamese_price("3 tỷ 500 triệu", area=100)
    assert total_cmp == 3_500_000_000.0
    assert pm2_cmp == 35_000_000.0


def test_infer_city_from_url_then_district():
    from realestate.infrastructure.crawler.parsing.listing_parser import infer_city

    assert infer_city("https://x.vn/ban-nha-ha-noi/1", None) == "Thành phố Hà Nội"
    assert infer_city("https://x.vn/1", "Quận Gò Vấp") == "Thành phố Hồ Chí Minh"
    assert infer_city("https://x.vn/1", "Huyện Đông Anh") == "Thành phố Hà Nội"
    assert infer_city("https://x.vn/1", "Huyện Cần Giuộc") is None
    assert infer_city("", None) is None


def test_url_template_used_when_item_has_no_url():
    from realestate.infrastructure.config.site_configs import load_site_config
    from realestate.infrastructure.crawler.parsing.listing_parser import ListingParser
    from tests.conftest import SITES_DIR

    config = load_site_config(SITES_DIR / "nhatot_hn.yaml")
    item = ListingParser(config).parse_item(
        {"list_id": 123456, "subject": "Bán nhà", "price": 1500000000}
    )
    assert item.details.url == "https://www.nhatot.com/123456.htm"
    assert item.listing_key == "nhatot_123456"  # portal key, shared with the nhatot feed


def test_datetime_relative_and_bad_clock_do_not_crash():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from realestate.infrastructure.crawler.parsing.datetime_parser import parse_vietnamese_datetime

    now = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)  # 10:00 in Vietnam
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    assert parse_vietnamese_datetime("5 phút trước", now=now) == datetime(
        2026, 11, 4, 2, 55, tzinfo=timezone.utc
    )
    assert parse_vietnamese_datetime("Hôm qua 14:30", now=now) == datetime(
        2026, 11, 3, 14, 30, tzinfo=vn
    )
    # used to raise ValueError and drop the whole listing
    assert parse_vietnamese_datetime("hôm nay 25:99", now=now) == now
