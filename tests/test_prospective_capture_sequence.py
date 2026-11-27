"""Pre-cutoff identity/price sequence classification and read-only audit."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sports_edge_tennis.live.oddspapi_identity_cache import (
    OddspapiCachedIdentity,
    upsert_cached_identity,
)
from sports_edge_tennis.validation.prospective_capture_sequence import (
    STATUS_COMPLETE,
    CaptureTimestamps,
    assess_capture,
    audit_run_capture,
)
from tests.abstention_helpers import (
    A1,
    A2,
    A3,
    finalize_matrix,
    insert_match,
    insert_run,
    insert_target,
    make_result,
    seed_agent_configs,
    utc,
)

CUTOFF = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


def _timestamps(**overrides) -> CaptureTimestamps:
    base = dict(
        match_id=1,
        prediction_as_of=CUTOFF,
        prediction_locked_at=CUTOFF + timedelta(minutes=5),
        identity_recorded_at=CUTOFF - timedelta(minutes=2),
        first_price_observed_at=CUTOFF - timedelta(minutes=1),
        first_price_ingested_at=CUTOFF - timedelta(seconds=30),
    )
    base.update(overrides)
    return CaptureTimestamps(**base)


def test_complete_sequence_is_safe():
    assessment = assess_capture(_timestamps())
    assert assessment.status == STATUS_COMPLETE
    assert assessment.safe is True


def test_identity_after_cutoff_is_not_promoted():
    assessment = assess_capture(
        _timestamps(identity_recorded_at=CUTOFF + timedelta(seconds=47))
    )
    assert assessment.status == "IDENTITY_LATE+PRICE_PRE_CUTOFF"
    assert assessment.identity_pre_cutoff is False
    assert assessment.safe is False


def test_price_observed_before_cutoff_but_ingested_after_is_late():
    assessment = assess_capture(
        _timestamps(first_price_ingested_at=CUTOFF + timedelta(seconds=1))
    )
    assert assessment.status == "IDENTITY_PRE_CUTOFF+PRICE_LATE"
    assert assessment.price_pre_cutoff is False


def test_missing_evidence_is_distinct_from_late_evidence():
    assert assess_capture(
        _timestamps(identity_recorded_at=None, first_price_observed_at=None,
                    first_price_ingested_at=None)
    ).status == "IDENTITY_MISSING+PRICE_MISSING"
    assert assess_capture(
        _timestamps(identity_recorded_at=None)
    ).status == "IDENTITY_MISSING+PRICE_PRE_CUTOFF"


def test_run_audit_reports_complete_pre_cutoff_capture(db):
    """Isolated rehearsal of the safe order: identity -> price -> lock."""
    match_id = insert_match(db)
    run_id = insert_run(db, as_of=utc(minute=10))
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    # Record the archived identity row with an explicit pre-cutoff timestamp.
    # The live writer stamps recorded_at at transaction time; this rehearsal
    # pins the archived clock so the classification is testable.
    db.execute(
        """
        INSERT INTO oddspapi_event_identity_history (
            canonical_match_id, sportradar_event_id, oddspapi_event_id,
            betradar_id, oddspapi_tournament_id, player_orientation,
            resolution_status, scheduled_start, resolved_at, last_verified_at,
            event_type, recorded_at
        )
        VALUES (%s, NULL, 'op-early', NULL, 47624, 'DIRECT', 'EXACT',
                %s, %s, %s, 'UPSERTED', %s)
        """,
        (match_id, utc(hour=12), utc(minute=1), utc(minute=1), utc(minute=5)),
    )
    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type,
            selection_type, decimal_price, source_snapshot_id,
            source_observed_at, as_of, ingested_at
        )
        VALUES (%s, 'ODDSPAPI', 'pinnacle+30', 'MATCH_WINNER', 'PLAYER_1',
                1.85, 'snap-early', %s, %s, %s)
        """,
        (match_id, utc(), utc(), utc()),
    )
    finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=[
            make_result(code, utc(minute=10), status="PREDICTED", side=1,
                        probability="0.6")
            for code in (A1, A2, A3)
        ],
        finalized_at=utc(minute=15),
    )

    report = audit_run_capture(db, prediction_run_id=run_id)
    assert report["fixtures"] == 1
    assert report["safe_fixtures"] == 1
    assert report["status_counts"] == {STATUS_COMPLETE: 1}
    assert report["database_writes"] is False
    assert report["provider_calls"] == 0


def test_run_audit_reports_late_same_cycle_capture(db):
    match_id = insert_match(db)
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=[
            make_result(code, utc(), status="PREDICTED", side=1, probability="0.6")
            for code in (A1, A2, A3)
        ],
        finalized_at=utc(minute=5),
    )
    # Identity and price are recorded now, i.e. after the September cutoff.
    upsert_cached_identity(
        db,
        OddspapiCachedIdentity(
            canonical_match_id=match_id,
            sportradar_event_id=None,
            oddspapi_event_id="op-1",
            betradar_id=None,
            oddspapi_tournament_id=47624,
            player_orientation="DIRECT",
            resolution_status="EXACT",
            scheduled_start=utc(hour=12),
            resolved_at=utc(),
            last_verified_at=utc(),
        ),
    )
    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type,
            selection_type, decimal_price, source_snapshot_id,
            source_observed_at, as_of
        )
        VALUES (%s, 'ODDSPAPI', 'pinnacle+30', 'MATCH_WINNER', 'PLAYER_1',
                1.85, 'snap-1', %s, %s)
        """,
        (match_id, utc(), utc()),
    )

    report = audit_run_capture(db, prediction_run_id=run_id)
    assert report["fixtures"] == 1
    assert report["safe_fixtures"] == 0
    assert report["database_writes"] is False
    assert report["fixtures_detail"][0]["match_id"] == match_id
    assert report["status_counts"] == {"IDENTITY_LATE+PRICE_LATE": 1}
