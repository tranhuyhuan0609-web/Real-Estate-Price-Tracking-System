from __future__ import annotations
from typing import Optional
from realestate.domain.value_objects.price import PriceChangeEvaluation, PriceStatus

def evaluate_price_change(
    old_price: Optional[float | int],
    new_price: Optional[float | int],
) -> PriceChangeEvaluation:
    """Compare old and new price and return difference amount, percentage and status."""
    if new_price is None or new_price <= 0:
        return PriceChangeEvaluation(
            price_diff_vnd=0.0,
            price_diff_percent=0.0,
            price_status=PriceStatus.CONTACT,
        )

    if old_price is None or old_price <= 0:
        return PriceChangeEvaluation(
            price_diff_vnd=0.0,
            price_diff_percent=0.0,
            price_status=PriceStatus.INITIAL,
        )

    diff_vnd = float(new_price) - float(old_price)
    diff_percent = round((diff_vnd / float(old_price)) * 100, 2)

    if diff_vnd > 0:
        status = PriceStatus.INCREASED
    elif diff_vnd < 0:
        status = PriceStatus.DECREASED
    else:
        status = PriceStatus.UNCHANGED

    return PriceChangeEvaluation(
        price_diff_vnd=diff_vnd,
        price_diff_percent=diff_percent,
        price_status=status,
    )
