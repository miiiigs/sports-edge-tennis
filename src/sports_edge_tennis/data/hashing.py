from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from .normalize import normalize_hash_text


HASH_RECIPE_VERSION = "SE_SOURCE_HASH_V1"


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _canonical_timestamp(value: datetime) -> str:
    instant = value.astimezone(timezone.utc)
    return instant.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonicalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _canonical_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return _canonical_decimal(Decimal(str(value)))
    if isinstance(value, str):
        return normalize_hash_text(value)
    raise TypeError(f"Unsupported hash value type: {type(value)!r}")


def canonical_hash_payload(source_system: str, fields: dict[str, Any]) -> dict[str, object]:
    items: list[list[object]] = []
    for field_name in sorted(fields):
        items.append([field_name, canonicalize_value(fields[field_name])])
    return {
        "version": HASH_RECIPE_VERSION,
        "source_system": normalize_hash_text(source_system),
        "fields": items,
    }


def source_hash(source_system: str, fields: dict[str, Any]) -> str:
    payload = canonical_hash_payload(source_system, fields)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{HASH_RECIPE_VERSION}:{digest}"

