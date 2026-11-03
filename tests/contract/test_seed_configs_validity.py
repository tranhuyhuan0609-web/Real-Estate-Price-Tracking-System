"""Test that all 11 seed configs in sql/02_seed_configs.sql are valid and loadable without errors."""

import json
import re
from pathlib import Path
import pytest
from realestate.infrastructure.persistence.postgres.config_mapping import row_to_config
from realestate.infrastructure.crawler.parsing.listing_parser import extract_by_json_path, parse_vietnamese_price
from realestate.domain.services import parse_vietnamese_address, generate_fingerprint


def test_all_11_seed_configs_load_successfully():
    sql_path = Path("sql/02_seed_configs.sql")
    sql = sql_path.read_text(encoding="utf-8")

    # Find each INSERT statement
    inserts = re.split(r"(?i)INSERT INTO crawler_configs", sql)[1:]
    assert len(inserts) == 11, f"Expected 11 seed configs, found {len(inserts)}"

    loaded_sites = []
    for insert in inserts:
        # Extract site_id
        m_site = re.search(r"VALUES\s*\(\s*'([^']+)'", insert)
        assert m_site is not None
        site_id = m_site.group(1)

        # Extract name
        m_name = re.search(rf"'{re.escape(site_id)}',\s*'([^']+)'", insert)
        name = m_name.group(1) if m_name else site_id

        # Extract crawler_type
        m_type = re.search(r"'(rest_api|html|json_ld|ssr_next)'", insert)
        crawler_type = m_type.group(1)

        # Extract schedule_times
        m_sched = re.search(r"'(\{[^}]+\})'", insert)
        schedule_times = [s.strip().strip('"') for s in m_sched.group(1).strip("{}").split(",")] if m_sched else []

        # Extract interval_seconds
        m_int = re.search(r"'(?:\{[^}]+\})',\s*(\d+|NULL)", insert)
        interval_seconds = int(m_int.group(1)) if (m_int and m_int.group(1) != "NULL") else None

        # Extract config_json (starts with {"request": )
        m_json = re.search(r"'({\s*\"request\"[\s\S]*?})'::jsonb", insert)
        assert m_json is not None, f"Could not find config_json for {site_id}"
        config_json = m_json.group(1)

        row = {
            "site_id": site_id,
            "name": name,
            "enabled": True,
            "crawler_type": crawler_type,
            "schedule_times": schedule_times,
            "interval_seconds": interval_seconds,
            "config_json": json.loads(config_json),
        }

        config = row_to_config(row)
        assert config.site_id == site_id
        loaded_sites.append(site_id)

    assert len(loaded_sites) == 11
    assert "nhatot" in loaded_sites
    assert "nhatot_hn" in loaded_sites
    assert "meeyland" in loaded_sites


def test_extract_by_json_path_fallback_candidates():
    data = {
        "account_name": "John Doe",
        "nested": {"val": 123},
    }
    # First candidate is missing, second candidate is present
    val = extract_by_json_path(data, "seller_info.full_name, full_name, account_name")
    assert val == "John Doe"

    # First candidate is present
    val2 = extract_by_json_path(data, "nested.val, account_name")
    assert val2 == 123


def test_parse_vietnamese_price_compound_vs_area():
    # "3 tỷ 100 m2" with area=100 -> price should be 3 tỷ (3_000_000_000), not 3.1 tỷ
    total, pm2 = parse_vietnamese_price("3 tỷ 100 m2", area=100)
    assert total == 3_000_000_000.0
    assert pm2 == 30_000_000.0

    # Genuine compound price: "3 tỷ 500 triệu"
    total_cmp, pm2_cmp = parse_vietnamese_price("3 tỷ 500 triệu", area=100)
    assert total_cmp == 3_500_000_000.0
    assert pm2_cmp == 35_000_000.0


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
