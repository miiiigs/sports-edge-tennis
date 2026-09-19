from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sports_edge_tennis.live.sportradar_fixtures import (
    SportradarFixtureError,
    classify_competition,
    parse_daily_summaries,
    parse_summary,
)


RECEIVED = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _summary(
    *,
    event_id="sr:sport_event:10",
    level="challenger",
    competition_name="ATP Challenger Rennes Men Singles",
    category_name="Challenger",
    status="not_started",
    competitors=None,
):
    return {
        "sport_event": {
            "id": event_id,
            "start_time": "2026-09-19T13:30:00Z",
            "sport_event_context": {
                "category": {"name": category_name},
                "competition": {
                    "id": "sr:competition:1",
                    "name": competition_name,
                    "level": level,
                },
                "season": {"id": "sr:season:2026"},
            },
            "competitors": competitors
            or [
                {
                    "id": "sr:competitor:873695",
                    "name": "Blanch, Darwin",
                    "qualifier": "home",
                },
                {
                    "id": "sr:competitor:294300",
                    "name": "Nakashima, Brandon",
                    "qualifier": "away",
                },
            ],
        },
        "sport_event_status": {"status": status},
    }


def test_parse_challenger_fixture_preserves_stable_ids_and_order() -> None:
    row = parse_summary(_summary(), received_at=RECEIVED)
    assert row.source_match_id == "sr:sport_event:10"
    assert row.competition_source_id == "sr:competition:1"
    assert row.season_source_id == "sr:season:2026"
    assert row.player_1_source_id == "sr:competitor:873695"
    assert row.player_2_source_id == "sr:competitor:294300"
    assert row.match_start_at == datetime(2026, 9, 19, 13, 30, tzinfo=UTC)
    assert row.match_status == "SCHEDULED"
    assert row.tour_scope == "CHALLENGER"
    assert row.competition_tier == "CHALLENGER"


def test_competition_classifier_is_explicit_and_fail_closed() -> None:
    assert classify_competition(
        competition_name="ATP 500 Tokyo",
        competition_level="atp",
        category_name="ATP",
    ) == ("ATP_MAIN", "ATP_500")
    assert classify_competition(
        competition_name="Davis Cup World Group I",
        competition_level="atp",
        category_name="Davis Cup",
    ) == ("ATP_MAIN", "ATP_TOUR")
    with pytest.raises(SportradarFixtureError, match="out_of_scope"):
        classify_competition(
            competition_name="WTA 500 Seoul",
            competition_level="wta",
            category_name="WTA",
        )
    with pytest.raises(SportradarFixtureError, match="unsupported"):
        classify_competition(
            competition_name="Unknown Open",
            competition_level="",
            category_name="",
        )


def test_missing_home_away_qualifier_rejects_instead_of_guessing_order() -> None:
    row = _summary(
        competitors=[
            {"id": "a", "name": "Player A"},
            {"id": "b", "name": "Player B"},
        ]
    )
    with pytest.raises(SportradarFixtureError, match="home_away"):
        parse_summary(row, received_at=RECEIVED)


def test_daily_parse_reconciles_every_seen_row() -> None:
    payload = {
        "summaries": [
            _summary(event_id="sr:sport_event:1"),
            _summary(
                event_id="sr:sport_event:2",
                competition_name="WTA 500 Seoul",
                level="wta",
                category_name="WTA",
            ),
            {"sport_event": {}},
        ]
    }
    outcome = parse_daily_summaries(payload, received_at=RECEIVED)
    assert outcome.rows_seen == 3
    assert outcome.rows_accepted == 1
    assert outcome.rows_rejected == 2
    assert sum(count for _, count in outcome.rejection_reasons) == 2


def test_terminal_status_is_parsed_but_not_reinterpreted() -> None:
    row = parse_summary(
        _summary(status="closed"),
        received_at=RECEIVED,
    )
    assert row.match_status == "COMPLETED"
