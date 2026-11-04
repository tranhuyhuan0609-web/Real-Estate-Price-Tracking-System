"""Unit tests for Vietnamese real estate area parsing."""

from realestate.infrastructure.crawler.parsing.area import parse_vietnamese_area


def test_parse_area_standard():
    assert parse_vietnamese_area("85.5 m2") == 85.5
    assert parse_vietnamese_area("120 m²") == 120.0
    assert parse_vietnamese_area("60,5 mét vuông") == 60.5


def test_parse_area_thousand_separator():
    # "1.200 m²" in Vietnamese denotes 1200 m2, NOT 1.2 m2
    assert parse_vietnamese_area("1.200 m²") == 1200.0
    assert parse_vietnamese_area("1.675 m²") == 1675.0
    assert parse_vietnamese_area("1,700 m2") == 1700.0


def test_parse_area_invalid():
    assert parse_vietnamese_area("Không rõ") is None
    assert parse_vietnamese_area(None) is None
    assert parse_vietnamese_area("-50 m2") is None


def test_parse_area_with_superscript_unit_split_by_text_extraction():
    # "250 m<sup>2</sup>" extracted with "li ::text" gives "250 m 2"
    assert parse_vietnamese_area("250 m 2") == 250.0
    assert parse_vietnamese_area("250 m") is None
