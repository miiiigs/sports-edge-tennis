from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sports_edge_tennis.market.contracts import (
    MarketDecisionV1,
    MarketPolicyError,
    MarketPolicyV1,
    OddsSnapshotV1,
    PersistedManagerConsensusRow,
    SharpReferenceSpec,
)
from sports_edge_tennis.market.evaluator import evaluate_market_match
from sports_edge_tennis.market.policy import is_fresh, resolve_threshold
from sports_edge_tennis.market.pricing import (
    booksum,
    edge_pct,
    minimum_acceptable_price,
    power_devig,
    snapshot_lineage_hash,
)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)


def _policy(**overrides) -> MarketPolicyV1:
    values = dict(
        market_policy_version_id=1,
        policy_key="tennis_market_value",
        policy_version="v1",
        atp_main_min_edge_pct=Decimal("0.05"),
        challenger_min_edge_pct=Decimal("0.08"),
        long_price_threshold=Decimal("4.00"),
        long_price_min_edge_pct=Decimal("0.08"),
        default_devig_method="POWER",
        sharp_reference_policy_code="PINNACLE_SHARP",
        odds_max_staleness_minutes=15,
        type_b_enabled=False,
    )
    values.update(overrides)
    return MarketPolicyV1(**values)


def _manager_row(*, side: int = 1, prob: str = "0.60", status: str = "CONSENSUS") -> PersistedManagerConsensusRow:
    if status == "NO_BET":
        side = None
        prob = None
    return PersistedManagerConsensusRow(
        manager_consensus_result_id=1,
        prediction_run_id=1,
        match_id=1,
        manager_policy_version_id=1,
        manager_status=status,
        selected_side=side,
        consensus_probability=Decimal(prob) if prob is not None else None,
        active_agent_count=3,
        abstaining_agent_count=0,
        probability_spread=Decimal("0.0"),
        reason_code="NONE" if status == "CONSENSUS" else "DIRECTIONAL_DISAGREEMENT",
        as_of=_utc(10, 0),
    )


def _snapshot(
    *,
    odds_snapshot_id: int,
    selection_type: str,
    price: str,
    source_system: str = "SHARP",
    bookmaker_code: str = "PINNACLE",
    source_snapshot_id: str = "s1",
    source_observed_at: datetime | None = None,
    market_status: str = "OPEN",
) -> OddsSnapshotV1:
    return OddsSnapshotV1(
        odds_snapshot_id=odds_snapshot_id,
        match_id=1,
        source_system=source_system,
        bookmaker_code=bookmaker_code,
        market_type="MATCH_WINNER",
        selection_type=selection_type,
        decimal_price=Decimal(price),
        market_status=market_status,
        booksum=None,
        source_snapshot_id=source_snapshot_id,
        source_observed_at=source_observed_at or _utc(10, 0),
        as_of=_utc(10, 0),
    )


SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


def _fair_reference_pair() -> tuple[OddsSnapshotV1, OddsSnapshotV1]:
    return (
        _snapshot(odds_snapshot_id=10, selection_type="PLAYER_1", price="2.00"),
        _snapshot(odds_snapshot_id=11, selection_type="PLAYER_2", price="2.00"),
    )


# --- pricing ---


def test_power_devig_probabilities_sum_to_one():
    p1, p2 = power_devig(Decimal("1.85"), Decimal("2.10"))
    assert p1 + p2 == pytest.approx(Decimal("1"), abs=Decimal("1e-20"))


def test_zero_overround_pair_returns_raw_implied_probabilities():
    p1, p2 = power_devig(Decimal("2.00"), Decimal("2.00"))
    assert p1 == Decimal("0.5")
    assert p2 == Decimal("0.5")


def test_power_devig_is_not_proportional_on_asymmetric_long_price():
    odds1 = Decimal("1.15")
    odds2 = Decimal("5.00")
    p1, p2 = power_devig(odds1, odds2)
    q1 = Decimal("1") / odds1
    q2 = Decimal("1") / odds2
    s = q1 + q2
    proportional1 = q1 / s
    proportional2 = q2 / s
    assert p1 != proportional1
    assert p2 != proportional2
    assert p1 > proportional1
    assert p2 < proportional2


