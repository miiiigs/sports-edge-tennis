from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.live.oddspapi_identity_cache import (
    OddspapiCachedIdentity,
    identity_invalidation_reason,
    partition_cached_identities,
    upsert_cached_identity,
)

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def _cached(**overrides) -> OddspapiCachedIdentity:
    base = dict(
        canonical_match_id=1,
        sportradar_event_id="sr:sport_event:111",
        oddspapi_event_id="id120",
        betradar_id="111",
        oddspapi_tournament_id=44,
        player_orientation="DIRECT",
        resolution_status="EXACT",
        scheduled_start=NOW + timedelta(hours=2),
        resolved_at=NOW,
        last_verified_at=NOW,
    )
    base.update(overrides)
    return OddspapiCachedIdentity(**base)


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _CacheConn:
    def __init__(self, row=None):
        self.row = row
        self.deleted: list[int] = []
        self.inserts = 0

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split()).lower()
        if normalized.startswith("select canonical_match_id"):
            return _Result(self.row)
        if normalized.startswith("delete from oddspapi_event_identity_cache"):
            self.deleted.append(int((params or (0,))[0]))
            return _Result((None,))
        if normalized.startswith("insert into oddspapi_event_identity_cache"):
            self.inserts += 1
            return _Result((None,))
        raise AssertionError(normalized)


def test_cache_never_stores_market_prices() -> None:
    fields = set(OddspapiCachedIdentity.__dataclass_fields__)
    forbidden = ("price", "decimal", "market", "bookmaker", "quote")
    assert not any(token in name for name in fields for token in forbidden)


def test_fresh_exact_identity_is_valid() -> None:
    assert (
        identity_invalidation_reason(
            _cached(),
            sportradar_event_id="sr:sport_event:111",
            scheduled_start=NOW + timedelta(hours=2),
            now=NOW,
        )
        is None
    )


def test_changed_player_sportradar_event_invalidates() -> None:
    assert (
        identity_invalidation_reason(
            _cached(),
            sportradar_event_id="sr:sport_event:999",
            scheduled_start=NOW + timedelta(hours=2),
            now=NOW,
        )
        == "sportradar_event_changed"
    )


def test_changed_scheduled_start_invalidates() -> None:
    assert (
        identity_invalidation_reason(
            _cached(),
            sportradar_event_id="sr:sport_event:111",
            scheduled_start=NOW + timedelta(hours=5),
            now=NOW,
        )
        == "scheduled_start_changed"
    )


def test_expired_identity_invalidates() -> None:
    assert (
        identity_invalidation_reason(
            _cached(last_verified_at=NOW - timedelta(hours=30)),
            sportradar_event_id="sr:sport_event:111",
            scheduled_start=NOW + timedelta(hours=2),
            now=NOW,
        )
        == "identity_verification_expired"
    )


def test_conflict_resolution_never_valid() -> None:
    assert (
        identity_invalidation_reason(
            _cached(resolution_status="CONFLICT"),
            sportradar_event_id="sr:sport_event:111",
            scheduled_start=NOW + timedelta(hours=2),
            now=NOW,
        )
        == "resolution_not_exact"
    )


def test_upsert_rejects_nonexact_and_missing_provider_id() -> None:
    conn = _CacheConn()
    with pytest.raises(ValueError, match="EXACT"):
        upsert_cached_identity(conn, _cached(resolution_status="CONFLICT"))
    with pytest.raises(ValueError, match="provider event id"):
        upsert_cached_identity(conn, _cached(oddspapi_event_id=""))
    upsert_cached_identity(conn, _cached())
    assert conn.inserts == 1


def test_partition_warm_and_invalidated_cold() -> None:
    warm_row = (
        1,
        "sr:sport_event:111",
        "id120",
        "111",
        44,
        "DIRECT",
        "EXACT",
        NOW + timedelta(hours=2),
        NOW,
        NOW,
    )
    conn = _CacheConn(row=warm_row)
    warm, cold = partition_cached_identities(
        conn,
        candidates=[(1, "sr:sport_event:111", NOW + timedelta(hours=2))],
        now=NOW,
    )
    assert set(warm) == {1}
    assert cold == []

    # Changed Sportradar event invalidates and cold-lists the match.
    conn2 = _CacheConn(row=warm_row)
    warm2, cold2 = partition_cached_identities(
        conn2,
        candidates=[(1, "sr:sport_event:999", NOW + timedelta(hours=2))],
        now=NOW,
    )
    assert warm2 == {}
    assert cold2 == [1]
    assert conn2.deleted == [1]
