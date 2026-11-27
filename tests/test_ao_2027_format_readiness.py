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
    ("tournament", "event_date", "round_code"),
    [
        ("Brisbane", "2027-01-04", "R32"),
        ("Hong Kong", "2027-01-04", "R32"),
        ("Adelaide", "2027-01-11", "R32"),
        ("Auckland", "2027-01-11", "R32"),
    ],
)
def test_january_tournament_discovery_scope_and_formats(
    tournament, event_date, round_code
):
    record = _parse(
        tournament_name=tournament, event_date=event_date, round=round_code
    )
    assert record.best_of_sets == 3
    assert record.competition_tier == "ATP_TOUR"
    assert record.qualifying_flag is False
    assert _rule(record).scoring_rule_version == ScoringRuleVersion.BO3_TB7_ALL_SETS


def test_united_cup_is_not_ao_2027_scope():
    record = _parse(
        tournament_name="United Cup",
        event_date="2027-01-01",
        round="RR",
    )
    assert record.best_of_sets == 3
    assert record.competition_tier == "ATP_TOUR"
    assert _rule(record).scoring_rule_version == ScoringRuleVersion.BO3_TB7_ALL_SETS
    resolved = resolve_scoring_rule(
        canonical_tournament_name="United Cup",
        season_year=2027,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        round_code="RR",
        best_of_sets=3,
        qualifying_flag=False,
    )
    assert resolved.scoring_rule_version == ScoringRuleVersion.BO3_TB7_ALL_SETS
    assert resolved.scoring_rule_version != ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6


def test_ao_main_draw_deciding_set_uses_ten_point_tiebreak_rule():
    for round_code in ("R128", "R64", "R32", "R16", "QF", "SF", "F"):
        resolved = _rule(_parse(round=round_code))
        assert resolved.scoring_rule_version is ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
        assert "TB10" in resolved.scoring_rule_version.value


def test_dashboard_aligned_read_model_is_not_tournament_or_season_scoped():
    # Structural January compatibility: the Model Picks read model selects from
    # the same Manager/prediction/version tables the runner writes and has no
    # tournament, tier or season filter that would exclude an AO 2027 pick.
    from sports_edge_tennis.dashboard.aligned_model_picks import (
        PIT_MATCH_START_LATERAL,
        aligned_production_model_picks_cte,
    )

    sql = aligned_production_model_picks_cte().lower()
    for token in ("season_year", "australian open", "grand_slam", "atp_250"):
        assert token not in sql
    assert "manager_consensus_results" in sql
    assert "prediction_runs" in sql
    lateral = PIT_MATCH_START_LATERAL.lower()
    assert "match_versions" in lateral
    assert "mv0.as_of <= p.picked_at" in lateral


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
    # The 2026 AO is historical data: this future rule must not create a new
    # corrected version on a re-ingestion. A later AO edition needs its own
    # reviewed rule rather than a silent BO3 fallback.
    assert _parse(event_date="2026-01-19").best_of_sets == 3
    with pytest.raises(ApiTennisSourceAError, match="ao_future_edition_unverified"):
        _parse(event_date="2028-01-19", best_of_sets=5)


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


def test_simple_independent_set_probability_reference_at_sixty_percent():
    """Brian's requested set-level regression, independent of the serve engine."""
    from ops.validation.ao_root_cause_and_dependency_20261126 import (
        independent_set_match_probability,
    )

    assert independent_set_match_probability(0.60, 3) == pytest.approx(0.648)
    assert independent_set_match_probability(0.60, 5) == pytest.approx(0.68256)
    # Sanity: a fair set is a fair match, and a match is never shorter than a set.
    assert independent_set_match_probability(0.50, 3) == pytest.approx(0.5)
    assert independent_set_match_probability(0.50, 5) == pytest.approx(0.5)
    assert independent_set_match_probability(0.60, 5) > independent_set_match_probability(0.60, 3)


