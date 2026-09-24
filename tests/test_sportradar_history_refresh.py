from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg
import pytest

from sports_edge_tennis.live import sportradar_history_refresh as refresh_module
from sports_edge_tennis.live.sportradar_fixtures import parse_summary
from sports_edge_tennis.live.sportradar_history_refresh import (
    ParsedResult,
    _orient_result_to_canonical,
    _parse_final_stats,
    _parse_terminal_result,
    _terminal_result_conflict,
    _validate_sportradar_player_map,
    refresh_sportradar_history_day,
)
from sports_edge_tennis.live.sportradar_ingest import (
    SportradarIngestedMatch,
    SportradarIngestOutcome,
)


RECEIVED = datetime(2026, 9, 19, 18, 0, tzinfo=UTC)


def _summary(
    *,
    status="closed",
    winner_id="sr:p1",
    winning_reason="played",
    period_scores=None,
    statistics=None,
):
    row = {
        "sport_event": {
            "id": "sr:event:1",
            "start_time": "2026-09-19T15:00:00Z",
            "sport_event_context": {
                "category": {"name": "Challenger"},
                "competition": {
                    "id": "sr:competition:1",
                    "name": "ATP Challenger Rennes Men Singles",
                    "level": "challenger",
                    "type": "singles",
                    "gender": "men",
                },
                "season": {"id": "sr:season:2026"},
                "mode": {"best_of": 3},
                "round": {"name": "round_of_32"},
            },
            "competitors": [
                {
                    "id": "sr:p1",
                    "name": "Player, One",
                    "qualifier": "home",
                },
                {
                    "id": "sr:p2",
                    "name": "Player, Two",
                    "qualifier": "away",
                },
            ],
        },
        "sport_event_status": {
            "status": status,
            "winner_id": winner_id,
            "winning_reason": winning_reason,
        },
    }
    if period_scores is not None:
        row["sport_event_status"]["period_scores"] = period_scores
    if statistics is not None:
        row["statistics"] = statistics
    return row


def _periods():
    return [
        {"type": "set", "home_score": 6, "away_score": 4},
        {"type": "set", "home_score": 3, "away_score": 6},
        {"type": "set", "home_score": 7, "away_score": 5},
    ]


def _stats():
    return {
        "totals": {
            "competitors": [
                {
                    "id": "sr:p1",
                    "statistics": {
                        "first_serve_points_won": 31,
                        "second_serve_points_won": 13,
                        "service_points_won": 44,
                        "service_points_lost": 28,
                        "first_serve_successful": 48,
                        "aces": 8,
                        "double_faults": 3,
                        "total_breakpoints": 4,
                        "breakpoints_won": 1,
                    },
                },
                {
                    "id": "sr:p2",
                    "statistics": {
                        "first_serve_points_won": 29,
                        "second_serve_points_won": 12,
                        "service_points_won": 41,
                        "service_points_lost": 31,
                        "first_serve_successful": 45,
                        "aces": 5,
                        "double_faults": 4,
                        "total_breakpoints": 6,
                        "breakpoints_won": 2,
                    },
                },
            ]
        }
    }


def test_completed_result_keeps_retired_side_null_and_parses_period_totals() -> None:
    summary = _summary(period_scores=_periods())
    fixture = parse_summary(summary, received_at=RECEIVED)

    result = _parse_terminal_result(summary, fixture=fixture)

    assert result.outcome_status == "COMPLETED"
    assert result.winner_side == 1
    assert result.retired_side is None
    assert result.sets_won_player_1 == 2
    assert result.sets_won_player_2 == 1
    assert result.games_won_player_1 == 16
    assert result.games_won_player_2 == 15


def test_retirement_sets_only_losing_side_as_retired() -> None:
    summary = _summary(
        winner_id="sr:p2",
        winning_reason="retirement",
        period_scores=_periods(),
    )
    fixture = parse_summary(summary, received_at=RECEIVED)

    result = _parse_terminal_result(summary, fixture=fixture)

    assert result.outcome_status == "RETIRED"
    assert result.winner_side == 2
    assert result.retired_side == 1


