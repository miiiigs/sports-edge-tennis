from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg

from sports_edge_tennis.market.contracts import (
    CANONICAL_ROLE_ORDER,
    MARKET_FEATURE_VERSION,
    MARKET_MODEL_VERSION,
    MARKET_POLICY_VERSION_V2,
    MIN_BET_MODEL_PROBABILITY,
    SUPPORTED_TIERS,
    MarketDecisionV1,
    MarketExecutionError,
    MarketIdempotencyMismatchError,
    MarketPolicyV1,
    MarketReasonCode,
    MarketSlateOutcome,
    OddsSnapshotV1,
    PersistedManagerConsensusRow,
    SharpReferenceSpec,
    SnapshotRole,
    quantize_price,
    quantize_probability,
)
from sports_edge_tennis.market.persistence import (
    load_competition_tier,
    load_manager_slate,
    load_market_policy_version,
    load_match_winner_odds,
    load_run_parent,
)
from sports_edge_tennis.market.policy import is_fresh, resolve_threshold, validate_sharp_reference
from sports_edge_tennis.market.pricing import (
    booksum,
    edge_pct,
    minimum_acceptable_price,
    power_devig,
    snapshot_lineage_hash,
)
from sports_edge_tennis.market.staking import quarter_kelly_stake_fraction, quantize_stake_fraction


@dataclass(frozen=True)
class _SharpResolution:
    status: str
    p1: OddsSnapshotV1 | None = None
    p2: OddsSnapshotV1 | None = None
    lineage: tuple[tuple[SnapshotRole, int], ...] = ()


@dataclass(frozen=True)
class _PersistedMarketDecisionTruth:
    decision_id: int
    prediction_run_id: int
    match_id: int
    signal_type: str
    decision_status: str
    reason_code: str
    market_type: str
    selection_type: str | None
    selection_side: int | None
    consensus_probability: Decimal | None
    consensus_fair_odds: Decimal | None
    market_decimal_price: Decimal | None
    sharp_reference_price: Decimal | None
    price_source_system: str | None
    price_bookmaker_code: str | None
    devig_method: str | None
    devig_fair_odds: Decimal | None
    edge_pct: Decimal | None
    min_acceptable_odds: Decimal | None
    staking_rule_version: str | None
    staking_probability: Decimal | None
    staking_decimal_price: Decimal | None
    full_kelly_fraction: Decimal | None
    quarter_kelly_fraction: Decimal | None
    suggested_stake_fraction: Decimal | None
    active_agent_count: int
    abstaining_agent_count: int
    directional_disagreement: bool
    probability_spread: Decimal | None
    model_version: str
    feature_version: str
    manager_consensus_result_id: int
    market_policy_version_id: int
    as_of: datetime
    snapshot_lineage_hash: str
    lineage: tuple[tuple[SnapshotRole, int], ...]

    @property
    def natural_key(self) -> tuple[int, int, datetime, str]:
        return (
            self.manager_consensus_result_id,
            self.market_policy_version_id,
            self.as_of,
            self.snapshot_lineage_hash,
        )


def _resolve_sharp_pair(
    odds_snapshots: list[OddsSnapshotV1],
    sharp_reference: SharpReferenceSpec,
) -> _SharpResolution:
    sharp = [
        o
        for o in odds_snapshots
        if o.source_system == sharp_reference.source_system
        and o.bookmaker_code == sharp_reference.bookmaker_code
        and o.source_snapshot_id is not None
    ]
    if not sharp:
        return _SharpResolution(status="MISSING_SHARP_REFERENCE")

    groups: dict[tuple[str, datetime], dict[str, OddsSnapshotV1]] = {}
    for o in sharp:
        key = (o.source_snapshot_id, o.source_observed_at)
        groups.setdefault(key, {})[o.selection_type] = o

    complete = [
        (key, sides)
        for key, sides in groups.items()
        if "PLAYER_1" in sides and "PLAYER_2" in sides
    ]
    if complete:
        complete.sort(
            key=lambda kv: (
                kv[0][1],
                max(kv[1]["PLAYER_1"].as_of, kv[1]["PLAYER_2"].as_of),
                -min(kv[1]["PLAYER_1"].odds_snapshot_id, kv[1]["PLAYER_2"].odds_snapshot_id),
            ),
            reverse=True,
        )
        _, sides = complete[0]
        p1 = sides["PLAYER_1"]
        p2 = sides["PLAYER_2"]
        return _SharpResolution(
            status="PAIR",
            p1=p1,
            p2=p2,
            lineage=(
                ("REFERENCE_PLAYER_1", p1.odds_snapshot_id),
                ("REFERENCE_PLAYER_2", p2.odds_snapshot_id),
            ),
        )

    best_per_side: dict[str, OddsSnapshotV1] = {}
    for side in ("PLAYER_1", "PLAYER_2"):
        side_rows = [o for o in sharp if o.selection_type == side]
        if side_rows:
            best_per_side[side] = max(
                side_rows, key=lambda o: (o.source_observed_at, -o.odds_snapshot_id)
            )

    lineage_entries: list[tuple[SnapshotRole, int]] = []
    if "PLAYER_1" in best_per_side:
        lineage_entries.append(("REFERENCE_PLAYER_1", best_per_side["PLAYER_1"].odds_snapshot_id))
    if "PLAYER_2" in best_per_side:
        lineage_entries.append(("REFERENCE_PLAYER_2", best_per_side["PLAYER_2"].odds_snapshot_id))
    return _SharpResolution(status="INCOMPLETE_MARKET", lineage=tuple(lineage_entries))


