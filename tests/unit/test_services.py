"""Unit tests for domain services (price change and fingerprint)."""

import pytest
from realestate.domain.services.price_change import evaluate_price_change
from realestate.domain.services.fingerprint import generate_fingerprint
from realestate.domain.entities.listing import PriceStatus


def test_evaluate_price_change_initial():
    eval_res = evaluate_price_change(old_price=None, new_price=5_000_000_000)
    assert eval_res.price_status == PriceStatus.INITIAL
    assert eval_res.price_diff_vnd == 0
    assert eval_res.price_diff_percent == 0.0


def test_evaluate_price_change_increased():
    eval_res = evaluate_price_change(old_price=5_000_000_000, new_price=5_500_000_000)
    assert eval_res.price_status == PriceStatus.INCREASED
    assert eval_res.price_diff_vnd == 500_000_000
    assert eval_res.price_diff_percent == 10.0


def test_evaluate_price_change_decreased():
    eval_res = evaluate_price_change(old_price=5_000_000_000, new_price=4_500_000_000)
    assert eval_res.price_status == PriceStatus.DECREASED
    assert eval_res.price_diff_vnd == -500_000_000
    assert eval_res.price_diff_percent == -10.0


def test_evaluate_price_change_unchanged():
    eval_res = evaluate_price_change(old_price=5_000_000_000, new_price=5_000_000_000)
    assert eval_res.price_status == PriceStatus.UNCHANGED
    assert eval_res.price_diff_vnd == 0
    assert eval_res.price_diff_percent == 0.0


def test_fingerprint_stability():
    # Fingerprint should be stable across runs regardless of unmasked or masked phone
    fp1 = generate_fingerprint(
        title="Bán nhà phố mặt tiền đường số 10",
        city_name="Thành phố Hồ Chí Minh",
        district_name="Quận 7",
        ward_name="Phường Tân Quy",
        area_m2=85.0,
        advertiser_phone="0901234567",
    )
    fp2 = generate_fingerprint(
        title="Bán nhà phố mặt tiền đường số 10",
        city_name="Thành phố Hồ Chí Minh",
        district_name="Quận 7",
        ward_name="Phường Tân Quy",
        area_m2=85.0,
        advertiser_phone="0901***567",
    )
    assert fp1 == fp2
