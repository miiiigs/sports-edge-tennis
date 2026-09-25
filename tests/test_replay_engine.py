from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sports_edge_tennis.agents.contracts import (
    AGENT1_RAW_PROBABILITY_V1,
    AGENT2_RAW_PROBABILITY_V1,
    AGENT3_RAW_PROBABILITY_V1,
    PredictiveAgentResultV1,
)
from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as A1_CONFIG,
    replay_history as replay_agent1_history,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3HistorySnapshot,
)
from sports_edge_tennis.data.agent2_history import (
    build_history_snapshot as build_agent2_snapshot,
)
from sports_edge_tennis.manager.contracts import MANAGER_POLICY_KEY, ManagerPolicyV1
from sports_edge_tennis.market.contracts import (
    MarketPolicyV1,
    OddsSnapshotV1,
    SharpReferenceSpec,
)
from sports_edge_tennis.replay import engine
from sports_edge_tennis.replay.contracts import (
    ReplayActualResult,
    ReplayDecisionTime,
    ReplayMatchInput,
    ReplayRunInput,
)
from sports_edge_tennis.replay.pit import PointInTimeViolation
from sports_edge_tennis.runner.contracts import ResolvedTargetV1
from sports_edge_tennis.veto.contracts import VetoContext

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"

PREDICTION = datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc)
MARKET = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
MATCH_START = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)

MANAGER_POLICY = ManagerPolicyV1(
    manager_policy_version_id=1,
    policy_key=MANAGER_POLICY_KEY,
    policy_version="v1",
    three_agent_max_probability_spread=Decimal("0.08"),
    agent3_abstention_max_probability_spread=Decimal("0.06"),
    min_active_agent_probability=Decimal("0.5"),
    allow_agent3_abstention=True,
)
MARKET_POLICY = MarketPolicyV1(
    market_policy_version_id=1,
    policy_key="tennis_market_value",
    policy_version="v1",
    atp_main_min_edge_pct=Decimal("0.05"),
    challenger_min_edge_pct=Decimal("0.08"),
    long_price_threshold=Decimal("4.0"),
    long_price_min_edge_pct=Decimal("0.08"),
    default_devig_method="POWER",
    sharp_reference_policy_code="PINNACLE_SHARP",
    odds_max_staleness_minutes=15,
    type_b_enabled=False,
)
SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


def _target(match_id: int = 101) -> ResolvedTargetV1:
    return ResolvedTargetV1(
        match_id=match_id,
        match_version_id=1,
        player_1_id=11,
        player_2_id=22,
        surface="HARD",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="SCHEDULED",
        canonical_tournament_name="Replay Open",
        normalized_tournament_alias="replay_open",
        season_year=2026,
    )


def _adapted(code: str, side, probability, representation):
    return PredictiveAgentResultV1(
        agent_code=code,
        prediction_status="PREDICTED",
        selected_side=side,
        selected_probability=Decimal(probability),
        probability_representation=representation,
        abstain_reason=None,
        model_version="m",
        feature_version="f",
        config_version="c",
        parameter_schema_version="p",
        as_of=PREDICTION,
    )


def _abstained(code: str, reason: str = "INSUFFICIENT_HISTORY"):
    return PredictiveAgentResultV1(
        agent_code=code,
        prediction_status="ABSTAINED",
        selected_side=None,
        selected_probability=None,
        probability_representation=None,
        abstain_reason=reason,
        model_version="m",
        feature_version="f",
        config_version="c",
        parameter_schema_version="p",
        as_of=PREDICTION,
    )


def _consensus_agents():
    return {
        A1: _adapted(A1, 1, "0.55", AGENT1_RAW_PROBABILITY_V1),
        A2: _adapted(A2, 1, "0.56", AGENT2_RAW_PROBABILITY_V1),
        A3: _adapted(A3, 1, "0.54", AGENT3_RAW_PROBABILITY_V1),
    }


def _wide_spread_agents():
    return {
        A1: _adapted(A1, 1, "0.60", AGENT1_RAW_PROBABILITY_V1),
        A2: _adapted(A2, 1, "0.50", AGENT2_RAW_PROBABILITY_V1),
        A3: _abstained(A3, "INSUFFICIENT_NETWORK"),
    }


def _monkeypatch_agents(monkeypatch, agents):
    monkeypatch.setattr(engine, "_adapt_agent_results", lambda run, match: dict(agents))