def test_retirement_uses_detailed_match_status_when_winning_reason_missing() -> None:
    summary = _summary(
        status="ended",
        winner_id="sr:p2",
        winning_reason="",
        period_scores=_periods(),
    )
    summary["sport_event_status"]["match_status"] = "retired"
    fixture = parse_summary(summary, received_at=RECEIVED)

    result = _parse_terminal_result(summary, fixture=fixture)

    assert fixture.match_status == "RETIRED"
    assert result.outcome_status == "RETIRED"
    assert result.winner_side == 2
    assert result.retired_side == 1


def test_walkover_remains_terminal_but_has_no_retired_side() -> None:
    summary = _summary(
        winner_id="sr:p1",
        winning_reason="walkover",
    )
    fixture = parse_summary(summary, received_at=RECEIVED)

    result = _parse_terminal_result(summary, fixture=fixture)

    assert result.outcome_status == "WALKOVER"
    assert result.winner_side == 1
    assert result.retired_side is None


def test_cancelled_result_does_not_require_winner_id() -> None:
    summary = _summary(
        status="cancelled",
        winner_id="",
        winning_reason="",
    )
    fixture = parse_summary(summary, received_at=RECEIVED)

    result = _parse_terminal_result(summary, fixture=fixture)

    assert result.outcome_status == "CANCELLED"
    assert result.winner_side is None
    assert result.retired_side is None


def test_terminal_winner_must_match_exact_sportradar_participant() -> None:
    summary = _summary(winner_id="sr:someone-else")
    fixture = parse_summary(summary, received_at=RECEIVED)

    with pytest.raises(ValueError, match="terminal_winner_id_unresolved"):
        _parse_terminal_result(summary, fixture=fixture)


def test_final_stats_translate_sportradar_core_fields_for_agent2() -> None:
    summary = _summary(statistics=_stats())
    fixture = parse_summary(summary, received_at=RECEIVED)

    p1, p2 = _parse_final_stats(summary, fixture=fixture)

    assert p1.provider_player_id == "sr:p1"
    assert p1.line.first_serve_points_won == 31
    assert p1.line.second_serve_points_won == 13
    assert p1.line.service_points_played == 72
    assert p1.line.return_points_won == 31
    assert p1.line.first_serves_in == 48
    assert p1.line.aces == 8
    assert p1.line.double_faults == 3
    # P1 faced P2's break-point opportunities; P2 converted 2/6.
    assert p1.line.break_points_faced == 6
    assert p1.line.break_points_saved == 4

    assert p2.line.service_points_played == 72
    assert p2.line.return_points_won == 28
    assert p1.record_hash != p2.record_hash


def test_final_stats_reject_service_point_incoherence() -> None:
    statistics = _stats()
    statistics["totals"]["competitors"][0]["statistics"]["service_points_won"] = 45
    summary = _summary(statistics=statistics)
    fixture = parse_summary(summary, received_at=RECEIVED)

    with pytest.raises(ValueError, match="service_points_incoherent"):
        _parse_final_stats(summary, fixture=fixture)


def test_final_stats_reject_wrong_competitor_pair() -> None:
    statistics = _stats()
    statistics["totals"]["competitors"][1]["id"] = "sr:wrong"
    summary = _summary(statistics=statistics)
    fixture = parse_summary(summary, received_at=RECEIVED)

    with pytest.raises(ValueError, match="identity_mismatch"):
        _parse_final_stats(summary, fixture=fixture)


def test_retirement_prefers_provider_set_tally_over_unfinished_period_score() -> None:
    summary = _summary(
        winner_id="sr:p1",
        winning_reason="retirement",
        period_scores=[
            {"type": "regular_period", "home_score": 6, "away_score": 4},
            {"type": "regular_period", "home_score": 2, "away_score": 4},
        ],
    )
    summary["sport_event_status"]["home_score"] = 1
    summary["sport_event_status"]["away_score"] = 0
    fixture = parse_summary(summary, received_at=RECEIVED)

    result = _parse_terminal_result(summary, fixture=fixture)

    assert result.outcome_status == "RETIRED"
    assert result.sets_won_player_1 == 1
    assert result.sets_won_player_2 == 0
    assert result.games_won_player_1 == 8
    assert result.games_won_player_2 == 8


def test_sportradar_stat_player_bridge_requires_exact_canonical_participants() -> None:
    assert _validate_sportradar_player_map(
        {"sr:p1": 101, "sr:p2": 202},
        expected_player_ids=(101, 202),
    ) == {"sr:p1": 101, "sr:p2": 202}

    with pytest.raises(ValueError, match="player_bridge_mismatch"):
        _validate_sportradar_player_map(
            {"sr:p1": 101, "sr:p2": 999},
            expected_player_ids=(101, 202),
        )


