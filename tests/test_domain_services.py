"""Unit tests for domain services: key generation, fingerprinting, and price evaluation."""

import pytest
from src.domain.entities import PriceStatus
from src.domain.services import (
    evaluate_price_change,
    generate_fingerprint,
    generate_listing_key,
)


def test_generate_listing_key_with_code():
    assert generate_listing_key("meeyland", external_code=998877, external_id="abc") == "meeyland_998877"


def test_generate_listing_key_fallback_to_id():
    assert generate_listing_key("meeyland", external_code=None, external_id="id_123") == "meeyland_id_123"


def test_generate_fingerprint_deterministic_and_normalized():
    fp1 = generate_fingerprint(
        title="Bán nhà mặt phố Cầu Giấy 50m2 cực đẹp!!!",
        area_m2=50.0,
        city_code="HN",
        district_code="CG",
        ward_code="W01",
        advertiser_phone="0912.345.678",
    )
    fp2 = generate_fingerprint(
        title="  bán nhà mặt phố cầu giấy 50m2 cực đẹp  ",
        area_m2=50.0,
        city_code="hn",
        district_code="cg",
        ward_code="w01",
        advertiser_phone="0912345678",
    )
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256


def test_evaluate_price_change_initial():
    res = evaluate_price_change(old_price=None, new_price=3000000000)
    assert res.price_status == PriceStatus.INITIAL
    assert res.price_diff_vnd == 0.0
    assert res.price_diff_percent == 0.0


def test_evaluate_price_change_increased():
    res = evaluate_price_change(old_price=2000000000, new_price=2200000000)
    assert res.price_status == PriceStatus.INCREASED
    assert res.price_diff_vnd == 200000000.0
    assert res.price_diff_percent == 10.0


def test_evaluate_price_change_decreased():
    res = evaluate_price_change(old_price=4000000000, new_price=3600000000)
    assert res.price_status == PriceStatus.DECREASED
    assert res.price_diff_vnd == -400000000.0
    assert res.price_diff_percent == -10.0


def test_evaluate_price_change_unchanged():
    res = evaluate_price_change(old_price=5000000000, new_price=5000000000)
    assert res.price_status == PriceStatus.UNCHANGED
    assert res.price_diff_vnd == 0.0
    assert res.price_diff_percent == 0.0


def test_evaluate_price_change_contact():
    res = evaluate_price_change(old_price=3000000000, new_price=None)
    assert res.price_status == PriceStatus.CONTACT

