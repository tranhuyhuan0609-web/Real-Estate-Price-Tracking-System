from __future__ import annotations


class ListingKeyError(ValueError):
    """Raised when a listing key cannot be built."""


def _clean_site_id(site_id: str) -> str:
    if not isinstance(site_id, str) or not site_id.strip():
        raise ListingKeyError("site_id must not be blank")
    return site_id.strip().lower()


def _clean_identifier(value: int | str | None) -> str | None:
    """Normalize an external identifier, or None when it is unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text or any(ch.isspace() for ch in text):
            return None
        return text
    return None


def build_listing_key(
    site_id: str,
    *,
    external_code: int | str | None = None,
    external_id: int | str | None = None,
) -> str:
    """Return ``<site_id>_<external_code>``, falling back to ``<site_id>_<external_id>``.

    The key is stable across URL changes (slug edits do not create a new
    listing). A non-positive or blank code counts as missing.
    """
    site = _clean_site_id(site_id)
    for identifier in (external_code, external_id):
        text = _clean_identifier(identifier)
        if text is not None:
            return f"{site}_{text}"
    raise ListingKeyError("either external_code or external_id is required")
