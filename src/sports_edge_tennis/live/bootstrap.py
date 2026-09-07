"""Live provider reference-data bootstrap helpers.

Reference/bootstrap data is not schema; it is seeded at runtime via small
idempotent helpers using the frozen mapping registry. The PINNAPI adapter must
resolve to SHARP/PINNACLE through ``provider_bookmaker_mappings`` and must never
bypass the registry.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.odds.providers.pinnapi import (
    PINNAPI_BOOKMAKER_KEY,
    PINNAPI_PROVIDER_CODE,
)

PINNAPI_BOOKMAKER_CODE = "PINNACLE"
PINNAPI_NORMALIZATION_CLASS = "SHARP"
PINNAPI_MAPPING_VERSION = 1


class BookmakerMappingConflictError(RuntimeError):
    """Raised when an existing PINNAPI mapping contradicts SHARP/PINNACLE."""


def ensure_pinnapi_bookmaker_mapping(
    conn: psycopg.Connection,
    *,
    clock: callable | None = None,
) -> int:
    """Ensure the PINNAPI -> SHARP/PINNACLE mapping exists (idempotent).

    Returns the ``provider_bookmaker_mapping_id``. A first call inserts the
    mapping; a later call reuses the existing equivalent mapping. If an existing
    mapping for PINNAPI/pinnacle contradicts SHARP/PINNACLE it fails closed.
    """
    clock = clock or (lambda: datetime.now(timezone.utc))

    existing = _select_existing(conn)
    if existing is not None:
        return _verify(existing)

    effective_from = clock()
    try:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO provider_bookmaker_mappings (
                    provider_code, provider_bookmaker_key, mapping_version,
                    bookmaker_code, normalization_class, effective_from
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    PINNAPI_PROVIDER_CODE,
                    PINNAPI_BOOKMAKER_KEY,
                    PINNAPI_MAPPING_VERSION,
                    PINNAPI_BOOKMAKER_CODE,
                    PINNAPI_NORMALIZATION_CLASS,
                    effective_from,
                ),
            )
    except psycopg.errors.RaiseException as exc:
        if "must move forward" not in str(exc):
            raise
        # A concurrent bootstrap inserted first; reuse the now-existing mapping.
        existing = _select_existing(conn)
        if existing is not None:
            return _verify(existing)
        raise

    return _select_id(conn)


def _select_existing(conn: psycopg.Connection):
    return conn.execute(
        """
        SELECT provider_bookmaker_mapping_id, bookmaker_code,
               normalization_class::text, mapping_version, effective_from
        FROM provider_bookmaker_mappings
        WHERE provider_code = %s AND provider_bookmaker_key = %s
        ORDER BY effective_from DESC, provider_bookmaker_mapping_id DESC
        LIMIT 1
        """,
        (PINNAPI_PROVIDER_CODE, PINNAPI_BOOKMAKER_KEY),
    ).fetchone()


def _verify(existing) -> int:
    mapping_id, bookmaker_code, normalization_class, _version, _effective_from = existing
    if (
        bookmaker_code != PINNAPI_BOOKMAKER_CODE
        or normalization_class != PINNAPI_NORMALIZATION_CLASS
    ):
        raise BookmakerMappingConflictError(
            "existing PINNAPI/pinnacle mapping contradicts SHARP/PINNACLE "
            f"(bookmaker_code={bookmaker_code}, normalization_class={normalization_class})"
        )
    return mapping_id


def _select_id(conn: psycopg.Connection) -> int:
    row = conn.execute(
        """
        SELECT provider_bookmaker_mapping_id
        FROM provider_bookmaker_mappings
        WHERE provider_code = %s AND provider_bookmaker_key = %s
        ORDER BY effective_from DESC, provider_bookmaker_mapping_id DESC
        LIMIT 1
        """,
        (PINNAPI_PROVIDER_CODE, PINNAPI_BOOKMAKER_KEY),
    ).fetchone()
    if row is None:
        raise RuntimeError("failed to resolve inserted PINNAPI bookmaker mapping")
    return row[0]