def _odds(*, offered_price: str = "2.30", future: bool = False, observed_at=None):
    if observed_at is not None:
        observed = observed_at
    else:
        observed = MARKET + timedelta(minutes=30) if future else MARKET
    return (
        OddsSnapshotV1(1, 101, "SHARP", "PINNACLE", "MATCH_WINNER", "PLAYER_1",
                       Decimal("2.10"), "OPEN", None, "s1", observed, observed),
        OddsSnapshotV1(2, 101, "SHARP", "PINNACLE", "MATCH_WINNER", "PLAYER_2",
                       Decimal("1.80"), "OPEN", None, "s1", observed, observed),
        OddsSnapshotV1(3, 101, "OFFERED", "B365", "MATCH_WINNER", "PLAYER_1",
                       Decimal(offered_price), "OPEN", None, "s2", observed, observed),
    )


def _veto_context(*, stale_fixture: bool = False) -> VetoContext:
    observed = MARKET - timedelta(hours=7) if stale_fixture else MARKET - timedelta(hours=1)
    return VetoContext(
        match_id=101,
        prediction_run_id=0,
        selected_side=1,
        decision_as_of=MARKET,
        target_start_at=MATCH_START,
        current_match_status="SCHEDULED",
        fixture_source_observed_at=observed,
        previous_matches=(),
        sharp_move_against_pct=Decimal("0.0"),
    )


_MISSING = object()


def _match_input(
    *,
    odds=None,
    veto_context=None,
    actual_result=_MISSING,
    match_id: int = 101,
) -> ReplayMatchInput:
    resolved_result = (
        ReplayActualResult(match_id, "COMPLETED", 1, 5, MATCH_START + timedelta(hours=2))
        if actual_result is _MISSING
        else actual_result
    )
    return ReplayMatchInput(
        target=_target(match_id),
        competition_tier="ATP_250",
        odds_snapshots=tuple(odds) if odds is not None else _odds(),
        veto_context=veto_context if veto_context is not None else _veto_context(),
        actual_result=resolved_result,
        provider_event_ids=("SR:123",),
    )


def _run(match_input: ReplayMatchInput, *, agent1_replay=None, agent2_snapshot=None, agent3_snapshot=None):
    return ReplayRunInput(
        run_id="run-1",
        run_sequence=1,
        decision_time=ReplayDecisionTime(PREDICTION, MARKET, "TEST"),
        manager_policy=MANAGER_POLICY,
        market_policy=MARKET_POLICY,
        sharp_reference=SHARP,
        matches=(match_input,),
        agent1_replay=agent1_replay if agent1_replay is not None else object(),
        agent2_snapshot=agent2_snapshot if agent2_snapshot is not None else object(),
        agent3_snapshot=agent3_snapshot if agent3_snapshot is not None else object(),
    )


def test_qualified_value_bet_scores_win(monkeypatch):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    result = engine.run_replay([_run(_match_input())])

    assert len(result.model_picks) == 1
    pick = result.model_picks[0]
    assert pick.classification == "MANAGER_QUALIFIED"
    assert pick.manager_outcome == "CONSENSUS"
    assert pick.selected_side == 1
    assert pick.actual_result == "WIN"

    assert len(result.value_bets) == 1
    bet = result.value_bets[0]
    assert bet.actual_result == "WIN"
    assert bet.offered_decimal_price == Decimal("2.30")
    assert bet.edge_pct == Decimal("0.55") * Decimal("2.30") - Decimal("1")
    assert bet.units == Decimal("1.30")
    assert bet.stake_fraction == Decimal("0.020000")
    assert bet.staking_rule_version == "QUARTER_KELLY_CAP_2_V1"
    assert bet.veto_status == "PASSED"
    assert bet.clv_status == "NOT_AVAILABLE_PIT"
    assert bet.clv_pct is None


def test_missing_odds_marked_data_not_available_pit(monkeypatch):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    result = engine.run_replay([_run(_match_input(odds=()))])

    assert len(result.model_picks) == 1
    assert result.value_bets == ()
    reasons = {exclusion.reason for exclusion in result.exclusions}
    assert "MISSING_MARKET_ODDS" in reasons
    assert any(o.outcome == "DATA_NOT_AVAILABLE_PIT" for o in result.opportunities)


def test_missing_sharp_reference_marked_data_not_available_pit(monkeypatch):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    offered_only = tuple(o for o in _odds() if o.source_system != "SHARP")
    result = engine.run_replay([_run(_match_input(odds=offered_only))])

    assert result.value_bets == ()
    reasons = {exclusion.reason for exclusion in result.exclusions}
    assert "SHARP_REFERENCE_UNAVAILABLE_PIT" in reasons