def test_sportradar_stat_player_bridge_rejects_incomplete_resolution() -> None:
    with pytest.raises(ValueError, match="player_bridge_incomplete"):
        _validate_sportradar_player_map(
            {"sr:p1": 101},
            expected_player_ids=(101, 202),
        )

def test_terminal_result_conflict_blocks_status_reinterpretation() -> None:
    candidate = ParsedResult(
        outcome_status="COMPLETED",
        winner_side=1,
        retired_side=None,
        sets_won_player_1=1,
        sets_won_player_2=0,
        games_won_player_1=None,
        games_won_player_2=None,
        scoreline_text="1-0",
    )
    current = ("RETIRED", 1, 2, 1, 0, None, None)
    assert (
        _terminal_result_conflict(current, candidate)
        == "terminal_outcome_conflict:RETIRED:COMPLETED"
    )


def test_terminal_result_conflict_blocks_winner_flip() -> None:
    candidate = ParsedResult(
        outcome_status="COMPLETED",
        winner_side=2,
        retired_side=None,
        sets_won_player_1=0,
        sets_won_player_2=2,
        games_won_player_1=None,
        games_won_player_2=None,
        scoreline_text="0-2",
    )
    current = ("COMPLETED", 1, None, 2, 0, None, None)
    assert (
        _terminal_result_conflict(current, candidate)
        == "terminal_winner_conflict:1:2"
    )


def test_terminal_result_conflict_allows_matching_terminal_truth() -> None:
    candidate = ParsedResult(
        outcome_status="RETIRED",
        winner_side=1,
        retired_side=2,
        sets_won_player_1=1,
        sets_won_player_2=0,
        games_won_player_1=8,
        games_won_player_2=8,
        scoreline_text="1-0",
    )
    current = ("RETIRED", 1, 2, 1, 0, 8, 8)
    assert _terminal_result_conflict(current, candidate) is None

def test_result_orientation_flips_winner_and_score_for_reversed_canonical_players() -> None:
    summary = _summary(
        winner_id="sr:p1",
        period_scores=_periods(),
    )
    fixture = parse_summary(summary, received_at=RECEIVED)
    provider_result = _parse_terminal_result(summary, fixture=fixture)

    canonical_result = _orient_result_to_canonical(
        provider_result,
        fixture=fixture,
        player_map={"sr:p1": 202, "sr:p2": 101},
        canonical_player_ids=(101, 202),
    )

    assert canonical_result.winner_side == 2
    assert canonical_result.retired_side is None
    assert canonical_result.sets_won_player_1 == 1
    assert canonical_result.sets_won_player_2 == 2
    assert canonical_result.games_won_player_1 == 15
    assert canonical_result.games_won_player_2 == 16
    assert canonical_result.scoreline_text == "1-2"


def test_result_orientation_flips_retired_side_for_reversed_canonical_players() -> None:
    summary = _summary(
        winner_id="sr:p2",
        winning_reason="retirement",
        period_scores=_periods(),
    )
    fixture = parse_summary(summary, received_at=RECEIVED)
    provider_result = _parse_terminal_result(summary, fixture=fixture)

    canonical_result = _orient_result_to_canonical(
        provider_result,
        fixture=fixture,
        player_map={"sr:p1": 202, "sr:p2": 101},
        canonical_player_ids=(101, 202),
    )

    assert canonical_result.outcome_status == "RETIRED"
    assert canonical_result.winner_side == 1
    assert canonical_result.retired_side == 2


def test_result_orientation_rejects_noncanonical_player_map() -> None:
    summary = _summary(period_scores=_periods())
    fixture = parse_summary(summary, received_at=RECEIVED)
    provider_result = _parse_terminal_result(summary, fixture=fixture)

    with pytest.raises(ValueError, match="orientation_conflict"):
        _orient_result_to_canonical(
            provider_result,
            fixture=fixture,
            player_map={"sr:p1": 101, "sr:p2": 999},
            canonical_player_ids=(101, 202),
        )


