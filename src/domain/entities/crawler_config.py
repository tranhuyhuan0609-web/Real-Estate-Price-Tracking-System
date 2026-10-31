from __future__ import annotations
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, HttpUrl, field_validator, model_validator

from src.domain.entities.base import NonBlank, StrictBaseModel

# listings.source and crawl_runs.site_id are VARCHAR(20).
SITE_ID_RE = re.compile(r"[a-z0-9_-]{1,20}")
SCHEDULE_TIME_RE = re.compile(r"(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)")
HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# Dotted path with optional list indexes: "oldDistrict.translation[0].name".
JSON_PATH_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*"
)
ALLOWED_PROXY_SCHEMES = frozenset({"http", "https", "socks5"})

RunMode = Literal["incremental", "full_sweep"]

DEFAULT_MAPPING_KEY = "_default"

# Every name a response_mapping may fill. Anything else is a typo.
MAPPABLE_FIELDS = frozenset(
    {
        "external_id",
        "external_code",
        "url",
        "title",
        "description",
        "price_vnd",
        "area_m2",
        "facade_m",
        "depth_m",
        "category",
        "property_type",
        "published_at",
        "project_id",
        "project_name",
        "city_code",
        "city_name",
        "district_code",
        "district_name",
        "ward_code",
        "ward_name",
        "lng",
        "lat",
        "road_width_m",
        "legal_status",
        "thumbnail_url",
        "media_list",
        "advertiser_id",
        "advertiser_name",
        "advertiser_phone",
        "advertiser_email",
        "advertiser_kind",
        "advertiser_is_verified",
    }
)
REQUIRED_MAPPED_FIELDS = frozenset({"url", "title"})

# Mirror the CHECK constraints of the listings / advertisers tables.
ENUM_VALUES: dict[str, frozenset[str]] = {
    "category": frozenset({"sale", "rent", "project"}),
    "property_type": frozenset(
        {"land", "house", "apartment", "villa", "shophouse", "commercial", "other"}
    ),
    "advertiser_kind": frozenset({"broker", "owner", "unknown"}),
}


def normalize_host(host: str | None) -> str:
    """Normalize a hostname for domain comparison."""
    if not host:
        raise ValueError("URL must have a hostname.")
    return host.lower().rstrip(".")


def _validate_path(value: str, name: str) -> str:
    if not JSON_PATH_RE.fullmatch(value):
        raise ValueError(
            f"{name} {value!r} is not a valid path (e.g. 'data.items' or 'a.b[0].c')."
        )
    return value


class RequestSettings(StrictBaseModel):
    """How to call the listing-search endpoint."""

    url: HttpUrl
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    body_template: dict[str, Any] | None = Field(
        default=None,
        description="Static JSON body merged with paging parameters (POST only).",
    )

    @model_validator(mode="after")
    def validate_body(self) -> RequestSettings:
        if self.body_template is not None and self.method != "POST":
            raise ValueError("body_template is only allowed with method='POST'.")
        return self


class PaginationSettings(StrictBaseModel):
    """Page-number pagination with a separate budget per run mode."""

    strategy: Literal["page_number", "offset_limit", "url_pattern"] = "page_number"
    param_location: Literal["query", "body"] = "query"
    page_param: NonBlank = "page"
    limit_param: NonBlank | None = "limit"
    start_page: int = Field(default=1, ge=0)
    page_size: int = Field(default=20, ge=1, le=500)
    max_pages_incremental: int = Field(default=10, ge=1)
    max_pages_full_sweep: int = Field(default=500, ge=1)
    stop_when_empty_path: NonBlank | None = Field(
        default=None,
        description="Path to the item list; stop when it is empty. "
        "Defaults to response_mapping.items_path.",
    )

    @field_validator("stop_when_empty_path")
    @classmethod
    def validate_stop_path(cls, value: str | None) -> str | None:
        return value

    @model_validator(mode="after")
    def validate_budgets(self) -> PaginationSettings:
        if self.max_pages_full_sweep < self.max_pages_incremental:
            raise ValueError(
                "max_pages_full_sweep must be >= max_pages_incremental."
            )
        return self


class RateLimitSettings(StrictBaseModel):
    """Politeness and resilience settings for the HTTP client."""

    download_delay_sec: float = Field(default=1.0, gt=0.0)
    concurrent_requests: int = Field(default=1, ge=1, le=10)
    timeout_sec: float = Field(default=30.0, gt=0.0, le=300.0)
    retry_times: int = Field(default=3, ge=0, le=10)


