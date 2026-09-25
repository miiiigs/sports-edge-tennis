"""Shared builders for replay tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sports_edge_tennis.replay.contracts import (
    REPLAY_ID,
    ModelPickLedgerRow,
    ReplayRunResult,
    ValueBetLedgerRow,
)

UTC = timezone.utc


def utc(hour: int, minute: int = 0, day: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def model_pick(
    *,
    match_id: int,
    run_sequence: int = 1,
    decision_as_of: datetime | None = None,
    classification: str = "MANAGER_QUALIFIED",
    actual_result: str | None = "PENDING",
    selected_side: int | None = 1,
) -> ModelPickLedgerRow:
    return ModelPickLedgerRow(
        replay_id=REPLAY_ID,
        run_id=f"run-{run_sequence}",
        run_sequence=run_sequence,
        sporting_match_id=match_id,
        provider_event_ids=(),
        decision_as_of=decision_as_of or utc(11),
        prediction_as_of=utc(6),
        player_a_id=1,
        player_b_id=2,
        a1_state="PREDICTED",
        a1_side=1,
        a1_probability=Decimal("0.55"),
        a1_config="a1",
        a2_state="PREDICTED",
        a2_side=1,
        a2_probability=Decimal("0.56"),
        a2_config="a2",
        a3_state="PREDICTED",
        a3_side=1,
        a3_probability=Decimal("0.54"),
        a3_reason=None,
        a3_config="a3",
        manager_version="v1",
        manager_outcome="CONSENSUS",
        manager_reason="NONE",
        selected_side=selected_side,
        consensus_probability=Decimal("0.55"),
        classification=classification,  # type: ignore[arg-type]
        pit_status="PIT_COMPLETE",
        identity_status="RESOLVED",
        surface_status="RESOLVED",
        actual_result=actual_result,  # type: ignore[arg-type]
    )


def value_bet(
    *,
    match_id: int,
    run_sequence: int = 1,
    decision_as_of: datetime | None = None,
    actual_result: str = "PENDING",
    units: Decimal | None = None,
    price: Decimal = Decimal("2.00"),
) -> ValueBetLedgerRow:
    decision = decision_as_of or utc(11)
    return ValueBetLedgerRow(
        replay_id=REPLAY_ID,
        run_id=f"run-{run_sequence}",
        run_sequence=run_sequence,
        sporting_match_id=match_id,
        provider_event_ids=(),
        first_model_pick_decision_as_of=None,
        decision_as_of=decision,
        prediction_as_of=decision - timedelta(hours=5),
        selected_side=1,
        consensus_probability=Decimal("0.55"),
        bookmaker="B365",
        price_source_system="OFFERED",
        market_observed_at=decision,
        offered_decimal_price=price,
        reference_source_system="SHARP",
        reference_bookmaker="PINNACLE",
        reference_odds=Decimal("2.10"),
        reference_observed_at=decision,
        edge_pct=Decimal("0.10"),
        min_acceptable_odds=Decimal("1.90"),
        market_status="BET",
        market_reason=None,
        veto_status="PASSED",
        veto_trigger=None,
        stake_fraction=Decimal("0.020000"),
        staking_rule_version="QUARTER_KELLY_CAP_2_V1",
        pit_status="PIT_COMPLETE",
        actual_result=actual_result,  # type: ignore[arg-type]
        units=units,
        clv_pct=None,
        clv_status="NOT_AVAILABLE_PIT",
    )


def run_result(run_sequence: int, *, model_picks=(), value_bets=()) -> ReplayRunResult:
    decision = utc(11) + timedelta(hours=run_sequence)
    return ReplayRunResult(
        run_id=f"run-{run_sequence}",
        run_sequence=run_sequence,
        prediction_as_of=utc(6) + timedelta(hours=run_sequence),
        market_as_of=decision,
        model_picks=tuple(model_picks),
        value_bets=tuple(value_bets),
        exclusions=(),
        opportunities=(),
    )
