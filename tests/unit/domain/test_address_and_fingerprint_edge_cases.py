"""Edge cases for address parsing and fingerprint stability."""

from realestate.domain.services import generate_fingerprint, parse_vietnamese_address


def test_parse_vietnamese_address_edge_cases():
    # Complex address ending with "TP HCM" and ward named after a province
    addr = "Đường Nguyễn Trãi, Phường Hòa Bình, Quận 1, TP HCM"
    parsed = parse_vietnamese_address(addr)
    assert parsed["city_name"] == "Thành phố Hồ Chí Minh"
    assert parsed["district_name"] == "Quận 1"
    assert parsed["ward_name"] == "Phường Hòa Bình"
    assert parsed["street_name"] == "Đường Nguyễn Trãi"


def test_generate_fingerprint_stability():
    f1 = generate_fingerprint(
        title="Bán nhà mặt phố Quận 1",
        area_m2=80.0,
        city_name="Thành phố Hồ Chí Minh",
        district_name="Quận 1",
        ward_name="Phường Bến Nghé",
        advertiser_phone="0912345678",
    )
    f2 = generate_fingerprint(
        title="Bán nhà mặt phố Quận 1",
        area_m2=80.0,
        city_name="Thành phố Hồ Chí Minh",
        district_name="Quận 1",
        ward_name="Phường Bến Nghé",
        advertiser_phone=None,
    )
    assert f1 == f2
