from __future__ import annotations
import re


class ListingKey(str):
    """Value object representing a unique canonical listing key."""
    pass


def build_listing_key(
    site_id: str,
    external_code: int | str | None = None,
    external_id: int | str | None = None,
) -> str:
    """Generate a canonical, stable listing_key from site_id and source codes."""
    site_clean = (site_id or "").strip().lower()
    if not site_clean:
        raise ValueError("site_id must not be empty.")

    code_candidate = str(external_code).strip() if external_code is not None else ""
    id_candidate = str(external_id).strip() if external_id is not None else ""

    primary = code_candidate or id_candidate
    if not primary:
        raise ValueError(
            f"Cannot build listing_key for '{site_id}': both external_code and external_id are missing."
        )

    clean_identifier = re.sub(r"[^a-zA-Z0-9_-]", "_", primary)
    return f"{site_clean}_{clean_identifier}"


def parse_listing_key(key: str) -> tuple[str, str]:
    """Split a listing_key into (site_id, source_id)."""
    parts = key.split("_", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid listing_key format: '{key}'")
    return parts[0], parts[1]
