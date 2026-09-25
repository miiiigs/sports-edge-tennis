"""Deterministic serialization and hashing for replay artifacts.

Replay reproducibility requires that the same replay version plus the same
input snapshot produces a byte-identical canonical payload and hash. This module
never depends on dict insertion order, floating point, or local timezones.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum


def _to_jsonable(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Decimal):
        # Plain string notation, no exponent.
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be canonically hashed")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _to_jsonable(value.value)
    if isinstance(value, (tuple, list)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_to_jsonable(item) for item in value), key=repr)
    if isinstance(value, dict):
        return {
            str(key): _to_jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    return value


def to_jsonable(value):
    return _to_jsonable(value)


def canonical_json(value) -> str:
    return json.dumps(
        _to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_sha256(value) -> str:
    return sha256_hex(canonical_json(value))


__all__ = ["to_jsonable", "canonical_json", "sha256_hex", "payload_sha256"]
