"""Deterministic selective-performance evaluation for Agent 1 research.

This module contains no database access and no model fitting.  It evaluates a
frozen, fixture-level population of already-issued forecasts.  Every ranking,
rounding and tie rule is explicit so that another researcher can reproduce the
exact counts from the committed population export.

Terminology
-----------
confidence
    ``max(p1, 1 - p1)``.  The predicted side is player 1 when ``p1 >= 0.5``.
coverage
    The top ``floor(fraction * n)`` rows of a fixed confidence ranking.
risk
    ``1 - accuracy`` inside the selected prefix.
generalized risk
    ``coverage * risk``, i.e. the error share of the *whole* population that
    has been accepted at that coverage.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from statistics import mean, median
from typing import Any

VERSION = "A1_SELECTIVE_PERFORMANCE_V1"

COVERAGE_POINTS: tuple[float, ...] = (
    0.10, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.75, 1.00,
)
CONFIDENCE_BANDS: tuple[tuple[float, float], ...] = (
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.0000001),
)
EPSILON = 1e-12
CONFIDENCE_QUANTUM = 6
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 20261126

Row = Mapping[str, Any]


# ---------------------------------------------------------------------------
# Basic deterministic rules
# ---------------------------------------------------------------------------
def confidence(p1: float) -> float:
    if not math.isfinite(p1) or not 0.0 <= p1 <= 1.0:
        raise ValueError(f"invalid probability {p1!r}")
    return round(max(p1, 1.0 - p1), CONFIDENCE_QUANTUM)


def selection_side(p1: float) -> int:
    """Probability 0.5 selects player 1 (the production ``p >= 0.5`` rule)."""
    if not math.isfinite(p1) or not 0.0 <= p1 <= 1.0:
        raise ValueError(f"invalid probability {p1!r}")
    return 1 if p1 >= 0.5 else 2


def is_correct(p1: float, winner_side: int) -> bool:
    if winner_side not in (1, 2):
        raise ValueError(f"invalid winner_side {winner_side!r}")
    return selection_side(p1) == winner_side


def coverage_count(n: int, fraction: float) -> int:
    """``floor(fraction * n)`` with a documented endpoint rule at 100 %."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be within [0, 1]")
    if fraction == 1.0:
        return n
    return math.floor(fraction * n + 1e-9)


def rank_rows(rows: Iterable[Row], key: str) -> list[Row]:
    """Descending confidence, then ascending prediction_id and match_id.

    The tie-break is deterministic and independent of outcome.  It is a
    *convention*, not a claim that tied forecasts are distinguishable.
    """
    return sorted(
        rows,
        key=lambda row: (
            -confidence(float(row[key])),
            int(row["prediction_id"]),
            int(row["match_id"]),
        ),
    )


def _day(row: Row) -> str:
    value = row.get("date") or row.get("match_start_at") or row.get("as_of")
    if not value:
        raise ValueError("row has no date/match_start_at/as_of")
    return str(value)[:10]


# ---------------------------------------------------------------------------
# Proper scores and intervals
# ---------------------------------------------------------------------------
def _log_loss_term(p1: float, y: int) -> float:
    p = p1 if y == 1 else 1.0 - p1
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    return -math.log(p)


