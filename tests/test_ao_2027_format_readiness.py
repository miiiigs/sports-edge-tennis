"""AO 2027 research readiness, not an assertion of live provider coverage."""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    ScoringRuleVersion,
    match_probability,
)
from sports_edge_tennis.data.agent2_history import resolve_scoring_rule
from sports_edge_tennis.data.sources.api_tennis_live import (
    ApiTennisSourceAError,
    _parse_fixture,
)


def _fixture(**overrides):
    return {
        "event_key": "101",
        "tournament_key": "201",
        "first_player_key": "301",
        "second_player_key": "302",
        "event_first_player": "Player One",
        "event_second_player": "Player Two",
        "tournament_name": "Australian Open",
        "event_type_type": "ATP Singles",
        "event_status": "Not Started",
        "event_date": "2027-01-19",
        "event_time": "02:00",
        "surface": "Hard",
        "round": "R128",
        **overrides,
    }


def _parse(**overrides):
    return _parse_fixture(
        _fixture(**overrides), row_number=1,
        received_at=datetime(2026, 11, 25, tzinfo=UTC),
    )


def _rule(record):
    return resolve_scoring_rule(
        canonical_tournament_name=record.tournament_name,
        season_year=record.season_year,
        tour_scope=record.tour_scope,
        competition_tier=record.competition_tier,
        round_code=record.round_code,
        best_of_sets=record.best_of_sets,
        qualifying_flag=record.qualifying_flag,
    )


@pytest.mark.parametrize("round_code", ["R128", "R64", "R32", "R16", "QF", "SF", "F"])
def test_main_draw_missing_format_no_longer_defaults_to_bo3(round_code):
    record = _parse(round=round_code)
    assert record.best_of_sets == 5
    assert record.competition_tier == "GRAND_SLAM"
    assert record.qualifying_flag is False
    assert _rule(record).scoring_rule_version == ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6


@pytest.mark.parametrize("round_code", ["Q1", "Q2", "Q3"])
def test_qualifying_classified_separately_but_not_enabled_for_agent2(round_code):
    record = _parse(round=round_code, event_date="2027-01-12")
    assert record.best_of_sets == 3
    assert record.qualifying_flag is True
    assert _rule(record).scoring_rule_version is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"round": None}, "ao_2027_unverified_round"),
        ({"round": "unknown"}, "ao_2027_unverified_round"),
        ({"round": "RR"}, "ao_2027_unverified_round"),
        ({"best_of_sets": 3}, "ao_2027_match_format_conflict"),
        ({"best_of_sets": 5, "event_best_of_sets": 3}, "ao_2027_match_format_conflict"),
        ({"round": "Q1", "best_of_sets": 5}, "ao_2027_match_format_conflict"),
        ({"round": "QF", "qualifying_flag": True}, "ao_2027_qualifying_conflict"),
        ({"round": "Q1", "qualifying_flag": False}, "ao_2027_qualifying_conflict"),
        ({"competition_tier": "ATP_250"}, "ao_2027_competition_conflict"),
    ],
)
def test_conflicting_or_unknown_metadata_rejected(overrides, reason):
    with pytest.raises(ApiTennisSourceAError, match=reason):
        _parse(**overrides)


def test_other_tournaments_and_historical_ingest_not_rewritten():
    assert _parse(tournament_name="Brisbane").best_of_sets == 3
    assert _parse(event_date="2026-01-19").best_of_sets == 3
    assert _rule(_parse(event_date="2028-01-19", best_of_sets=5)).scoring_rule_version is None


def test_direct_resolver_rejects_bo3_main_draw_and_flag_conflict():
    for best_of, qualifying in [(3, False), (5, True)]:
        resolved = resolve_scoring_rule(
            canonical_tournament_name="Australian Open",
            season_year=2027,
            tour_scope="ATP_MAIN",
            competition_tier="GRAND_SLAM",
            round_code="QF",
            best_of_sets=best_of,
            qualifying_flag=qualifying,
        )
        assert resolved.scoring_rule_version is None


def test_markov_uses_different_match_length_and_symmetric_equal_players():
    bo5 = _rule(_parse()).scoring_rule_version
    assert bo5 is not None
    bo3 = ScoringRuleVersion.BO3_TB7_ALL_SETS

    def forecast(rule, p1, p2, server):
        return match_probability(
            player_1_serve_probability=Decimal(p1),
            player_2_serve_probability=Decimal(p2),
            scoring_rule_version=rule,
            first_server=server,
        )

    assert forecast(bo5, "0.68", "0.61", 1) > forecast(bo3, "0.68", "0.61", 1)
    p = forecast(bo5, "0.65", "0.65", 1)
    q = forecast(bo5, "0.65", "0.65", 2)
    assert abs((p + q) / 2 - Decimal("0.5")) < Decimal("0.00001")


@pytest.mark.parametrize("overrides,reason", [
    ({"event_qualification": "1"}, "ao_2027_qualifying_conflict"),
    ({"qualifying_flag": False, "event_qualification": "1"}, "ao_2027_qualifying_conflict"),
    ({"round": "QF", "event_round": "Q1"}, "ao_2027_round_conflict"),
    ({"competition_tier": "GRAND_SLAM", "event_competition_tier": "ATP_250"}, "ao_2027_competition_conflict"),
    ({"tournament_name": "ATP Australian Open"}, "ao_2027_unverified_tournament_alias"),
])
def test_ao_2027_secondary_metadata_cannot_bypass_guard(overrides, reason):
    with pytest.raises(ApiTennisSourceAError, match=reason):
        _parse(**overrides)


@pytest.mark.parametrize("p1,p2,expected_bo3,expected_bo5", [
    ("0.65", "0.65", "0.5", "0.5"),
    ("0.68", "0.61", "0.8072855395475537", "0.8618903566595986"),
    ("0.65", "0.62", "0.6465911619592051", "0.6814968606019012"),
    ("0.70", "0.60", "0.8910122349465719", "0.9388321909043293"),
    ("0.61", "0.68", "0.19271446045244633", "0.13810964334040127"),
])
def test_existing_markov_against_independent_point_to_match_reference(p1, p2, expected_bo3, expected_bo5):
    # Independent floating-point DP reference: analytic hold and deuce tails,
    # point-level service-order tiebreak, set winner + next server transitions,
    # first-server marginalization. Not empirical forecasting results.
    for rule, expected in (
        (ScoringRuleVersion.BO3_TB7_ALL_SETS, expected_bo3),
        (ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6, expected_bo5),
    ):
        def average(a, b):
            return sum(match_probability(
                player_1_serve_probability=Decimal(a),
                player_2_serve_probability=Decimal(b),
                scoring_rule_version=rule,
                first_server=server,
            ) for server in (1, 2)) / 2

        actual = average(p1, p2)
        assert abs(actual - Decimal(expected)) < Decimal("0.00001")
        assert abs(actual + average(p2, p1) - Decimal("1")) < Decimal("0.00001")