def _resolve_offered(
    odds_snapshots: list[OddsSnapshotV1],
    selected_side: int,
    decision_as_of: datetime,
    policy: MarketPolicyV1,
    sharp_reference: SharpReferenceSpec,
) -> OddsSnapshotV1 | None:
    selection_type = "PLAYER_1" if selected_side == 1 else "PLAYER_2"
    candidates = [
        o
        for o in odds_snapshots
        if o.selection_type == selection_type
        and o.market_status == "OPEN"
        and o.decimal_price > 1
        and is_fresh(o.source_observed_at, decision_as_of, policy.odds_max_staleness_minutes)
        and not (
            o.source_system == sharp_reference.source_system
            and o.bookmaker_code == sharp_reference.bookmaker_code
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda o: (o.decimal_price, -o.odds_snapshot_id))


def evaluate_market_match(
    *,
    manager_row: PersistedManagerConsensusRow,
    policy: MarketPolicyV1,
    competition_tier: str | None,
    sharp_reference: SharpReferenceSpec,
    odds_snapshots: list[OddsSnapshotV1],
    decision_as_of: datetime,
) -> MarketDecisionV1:
    validate_sharp_reference(policy, sharp_reference)

    def no_bet(reason: MarketReasonCode, lineage: tuple[tuple[SnapshotRole, int], ...] = ()) -> MarketDecisionV1:
        return MarketDecisionV1(
            manager_consensus_result_id=manager_row.manager_consensus_result_id,
            prediction_run_id=manager_row.prediction_run_id,
            match_id=manager_row.match_id,
            market_policy_version_id=policy.market_policy_version_id,
            decision_as_of=decision_as_of,
            decision_status="NO_BET",
            reason_code=reason,
            selected_side=None,
            consensus_probability=_audit_probability(manager_row),
            edge_pct=None,
            min_acceptable_odds=None,
            staking_rule_version=None,
            staking_probability=None,
            staking_decimal_price=None,
            full_kelly_fraction=None,
            quarter_kelly_fraction=None,
            suggested_stake_fraction=None,
            market_decimal_price=None,
            sharp_reference_price=None,
            price_source_system=None,
            price_bookmaker_code=None,
            devig_method=None,
            devig_fair_odds=None,
            active_agent_count=manager_row.active_agent_count,
            abstaining_agent_count=manager_row.abstaining_agent_count,
            probability_spread=manager_row.probability_spread,
            lineage=lineage,
            snapshot_lineage_hash=snapshot_lineage_hash(lineage),
        )

    if manager_row.manager_status != "CONSENSUS":
        return no_bet("NOT_MANAGER_ELIGIBLE")

    if competition_tier not in SUPPORTED_TIERS:
        return no_bet("UNSUPPORTED_TIER")

    selected_side = manager_row.selected_side
    if selected_side not in (1, 2):
        raise MarketExecutionError("CONSENSUS Manager row must carry selected_side 1 or 2")

    p_model = manager_row.consensus_probability
    if p_model is None:
        raise MarketExecutionError("CONSENSUS Manager row must carry consensus_probability")

    sharp = _resolve_sharp_pair(odds_snapshots, sharp_reference)
    if sharp.status != "PAIR":
        return no_bet(
            "MISSING_SHARP_REFERENCE" if sharp.status == "MISSING_SHARP_REFERENCE" else "INCOMPLETE_MARKET",
            sharp.lineage,
        )

    ref_p1 = sharp.p1
    ref_p2 = sharp.p2
    assert ref_p1 is not None and ref_p2 is not None

    if ref_p1.decimal_price <= 1 or ref_p2.decimal_price <= 1:
        return no_bet("INVALID_PRICE", sharp.lineage)

    if ref_p1.market_status != "OPEN" or ref_p2.market_status != "OPEN":
        return no_bet("MARKET_SUSPENDED", sharp.lineage)

    if booksum(ref_p1.decimal_price, ref_p2.decimal_price) < 1:
        return no_bet("CROSSED_MARKET", sharp.lineage)

    ttl = policy.odds_max_staleness_minutes
    if not is_fresh(ref_p1.source_observed_at, decision_as_of, ttl) or not is_fresh(
        ref_p2.source_observed_at, decision_as_of, ttl
    ):
        return no_bet("STALE_MARKET", sharp.lineage)

    # Current live betting rule: a price edge can never turn a sub-60% model
    # selection into an actionable bet. Keep the Manager pick available for
    # model tracking, but fail closed at the Market layer before value/staking.
    if (
        policy.policy_version == MARKET_POLICY_VERSION_V2
        and p_model < MIN_BET_MODEL_PROBABILITY
    ):
        return no_bet("PROBABILITY_FLOOR", sharp.lineage)

    if policy.default_devig_method != "POWER":
        raise MarketExecutionError(
            f"unsupported default_devig_method {policy.default_devig_method!r}; V1 implements POWER only"
        )

    p1_fair, p2_fair = power_devig(ref_p1.decimal_price, ref_p2.decimal_price)
    p_market = p1_fair if selected_side == 1 else p2_fair
    reference_price = ref_p1.decimal_price if selected_side == 1 else ref_p2.decimal_price

    if not p_model > p_market:
        return no_bet("NO_MODEL_EDGE_VS_SHARP", sharp.lineage)

    offered = _resolve_offered(odds_snapshots, selected_side, decision_as_of, policy, sharp_reference)
    if offered is None:
        return no_bet("MISSING_MARKET", sharp.lineage)

    threshold = resolve_threshold(policy, competition_tier, ref_p1.decimal_price, ref_p2.decimal_price)
    edge = edge_pct(p_model, offered.decimal_price)
    min_price = minimum_acceptable_price(p_model, threshold)

    if edge < threshold:
        lineage = (
            ("REFERENCE_PLAYER_1", ref_p1.odds_snapshot_id),
            ("REFERENCE_PLAYER_2", ref_p2.odds_snapshot_id),
            ("OFFERED_SELECTION", offered.odds_snapshot_id),
        )
        return no_bet("EDGE_BELOW_THRESHOLD", lineage)

    lineage: tuple[tuple[SnapshotRole, int], ...] = (
        ("REFERENCE_PLAYER_1", ref_p1.odds_snapshot_id),
        ("REFERENCE_PLAYER_2", ref_p2.odds_snapshot_id),
        ("OFFERED_SELECTION", offered.odds_snapshot_id),
    )
    stake = quarter_kelly_stake_fraction(
        probability=p_model,
        decimal_price=offered.decimal_price,
    )
    return MarketDecisionV1(
        manager_consensus_result_id=manager_row.manager_consensus_result_id,
        prediction_run_id=manager_row.prediction_run_id,
        match_id=manager_row.match_id,
        market_policy_version_id=policy.market_policy_version_id,
        decision_as_of=decision_as_of,
        decision_status="BET",
        reason_code="NONE",
        selected_side=selected_side,
        consensus_probability=p_model,
        edge_pct=edge,
        min_acceptable_odds=min_price,
        staking_rule_version=stake.staking_rule_version,
        staking_probability=stake.staking_probability,
        staking_decimal_price=stake.staking_decimal_price,
        full_kelly_fraction=stake.full_kelly_fraction,
        quarter_kelly_fraction=stake.quarter_kelly_fraction,
        suggested_stake_fraction=stake.suggested_stake_fraction,
        market_decimal_price=offered.decimal_price,
        sharp_reference_price=reference_price,
        price_source_system=offered.source_system,
        price_bookmaker_code=offered.bookmaker_code,
        devig_method="POWER",
        devig_fair_odds=Decimal("1") / p_market,
        active_agent_count=manager_row.active_agent_count,
        abstaining_agent_count=manager_row.abstaining_agent_count,
        probability_spread=manager_row.probability_spread,
        lineage=lineage,
        snapshot_lineage_hash=snapshot_lineage_hash(lineage),
    )


def _audit_probability(manager_row: PersistedManagerConsensusRow) -> Decimal | None:
    return manager_row.consensus_probability


def _selection_type(selected_side: int | None) -> str | None:
    if selected_side == 1:
        return "PLAYER_1"
    if selected_side == 2:
        return "PLAYER_2"
    return None


def _canonical_lineage_mapping(
    lineage: tuple[tuple[SnapshotRole, int], ...],
) -> tuple[tuple[SnapshotRole, int], ...]:
    role_order = {role: index for index, role in enumerate(CANONICAL_ROLE_ORDER)}
    return tuple(sorted(lineage, key=lambda entry: role_order[entry[0]]))


def _canonical_decision_truth(decision: MarketDecisionV1, decision_id: int = 0) -> _PersistedMarketDecisionTruth:
    consensus_probability = (
        quantize_probability(decision.consensus_probability)
        if decision.consensus_probability is not None
        else None
    )
    consensus_fair_odds = (
        quantize_price(Decimal("1") / decision.consensus_probability)
        if decision.decision_status == "BET" and decision.consensus_probability is not None
        else None
    )
    return _PersistedMarketDecisionTruth(
        decision_id=decision_id,
        prediction_run_id=decision.prediction_run_id,
        match_id=decision.match_id,
        signal_type="TYPE_A_MODEL_EDGE",
        decision_status=decision.decision_status,
        reason_code=decision.reason_code,
        market_type="MATCH_WINNER",
        selection_type=_selection_type(decision.selected_side),
        selection_side=decision.selected_side,
        consensus_probability=consensus_probability,
        consensus_fair_odds=consensus_fair_odds,
        market_decimal_price=(
            quantize_price(decision.market_decimal_price)
            if decision.market_decimal_price is not None
            else None
        ),
        sharp_reference_price=(
            quantize_price(decision.sharp_reference_price)
            if decision.sharp_reference_price is not None
            else None
        ),
        price_source_system=decision.price_source_system,
        price_bookmaker_code=decision.price_bookmaker_code,
        devig_method=decision.devig_method,
        devig_fair_odds=(
            quantize_price(decision.devig_fair_odds)
            if decision.devig_fair_odds is not None
            else None
        ),
        edge_pct=quantize_price(decision.edge_pct) if decision.edge_pct is not None else None,
        min_acceptable_odds=(
            quantize_price(decision.min_acceptable_odds)
            if decision.min_acceptable_odds is not None
            else None
        ),
        staking_rule_version=decision.staking_rule_version,
        staking_probability=(
            quantize_probability(decision.staking_probability)
            if decision.staking_probability is not None
            else None
        ),
        staking_decimal_price=(
            quantize_price(decision.staking_decimal_price)
            if decision.staking_decimal_price is not None
            else None
        ),
        full_kelly_fraction=(
            quantize_stake_fraction(decision.full_kelly_fraction)
            if decision.full_kelly_fraction is not None
            else None
        ),
        quarter_kelly_fraction=(
            quantize_stake_fraction(decision.quarter_kelly_fraction)
            if decision.quarter_kelly_fraction is not None
            else None
        ),
        suggested_stake_fraction=(
            quantize_stake_fraction(decision.suggested_stake_fraction)
            if decision.suggested_stake_fraction is not None
            else None
        ),
        active_agent_count=decision.active_agent_count,
        abstaining_agent_count=decision.abstaining_agent_count,
        directional_disagreement=False,
        probability_spread=(
            quantize_probability(decision.probability_spread)
            if decision.probability_spread is not None
            else None
        ),
        model_version=MARKET_MODEL_VERSION,
        feature_version=MARKET_FEATURE_VERSION,
        manager_consensus_result_id=decision.manager_consensus_result_id,
        market_policy_version_id=decision.market_policy_version_id,
        as_of=decision.decision_as_of,
        snapshot_lineage_hash=decision.snapshot_lineage_hash,
        lineage=_canonical_lineage_mapping(decision.lineage),
    )


def _persist_decision(
    conn: psycopg.Connection,
    decision: MarketDecisionV1,
    market_execution_now: datetime,
) -> int:
    truth = _canonical_decision_truth(decision)

    return conn.execute(
        """
        INSERT INTO decisions (
            match_id,
            prediction_run_id,
            signal_type,
            decision_status,
            reason_code,
            market_type,
            selection_type,
            selection_side,
            line_value,
            consensus_probability,
            consensus_fair_odds,
            market_decimal_price,
            sharp_reference_price,
            price_source_system,
            price_bookmaker_code,
            devig_method,
            devig_fair_odds,
            edge_pct,
            min_acceptable_odds,
            staking_rule_version,
            staking_probability,
            staking_decimal_price,
            full_kelly_fraction,
            quarter_kelly_fraction,
            suggested_stake_fraction,
            active_agent_count,
            abstaining_agent_count,
            directional_disagreement,
            probability_spread,
            model_version,
            feature_version,
            as_of,
            prediction_locked_at,
            created_at,
            manager_consensus_result_id,
            market_policy_version_id,
            snapshot_lineage_hash
        )
        VALUES (
            %s, %s, 'TYPE_A_MODEL_EDGE', %s, %s, 'MATCH_WINNER', %s, %s, NULL,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, FALSE, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s
        )
        RETURNING decision_id
        """,
        (
            decision.match_id,
            decision.prediction_run_id,
            decision.decision_status,
            decision.reason_code,
            truth.selection_type,
            truth.selection_side,
            truth.consensus_probability,
            truth.consensus_fair_odds,
            truth.market_decimal_price,
            truth.sharp_reference_price,
            decision.price_source_system,
            decision.price_bookmaker_code,
            decision.devig_method,
            truth.devig_fair_odds,
            truth.edge_pct,
            truth.min_acceptable_odds,
            truth.staking_rule_version,
            truth.staking_probability,
            truth.staking_decimal_price,
            truth.full_kelly_fraction,
            truth.quarter_kelly_fraction,
            truth.suggested_stake_fraction,
            decision.active_agent_count,
            decision.abstaining_agent_count,
            truth.probability_spread,
            MARKET_MODEL_VERSION,
            MARKET_FEATURE_VERSION,
            decision.decision_as_of,
            market_execution_now,
            market_execution_now,
            decision.manager_consensus_result_id,
            decision.market_policy_version_id,
            decision.snapshot_lineage_hash,
        ),
    ).fetchone()[0]


def _persist_lineage(
    conn: psycopg.Connection,
    decision_id: int,
    lineage: tuple[tuple[SnapshotRole, int], ...],
) -> None:
    for role, odds_snapshot_id in lineage:
        conn.execute(
            """
            INSERT INTO decision_odds_snapshots (decision_id, odds_snapshot_id, snapshot_role)
            VALUES (%s, %s, %s)
            """,
            (decision_id, odds_snapshot_id, role),
        )


def _load_existing_market_decisions(
    conn: psycopg.Connection,
    prediction_run_id: int,
) -> dict[tuple[int, int, datetime, str], _PersistedMarketDecisionTruth]:
    rows = conn.execute(
        """
        SELECT
            decision_id,
            prediction_run_id,
            match_id,
            signal_type::text,
            decision_status::text,
            reason_code::text,
            market_type::text,
            selection_type::text,
            selection_side,
            consensus_probability,
            consensus_fair_odds,
            market_decimal_price,
            sharp_reference_price,
            price_source_system,
            price_bookmaker_code,
            devig_method,
            devig_fair_odds,
            edge_pct,
            min_acceptable_odds,
            staking_rule_version,
            staking_probability,
            staking_decimal_price,
            full_kelly_fraction,
            quarter_kelly_fraction,
            suggested_stake_fraction,
            active_agent_count,
            abstaining_agent_count,
            directional_disagreement,
            probability_spread,
            model_version,
            feature_version,
            manager_consensus_result_id,
            market_policy_version_id,
            as_of,
            snapshot_lineage_hash
        FROM decisions
        WHERE prediction_run_id = %s
          AND manager_consensus_result_id IS NOT NULL
        """,
        (prediction_run_id,),
    ).fetchall()
    decisions = {
        row[0]: _PersistedMarketDecisionTruth(*row, lineage=())
        for row in rows
    }
    if not decisions:
        return {}

    lineage_rows = conn.execute(
        """
        SELECT decision_id, snapshot_role::text, odds_snapshot_id
        FROM decision_odds_snapshots
        WHERE decision_id = ANY(%s)
        ORDER BY decision_id, snapshot_role
        """,
        (list(decisions),),
    ).fetchall()
    lineages: dict[int, list[tuple[SnapshotRole, int]]] = {decision_id: [] for decision_id in decisions}
    for decision_id, role, odds_snapshot_id in lineage_rows:
        lineages[decision_id].append((role, odds_snapshot_id))

    hydrated = {
        decision_id: _PersistedMarketDecisionTruth(
            **{
                **truth.__dict__,
                "lineage": _canonical_lineage_mapping(tuple(lineages[decision_id])),
            }
        )
        for decision_id, truth in decisions.items()
    }
    return {truth.natural_key: truth for truth in hydrated.values()}


def evaluate_and_persist_market_slate(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    market_policy_version_id: int,
    decision_as_of: datetime,
    market_execution_now: datetime,
    sharp_reference: SharpReferenceSpec,
) -> MarketSlateOutcome:
    if market_execution_now < decision_as_of:
        raise MarketExecutionError("market_execution_now must be >= decision_as_of")

    with conn.transaction():
        run_status, contract_version, run_as_of, finalized_at = load_run_parent(
            conn, prediction_run_id=prediction_run_id
        )
        if contract_version != "predictive_agent_result_v1":
            raise MarketExecutionError("Market consumes only predictive_agent_result_v1 runs")
        if run_status != "COMPLETED":
            raise MarketExecutionError("Market consumes only COMPLETED prediction runs")
        if finalized_at is None:
            raise MarketExecutionError("Market consumes only finalized prediction runs")
        if decision_as_of < run_as_of:
            raise MarketExecutionError("decision_as_of must be >= prediction_run.as_of")

        policy = load_market_policy_version(conn, market_policy_version_id=market_policy_version_id)
        if policy.type_b_enabled:
            raise MarketExecutionError("Type B is disabled in V1")
        validate_sharp_reference(policy, sharp_reference)

        manager_rows = load_manager_slate(conn, prediction_run_id=prediction_run_id)
        if not manager_rows:
            return MarketSlateOutcome(
                prediction_run_id=prediction_run_id,
                manager_result_count=0,
                new_decision_count=0,
                existing_decision_count=0,
                qualified_bet_count=0,
                status="EMPTY_SLATE",
            )

        decisions: list[MarketDecisionV1] = []
        for manager_row in manager_rows:
            competition_tier = load_competition_tier(
                conn, match_id=manager_row.match_id, decision_as_of=decision_as_of
            )
            odds_snapshots = load_match_winner_odds(
                conn, match_id=manager_row.match_id, decision_as_of=decision_as_of
            )
            decisions.append(
                evaluate_market_match(
                    manager_row=manager_row,
                    policy=policy,
                    competition_tier=competition_tier,
                    sharp_reference=sharp_reference,
                    odds_snapshots=odds_snapshots,
                    decision_as_of=decision_as_of,
                )
            )

        existing = _load_existing_market_decisions(conn, prediction_run_id)
        before_count = len(existing)

        new_decisions: list[MarketDecisionV1] = []
        existing_count = 0
        for decision in decisions:
            key = (
                decision.manager_consensus_result_id,
                decision.market_policy_version_id,
                decision.decision_as_of,
                decision.snapshot_lineage_hash,
            )
            if key in existing:
                expected = _canonical_decision_truth(decision, decision_id=existing[key].decision_id)
                if existing[key] != expected:
                    raise MarketIdempotencyMismatchError(
                        "persisted Market truth differs from the computed canonical decision"
                    )
                existing_count += 1
            else:
                new_decisions.append(decision)

        for decision in new_decisions:
            decision_id = _persist_decision(conn, decision, market_execution_now)
            _persist_lineage(conn, decision_id, decision.lineage)

        after_count = _count_market_decisions(conn, prediction_run_id)
        if after_count != before_count + len(new_decisions):
            raise MarketExecutionError(
                f"Market slate persistence produced an unexpected row count "
                f"({after_count} != {before_count + len(new_decisions)})"
            )

        qualified_bets = sum(1 for d in decisions if d.decision_status == "BET")
        status = "PERSISTED" if new_decisions else "IDEMPOTENT_SUCCESS"

        return MarketSlateOutcome(
            prediction_run_id=prediction_run_id,
            manager_result_count=len(decisions),
            new_decision_count=len(new_decisions),
            existing_decision_count=existing_count,
            qualified_bet_count=qualified_bets,
            status=status,
        )


def _count_market_decisions(conn: psycopg.Connection, prediction_run_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM decisions WHERE prediction_run_id = %s AND manager_consensus_result_id IS NOT NULL",
        (prediction_run_id,),
    ).fetchone()[0]
