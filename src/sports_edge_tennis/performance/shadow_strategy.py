"""Prospective shadow strategy: A1/A2 agree while A3 abstains.

This lane is deliberately isolated from the canonical Manager, Today publication,
and production decisions table. It records every qualifying prospective run,
freezes only the first Market+Veto-qualified position per sporting match, and
reuses production Market, Veto, settlement, staking, and close-price semantics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

import psycopg

from sports_edge_tennis.clv import service as clv_service
from sports_edge_tennis.manager.contracts import consensus_mean, exact_spread
from sports_edge_tennis.market.contracts import (
    PersistedManagerConsensusRow,
    SharpReferenceSpec,
)
from sports_edge_tennis.market.evaluator import evaluate_market_match
from sports_edge_tennis.market.persistence import (
    ensure_market_policy_version_v1,
    load_competition_tier,
    load_match_winner_odds,
)
from sports_edge_tennis.performance.paper_bankroll import (
    STARTING_BANKROLL,
    PaperBankrollSummary,
    PaperBet,
    simulate_paper_bankroll,
)
from sports_edge_tennis.settlement.service import (
    SETTLEMENT_POLICY_VERSION,
    classify_match_winner_settlement,
)
from sports_edge_tennis.veto.integration import _mark_decision_vetoed
from sports_edge_tennis.veto.service import (
    VETO_POLICY_VERSION,
    VetoEvidenceError,
    evaluate_veto_for_decision,
)

STRATEGY_CODE = "A1_A2_AGREE_A3_ABSTAIN_V1"
SHADOW_SCHEMA_VERSION = "shadow_a1_a2_a3_abstain_v1"
CLV_CAPTURE_VERSION = "shadow_close_v1"
CLV_CALCULATION_VERSION = "tennis_clv_v1"
DEFAULT_CLOSE_DELAY = timedelta(hours=2)
_CLV_QUANTIZER = Decimal("0.00000001")

_SCHEMA_DDL = f"""
CREATE TABLE IF NOT EXISTS shadow_strategy_registry (
    strategy_code TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (btrim(strategy_code) <> ''),
    CHECK (btrim(schema_version) <> '')
);

CREATE TABLE IF NOT EXISTS shadow_strategy_runs (
    shadow_run_id BIGSERIAL PRIMARY KEY,
    strategy_code TEXT NOT NULL REFERENCES shadow_strategy_registry(strategy_code),
    prediction_run_id BIGINT NOT NULL REFERENCES prediction_runs(prediction_run_id),
    observed_at TIMESTAMPTZ NOT NULL,
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (strategy_code, prediction_run_id)
);

CREATE TABLE IF NOT EXISTS shadow_strategy_observations (
    shadow_observation_id BIGSERIAL PRIMARY KEY,
    shadow_run_id BIGINT NOT NULL REFERENCES shadow_strategy_runs(shadow_run_id),
    strategy_code TEXT NOT NULL,
    prediction_run_id BIGINT NOT NULL REFERENCES prediction_runs(prediction_run_id),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    observed_at TIMESTAMPTZ NOT NULL,

    agent_1_prediction_id BIGINT NOT NULL REFERENCES predictions(prediction_id),
    agent_2_prediction_id BIGINT NOT NULL REFERENCES predictions(prediction_id),
    agent_3_prediction_id BIGINT NOT NULL REFERENCES predictions(prediction_id),
    selected_side SMALLINT NOT NULL CHECK (selected_side IN (1, 2)),
    agent_1_probability NUMERIC(18, 12) NOT NULL CHECK (agent_1_probability > 0.5 AND agent_1_probability <= 1),
    agent_2_probability NUMERIC(18, 12) NOT NULL CHECK (agent_2_probability > 0.5 AND agent_2_probability <= 1),
    consensus_probability NUMERIC(18, 12) NOT NULL CHECK (consensus_probability > 0.5 AND consensus_probability <= 1),
    probability_spread NUMERIC(18, 12) NOT NULL CHECK (probability_spread >= 0 AND probability_spread <= 1),

    market_policy_version_id BIGINT NOT NULL REFERENCES market_policy_versions(market_policy_version_id),
    market_decision_status TEXT NOT NULL CHECK (market_decision_status IN ('BET', 'NO_BET')),
    market_reason_code TEXT NOT NULL,
    final_shadow_status TEXT NOT NULL CHECK (final_shadow_status IN ('BET', 'NO_BET', 'VETOED', 'BLOCKED')),
    edge_pct NUMERIC(18, 12),
    min_acceptable_odds NUMERIC(18, 8),
    market_decimal_price NUMERIC(18, 8),
    sharp_reference_price NUMERIC(18, 8),
    price_source_system TEXT,
    price_bookmaker_code TEXT,
    staking_rule_version TEXT,
    staking_probability NUMERIC(18, 12),
    staking_decimal_price NUMERIC(18, 8),
    full_kelly_fraction NUMERIC(18, 12),
    quarter_kelly_fraction NUMERIC(18, 12),
    suggested_stake_fraction NUMERIC(18, 12),
    snapshot_lineage_hash TEXT NOT NULL,

    veto_policy_version TEXT,
    veto_status TEXT NOT NULL CHECK (veto_status IN ('NOT_APPLICABLE', 'PASS', 'VETOED', 'ERROR')),
    veto_triggered_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    veto_unavailable_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    veto_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    veto_error TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (strategy_code, prediction_run_id, match_id),
    CHECK (
        final_shadow_status = 'BET'
        OR suggested_stake_fraction IS NULL
    )
);

