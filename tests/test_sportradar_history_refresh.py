from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sports_edge_tennis.live.sportradar_fixtures import parse_summary
from sports_edge_tennis.live.sportradar_history_refresh import (
    _parse_final_stats,
    _parse_terminal_result,
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
