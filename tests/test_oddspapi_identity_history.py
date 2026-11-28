"""Atomic append-only identity history and reproducible PIT reads."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.live.oddspapi_identity_cache import (
    OddspapiCachedIdentity,
    invalidate_cached_identity,
    load_cached_identity,
    partition_cached_identities,
    upsert_cached_identity,
)
from sports_edge_tennis.live.oddspapi_identity_history import (
    HISTORY_PIT_POLICY_VERSION,
    STATUS_INVALIDATED,
    STATUS_NEVER_RECORDED,
    STATUS_WARM,
    IdentityHistoryUnavailable,
    identity_at_cutoff,
    load_identity_history,
    summarize_identity_availability,
)
from tests.abstention_helpers import insert_match

NOW = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)


def _identity(match_id: int, *, event_id: str = "op-1", verified: datetime = NOW):
    return OddspapiCachedIdentity(
        canonical_match_id=match_id,
        sportradar_event_id="sr-1",
        oddspapi_event_id=event_id,
        betradar_id="b-1",
        oddspapi_tournament_id=47624,
        player_orientation="DIRECT",
        resolution_status="EXACT",
        scheduled_start=NOW + timedelta(hours=2),
        resolved_at=verified,
        last_verified_at=verified,
    )


def test_upsert_records_full_history_row(db):
    match_id = insert_match(db)
    upsert_cached_identity(db, _identity(match_id))

    row = db.execute(
        """
        SELECT canonical_match_id, sportradar_event_id, oddspapi_event_id,
               betradar_id, oddspapi_tournament_id, player_orientation,
               resolution_status, scheduled_start, resolved_at, last_verified_at,
               event_type, recorded_at
        FROM oddspapi_event_identity_history
        """
    ).fetchone()
    assert row[:11] == (
        match_id, "sr-1", "op-1", "b-1", 47624, "DIRECT", "EXACT",
        NOW + timedelta(hours=2), NOW, NOW, "UPSERTED",
    )
    assert row[11] is not None
    assert load_cached_identity(db, match_id=match_id).oddspapi_event_id == "op-1"


def test_invalidation_records_row_and_deletes_cache(db):
    match_id = insert_match(db)
    upsert_cached_identity(db, _identity(match_id))
    invalidate_cached_identity(db, match_id=match_id)

    events = db.execute(
        "SELECT event_type FROM oddspapi_event_identity_history ORDER BY "
        "oddspapi_event_identity_history_id"
    ).fetchall()
    assert events == [("UPSERTED",), ("INVALIDATED",)]
    assert load_cached_identity(db, match_id=match_id) is None


def test_history_and_cache_roll_back_together(db):
    match_id = insert_match(db)
    db.execute(
        """
        CREATE FUNCTION test_reject_identity_cache() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'injected cache failure';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    db.execute(
        "CREATE TRIGGER test_reject_identity_cache BEFORE INSERT OR UPDATE "
        "ON oddspapi_event_identity_cache FOR EACH ROW "
        "EXECUTE FUNCTION test_reject_identity_cache()"
    )
    try:
        with pytest.raises(Exception, match="injected cache failure"):
            upsert_cached_identity(db, _identity(match_id))
    finally:
        db.execute("DROP TRIGGER test_reject_identity_cache "
                   "ON oddspapi_event_identity_cache")
        db.execute("DROP FUNCTION test_reject_identity_cache()")

    # The failed write must not leave a history row claiming an identity.
    assert db.execute(
        "SELECT count(*) FROM oddspapi_event_identity_history"
    ).fetchone()[0] == 0
    assert load_cached_identity(db, match_id=match_id) is None