class ResponseMapping(StrictBaseModel):
    """Where each listing field lives in the JSON response.

    ``value_mappings[field]`` translates source values to stored values. A
    source value with no entry uses ``_default`` when present; otherwise the
    listing is rejected instead of being stored with a guessed value.
    """

    items_path: NonBlank
    fields: dict[str, str]
    value_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator("items_path")
    @classmethod
    def validate_items_path(cls, value: str) -> str:
        return value

    @model_validator(mode="after")
    def validate_mapping(self) -> ResponseMapping:
        unknown = self.fields.keys() - MAPPABLE_FIELDS
        if unknown:
            raise ValueError(f"Unknown mapped fields: {sorted(unknown)}.")

        for name, path in self.fields.items():
            pass

        missing = REQUIRED_MAPPED_FIELDS - self.fields.keys()
        if missing:
            raise ValueError(f"Missing required mapped fields: {sorted(missing)}.")
        if not ({"external_code", "external_id"} & self.fields.keys()):
            raise ValueError(
                "Map at least one of external_code or external_id "
                "(needed to build listing_key)."
            )

        for name, mapping in self.value_mappings.items():
            if name not in self.fields:
                raise ValueError(
                    f"value_mappings[{name!r}] has no matching entry in fields."
                )
            allowed = ENUM_VALUES.get(name)
            if allowed is None:
                continue
            invalid = {v for v in mapping.values() if v not in allowed}
            if invalid:
                raise ValueError(
                    f"value_mappings[{name!r}] has values {sorted(invalid)}; "
                    f"allowed: {sorted(allowed)}."
                )
        return self