CREATE TABLE IF NOT EXISTS shadow_strategy_observation_odds (
    shadow_observation_id BIGINT NOT NULL REFERENCES shadow_strategy_observations(shadow_observation_id),
    snapshot_role TEXT NOT NULL CHECK (
        snapshot_role IN ('REFERENCE_PLAYER_1', 'REFERENCE_PLAYER_2', 'OFFERED_SELECTION')
    ),
    odds_snapshot_id BIGINT NOT NULL REFERENCES odds_snapshots(odds_snapshot_id),
    PRIMARY KEY (shadow_observation_id, snapshot_role)
);

CREATE TABLE IF NOT EXISTS shadow_strategy_positions (
    shadow_position_id BIGSERIAL PRIMARY KEY,
    strategy_code TEXT NOT NULL,
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    first_observation_id BIGINT NOT NULL UNIQUE REFERENCES shadow_strategy_observations(shadow_observation_id),
    opened_at TIMESTAMPTZ NOT NULL,
    selected_side SMALLINT NOT NULL CHECK (selected_side IN (1, 2)),
    decimal_price NUMERIC(18, 8) NOT NULL CHECK (decimal_price > 1),
    price_source_system TEXT NOT NULL,
    price_bookmaker_code TEXT NOT NULL,
    offered_odds_snapshot_id BIGINT NOT NULL REFERENCES odds_snapshots(odds_snapshot_id),
    staking_rule_version TEXT NOT NULL,
    suggested_stake_fraction NUMERIC(18, 12) NOT NULL CHECK (
        suggested_stake_fraction > 0 AND suggested_stake_fraction <= 0.02
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (strategy_code, match_id)
);

CREATE TABLE IF NOT EXISTS shadow_strategy_settlements (
    shadow_position_id BIGINT PRIMARY KEY REFERENCES shadow_strategy_positions(shadow_position_id),
    match_result_observation_id BIGINT NOT NULL REFERENCES match_result_observations(match_result_observation_id),
    settlement_policy_version TEXT NOT NULL,
    settlement_status TEXT NOT NULL CHECK (settlement_status IN ('WON', 'LOST', 'VOID', 'CANCELLED')),
    settled_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shadow_strategy_clv (
    shadow_position_id BIGINT PRIMARY KEY REFERENCES shadow_strategy_positions(shadow_position_id),
    capture_version TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    match_start_at TIMESTAMPTZ NOT NULL,
    match_status_at_capture TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    close_status TEXT NOT NULL CHECK (close_status IN ('COMPLETE', 'PARTIAL', 'MISSING')),
    sharp_player_1_snapshot_id BIGINT REFERENCES odds_snapshots(odds_snapshot_id),
    sharp_player_2_snapshot_id BIGINT REFERENCES odds_snapshots(odds_snapshot_id),
    sharp_pair_observed_at TIMESTAMPTZ,
    same_book_player_1_snapshot_id BIGINT REFERENCES odds_snapshots(odds_snapshot_id),
    same_book_player_2_snapshot_id BIGINT REFERENCES odds_snapshots(odds_snapshot_id),
    same_book_pair_observed_at TIMESTAMPTZ,
    closing_same_book_price NUMERIC(18, 8),
    closing_sharp_price NUMERIC(18, 8),
    closing_margin NUMERIC(18, 12),
    clv_pct NUMERIC(18, 12),
    clv_available BOOLEAN NOT NULL,
    unavailable_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (clv_available OR unavailable_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_shadow_strategy_runs_observed
    ON shadow_strategy_runs (strategy_code, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_strategy_observations_match
    ON shadow_strategy_observations (strategy_code, match_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_shadow_strategy_positions_opened
    ON shadow_strategy_positions (strategy_code, opened_at);

DO $$
DECLARE
    table_name TEXT;
    trigger_name TEXT;
BEGIN
    IF to_regprocedure('raise_append_only_mutation()') IS NULL THEN
        RETURN;
    END IF;

    FOREACH table_name IN ARRAY ARRAY[
        'shadow_strategy_registry',
        'shadow_strategy_runs',
        'shadow_strategy_observations',
        'shadow_strategy_observation_odds',
        'shadow_strategy_positions',
        'shadow_strategy_settlements',
        'shadow_strategy_clv'
    ]
    LOOP
        trigger_name := 'trg_' || table_name || '_append_only';
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = trigger_name
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                'FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation()',
                trigger_name,
                table_name
            );
        END IF;
    END LOOP;
END;
$$;
"""


class ShadowStrategyError(RuntimeError):
    """Base error for shadow-only strategy integrity failures."""


class ShadowSettlementConflict(ShadowStrategyError):
    """Immutable shadow settlement conflicts with latest canonical truth."""


@dataclass(frozen=True)
class ShadowCandidate:
    match_id: int
    a1_prediction_id: int
    a2_prediction_id: int
    a3_prediction_id: int
    selected_side: int
    a1_probability: Decimal
    a2_probability: Decimal

    @property
    def consensus_probability(self) -> Decimal:
        return consensus_mean((self.a1_probability, self.a2_probability))

    @property
    def probability_spread(self) -> Decimal:
        return exact_spread((self.a1_probability, self.a2_probability))


@dataclass(frozen=True)
class ShadowRunOutcome:
    prediction_run_id: int
    candidates: int
    observations: int
    market_bets: int
    vetoed: int
    blocked: int
    positions_opened: int


@dataclass(frozen=True)
class ShadowSettlementOutcome:
    eligible: int
    pending: int
    inserted: int
    existing: int
    reconciled: int
    won: int
    lost: int
    void: int
    cancelled: int


@dataclass(frozen=True)
class ShadowClvOutcome:
    eligible: int
    inserted: int
    available: int
    unavailable: int


@dataclass(frozen=True)
class ShadowStrategySummary:
    activation_at: datetime
    observed_runs: int
    observations: int
    positions: int
    settled: int
    wins: int
    losses: int
    voids: int
    cancelled: int
    clv_records: int
    mean_clv: Decimal | None
    bankroll: PaperBankrollSummary


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("shadow timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def ensure_shadow_strategy_schema(conn: psycopg.Connection) -> None:
    conn.execute(_SCHEMA_DDL)


def activate_shadow_strategy(
    conn: psycopg.Connection,
    *,
    activated_at: datetime,
) -> datetime:
    """Prospectively activate once; never move the activation timestamp backward."""
    activated = _aware(activated_at)
    ensure_shadow_strategy_schema(conn)
    conn.execute(
        """
        INSERT INTO shadow_strategy_registry (strategy_code, schema_version, activated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (strategy_code) DO NOTHING
        """,
        (STRATEGY_CODE, SHADOW_SCHEMA_VERSION, activated),
    )
    row = conn.execute(
        """
        SELECT activated_at
        FROM shadow_strategy_registry
        WHERE strategy_code = %s
        """,
        (STRATEGY_CODE,),
    ).fetchone()
    if row is None:
        raise ShadowStrategyError("shadow strategy activation row was not persisted")
    return row[0]


def _load_candidate_rows(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
) -> tuple[ShadowCandidate, ...]:
    run = conn.execute(
        """
        SELECT run_status::text, prediction_contract_version, prediction_finalized_at
        FROM prediction_runs
        WHERE prediction_run_id = %s
        """,
        (prediction_run_id,),
    ).fetchone()
    if run is None:
        raise ShadowStrategyError(f"prediction run {prediction_run_id} does not exist")
    if run[0] != "COMPLETED" or run[1] != "predictive_agent_result_v1" or run[2] is None:
        raise ShadowStrategyError("shadow strategy consumes only finalized predictive_agent_result_v1 runs")

    rows = conn.execute(
        """
        SELECT
            prediction_id,
            match_id,
            agent_code::text,
            prediction_status::text,
            selection_side,
            win_probability
        FROM predictions
        WHERE prediction_run_id = %s
          AND agent_code IN (
              'AGENT_1_SURFACE_ELO',
              'AGENT_2_HIERARCHICAL_MARKOV',
              'AGENT_3_INTRANSITIVITY'
          )
        ORDER BY match_id, agent_code
        """,
        (prediction_run_id,),
    ).fetchall()

    by_match: dict[int, dict[str, tuple]] = {}
    for row in rows:
        match_id = int(row[1])
        agent_code = str(row[2])
        bucket = by_match.setdefault(match_id, {})
        if agent_code in bucket:
            raise ShadowStrategyError(
                f"duplicate {agent_code} prediction in run={prediction_run_id} match={match_id}"
            )
        bucket[agent_code] = row

    candidates: list[ShadowCandidate] = []
    required = {
        "AGENT_1_SURFACE_ELO",
        "AGENT_2_HIERARCHICAL_MARKOV",
        "AGENT_3_INTRANSITIVITY",
    }
    for match_id, bucket in sorted(by_match.items()):
        if set(bucket) != required:
            continue
        a1 = bucket["AGENT_1_SURFACE_ELO"]
        a2 = bucket["AGENT_2_HIERARCHICAL_MARKOV"]
        a3 = bucket["AGENT_3_INTRANSITIVITY"]
        if a1[3] != "PREDICTED" or a2[3] != "PREDICTED" or a3[3] != "ABSTAINED":
            continue
        if a1[4] not in (1, 2) or a2[4] != a1[4]:
            continue
        if a1[5] is None or a2[5] is None:
            continue
        p1 = Decimal(a1[5])
        p2 = Decimal(a2[5])
        if p1 <= Decimal("0.5") or p2 <= Decimal("0.5"):
            continue
        candidates.append(
            ShadowCandidate(
                match_id=match_id,
                a1_prediction_id=int(a1[0]),
                a2_prediction_id=int(a2[0]),
                a3_prediction_id=int(a3[0]),
                selected_side=int(a1[4]),
                a1_probability=p1,
                a2_probability=p2,
            )
        )
    return tuple(candidates)


def _json_payload(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _persist_observation(
    conn: psycopg.Connection,
    *,
    shadow_run_id: int,
    prediction_run_id: int,
    candidate: ShadowCandidate,
    observed_at: datetime,
    market_policy_version_id: int,
    market_decision,
    final_decision,
    final_status: str,
    veto_status: str,
    veto_evaluation,
    veto_error: str | None,
) -> tuple[int, bool]:
    staking = final_status == "BET"
    row = conn.execute(
        """
        INSERT INTO shadow_strategy_observations (
            shadow_run_id, strategy_code, prediction_run_id, match_id, observed_at,
            agent_1_prediction_id, agent_2_prediction_id, agent_3_prediction_id,
            selected_side, agent_1_probability, agent_2_probability,
            consensus_probability, probability_spread,
            market_policy_version_id, market_decision_status, market_reason_code,
            final_shadow_status, edge_pct, min_acceptable_odds,
            market_decimal_price, sharp_reference_price,
            price_source_system, price_bookmaker_code,
            staking_rule_version, staking_probability, staking_decimal_price,
            full_kelly_fraction, quarter_kelly_fraction, suggested_stake_fraction,
            snapshot_lineage_hash,
            veto_policy_version, veto_status, veto_triggered_codes,
            veto_unavailable_codes, veto_evidence, veto_error
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s,
            %s, %s, %s::jsonb,
            %s::jsonb, %s::jsonb, %s
        )
        ON CONFLICT (strategy_code, prediction_run_id, match_id) DO NOTHING
        RETURNING shadow_observation_id
        """,
        (
            shadow_run_id,
            STRATEGY_CODE,
            prediction_run_id,
            candidate.match_id,
            observed_at,
            candidate.a1_prediction_id,
            candidate.a2_prediction_id,
            candidate.a3_prediction_id,
            candidate.selected_side,
            candidate.a1_probability,
            candidate.a2_probability,
            candidate.consensus_probability,
            candidate.probability_spread,
            market_policy_version_id,
            market_decision.decision_status,
            market_decision.reason_code,
            final_status,
            market_decision.edge_pct,
            market_decision.min_acceptable_odds,
            market_decision.market_decimal_price,
            market_decision.sharp_reference_price,
            market_decision.price_source_system,
            market_decision.price_bookmaker_code,
            final_decision.staking_rule_version if staking else None,
            final_decision.staking_probability if staking else None,
            final_decision.staking_decimal_price if staking else None,
            final_decision.full_kelly_fraction if staking else None,
            final_decision.quarter_kelly_fraction if staking else None,
            final_decision.suggested_stake_fraction if staking else None,
            market_decision.snapshot_lineage_hash,
            VETO_POLICY_VERSION if market_decision.decision_status == "BET" else None,
            veto_status,
            _json_payload(veto_evaluation.triggered_codes if veto_evaluation else ()),
            _json_payload(veto_evaluation.unavailable_codes if veto_evaluation else ()),
            _json_payload(veto_evaluation.evidence if veto_evaluation else ()),
            veto_error,
        ),
    ).fetchone()
    if row is None:
        existing = conn.execute(
            """
            SELECT shadow_observation_id
            FROM shadow_strategy_observations
            WHERE strategy_code = %s
              AND prediction_run_id = %s
              AND match_id = %s
            """,
            (STRATEGY_CODE, prediction_run_id, candidate.match_id),
        ).fetchone()
        if existing is None:
            raise ShadowStrategyError("shadow observation conflict row disappeared")
        return int(existing[0]), False

    observation_id = int(row[0])
    for role, odds_snapshot_id in market_decision.lineage:
        conn.execute(
            """
            INSERT INTO shadow_strategy_observation_odds (
                shadow_observation_id, snapshot_role, odds_snapshot_id
            )
            VALUES (%s, %s, %s)
            """,
            (observation_id, role, odds_snapshot_id),
        )
    return observation_id, True


def _freeze_position(
    conn: psycopg.Connection,
    *,
    observation_id: int,
    candidate: ShadowCandidate,
    observed_at: datetime,
    market_decision,
) -> bool:
    lineage = dict(market_decision.lineage)
    offered_id = lineage.get("OFFERED_SELECTION")
    if offered_id is None:
        raise ShadowStrategyError("qualified shadow BET is missing offered-price lineage")
    if (
        market_decision.market_decimal_price is None
        or market_decision.price_source_system is None
        or market_decision.price_bookmaker_code is None
        or market_decision.staking_rule_version is None
        or market_decision.suggested_stake_fraction is None
        or market_decision.suggested_stake_fraction <= 0
    ):
        raise ShadowStrategyError("qualified shadow BET is missing reproducible staking evidence")

    row = conn.execute(
        """
        INSERT INTO shadow_strategy_positions (
            strategy_code, match_id, first_observation_id, opened_at,
            selected_side, decimal_price, price_source_system, price_bookmaker_code,
            offered_odds_snapshot_id, staking_rule_version, suggested_stake_fraction
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (strategy_code, match_id) DO NOTHING
        RETURNING shadow_position_id
        """,
        (
            STRATEGY_CODE,
            candidate.match_id,
            observation_id,
            observed_at,
            candidate.selected_side,
            market_decision.market_decimal_price,
            market_decision.price_source_system,
            market_decision.price_bookmaker_code,
            offered_id,
            market_decision.staking_rule_version,
            market_decision.suggested_stake_fraction,
        ),
    ).fetchone()
    return row is not None


def observe_shadow_run(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    observed_at: datetime,
    sharp_reference: SharpReferenceSpec,
) -> ShadowRunOutcome:
    """Evaluate one finalized live prediction run prospectively and idempotently."""
    observed = _aware(observed_at)
    ensure_shadow_strategy_schema(conn)
    policy = ensure_market_policy_version_v1(conn)
    candidates = _load_candidate_rows(conn, prediction_run_id=prediction_run_id)

    market_bets = vetoed = blocked = positions_opened = observations = 0
    with conn.transaction():
        existing_run = conn.execute(
            """
            SELECT
                sr.candidate_count,
                count(so.shadow_observation_id),
                count(*) FILTER (WHERE so.market_decision_status = 'BET'),
                count(*) FILTER (WHERE so.final_shadow_status = 'VETOED'),
                count(*) FILTER (WHERE so.final_shadow_status = 'BLOCKED'),
                count(sp.shadow_position_id)
            FROM shadow_strategy_runs sr
            LEFT JOIN shadow_strategy_observations so
              ON so.shadow_run_id = sr.shadow_run_id
            LEFT JOIN shadow_strategy_positions sp
              ON sp.first_observation_id = so.shadow_observation_id
            WHERE sr.strategy_code = %s AND sr.prediction_run_id = %s
            GROUP BY sr.candidate_count
            """,
            (STRATEGY_CODE, prediction_run_id),
        ).fetchone()
        if existing_run is not None:
            return ShadowRunOutcome(
                prediction_run_id=prediction_run_id,
                candidates=int(existing_run[0]),
                observations=int(existing_run[1]),
                market_bets=int(existing_run[2]),
                vetoed=int(existing_run[3]),
                blocked=int(existing_run[4]),
                positions_opened=int(existing_run[5]),
            )

        shadow_run_id = int(
            conn.execute(
                """
                INSERT INTO shadow_strategy_runs (
                    strategy_code, prediction_run_id, observed_at, candidate_count
                )
                VALUES (%s, %s, %s, %s)
                RETURNING shadow_run_id
                """,
                (STRATEGY_CODE, prediction_run_id, observed, len(candidates)),
            ).fetchone()[0]
        )

        for candidate in candidates:
            synthetic_manager = PersistedManagerConsensusRow(
                manager_consensus_result_id=0,
                prediction_run_id=prediction_run_id,
                match_id=candidate.match_id,
                manager_policy_version_id=0,
                manager_status="CONSENSUS",
                selected_side=candidate.selected_side,
                consensus_probability=candidate.consensus_probability,
                active_agent_count=2,
                abstaining_agent_count=1,
                probability_spread=candidate.probability_spread,
                reason_code="NONE",
                as_of=observed,
            )
            competition_tier = load_competition_tier(
                conn,
                match_id=candidate.match_id,
                decision_as_of=observed,
            )
            odds = load_match_winner_odds(
                conn,
                match_id=candidate.match_id,
                decision_as_of=observed,
            )
            market_decision = evaluate_market_match(
                manager_row=synthetic_manager,
                policy=policy,
                competition_tier=competition_tier,
                sharp_reference=sharp_reference,
                odds_snapshots=odds,
                decision_as_of=observed,
            )

            final_status = market_decision.decision_status
            veto_status = "NOT_APPLICABLE"
            veto_evaluation = None
            veto_error = None
            final_decision = market_decision
            if market_decision.decision_status == "BET":
                market_bets += 1
                try:
                    veto_evaluation = evaluate_veto_for_decision(
                        conn,
                        decision=market_decision,
                        sharp_reference=sharp_reference,
                    )
                except VetoEvidenceError as exc:
                    blocked += 1
                    veto_status = "ERROR"
                    final_status = "BLOCKED"
                    veto_error = str(exc)
                else:
                    if veto_evaluation.vetoed:
                        vetoed += 1
                        veto_status = "VETOED"
                        final_status = "VETOED"
                        final_decision = _mark_decision_vetoed(market_decision)
                    else:
                        veto_status = "PASS"
                        final_status = "BET"

            observation_id, inserted = _persist_observation(
                conn,
                shadow_run_id=shadow_run_id,
                prediction_run_id=prediction_run_id,
                candidate=candidate,
                observed_at=observed,
                market_policy_version_id=policy.market_policy_version_id,
                market_decision=market_decision,
                final_decision=final_decision,
                final_status=final_status,
                veto_status=veto_status,
                veto_evaluation=veto_evaluation,
                veto_error=veto_error,
            )
            observations += int(inserted)

            if final_status == "BET":
                positions_opened += int(
                    _freeze_position(
                        conn,
                        observation_id=observation_id,
                        candidate=candidate,
                        observed_at=observed,
                        market_decision=final_decision,
                    )
                )

    return ShadowRunOutcome(
        prediction_run_id=prediction_run_id,
        candidates=len(candidates),
        observations=observations,
        market_bets=market_bets,
        vetoed=vetoed,
        blocked=blocked,
        positions_opened=positions_opened,
    )


def observe_pending_shadow_runs(
    conn: psycopg.Connection,
    *,
    observed_at: datetime,
    sharp_reference: SharpReferenceSpec,
    limit: int = 100,
) -> tuple[ShadowRunOutcome, ...]:
    """Consume only prediction runs finalized after prospective strategy activation."""
    observed = _aware(observed_at)
    activation = activate_shadow_strategy(conn, activated_at=observed)
    rows = conn.execute(
        """
        SELECT pr.prediction_run_id
        FROM prediction_runs pr
        WHERE pr.prediction_contract_version = 'predictive_agent_result_v1'
          AND pr.run_status = 'COMPLETED'
          AND pr.prediction_finalized_at IS NOT NULL
          AND pr.prediction_finalized_at >= %s
          AND pr.prediction_finalized_at <= %s
          AND NOT EXISTS (
              SELECT 1
              FROM shadow_strategy_runs sr
              WHERE sr.strategy_code = %s
                AND sr.prediction_run_id = pr.prediction_run_id
          )
        ORDER BY pr.prediction_finalized_at, pr.prediction_run_id
        LIMIT %s
        """,
        (activation, observed, STRATEGY_CODE, limit),
    ).fetchall()
    return tuple(
        observe_shadow_run(
            conn,
            prediction_run_id=int(row[0]),
            observed_at=observed,
            sharp_reference=sharp_reference,
        )
        for row in rows
    )


def _latest_terminal_result(conn: psycopg.Connection, match_id: int):
    return conn.execute(
        """
        SELECT match_result_observation_id, outcome_status::text, winner_side
        FROM match_result_observations
        WHERE match_id = %s
          AND outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
        ORDER BY as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()


def settle_shadow_positions(
    conn: psycopg.Connection,
    *,
    settled_at: datetime,
) -> ShadowSettlementOutcome:
    settled = _aware(settled_at)
    ensure_shadow_strategy_schema(conn)
    rows = conn.execute(
        """
        SELECT shadow_position_id, match_id, selected_side
        FROM shadow_strategy_positions
        WHERE strategy_code = %s
        ORDER BY shadow_position_id
        """,
        (STRATEGY_CODE,),
    ).fetchall()

    pending = inserted = existing = reconciled = won = lost = void = cancelled = 0
    for row in rows:
        position_id, match_id, side = int(row[0]), int(row[1]), int(row[2])
        result = _latest_terminal_result(conn, match_id)
        if result is None:
            pending += 1
            continue
        result_id = int(result[0])
        outcome_status = str(result[1])
        winner_side = int(result[2]) if result[2] is not None else None
        status = classify_match_winner_settlement(
            outcome_status=outcome_status,
            winner_side=winner_side,
            selection_side=side,
        )
        prior = conn.execute(
            """
            SELECT
                s.match_result_observation_id,
                s.settlement_status,
                mro.match_id
            FROM shadow_strategy_settlements s
            LEFT JOIN match_result_observations mro
              ON mro.match_result_observation_id = s.match_result_observation_id
            WHERE s.shadow_position_id = %s
            """,
            (position_id,),
        ).fetchone()
        if prior is not None:
            prior_result_id = int(prior[0]) if prior[0] is not None else None
            prior_status = str(prior[1])
            prior_match_id = int(prior[2]) if prior[2] is not None else None
            if prior_status != status:
                raise ShadowSettlementConflict(
                    "immutable shadow settlement conflicts with latest canonical result "
                    f"position={position_id} match_id={match_id} "
                    f"existing_result_id={prior_result_id} latest_result_id={result_id} "
                    f"existing_status={prior_status} expected_status={status}"
                )
            if prior_result_id != result_id:
                if prior_result_id is None or prior_match_id != match_id:
                    raise ShadowSettlementConflict(
                        "shadow settlement lineage references a different canonical match "
                        f"position={position_id} match_id={match_id} "
                        f"existing_result_id={prior_result_id} "
                        f"existing_result_match_id={prior_match_id} "
                        f"latest_result_id={result_id}"
                    )
                reconciled += 1
                print(
                    "SHADOW_SETTLEMENT_EQUIVALENT_RESULT "
                    f"position={position_id} match_id={match_id} "
                    f"settlement_status={prior_status} "
                    f"existing_result_id={prior_result_id} latest_result_id={result_id}",
                    flush=True,
                )
            else:
                existing += 1
        else:
            created = conn.execute(
                """
                INSERT INTO shadow_strategy_settlements (
                    shadow_position_id, match_result_observation_id,
                    settlement_policy_version, settlement_status, settled_at
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (shadow_position_id) DO NOTHING
                RETURNING shadow_position_id
                """,
                (
                    position_id,
                    result_id,
                    SETTLEMENT_POLICY_VERSION,
                    status,
                    settled,
                ),
            ).fetchone()
            if created is None:
                concurrent = conn.execute(
                    """
                    SELECT match_result_observation_id, settlement_status
                    FROM shadow_strategy_settlements
                    WHERE shadow_position_id = %s
                    """,
                    (position_id,),
                ).fetchone()
                if concurrent is None or str(concurrent[1]) != status:
                    raise ShadowSettlementConflict(
                        "concurrent shadow settlement could not be verified"
                    )
                if int(concurrent[0]) != result_id:
                    reconciled += 1
                else:
                    existing += 1
            else:
                inserted += 1

        won += int(status == "WON")
        lost += int(status == "LOST")
        void += int(status == "VOID")
        cancelled += int(status == "CANCELLED")

    return ShadowSettlementOutcome(
        eligible=len(rows),
        pending=pending,
        inserted=inserted,
        existing=existing,
        reconciled=reconciled,
        won=won,
        lost=lost,
        void=void,
        cancelled=cancelled,
    )


def _close_status(pair) -> str:
    if pair.complete:
        return "COMPLETE"
    if pair.player_1 is not None or pair.player_2 is not None:
        return "PARTIAL"
    return "MISSING"


def _selected_pair_price(pair, selected_side: int) -> Decimal | None:
    if not pair.complete:
        return None
    point = pair.player_1 if selected_side == 1 else pair.player_2
    return point.decimal_price if point is not None else None


def _calculate_shadow_clv(
    *,
    price_taken: Decimal,
    closing_price: Decimal,
    closing_booksum: Decimal,
) -> tuple[Decimal, Decimal]:
    margin = (closing_booksum - Decimal("1")).quantize(
        _CLV_QUANTIZER,
        rounding=ROUND_HALF_UP,
    )
    if margin <= 0:
        raise ValueError("closing bookmaker margin must be positive")
    clv = (
        (price_taken / closing_price / (Decimal("1") + margin)) - Decimal("1")
    ).quantize(_CLV_QUANTIZER, rounding=ROUND_HALF_UP)
    return margin, clv


def finalize_shadow_clv(
    conn: psycopg.Connection,
    *,
    captured_at: datetime,
    sharp_reference: SharpReferenceSpec,
    finalization_delay: timedelta = DEFAULT_CLOSE_DELAY,
) -> ShadowClvOutcome:
    captured = _aware(captured_at)
    if finalization_delay < timedelta(0):
        raise ValueError("finalization_delay must be non-negative")
    ensure_shadow_strategy_schema(conn)
    close_before = captured - finalization_delay
    rows = conn.execute(
        """
        SELECT
            sp.shadow_position_id,
            sp.match_id,
            sp.selected_side,
            sp.decimal_price,
            sp.price_source_system,
            sp.price_bookmaker_code,
            sp.offered_odds_snapshot_id,
            mv.match_start_at,
            mv.match_status::text
        FROM shadow_strategy_positions sp
        JOIN LATERAL (
            SELECT match_start_at, match_status
            FROM match_versions
            WHERE match_id = sp.match_id
              AND source_observed_at <= %s
              AND as_of <= %s
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE sp.strategy_code = %s
          AND mv.match_start_at IS NOT NULL
          AND mv.match_start_at <= %s
          AND mv.match_status <> 'POSTPONED'
          AND NOT EXISTS (
              SELECT 1
              FROM shadow_strategy_clv sc
              WHERE sc.shadow_position_id = sp.shadow_position_id
          )
        ORDER BY mv.match_start_at, sp.shadow_position_id
        """,
        (captured, captured, STRATEGY_CODE, close_before),
    ).fetchall()

    inserted = available = unavailable = 0
    for row in rows:
        (
            position_id,
            match_id,
            side,
            price_taken,
            price_source,
            bookmaker,
            offered_snapshot_id,
            match_start,
            match_status,
        ) = row
        position_id = int(position_id)
        match_id = int(match_id)
        side = int(side)
        price_taken = Decimal(price_taken)

        offered = conn.execute(
            """
            SELECT decimal_price, source_system, bookmaker_code,
                   selection_type::text, source_observed_at
            FROM odds_snapshots
            WHERE odds_snapshot_id = %s
            """,
            (int(offered_snapshot_id),),
        ).fetchone()

        sharp = clv_service._load_pair_evidence(
            conn,
            match_id=match_id,
            source_system=sharp_reference.source_system,
            bookmaker_code=sharp_reference.bookmaker_code,
            close_cutoff=match_start,
            available_as_of=captured,
        )
        same_book = clv_service._load_pair_evidence(
            conn,
            match_id=match_id,
            source_system=str(price_source),
            bookmaker_code=str(bookmaker),
            close_cutoff=match_start,
            available_as_of=captured,
        )

        reason = None
        closing_same = closing_sharp = margin = clv = None
        if str(match_status) in {"CANCELLED", "POSTPONED", "WALKOVER"}:
            reason = "MATCH_NOT_PLAYED_AS_SCHEDULED"
        elif offered is None:
            reason = "PRICE_TAKEN_LINEAGE_MISSING"
        else:
            expected_selection = "PLAYER_1" if side == 1 else "PLAYER_2"
            offered_price = Decimal(offered[0])
            price_taken_at = offered[4]
            if (
                offered_price != price_taken
                or str(offered[1]) != str(price_source)
                or str(offered[2]) != str(bookmaker)
                or str(offered[3]) != expected_selection
            ):
                reason = "PRICE_TAKEN_LINEAGE_MISMATCH"
            elif price_taken_at > match_start:
                reason = "PRICE_TAKEN_AFTER_START"
            elif not same_book.complete:
                reason = "SAME_BOOK_CLOSE_MISSING"
            elif same_book.pair_observed_at is None or same_book.pair_observed_at < price_taken_at:
                reason = "SAME_BOOK_CLOSE_PRECEDES_PRICE_TAKEN"
            elif same_book.booksum is None:
                reason = "SAME_BOOK_CLOSE_MISSING"
            else:
                closing_same = _selected_pair_price(same_book, side)
                closing_sharp = _selected_pair_price(sharp, side)
                if closing_same is None:
                    reason = "SAME_BOOK_CLOSE_MISSING"
                else:
                    try:
                        margin, clv = _calculate_shadow_clv(
                            price_taken=price_taken,
                            closing_price=closing_same,
                            closing_booksum=same_book.booksum,
                        )
                    except ValueError:
                        reason = "SAME_BOOK_MARGIN_NON_POSITIVE"

        clv_available = reason is None and clv is not None
        created = conn.execute(
            """
            INSERT INTO shadow_strategy_clv (
                shadow_position_id, capture_version, calculation_version,
                match_start_at, match_status_at_capture, captured_at, close_status,
                sharp_player_1_snapshot_id, sharp_player_2_snapshot_id,
                sharp_pair_observed_at,
                same_book_player_1_snapshot_id, same_book_player_2_snapshot_id,
                same_book_pair_observed_at, closing_same_book_price,
                closing_sharp_price, closing_margin, clv_pct,
                clv_available, unavailable_reason
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (shadow_position_id) DO NOTHING
            RETURNING shadow_position_id
            """,
            (
                position_id,
                CLV_CAPTURE_VERSION,
                CLV_CALCULATION_VERSION,
                match_start,
                str(match_status),
                captured,
                _close_status(sharp),
                sharp.player_1.odds_snapshot_id if sharp.player_1 else None,
                sharp.player_2.odds_snapshot_id if sharp.player_2 else None,
                sharp.pair_observed_at,
                same_book.player_1.odds_snapshot_id if same_book.player_1 else None,
                same_book.player_2.odds_snapshot_id if same_book.player_2 else None,
                same_book.pair_observed_at,
                closing_same,
                closing_sharp,
                margin,
                clv,
                clv_available,
                reason,
            ),
        ).fetchone()
        if created is not None:
            inserted += 1
            available += int(clv_available)
            unavailable += int(not clv_available)

    return ShadowClvOutcome(
        eligible=len(rows),
        inserted=inserted,
        available=available,
        unavailable=unavailable,
    )


def load_shadow_bankroll(
    conn: psycopg.Connection,
    *,
    starting_bankroll: Decimal = STARTING_BANKROLL,
) -> PaperBankrollSummary:
    ensure_shadow_strategy_schema(conn)
    rows = conn.execute(
        """
        SELECT
            sp.shadow_position_id,
            sp.match_id,
            sp.opened_at,
            sp.decimal_price,
            sp.suggested_stake_fraction,
            COALESCE(ss.settlement_status, 'PENDING') AS settlement_status,
            ss.settled_at
        FROM shadow_strategy_positions sp
        LEFT JOIN shadow_strategy_settlements ss
          ON ss.shadow_position_id = sp.shadow_position_id
        WHERE sp.strategy_code = %s
        ORDER BY sp.opened_at, sp.shadow_position_id
        """,
        (STRATEGY_CODE,),
    ).fetchall()
    bets = tuple(
        PaperBet(
            decision_id=int(row[0]),
            match_id=int(row[1]),
            decision_at=row[2],
            settlement_status=str(row[5]),
            settled_at=row[6],
            decimal_price=Decimal(row[3]),
            stake_fraction=Decimal(row[4]),
            selection=None,
            player_1=None,
            player_2=None,
            tournament=None,
            bookmaker=None,
        )
        for row in rows
    )
    return simulate_paper_bankroll(bets, starting_bankroll=starting_bankroll)


def load_shadow_strategy_summary(
    conn: psycopg.Connection,
) -> ShadowStrategySummary:
    ensure_shadow_strategy_schema(conn)
    activation = conn.execute(
        "SELECT activated_at FROM shadow_strategy_registry WHERE strategy_code = %s",
        (STRATEGY_CODE,),
    ).fetchone()
    if activation is None:
        activation_at = activate_shadow_strategy(
            conn,
            activated_at=datetime.now(timezone.utc),
        )
    else:
        activation_at = activation[0]

    row = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM shadow_strategy_runs WHERE strategy_code = %s),
            (SELECT count(*) FROM shadow_strategy_observations WHERE strategy_code = %s),
            (SELECT count(*) FROM shadow_strategy_positions WHERE strategy_code = %s),
            count(ss.shadow_position_id),
            count(*) FILTER (WHERE ss.settlement_status = 'WON'),
            count(*) FILTER (WHERE ss.settlement_status = 'LOST'),
            count(*) FILTER (WHERE ss.settlement_status = 'VOID'),
            count(*) FILTER (WHERE ss.settlement_status = 'CANCELLED')
        FROM shadow_strategy_positions sp
        LEFT JOIN shadow_strategy_settlements ss
          ON ss.shadow_position_id = sp.shadow_position_id
        WHERE sp.strategy_code = %s
        """,
        (STRATEGY_CODE, STRATEGY_CODE, STRATEGY_CODE, STRATEGY_CODE),
    ).fetchone()
    clv = conn.execute(
        """
        SELECT count(*) FILTER (WHERE clv_available), avg(clv_pct) FILTER (WHERE clv_available)
        FROM shadow_strategy_clv sc
        JOIN shadow_strategy_positions sp
          ON sp.shadow_position_id = sc.shadow_position_id
        WHERE sp.strategy_code = %s
        """,
        (STRATEGY_CODE,),
    ).fetchone()
    bankroll = load_shadow_bankroll(conn)
    return ShadowStrategySummary(
        activation_at=activation_at,
        observed_runs=int(row[0] or 0),
        observations=int(row[1] or 0),
        positions=int(row[2] or 0),
        settled=int(row[3] or 0),
        wins=int(row[4] or 0),
        losses=int(row[5] or 0),
        voids=int(row[6] or 0),
        cancelled=int(row[7] or 0),
        clv_records=int(clv[0] or 0),
        mean_clv=Decimal(clv[1]) if clv[1] is not None else None,
        bankroll=bankroll,
    )


__all__ = [
    "STRATEGY_CODE",
    "SHADOW_SCHEMA_VERSION",
    "ShadowStrategyError",
    "ShadowSettlementConflict",
    "ShadowRunOutcome",
    "ShadowSettlementOutcome",
    "ShadowClvOutcome",
    "ShadowStrategySummary",
    "activate_shadow_strategy",
    "ensure_shadow_strategy_schema",
    "finalize_shadow_clv",
    "load_shadow_bankroll",
    "load_shadow_strategy_summary",
    "observe_pending_shadow_runs",
    "observe_shadow_run",
    "settle_shadow_positions",
]
