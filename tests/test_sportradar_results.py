from __future__ import annotations

from sports_edge_tennis.live.sportradar_results import parse_result_stat_evidence


def _summary(*, reason=None, status="closed", match_status="ended"):
    event_status = {
        "status": status,
        "match_status": match_status,
        "winner_id": "sr:p1",
        "home_score": 2,
        "away_score": 1,
        "period_scores": [
            {"type": "set", "number": 1, "home_score": 6, "away_score": 4},
            {"type": "set", "number": 2, "home_score": 3, "away_score": 6},
            {
                "type": "set",
                "number": 3,
                "home_score": 7,
                "away_score": 6,
                "home_tiebreak_score": 7,
                "away_tiebreak_score": 4,
            },
        ],
    }
    if reason is not None:
        event_status["winning_reason"] = reason
    return {
        "sport_event": {
            "competitors": [
                {"id": "sr:p1", "name": "A, Player", "qualifier": "home"},
                {"id": "sr:p2", "name": "B, Player", "qualifier": "away"},
            ]
        },
        "sport_event_status": event_status,
        "statistics": {
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
                            "aces": 6,
                            "double_faults": 2,
                        },
                    },
                    {
                        "id": "sr:p2",
                        "statistics": {
                            "first_serve_points_won": 28,
                            "second_serve_points_won": 12,
                            "service_points_won": 40,
                            "service_points_lost": 30,
                            "first_serve_successful": 45,
                            "aces": 4,
                            "double_faults": 3,
                        },
                    },
                ]
            }
        },
    }


def test_completed_result_and_two_sided_a2_stats_are_normalized() -> None:
    evidence = parse_result_stat_evidence(_summary())
    assert evidence.result.outcome_status == "COMPLETED"
    assert evidence.result.winner_provider_id == "sr:p1"
    assert evidence.result.sets_home == 2
    assert evidence.result.sets_away == 1
    assert evidence.result.games_home == 16
    assert evidence.result.games_away == 16
    assert evidence.result.scoreline_text.startswith("6-4 3-6 7-6")
    assert len(evidence.stats) == 2

    p1, p2 = evidence.stats
    assert p1.line.service_points_played == 72
    assert p1.line.first_serve_points_won == 31
    assert p1.line.second_serve_points_won == 13
    assert p1.line.return_points_won == 30
    assert p2.line.service_points_played == 70
    assert p2.line.return_points_won == 28


def test_retirement_maps_loser_as_retired_without_forcing_completed() -> None:
    evidence = parse_result_stat_evidence(_summary(reason="retirement"))
    assert evidence.result.outcome_status == "RETIRED"
    assert evidence.result.winner_provider_id == "sr:p1"
    assert evidence.result.retired_provider_id == "sr:p2"


def test_walkover_remains_walkover() -> None:
    evidence = parse_result_stat_evidence(
        _summary(reason="walkover", match_status="walkover")
    )
    assert evidence.result.outcome_status == "WALKOVER"
    assert evidence.result.retired_provider_id is None


def test_incoherent_service_totals_drop_stats_fail_closed() -> None:
    row = _summary()
    row["statistics"]["totals"]["competitors"][0]["statistics"]["service_points_won"] = 45
    evidence = parse_result_stat_evidence(row)
    assert evidence.result is not None
    assert evidence.stats == ()


def test_live_match_does_not_create_terminal_result() -> None:
    row = _summary(status="live", match_status="2nd_set")
    row["sport_event_status"].pop("winner_id")
    evidence = parse_result_stat_evidence(row)
    assert evidence.result is None