class ProxySettings(StrictBaseModel):
    """Proxy configuration with optional proxy rotation."""

    enabled: bool = False
    rotation_enabled: bool = False
    proxies: list[str] = Field(default_factory=list)
    max_failures: int = Field(default=3, ge=1)
    cooldown_seconds: int = Field(default=60, ge=1)

    @field_validator("proxies")
    @classmethod
    def validate_proxies(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            value = value.strip()
            try:
                parts = urlsplit(value)
                scheme = parts.scheme.lower()
                hostname = parts.hostname
                port = parts.port
            except ValueError:
                raise ValueError("Invalid proxy URL.") from None

            if (
                scheme not in ALLOWED_PROXY_SCHEMES
                or not hostname
                or port is None
                or not 1 <= port <= 65535
            ):
                raise ValueError(
                    "Proxy URL must include an allowed scheme, "
                    "a hostname, and a valid port from 1 to 65535."
                )
            normalized.append(value)

        if len(normalized) != len(set(normalized)):
            raise ValueError("Duplicate proxy URLs are not allowed.")
        return normalized

    @model_validator(mode="after")
    def validate_proxy_settings(self) -> ProxySettings:
        if self.rotation_enabled and not self.enabled:
            raise ValueError("Proxy rotation requires enabled=True.")
        if self.enabled and not self.proxies:
            raise ValueError("At least one proxy is required when proxy is enabled.")
        if self.rotation_enabled and len(self.proxies) < 2:
            raise ValueError("Proxy rotation requires at least two proxies.")
        if not self.enabled and self.proxies:
            raise ValueError("Proxy URLs must be empty when proxy is disabled.")
        return self

    def __repr__(self) -> str:
        return (
            "ProxySettings("
            f"enabled={self.enabled}, "
            f"rotation_enabled={self.rotation_enabled}, "
            f"proxy_count={len(self.proxies)}"
            ")"
        )

    __str__ = __repr__


class DetailPageSettings(StrictBaseModel):
    """Configuration for scraping the individual listing detail page."""

    enabled: bool = False
    url_field: str = "url"
    fields: dict[str, str] = Field(default_factory=dict)


class CrawlerConfig(StrictBaseModel):
    """Validated configuration for one crawl source.

    The first six fields are stored as columns of ``crawler_configs``; the rest
    live in its ``config_json``.
    """

    site_id: NonBlank
    name: NonBlank = Field(max_length=100)
    enabled: bool = True
    crawler_type: Literal["rest_api", "html"] = "rest_api"
    schedule_times: list[str] = Field(
        default_factory=list,
        description=(
            "Fixed daily times 'HH:MM' in source_timezone. When provided they "
            "override interval_seconds."
        ),
    )
    interval_seconds: int = Field(default=300, ge=60)

    request: RequestSettings
    pagination: PaginationSettings = Field(default_factory=PaginationSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    response_mapping: ResponseMapping
    detail_page: DetailPageSettings = Field(default_factory=DetailPageSettings)

    allowed_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Hosts accepted for listing URLs. Empty means the request host and "
            "its parent domain (api.example.com -> example.com)."
        ),
    )
    parser_version: NonBlank = "2.0.0"
    proxy_settings: ProxySettings = Field(default_factory=ProxySettings)
    source_timezone: str = "UTC"

    @field_validator("site_id")
    @classmethod
    def validate_site_id(cls, value: str) -> str:
        value = value.lower()
        if not SITE_ID_RE.fullmatch(value):
            raise ValueError(
                "site_id must be 1-20 characters of a-z, 0-9, underscores, "
                "and hyphens."
            )
        return value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("schedule_times")
    @classmethod
    def validate_schedule_times(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            match = SCHEDULE_TIME_RE.fullmatch(value.strip())
            if match is None:
                raise ValueError(
                    f"Invalid schedule time {value!r}; use 'HH:MM' (00:00-23:59)."
                )
            normalized.append(f"{int(match['h']):02d}:{match['m']}")

        if len(normalized) != len(set(normalized)):
            raise ValueError("Duplicate schedule_times are not allowed.")
        return sorted(normalized)

    @field_validator("source_timezone")
    @classmethod
    def validate_source_timezone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError(f"Invalid source timezone: {value!r}") from None
        return value

    @field_validator("allowed_domains")
    @classmethod
    def normalize_allowed_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if not domain or not all(
                HOST_LABEL_RE.fullmatch(label) for label in domain.split(".")
            ):
                raise ValueError(
                    f"Invalid hostname: {value!r}. Use a plain ASCII hostname "
                    "without scheme, path, or port; convert IDNs to punycode."
                )
            normalized.append(domain)

        if len(normalized) != len(set(normalized)):
            raise ValueError("Duplicate allowed domains are not allowed.")
        return normalized

    @model_validator(mode="after")
    def validate_pagination_location(self) -> CrawlerConfig:
        if (
            self.pagination.param_location == "body"
            and self.request.method != "POST"
        ):
            raise ValueError("pagination.param_location='body' requires method='POST'.")
        return self

    @model_validator(mode="after")
    def validate_crawler_type_and_paths(self) -> CrawlerConfig:
        if self.crawler_type == "rest_api":
            _validate_path(self.response_mapping.items_path, "items_path")
            for name, path in self.response_mapping.fields.items():
                _validate_path(path, f"fields[{name!r}]")
            if self.pagination.stop_when_empty_path:
                _validate_path(self.pagination.stop_when_empty_path, "stop_when_empty_path")
        return self

    @property
    def schedule_interval_seconds(self) -> int:
        """Alias kept for the scheduler."""
        return self.interval_seconds

    @property
    def effective_allowed_domains(self) -> tuple[str, ...]:
        if self.allowed_domains:
            return tuple(self.allowed_domains)
        host = normalize_host(self.request.url.host)
        labels = host.split(".")
        if len(labels) >= 3:
            return (host, ".".join(labels[1:]))
        return (host,)

    def is_allowed_domain(self, domain: str) -> bool:
        """Return whether a hostname matches the allowlist."""
        domain = normalize_host(domain)
        return any(
            domain == allowed or domain.endswith("." + allowed)
            for allowed in self.effective_allowed_domains
        )

    @property
    def items_stop_path(self) -> str:
        return self.pagination.stop_when_empty_path or self.response_mapping.items_path

    def max_pages(self, run_mode: RunMode) -> int:
        """Page budget for one crawl run of the given mode."""
        if run_mode == "incremental":
            return self.pagination.max_pages_incremental
        if run_mode == "full_sweep":
            return self.pagination.max_pages_full_sweep
        raise ValueError(f"Unknown run_mode: {run_mode!r}")

    def latest_scheduled_slot(self, now: datetime) -> datetime | None:
        """The most recent scheduled slot up to ``now`` (aware, UTC)."""
        if not self.schedule_times:
            return None
        tz = self.source_zoneinfo()
        local_now = now.astimezone(tz)
        slots = []
        for day_offset in (0, -1):
            day = (local_now + timedelta(days=day_offset)).date()
            for text in self.schedule_times:
                hour, minute = map(int, text.split(":"))
                slots.append(datetime.combine(day, time(hour, minute), tzinfo=tz))
        past = [s for s in slots if s <= local_now]
        return max(past).astimezone(timezone.utc)

    def source_zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.source_timezone)