def test_booksum():
    assert booksum(Decimal("2.00"), Decimal("2.00")) == Decimal("1")
    assert booksum(Decimal("3.00"), Decimal("3.00")) < Decimal("1")


def test_edge_pct_formula():
    assert edge_pct(Decimal("0.60"), Decimal("1.75")) == Decimal("0.05")


def test_minimum_acceptable_price_formula():
    assert minimum_acceptable_price(Decimal("0.60"), Decimal("0.05")) == Decimal("1.75")


# --- threshold resolution ---


def test_atp_threshold_is_5_percent():
    policy = _policy()
    assert resolve_threshold(policy, "ATP_250", Decimal("2.00"), Decimal("2.00")) == Decimal("0.05")
    assert resolve_threshold(policy, "MASTERS_1000", Decimal("2.00"), Decimal("2.00")) == Decimal("0.05")
    assert resolve_threshold(policy, "GRAND_SLAM", Decimal("2.00"), Decimal("2.00")) == Decimal("0.05")


def test_challenger_threshold_is_8_percent():
    assert resolve_threshold(_policy(), "CHALLENGER", Decimal("2.00"), Decimal("2.00")) == Decimal("0.08")


def test_long_price_either_reference_leg_escalates_to_8_percent():
    policy = _policy()
    assert resolve_threshold(policy, "ATP_250", Decimal("4.50"), Decimal("1.20")) == Decimal("0.08")
    assert resolve_threshold(policy, "ATP_250", Decimal("1.20"), Decimal("4.50")) == Decimal("0.08")


def test_exact_4_00_does_not_escalate():
    assert resolve_threshold(_policy(), "ATP_250", Decimal("4.00"), Decimal("1.25")) == Decimal("0.05")


def test_unknown_tier_fails_closed():
    with pytest.raises(MarketPolicyError):
        resolve_threshold(_policy(), "SOME_TIER", Decimal("2.00"), Decimal("2.00"))
    with pytest.raises(MarketPolicyError):
        resolve_threshold(_policy(), None, Decimal("2.00"), Decimal("2.00"))


def test_offered_price_never_feeds_threshold_resolution():
    # Threshold resolution only ever sees reference legs; an "offered" price is not an input.
    assert resolve_threshold(_policy(), "ATP_250", Decimal("1.90"), Decimal("2.10")) == Decimal("0.05")


# --- freshness ---


def test_freshness_is_decision_as_of_minus_source_observed_at():
    decision_as_of = _utc(10, 30)
    assert is_fresh(_utc(10, 20), decision_as_of, 15)
    assert not is_fresh(_utc(10, 10), decision_as_of, 15)
    assert is_fresh(_utc(10, 15), decision_as_of, 15)


# --- lineage hash ---


def test_lineage_hash_is_deterministic_and_order_independent():
    a = snapshot_lineage_hash((("REFERENCE_PLAYER_1", 10), ("REFERENCE_PLAYER_2", 11), ("OFFERED_SELECTION", 12)))
    b = snapshot_lineage_hash((("OFFERED_SELECTION", 12), ("REFERENCE_PLAYER_2", 11), ("REFERENCE_PLAYER_1", 10)))
    assert a == b
    assert a == snapshot_lineage_hash((("REFERENCE_PLAYER_1", 10), ("REFERENCE_PLAYER_2", 11), ("OFFERED_SELECTION", 12)))


def test_lineage_hash_changes_when_snapshot_set_changes():
    a = snapshot_lineage_hash((("REFERENCE_PLAYER_1", 10), ("REFERENCE_PLAYER_2", 11), ("OFFERED_SELECTION", 12)))
    b = snapshot_lineage_hash((("REFERENCE_PLAYER_1", 10), ("REFERENCE_PLAYER_2", 11), ("OFFERED_SELECTION", 13)))
    assert a != b


