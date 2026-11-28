"""Pure lifecycle, capture-health and population reconciliation regressions."""
from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.validation.fixture_lifecycle import (
    ABSENT_COMPLETE_SNAPSHOT,
    CANCELLED,
    CREATED_OR_NEWLY_OBSERVED,
    EXPLICIT_REMOVAL,
    MISSING_VERSION,
    POSTPONED,
    RESCHEDULED,
    UNCHANGED,
    UNKNOWN_INCOMPLETE_SNAPSHOT,
    UPDATED,
    FixtureSnapshot,
    MatchVersionObservation,
    build_fixture_timeline,
    detect_capture_gaps,
    reconcile_selection_populations,
    reconcile_snapshot_changes,
    select_prematch_targets,
)

START = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)


def version(
    version_id=1,
    *,
    start=START,
    status="SCHEDULED",
    observed=START - timedelta(hours=2),
    ingested=START - timedelta(hours=2),
    **overrides,
):
    return MatchVersionObservation(
        match_version_id=version_id,
        match_start_at=start,
        match_status=status,
        source_observed_at=observed,
        as_of=observed,
        ingested_at=ingested,
        **overrides,
    )


def test_missing_fixture_is_explicit_and_not_void():
    timeline = build_fixture_timeline(1, [])
    assert timeline.classification == MISSING_VERSION
    assert timeline.version_count == 0
    assert timeline.explicitly_removed is False


def test_unchanged_fixture_preserves_original_start():
    timeline = build_fixture_timeline(1, [version(1), version(2, observed=START - timedelta(hours=1))])
    assert timeline.classification == UNCHANGED
    assert timeline.original_scheduled_start == START
    assert timeline.latest_scheduled_start == START
    assert timeline.start_change_count == 0


def test_rescheduled_fixture_keeps_both_clocks_and_change_history():
    new_start = START + timedelta(hours=3)
    timeline = build_fixture_timeline(
        1,
        [
            version(1),
            version(2, start=new_start, observed=START - timedelta(hours=1)),
        ],
    )
    assert timeline.classification == RESCHEDULED
    assert timeline.original_scheduled_start == START
    assert timeline.latest_scheduled_start == new_start
    assert timeline.start_change_count == 1
    assert timeline.changes[0].from_start == START
    assert timeline.changes[0].to_start == new_start
    assert timeline.changes[0].observed_at == START - timedelta(hours=1)


def test_updated_fixture_is_not_mistaken_for_reschedule():
    timeline = build_fixture_timeline(
        1,
        [
            version(1, round_code="QF", best_of_sets=3),
            version(2, round_code="R16", best_of_sets=5, observed=START - timedelta(hours=1)),
        ],
    )
    assert timeline.classification == UPDATED
    assert timeline.start_change_count == 0


def test_status_change_is_traceable_even_without_a_reschedule():
    timeline = build_fixture_timeline(
        1,
        [
            version(1, status="SCHEDULED"),
            version(2, status="POSTPONED", observed=START - timedelta(hours=1)),
        ],
    )
    assert timeline.classification == POSTPONED
    assert timeline.start_change_count == 0
    assert timeline.status_change_count == 1


def test_postponed_and_cancelled_are_explicit_not_void():
    postponed = build_fixture_timeline(
        1, [version(1), version(2, status="POSTPONED", observed=START - timedelta(hours=1))]
    )
    cancelled = build_fixture_timeline(
        1, [version(1), version(2, status="CANCELLED", observed=START - timedelta(hours=1))]
    )
    assert postponed.classification == POSTPONED
    assert cancelled.classification == CANCELLED
    assert postponed.explicitly_removed is False
    assert cancelled.explicitly_removed is False


def test_explicit_removal_requires_authorising_flag():
    timeline = build_fixture_timeline(1, [version(1)], explicitly_removed=True)
    assert timeline.classification == EXPLICIT_REMOVAL
    assert timeline.original_scheduled_start == START


