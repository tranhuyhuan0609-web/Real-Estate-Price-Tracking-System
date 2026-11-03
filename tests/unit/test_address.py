"""Unit tests for address and administrative division parser."""

import pytest
from realestate.domain.location.address_parser import parse_vietnamese_address


def test_parse_standard_hcm():
    addr = "Đường số 10, Phường Tân Quy, Quận 7, TP HCM"
    res = parse_vietnamese_address(addr)
    assert res["city_name"] == "Thành phố Hồ Chí Minh"
    assert res["district_name"] == "Quận 7"
    assert res["ward_name"] == "Phường Tân Quy"


def test_parse_hanoi():
    addr = "120 Kim Mã, Phường Kim Mã, Quận Ba Đình, Hà Nội"
    res = parse_vietnamese_address(addr)
    assert res["city_name"] == "Thành phố Hà Nội"
    assert res["district_name"] == "Quận Ba Đình"
    assert res["ward_name"] == "Phường Kim Mã"


def test_parse_hoa_binh_ward_not_city():
    # "Phường Hòa Bình, Quận Ninh Kiều, Cần Thơ" -> City is Cần Thơ, not Hòa Bình!
    addr = "Số 15 Lý Tự Trọng, Phường Hòa Bình, Quận Ninh Kiều, TP Cần Thơ"
    res = parse_vietnamese_address(addr)
    assert res["city_name"] == "Thành phố Cần Thơ"
    assert res["district_name"] == "Quận Ninh Kiều"
    assert res["ward_name"] == "Phường Hòa Bình"