def wilson_interval(wins: int, n: int, z: float = 1.96) -> list[float] | None:
    if n <= 0:
        return None
    p = wins / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denominator
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("empty sample")
    ordered = sorted(values)
    index = q * (len(ordered) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _summarise_scores(pairs: Sequence[tuple[float, int]]) -> dict[str, float]:
    n = len(pairs)
    if not n:
        return {"n": 0.0}
    wins = sum(1 for p1, y in pairs if is_correct(p1, 2 if y == 0 else 1))
    brier = math.fsum((p1 - y) ** 2 for p1, y in pairs) / n
    log_loss = math.fsum(_log_loss_term(p1, y) for p1, y in pairs) / n
    mean_conf = math.fsum(confidence(p1) for p1, _ in pairs) / n
    return {
        "n": float(n),
        "wins": float(wins),
        "accuracy": wins / n,
        "brier": brier,
        "log_loss": log_loss,
        "mean_confidence": mean_conf,
    }


def day_block_bootstrap(
    selected: Sequence[Row],
    key: str,
    *,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, list[float] | None]:
    """Percentile intervals for accuracy, Brier and log loss.

    Calendar days are the resampling unit because fixtures on one day share
    tournament, weather and market conditions.  The seed is fixed.
    """
    if not selected:
        return {"accuracy": None, "brier": None, "log_loss": None}
    by_day: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for row in selected:
        by_day[_day(row)].append((float(row[key]), int(row["y"])))
    days = sorted(by_day)
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {"accuracy": [], "brier": [], "log_loss": []}
    for _ in range(reps):
        sample: list[tuple[float, int]] = []
        for _ in range(len(days)):
            sample.extend(by_day[rng.choice(days)])
        block = _summarise_scores(sample)
        for metric, values in draws.items():
            values.append(block[metric])
    return {
        metric: [_percentile(values, 0.025), _percentile(values, 0.975)]
        for metric, values in draws.items()
    }


def paired_day_block_bootstrap(
    original: Sequence[Row],
    corrected: Sequence[Row],
    key_original: str,
    key_corrected: str,
    *,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, list[float] | None]:
    """Paired-by-day interval for corrected minus original.

    Each version keeps its own selected fixtures; the same resampled calendar
    days are used for both.  The selections are therefore not paired at the
    fixture level, only at the day-block level.
    """
    if not original or not corrected:
        return {"accuracy": None, "brier": None, "log_loss": None}
    days = sorted({_day(row) for row in original} | {_day(row) for row in corrected})
    left_by_day: dict[str, list[tuple[float, int]]] = defaultdict(list)
    right_by_day: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for row in original:
        left_by_day[_day(row)].append((float(row[key_original]), int(row["y"])))
    for row in corrected:
        right_by_day[_day(row)].append((float(row[key_corrected]), int(row["y"])))
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {"accuracy": [], "brier": [], "log_loss": []}
    for _ in range(reps):
        left: list[tuple[float, int]] = []
        right: list[tuple[float, int]] = []
        for _ in range(len(days)):
            day = rng.choice(days)
            left.extend(left_by_day.get(day, ()))
            right.extend(right_by_day.get(day, ()))
        left_block = _summarise_scores(left)
        right_block = _summarise_scores(right)
        draws["accuracy"].append(right_block["accuracy"] - left_block["accuracy"])
        draws["brier"].append(right_block["brier"] - left_block["brier"])
        draws["log_loss"].append(right_block["log_loss"] - left_block["log_loss"])
    return {
        metric: [_percentile(values, 0.025), _percentile(values, 0.975)]
        for metric, values in draws.items()
    }


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def calibration_bands(selected: Sequence[Row], key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for lower, upper in CONFIDENCE_BANDS:
        group = [
            row
            for row in selected
            if lower <= confidence(float(row[key])) < upper
        ]
        label = f"{lower:.1f}-{min(upper, 1.0):.1f}"
        if not group:
            result[label] = {"n": 0}
            continue
        pairs = [(float(row[key]), int(row["y"])) for row in group]
        block = _summarise_scores(pairs)
        result[label] = {
            "n": len(group),
            "wins": int(block["wins"]),
            "losses": len(group) - int(block["wins"]),
            "accuracy": block["accuracy"],
            "mean_confidence": block["mean_confidence"],
            "expected_wins": block["mean_confidence"] * len(group),
            "calibration_gap": block["mean_confidence"] - block["accuracy"],
            "brier": block["brier"],
            "log_loss": block["log_loss"],
            "wilson95": wilson_interval(int(block["wins"]), len(group)),
        }
    return result


# ---------------------------------------------------------------------------
# Coverage evaluation
# ---------------------------------------------------------------------------
def boundary_diagnostics(ranked: Sequence[Row], k: int, key: str) -> dict[str, Any]:
    if not ranked or k <= 0:
        return {"n": 0}
    boundary = confidence(float(ranked[k - 1][key]))
    selected_at = sum(
        1 for row in ranked[:k] if confidence(float(row[key])) == boundary
    )
    eligible = sum(1 for row in ranked if confidence(float(row[key])) == boundary)
    return {
        "boundary_confidence": boundary,
        "selected_at_boundary": selected_at,
        "eligible_at_boundary": eligible,
        "excluded_at_boundary": eligible - selected_at,
        "last_selected_prediction_id": int(ranked[k - 1]["prediction_id"]),
        "first_excluded_prediction_id": (
            int(ranked[k]["prediction_id"]) if k < len(ranked) else None
        ),
        "rule": (
            "descending confidence; ascending prediction_id then match_id. "
            "Equal-confidence forecasts are ordered by that convention, not by outcome."
        ),
    }


def _tour_split(selected: Sequence[Row], key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for tour in ("ATP_MAIN", "CHALLENGER"):
        group = [row for row in selected if str(row.get("tour")) == tour]
        if not group:
            result[tour] = {"n": 0}
            continue
        pairs = [(float(row[key]), int(row["y"])) for row in group]
        block = _summarise_scores(pairs)
        result[tour] = {
            "n": len(group),
            "wins": int(block["wins"]),
            "losses": len(group) - int(block["wins"]),
            "accuracy": block["accuracy"],
            "brier": block["brier"],
            "log_loss": block["log_loss"],
            "mean_confidence": block["mean_confidence"],
            "expected_wins": block["mean_confidence"] * len(group),
            "calibration_gap": block["mean_confidence"] - block["accuracy"],
            "wilson95": wilson_interval(int(block["wins"]), len(group)),
        }
    return result


def evaluate_coverage(
    rows: Sequence[Row],
    key: str,
    *,
    points: Sequence[float] = COVERAGE_POINTS,
    bootstrap: bool = True,
) -> dict[str, Any]:
    """Evaluate one frozen ranking at every requested coverage point."""
    ranked = rank_rows(rows, key)
    n = len(ranked)
    result: dict[str, Any] = {
        "n": n,
        "ranking": "descending confidence; ties by ascending prediction_id then match_id",
        "points": {},
    }
    previous_k = 0
    for fraction in points:
        k = coverage_count(n, fraction)
        selected = ranked[:k]
        pairs = [(float(row[key]), int(row["y"])) for row in selected]
        block = _summarise_scores(pairs)
        marginal_rows = ranked[previous_k:k]
        marginal_pairs = [(float(row[key]), int(row["y"])) for row in marginal_rows]
        marginal = _summarise_scores(marginal_pairs)
        wins = int(block["wins"])
        entry = {
            "target_fraction": fraction,
            "selection_count": k,
            "achieved_coverage": k / n if n else None,
            "wins": wins,
            "losses": k - wins,
            "accuracy": block["accuracy"],
            "wilson95": wilson_interval(wins, k),
            "marginal": {
                "selection_count": len(marginal_rows),
                "wins": int(marginal["wins"]),
                "losses": len(marginal_rows) - int(marginal["wins"]),
                "accuracy": marginal["accuracy"],
            },
            "mean_confidence": block["mean_confidence"],
            "expected_wins": block["mean_confidence"] * k,
            "observed_wins": wins,
            "expected_minus_observed_wins": block["mean_confidence"] * k - wins,
            "calibration_gap": block["mean_confidence"] - block["accuracy"],
            "brier": block["brier"],
            "log_loss": block["log_loss"],
            "calibration_bands": calibration_bands(selected, key),
            "by_tour": _tour_split(selected, key),
            "boundary": boundary_diagnostics(ranked, k, key),
        }
        if bootstrap:
            entry["bootstrap95"] = day_block_bootstrap(selected, key)
        result["points"][f"{fraction:.2f}"] = entry
        previous_k = k
    return result


# ---------------------------------------------------------------------------
# Risk-coverage curve, AURC and AUGRC
# ---------------------------------------------------------------------------
def _tie_groups(ranked: Sequence[Row], key: str) -> list[tuple[int, int, int]]:
    """``(start, end, errors)`` for equal-confidence blocks, zero-based."""
    groups: list[tuple[int, int, int]] = []
    start = 0
    while start < len(ranked):
        value = confidence(float(ranked[start][key]))
        end = start + 1
        while end < len(ranked) and confidence(float(ranked[end][key])) == value:
            end += 1
        errors = sum(0 if is_correct(float(row[key]), int(row["winner_side"])) else 1 for row in ranked[start:end])
        groups.append((start, end, errors))
        start = end
    return groups


def risk_coverage(rows: Sequence[Row], key: str) -> dict[str, Any]:
    """Full risk-coverage curve and published AURC/AUGRC estimators.

    ``augrc_paper`` uses the finite-pool trapezoidal rank weight from Traub et
    al. (NeurIPS 2024): an error at zero-based rank ``r`` contributes
    ``(n - r - 0.5) / n**2``.  Ties are averaged exactly.  ``augrc_left`` is the
    left-Riemann variant used by common libraries; it is reported for
    transparency, not as the primary value.
    """
    ranked = rank_rows(rows, key)
    n = len(ranked)
    if not n:
        return {"n": 0}
    groups = _tie_groups(ranked, key)
    errors_before = 0
    aurc_sum = 0.0
    augrc_paper_terms: list[float] = []
    augrc_left_terms: list[float] = []
    for start, end, group_errors in groups:
        size = end - start
        expected = group_errors / size
        for offset in range(size):
            cumulative = errors_before + (offset + 1) * expected
            aurc_sum += cumulative / (start + offset + 1)
        average_rank = (start + end - 1) / 2.0
        augrc_paper_terms.append(group_errors * (n - average_rank - 0.5) / (n * n))
        augrc_left_terms.append(group_errors * (n - average_rank) / (n * n))
        errors_before += group_errors
    return {
        "n": n,
        "errors": errors_before,
        "aurc": aurc_sum / n,
        "augrc_paper": math.fsum(augrc_paper_terms),
        "augrc_left": math.fsum(augrc_left_terms),
        "optimal_augrc_binary_ranking": (errors_before / n) ** 2 / 2.0,
        "definitions": {
            "selective_risk": "errors in prefix / prefix size",
            "generalized_risk": "errors in prefix / full population",
            "aurc": "mean over ranks of cumulative selective risk (tie-averaged)",
            "augrc_paper": "trapezoidal finite-pool AUGRC, tie-averaged (Traub et al. 2024)",
            "augrc_left": "left-Riemann AUGRC used by some libraries (comparison only)",
        },
    }


def risk_coverage_curve(rows: Sequence[Row], key: str, *, step: int = 1) -> list[dict[str, float]]:
    """Every-rank curve (or every ``step`` ranks) for audit export."""
    ranked = rank_rows(rows, key)
    n = len(ranked)
    out: list[dict[str, float]] = []
    errors = 0
    for index, row in enumerate(ranked, start=1):
        if not is_correct(float(row[key]), int(row["winner_side"])):
            errors += 1
        if index % step == 0 or index == n:
            out.append(
                {
                    "rank": float(index),
                    "coverage": index / n,
                    "selective_risk": errors / index,
                    "generalized_risk": errors / n,
                    "confidence": confidence(float(row[key])),
                }
            )
    return out


# ---------------------------------------------------------------------------
# Four-way ranking/winner decomposition
# ---------------------------------------------------------------------------
def four_way(
    rows: Sequence[Row],
    *,
    original_key: str = "p1_original",
    corrected_key: str = "p1_corrected",
    k: int = 800,
) -> dict[str, Any]:
    """2x2 decomposition of ranking and winner-call corrections at fixed k.

    A = original ranking, original winner call
    B = original ranking, corrected winner call
    C = corrected ranking, original winner call
    D = corrected ranking, corrected winner call

    This is an *order-averaged algebraic decomposition*, not a causal
    attribution.  The two factors interact.
    """
    rank_a = rank_rows(rows, original_key)[:k]
    rank_d = rank_rows(rows, corrected_key)[:k]

    def wins(ranking: Sequence[Row], winner_key: str) -> int:
        return sum(
            1 for row in ranking if is_correct(float(row[winner_key]), int(row["winner_side"]))
        )

    a = wins(rank_a, original_key)
    b = wins(rank_a, corrected_key)
    c = wins(rank_d, original_key)
    d = wins(rank_d, corrected_key)
    ranking_effect_original = c - a
    ranking_effect_corrected = d - b
    winner_effect_original_ranking = b - a
    winner_effect_corrected_ranking = d - c
    ranking_effect_average = (ranking_effect_original + ranking_effect_corrected) / 2.0
    winner_effect_average = (
        winner_effect_original_ranking + winner_effect_corrected_ranking
    ) / 2.0
    interaction = (d - c) - (b - a)
    return {
        "k": k,
        "wins": {"A": a, "B": b, "C": c, "D": d},
        "accuracy": {"A": a / k, "B": b / k, "C": c / k, "D": d / k},
        "ranking_effect_at_original_winner": ranking_effect_original,
        "ranking_effect_at_corrected_winner": ranking_effect_corrected,
        "winner_effect_at_original_ranking": winner_effect_original_ranking,
        "winner_effect_at_corrected_ranking": winner_effect_corrected_ranking,
        "order_averaged_ranking_effect": ranking_effect_average,
        "order_averaged_winner_effect": winner_effect_average,
        "interaction": interaction,
        "identity": (
            f"D - A = {d - a} = ranking {ranking_effect_average:.1f} + "
            f"winner {winner_effect_average:.1f}; interaction {interaction:+d}"
        ),
        "interpretation": (
            "Order-averaged algebraic decomposition. It does not identify an "
            "independent causal effect and does not transfer to a new population."
        ),
    }


# ---------------------------------------------------------------------------
# Cohort comparison
# ---------------------------------------------------------------------------
def _numeric(row: Row, name: str) -> float | None:
    value = row.get(name)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean_or_none(values: Iterable[float]) -> float | None:
    values = [value for value in values if value is not None]
    return mean(values) if values else None


def group_profile(
    rows: Sequence[Row],
    *,
    probability_key: str,
    winner_key: str,
    history_suffix: str,
) -> dict[str, Any]:
    """Describe a selection cohort without using its outcome to select it."""
    if not rows:
        return {"n": 0}
    probabilities = [float(row[probability_key]) for row in rows]
    wins = sum(
        1 for row in rows if is_correct(float(row[winner_key]), int(row["winner_side"]))
    )
    overall_matches = [
        _mean_or_none(
            [_numeric(row, f"overall_matches_{history_suffix}_p1"), _numeric(row, f"overall_matches_{history_suffix}_p2")]
        )
        for row in rows
    ]
    surface_matches = [
        _mean_or_none(
            [_numeric(row, f"surface_matches_{history_suffix}_p1"), _numeric(row, f"surface_matches_{history_suffix}_p2")]
        )
        for row in rows
    ]
    overall_ratings = [
        _mean_or_none(
            [_numeric(row, f"overall_rating_{history_suffix}_p1"), _numeric(row, f"overall_rating_{history_suffix}_p2")]
        )
        for row in rows
    ]
    surface_ratings = [
        _mean_or_none(
            [_numeric(row, f"surface_rating_{history_suffix}_p1"), _numeric(row, f"surface_rating_{history_suffix}_p2")]
        )
        for row in rows
    ]
    inactivity = [
        _mean_or_none(
            [_numeric(row, f"inactivity_days_{history_suffix}_p1"), _numeric(row, f"inactivity_days_{history_suffix}_p2")]
        )
        for row in rows
    ]
    methods = Counter()
    for row in rows:
        for slot in ("p1", "p2"):
            method = row.get(f"resolution_{slot}_method")
            if method:
                methods[str(method)] += 1
    inactivity_available = sum(1 for value in inactivity if value is not None)
    history_available = sum(1 for value in overall_matches if value is not None)
    market_rows = [row for row in rows if _numeric(row, "market_p1") is not None]
    market_block = (
        _summarise_scores([(float(row["market_p1"]), int(row["y"])) for row in market_rows])
        if market_rows
        else {"n": 0.0}
    )
    return {
        "n": len(rows),
        "wins": wins,
        "losses": len(rows) - wins,
        "accuracy": wins / len(rows),
        "mean_confidence": mean(confidence(value) for value in probabilities),
        "mean_abs_probability_delta": _mean_or_none(
            abs(float(row[probability_key]) - float(row["p1_original"] if probability_key == "p1_corrected" else row["p1_corrected"]))
            for row in rows
        ),
        "mean_player_overall_matches": _mean_or_none(overall_matches),
        "mean_player_surface_matches": _mean_or_none(surface_matches),
        "mean_player_overall_rating": _mean_or_none(overall_ratings),
        "mean_player_surface_rating": _mean_or_none(surface_ratings),
        "mean_inactivity_days": _mean_or_none(inactivity),
        "inactivity_coverage": inactivity_available / len(rows),
        "history_coverage": history_available / len(rows),
        "share_any_bridged_player": sum(
            1
            for row in rows
            if str(row.get("resolution_p1_bridged")).lower() == "true"
            or str(row.get("resolution_p2_bridged")).lower() == "true"
        )
        / len(rows),
        "tours": dict(Counter(str(row.get("tour")) for row in rows)),
        "rounds": dict(Counter(str(row.get("round_code")) for row in rows)),
        "identity_methods": dict(methods),
        "bookmaker_available": sum(
            1 for row in rows if _numeric(row, "market_p1") is not None
        ),
        "market_benchmark_on_available": {
            "n": int(market_block["n"]),
            "accuracy": market_block.get("accuracy"),
            "brier": market_block.get("brier"),
            "log_loss": market_block.get("log_loss"),
            "mean_confidence": market_block.get("mean_confidence"),
        },
    }


def _recovery_split(rows: Sequence[Row]) -> dict[str, int]:
    direct = indirect = 0
    for row in rows:
        if abs(float(row["p1_corrected"]) - float(row["p1_original"])) <= 1e-9:
            continue
        bridged = (
            str(row.get("resolution_p1_bridged")).lower() == "true"
            or str(row.get("resolution_p2_bridged")).lower() == "true"
        )
        recovered = max(
            0.0,
            float(row.get("overall_matches_corrected_p1", 0) or 0)
            - float(row.get("overall_matches_original_p1", 0) or 0),
        ) + max(
            0.0,
            float(row.get("overall_matches_corrected_p2", 0) or 0)
            - float(row.get("overall_matches_original_p2", 0) or 0),
        )
        if bridged and recovered > 0:
            direct += 1
        else:
            indirect += 1
    return {"direct_recovery": direct, "indirect_only": indirect}


def cohort_comparison(rows: Sequence[Row], *, k: int = 800) -> dict[str, Any]:
    original = rank_rows(rows, "p1_original")[:k]
    corrected = rank_rows(rows, "p1_corrected")[:k]
    original_ids = {int(row["prediction_id"]) for row in original}
    corrected_ids = {int(row["prediction_id"]) for row in corrected}
    by_id = {int(row["prediction_id"]): row for row in rows}
    shared_ids = original_ids & corrected_ids
    removed_ids = original_ids - corrected_ids
    added_ids = corrected_ids - original_ids
    shared = [by_id[pid] for pid in sorted(shared_ids)]
    removed = [by_id[pid] for pid in sorted(removed_ids)]
    added = [by_id[pid] for pid in sorted(added_ids)]
    changed_side = [
        row
        for row in shared
        if selection_side(float(row["p1_original"])) != selection_side(float(row["p1_corrected"]))
    ]
    probability_changes = [
        row
        for row in shared
        if abs(float(row["p1_original"]) - float(row["p1_corrected"])) > 1e-9
    ]
    return {
        "k": k,
        "shared": {
            "n": len(shared),
            **_recovery_split(shared),
            "wins_original": sum(
                1 for row in shared if is_correct(float(row["p1_original"]), int(row["winner_side"]))
            ),
            "wins_corrected": sum(
                1 for row in shared if is_correct(float(row["p1_corrected"]), int(row["winner_side"]))
            ),
            "side_changes": len(changed_side),
            "probability_changes": len(probability_changes),
            "profile_original": group_profile(
                shared, probability_key="p1_original", winner_key="p1_original", history_suffix="original"
            ),
            "profile_corrected": group_profile(
                shared, probability_key="p1_corrected", winner_key="p1_corrected", history_suffix="corrected"
            ),
        },
        "removed": {
            "n": len(removed),
            **_recovery_split(removed),
            **group_profile(
                removed, probability_key="p1_original", winner_key="p1_original", history_suffix="original"
            ),
            "profile_original": group_profile(
                removed, probability_key="p1_original", winner_key="p1_original", history_suffix="original"
            ),
            "profile_corrected": group_profile(
                removed, probability_key="p1_corrected", winner_key="p1_corrected", history_suffix="corrected"
            ),
        },
        "added": {
            "n": len(added),
            **_recovery_split(added),
            **group_profile(
                added, probability_key="p1_corrected", winner_key="p1_corrected", history_suffix="corrected"
            ),
            "profile_original": group_profile(
                added, probability_key="p1_original", winner_key="p1_original", history_suffix="original"
            ),
            "profile_corrected": group_profile(
                added, probability_key="p1_corrected", winner_key="p1_corrected", history_suffix="corrected"
            ),
        },
        "probability_quality_on_identical_fixtures": {
            "n": len(shared),
            "original": _summarise_scores(
                [(float(row["p1_original"]), int(row["y"])) for row in shared]
            ),
            "corrected": _summarise_scores(
                [(float(row["p1_corrected"]), int(row["y"])) for row in shared]
            ),
        },
        "selection_effect_on_separate_cohorts": {
            "removed_accuracy": (
                sum(1 for row in removed if is_correct(float(row["p1_original"]), int(row["winner_side"]))) / len(removed)
                if removed else None
            ),
            "added_accuracy": (
                sum(1 for row in added if is_correct(float(row["p1_corrected"]), int(row["winner_side"]))) / len(added)
                if added else None
            ),
        },
    }


# ---------------------------------------------------------------------------
# Chronological threshold freezing
# ---------------------------------------------------------------------------
def development_threshold(
    rows: Sequence[Row], fraction: float, key: str
) -> dict[str, Any]:
    """Confidence threshold at the ``floor(fraction*n)`` rank of a dev set."""
    ranked = rank_rows(rows, key)
    n = len(ranked)
    k = coverage_count(n, fraction)
    if k <= 0:
        raise ValueError("development set too small for requested fraction")
    return {
        "fraction": fraction,
        "selection_count": k,
        "achieved_development_coverage": k / n,
        "threshold": confidence(float(ranked[k - 1][key])),
        "development_n": n,
        "rule": "frozen before the holdout period; no holdout outcome enters this value",
    }


def apply_frozen_threshold(
    rows: Sequence[Row], threshold: float, key: str
) -> list[Row]:
    """Select every row whose frozen confidence reaches the threshold."""
    return [
        row for row in rows if confidence(float(row[key])) >= threshold - 1e-12
    ]


def chronological_validation(
    rows: Sequence[Row],
    *,
    development_end: str,
    key: str,
    points: Sequence[float] = (0.40, 0.45, 0.50),
) -> dict[str, Any]:
    """Freeze thresholds on an earlier period and score them on a later one."""
    development = [row for row in rows if _day(row) <= development_end]
    holdout = [row for row in rows if _day(row) > development_end]
    if not development or not holdout:
        return {"status": "INSUFFICIENT_PERIOD", "development_n": len(development), "holdout_n": len(holdout)}
    result: dict[str, Any] = {
        "development_end_inclusive": development_end,
        "development_n": len(development),
        "holdout_n": len(holdout),
        "holdout_start": min(_day(row) for row in holdout),
        "holdout_end": max(_day(row) for row in holdout),
        "thresholds": {},
    }
    for fraction in points:
        threshold = development_threshold(development, fraction, key)
        selected = apply_frozen_threshold(holdout, threshold["threshold"], key)
        pairs = [(float(row[key]), int(row["y"])) for row in selected]
        block = _summarise_scores(pairs)
        wins = int(block["wins"])
        result["thresholds"][f"{fraction:.2f}"] = {
            **threshold,
            "holdout_selection_count": len(selected),
            "holdout_achieved_coverage": len(selected) / len(holdout),
            "holdout_wins": wins,
            "holdout_losses": len(selected) - wins,
            "holdout_accuracy": block["accuracy"],
            "holdout_wilson95": wilson_interval(wins, len(selected)),
            "holdout_brier": block["brier"],
            "holdout_log_loss": block["log_loss"],
            "holdout_mean_confidence": block["mean_confidence"],
            "holdout_calibration_gap": block["mean_confidence"] - block["accuracy"],
            "holdout_by_tour": _tour_split(selected, key),
            "holdout_bootstrap95": day_block_bootstrap(selected, key),
        }
    return result


def weekly_walk_forward(
    rows: Sequence[Row],
    *,
    key: str,
    fraction: float,
    minimum_development: int = 300,
) -> dict[str, Any]:
    """Expanding-window thresholds, one frozen threshold per calendar week."""
    by_week: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        iso = date.fromisoformat(_day(row)).isocalendar()
        by_week[f"{iso.year}-W{iso.week:02d}"].append(row)
    weeks = sorted(by_week)
    history: list[Row] = []
    weekly: dict[str, Any] = {}
    aggregate: list[Row] = []
    for week in weeks:
        current = by_week[week]
        if len(history) >= minimum_development:
            threshold = development_threshold(history, fraction, key)["threshold"]
            selected = apply_frozen_threshold(current, threshold, key)
            pairs = [(float(row[key]), int(row["y"])) for row in selected]
            block = _summarise_scores(pairs)
            weekly[week] = {
                "prior_n": len(history),
                "threshold": threshold,
                "n": len(selected),
                "coverage": len(selected) / len(current),
                "wins": int(block["wins"]),
                "accuracy": block["accuracy"],
                "brier": block["brier"],
                "log_loss": block["log_loss"],
            }
            aggregate.extend(selected)
        else:
            weekly[week] = {"prior_n": len(history), "status": "INSUFFICIENT_PRIOR"}
        history.extend(current)
    pairs = [(float(row[key]), int(row["y"])) for row in aggregate]
    block = _summarise_scores(pairs)
    return {
        "fraction": fraction,
        "weeks": weekly,
        "aggregate": {
            "n": len(aggregate),
            "wins": int(block["wins"]),
            "accuracy": block["accuracy"],
            "brier": block["brier"],
            "log_loss": block["log_loss"],
        },
        "note": "Threshold at each week uses only earlier weeks; no same-week or future outcome enters it.",
    }


# ---------------------------------------------------------------------------
# Provenance and history summaries (from the exported fixture rows)
# ---------------------------------------------------------------------------
def provenance_summary(rows: Sequence[Row]) -> dict[str, Any]:
    methods = Counter()
    slots = 0
    missing = 0
    bridged = 0
    ambiguous = 0
    multi_candidate = 0
    live_ids: set[int] = set()
    model_ids: set[int] = set()
    live_to_model: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        for slot in ("p1", "p2"):
            slots += 1
            live = _numeric(row, f"resolution_{slot}_live")
            model = _numeric(row, f"resolution_{slot}_model")
            method = row.get(f"resolution_{slot}_method")
            if live is not None:
                live_ids.add(int(live))
            if model is not None:
                model_ids.add(int(model))
            if live is not None and model is not None:
                live_to_model[int(live)].add(int(model))
            if method:
                methods[str(method)] += 1
            else:
                missing += 1
            if str(row.get(f"resolution_{slot}_bridged")).lower() == "true":
                bridged += 1
            if str(method) == "AMBIGUOUS":
                ambiguous += 1
            candidate_count = _numeric(row, f"resolution_{slot}_candidate_count")
            if candidate_count is not None and candidate_count > 1:
                multi_candidate += 1
    split_identities = sum(1 for values in live_to_model.values() if len(values) > 1)
    merge_groups: dict[int, set[int]] = defaultdict(set)
    for live, models in live_to_model.items():
        for model in models:
            merge_groups[model].add(live)
    return {
        "rows": len(rows),
        "player_slots": slots,
        "classified_slots": sum(methods.values()),
        "missing_method_slots": missing,
        "method_gap_vs_slots": slots - sum(methods.values()),
        "methods": dict(methods),
        "bridged_slots": bridged,
        "bridged_share": bridged / slots if slots else None,
        "ambiguous_slots": ambiguous,
        "multi_candidate_slots": multi_candidate,
        "unique_live_players": len(live_ids),
        "unique_model_players": len(model_ids),
        "live_players_with_multiple_models": split_identities,
        "model_players_receiving_multiple_live_ids": sum(
            1 for values in merge_groups.values() if len(values) > 1
        ),
        "identity_merge_risk_note": (
            "A model id receiving multiple live ids is expected for fragmented source "
            "identities; each such merge must have one unambiguous alias/mapping. "
            "AMBIGUOUS or multi-candidate slots are the fail-closed risk set."
        ),
    }


def recovery_summary(rows: Sequence[Row]) -> dict[str, Any]:
    direct = []
    indirect = []
    for row in rows:
        delta = abs(float(row["p1_corrected"]) - float(row["p1_original"]))
        if delta <= 1e-9:
            continue
        bridged = (
            str(row.get("resolution_p1_bridged")).lower() == "true"
            or str(row.get("resolution_p2_bridged")).lower() == "true"
        )
        recovered = max(
            0.0,
            float(row.get("overall_matches_corrected_p1", 0) or 0)
            - float(row.get("overall_matches_original_p1", 0) or 0),
        ) + max(
            0.0,
            float(row.get("overall_matches_corrected_p2", 0) or 0)
            - float(row.get("overall_matches_original_p2", 0) or 0),
        )
        if bridged and recovered > 0:
            direct.append(row)
        else:
            indirect.append(row)
    def block(group: Sequence[Row]) -> dict[str, Any]:
        if not group:
            return {"n": 0}
        return {
            "n": len(group),
            "mean_abs_probability_delta": mean(
                abs(float(row["p1_corrected"]) - float(row["p1_original"])) for row in group
            ),
            "mean_abs_overall_elo_delta": mean(
                (
                    abs(float(row["overall_rating_corrected_p1"]) - float(row["overall_rating_original_p1"]))
                    + abs(float(row["overall_rating_corrected_p2"]) - float(row["overall_rating_original_p2"]))
                )
                / 2.0
                for row in group
            ),
            "recovered_overall_matches_total": sum(
                max(0.0, float(row["overall_matches_corrected_p1"]) - float(row["overall_matches_original_p1"]))
                + max(0.0, float(row["overall_matches_corrected_p2"]) - float(row["overall_matches_original_p2"]))
                for row in group
            ),
        }
    direct_ids = {int(row["prediction_id"]) for row in direct}
    indirect_ids = {int(row["prediction_id"]) for row in indirect}
    return {
        "direct": block(direct),
        "indirect_only": block(indirect),
        "mutually_exclusive": not (direct_ids & indirect_ids),
        "direct_ids_count": len(direct_ids),
        "indirect_ids_count": len(indirect_ids),
        "union_count": len(direct_ids | indirect_ids),
    }


def inactivity_audit(
    rows: Sequence[Row],
    *,
    ranking_key: str,
    history_suffix: str,
    k: int = 800,
) -> dict[str, Any]:
    """Accuracy by the shorter of the two players' observed inactivity gaps."""
    buckets = {
        "le_7_days": (0.0, 7.0),
        "8_to_30_days": (7.0, 30.0),
        "31_to_90_days": (30.0, 90.0),
        "gt_90_days": (90.0, float("inf")),
    }
    selected = rank_rows(rows, ranking_key)[:k]
    result: dict[str, Any] = {
        "k": len(selected),
        "history_suffix": history_suffix,
        "bucket_rule": "min(inactivity p1, inactivity p2); missing values are a separate bucket",
        "buckets": {},
    }
    for label, (lower, upper) in buckets.items():
        group = []
        for row in selected:
            # Use the shorter gap so one long layoff does not hide an active opponent.
            values = [
                value
                for value in (
                    _numeric(row, f"inactivity_days_{history_suffix}_p1"),
                    _numeric(row, f"inactivity_days_{history_suffix}_p2"),
                )
                if value is not None
            ]
            gap = min(values) if values else None
            if gap is not None and lower <= gap < upper:
                group.append(row)
        if not group:
            result["buckets"][label] = {"n": 0}
            continue
        pairs = [(float(row[ranking_key]), int(row["y"])) for row in group]
        block = _summarise_scores(pairs)
        result["buckets"][label] = {
            "n": len(group),
            "wins": int(block["wins"]),
            "accuracy": block["accuracy"],
            "brier": block["brier"],
            "log_loss": block["log_loss"],
            "mean_confidence": block["mean_confidence"],
        }
    missing = []
    for row in selected:
        values = [
            value
            for value in (
                _numeric(row, f"inactivity_days_{history_suffix}_p1"),
                _numeric(row, f"inactivity_days_{history_suffix}_p2"),
            )
            if value is not None
        ]
        if not values:
            missing.append(row)
    if missing:
        pairs = [(float(row[ranking_key]), int(row["y"])) for row in missing]
        block = _summarise_scores(pairs)
        result["buckets"]["missing"] = {
            "n": len(missing),
            "wins": int(block["wins"]),
            "accuracy": block["accuracy"],
        }
    return result


def surface_history_audit(rows: Sequence[Row], key: str) -> dict[str, Any]:
    """How much of the effective rating comes from surface history."""
    buckets = {
        "0_surface_matches": (0, 1),
        "1_to_4": (1, 5),
        "5_to_9": (5, 10),
        "10_plus": (10, 10**9),
    }
    result: dict[str, Any] = {"buckets": {}, "surface_weight_rule": "w = min(surface_matches, 10)/10 * 0.5"}
    weights: list[float] = []
    for row in rows:
        for slot in ("p1", "p2"):
            surface_matches = _numeric(row, f"surface_matches_original_{slot}")
            if surface_matches is not None:
                weights.append(min(surface_matches, 10) / 10.0 * 0.5)
    if weights:
        result["surface_weight"] = {
            "mean": mean(weights),
            "median": median(weights),
            "share_zero": sum(1 for value in weights if value == 0) / len(weights),
            "share_max": sum(1 for value in weights if value >= 0.5) / len(weights),
        }
    for label, (lower, upper) in buckets.items():
        group = [
            row
            for row in rows
            if lower
            <= min(
                _numeric(row, "surface_matches_original_p1") or 0,
                _numeric(row, "surface_matches_original_p2") or 0,
            )
            < upper
        ]
        pairs = [(float(row[key]), int(row["y"])) for row in group]
        block = _summarise_scores(pairs)
        result["buckets"][label] = {
            "n": len(group),
            "wins": int(block.get("wins", 0)),
            "accuracy": block.get("accuracy"),
            "brier": block.get("brier"),
            "log_loss": block.get("log_loss"),
            "mean_confidence": block.get("mean_confidence"),
        }
    return result


def timestamp_audit(rows: Sequence[Row]) -> dict[str, Any]:
    """Precision and ordering checks for historical timestamps."""
    parsed = 0
    subsecond = 0
    as_of_not_before_start = 0
    missing_start = 0
    same_as_of_and_start = 0
    for row in rows:
        start = row.get("match_start_at")
        as_of = row.get("as_of")
        if not start:
            missing_start += 1
        for value in (start, as_of):
            if not value:
                continue
            text = str(value)
            parsed += 1
            if "." in text.split("+")[0]:
                subsecond += 1
        if start and as_of:
            if str(as_of) >= str(start):
                as_of_not_before_start += 1
            if str(as_of)[:19] == str(start)[:19]:
                same_as_of_and_start += 1
    return {
        "rows": len(rows),
        "timestamp_values": parsed,
        "subsecond_precision_values": subsecond,
        "subsecond_share": subsecond / parsed if parsed else None,
        "missing_match_start": missing_start,
        "prediction_as_of_not_before_start": as_of_not_before_start,
        "as_of_equal_to_start_to_the_second": same_as_of_and_start,
        "note": (
            "as_of is the forecast cutoff; it must be strictly before match_start_at. "
            "Sub-second precision is preserved in the audit but not used for ranking."
        ),
    }


def load_population_csv(path: str) -> list[dict[str, Any]]:
    """Load the committed sanitised fixture population without external packages."""
    import csv

    integer_columns = {
        "prediction_id",
        "match_id",
        "prediction_run_id",
        "match_version_id",
        "best_of_sets",
        "season_year",
        "winner_side",
        "y",
        "resolution_p1_live",
        "resolution_p1_model",
        "resolution_p2_live",
        "resolution_p2_model",
        "resolution_p1_candidate_count",
        "resolution_p2_candidate_count",
    }
    float_columns = {
        "p1_original",
        "p1_corrected",
        "overall_matches_original_p1",
        "overall_matches_original_p2",
        "surface_matches_original_p1",
        "surface_matches_original_p2",
        "overall_rating_original_p1",
        "overall_rating_original_p2",
        "surface_rating_original_p1",
        "surface_rating_original_p2",
        "effective_rating_original_p1",
        "effective_rating_original_p2",
        "overall_matches_corrected_p1",
        "overall_matches_corrected_p2",
        "surface_matches_corrected_p1",
        "surface_matches_corrected_p2",
        "overall_rating_corrected_p1",
        "overall_rating_corrected_p2",
        "surface_rating_corrected_p1",
        "surface_rating_corrected_p2",
        "effective_rating_corrected_p1",
        "effective_rating_corrected_p2",
        "inactivity_days_original_p1",
        "inactivity_days_original_p2",
        "inactivity_days_corrected_p1",
        "inactivity_days_corrected_p2",
        "market_p1",
        "market_age_hours",
    }
    rows: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, Any] = {}
            for name, value in raw.items():
                if value is None or value == "":
                    row[name] = None
                elif name in integer_columns:
                    row[name] = int(value)
                elif name in float_columns:
                    row[name] = float(value)
                elif name in {"qualifying_flag", "resolution_p1_bridged", "resolution_p2_bridged"}:
                    row[name] = str(value).lower() == "true"
                else:
                    row[name] = value
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Whole-population report
# ---------------------------------------------------------------------------
def evaluate_population(
    rows: Sequence[Row],
    *,
    development_end: str = "2026-10-31",
    top_k: int = 800,
    bootstrap: bool = True,
) -> dict[str, Any]:
    if not rows:
        raise ValueError("empty population")
    if len({int(row["prediction_id"]) for row in rows}) != len(rows):
        raise ValueError("duplicate prediction_id in population")
    for row in rows:
        if int(row["y"]) not in (0, 1):
            raise ValueError("outcome y must be 0/1")
        if int(row["winner_side"]) not in (1, 2):
            raise ValueError("winner_side must be 1/2")

    original = evaluate_coverage(rows, "p1_original", bootstrap=bootstrap)
    corrected = evaluate_coverage(rows, "p1_corrected", bootstrap=bootstrap)
    # Attach a day-block interval for the corrected-minus-original contrast.
    for fraction in COVERAGE_POINTS:
        k = coverage_count(len(rows), fraction)
        key = f"{fraction:.2f}"
        original["points"][key]["difference_corrected_minus_original"] = (
            paired_day_block_bootstrap(
                rank_rows(rows, "p1_original")[:k],
                rank_rows(rows, "p1_corrected")[:k],
                "p1_original",
                "p1_corrected",
            )
            if bootstrap
            else None
        )

    four = four_way(rows, k=top_k)
    cohorts = cohort_comparison(rows, k=top_k)
    risk = {
        "original": risk_coverage(rows, "p1_original"),
        "corrected": risk_coverage(rows, "p1_corrected"),
    }
    chronological = {
        "original": chronological_validation(
            rows, development_end=development_end, key="p1_original"
        ),
        "corrected": chronological_validation(
            rows, development_end=development_end, key="p1_corrected"
        ),
    }
    walk_forward = {
        "original": {
            f"{fraction:.2f}": weekly_walk_forward(rows, key="p1_original", fraction=fraction)
            for fraction in (0.40, 0.45, 0.50)
        },
        "corrected": {
            f"{fraction:.2f}": weekly_walk_forward(rows, key="p1_corrected", fraction=fraction)
            for fraction in (0.40, 0.45, 0.50)
        },
    }

    top_key = development_end
    checks = {
        "settled_population_is_3223": len(rows) == 3223,
        "top800_original_is_596": four["wins"]["A"] == 596,
        "top800_corrected_is_655": four["wins"]["D"] == 655,
        "four_way_B_is_589": four["wins"]["B"] == 589,
        "four_way_C_is_646": four["wins"]["C"] == 646,
        "shared_is_509": cohorts["shared"]["n"] == 509,
        "removed_is_291": cohorts["removed"]["n"] == 291,
        "added_is_291": cohorts["added"]["n"] == 291,
        "shared_side_changes_is_0": cohorts["shared"]["side_changes"] == 0,
        "shared_probability_changes_is_495": cohorts["shared"]["probability_changes"] == 495,
        "direct_plus_indirect_is_affected": (
            recovery_summary(rows)["direct"]["n"] + recovery_summary(rows)["indirect_only"]["n"]
            == sum(
                1
                for row in rows
                if abs(float(row["p1_corrected"]) - float(row["p1_original"])) > 1e-9
            )
        ),
    }

    return {
        "version": VERSION,
        "classification": "CORRECTED_RETROSPECTIVE_RESEARCH",
        "can_promote": False,
        "production_changed": False,
        "population": {
            "n": len(rows),
            "tour": dict(Counter(str(row["tour"]) for row in rows)),
            "competition_tier": dict(Counter(str(row["competition_tier"]) for row in rows)),
            "rounds": dict(Counter(str(row.get("round_code")) for row in rows)),
            "date_min": min(_day(row) for row in rows),
            "date_max": max(_day(row) for row in rows),
            "development_end_inclusive": development_end,
            "holdout_start": min(
                (_day(row) for row in rows if _day(row) > top_key), default=None
            ),
            "holdout_n": sum(1 for row in rows if _day(row) > top_key),
        },
        "coverage_experiment": {
            "coverage_points": list(COVERAGE_POINTS),
            "selection_count_rule": "floor(fraction * n); 100% selects all n",
            "ranking_rule": "descending confidence; ties by ascending prediction_id then match_id",
            "original": original,
            "corrected": corrected,
        },
        "risk_coverage": risk,
        "four_way_top800": four,
        "cohort_comparison_top800": cohorts,
        "chronological_frozen_thresholds": chronological,
        "weekly_walk_forward": walk_forward,
        "provenance": provenance_summary(rows),
        "recovery": recovery_summary(rows),
        "surface_history": {
            "original": surface_history_audit(rows, "p1_original"),
            "corrected": surface_history_audit(rows, "p1_corrected"),
        },
        "inactivity": {
            "original_top800": inactivity_audit(
                rows, ranking_key="p1_original", history_suffix="original", k=top_k
            ),
            "corrected_top800": inactivity_audit(
                rows, ranking_key="p1_corrected", history_suffix="corrected", k=top_k
            ),
        },
        "timestamps": timestamp_audit(rows),
        "validation": checks,
        "limitations": [
            "Historical development reuse, not untouched validation.",
            "Top-k coverage is a retrospective ranking, not a deployable live policy.",
            "The 3,223 settled fixtures are a subset of issued targets; full ATP/Challenger coverage is not established.",
            "Order-averaged four-way attribution is algebraic, not causal.",
            "No production activation, migration, deployment or provider spend.",
        ],
    }
