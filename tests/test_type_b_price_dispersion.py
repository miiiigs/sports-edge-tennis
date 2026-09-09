from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sports_edge_tennis.market.contracts import (
    MarketPolicyV1,
    OddsSnapshotV1,
    SharpReferenceSpec,
)
from sports_edge_tennis.market.type_b import evaluate_type_b_match


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


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


def _snapshot(
    snapshot_id: int,
    *,
    source_system: str,
    bookmaker_code: str,
    side: int,
    price: str,
    source_snapshot_id: str,
    observed_at: datetime = NOW,
) -> OddsSnapshotV1:
    return OddsSnapshotV1(
        odds_snapshot_id=snapshot_id,
        match_id=101,
        source_system=source_system,
        bookmaker_code=bookmaker_code,
        market_type="MATCH_WINNER",
        selection_type="PLAYER_1" if side == 1 else "PLAYER_2",
        decimal_price=Decimal(price),
        market_status="OPEN",
        booksum=None,
        source_snapshot_id=source_snapshot_id,
        source_observed_at=observed_at,
        as_of=observed_at,
    )


def test_type_b_fires_without_manager_when_soft_price_beats_sharp_fair_by_threshold():
    snapshots = [
        _snapshot(1, source_system="SHARP", bookmaker_code="PINNACLE", side=1, price="2.00", source_snapshot_id="sharp-1"),
        _snapshot(2, source_system="SHARP", bookmaker_code="PINNACLE", side=2, price="2.00", source_snapshot_id="sharp-1"),
        _snapshot(3, source_system="OFFERED", bookmaker_code="SOFT_A", side=1, price="2.11", source_snapshot_id="soft-a"),
        _snapshot(4, source_system="OFFERED", bookmaker_code="SOFT_B", side=1, price="2.08", source_snapshot_id="soft-b"),
        _snapshot(5, source_system="OFFERED", bookmaker_code="SOFT_A", side=2, price="2.00", source_snapshot_id="soft-a"),
    ]

    signals = evaluate_type_b_match(
        match_id=101,
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=snapshots,
        decision_as_of=NOW,
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.selection_side == 1
    assert signal.bookmaker_code == "SOFT_A"
    assert signal.offered_price == Decimal("2.11")
    assert signal.sharp_fair_probability == Decimal("0.5")
    assert signal.edge_pct == Decimal("0.055")
    assert signal.threshold == Decimal("0.05")
    assert signal.min_acceptable_odds == Decimal("2.1")


def test_type_b_respects_wider_challenger_threshold():
    snapshots = [
        _snapshot(1, source_system="SHARP", bookmaker_code="PINNACLE", side=1, price="2.00", source_snapshot_id="sharp-1"),
        _snapshot(2, source_system="SHARP", bookmaker_code="PINNACLE", side=2, price="2.00", source_snapshot_id="sharp-1"),
        _snapshot(3, source_system="OFFERED", bookmaker_code="SOFT_A", side=1, price="2.11", source_snapshot_id="soft-a"),
    ]

    signals = evaluate_type_b_match(
        match_id=101,
        policy=_policy(),
        competition_tier="CHALLENGER",
        sharp_reference=SHARP,
        odds_snapshots=snapshots,
        decision_as_of=NOW,
    )

    assert signals == ()


def test_type_b_applies_eight_percent_floor_to_long_offered_price():
    snapshots = [
        _snapshot(1, source_system="SHARP", bookmaker_code="PINNACLE", side=1, price="4.00", source_snapshot_id="sharp-1"),
        _snapshot(2, source_system="SHARP", bookmaker_code="PINNACLE", side=2, price="1.333333333333", source_snapshot_id="sharp-1"),
        _snapshot(3, source_system="OFFERED", bookmaker_code="SOFT_A", side=1, price="4.20", source_snapshot_id="soft-a"),
    ]

    signals = evaluate_type_b_match(
        match_id=101,
        policy=_policy(),
        competition_tier="ATP_500",
        sharp_reference=SHARP,
        odds_snapshots=snapshots,
        decision_as_of=NOW,
    )

    assert signals == ()


def test_type_b_fails_closed_on_stale_sharp_reference():
    old = NOW - timedelta(minutes=16)
    snapshots = [
        _snapshot(1, source_system="SHARP", bookmaker_code="PINNACLE", side=1, price="2.00", source_snapshot_id="sharp-1", observed_at=old),
        _snapshot(2, source_system="SHARP", bookmaker_code="PINNACLE", side=2, price="2.00", source_snapshot_id="sharp-1", observed_at=old),
        _snapshot(3, source_system="OFFERED", bookmaker_code="SOFT_A", side=1, price="2.20", source_snapshot_id="soft-a"),
    ]

    signals = evaluate_type_b_match(
        match_id=101,
        policy=_policy(),
        competition_tier="ATP_250",
        sharp_reference=SHARP,
        odds_snapshots=snapshots,
        decision_as_of=NOW,
    )

    assert signals == ()
