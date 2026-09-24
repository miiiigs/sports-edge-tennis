from __future__ import annotations

import inspect
from datetime import UTC, datetime

from sports_edge_tennis.data.agent2_history import (
    Agent2CanonicalCounterpart,
    Agent2HistoricalMatch,
    Agent2MatchStatRow,
    build_history_snapshot,
    canonicalize_agent2_observations,
    historical_match_is_eligible_for_update,
    normalize_tournament_alias,
)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _stat_row(observation_id: int, player_id: int) -> Agent2MatchStatRow:
    return Agent2MatchStatRow(
        match_stat_observation_id=observation_id,
        player_id=player_id,
        source_system="SPORTRADAR",
        source_observed_at=_utc(2025, 1, 10, 22),
        as_of=_utc(2025, 1, 10, 22),
        first_serve_points_won=20,
        second_serve_points_won=10,
        service_points_played=40,
        return_points_won=15,
    )


def _coherent_stats(base_id: int, player_1_id: int, player_2_id: int) -> tuple[Agent2MatchStatRow, ...]:
    return (_stat_row(base_id, player_1_id), _stat_row(base_id + 1, player_2_id))


def _match(
    *,
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    surface: str | None = "HARD",
    tournament_id: int = 1,
    outcome_status: str = "COMPLETED",
    winner_side: int | None = 1,
    stat_rows: tuple[Agent2MatchStatRow, ...] = (),
) -> Agent2HistoricalMatch:
    return Agent2HistoricalMatch(
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        match_start_at=_utc(2025, 1, 10, 9),
        surface=surface,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        tournament_id=tournament_id,
        canonical_tournament_name="ATP 250 Example",
        normalized_tournament_alias=normalize_tournament_alias("ATP 250 Example"),
        season_year=2025,
        outcome_status=outcome_status,
        winner_side=winner_side,
        result_source_observed_at=_utc(2025, 1, 10, 23),
        result_as_of=_utc(2025, 1, 10, 23),
        version_source_observed_at=_utc(2025, 1, 10, 8),
        version_as_of=_utc(2025, 1, 10, 8),
        stat_rows=stat_rows,
    )


def _counterpart(duplicate_id: int, canonical_id: int, *, surface="HARD", tournament_id=1):
    return Agent2CanonicalCounterpart(
        duplicate_match_id=duplicate_id,
        canonical_match_id=canonical_id,
        surface=surface,
        tournament_id=tournament_id,
        version_source_observed_at=_utc(2025, 1, 10, 8),
        version_as_of=_utc(2025, 1, 10, 8),
    )


CUTOFF = _utc(2025, 2, 1)


def test_original_surface_plus_duplicate_result_stats_becomes_one_usable_match():
    canonical = _match(match_id=100, player_1_id=1, player_2_id=2, surface="HARD", stat_rows=())
    duplicate = _match(
        match_id=200,
        player_1_id=1,
        player_2_id=2,
        surface=None,
        stat_rows=_coherent_stats(10, 1, 2),
    )
    canonicalized = canonicalize_agent2_observations(
        [canonical, duplicate], [_counterpart(200, 100)], cutoff=CUTOFF
    )
    assert len(canonicalized) == 1
    assert canonicalized[0].surface == "HARD"
    assert len(canonicalized[0].stat_rows) == 2
    assert historical_match_is_eligible_for_update(canonicalized[0]) is True

    snapshot = build_history_snapshot(canonicalized, cutoff=CUTOFF)
    assert snapshot.players[1].prior_usable_matches == 1
    assert snapshot.players[2].prior_usable_matches == 1


def test_both_records_eligible_are_counted_once():
    canonical = _match(
        match_id=100, player_1_id=1, player_2_id=2, stat_rows=_coherent_stats(20, 1, 2)
    )
    duplicate = _match(
        match_id=200, player_1_id=1, player_2_id=2, stat_rows=_coherent_stats(30, 1, 2)
    )
    canonicalized = canonicalize_agent2_observations(
        [canonical, duplicate], [_counterpart(200, 100)], cutoff=CUTOFF
    )
    assert len(canonicalized) == 1
    snapshot = build_history_snapshot(canonicalized, cutoff=CUTOFF)
    assert snapshot.players[1].prior_usable_matches == 1
    assert snapshot.players[2].prior_usable_matches == 1


