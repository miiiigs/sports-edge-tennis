"""Read-only A1/A2 predictive independence and selective-risk benchmark.

This is NOT an evaluation of the current three-agent Manager. It compares only
genuine first co-issued A1/A2 predictions already stored before the match.
Top-k coverage denominators refer to the *paired settled research cohort*,
not all eligible ATP/Challenger fixtures or published Model Picks.

No training, live predictions, market bets, database writes or vendor data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from datetime import UTC, datetime
from statistics import mean
from typing import Any

import psycopg

from sports_edge_tennis.validation.a1_a2_blend_shadow import (
    Pair, _cluster_ci, _stats, candidate_probabilities, normalize, source_sql,
)

VERSION = "A1_A2_INDEPENDENCE_AND_RISK_COVERAGE_V1"
MODELS = ("a1_surface_elo", "a2_serve_return_markov", "equal_log_odds_blend")
FRACTIONS = (1.0, .75, .50, .40, .25)
EPSILON = 1e-12


def _side(p: float) -> int | None:
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("invalid probability")
    if abs(p - .5) <= EPSILON:
        return None
    return 1 if p > .5 else 0


def _corr(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b):
        raise ValueError("unequal observations")
    if len(a) < 2:
        return None
    aa, bb = mean(a), mean(b)
    va = math.fsum((x - aa) ** 2 for x in a)
    vb = math.fsum((x - bb) ** 2 for x in b)
    if va <= EPSILON or vb <= EPSILON:
        return None
    return math.fsum((x-aa)*(y-bb) for x, y in zip(a, b)) / math.sqrt(va*vb)


def _mcnemar(a_correct: list[bool], b_correct: list[bool]) -> dict[str, Any]:
    """Exact two-sided binomial form of McNemar for paired, non-tied picks."""
    if len(a_correct) != len(b_correct):
        raise ValueError("unpaired predictions")
    a_only = sum(a and not b for a, b in zip(a_correct, b_correct))
    b_only = sum(b and not a for a, b in zip(a_correct, b_correct))
    n = a_only + b_only
    pvalue = min(
        1.0, (2 * sum(math.comb(n, k) for k in range(min(a_only, b_only)+1)))
        / (2 ** n)
    ) if n else 1.0
    return {
        "a_only_correct": a_only,
        "b_only_correct": b_only,
        "discordant": n,
        "mcnemar_exact_two_sided_p": pvalue,
        "paired_samples": len(a_correct),
        "interpretation": "descriptive historical paired test, not forward validation",
    }


def _summary(records: list[tuple[Pair, float]]) -> dict[str, Any]:
    points = [(p, int(row.winner_p1)) for row, p in records]
    stats = _stats(points)
    decided = [(row, p) for row, p in records if _side(p) is not None]
    wins = sum(_side(p) == int(row.winner_p1) for row, p in decided)
    return {
        **stats,
        "selections": len(decided),
        "wins": wins,
        "losses": len(decided) - wins,
        "average_confidence": (
            mean(max(p, 1-p) for _, p in decided) if decided else None
        ),
    }


def _curve(rows: list[Pair], model: str) -> dict[str, dict[str, Any]]:
    ranked = sorted(
        ((row, candidate_probabilities(row)[model]) for row in rows
         if _side(candidate_probabilities(row)[model]) is not None),
        key=lambda item: (-max(item[1], 1 - item[1]), item[0].match_id),
    )
    eligible_paired = len(rows)
    curves: dict[str, dict[str, Any]] = {}
    for fraction in FRACTIONS:
        desired = math.ceil(fraction * eligible_paired)
        selected = ranked[:desired]
        result = _summary(selected)
        curves[str(fraction)] = {
            **result,
            "target_fraction_of_paired_settled": fraction,
            "achieved_fraction_of_paired_settled": (
                len(selected) / eligible_paired if eligible_paired else None
            ),
            "pair_cohort_denominator": eligible_paired,
            "full_eligible_match_coverage": None,
            "selection_rule": "fixed top-k by pre-issued confidence, canonical match id tie-break",
        }
    return curves


def _bands(rows: list[tuple[Pair, float]]) -> dict[str, dict[str, Any]]:
    result = {}
    for lower, upper in ((.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.0000001)):
        selected = [
            (row, p) for row, p in rows
            if _side(p) is not None and lower <= max(p, 1-p) < upper
        ]
        record = _summary(selected)
        result[f"{lower:.1f}-{min(upper,1):.1f}"] = {
            "n": record["selections"],
            "predicted_mean_confidence": record["average_confidence"],
            "actual_accuracy": record["winner_accuracy"],
            "overconfidence_gap": (
                record["average_confidence"] - record["winner_accuracy"]
                if record["winner_accuracy"] is not None else None
            ),
        }
    return result


def evaluate(pairs: list[Pair]) -> dict[str, Any]:
    if len({p.match_id for p in pairs}) != len(pairs):
        raise ValueError("duplicate canonical fixture in original paired cohort")
    for row in pairs:
        if any(t.tzinfo is None for t in (row.issued_at, row.start_at)):
            raise ValueError("naive prediction or fixture timestamp")
        if row.issued_at >= row.start_at:
            raise ValueError("non-prematch original prediction")
        if row.winner_p1 not in (None, 0, 1):
            raise ValueError("unsafe winner or outcome")

    settled = [row for row in pairs if row.winner_p1 in (0, 1)]
    digest = hashlib.sha256()
    for row in sorted(pairs, key=lambda x: x.match_id):
        digest.update(
            (f"{row.match_id}|{row.issued_at.isoformat()}|"
             f"{row.start_at.isoformat()}|{row.tour}|{row.surface}|"
             f"{row.a1_p1:.12f}|{row.a2_p1:.12f}|{row.winner_p1}\n").encode()
        )
    base = {
        "version": VERSION,
        "status": "INSUFFICIENT_SETTLED_PAIRED_COHORT",
        "source": "earliest genuine co-issued A1/A2 production runs",
        "dataset_sha256": digest.hexdigest(),
        "paired_originals": len(pairs),
        "paired_settled": len(settled),
        "pending_void_or_unsettled": len(pairs) - len(settled),
        "full_eligible_fixture_denominator": None,
        "full_eligible_match_coverage_verified": False,
        "third_agent_assessed": False,
        "actual_manager_compared": False,
        "market_or_competitor_assessed": False,
        "research_only": True,
        "release_eligible": False,
        "production_rules_modified": False,
        "db_writes": False,
    }
    if len(settled) < 20:
        return base

    forecast = {
        model: [(row, candidate_probabilities(row)[model]) for row in settled]
        for model in MODELS
    }
    no_ties = [
        row for row in settled if all(
            _side(candidate_probabilities(row)[m]) is not None
            for m in ("a1_surface_elo", "a2_serve_return_markov")
        )
    ]
    outcomes = Counter()
    a1_wrong, a2_wrong, p1, p2 = [], [], [], []
    for row in no_ties:
        ps = candidate_probabilities(row)
        a = _side(ps["a1_surface_elo"]) == row.winner_p1
        b = _side(ps["a2_serve_return_markov"]) == row.winner_p1
        outcomes[
            "both_correct" if a and b else
            "a1_only_correct" if a else
            "a2_only_correct" if b else "both_wrong"
        ] += 1
        a1_wrong.append(float(not a))
        a2_wrong.append(float(not b))
        p1.append(ps["a1_surface_elo"])
        p2.append(ps["a2_serve_return_markov"])

    agree = [row for row in no_ties if (
        _side(candidate_probabilities(row)["a1_surface_elo"])
        == _side(candidate_probabilities(row)["a2_serve_return_markov"])
    )]
    disagree = [row for row in no_ties if row not in agree]
    # Same-match paired Brier difference, positive when blend is better.
    diffs = [
        (row.cluster_id,
         (candidate_probabilities(row)["a1_surface_elo"] - row.winner_p1)**2
         - (candidate_probabilities(row)["equal_log_odds_blend"]-row.winner_p1)**2)
        for row in settled
    ]
    return {
        **base,
        "status": "RESEARCH_DIAGNOSTICS_COMPLETE_NO_LIVE_MANAGER_INFERENCE",
        "original_first_pair_model_scores": {
            model: _summary(records) for model, records in forecast.items()
        },
        "per_tour": {
            tour: {
                "settled": sum(row.tour == tour for row in settled),
                "model_scores": {
                    model: _summary([
                        (row, p) for row, p in forecast[model] if row.tour == tour
                    ]) for model in MODELS
                },
            } for tour in ("ATP_TOUR", "CHALLENGER")
        },
        "probability_correlation_a1_a2": _corr(p1, p2),
        "binary_error_correlation_a1_a2": _corr(a1_wrong, a2_wrong),
        "correctness_overlap": dict(sorted(outcomes.items())),
        "both_decided_on_same_matches": len(no_ties),
        "direction_agreement": {
            "agreed": len(agree),
            "disagreed": len(disagree),
            "agreed_correct": sum(
                _side(candidate_probabilities(row)["a1_surface_elo"]) == row.winner_p1
                for row in agree
            ),
            "a1_correct_when_disagree": sum(
                _side(candidate_probabilities(row)["a1_surface_elo"]) == row.winner_p1
                for row in disagree
            ),
            "a2_correct_when_disagree": sum(
                _side(candidate_probabilities(row)["a2_serve_return_markov"]) == row.winner_p1
                for row in disagree
            ),
            "not_production_manager_green_light": True,
        },
        "mcnemar_a1_vs_a2": _mcnemar(
            [not bool(x) for x in a1_wrong],
            [not bool(x) for x in a2_wrong],
        ),
        "a1_vs_equal_log_odds_brier_improvement_cluster_ci_95pct": _cluster_ci(diffs),
        "accuracy_by_paired_cohort_selection_fraction": {
            model: _curve(settled, model) for model in MODELS
        },
        "a1_reliability_bins": _bands(forecast["a1_surface_elo"]),
        "a2_reliability_bins": _bands(forecast["a2_serve_return_markov"]),
        "critical_caveat": (
            "All coverage fractions are conditional on the observed first co-issued "
            "A1/A2 settled cohort. They are NOT 40-50% coverage of all ATP/Ch fixtures. "
            "This evaluates original pre-match forecasts, not a trained model, A3, "
            "the production Manager, a bookmaker or any prospective model."
        ),
    }


def run(conn: psycopg.Connection, *, as_of: datetime) -> dict[str, Any]:
    if as_of.tzinfo is None:
        raise ValueError("UTC-aware cutoff is mandatory")
    conn.execute("SET default_transaction_read_only=on")
    rows = conn.execute(source_sql(), {"as_of": as_of}).fetchall()
    pairs, rejected = normalize(rows, as_of=as_of)
    return {
        **evaluate(pairs),
        "source_rows": len(rows),
        "source_rejections": rejected,
        "as_of_utc": as_of.astimezone(UTC).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc", required=True)
    args = parser.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        parser.error("explicit UTC cutoff with Z required")
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        report = run(
            conn, as_of=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00"))
        )
    print("A1_A2_INDEPENDENCE_RESEARCH " + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
