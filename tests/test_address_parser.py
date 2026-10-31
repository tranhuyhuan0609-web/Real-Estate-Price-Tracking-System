"""Unit tests for Vietnamese address parsing and canonical location normalization."""

import pytest
from src.domain.services import canonicalize_vietnamese_location, parse_vietnamese_address


def test_canonicalize_vietnamese_location():
    # City variants
    assert canonicalize_vietnamese_location("TP.HCM")[0] == "Thành phố Hồ Chí Minh"
    assert canonicalize_vietnamese_location("TP. HCM")[0] == "Thành phố Hồ Chí Minh"
    assert canonicalize_vietnamese_location("TP Hồ Chí Minh")[0] == "Thành phố Hồ Chí Minh"
    assert canonicalize_vietnamese_location("Sài Gòn")[0] == "Thành phố Hồ Chí Minh"
    assert canonicalize_vietnamese_location("Hà Nội")[0] == "Thành phố Hà Nội"
    assert canonicalize_vietnamese_location("TP. Hà Nội")[0] == "Thành phố Hà Nội"
    assert canonicalize_vietnamese_location("Đà Nẵng")[0] == "Thành phố Đà Nẵng"

    # District variants
    assert canonicalize_vietnamese_location(district="Q. 1")[1] == "Quận 1"
    assert canonicalize_vietnamese_location(district="Q1")[1] == "Quận 1"
    assert canonicalize_vietnamese_location(district="Quận 01")[1] == "Quận 1"
    assert canonicalize_vietnamese_location(district="Q. Bình Thạnh")[1] == "Quận Bình Thạnh"
    assert canonicalize_vietnamese_location(district="Bình Thạnh")[1] == "Quận Bình Thạnh"
    assert canonicalize_vietnamese_location(district="H. Củ Chi")[1] == "Huyện Củ Chi"
    assert canonicalize_vietnamese_location(district="Thủ Đức")[1] == "Thành phố Thủ Đức"

    # Ward variants
    assert canonicalize_vietnamese_location(ward="P. 7")[2] == "Phường 7"
    assert canonicalize_vietnamese_location(ward="P.07")[2] == "Phường 7"
    assert canonicalize_vietnamese_location(ward="P7")[2] == "Phường 7"
    assert canonicalize_vietnamese_location(ward="P. Bến Nghé")[2] == "Phường Bến Nghé"
    assert canonicalize_vietnamese_location(ward="X. Vĩnh Lộc A")[2] == "Xã Vĩnh Lộc A"


def test_full_address_with_street_ward_district_city():
    raw = "218/3 Nguyễn Văn Nghi, Phường 7, Quận Gò Vấp, TP.HCM"
    res = parse_vietnamese_address(raw)
    assert res["street_name"] == "218/3 Nguyễn Văn Nghi"
    assert res["ward_name"] == "Phường 7"
    assert res["district_name"] == "Quận Gò Vấp"
    assert res["city_name"] == "Thành phố Hồ Chí Minh"


def test_address_with_street_prefix_and_dots():
    raw = "80/..., Đường Hoàng Hoa Thám, Phường 7, Quận Bình Thạnh, TP.HCM"
    res = parse_vietnamese_address(raw)
    assert "Hoàng Hoa Thám" in res["street_name"]
    assert res["ward_name"] == "Phường 7"
    assert res["district_name"] == "Quận Bình Thạnh"
    assert res["city_name"] == "Thành phố Hồ Chí Minh"


def test_district_and_city_only():
    raw = "Huyện Ba Vì, Hà Nội"
    res = parse_vietnamese_address(raw)
    assert res["street_name"] is None
    assert res["ward_name"] is None
    assert res["district_name"] == "Huyện Ba Vì"
    assert res["city_name"] == "Thành phố Hà Nội"


def test_district_without_prefix_and_city():
    raw = "Bến Cát, TP. Hồ Chí Minh"
    res = parse_vietnamese_address(raw)
    assert res["street_name"] is None
    assert res["ward_name"] is None
    assert res["district_name"] == "Bến Cát"
    assert res["city_name"] == "Thành phố Hồ Chí Minh"


def test_ward_district_city():
    raw = "Phường An Phú, Quận 2, TP Hồ Chí Minh"
    res = parse_vietnamese_address(raw)
    assert res["street_name"] is None
    assert res["ward_name"] == "Phường An Phú"
    assert res["district_name"] == "Quận 2"
    assert res["city_name"] == "Thành phố Hồ Chí Minh"


def test_ward_without_prefix_and_district_and_city():
    raw = "Minh Quang, Ba Vì, Hà Nội"
    res = parse_vietnamese_address(raw)
    assert res["street_name"] is None
    assert res["ward_name"] == "Minh Quang"
    assert res["district_name"] == "Ba Vì"
    assert res["city_name"] == "Thành phố Hà Nội"


def test_complex_apartment_address():
    raw = "Chung cư Mỹ Phước, 280 Bùi Hữu Nghĩa, Phường 2, Bình Thạnh, Hồ Chí Minh"
    res = parse_vietnamese_address(raw)
    assert "280 Bùi Hữu Nghĩa" in res["street_name"]
    assert res["ward_name"] == "Phường 2"
    assert res["district_name"] == "Quận Bình Thạnh"
    assert res["city_name"] == "Thành phố Hồ Chí Minh"


def test_address_with_hints():
    res = parse_vietnamese_address(
        raw_address="218/3 Nguyễn Văn Nghi",
        ward_hint="Phường 7",
        district_hint="Quận Gò Vấp",
        city_hint="TP.HCM",
    )
    assert res["street_name"] == "218/3 Nguyễn Văn Nghi"
    assert res["ward_name"] == "Phường 7"
    assert res["district_name"] == "Quận Gò Vấp"
    assert res["city_name"] == "Thành phố Hồ Chí Minh"


def test_address_without_canonicalization():
    raw = "218/3 Nguyễn Văn Nghi, Phường 7, Quận Gò Vấp, TP.HCM"
    res = parse_vietnamese_address(raw, canonicalize=False)
    assert res["city_name"] == "TP.HCM"
    assert res["district_name"] == "Quận Gò Vấp"


def test_empty_address():
    res = parse_vietnamese_address(None)
    assert res["street_name"] is None
    assert res["ward_name"] is None
    assert res["district_name"] is None
    assert res["city_name"] is None