def test_invalidated_history_rolls_back_with_failed_delete(db):
    match_id = insert_match(db)
    upsert_cached_identity(db, _identity(match_id))
    db.execute(
        """
        CREATE FUNCTION test_reject_identity_delete() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'injected delete failure';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    db.execute(
        "CREATE TRIGGER test_reject_identity_delete BEFORE DELETE "
        "ON oddspapi_event_identity_cache FOR EACH ROW "
        "EXECUTE FUNCTION test_reject_identity_delete()"
    )
    try:
        with pytest.raises(Exception, match="injected delete failure"):
            invalidate_cached_identity(db, match_id=match_id)
    finally:
        db.execute("DROP TRIGGER test_reject_identity_delete "
                   "ON oddspapi_event_identity_cache")
        db.execute("DROP FUNCTION test_reject_identity_delete()")

    assert [r[0] for r in db.execute(
        "SELECT event_type FROM oddspapi_event_identity_history"
    ).fetchall()] == ["UPSERTED"]
    assert load_cached_identity(db, match_id=match_id) is not None


def test_point_in_time_history_is_reproducible(db):
    match_id = insert_match(db)
    upsert_cached_identity(db, _identity(match_id, event_id="op-1"))
    warm_recorded = db.execute(
        "SELECT recorded_at FROM oddspapi_event_identity_history"
    ).fetchone()[0]
    time.sleep(0.02)
    invalidate_cached_identity(db, match_id=match_id)
    invalidated_recorded = db.execute(
        "SELECT max(recorded_at) FROM oddspapi_event_identity_history"
    ).fetchone()[0]
    time.sleep(0.02)
    upsert_cached_identity(
        db, _identity(match_id, event_id="op-2", verified=NOW + timedelta(days=1))
    )

    before = identity_at_cutoff(
        db, match_id=match_id, as_of=warm_recorded - timedelta(seconds=1)
    )
    assert before.status == STATUS_NEVER_RECORDED
    assert before.policy_version == HISTORY_PIT_POLICY_VERSION

    warm = identity_at_cutoff(db, match_id=match_id, as_of=warm_recorded)
    assert warm.status == STATUS_WARM
    assert warm.identity.oddspapi_event_id == "op-1"
    assert warm.identity.sportradar_event_id == "sr-1"

    cold = identity_at_cutoff(db, match_id=match_id, as_of=invalidated_recorded)
    assert cold.status == STATUS_INVALIDATED

    # The later upsert does not rewrite the earlier answers.
    assert identity_at_cutoff(
        db, match_id=match_id, as_of=warm_recorded
    ).identity.oddspapi_event_id == "op-1"
    current = identity_at_cutoff(
        db, match_id=match_id, as_of=datetime.now(UTC)
    )
    assert current.status == STATUS_WARM
    assert current.identity.oddspapi_event_id == "op-2"


def test_history_reads_are_ordered_and_immutable(db):
    match_id = insert_match(db)
    upsert_cached_identity(db, _identity(match_id))
    time.sleep(0.02)
    invalidate_cached_identity(db, match_id=match_id)
    history = load_identity_history(db, match_id=match_id)
    assert [record.event_type for record in history] == ["UPSERTED", "INVALIDATED"]
    assert history[0].recorded_at <= history[1].recorded_at

    for statement in (
        "UPDATE oddspapi_event_identity_history SET event_type = 'UPSERTED'",
        "DELETE FROM oddspapi_event_identity_history",
    ):
        with pytest.raises(psycopg.errors.RaiseException):
            db.execute(statement)

    summary = summarize_identity_availability(
        db, match_ids=[match_id, 999999], as_of=history[0].recorded_at
    )
    assert summary == {
        "fixtures": 2,
        STATUS_WARM: 1,
        STATUS_INVALIDATED: 0,
        STATUS_NEVER_RECORDED: 1,
    }


def test_read_only_partition_never_audits_by_deleting(db):
    match_id = insert_match(db)
    upsert_cached_identity(db, _identity(match_id))
    warm, cold = partition_cached_identities(
        db,
        candidates=[(match_id, "sr-changed", NOW + timedelta(hours=2))],
        now=NOW,
        invalidate_stale=False,
    )
    assert warm == {} and cold == [match_id]
    assert load_cached_identity(db, match_id=match_id) is not None
    assert len(load_identity_history(db, match_id=match_id)) == 1


def test_history_absent_fails_closed_for_evidence_reads(db, monkeypatch):
    import sports_edge_tennis.live.oddspapi_identity_history as history_module

    monkeypatch.setattr(
        history_module, "HISTORY_TABLE", "absent_oddspapi_identity_history"
    )
    with pytest.raises(IdentityHistoryUnavailable):
        identity_at_cutoff(db, match_id=1, as_of=NOW)
    with pytest.raises(IdentityHistoryUnavailable):
        load_identity_history(db, match_id=1)
