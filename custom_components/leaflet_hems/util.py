"""Small helpers shared by the Leaflet HEMS integration."""

from __future__ import annotations

from typing import Any, Mapping


def normalize_uuid(value: Any) -> str:
    """Return a UUID string without braces, in lower case.

    nymea emits UUIDs in both ``{uuid}`` and ``uuid`` form depending on the
    transport (mDNS TXT records vs JSON-RPC).  The integration uses the
    normalized form for unique IDs and cache keys so a device is never
    discovered as a second device just because the braces differ.
    """

    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return text.lower()


def clean_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Strip the optional ``o:`` prefix nymea uses in introspection output."""

    if not params:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        cleaned[str(key).removeprefix("o:")] = value
    return cleaned


def to_float(value: Any) -> float | None:
    """Convert a JSON number/bool to float, returning None for invalid data."""

    if value is None or isinstance(value, str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
