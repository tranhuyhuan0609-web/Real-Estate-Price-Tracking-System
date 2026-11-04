from unittest.mock import AsyncMock

import pytest

from realestate.infrastructure.persistence.postgres.phone_repo import PgPhoneListingRepository
from tests.helpers import make_mock_pool


@pytest.mark.asyncio
async def test_get_listing_for_phone_reveal():
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = {"listing_key": "k1", "url": "https://example.com/1"}
    pool = make_mock_pool(mock_cursor)

    repo = PgPhoneListingRepository(pool)
    res = await repo.get_listing_for_phone_reveal("k1")

    assert res == {"listing_key": "k1", "url": "https://example.com/1"}
    assert mock_cursor.execute.called


@pytest.mark.asyncio
async def test_update_advertiser_phone_with_existing_advertiser():
    mock_cursor = AsyncMock()
    pool = make_mock_pool(mock_cursor)
    repo = PgPhoneListingRepository(pool)

    adv_id = await repo.update_advertiser_phone(
        listing_key="k1",
        source="s1",
        advertiser_id="adv_123",
        advertiser_name="Name",
        phone="0912345678",
    )

    assert adv_id == "adv_123"
    assert mock_cursor.execute.called


@pytest.mark.asyncio
async def test_update_advertiser_phone_without_advertiser_id():
    mock_cursor = AsyncMock()
    pool = make_mock_pool(mock_cursor)
    repo = PgPhoneListingRepository(pool)

    adv_id = await repo.update_advertiser_phone(
        listing_key="k1",
        source="s1",
        advertiser_id=None,
        advertiser_name="Name",
        phone="0912345678",
    )

    assert adv_id == "adv_s1_0912345678"
    assert mock_cursor.execute.call_count == 2


@pytest.mark.asyncio
async def test_update_advertiser_contact_with_email_and_phone():
    mock_cursor = AsyncMock()
    pool = make_mock_pool(mock_cursor)
    repo = PgPhoneListingRepository(pool)

    adv_id = await repo.update_advertiser_contact(
        listing_key="k1",
        source="s1",
        advertiser_id=None,
        advertiser_name="Name",
        phone="0912345678",
        email="test@example.com",
    )

    assert adv_id == "adv_s1_0912345678"
    assert mock_cursor.execute.call_count == 2
