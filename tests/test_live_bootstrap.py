from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from sports_edge_tennis.live.bootstrap import (
    BookmakerMappingConflictError,
    ensure_pinnapi_bookmaker_mapping,
)
from sports_edge_tennis.odds.persistence import insert_bookmaker_mapping


FIXED = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)


def _count(conn: psycopg.Connection) -> int:
    return conn.execute(
        "SELECT count(*) FROM provider_bookmaker_mappings "
        "WHERE provider_code = 'PINNAPI' AND provider_bookmaker_key = 'pinnacle'"
    ).fetchone()[0]


def _effective_mapping(conn: psycopg.Connection):
    return conn.execute(
        "SELECT bookmaker_code, normalization_class::text, mapping_version, effective_from "
        "FROM provider_bookmaker_mappings "
        "WHERE provider_code = 'PINNAPI' AND provider_bookmaker_key = 'pinnacle' "
        "ORDER BY effective_from DESC, provider_bookmaker_mapping_id DESC LIMIT 1"
    ).fetchone()


def test_bootstrap_inserts_once(db: psycopg.Connection):
    mapping_id = ensure_pinnapi_bookmaker_mapping(db, clock=lambda: FIXED)
    assert mapping_id is not None
    assert _count(db) == 1
    bookmaker_code, normalization_class, version, effective_from = _effective_mapping(db)
    assert bookmaker_code == "PINNACLE"
    assert normalization_class == "SHARP"
    assert version == 1
    assert effective_from == FIXED


def test_bootstrap_is_idempotent(db: psycopg.Connection):
    first = ensure_pinnapi_bookmaker_mapping(db, clock=lambda: FIXED)
    second = ensure_pinnapi_bookmaker_mapping(db, clock=lambda: FIXED)
    assert first == second
    assert _count(db) == 1


def test_bootstrap_reuses_existing_equivalent_mapping(db: psycopg.Connection):
    seeded = insert_bookmaker_mapping(
        db,
        provider_code="PINNAPI",
        provider_bookmaker_key="pinnacle",
        mapping_version=1,
        bookmaker_code="PINNACLE",
        normalization_class="SHARP",
        effective_from=FIXED,
    )
    resolved = ensure_pinnapi_bookmaker_mapping(db, clock=lambda: FIXED)
    assert resolved == seeded
    assert _count(db) == 1


def test_bootstrap_contradictory_mapping_fails_closed(db: psycopg.Connection):
    insert_bookmaker_mapping(
        db,
        provider_code="PINNAPI",
        provider_bookmaker_key="pinnacle",
        mapping_version=1,
        bookmaker_code="DRAFTKINGS",
        normalization_class="OFFERED",
        effective_from=FIXED,
    )
    with pytest.raises(BookmakerMappingConflictError):
        ensure_pinnapi_bookmaker_mapping(db, clock=lambda: FIXED)
    assert _count(db) == 1
