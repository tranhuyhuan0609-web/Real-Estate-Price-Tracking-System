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
