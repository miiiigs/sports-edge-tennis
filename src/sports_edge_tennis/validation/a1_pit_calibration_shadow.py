"""Read-only chronological Agent 1 probability calibration experiment.

One real, first-issued, pre-match ATP/Challenger Agent 1 prediction per fixture.
Completed outcomes are used only after their recorded ingestion timestamp.
Does not update predictions, Manager, bookmaker decisions, or production rules.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.performance.calibration import (
    Sample, brier, fit_isotonic, fit_platt, log_loss,
)

VERSION = "A1_FIRST_ISSUE_CHRONO_CALIBRATION_SHADOW_V1"
PREMATCH_MINUTES = 30


@dataclass(frozen=True)
class FirstIssue:
    match_id: int
    issued_at: datetime
    start_at: datetime
    probability: float
    outcome: int | None
    result_known_at: datetime | None
    tour: str
    surface: str


def source_sql() -> str:
    """PIT fixture and latest *overall* terminal state, never last COMPLETED only."""
    return """
        WITH first_issue AS (
            SELECT DISTINCT ON (p.match_id)
                p.match_id, p.as_of AS prediction_as_of,
                pr.prediction_finalized_at AS issued_at,
                p.win_probability AS probability, p.selection_side
            FROM predictions p
            JOIN prediction_runs pr ON pr.prediction_run_id=p.prediction_run_id
            WHERE p.agent_code='AGENT_1_SURFACE_ELO'
              AND p.prediction_status='PREDICTED'
              AND p.selection_side IN (1,2)
              AND p.win_probability IS NOT NULL
              AND pr.prediction_contract_version='predictive_agent_result_v1'
              AND pr.run_status='COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
              AND pr.experiment_id IS NULL
              AND p.experiment_id IS NULL
              AND pr.prediction_finalized_at <= %(as_of)s
            ORDER BY p.match_id, p.as_of, p.prediction_id
        )
        SELECT
            fi.match_id, fi.prediction_as_of, fi.issued_at,
            fi.probability, fi.selection_side,
            mv.match_start_at, mv.tour_scope::text,
            COALESCE(mv.surface::text,'UNKNOWN'),
            lr.outcome_status, lr.winner_side, lr.known_at
        FROM first_issue fi
        LEFT JOIN LATERAL (
            SELECT match_start_at, tour_scope, surface
            FROM match_versions mv0
            WHERE mv0.match_id=fi.match_id
              AND mv0.as_of <= fi.prediction_as_of
              AND mv0.source_observed_at <= fi.prediction_as_of
              AND mv0.ingested_at <= fi.prediction_as_of
            ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
                     mv0.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        LEFT JOIN LATERAL (
            SELECT outcome_status::text AS outcome_status, winner_side,
                   GREATEST(as_of,source_observed_at,ingested_at) AS known_at
            FROM match_result_observations mro
            WHERE mro.match_id=fi.match_id
              AND mro.as_of <= %(as_of)s
              AND mro.source_observed_at <= %(as_of)s
              AND mro.ingested_at <= %(as_of)s
            ORDER BY as_of DESC, source_observed_at DESC,
                     match_result_observation_id DESC
            LIMIT 1
        ) lr ON TRUE
        ORDER BY fi.match_id
    """


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize(rows: list[tuple], *, as_of: datetime) -> tuple[list[FirstIssue], dict[str, int]]:
    """Reject late or unverifiable first-issued records; never choose a later retry."""
    if as_of.tzinfo is None:
        raise ValueError("evaluation cutoff must have timezone")
    records: list[FirstIssue] = []
    rejected: Counter[str] = Counter()
    seen: set[int] = set()
    for (mid, prediction_at, issued_at, prob, side, start, tour, surface,
         status, winner, known_at) in rows:
        if mid in seen:
            raise ValueError("duplicate first-issued canonical match")
        seen.add(mid)
        if not isinstance(start, datetime) or not isinstance(issued_at, datetime):
            rejected["MISSING_PIT_FIXTURE_OR_ISSUANCE"] += 1
            continue
        if (prediction_at is None or prediction_at.tzinfo is None
            or start.tzinfo is None or issued_at.tzinfo is None):
            rejected["MISSING_AWARE_PIT_TIMESTAMP"] += 1
            continue
        if (prediction_at > issued_at or issued_at > as_of
            or issued_at > start - timedelta(minutes=PREMATCH_MINUTES)):
            rejected["LATE_OR_CHRONOLOGICALLY_INVALID_FIRST_ISSUE"] += 1
            continue
        if tour not in ("ATP_MAIN", "CHALLENGER"):
            rejected["OUT_OF_SCOPE_TOUR"] += 1
            continue
        if side not in (1, 2) or prob is None:
            rejected["INVALID_SELECTION"] += 1
            continue
        p = float(prob)
        if not math.isfinite(p) or not 0.5 <= p < 1.0:
            rejected["INVALID_PROBABILITY"] += 1
            continue
        y: int | None = None
        known: datetime | None = None
        if status == "COMPLETED":
            if winner not in (1, 2) or not isinstance(known_at, datetime):
                rejected["UNVERIFIED_COMPLETED_OUTCOME"] += 1
                continue
            if known_at.tzinfo is None or known_at > as_of:
                rejected["FUTURE_OR_NAIVE_RESULT_TIMESTAMP"] += 1
                continue
            if known_at < start:
                rejected["OUTCOME_KNOWN_BEFORE_FROZEN_START"] += 1
                continue
            y = int(side == winner)
            known = known_at
        # VOID/DEFAULTED/RETIRED/WALKOVER/CANCELLED/unresolved are not graded.
        records.append(FirstIssue(
            match_id=int(mid), issued_at=issued_at, start_at=start,
            probability=p, outcome=y, result_known_at=known,
            tour=str(tour), surface=str(surface),
        ))
    return records, dict(sorted(rejected.items()))


def _metrics(points: list[tuple[float, int]]) -> dict[str, Any]:
    return {
        "n": len(points),
        "hit_rate": sum(y for _, y in points)/len(points) if points else None,
        "brier": brier(points),
        "log_loss": log_loss(points),
    }


def evaluate(records: list[FirstIssue], *, as_of: datetime) -> dict[str, Any]:
    """Freeze date-based splits. Train outcomes must predate validation, and
    validation outcomes must predate holdout. The holdout is never used to fit
    or choose a calibration method.
    """
    dates = sorted({r.start_at.astimezone(UTC).date() for r in records})
    result: dict[str, Any] = {
        "version": VERSION, "evaluation_as_of": _iso(as_of),
        "first_issued_prematch": len(records), "unique_fixture_dates": len(dates),
        "scored_at_evaluation": sum(r.outcome is not None for r in records),
        "noncompleted_or_unresolved": sum(r.outcome is None for r in records),
        "research_only": True, "db_writes": False, "live_selection_changed": False,
        "winner_selection_unchanged": True,
    }
    if len(dates) < 5:
        return {**result, "status": "INSUFFICIENT_DISTINCT_DATES"}
    val_idx = max(1, int(len(dates)*.6))
    hold_idx = max(val_idx+1, int(len(dates)*.8))
    if hold_idx >= len(dates):
        return {**result, "status": "INSUFFICIENT_SPLIT"}
    val_start, hold_start = dates[val_idx], dates[hold_idx]
    buckets: dict[str, list[FirstIssue]] = {"train": [], "validation": [], "holdout": []}
    for row in records:
        day = row.start_at.astimezone(UTC).date()
        buckets["train" if day < val_start else
                "validation" if day < hold_start else "holdout"].append(row)
    # Only outcomes with persisted timestamps already known at the next
    # training/model-selection boundary may influence coefficients or choice.
    train = [r for r in buckets["train"] if r.outcome is not None
             and r.result_known_at is not None
             and r.result_known_at.date() < val_start]
    validation = [r for r in buckets["validation"] if r.outcome is not None
                  and r.result_known_at is not None
                  and r.result_known_at.date() < hold_start]
    holdout = [r for r in buckets["holdout"] if r.outcome is not None
               and r.result_known_at is not None
               and r.result_known_at <= as_of]
    result["splits"] = {
        "validation_start_utc": val_start.isoformat(),
        "holdout_start_utc": hold_start.isoformat(),
        "train_first_issued": len(buckets["train"]),
        "validation_first_issued": len(buckets["validation"]),
        "holdout_first_issued": len(buckets["holdout"]),
        "train_known_before_validation": len(train),
        "validation_known_before_holdout": len(validation),
        "holdout_scored_as_of": len(holdout),
    }
    if len(train) < 30 or len(validation) < 10 or len(holdout) < 10:
        return {**result, "status": "INSUFFICIENT_NONLEAKY_TRAIN_VALIDATION_OR_HOLDOUT"}
    samples = [Sample(raw=r.probability, outcome=int(r.outcome)) for r in train]
    if len({s.outcome for s in samples}) < 2:
        return {**result, "status": "TRAIN_LACKS_BOTH_OUTCOME_CLASSES"}
    models = {"platt": fit_platt(samples), "isotonic": fit_isotonic(samples)}
    raw_val = [(r.probability, int(r.outcome)) for r in validation]
    validation_scores: dict[str, dict[str, Any]] = {"raw": _metrics(raw_val)}
    for name, model in models.items():
        if model is not None:
            validation_scores[name] = _metrics([
                (model.predict(r.probability), int(r.outcome)) for r in validation
            ])
    # Deterministic winner: minimize validation Brier then log loss.
    # The untouched raw probabilities win ties. Never select on holdout.
    candidates = ["raw", "platt", "isotonic"]
    winner = min(
        (name for name in candidates if name in validation_scores),
        key=lambda n: (validation_scores[n]["brier"],
                       validation_scores[n]["log_loss"], candidates.index(n)),
    )
    raw_test = [(r.probability, int(r.outcome)) for r in holdout]
    selected_test = (
        raw_test if winner == "raw"
        else [(models[winner].predict(r.probability), int(r.outcome)) for r in holdout]
    )
    raw_metrics = _metrics(raw_test)
    selected_metrics = _metrics(selected_test)
    result.update({
        "status": "EVALUATED_RESEARCH_ONLY",
        "validation_candidates": validation_scores,
        "selected_on_validation": winner,
        "holdout_raw": raw_metrics,
        "holdout_selected": selected_metrics,
        "holdout_brier_improvement": raw_metrics["brier"] - selected_metrics["brier"],
        "holdout_log_loss_improvement": raw_metrics["log_loss"] - selected_metrics["log_loss"],
        "holdout_calendar_span_days": (
            max(r.start_at.date() for r in holdout)
            - min(r.start_at.date() for r in holdout)
        ).days + 1,
        "release_eligible": False,
        "interpretation": (
            "Nonleaky chronological offline test only; training and validation "
            "results were known before their next boundaries. No tuning on "
            "holdout and no live policy change. Additional prospective fixed-"
            "version testing and independent source/licensing review required."
        ),
    })
    return result


def run(conn: psycopg.Connection, *, as_of: datetime) -> dict[str, Any]:
    conn.execute("SET default_transaction_read_only=on")
    rows = conn.execute(source_sql(), {"as_of": as_of}).fetchall()
    records, rejected = normalize(rows, as_of=as_of)
    result = evaluate(records, as_of=as_of)
    result["source_rows"] = len(rows)
    result["rejection_counts"] = rejected
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc", required=True)
    args = parser.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        parser.error("--as-of-utc must use explicit UTC Z")
    as_of = datetime.fromisoformat(args.as_of_utc.replace("Z", "+00:00"))
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        report = run(conn, as_of=as_of)
    print("A1_PIT_CALIBRATION_SHADOW " + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
