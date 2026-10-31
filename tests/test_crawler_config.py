import copy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.domain.entities.crawler_config import CrawlerConfig


@pytest.fixture
def config_data(seed_row) -> dict:
    return {**seed_row["config_json"], **{k: v for k, v in seed_row.items() if k != "config_json"}}


def make(data: dict, **changes) -> CrawlerConfig:
    merged = copy.deepcopy(data)
    for key, value in changes.items():
        merged[key] = value
    return CrawlerConfig.model_validate(merged)


def test_seed_config_is_valid(config_data):
    config = CrawlerConfig.model_validate(config_data)
    assert config.site_id == "meeyland"
    assert config.request.method == "POST"
    assert config.pagination.param_location == "body"
    assert config.rate_limit.download_delay_sec == 1.5
    assert config.response_mapping.fields["district_name"] == "oldDistrict.translation[0].name"


def test_page_budget_per_run_mode(config_data):
    config = CrawlerConfig.model_validate(config_data)
    assert config.max_pages("incremental") == 10
    assert config.max_pages("full_sweep") == 500
    with pytest.raises(ValueError):
        config.max_pages("weekly")


def test_stop_path_defaults_to_items_path(config_data):
    data = copy.deepcopy(config_data)
    del data["pagination"]["stop_when_empty_path"]
    assert CrawlerConfig.model_validate(data).items_stop_path == data["response_mapping"]["items_path"]


def test_allowed_domains_derived_from_request_host(config_data):
    config = CrawlerConfig.model_validate(config_data)
    assert config.effective_allowed_domains == ("api5.meeyland.com", "meeyland.com")
    assert config.is_allowed_domain("meeyland.com")
    assert config.is_allowed_domain("www.meeyland.com")
    assert not config.is_allowed_domain("evil.example.com")


def test_explicit_allowed_domains_win(config_data):
    config = make(config_data, allowed_domains=["Example.COM."])
    assert config.effective_allowed_domains == ("example.com",)


def test_schedule_interval_alias(config_data):
    assert CrawlerConfig.model_validate(config_data).schedule_interval_seconds == 300


@pytest.mark.parametrize(
    "site_id", ["", "Mee yland", "a" * 21, "mee.yland"]
)
def test_bad_site_id(config_data, site_id):
    with pytest.raises(ValidationError):
        make(config_data, site_id=site_id)


def test_name_limited_to_100_chars(config_data):
    with pytest.raises(ValidationError):
        make(config_data, name="x" * 101)


def test_unknown_top_level_key_rejected(config_data):
    with pytest.raises(ValidationError):
        make(config_data, selectors={"item": "div"})


def test_crawler_type_html_rejected(config_data):
    cfg = make(config_data, crawler_type="html")
    assert cfg.crawler_type == "html"


def test_body_paging_requires_post(config_data):
    data = copy.deepcopy(config_data)
    data["request"]["method"] = "GET"
    data["request"].pop("body_template")
    with pytest.raises(ValidationError, match="requires method='POST'"):
        CrawlerConfig.model_validate(data)


def test_body_template_requires_post(config_data):
    data = copy.deepcopy(config_data)
    data["request"]["method"] = "GET"
    data["pagination"]["param_location"] = "query"
    with pytest.raises(ValidationError, match="body_template"):
        CrawlerConfig.model_validate(data)


def test_full_sweep_budget_not_below_incremental(config_data):
    data = copy.deepcopy(config_data)
    data["pagination"]["max_pages_full_sweep"] = 5
    with pytest.raises(ValidationError, match="max_pages_full_sweep"):
        CrawlerConfig.model_validate(data)


def test_unknown_mapped_field_rejected(config_data):
    data = copy.deepcopy(config_data)
    data["response_mapping"]["fields"]["pricevnd"] = "totalPrice"
    with pytest.raises(ValidationError, match="Unknown mapped fields"):
        CrawlerConfig.model_validate(data)


@pytest.mark.parametrize("missing", ["url", "title"])
def test_required_mapped_fields(config_data, missing):
    data = copy.deepcopy(config_data)
    del data["response_mapping"]["fields"][missing]
    with pytest.raises(ValidationError, match="required mapped fields"):
        CrawlerConfig.model_validate(data)


def test_needs_code_or_id(config_data):
    data = copy.deepcopy(config_data)
    del data["response_mapping"]["fields"]["external_code"]
    del data["response_mapping"]["fields"]["external_id"]
    with pytest.raises(ValidationError, match="external_code or external_id"):
        CrawlerConfig.model_validate(data)


def test_id_alone_is_enough(config_data):
    data = copy.deepcopy(config_data)
    del data["response_mapping"]["fields"]["external_code"]
    CrawlerConfig.model_validate(data)


@pytest.mark.parametrize("path", ["a..b", "a[x]", "1abc", "a.b[0", ""])
def test_bad_json_path(config_data, path):
    data = copy.deepcopy(config_data)
    data["response_mapping"]["fields"]["title"] = path
    with pytest.raises(ValidationError):
        CrawlerConfig.model_validate(data)


def test_enum_value_mapping_checked_against_db_constraints(config_data):
    data = copy.deepcopy(config_data)
    data["response_mapping"]["value_mappings"]["category"]["mua_ban"] = "buy"
    with pytest.raises(ValidationError, match="allowed"):
        CrawlerConfig.model_validate(data)


def test_value_mapping_needs_matching_field(config_data):
    data = copy.deepcopy(config_data)
    data["response_mapping"]["value_mappings"]["road_width_m"] = {"x": "y"}
    with pytest.raises(ValidationError, match="no matching entry"):
        CrawlerConfig.model_validate(data)


def test_schedule_times_normalized_and_sorted(config_data):
    config = make(config_data, schedule_times=["8:05", "07:00"])
    assert config.schedule_times == ["07:00", "08:05"]


def test_latest_scheduled_slot(config_data):
    config = make(
        config_data, schedule_times=["08:00"], source_timezone="Asia/Ho_Chi_Minh"
    )
    now = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)  # 10:00 in Vietnam
    assert config.latest_scheduled_slot(now) == datetime(
        2026, 10, 30, 1, 0, tzinfo=timezone.utc
    )
    assert make(config_data).latest_scheduled_slot(now) is None