def test_wide_spread_classification_is_not_a_value_bet(monkeypatch):
    _monkeypatch_agents(monkeypatch, _wide_spread_agents())
    result = engine.run_replay([_run(_match_input(odds=()))])

    assert len(result.model_picks) == 1
    pick = result.model_picks[0]
    assert pick.classification == "A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED"
    assert pick.manager_reason == "AGENT3_ABSTENTION_SPREAD_EXCEEDED"
    # The agreed primary side is retained for display scoring, never as a bet.
    assert pick.selected_side == 1
    assert result.value_bets == ()
    outcomes = {o.outcome for o in result.opportunities}
    assert outcomes == {"MANAGER_REJECTION"}


def test_veto_rejection_recorded(monkeypatch):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    result = engine.run_replay(
        [_run(_match_input(veto_context=_veto_context(stale_fixture=True)))]
    )

    assert result.value_bets == ()
    assert any(o.outcome == "VETO_REJECTION" for o in result.opportunities)
    triggers = {o.detail for o in result.opportunities}
    assert "FIXTURE_FEED_STALE" in triggers


def test_insufficient_edge(monkeypatch):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    result = engine.run_replay([_run(_match_input(odds=_odds(offered_price="1.85")))])

    assert result.value_bets == ()
    assert any(o.outcome == "INSUFFICIENT_EDGE" for o in result.opportunities)


def test_future_odds_fail_closed(monkeypatch):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    with pytest.raises(PointInTimeViolation):
        engine.run_replay([_run(_match_input(odds=_odds(future=True)))])


def test_result_visible_at_prediction_fails_closed(monkeypatch):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    bad_result = ReplayActualResult(101, "COMPLETED", 1, 5, PREDICTION - timedelta(hours=1))
    with pytest.raises(PointInTimeViolation):
        engine.run_replay([_run(_match_input(actual_result=bad_result))])


@pytest.mark.parametrize(
    "outcome_status,winner_side,expected,expected_units",
    (
        ("COMPLETED", 1, "WIN", Decimal("1.30")),
        ("COMPLETED", 2, "LOSS", Decimal("-1")),
        ("RETIRED", None, "VOID", Decimal("0")),
        ("WALKOVER", None, "VOID", Decimal("0")),
        ("CANCELLED", None, "CANCELLED", Decimal("0")),
    ),
)
def test_value_bet_scoring_outcomes(monkeypatch, outcome_status, winner_side, expected, expected_units):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    actual = ReplayActualResult(
        match_id=101,
        outcome_status=outcome_status,
        winner_side=winner_side,
        result_observation_id=5,
        result_visible_at=MATCH_START + timedelta(hours=2),
    )
    result = engine.run_replay([_run(_match_input(actual_result=actual))])

    assert len(result.value_bets) == 1
    bet = result.value_bets[0]
    assert bet.actual_result == expected
    assert bet.units == expected_units


def test_pending_value_bet_has_no_units(monkeypatch):
    _monkeypatch_agents(monkeypatch, _consensus_agents())
    result = engine.run_replay([_run(_match_input(actual_result=None))])

    assert len(result.value_bets) == 1
    bet = result.value_bets[0]
    assert bet.actual_result == "PENDING"
    assert bet.units is None


def test_first_model_pick_precedes_later_value_bet(monkeypatch):
    from dataclasses import replace

    _monkeypatch_agents(monkeypatch, _consensus_agents())

    run1 = _run(_match_input(odds=()))
    later_market = MARKET + timedelta(hours=1)
    run2 = replace(
        run1,
        run_id="run-2",
        run_sequence=2,
        decision_time=ReplayDecisionTime(PREDICTION + timedelta(hours=1), later_market, "TEST"),
        matches=(_match_input(odds=_odds(observed_at=later_market)),),
    )

    result = engine.run_replay([run2, run1])

    assert len(result.value_bets) == 1
    assert result.value_bets[0].run_sequence == 2
    # The match was a Manager-qualified Model Pick in run 1, before odds existed.
    assert result.value_bets[0].first_model_pick_decision_as_of == MARKET


def test_real_agent_pipeline_abstains_without_history():
    """Integration smoke test using the current real A1/A2/A3 functions."""
    agent1_replay = replay_agent1_history([], PREDICTION, A1_CONFIG)
    agent2_snapshot = build_agent2_snapshot([], cutoff=PREDICTION)
    agent3_snapshot = Agent3HistorySnapshot()

    result = engine.run_replay(
        [
            _run(
                _match_input(odds=()),
                agent1_replay=agent1_replay,
                agent2_snapshot=agent2_snapshot,
                agent3_snapshot=agent3_snapshot,
            )
        ]
    )

    assert len(result.model_picks) == 1
    pick = result.model_picks[0]
    assert pick.a1_state == "PREDICTED"
    assert pick.manager_outcome == "NO_BET"
    assert pick.classification == "NOT_MANAGER_QUALIFIED"
    assert result.value_bets == ()