def test_serve_based_markov_deciding_set_tiebreak_is_effective_and_symmetric():
    """Separate engine check: the deciding-set rule changes the result."""
    from sports_edge_tennis.agents.agent2_hierarchical_markov import match_probability

    def average(rule, p1, p2):
        return sum(
            match_probability(
                player_1_serve_probability=Decimal(p1),
                player_2_serve_probability=Decimal(p2),
                scoring_rule_version=rule,
                first_server=server,
            )
            for server in (1, 2)
        ) / 2

    tb10 = average(ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6, "0.68", "0.61")
    advantage = average(ScoringRuleVersion.BO5_DECIDER_ADVANTAGE, "0.68", "0.61")
    assert tb10 != advantage
    # Player symmetry: swapping both players complements the probability.
    assert abs(
        average(ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6, "0.68", "0.61")
        + average(ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6, "0.61", "0.68")
        - Decimal(1)
    ) < Decimal("0.00001")


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
    ({"tournament_name": "Australian Open 2027"}, "ao_2027_unverified_tournament_alias"),
])
def test_ao_2027_secondary_metadata_cannot_bypass_guard(overrides, reason):
    with pytest.raises(ApiTennisSourceAError, match=reason):
        _parse(**overrides)


def test_qualifying_draw_with_main_draw_round_label_is_rejected_generically():
    # The 2026 AO qualifying rows were labelled QF/SF/F with the Q prefix
    # stripped. Future ingestion must quarantine that ambiguity, not repeat it.
    with pytest.raises(ApiTennisSourceAError, match="qualifying_round_unverified"):
        _parse(round="QF", qualifying_flag=True, event_date="2026-01-12")


def test_reviewed_alias_keeps_one_edition_identity_and_hard_surface():
    record = _parse(tournament_name="ATP Australian Open")
    assert record.tournament_name == "Australian Open"
    assert record.edition_name == "Australian Open 2027"
    assert record.surface == "HARD"
    assert record.competition_tier == "GRAND_SLAM"
    assert record.best_of_sets == 5


def test_missing_surface_is_filled_for_the_reviewed_ao_identity():
    fixture = _fixture()
    fixture.pop("surface")
    record = _parse_fixture(
        fixture, row_number=1, received_at=datetime(2026, 11, 25, tzinfo=UTC)
    )
    assert record.surface == "HARD"


def test_non_hard_surface_is_rejected_for_the_ao_identity():
    with pytest.raises(ApiTennisSourceAError, match="ao_2027_surface_conflict"):
        _parse(surface="Clay")


def test_future_final_supplies_an_evidence_based_edition_end_date():
    from sports_edge_tennis.data.sources.api_tennis_live import _edition_end_date

    final = _parse(round="F", event_date="2027-01-31")
    assert _edition_end_date(final) == final.match_start_at.date()
    assert _edition_end_date(_parse(round="R128")) is None
    assert _edition_end_date(_parse(event_date="2026-01-31", round="F")) is None


def _agent_prediction(agent_code: str, probability: str):
    from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
    from sports_edge_tennis.data.prediction_persistence import (
        PersistedPredictiveAgentPrediction,
    )

    return PersistedPredictiveAgentPrediction(
        prediction_id=1,
        prediction_run_id=1,
        match_id=1,
        agent_code=agent_code,
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=Decimal(probability),
        probability_representation=AGENT_PROBABILITY_REPRESENTATIONS[agent_code],
        model_version="test_model",
        feature_version="test_features",
        config_version="test_config",
        parameter_schema_version="test_params",
        agent_config_version_id=1,
        as_of=datetime(2027, 1, 19, tzinfo=UTC),
    )


def _manager_policy():
    from sports_edge_tennis.manager.contracts import (
        AGENT3_ABSTENTION_MAX_PROBABILITY_SPREAD,
        MANAGER_POLICY_KEY,
        MIN_ACTIVE_AGENT_PROBABILITY,
        THREE_AGENT_MAX_PROBABILITY_SPREAD,
        ManagerPolicyV1,
    )

    return ManagerPolicyV1(
        manager_policy_version_id=1,
        policy_key=MANAGER_POLICY_KEY,
        policy_version="v1",
        three_agent_max_probability_spread=THREE_AGENT_MAX_PROBABILITY_SPREAD,
        agent3_abstention_max_probability_spread=(
            AGENT3_ABSTENTION_MAX_PROBABILITY_SPREAD
        ),
        min_active_agent_probability=MIN_ACTIVE_AGENT_PROBABILITY,
        allow_agent3_abstention=True,
    )


