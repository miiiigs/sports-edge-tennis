from __future__ import annotations

from datetime import UTC, datetime

import sports_edge_tennis.runner.agent3_live_snapshot as live_snapshot
from sports_edge_tennis.data.agent3_history import (
    Agent3HistoricalMatch,
    build_history_snapshot,
)
from sports_edge_tennis.runner.agent3_live_snapshot import build_live_agent3_history_snapshot


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _match(
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    *,
    observed_at: datetime,
    surface: str | None = "HARD",
    winner_side: int = 1,
) -> Agent3HistoricalMatch:
    return Agent3HistoricalMatch(
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        match_start_at=observed_at,
        surface=surface,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        outcome_status="COMPLETED",
        winner_side=winner_side,
        result_source_observed_at=observed_at,
        result_as_of=observed_at,
        version_source_observed_at=observed_at,
        version_as_of=observed_at,
    )


def _representative_history() -> list[Agent3HistoricalMatch]:
    same_cohort = _utc(2025, 6, 1, 23)
    return [
        # Old evidence must be ignored by temporal weighting during later cohorts
        # and removed from the final four-year live snapshot.
        _match(1, 1, 2, observed_at=_utc(2020, 1, 1, 23), winner_side=1),
        _match(2, 1, 3, observed_at=_utc(2024, 1, 1, 23), winner_side=2),
        _match(3, 2, 3, observed_at=_utc(2024, 2, 1, 23), surface="CLAY", winner_side=1),
        # Same visibility cohort verifies that neither update can leak into the
        # other match's pre-cohort baseline.
        _match(4, 1, 4, observed_at=same_cohort, surface="HARD", winner_side=1),
        _match(5, 1, 5, observed_at=same_cohort, surface="CLAY", winner_side=2),
        _match(6, 4, 5, observed_at=_utc(2025, 7, 1, 23), surface=None, winner_side=1),
        _match(7, 2, 4, observed_at=_utc(2026, 1, 1, 23), surface="HARD", winner_side=2),
    ]


def test_live_snapshot_matches_reference_snapshot_exactly():
    observations = _representative_history()
    cutoff = _utc(2026, 9, 9, 9)

    reference = build_history_snapshot(observations, cutoff=cutoff)
    optimized = build_live_agent3_history_snapshot(observations, cutoff=cutoff)

    assert optimized == reference


def test_live_snapshot_is_deterministic_across_input_order():
    observations = _representative_history()
    cutoff = _utc(2026, 9, 9, 9)

    forward = build_live_agent3_history_snapshot(observations, cutoff=cutoff)
    reversed_snapshot = build_live_agent3_history_snapshot(
        list(reversed(observations)),
        cutoff=cutoff,
    )

    assert forward == reversed_snapshot


def test_live_snapshot_materializes_full_graph_only_once(monkeypatch):
    observations = _representative_history()
    cutoff = _utc(2026, 9, 9, 9)
    calls = 0
    original = live_snapshot.snapshot_history_state

    def counted_snapshot(players, pairs, adjacency):
        nonlocal calls
        calls += 1
        return original(players, pairs, adjacency)

    monkeypatch.setattr(live_snapshot, "snapshot_history_state", counted_snapshot)

    build_live_agent3_history_snapshot(observations, cutoff=cutoff)

    assert calls == 1
