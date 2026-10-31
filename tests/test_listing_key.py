import pytest

from src.domain.entities.listing_key import ListingKeyError, build_listing_key


def test_uses_external_code():
    assert (
        build_listing_key("meeyland", external_code=307624752, external_id="6a86b1")
        == "meeyland_307624752"
    )


def test_code_may_be_a_string():
    assert build_listing_key("meeyland", external_code=" 123 ") == "meeyland_123"


@pytest.mark.parametrize("code", [None, "", "  ", 0, -5, True, "a b"])
def test_falls_back_to_id_when_code_unusable(code):
    key = build_listing_key(
        "meeyland", external_code=code, external_id="6a86b17834e5de3cc593accf"
    )
    assert key == "meeyland_6a86b17834e5de3cc593accf"


def test_site_id_is_lowercased():
    assert build_listing_key(" MeeyLand ", external_code=1) == "meeyland_1"


def test_requires_code_or_id():
    with pytest.raises(ListingKeyError):
        build_listing_key("meeyland")
    with pytest.raises(ListingKeyError):
        build_listing_key("meeyland", external_code=0, external_id=" ")


def test_blank_site_id_rejected():
    with pytest.raises(ListingKeyError):
        build_listing_key("  ", external_code=1)