def test_retrospective_discovery_is_not_pit_visible():
    # The 53-fixture shape: first seen after the match had already started and
    # already carrying a terminal result.
    observed = START + timedelta(days=3)
    timeline = build_fixture_timeline(
        1,
        [
            version(
                1,
                status="COMPLETED",
                observed=observed,
                ingested=observed + timedelta(seconds=2),
            )
        ],
    )
    assert timeline.retrospective_discovery is True
    assert timeline.pit_visible_by_original_deadline is False
    assert timeline.classification == UNCHANGED


def test_reschedule_cannot_retroactively_create_a_pit_version():
    observed = START + timedelta(hours=1)
    timeline = build_fixture_timeline(
        1,
        [
            version(1, start=START, observed=observed, ingested=observed),
            version(
                2,
                start=START + timedelta(days=1),
                observed=observed + timedelta(minutes=1),
                ingested=observed + timedelta(minutes=1),
            ),
        ],
    )
    assert timeline.classification == RESCHEDULED
    assert timeline.pit_visible_by_original_deadline is False
    assert timeline.retrospective_discovery is True


def test_snapshot_reconciliation_covers_missing_updated_removed_postponed_rescheduled():
    previous = {
        "unchanged": FixtureSnapshot(START.isoformat()),
        "rescheduled": FixtureSnapshot(START.isoformat()),
        "updated": FixtureSnapshot(START.isoformat(), "SCHEDULED"),
        "missing": FixtureSnapshot(START.isoformat()),
        "removed": FixtureSnapshot(START.isoformat()),
        "postponed": FixtureSnapshot(START.isoformat()),
    }
    current = {
        "unchanged": FixtureSnapshot(START.isoformat()),
        "rescheduled": FixtureSnapshot((START + timedelta(hours=2)).isoformat()),
        "updated": FixtureSnapshot(START.isoformat(), "COMPLETED"),
        "new": FixtureSnapshot((START + timedelta(days=1)).isoformat()),
    }
    rows = {
        row["source_match_id"]: row
        for row in reconcile_snapshot_changes(
            previous,
            current,
            current_complete=False,
            explicit_removed=["removed"],
            explicit_postponed=["postponed"],
        )
    }
    assert rows["unchanged"]["status"] == UNCHANGED
    assert rows["rescheduled"]["status"] == RESCHEDULED
    assert rows["rescheduled"]["previous_start"] == START.isoformat()
    assert rows["updated"]["status"] == UPDATED
    assert rows["missing"]["status"] == UNKNOWN_INCOMPLETE_SNAPSHOT
    assert rows["removed"]["status"] == EXPLICIT_REMOVAL
    assert rows["postponed"]["status"] == POSTPONED
    assert rows["new"]["status"] == CREATED_OR_NEWLY_OBSERVED
    assert not any(row["void_inferred"] for row in rows.values())


def test_complete_snapshot_absence_is_still_not_a_void():
    rows = reconcile_snapshot_changes(
        {"a": FixtureSnapshot(START.isoformat())}, {}, current_complete=True
    )
    assert rows[0]["status"] == ABSENT_COMPLETE_SNAPSHOT
    assert rows[0]["void_inferred"] is False


def test_removal_and_postponement_feeds_cannot_contradict_snapshot():
    with pytest.raises(ValueError, match="contradictory"):
        reconcile_snapshot_changes(
            {"a": FixtureSnapshot(START.isoformat())},
            {"a": FixtureSnapshot(START.isoformat())},
            explicit_removed=["a"],
        )
    with pytest.raises(ValueError, match="contradictory"):
        reconcile_snapshot_changes(
            {"a": FixtureSnapshot(START.isoformat())},
            {"a": FixtureSnapshot(START.isoformat())},
            explicit_postponed=["a"],
        )


class _Match:
    def __init__(self, match_id, start, status="SCHEDULED"):
        self.match_id = match_id
        self.match_start_at = start
        self.match_status = status


def test_prematch_target_selection_fails_closed():
    as_of = START - timedelta(hours=1)
    matches = [
        _Match(1, START + timedelta(hours=1)),
        _Match(2, START - timedelta(minutes=40)),
        _Match(3, None),
        _Match(4, START + timedelta(hours=1), "COMPLETED"),
    ]
    eligible, excluded = select_prematch_targets(matches, as_of=as_of)
    assert eligible == [1]
    reasons = {row["match_id"]: row["reason"] for row in excluded}
    assert reasons[2] == "START_INSIDE_OR_AFTER_DEADLINE"
    assert reasons[3] == "MISSING_SCHEDULED_START"
    assert reasons[4] == "NOT_SCHEDULED"


