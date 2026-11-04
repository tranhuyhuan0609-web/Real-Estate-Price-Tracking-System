"""Unit tests for Vietnamese real estate price parsing."""

from realestate.infrastructure.crawler.parsing.price import (
    parse_vietnamese_price,
)


def test_parse_vietnamese_price_ty():
    price, _ = parse_vietnamese_price("4.2 tỷ")
    assert price == 4_200_000_000.0


def test_parse_vietnamese_price_trieu():
    price, _ = parse_vietnamese_price("850 triệu")
    assert price == 850_000_000.0


def test_parse_vietnamese_price_compound():
    # 3 tỷ 500 triệu -> 3,500,000,000
    price, _ = parse_vietnamese_price("3 tỷ 500 triệu")
    assert price == 3_500_000_000.0


def test_parse_vietnamese_price_compound_with_area_ignored():
    # "3 tỷ 100 m2" -> 3,000,000,000 (100 is followed by m2, NOT millions)
    price, _ = parse_vietnamese_price("3 tỷ 100 m2")
    assert price == 3_000_000_000.0


def test_parse_vietnamese_price_per_m2():
    price, ppm = parse_vietnamese_price("45 triệu/m²", area=100.0)
    assert price == 4_500_000_000.0
    assert ppm == 45_000_000.0


def test_parse_vietnamese_price_contact():
    price, ppm = parse_vietnamese_price("Thỏa thuận")
    assert price is None
    assert ppm is None

    price, ppm = parse_vietnamese_price("Liên hệ")
    assert price is None
    assert ppm is None


def test_parse_vietnamese_price_sanity_bound():
    # Extreme abnormal price (> 5000 tỷ) should be filtered out
    price, ppm = parse_vietnamese_price("15666000 tỷ", area=348.0)
    assert price is None
    assert ppm is None