def _two_event_summary(event_id: str, competitor_1: str, competitor_2: str, winner: str):
    summary = _summary(
        winner_id=winner,
        period_scores=_periods(),
        statistics=_stats(),
    )
    summary["sport_event"]["id"] = event_id
    summary["sport_event"]["competitors"] = [
        {"id": competitor_1, "name": "One", "qualifier": "home"},
        {"id": competitor_2, "name": "Two", "qualifier": "away"},
    ]
    statistics = summary["statistics"]
    statistics["totals"]["competitors"][0]["id"] = competitor_1
    statistics["totals"]["competitors"][1]["id"] = competitor_2
    return summary


def test_refresh_uses_each_events_own_player_bridge_map(
    db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the stats writing pass must not reuse a stale per-event bridge map.

    Two terminal events with different Sportradar competitor pairs are prepared in
    one refresh pass. The result/stats persistence pass previously reused the last
    prepared event's player map for every event, so all but one result was rejected
    with ``sportradar_final_stats_player_bridge_mismatch``. Each event must instead
    be validated against its own bridge map.
    """
    player_ids = {}
    for provider_id, name in (
        ("sr:a1", "A One"),
        ("sr:a2", "A Two"),
        ("sr:b1", "B One"),
        ("sr:b2", "B Two"),
    ):
        player_id = db.execute(
            "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
            (name,),
        ).fetchone()[0]
        db.execute(
            "INSERT INTO player_source_ids (player_id, source_system, source_player_id) "
            "VALUES (%s, 'SPORTRADAR', %s)",
            (player_id, provider_id),
        )
        player_ids[provider_id] = player_id

    tournament_id = db.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Bridge Map Open', 'CHALLENGER', 'CHALLENGER') RETURNING tournament_id"
    ).fetchone()[0]
    edition_id = db.execute(
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface, start_date, end_date) "
        "VALUES (%s, 2026, 'Bridge Map Open 2026', 'CLAY', '2026-09-14', '2026-09-20') "
        "RETURNING tournament_edition_id",
        (tournament_id,),
    ).fetchone()[0]
    match_a = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    match_b = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]

    summaries = [
        _two_event_summary("sr:event:A", "sr:a1", "sr:a2", "sr:a1"),
        _two_event_summary("sr:event:B", "sr:b1", "sr:b2", "sr:b2"),
    ]

    def fake_fetch_rows(api_key: str, target_date: date):
        return RECEIVED, summaries

    def fake_ingest(conn, payload, *, received_at, allow_new_events):
        return SportradarIngestOutcome(
            rows_seen=2,
            rows_ingested=2,
            rows_rejected=0,
            rows_deferred_new=0,
            status="COMPLETED",
            matches=(
                SportradarIngestedMatch(
                    source_match_id="sr:event:A",
                    match_id=match_a,
                    match_version_id=1,
                    player_1_id=player_ids["sr:a1"],
                    player_2_id=player_ids["sr:a2"],
                    match_status="COMPLETED",
                    overlap_status="NEW_CANONICAL_EVENT",
                ),
                SportradarIngestedMatch(
                    source_match_id="sr:event:B",
                    match_id=match_b,
                    match_version_id=2,
                    player_1_id=player_ids["sr:b1"],
                    player_2_id=player_ids["sr:b2"],
                    match_status="COMPLETED",
                    overlap_status="NEW_CANONICAL_EVENT",
                ),
            ),
        )

    monkeypatch.setattr(refresh_module, "ingest_sportradar_daily_summaries", fake_ingest)

    outcome = refresh_sportradar_history_day(
        db,
        api_key="test-key",
        target_date=date(2026, 9, 19),
        fetch_rows=fake_fetch_rows,
    )

    assert outcome.observations_inserted == 2
    assert outcome.stat_rows_inserted == 4
    for match_id, (p1, p2) in (
        (match_a, (player_ids["sr:a1"], player_ids["sr:a2"])),
        (match_b, (player_ids["sr:b1"], player_ids["sr:b2"])),
    ):
        completed = db.execute(
            "SELECT count(*) FROM match_result_observations "
            "WHERE match_id = %s AND outcome_status = 'COMPLETED'",
            (match_id,),
        ).fetchone()[0]
        stat_players = db.execute(
            "SELECT array_agg(player_id ORDER BY player_id) FROM match_stat_observations "
            "WHERE match_id = %s AND source_system = 'SPORTRADAR'",
            (match_id,),
        ).fetchone()[0]
        assert completed == 1
        assert stat_players == [min(p1, p2), max(p1, p2)]