def test_recovery_after_outage_never_backdates_a_started_fixture():
    """A runner restart may only capture fixtures still outside the deadline."""
    recovery_as_of = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
    matches = [
        # Scheduled during the outage and already started when the runner returns.
        _Match(10, recovery_as_of - timedelta(hours=5)),
        # Started just inside the 30-minute deadline.
        _Match(11, recovery_as_of + timedelta(minutes=20)),
        # Still comfortably in the future: capture once, normally.
        _Match(12, recovery_as_of + timedelta(hours=3)),
    ]
    eligible, excluded = select_prematch_targets(matches, as_of=recovery_as_of)
    assert eligible == [12]
    reasons = {row["match_id"]: row["reason"] for row in excluded}
    assert reasons[10] == "START_INSIDE_OR_AFTER_DEADLINE"
    assert reasons[11] == "START_INSIDE_OR_AFTER_DEADLINE"
    # No exclusion is converted into a retrospective target or a void.
    assert all(row["reason"] != "VOID" for row in excluded)


def test_capture_gap_detects_silent_scheduler_stop():
    heartbeats = [
        datetime(2026, 11, 11, 0, tzinfo=UTC),
        datetime(2026, 11, 12, 0, tzinfo=UTC),
        datetime(2026, 11, 16, 23, tzinfo=UTC),
        datetime(2026, 11, 17, 0, tzinfo=UTC),
    ]
    health = detect_capture_gaps(
        heartbeats,
        window_start=datetime(2026, 11, 11, 0, tzinfo=UTC),
        window_end=datetime(2026, 11, 17, 1, tzinfo=UTC),
        expected_interval=timedelta(hours=1),
    )
    assert health.status == "GAP"
    assert any(gap["from"].startswith("2026-11-12") for gap in health.gaps)
    assert max(gap["missing_expected_intervals"] for gap in health.gaps) >= 96


def test_capture_gap_detects_no_heartbeat_and_stale_tail():
    window_start = datetime(2026, 11, 11, tzinfo=UTC)
    window_end = datetime(2026, 11, 12, tzinfo=UTC)
    empty = detect_capture_gaps(
        [], window_start=window_start, window_end=window_end,
        expected_interval=timedelta(hours=1),
    )
    assert empty.status == "NO_HEARTBEAT"
    stale = detect_capture_gaps(
        [window_start], window_start=window_start, window_end=window_end,
        expected_interval=timedelta(hours=1),
    )
    assert stale.status == "GAP"
    assert stale.stale_seconds == 86400


def test_healthy_capture_cadence_is_not_flagged():
    heartbeats = [
        datetime(2026, 11, 11, h, tzinfo=UTC) for h in range(4)
    ]
    health = detect_capture_gaps(
        heartbeats,
        window_start=datetime(2026, 11, 11, 0, tzinfo=UTC),
        window_end=datetime(2026, 11, 11, 3, tzinfo=UTC),
        expected_interval=timedelta(hours=1),
    )
    assert health.status == "HEALTHY"
    assert health.gaps == ()


def test_population_reconciliation_keeps_sets_distinct():
    result = reconcile_selection_populations(
        manager_consensus={1, 2, 3},
        tracking_only={3, 4},
        published_model_picks={2, 3, 4},
        settled={2, 3, 4, 9},
    )
    assert result["manager_consensus"] == 3
    assert result["tracking_only"] == 2
    assert result["published_model_picks"] == 3
    assert result["settled"] == 4
    assert result["tracking_only_outside_published"] == []
    assert result["published_outside_manager_consensus"] == [4]
    assert result["consensus_not_published"] == [1]
    assert result["published_not_settled"] == []
    assert result["settled_without_selection"] == [9]
    assert result["populations_are_equivalent"] is False
    assert result["void_or_cancellation_inferred"] is False