def test_multiple_duplicates_for_one_event_are_counted_once():
    canonical = _match(match_id=100, player_1_id=1, player_2_id=2, surface="HARD", stat_rows=())
    first_duplicate = _match(
        match_id=200,
        player_1_id=1,
        player_2_id=2,
        surface=None,
        stat_rows=_coherent_stats(40, 1, 2),
    )
    replay_duplicate = _match(
        match_id=201,
        player_1_id=1,
        player_2_id=2,
        surface=None,
        stat_rows=_coherent_stats(50, 1, 2),
    )
    canonicalized = canonicalize_agent2_observations(
        [canonical, first_duplicate, replay_duplicate],
        [_counterpart(200, 100), _counterpart(201, 100)],
        cutoff=CUTOFF,
    )
    assert len(canonicalized) == 1
    snapshot = build_history_snapshot(canonicalized, cutoff=CUTOFF)
    assert snapshot.players[1].prior_usable_matches == 1


def test_terminal_truth_conflict_is_not_canonicalized():
    canonical = _match(
        match_id=100,
        player_1_id=1,
        player_2_id=2,
        winner_side=1,
        stat_rows=_coherent_stats(20, 1, 2),
    )
    duplicate = _match(
        match_id=200,
        player_1_id=1,
        player_2_id=2,
        winner_side=2,
        stat_rows=_coherent_stats(30, 1, 2),
    )
    canonicalized = canonicalize_agent2_observations(
        [canonical, duplicate], [_counterpart(200, 100)], cutoff=CUTOFF
    )
    assert len(canonicalized) == 2


def test_tournament_conflict_is_not_canonicalized():
    canonical = _match(
        match_id=100,
        player_1_id=1,
        player_2_id=2,
        tournament_id=1,
        stat_rows=_coherent_stats(20, 1, 2),
    )
    duplicate = _match(
        match_id=200,
        player_1_id=1,
        player_2_id=2,
        tournament_id=2,
        stat_rows=_coherent_stats(30, 1, 2),
    )
    canonicalized = canonicalize_agent2_observations(
        [canonical, duplicate], [_counterpart(200, 100, tournament_id=1)], cutoff=CUTOFF
    )
    assert len(canonicalized) == 2


def test_provider_identity_ambiguity_fails_closed():
    canonical_a = _match(
        match_id=100, player_1_id=1, player_2_id=2, tournament_id=1, stat_rows=_coherent_stats(20, 1, 2)
    )
    canonical_b = _match(
        match_id=101, player_1_id=1, player_2_id=2, tournament_id=1, stat_rows=_coherent_stats(22, 1, 2)
    )
    duplicate = _match(
        match_id=200, player_1_id=1, player_2_id=2, tournament_id=1, stat_rows=_coherent_stats(30, 1, 2)
    )
    canonicalized = canonicalize_agent2_observations(
        [canonical_a, canonical_b, duplicate],
        [_counterpart(200, 100), _counterpart(200, 101)],
        cutoff=CUTOFF,
    )
    assert len(canonicalized) == 3


def test_point_in_time_cutoff_blocks_counterpart_surface():
    canonical = _match(match_id=100, player_1_id=1, player_2_id=2, surface="HARD", stat_rows=())
    duplicate = _match(
        match_id=200,
        player_1_id=1,
        player_2_id=2,
        surface=None,
        stat_rows=_coherent_stats(10, 1, 2),
    )
    counterpart = Agent2CanonicalCounterpart(
        duplicate_match_id=200,
        canonical_match_id=100,
        surface="HARD",
        tournament_id=1,
        version_source_observed_at=_utc(2025, 3, 1),
        version_as_of=_utc(2025, 3, 1),
    )
    canonicalized = canonicalize_agent2_observations(
        [canonical, duplicate], [counterpart], cutoff=CUTOFF
    )
    duplicate_after = next(item for item in canonicalized if item.match_id == 200)
    assert duplicate_after.surface is None
    assert historical_match_is_eligible_for_update(duplicate_after) is False


def test_normal_non_duplicate_match_is_unchanged():
    observation = _match(
        match_id=100, player_1_id=1, player_2_id=2, stat_rows=_coherent_stats(20, 1, 2)
    )
    canonicalized = canonicalize_agent2_observations([observation], [], cutoff=CUTOFF)
    assert canonicalized == [observation]


def test_agent1_and_agent3_histories_are_not_changed():
    from sports_edge_tennis.data import agent1_history, agent3_history

    for module in (agent1_history, agent3_history):
        source = inspect.getsource(module)
        assert "canonicalize_agent2_observations" not in source
        assert "Agent2CanonicalCounterpart" not in source