def test_agent2_is_format_sensitive_through_exactly_one_markov_conversion():
    """Same serve/return inputs, only match length changes.

    The serve-based engine already resolves the match from point level. This
    asserts the expected directional effect and that the BO5 result is not a
    second independent-set conversion applied on top of the BO3 result.
    """
    from sports_edge_tennis.agents.agent2_hierarchical_markov import (
        match_probability,
    )

    def average(rule):
        return sum(
            match_probability(
                player_1_serve_probability=Decimal("0.65"),
                player_2_serve_probability=Decimal("0.62"),
                scoring_rule_version=rule,
                first_server=server,
            )
            for server in (1, 2)
        ) / 2

    bo3 = average(ScoringRuleVersion.BO3_TB7_ALL_SETS)
    bo5 = average(ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6)
    assert bo5 > bo3
    # A duplicate adjustment would push BO5 far above the point-level
    # reference; the calibrated engine stays in the narrow format effect.
    assert Decimal("0.02") < bo5 - bo3 < Decimal("0.05")
    # Swapping the players complements each result (no hidden format bias).
    def swapped(rule):
        return sum(
            match_probability(
                player_1_serve_probability=Decimal("0.62"),
                player_2_serve_probability=Decimal("0.65"),
                scoring_rule_version=rule,
                first_server=server,
            )
            for server in (1, 2)
        ) / 2

    assert abs(bo3 + swapped(ScoringRuleVersion.BO3_TB7_ALL_SETS) - 1) < Decimal(
        "0.00001"
    )
    assert abs(bo5 + swapped(ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6) - 1) < Decimal(
        "0.00001"
    )


def test_manager_adds_no_second_match_format_adjustment():
    """Manager consumes agent probabilities; it must not re-apply BO3/BO5.

    Controlled counterfactual: identical players, ratings, history, surface,
    cutoff and configuration. Only Agent 2's match length changes. Agent 1 is
    format-invariant. Manager must move by exactly one third of the Agent 2
    delta (the arithmetic mean of the three active agents), not by a further
    set-to-match conversion.
    """
    from sports_edge_tennis.agents.agent2_hierarchical_markov import (
        match_probability,
    )
    from sports_edge_tennis.manager.consensus import evaluate_consensus
    from sports_edge_tennis.manager.contracts import consensus_mean

    def agent2_average(rule):
        return sum(
            match_probability(
                player_1_serve_probability=Decimal("0.65"),
                player_2_serve_probability=Decimal("0.62"),
                scoring_rule_version=rule,
                first_server=server,
            )
            for server in (1, 2)
        ) / 2

    agent2_bo3 = agent2_average(ScoringRuleVersion.BO3_TB7_ALL_SETS)
    agent2_bo5 = agent2_average(ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6)
    agent1 = Decimal("0.660000")
    agent3 = Decimal("0.670000")

    def manager_result(agent2_probability):
        return evaluate_consensus(
            [
                _agent_prediction("AGENT_1_SURFACE_ELO", str(agent1)),
                _agent_prediction(
                    "AGENT_2_HIERARCHICAL_MARKOV", str(agent2_probability)
                ),
                _agent_prediction("AGENT_3_INTRANSITIVITY", str(agent3)),
            ],
            match_id=1,
            prediction_run_id=1,
            policy=_manager_policy(),
            as_of=datetime(2027, 1, 19, tzinfo=UTC),
        )

    bo3_result = manager_result(agent2_bo3)
    bo5_result = manager_result(agent2_bo5)
    assert bo3_result.manager_status == "CONSENSUS"
    assert bo5_result.manager_status == "CONSENSUS"
    assert bo3_result.selected_side == bo5_result.selected_side == 1
    assert bo3_result.consensus_probability == consensus_mean(
        (agent1, agent2_bo3, agent3)
    )
    assert bo5_result.consensus_probability == consensus_mean(
        (agent1, agent2_bo5, agent3)
    )
    expected_delta = consensus_mean(
        (agent1, agent2_bo5, agent3)
    ) - consensus_mean((agent1, agent2_bo3, agent3))
    actual_delta = (
        bo5_result.consensus_probability - bo3_result.consensus_probability
    )
    # Exactly the mean effect, not a second set-to-match transformation.
    assert actual_delta == expected_delta
    assert abs(actual_delta - (agent2_bo5 - agent2_bo3) / Decimal(3)) < Decimal(
        "0.000001"
    )


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
        def average(a, b, scoring_rule=rule):
            return sum(match_probability(
                player_1_serve_probability=Decimal(a),
                player_2_serve_probability=Decimal(b),
                scoring_rule_version=scoring_rule,
                first_server=server,
            ) for server in (1, 2)) / 2

        actual = average(p1, p2)
        assert abs(actual - Decimal(expected)) < Decimal("0.00001")
        assert abs(actual + average(p2, p1) - Decimal(1)) < Decimal("0.00001")
