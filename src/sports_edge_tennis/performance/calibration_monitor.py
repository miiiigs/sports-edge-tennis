"""Read-only daily calibration-health monitoring for aligned Model Picks.

This module observes; it never changes a prediction, decision, threshold, pick
selection, Value Bet eligibility, or stake. It aggregates persisted live
production evidence (aligned Model Picks) into rolling calibration windows,
confidence buckets, Wide-Spread divergence cohorts, a daily-top-pick view, and
CLV/sample-sufficiency summaries.

Health flags are informational. There is NO automatic retraining or filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

import psycopg

from sports_edge_tennis.performance.calibration import (
    brier,
    expected_calibration_error,
    log_loss,
)

CONFIDENCE_EDGES: tuple[float, ...] = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.0001)
CONFIDENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("60-65", 0.60, 0.65),
    ("65-70", 0.65, 0.70),
    ("70-75", 0.70, 0.75),
    ("75-80", 0.75, 0.80),
    ("80-85", 0.80, 0.85),
    ("85-90", 0.85, 0.90),
    ("90+", 0.90, 1.0001),
)
DIVERGENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<2.5pt", 0.0, 0.025),
    ("2.5-5pt", 0.025, 0.05),
    ("5-10pt", 0.05, 0.10),
    (">10pt", 0.10, 1.01),
)

# Documented monitoring thresholds (informational only).
HEALTH_MIN_SAMPLE = 20
HEALTH_GREEN_GAP = -0.03
HEALTH_AMBER_GAP = -0.08

WINDOW_DAYS: tuple[tuple[str, int | None], ...] = (
    ("TODAY", 1),
    ("LAST_3_DAYS", 3),
    ("LAST_7_DAYS", 7),
    ("LAST_14_DAYS", 14),
    ("LAST_30_DAYS", 30),
    ("ALL_CLEAN_PRODUCTION", None),
)

#: Guidance targets before a production calibrator can be considered.
CALIBRATION_TARGET_SCORED = 1000
CALIBRATION_TARGET_HIGH_CONFIDENCE = 200

_LEDGER_SQL_TEMPLATE = """
WITH {cte},
first_bets AS (
    SELECT DISTINCT ON (d.match_id) d.match_id, d.decision_id, d.selection_side,
        d.market_decimal_price, d.price_source_system
    FROM decisions d
    JOIN prediction_runs pr ON pr.prediction_run_id = d.prediction_run_id
    WHERE d.signal_type='TYPE_A_MODEL_EDGE' AND d.decision_status='BET'
      AND d.market_type='MATCH_WINNER' AND d.experiment_id IS NULL
      AND pr.prediction_contract_version='predictive_agent_result_v1'
      AND pr.run_status='COMPLETED' AND pr.prediction_finalized_at IS NOT NULL
      AND pr.experiment_id IS NULL
    ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id
),
day AS (
    SELECT p.*, mv.match_start_at, mv.surface::text AS surface, mv.competition_tier::text AS tier,
        mv.player_1_id, mv.player_2_id,
        COALESCE((mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
                 (p.picked_at AT TIME ZONE 'Asia/Manila')::date) AS manila_date
    FROM aligned_model_picks p
    LEFT JOIN LATERAL (
        SELECT mv0.match_start_at, mv0.surface, mv0.competition_tier, mv0.player_1_id, mv0.player_2_id
        FROM match_versions mv0
        WHERE mv0.match_id=p.match_id AND mv0.as_of<=p.picked_at AND mv0.source_observed_at<=p.picked_at
        ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC LIMIT 1
    ) mv ON TRUE
),
wr AS (
    SELECT d.*, r.outcome_status, r.winner_side,
        a1.selection_side AS a1side, a1.win_probability AS a1p,
        a2.selection_side AS a2side, a2.win_probability AS a2p,
        a3.prediction_status::text AS a3s
    FROM day d
    LEFT JOIN LATERAL (
        SELECT mro.outcome_status::text AS outcome_status, mro.winner_side
        FROM match_result_observations mro
        WHERE mro.match_id=d.match_id AND mro.outcome_status IN ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
        ORDER BY mro.as_of DESC, mro.source_observed_at DESC, mro.match_result_observation_id DESC LIMIT 1
    ) r ON TRUE
    LEFT JOIN predictions a1 ON a1.prediction_run_id=d.prediction_run_id AND a1.match_id=d.match_id AND a1.agent_code='AGENT_1_SURFACE_ELO'
    LEFT JOIN predictions a2 ON a2.prediction_run_id=d.prediction_run_id AND a2.match_id=d.match_id AND a2.agent_code='AGENT_2_HIERARCHICAL_MARKOV'
    LEFT JOIN predictions a3 ON a3.prediction_run_id=d.prediction_run_id AND a3.match_id=d.match_id AND a3.agent_code='AGENT_3_INTRANSITIVITY'
)
SELECT w.picked_at::date, w.manila_date, w.match_id,
    t.canonical_name AS tournament, p1.canonical_name AS player_1, p2.canonical_name AS player_2,
    w.selected_side, w.aligned_probability, w.aligned_pick_type, w.tier, w.surface,
    w.a1p, w.a1side, w.a2p, w.a2side, w.a3s, w.outcome_status, w.winner_side,
    CASE WHEN w.outcome_status='COMPLETED' AND w.winner_side=w.selected_side THEN 'WON'
         WHEN w.outcome_status='COMPLETED' AND w.winner_side IN (1,2) THEN 'LOST'
         WHEN w.outcome_status IN ('RETIRED','WALKOVER','CANCELLED') THEN 'VOID'
         ELSE 'PENDING' END AS result_status,
    (fb.decision_id IS NOT NULL AND fb.selection_side=w.selected_side) AS is_value_bet,
    fb.market_decimal_price, clv.clv_pct
FROM wr w
JOIN matches m ON m.match_id=w.match_id
JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
JOIN tournaments t ON t.tournament_id=te.tournament_id
LEFT JOIN players p1 ON p1.player_id=w.player_1_id
LEFT JOIN players p2 ON p2.player_id=w.player_2_id
LEFT JOIN first_bets fb ON fb.match_id=w.match_id
LEFT JOIN decision_clv clv ON clv.decision_id=fb.decision_id
ORDER BY w.picked_at, w.match_id
"""


@dataclass(frozen=True)
class PickObservation:
    prediction_date: date
    manila_date: date
    match_id: int
    player: str
    ptype: str
    tier: str | None
    surface: str | None
    probability: float
    outcome: int | None
    result_status: str
    a1: float | None
    a2: float | None
    a3_status: str | None
    is_value_bet: bool
    odds: float | None
    clv: float | None
    label: str = field(default="")


def fetch_observations(conn: psycopg.Connection) -> list[PickObservation]:
    from sports_edge_tennis.dashboard.aligned_model_picks import (
        aligned_production_model_picks_cte,
    )

    rows = conn.execute(
        _LEDGER_SQL_TEMPLATE.format(cte=aligned_production_model_picks_cte())
    ).fetchall()
    observations: list[PickObservation] = []
    for r in rows:
        result = str(r[18])
        outcome = 1 if result == "WON" else (0 if result == "LOST" else None)
        observations.append(
            PickObservation(
                prediction_date=r[0],
                manila_date=r[1],
                match_id=int(r[2]),
                player=str(r[4] or r[5] or "?"),
                ptype=str(r[8]),
                tier=(str(r[9]) if r[9] is not None else None),
                surface=(str(r[10]) if r[10] is not None else None),
                probability=float(r[7]),
                outcome=outcome,
                result_status=result,
                a1=(float(r[11]) if r[11] is not None else None),
                a2=(float(r[13]) if r[13] is not None else None),
                a3_status=(str(r[15]) if r[15] is not None else None),
                is_value_bet=bool(r[19]),
                odds=(float(r[20]) if r[20] is not None else None),
                clv=(float(r[21]) if r[21] is not None else None),
            )
        )
    return observations


def _scored(obs: Iterable[PickObservation]) -> list[PickObservation]:
    return [o for o in obs if o.outcome is not None]


def _summary(obs: list[PickObservation]) -> dict[str, Any]:
    scored = _scored(obs)
    wins = sum(1 for o in scored if o.outcome == 1)
    losses = len(scored) - wins
    avg = sum(o.probability for o in scored) / len(scored) if scored else None
    observed = (wins / len(scored)) if scored else None
    pairs = [(o.probability, o.outcome) for o in scored]
    return {
        "picks": len(obs),
        "scored": len(scored),
        "wins": wins,
        "losses": losses,
        "pending": sum(1 for o in obs if o.result_status == "PENDING"),
        "voids": sum(1 for o in obs if o.result_status == "VOID"),
        "observed_hit_rate": observed,
        "average_displayed": avg,
        "calibration_gap": (observed - avg) if observed is not None and avg is not None else None,
        "brier": brier(pairs),
        "log_loss": log_loss(pairs),
        "ece": expected_calibration_error(pairs),
    }


def _buckets(obs: list[PickObservation]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, lower, upper in CONFIDENCE_BUCKETS:
        group = [o for o in obs if lower <= o.probability < upper]
        scored = _scored(group)
        if not scored:
            out[label] = {"n": len(group), "scored": 0, "sparse": len(group) < HEALTH_MIN_SAMPLE}
            continue
        wins = sum(1 for o in scored if o.outcome == 1)
        avg = sum(o.probability for o in scored) / len(scored)
        observed = wins / len(scored)
        pairs = [(o.probability, o.outcome) for o in scored]
        out[label] = {
            "n": len(group),
            "scored": len(scored),
            "wins": wins,
            "losses": len(scored) - wins,
            "observed_hit_rate": observed,
            "average_displayed": avg,
            "calibration_gap": observed - avg,
            "brier": brier(pairs),
            "log_loss": log_loss(pairs),
            "ece": expected_calibration_error(pairs),
            "sparse": len(scored) < HEALTH_MIN_SAMPLE,
        }
    return out


def _health(scored_n: int, observed: float | None, avg: float | None) -> str:
    if observed is None or avg is None or scored_n < HEALTH_MIN_SAMPLE:
        return "AMBER_INSUFFICIENT_SAMPLE"
    gap = observed - avg
    if gap >= HEALTH_GREEN_GAP:
        return "GREEN"
    if gap >= HEALTH_AMBER_GAP:
        return "AMBER"
    return "RED"


def _high_confidence(obs: list[PickObservation]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for threshold in (0.80, 0.85, 0.90):
        group = [o for o in obs if o.probability >= threshold]
        scored = _scored(group)
        wins = sum(1 for o in scored if o.outcome == 1)
        avg = sum(o.probability for o in scored) / len(scored) if scored else None
        observed = (wins / len(scored)) if scored else None
        out[f"{threshold:.2f}"] = {
            "n": len(group),
            "scored": len(scored),
            "wins": wins,
            "losses": len(scored) - wins,
            "observed_hit_rate": observed,
            "average_displayed": avg,
            "calibration_gap": (observed - avg) if observed is not None and avg is not None else None,
            "health": _health(len(scored), observed, avg),
        }
    return out


def _wide_divergence(obs: list[PickObservation]) -> dict[str, dict[str, Any]]:
    wide = [o for o in obs if o.ptype == "A1_A2_WIDE_SPREAD" and o.a1 is not None and o.a2 is not None]
    out: dict[str, dict[str, Any]] = {}
    for label, lower, upper in DIVERGENCE_BUCKETS:
        group = [o for o in wide if lower <= abs(o.a1 - o.a2) < upper]
        scored = _scored(group)
        wins = sum(1 for o in scored if o.outcome == 1)
        avg = sum(o.probability for o in scored) / len(scored) if scored else None
        observed = (wins / len(scored)) if scored else None
        out[label] = {
            "n": len(group),
            "scored": len(scored),
            "wins": wins,
            "losses": len(scored) - wins,
            "observed_hit_rate": observed,
            "average_displayed": avg,
            "calibration_gap": (observed - avg) if observed is not None and avg is not None else None,
            "sparse": len(scored) < HEALTH_MIN_SAMPLE,
        }
    return out


def _cohort(obs: list[PickObservation], predicate) -> dict[str, Any]:
    return _summary([o for o in obs if predicate(o)])


def _clv(obs: list[PickObservation]) -> dict[str, Any]:
    with_clv = [o for o in obs if o.clv is not None]
    value_bets = [o for o in obs if o.is_value_bet]
    settled_vb = [o for o in value_bets if o.outcome is not None and o.odds]
    profit = sum((o.odds - 1) if o.outcome == 1 else -1.0 for o in settled_vb)
    return {
        "value_bets": len(value_bets),
        "value_bets_scored": len(settled_vb),
        "value_bet_wins": sum(1 for o in settled_vb if o.outcome == 1),
        "value_bet_roi": (profit / len(settled_vb)) if settled_vb else None,
        "clv_n": len(with_clv),
        "clv_positive": sum(1 for o in with_clv if o.clv is not None and o.clv > 0),
        "clv_negative": sum(1 for o in with_clv if o.clv is not None and o.clv < 0),
        "clv_mean": (sum(o.clv for o in with_clv) / len(with_clv)) if with_clv else None,
    }


def _daily_tops(observations: list[PickObservation]) -> dict[str, Any]:
    by_day: dict[date, list[PickObservation]] = {}
    for o in observations:
        by_day.setdefault(o.manila_date, []).append(o)
    days = []
    for day in sorted(by_day):
        top = max(by_day[day], key=lambda z: (z.probability, -z.match_id))
        days.append(
            {
                "date": day.isoformat(),
                "match_id": top.match_id,
                "player": top.player,
                "probability": top.probability,
                "type": top.ptype,
                "result": top.result_status,
            }
        )
    decided = [d for d in days if d["result"] in ("WON", "LOST")]
    wins = sum(1 for d in decided if d["result"] == "WON")
    longest = current = 0
    for d in decided:
        if d["result"] == "LOST":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return {
        "days": days,
        "decided": len(decided),
        "wins": wins,
        "losses": len(decided) - wins,
        "hit_rate": (wins / len(decided)) if decided else None,
        "longest_loss_streak": longest,
        "current_loss_streak": current,
        "last_7": decided[-7:],
    }


def build_calibration_health(
    observations: list[PickObservation],
    *,
    reference_date: date,
    abstention: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure aggregation over observations (testable without a database)."""
    windows: dict[str, Any] = {}
    for name, days in WINDOW_DAYS:
        if days is None:
            subset = list(observations)
        else:
            start = reference_date - timedelta(days=days - 1)
            subset = [o for o in observations if start <= o.prediction_date <= reference_date]
        wide = [o for o in subset if o.ptype == "A1_A2_WIDE_SPREAD"]
        manager = [o for o in subset if o.ptype == "MANAGER_CONSENSUS"]
        windows[name] = {
            "combined": _summary(subset),
            "manager": _summary(manager),
            "wide": _summary(wide),
            "confidence_buckets": _buckets(subset),
            "high_confidence": _high_confidence(subset),
            "wide_divergence": _wide_divergence(subset),
            "unknown_surface_wide": _cohort(
                subset,
                lambda o: o.ptype == "A1_A2_WIDE_SPREAD" and o.surface is None,
            ),
            "known_surface_wide": _cohort(
                subset,
                lambda o: o.ptype == "A1_A2_WIDE_SPREAD" and o.surface is not None,
            ),
            "clv": _clv(subset),
        }

    all_scored = _scored(observations)
    high_scored = [o for o in all_scored if o.probability >= 0.80]
    sufficiency = {
        "scored": len(all_scored),
        "high_confidence_scored": len(high_scored),
        "target_scored": CALIBRATION_TARGET_SCORED,
        "target_high_confidence": CALIBRATION_TARGET_HIGH_CONFIDENCE,
        "calibration_model_ready": (
            len(all_scored) >= CALIBRATION_TARGET_SCORED
            and len(high_scored) >= CALIBRATION_TARGET_HIGH_CONFIDENCE
        ),
    }

    flags = _flags(observations, windows["ALL_CLEAN_PRODUCTION"], sufficiency)
    return {
        "reference_date": reference_date.isoformat(),
        "windows": windows,
        "daily_top_pick": _daily_tops(observations),
        "abstention": abstention,
        "sample_sufficiency": sufficiency,
        "flags": flags,
        "health_thresholds": {
            "min_sample": HEALTH_MIN_SAMPLE,
            "green_gap": HEALTH_GREEN_GAP,
            "amber_gap": HEALTH_AMBER_GAP,
            "note": "Informational monitoring only; no automatic retraining or filtering.",
        },
    }


def _flags(
    observations: list[PickObservation],
    all_window: dict[str, Any],
    sufficiency: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    if not sufficiency["calibration_model_ready"]:
        flags.append("INSUFFICIENT_CALIBRATION_SAMPLE")
    hc80 = all_window["high_confidence"].get("0.80", {})
    if hc80.get("health") in ("AMBER", "RED"):
        flags.append("HIGH_CONFIDENCE_UNCALIBRATED")
    if all_window["unknown_surface_wide"]["scored"] and (
        all_window["unknown_surface_wide"]["calibration_gap"] is not None
        and all_window["unknown_surface_wide"]["calibration_gap"] <= HEALTH_AMBER_GAP
    ):
        flags.append("UNKNOWN_SURFACE")
    if all_window["wide_divergence"][">10pt"]["scored"]:
        gap = all_window["wide_divergence"][">10pt"]["calibration_gap"]
        if gap is not None and gap <= HEALTH_AMBER_GAP:
            flags.append("WIDE_HIGH_DIVERGENCE")
    if all_window["wide"]["scored"] and any(o.outcome is None for o in observations):
        flags.append("A3_ABSTAINED")
    if all_window["clv"]["clv_n"] and all_window["clv"]["clv_mean"] is not None:
        if all_window["clv"]["clv_mean"] < 0:
            flags.append("NEGATIVE_CLV_AVAILABLE")
    return flags


def load_calibration_health(
    conn: psycopg.Connection,
    *,
    reference_date: date | None = None,
    include_abstention: bool = True,
) -> dict[str, Any]:
    observations = fetch_observations(conn)
    ref = reference_date or date.today()
    abstention = None
    if include_abstention:
        from sports_edge_tennis.performance.abstention_evidence import (
            load_abstention_windows,
        )

        abstention = [
            {
                "window": w.window,
                "agents": [
                    {
                        "agent_code": a.agent_code,
                        "evaluations": a.evaluations,
                        "predicted": a.predicted,
                        "abstained": a.abstained,
                        "reasons": [
                            {"reason_code": r.reason_code, "count": r.count}
                            for r in a.reasons
                        ],
                    }
                    for a in w.agents
                ],
            }
            for w in load_abstention_windows(conn)
        ]
    return build_calibration_health(observations, reference_date=ref, abstention=abstention)


def hypothetical_filter(
    observations: list[PickObservation],
    *,
    exclude_unknown_surface_wide: bool = False,
    exclude_high_divergence_wide: bool = False,
    divergence_threshold: float = 0.10,
) -> dict[str, Any]:
    """Read-only counterfactual: what would the population look like if filtered.

    Used only for filter research. It never alters production selection.
    """

    def excluded(o: PickObservation) -> bool:
        if o.ptype != "A1_A2_WIDE_SPREAD":
            return False
        if exclude_unknown_surface_wide and o.surface is None:
            return True
        if (
            exclude_high_divergence_wide
            and o.a1 is not None
            and o.a2 is not None
            and abs(o.a1 - o.a2) > divergence_threshold
        ):
            return True
        return False

    kept = [o for o in observations if not excluded(o)]
    removed = [o for o in observations if excluded(o)]
    kept_summary = _summary(kept)
    removed_summary = _summary(removed)
    return {
        "retained_picks": kept_summary["picks"],
        "removed_picks": removed_summary["picks"],
        "retained_wins": kept_summary["wins"],
        "retained_losses": kept_summary["losses"],
        "removed_wins": removed_summary["wins"],
        "removed_losses": removed_summary["losses"],
        "retained_hit_rate": kept_summary["observed_hit_rate"],
        "removed_hit_rate": removed_summary["observed_hit_rate"],
        "coverage_reduction": (
            removed_summary["picks"] / (kept_summary["picks"] + removed_summary["picks"])
            if (kept_summary["picks"] + removed_summary["picks"])
            else None
        ),
        "exclude_unknown_surface_wide": exclude_unknown_surface_wide,
        "exclude_high_divergence_wide": exclude_high_divergence_wide,
        "divergence_threshold": divergence_threshold,
    }


__all__ = [
    "CALIBRATION_TARGET_HIGH_CONFIDENCE",
    "CALIBRATION_TARGET_SCORED",
    "CONFIDENCE_BUCKETS",
    "DIVERGENCE_BUCKETS",
    "PickObservation",
    "WINDOW_DAYS",
    "build_calibration_health",
    "fetch_observations",
    "hypothetical_filter",
    "load_calibration_health",
]
