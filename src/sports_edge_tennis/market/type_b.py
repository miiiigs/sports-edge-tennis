from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg

from sports_edge_tennis.market.contracts import (
    MarketExecutionError,
    MarketPolicyV1,
    OddsSnapshotV1,
    SharpReferenceSpec,
)
from sports_edge_tennis.market.persistence import (
    load_competition_tier,
    load_match_winner_odds,
)
from sports_edge_tennis.market.policy import (
    is_fresh,
    resolve_threshold,
    validate_sharp_reference,
)
from sports_edge_tennis.market.pricing import (
    booksum,
    edge_pct,
    minimum_acceptable_price,
    power_devig,
)


@dataclass(frozen=True)
class TypeBValueSignal:
    match_id: int
    selection_side: int
    sharp_fair_probability: Decimal
    sharp_fair_odds: Decimal
    sharp_reference_price: Decimal
    offered_price: Decimal
    price_source_system: str
    bookmaker_code: str
    edge_pct: Decimal
    threshold: Decimal
    min_acceptable_odds: Decimal
    reference_player_1_snapshot_id: int
    reference_player_2_snapshot_id: int
    offered_snapshot_id: int


def _latest_sharp_pair(
    odds_snapshots: list[OddsSnapshotV1],
    sharp_reference: SharpReferenceSpec,
) -> tuple[OddsSnapshotV1, OddsSnapshotV1] | None:
    sharp = [
        row
        for row in odds_snapshots
        if row.source_system == sharp_reference.source_system
        and row.bookmaker_code == sharp_reference.bookmaker_code
        and row.source_snapshot_id is not None
    ]
    groups: dict[tuple[str, datetime], dict[str, OddsSnapshotV1]] = {}
    for row in sharp:
        key = (row.source_snapshot_id, row.source_observed_at)
        groups.setdefault(key, {})[row.selection_type] = row

    complete = [
        (key, sides)
        for key, sides in groups.items()
        if "PLAYER_1" in sides and "PLAYER_2" in sides
    ]
    if not complete:
        return None
    complete.sort(
        key=lambda item: (
            item[0][1],
            max(item[1]["PLAYER_1"].as_of, item[1]["PLAYER_2"].as_of),
            -min(
                item[1]["PLAYER_1"].odds_snapshot_id,
                item[1]["PLAYER_2"].odds_snapshot_id,
            ),
        ),
        reverse=True,
    )
    sides = complete[0][1]
    return sides["PLAYER_1"], sides["PLAYER_2"]


def _best_offered(
    odds_snapshots: list[OddsSnapshotV1],
    *,
    selection_side: int,
    decision_as_of: datetime,
    policy: MarketPolicyV1,
    sharp_reference: SharpReferenceSpec,
) -> OddsSnapshotV1 | None:
    selection_type = "PLAYER_1" if selection_side == 1 else "PLAYER_2"
    candidates = [
        row
        for row in odds_snapshots
        if row.selection_type == selection_type
        and row.market_status == "OPEN"
        and row.decimal_price > 1
        and is_fresh(
            row.source_observed_at,
            decision_as_of,
            policy.odds_max_staleness_minutes,
        )
        and not (
            row.source_system == sharp_reference.source_system
            and row.bookmaker_code == sharp_reference.bookmaker_code
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row.decimal_price, -row.odds_snapshot_id))


def evaluate_type_b_match(
    *,
    match_id: int,
    policy: MarketPolicyV1,
    competition_tier: str | None,
    sharp_reference: SharpReferenceSpec,
    odds_snapshots: list[OddsSnapshotV1],
    decision_as_of: datetime,
) -> tuple[TypeBValueSignal, ...]:
    """Return independent sharp-vs-soft price-dispersion value signals.

    Type B deliberately does not consume Agent or Manager outputs. A signal fires
    when a best available offered price has expected value at least the locked
    tier threshold using Pinnacle's de-vigged fair probability.
    """
    validate_sharp_reference(policy, sharp_reference)
    if competition_tier is None:
        return ()
    if policy.default_devig_method != "POWER":
        raise MarketExecutionError(
            f"unsupported default_devig_method {policy.default_devig_method!r}; Type B v1 implements POWER only"
        )

    sharp_pair = _latest_sharp_pair(odds_snapshots, sharp_reference)
    if sharp_pair is None:
        return ()
    ref_p1, ref_p2 = sharp_pair
    if ref_p1.decimal_price <= 1 or ref_p2.decimal_price <= 1:
        return ()
    if ref_p1.market_status != "OPEN" or ref_p2.market_status != "OPEN":
        return ()
    if booksum(ref_p1.decimal_price, ref_p2.decimal_price) < 1:
        return ()
    ttl = policy.odds_max_staleness_minutes
    if not is_fresh(ref_p1.source_observed_at, decision_as_of, ttl):
        return ()
    if not is_fresh(ref_p2.source_observed_at, decision_as_of, ttl):
        return ()

    p1_fair, p2_fair = power_devig(ref_p1.decimal_price, ref_p2.decimal_price)
    signals: list[TypeBValueSignal] = []
    for side, p_fair, reference_price in (
        (1, p1_fair, ref_p1.decimal_price),
        (2, p2_fair, ref_p2.decimal_price),
    ):
        offered = _best_offered(
            odds_snapshots,
            selection_side=side,
            decision_as_of=decision_as_of,
            policy=policy,
            sharp_reference=sharp_reference,
        )
        if offered is None:
            continue

        threshold = resolve_threshold(
            policy,
            competition_tier,
            ref_p1.decimal_price,
            ref_p2.decimal_price,
        )
        if offered.decimal_price > policy.long_price_threshold:
            threshold = max(threshold, policy.long_price_min_edge_pct)

        edge = edge_pct(p_fair, offered.decimal_price)
        if edge < threshold:
            continue

        signals.append(
            TypeBValueSignal(
                match_id=match_id,
                selection_side=side,
                sharp_fair_probability=p_fair,
                sharp_fair_odds=Decimal("1") / p_fair,
                sharp_reference_price=reference_price,
                offered_price=offered.decimal_price,
                price_source_system=offered.source_system,
                bookmaker_code=offered.bookmaker_code,
                edge_pct=edge,
                threshold=threshold,
                min_acceptable_odds=minimum_acceptable_price(p_fair, threshold),
                reference_player_1_snapshot_id=ref_p1.odds_snapshot_id,
                reference_player_2_snapshot_id=ref_p2.odds_snapshot_id,
                offered_snapshot_id=offered.odds_snapshot_id,
            )
        )
    return tuple(signals)


def scan_type_b_value_signals(
    conn: psycopg.Connection,
    *,
    match_ids: tuple[int, ...],
    policy: MarketPolicyV1,
    decision_as_of: datetime,
    sharp_reference: SharpReferenceSpec,
) -> tuple[TypeBValueSignal, ...]:
    signals: list[TypeBValueSignal] = []
    for match_id in match_ids:
        tier = load_competition_tier(
            conn,
            match_id=match_id,
            decision_as_of=decision_as_of,
        )
        snapshots = load_match_winner_odds(
            conn,
            match_id=match_id,
            decision_as_of=decision_as_of,
        )
        signals.extend(
            evaluate_type_b_match(
                match_id=match_id,
                policy=policy,
                competition_tier=tier,
                sharp_reference=sharp_reference,
                odds_snapshots=snapshots,
                decision_as_of=decision_as_of,
            )
        )
    return tuple(signals)