def test_empty_lineage_hash_is_canonical():
    assert snapshot_lineage_hash(()) == snapshot_lineage_hash(())


# --- pure Type A gate ---


def test_qualifying_bet():
    ref_p1, ref_p2 = _fair_reference_pair()
    offered = _snapshot(
        odds_snapshot_id=12,
        selection_type="PLAYER_1",
        price="1.85",
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        source_snapshot_id="s2",
    )
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[ref_p1, ref_p2, offered],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "BET"
    assert decision.reason_code == "NONE"
    assert decision.selected_side == 1
    assert decision.lineage == (("REFERENCE_PLAYER_1", 10), ("REFERENCE_PLAYER_2", 11), ("OFFERED_SELECTION", 12))


def test_manager_no_bet_is_never_qualified():
    decision = evaluate_market_match(
        manager_row=_manager_row(status="NO_BET"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "NOT_MANAGER_ELIGIBLE"
    assert decision.lineage == ()


def test_no_model_edge_vs_sharp():
    ref_p1, ref_p2 = _fair_reference_pair()
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.45"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[ref_p1, ref_p2],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "NO_MODEL_EDGE_VS_SHARP"


def test_edge_below_threshold():
    ref_p1, ref_p2 = _fair_reference_pair()
    offered = _snapshot(
        odds_snapshot_id=12,
        selection_type="PLAYER_1",
        price="1.70",
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        source_snapshot_id="s2",
    )
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[ref_p1, ref_p2, offered],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "EDGE_BELOW_THRESHOLD"


def test_equality_at_minimum_price_qualifies():
    ref_p1, ref_p2 = _fair_reference_pair()
    offered = _snapshot(
        odds_snapshot_id=12,
        selection_type="PLAYER_1",
        price="1.75",
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        source_snapshot_id="s2",
    )
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[ref_p1, ref_p2, offered],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "BET"
    assert decision.min_acceptable_odds == Decimal("1.75")


def test_missing_sharp_reference():
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "MISSING_SHARP_REFERENCE"


def test_incomplete_market_one_reference_side():
    ref_p1 = _snapshot(odds_snapshot_id=10, selection_type="PLAYER_1", price="2.00")
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[ref_p1],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "INCOMPLETE_MARKET"
    assert decision.lineage == (("REFERENCE_PLAYER_1", 10),)


def test_crossed_reference_market():
    ref_p1 = _snapshot(odds_snapshot_id=10, selection_type="PLAYER_1", price="3.00")
    ref_p2 = _snapshot(odds_snapshot_id=11, selection_type="PLAYER_2", price="3.00")
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[ref_p1, ref_p2],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "CROSSED_MARKET"


def test_stale_reference_market():
    ref_p1 = _snapshot(odds_snapshot_id=10, selection_type="PLAYER_1", price="2.00", source_observed_at=_utc(9, 0))
    ref_p2 = _snapshot(odds_snapshot_id=11, selection_type="PLAYER_2", price="2.00", source_observed_at=_utc(9, 0))
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[ref_p1, ref_p2],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "STALE_MARKET"


def test_unsupported_tier():
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier=None,
        sharp_reference=SHARP,
        odds_snapshots=[],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "UNSUPPORTED_TIER"


def test_suspended_reference_market():
    ref_p1 = _snapshot(odds_snapshot_id=10, selection_type="PLAYER_1", price="2.00", market_status="SUSPENDED")
    ref_p2 = _snapshot(odds_snapshot_id=11, selection_type="PLAYER_2", price="2.00", market_status="SUSPENDED")
    decision = evaluate_market_match(
        manager_row=_manager_row(side=1, prob="0.60"),
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=[ref_p1, ref_p2],
        decision_as_of=_utc(10, 5),
    )
    assert decision.decision_status == "NO_BET"
    assert decision.reason_code == "MARKET_SUSPENDED"
