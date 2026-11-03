"""Unit tests for PhoneRevealer and RevealPhoneUseCase."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.infrastructure.crawler.phone_revealer import PhoneRevealer, clean_phone_number
from src.application.use_cases.reveal_phone_uc import RevealPhoneUseCase
from src.infrastructure.database.pg_phone_repo import PgPhoneListingRepository


def test_clean_phone_number_formats():
    assert clean_phone_number("+84912345678") == "0912345678"
    assert clean_phone_number("84912345678") == "0912345678"
    assert clean_phone_number("091.234.5678") == "0912345678"
    assert clean_phone_number("091 234 5678") == "0912345678"
    assert clean_phone_number("(091) 234-5678") == "0912345678"
    assert clean_phone_number("091748****") is None
    assert clean_phone_number("091748xxxx") is None
    assert clean_phone_number("12345") is None
    assert clean_phone_number(None) is None


@pytest.mark.asyncio
async def test_revealer_returns_existing_unmasked_phone_immediately():
    revealer = PhoneRevealer()
    res = await revealer.reveal_phone(
        url="https://example.com/item/1",
        existing_phone="0912345678",
    )
    assert res == "0912345678"


def test_revealer_extracts_from_data_attributes():
    revealer = PhoneRevealer()
    html = """
    <div class="contact-box">
        <button class="btn-phone" data-phone="0987654321">Bấm để hiện số</button>
    </div>
    """
    assert revealer.extract_from_html(html) == "0987654321"


def test_revealer_extracts_from_tel_link():
    revealer = PhoneRevealer()
    html = """
    <div class="agent">
        <a href="tel:0909123456" class="phone-link">Liên hệ</a>
    </div>
    """
    assert revealer.extract_from_html(html) == "0909123456"


def test_revealer_extracts_from_zalo_link():
    revealer = PhoneRevealer()
    html = """
    <div class="chat">
        <a href="https://zalo.me/0933112233" target="_blank">Chat qua Zalo</a>
    </div>
    """
    assert revealer.extract_from_html(html) == "0933112233"


def test_revealer_extracts_from_inline_script():
    revealer = PhoneRevealer()
    html = """
    <html>
        <script>
            window.__INITIAL_STATE__ = {
                "listing": {
                    "id": "12345",
                    "contact_phone": "0977889900"
                }
            };
        </script>
    </html>
    """
    assert revealer.extract_from_html(html) == "0977889900"


@pytest.mark.asyncio
async def test_reveal_phone_use_case_success():
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = {
        "listing_key": "mogi_12345",
        "source": "mogi",
        "advertiser_id": "adv_99",
        "advertiser_name": "Nguyen Son",
        "url": "https://mogi.vn/tin-dang-12345",
        "advertiser_phone": "091748****",
    }

    mock_conn = AsyncMock()
    mock_conn.cursor = MagicMock()
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__.return_value = mock_conn

    mock_revealer = AsyncMock()
    mock_revealer.reveal_phone.return_value = "0917485869"

    use_case = RevealPhoneUseCase(repo=PgPhoneListingRepository(mock_pool), revealer=mock_revealer)
    result = await use_case.execute("mogi_12345")

    assert result["listing_key"] == "mogi_12345"
    assert result["advertiser_phone"] == "0917485869"
    assert result["status"] == "revealed"
    assert mock_cursor.execute.call_count == 2  # SELECT, UPDATE advertisers
    assert mock_conn.commit.called


@pytest.mark.asyncio
async def test_reveal_phone_use_case_creates_advertiser_if_missing():
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = {
        "listing_key": "mogi_12345",
        "source": "mogi",
        "advertiser_id": None,
        "advertiser_name": "Nguyen Son",
        "url": "https://mogi.vn/tin-dang-12345",
        "advertiser_phone": None,
    }

    mock_conn = AsyncMock()
    mock_conn.cursor = MagicMock()
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__.return_value = mock_conn

    mock_revealer = AsyncMock()
    mock_revealer.reveal_phone.return_value = "0917485869"

    use_case = RevealPhoneUseCase(repo=PgPhoneListingRepository(mock_pool), revealer=mock_revealer)
    result = await use_case.execute("mogi_12345")

    assert result["listing_key"] == "mogi_12345"
    assert result["advertiser_phone"] == "0917485869"
    assert result["status"] == "revealed"
    assert mock_cursor.execute.call_count == 3  # SELECT, INSERT advertisers, UPDATE listings
    assert mock_conn.commit.called


def test_revealer_extracts_email_from_html():
    revealer = PhoneRevealer()

    # Cloudflare data-cfemail: XOR key 0x12 -> decoded
    # Let's encode test@example.com with key 0x12:
    # 't' (0x74 ^ 0x12 = 0x66), 'e' (0x65 ^ 0x12 = 0x77), ...
    cf_hex = "126677616652776a737f627e773c717d7f"  # test@example.com
    html_cf = f'<span class="__cf_email__" data-cfemail="{cf_hex}">[email&#160;protected]</span>'
    assert revealer.extract_email_from_html(html_cf) == "test@example.com"

    # Homedy chat attribute
    html_homedy = '<a class="btn chatpostinfo" data-id="chatpostinfo_owner@domain.com">Chat</a>'
    assert revealer.extract_email_from_html(html_homedy) == "owner@domain.com"

    # Mailto link
    html_mailto = '<a href="mailto:broker@realestate.vn">Email me</a>'
    assert revealer.extract_email_from_html(html_mailto) == "broker@realestate.vn"

    # Text fallback
    html_text = '<p>Liên hệ chính chủ: minh.land@gmail.com</p>'
    assert revealer.extract_email_from_html(html_text) == "minh.land@gmail.com"
