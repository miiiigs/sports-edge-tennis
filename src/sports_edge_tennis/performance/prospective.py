"""Prospective performance evidence from real persisted Tennis decisions.

Prediction quality and wagering profitability are intentionally separate:
- calibration/Brier/log loss score matured Manager consensus calls across BET,
  NO_BET and VETOED Type A decisions whenever the match has a normal COMPLETED result;
- unit-profit/ROI use only Type A BET rows that have an immutable settlement.

No historical rows are synthesized and no closing prices are inferred here. CLV remains
owned by the prospective CLV lifecycle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

import psycopg

_EPS = 1e-15


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    sample: int
    mean_probability: float | None
    observed_rate: float | None
    absolute_gap: float | None


@dataclass(frozen=True)
class ProspectivePerformanceSummary:
    initialized: bool
    total_decisions: int = 0
    bet_decisions: int = 0
    no_bet_decisions: int = 0
    vetoed_decisions: int = 0
    settled_bets: int = 0
    pending_bets: int = 0
    wins: int = 0
    losses: int = 0
    voids: int = 0
    cancelled: int = 0
    one_unit_profit: Decimal | None = None
    roi: Decimal | None = None
    hit_rate: float | None = None
    calibration_sample: int = 0
    brier_score: float | None = None
    log_loss: float | None = None
    ece: float | None = None
    bins: tuple[CalibrationBin, ...] = ()


@dataclass(frozen=True)
class _Row:
    decision_status: str
    selected_side: int | None
    probability: float | None
    market_price: Decimal | None
    settlement_status: str | None
    winner_side: int | None


def _build_bins(scored: list[tuple[float, int]]) -> tuple[tuple[CalibrationBin, ...], float | None]:
    bins: list[CalibrationBin] = []
    if not scored:
        for index in range(10):
            bins.append(
                CalibrationBin(
                    lower=index / 10,
                    upper=(index + 1) / 10,
                    sample=0,
                    mean_probability=None,
                    observed_rate=None,
                    absolute_gap=None,
                )
            )
        return tuple(bins), None

    weighted_gap = 0.0
    total = len(scored)
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        members = [
            (p, y)
            for p, y in scored
            if p >= lower and (p < upper or (index == 9 and p <= upper))
        ]
        if not members:
            bins.append(
                CalibrationBin(
                    lower=lower,
                    upper=upper,
                    sample=0,
                    mean_probability=None,
                    observed_rate=None,
                    absolute_gap=None,
                )
            )
            continue
        mean_probability = sum(p for p, _ in members) / len(members)
        observed_rate = sum(y for _, y in members) / len(members)
        gap = abs(mean_probability - observed_rate)
        weighted_gap += gap * len(members) / total
        bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                sample=len(members),
                mean_probability=mean_probability,
                observed_rate=observed_rate,
                absolute_gap=gap,
            )
        )
    return tuple(bins), weighted_gap


def _load_rows(conn: psycopg.Connection) -> list[_Row]:
    rows = conn.execute(
        """
        WITH latest_results AS (
            SELECT DISTINCT ON (match_id)
                match_id,
                outcome_status::text AS outcome_status,
                winner_side
            FROM match_result_observations
            WHERE outcome_status = 'COMPLETED'
            ORDER BY match_id, as_of DESC, source_observed_at DESC,
                     match_result_observation_id DESC
        )
        SELECT
            d.decision_status::text,
            mcr.selected_side,
            mcr.consensus_probability,
            d.market_decimal_price,
            ds.settlement_status::text,
            lr.winner_side
        FROM decisions d
        JOIN manager_consensus_results mcr
          ON mcr.manager_consensus_result_id = d.manager_consensus_result_id
        LEFT JOIN decision_settlements ds ON ds.decision_id = d.decision_id
        LEFT JOIN latest_results lr ON lr.match_id = d.match_id
        WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
          AND d.market_type = 'MATCH_WINNER'
        ORDER BY d.decision_id
        """
    ).fetchall()
    return [
        _Row(
            decision_status=str(row[0]),
            selected_side=int(row[1]) if row[1] is not None else None,
            probability=float(row[2]) if row[2] is not None else None,
            market_price=Decimal(row[3]) if row[3] is not None else None,
            settlement_status=str(row[4]) if row[4] is not None else None,
            winner_side=int(row[5]) if row[5] is not None else None,
        )
        for row in rows
    ]


def load_prospective_performance(conn: psycopg.Connection) -> ProspectivePerformanceSummary:
    rows = _load_rows(conn)
    if not rows:
        return ProspectivePerformanceSummary(initialized=False)

    bets = [row for row in rows if row.decision_status == "BET"]
    no_bets = [row for row in rows if row.decision_status == "NO_BET"]
    vetoed = [row for row in rows if row.decision_status == "VETOED"]
    settled = [row for row in bets if row.settlement_status is not None]

    wins = sum(row.settlement_status == "WON" for row in settled)
    losses = sum(row.settlement_status == "LOST" for row in settled)
    voids = sum(row.settlement_status == "VOID" for row in settled)
    cancelled = sum(row.settlement_status == "CANCELLED" for row in settled)

    profit = Decimal("0")
    for row in settled:
        if row.settlement_status == "WON":
            if row.market_price is None:
                # A canonical Type A BET must be priced; fail closed if historical
                # corruption violates that contract rather than fabricating P&L.
                raise ValueError("settled Type A BET is missing market_decimal_price")
            profit += row.market_price - Decimal("1")
        elif row.settlement_status == "LOST":
            profit -= Decimal("1")
        elif row.settlement_status in {"VOID", "CANCELLED"}:
            pass
        else:
            raise ValueError(f"unsupported settlement_status: {row.settlement_status}")

    roi = profit / Decimal(len(settled)) if settled else None
    hit_denominator = wins + losses
    hit_rate = wins / hit_denominator if hit_denominator else None

    scored: list[tuple[float, int]] = []
    for row in rows:
        if row.selected_side not in (1, 2) or row.probability is None or row.winner_side not in (1, 2):
            continue
        if not 0.0 < row.probability < 1.0:
            raise ValueError("consensus_probability must be strictly between 0 and 1")
        scored.append((row.probability, 1 if row.selected_side == row.winner_side else 0))

    if scored:
        brier = sum((p - y) ** 2 for p, y in scored) / len(scored)
        log_loss = -sum(
            y * math.log(max(_EPS, min(1 - _EPS, p)))
            + (1 - y) * math.log(max(_EPS, min(1 - _EPS, 1 - p)))
            for p, y in scored
        ) / len(scored)
    else:
        brier = None
        log_loss = None

    bins, ece = _build_bins(scored)
    return ProspectivePerformanceSummary(
        initialized=True,
        total_decisions=len(rows),
        bet_decisions=len(bets),
        no_bet_decisions=len(no_bets),
        vetoed_decisions=len(vetoed),
        settled_bets=len(settled),
        pending_bets=len(bets) - len(settled),
        wins=wins,
        losses=losses,
        voids=voids,
        cancelled=cancelled,
        one_unit_profit=profit if settled else None,
        roi=roi,
        hit_rate=hit_rate,
        calibration_sample=len(scored),
        brier_score=brier,
        log_loss=log_loss,
        ece=ece,
        bins=bins,
    )
