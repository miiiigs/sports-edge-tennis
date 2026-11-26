"""Six-way forecasting comparison around corrected Agent 1 and Manager C.

Research only. Read-only production access, no policy change, no threshold
change, no new production Manager rule and no odds fabrication.

Arms
----
A  corrected Agent 1 alone
B  corrected Agent 1 + Agent 2, simple probability average
C  corrected Agent 1 + Agent 2, one weight fitted only on earlier fixtures
D  corrected Agent 1 + Agent 3 where Agent 3 is active (evidence support kept)
E  existing Manager C rule with corrected Agent 1 substituted
F  all available agents, equal-weight average (at least two agents)
R1 original Agent 1 alone (reference control)
R2 original Manager C (reference control)

The evaluation fixtures, cutoffs, outcomes and frozen thresholds are identical
to the registered Manager C identity comparison. Nothing is retrained or
refrozen. Agent 3 abstentions are never relaxed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

import psycopg

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES,
    _utc,
    _wilson,
)
from sports_edge_tennis.validation.manager_c_challenger_history import _sha
from sports_edge_tennis.validation.manager_c_identity_comparison import build_rows
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY
from sports_edge_tennis.validation.manager_c_original_source import (
    HISTORICAL_AS_OF,
    TRAIN_START,
    VALIDATION_END,
)

VERSION = "SIX_WAY_MANAGER_COMPARISON_V1"
A2 = CODES[1]
A3 = CODES[2]

ARMS = (
    "A_A1_CORRECTED",
    "B_A1_A2_MEAN",
    "C_A1_A2_WEIGHTED",
    "D_A1_A3",
    "E_MANAGER_C_CORRECTED",
    "F_ALL_AVAILABLE",
    "R1_A1_ORIGINAL",
    "R2_MANAGER_C_ORIGINAL",
)
BOOTSTRAP_ITERATIONS = 4000
BOOTSTRAP_SEED = 20261126
CALIBRATION_BINS = 10
WEIGHT_GRID = tuple(round(i / 20, 2) for i in range(21))


def _is_settled(row: dict) -> bool:
    return row.get("winner") in (1, 2)


def _candidate(p1: float | None) -> tuple[int, float] | None:
    """Return (side, p1). Exactly 0.5 is an abstention, not a coin flip."""
    if p1 is None:
        return None
    value = float(p1)
    if not math.isfinite(value) or not 0 < value < 1:
        return None
    if abs(value - .5) < 1e-12:
        return None
    return (1 if value > .5 else 2, value)


def _mean(values: Iterable[float]) -> float | None:
    items = [float(value) for value in values]
    return sum(items) / len(items) if items else None


def _weighted(p1_a1: float | None, p1_a2: float | None,
              weight: float) -> tuple[int, float] | None:
    if p1_a1 is None or p1_a2 is None:
        return None
    return _candidate(weight * float(p1_a1) + (1 - weight) * float(p1_a2))


def _arm_candidates(rows: list[dict], weight: float) -> dict[str, dict[int, tuple[int, float] | None]]:
    """Build every arm from the same per-fixture evidence."""
    out: dict[str, dict[int, tuple[int, float] | None]] = {
        arm: {} for arm in ARMS}
    for row in rows:
        mid = int(row["match_id"])
        agents = row.get("agents_p1") or {}
        corrected = row.get("a1_bridged_replay_p1")
        original = row.get("a1_stored_p1")
        out["A_A1_CORRECTED"][mid] = _candidate(corrected)
        out["B_A1_A2_MEAN"][mid] = _candidate(_mean(
            [corrected, agents[A2]]) if corrected is not None and A2 in agents else None)
        out["C_A1_A2_WEIGHTED"][mid] = _weighted(corrected, agents.get(A2), weight)
        out["D_A1_A3"][mid] = _candidate(_mean(
            [corrected, agents[A3]]) if corrected is not None and A3 in agents else None)
        out["E_MANAGER_C_CORRECTED"][mid] = row["candidates"]["B"]
        available = []
        if corrected is not None:
            available.append(corrected)
        for code in (A2, A3):
            if code in agents:
                available.append(agents[code])
        out["F_ALL_AVAILABLE"][mid] = (
            _candidate(_mean(available)) if len(available) >= 2 else None)
        out["R1_A1_ORIGINAL"][mid] = _candidate(original)
        out["R2_MANAGER_C_ORIGINAL"][mid] = row["candidates"][POLICY]
    return out


def fit_weight(rows: list[dict], grid: tuple[float, ...] = WEIGHT_GRID) -> dict[str, Any]:
    """Fit one A1 weight on earlier settled fixtures only.

    The returned weight is the midpoint of the best-scoring plateau. A flat
    objective therefore does not silently select an endpoint.
    """
    pairs = []
    for row in rows:
        if not _is_settled(row):
            continue
        p1 = row.get("a1_bridged_replay_p1")
        agents = row.get("agents_p1") or {}
        if p1 is None or A2 not in agents:
            continue
        pairs.append((float(p1), float(agents[A2]), int(row["winner"] == 1)))
    if len(pairs) < 30:
        return {"available": False, "n": len(pairs),
                "blocker": "INSUFFICIENT_EARLIER_DUAL_AGENT_FIXTURES"}
    scanned = []
    for weight in grid:
        brier = sum((weight * p1 + (1 - weight) * p2 - y) ** 2
                    for p1, p2, y in pairs) / len(pairs)
        scanned.append({"weight": weight, "brier": brier})
    best = min(item["brier"] for item in scanned)
    plateau = [item["weight"] for item in scanned
               if abs(item["brier"] - best) <= 1e-12]
    chosen = plateau[len(plateau) // 2]
    return {
        "available": True, "n": len(pairs), "chosen_weight_a1": chosen,
        "plateau": plateau, "best_brier": best,
        "scanned": scanned,
        "fit_cohort": "earlier_than_validation_start_only",
        "note": ("One scalar weight, no per-tour or per-surface fitting. The "
                 "evaluation window is never used to choose the weight."),
    }


def _pairs(rows: list[dict], candidates: dict[int, tuple[int, float] | None]
           ) -> list[tuple[float, int]]:
    out = []
    for row in rows:
        pick = candidates.get(int(row["match_id"]))
        if pick is None or not _is_settled(row):
            continue
        out.append((float(pick[1]), int(row["winner"] == 1)))
    return out


def _brier(pairs: list[tuple[float, int]]) -> float | None:
    return (sum((p - y) ** 2 for p, y in pairs) / len(pairs)) if pairs else None


def _log_loss(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return -sum(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))
                for p, y in pairs) / len(pairs)


def _ece(pairs: list[tuple[float, int]], bins: int = CALIBRATION_BINS) -> dict[str, Any]:
    if not pairs:
        return {"ece": None, "bins": []}
    grouped: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in pairs:
        index = min(bins - 1, max(0, int(p * bins)))
        grouped[index].append((p, y))
    total = len(pairs)
    ece = 0.0
    detail = []
    for index, group in enumerate(grouped):
        if not group:
            detail.append({"lo": index / bins, "hi": (index + 1) / bins,
                           "n": 0, "mean_p": None, "observed": None})
            continue
        mean_p = sum(p for p, _ in group) / len(group)
        observed = sum(y for _, y in group) / len(group)
        ece += len(group) / total * abs(mean_p - observed)
        detail.append({"lo": index / bins, "hi": (index + 1) / bins,
                       "n": len(group), "mean_p": mean_p,
                       "observed": observed})
    return {"ece": ece, "bins": detail}


def _logistic_calibration(pairs: list[tuple[float, int]]) -> dict[str, Any]:
    """Intercept/slope from y ~ a + b*logit(p), pure Newton-Raphson."""
    if len(pairs) < 10:
        return {"available": False, "n": len(pairs)}
    xs, ys = [], []
    for p, y in pairs:
        clipped = min(max(p, 1e-6), 1 - 1e-6)
        xs.append(math.log(clipped / (1 - clipped)))
        ys.append(float(y))
    a = b = 0.0
    for _ in range(60):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            z = a + b * x
            q = 1 / (1 + math.exp(-max(-35.0, min(35.0, z))))
            w = max(q * (1 - q), 1e-12)
            g0 += y - q
            g1 += (y - q) * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a += da
        b += db
        if abs(da) + abs(db) < 1e-10:
            break
    return {"available": True, "n": len(pairs), "intercept": a, "slope": b}


def group_metrics(rows: list[dict],
                  candidates: dict[int, tuple[int, float] | None]) -> dict[str, Any]:
    """Metrics on the arm's covered, settled fixtures in ``rows``."""
    settled = [row for row in rows if _is_settled(row)]
    covered = [row for row in settled
               if candidates.get(int(row["match_id"])) is not None]
    pairs = _pairs(covered, candidates)
    if not pairs:
        return {"eligible": len(settled), "covered": 0, "coverage": 0.0,
                "blocker": "NO_COVERED_SETTLED_FIXTURES"}
    picks = [candidates[int(row["match_id"])] for row in covered]
    wins = sum(pick[0] == int(row["winner"]) for pick, row in zip(picks, covered))
    expected = sum(max(p, 1 - p) for p, _ in pairs)
    calibration = _logistic_calibration(pairs)
    return {
        "eligible": len(settled), "covered": len(covered),
        "coverage": len(covered) / len(settled) if settled else None,
        "wins": wins, "losses": len(covered) - wins,
        "accuracy": wins / len(covered),
        "accuracy_wilson95": _wilson(wins, len(covered)),
        "expected_wins": expected, "residual": wins - expected,
        "mean_selected_probability": sum(max(p, 1 - p) for p, _ in pairs) / len(pairs),
        "brier": _brier(pairs), "log_loss": _log_loss(pairs),
        "ece": _ece(pairs), "calibration": calibration,
        "ids_sha256": _sha(sorted(int(row["match_id"]) for row in covered)),
    }


def common_support(rows: list[dict],
                   candidates: dict[str, dict[int, tuple[int, float] | None]],
                   arms: Iterable[str] | None = None) -> list[dict]:
    selected = tuple(arms) if arms is not None else ARMS
    settled = [row for row in rows if _is_settled(row)]
    return [
        row for row in settled
        if all(candidates[arm].get(int(row["match_id"])) is not None
               for arm in selected)
    ]


def available_arms(rows: list[dict],
                    candidates: dict[str, dict[int, tuple[int, float] | None]]
                    ) -> list[str]:
    """Arms that cover at least one settled fixture in this cohort."""
    return [arm for arm in ARMS
            if group_metrics(rows, candidates[arm]).get("covered", 0) > 0]


def disagreement(rows: list[dict],
                 candidates: dict[str, dict[int, tuple[int, float] | None]]) -> dict[str, Any]:
    matrix: dict[str, Any] = {}
    for i, arm_a in enumerate(ARMS):
        for arm_b in ARMS[i + 1:]:
            shared = [row for row in rows if _is_settled(row)
                      and candidates[arm_a].get(int(row["match_id"])) is not None
                      and candidates[arm_b].get(int(row["match_id"])) is not None]
            if not shared:
                matrix[f"{arm_a}|{arm_b}"] = {"n": 0}
                continue
            pa = [candidates[arm_a][int(r["match_id"])][1] for r in shared]
            pb = [candidates[arm_b][int(r["match_id"])][1] for r in shared]
            side_flips = sum(
                candidates[arm_a][int(r["match_id"])][0]
                != candidates[arm_b][int(r["match_id"])][0] for r in shared)
            matrix[f"{arm_a}|{arm_b}"] = {
                "n": len(shared),
                "mean_abs_p1_difference": sum(abs(a - b) for a, b in zip(pa, pb)) / len(shared),
                "side_disagreement": side_flips / len(shared),
                "p1_correlation": _correlation(pa, pb),
            }
    agent_corr = {}
    for code_a, code_b in ((A2, A3),):
        pairs = [(row["agents_p1"][code_a], row["agents_p1"][code_b])
                 for row in rows
                 if code_a in (row.get("agents_p1") or {})
                 and code_b in (row.get("agents_p1") or {})]
        agent_corr[f"{code_a}|{code_b}"] = {
            "n": len(pairs),
            "p1_correlation": _correlation(
                [p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None,
        }
    return {"arm_pairwise": matrix, "agent_pairwise": agent_corr}


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def error_correlation(rows: list[dict],
                      candidates: dict[str, dict[int, tuple[int, float] | None]]
                      ) -> dict[str, Any]:
    """Pairwise correlation of per-fixture Brier contributions."""
    out: dict[str, Any] = {}
    for i, arm_a in enumerate(ARMS):
        for arm_b in ARMS[i + 1:]:
            shared = [row for row in rows if _is_settled(row)
                      and candidates[arm_a].get(int(row["match_id"])) is not None
                      and candidates[arm_b].get(int(row["match_id"])) is not None]
            if len(shared) < 3:
                out[f"{arm_a}|{arm_b}"] = {"n": len(shared)}
                continue
            ea = []
            eb = []
            for row in shared:
                y = int(row["winner"] == 1)
                ea.append((candidates[arm_a][int(row["match_id"])][1] - y) ** 2)
                eb.append((candidates[arm_b][int(row["match_id"])][1] - y) ** 2)
            out[f"{arm_a}|{arm_b}"] = {
                "n": len(shared), "brier_contribution_correlation": _correlation(ea, eb)}
    return out


def _delta_components(row: dict, arm_a: str, arm_b: str,
                      candidates: dict[str, dict[int, tuple[int, float] | None]]
                      ) -> tuple[float, float, float]:
    y = int(row["winner"] == 1)
    pa = candidates[arm_a][int(row["match_id"])]
    pb = candidates[arm_b][int(row["match_id"])]
    brier = (pa[1] - y) ** 2 - (pb[1] - y) ** 2
    acc = int(pa[0] == row["winner"]) - int(pb[0] == row["winner"])
    res = ((int(pa[0] == row["winner"]) - max(pa[1], 1 - pa[1]))
           - (int(pb[0] == row["winner"]) - max(pb[1], 1 - pb[1])))
    return brier, acc, res


def paired_bootstrap(rows: list[dict],
                     candidates: dict[str, dict[int, tuple[int, float] | None]],
                     arm_a: str, arm_b: str, *,
                     cluster_key: str = "match_id") -> dict[str, Any]:
    shared = [row for row in rows if _is_settled(row)
              and candidates[arm_a].get(int(row["match_id"])) is not None
              and candidates[arm_b].get(int(row["match_id"])) is not None]
    clusters: dict[Any, list[dict]] = defaultdict(list)
    for row in shared:
        clusters[row.get(cluster_key, row["match_id"])].append(row)
    keys = sorted(clusters, key=str)
    if len(keys) < 2 or not shared:
        return {"available": False, "clusters": len(keys)}
    rng = random.Random(BOOTSTRAP_SEED + 11)
    samples = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        picked = [row for _ in keys
                  for row in clusters[keys[rng.randrange(len(keys))]]]
        if not picked:
            continue
        totals = [0.0, 0.0, 0.0]
        for row in picked:
            parts = _delta_components(row, arm_a, arm_b, candidates)
            totals = [t + p for t, p in zip(totals, parts)]
        samples.append([t / len(picked) for t in totals])

    def interval(index: int) -> list[float] | None:
        values = sorted(s[index] for s in samples)
        if not values:
            return None
        return [values[int(.025 * len(values))],
                values[min(len(values) - 1, int(.975 * len(values)))]]

    return {
        "available": True, "clusters": len(keys),
        "iterations": BOOTSTRAP_ITERATIONS, "seed": BOOTSTRAP_SEED,
        "cluster_key": cluster_key, "paired_settled": len(shared),
        "brier_improvement_a_minus_b_95": interval(0),
        "accuracy_improvement_a_minus_b_95": interval(1),
        "residual_improvement_a_minus_b_95": interval(2),
        "interpretation": (
            "Positive Brier improvement means A scored worse than B. Accuracy "
            "improvement is A minus B. Intervals resample the stated cluster."),
    }


def reconcile(rows: list[dict],
              candidates: dict[str, dict[int, tuple[int, float] | None]],
              *, a_arm: str = "A_A1_CORRECTED",
              e_arm: str = "E_MANAGER_C_CORRECTED") -> dict[str, Any]:
    """Manager acceptance/rejection accounting against corrected Agent 1."""
    settled = [row for row in rows if _is_settled(row)
               and candidates[a_arm].get(int(row["match_id"])) is not None]
    counts = Counter()
    ids: dict[str, list[int]] = defaultdict(list)
    for row in settled:
        mid = int(row["match_id"])
        a = candidates[a_arm][mid]
        e = candidates[e_arm].get(mid)
        a_correct = a[0] == int(row["winner"])
        if e is None:
            counts["manager_abstained"] += 1
            if a_correct:
                counts["correct_a1_lost_to_abstention"] += 1
                ids["correct_a1_lost_to_abstention"].append(mid)
            else:
                counts["incorrect_a1_lost_to_abstention"] += 1
                ids["incorrect_a1_lost_to_abstention"].append(mid)
            continue
        e_correct = e[0] == int(row["winner"])
        if a_correct and not e_correct:
            counts["correct_a1_rejected_or_overruled"] += 1
            ids["correct_a1_rejected_or_overruled"].append(mid)
            if e[0] != a[0]:
                counts["correct_winner_changed_to_incorrect"] += 1
                ids["correct_winner_changed_to_incorrect"].append(mid)
            else:  # pragma: no cover - same side cannot differ in correctness
                counts["same_side_probability_only"] += 1
        elif not a_correct and e_correct:
            counts["incorrect_winner_corrected"] += 1
            ids["incorrect_winner_corrected"].append(mid)
        elif a_correct and e_correct:
            counts["both_correct"] += 1
        else:
            counts["both_incorrect"] += 1
    return {
        "a_arm": a_arm, "e_arm": e_arm, "settled_with_a1": len(settled),
        "counts": dict(sorted(counts.items())),
        "ids_sha256": {name: _sha(sorted(values)) for name, values in ids.items()},
        "coverage_lost_through_abstention": (
            counts["manager_abstained"] / len(settled) if settled else None),
    }


def _agent_dependency(rows: list[dict]) -> dict[str, Any]:
    pairs = [(row["a1_bridged_replay_p1"], row["agents_p1"][A2])
             for row in rows
             if row.get("a1_bridged_replay_p1") is not None
             and A2 in (row.get("agents_p1") or {})]
    orig = [(row["a1_stored_p1"], row["agents_p1"][A2])
            for row in rows
            if row.get("a1_stored_p1") is not None
            and A2 in (row.get("agents_p1") or {})]
    if len(pairs) < 3:
        return {"available": False, "n": len(pairs)}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
             if sxx > 0 else None)
    intercept = my - slope * mx if slope is not None else None
    corr = _correlation(xs, ys)
    return {
        "available": True, "n": len(pairs),
        "corrected_a1_vs_a2_correlation": corr,
        "corrected_a1_vs_a2_slope": slope,
        "corrected_a1_vs_a2_intercept": intercept,
        "corrected_a1_vs_a2_r_squared": corr * corr if corr is not None else None,
        "original_a1_vs_a2_correlation": _correlation(
            [p[0] for p in orig], [p[1] for p in orig]) if len(orig) >= 3 else None,
        "side_agreement": sum((a > .5) == (b > .5) for a, b in pairs) / len(pairs),
        "mean_abs_p1_difference": sum(abs(a - b) for a, b in pairs) / len(pairs),
        "code_level": {
            "agent2_imports_agent1": False,
            "direct_probability_dependency": False,
            "direct_rating_dependency": False,
            "shared_inputs": [
                "canonical match history and result observations",
                "PIT-visible stat observations",
            ],
            "agent1_inputs": ["surface Elo ratings and rating uncertainty"],
            "agent2_inputs": ["serve/return point model and hierarchical population"],
        },
    }


def _agent3_incremental(rows: list[dict],
                        candidates: dict[str, dict[int, tuple[int, float] | None]]
                        ) -> dict[str, Any]:
    with_a3 = [row for row in rows if _is_settled(row)
               and A3 in (row.get("agents_p1") or {})]
    f_minus_b = []
    for row in with_a3:
        mid = int(row["match_id"])
        if (candidates["F_ALL_AVAILABLE"].get(mid) is None
                or candidates["B_A1_A2_MEAN"].get(mid) is None):
            continue
        y = int(row["winner"] == 1)
        f_minus_b.append(
            (candidates["F_ALL_AVAILABLE"][mid][1] - y) ** 2
            - (candidates["B_A1_A2_MEAN"][mid][1] - y) ** 2)
    return {
        "settled": sum(_is_settled(row) for row in rows),
        "a3_active_settled": len(with_a3),
        "a3_abstained_settled": sum(_is_settled(row) for row in rows) - len(with_a3),
        "a3_coverage": (len(with_a3) / sum(_is_settled(row) for row in rows)
                        if rows else None),
        "f_minus_b_brier_on_a3_support": (
            sum(f_minus_b) / len(f_minus_b) if f_minus_b else None),
        "f_minus_b_n": len(f_minus_b),
        "evidence_requirements_relaxed": False,
        "note": ("Agent 3 remains active only where the stored production "
                 "support gate passed. D and F therefore have lower coverage; "
                 "they are never compared by silently imputing Agent 3."),
    }


def _scope(rows: list[dict]) -> dict[str, Any]:
    settled = [row for row in rows if _is_settled(row)]
    return {
        "settled": len(settled),
        "main_draw": sum(bool(row.get("main_draw_verified")) for row in settled),
        "qualifying": sum(not bool(row.get("main_draw_verified")) for row in settled),
        "ATP_MAIN": sum(row.get("tour") == "ATP_MAIN" for row in settled),
        "CHALLENGER": sum(row.get("tour") == "CHALLENGER" for row in settled),
        "other_tour": sum(row.get("tour") not in ("ATP_MAIN", "CHALLENGER")
                          for row in settled),
        "note": ("main_draw_verified is the existing qualifying_flag=False "
                 "definition. The ATP/Challenger split is tour_scope, not "
                 "competition_tier; both are reported so the earlier "
                 "main-draw/ATP conflation cannot recur."),
    }


def _cohort_report(rows: list[dict],
                   candidates: dict[str, dict[int, tuple[int, float] | None]]
                   ) -> dict[str, Any]:
    arms = available_arms(rows, candidates)
    common = common_support(rows, candidates, arms)
    all_arm_common = common_support(rows, candidates)
    blockers = [
        arm for arm in ARMS if arm not in arms
    ]
    return {
        "eligible_settled": sum(_is_settled(row) for row in rows),
        "complete_population": {
            arm: group_metrics(rows, candidates[arm]) for arm in ARMS},
        "common_support": {
            "arms": arms,
            "n": len(common),
            "ids_sha256": _sha(sorted(int(row["match_id"]) for row in common)),
            "metrics": {arm: group_metrics(common, candidates[arm]) for arm in ARMS},
        },
        "all_arm_common_support": {
            "n": len(all_arm_common),
            "arms_without_coverage": blockers,
            "blocker": (
                "ZERO_COVERAGE_ARM" if blockers else None),
        },
    }


def run(conn: psycopg.Connection, *, start=TRAIN_START, end=VALIDATION_END,
        as_of=HISTORICAL_AS_OF) -> dict[str, Any]:
    built = build_rows(conn, start=start, end=end, as_of=as_of,
                       include_early=True)
    rows = built["rows"]
    early = [row for row in rows if row["cohort"] == "early"]
    later = [row for row in rows if row["cohort"] == "later"]
    weight = fit_weight(early)
    chosen = weight.get("chosen_weight_a1", 0.5)
    candidates = _arm_candidates(later, chosen)
    arms = available_arms(later, candidates)
    common = common_support(later, candidates, arms)

    paired = {}
    for arm in ARMS:
        if arm == "A_A1_CORRECTED":
            continue
        paired[arm] = {
            "vs_A_match_cluster": paired_bootstrap(later, candidates,
                                                   "A_A1_CORRECTED", arm),
            "vs_A_favourite_cluster": paired_bootstrap(
                later, candidates, "A_A1_CORRECTED", arm,
                cluster_key="favourite_player_id"),
        }
    paired["E_MANAGER_C_CORRECTED_vs_B_A1_A2_MEAN"] = {
        "match_cluster": paired_bootstrap(
            later, candidates, "E_MANAGER_C_CORRECTED", "B_A1_A2_MEAN"),
        "favourite_cluster": paired_bootstrap(
            later, candidates, "E_MANAGER_C_CORRECTED", "B_A1_A2_MEAN",
            cluster_key="favourite_player_id"),
    }

    return {
        "version": VERSION,
        "status": "DESCRIPTIVE_HISTORICAL_RESEARCH_ONLY",
        "independent_forward_validation": False,
        "historical_evaluation_previously_inspected": True,
        "as_of": _utc(as_of).isoformat(),
        "policy": POLICY,
        "arms": list(ARMS),
        "arms_with_coverage": arms,
        "arms_without_coverage": [arm for arm in ARMS if arm not in arms],
        "eligible_fixtures": len(later),
        "early_fixtures": len(early),
        "weight_fit": weight,
        "all": _cohort_report(later, candidates),
        "atp_main": _cohort_report(
            [row for row in later if row.get("tour") == "ATP_MAIN"], candidates),
        "challenger": _cohort_report(
            [row for row in later if row.get("tour") == "CHALLENGER"], candidates),
        "main_draw": _cohort_report(
            [row for row in later if row.get("main_draw_verified")], candidates),
        "qualifying": _cohort_report(
            [row for row in later if not row.get("main_draw_verified")], candidates),
        "scope": _scope(later),
        "disagreement": disagreement(common or later, candidates),
        "error_correlation": error_correlation(common or later, candidates),
        "paired": paired,
        "reconciliation": {
            "all": reconcile(later, candidates),
            "atp_main": reconcile(
                [row for row in later if row.get("tour") == "ATP_MAIN"], candidates),
            "challenger": reconcile(
                [row for row in later if row.get("tour") == "CHALLENGER"], candidates),
        },
        "agent2_dependency": _agent_dependency(later),
        "agent3_incremental": _agent3_incremental(later, candidates),
        "a1_replay": {
            "targets": len(built["targets"]),
            "stored_a1_available": len(built["stored"]),
            "current_replayed": built["current_diag"],
            "bridged_replayed": built["bridged_diag"],
            "reproduction_vs_stored": {
                "compared": len(built["reproduction"]),
                "exact_1e-6": sum(d <= 1e-6 for d in built["reproduction"]),
                "within_0.005": sum(d <= .005 for d in built["reproduction"]),
            },
            "identity_mapping": {
                "resolved_players": sum(
                    built["mapping"].get(p, p) != p
                    for p in built["target_players"]),
                "target_players": len(built["target_players"]),
                "invariant_across_sampled_cutoffs": built["mapping_invariant"],
            },
        },
        "limitations": [
            "The November window was previously inspected; this is not independent forward validation.",
            "Corrected Agent 1 is a local replay; reproduction against stored Agent 1 is reported.",
            "Agent 2 and Agent 3 probabilities are the stored original forecasts; they are not re-run.",
            "Agent 3 support is never relaxed; D and F have lower coverage by construction.",
            "One scalar A1/A2 weight is fitted on earlier fixtures only; no per-tour tuning.",
            "Selections are not independent; match and favourite cluster intervals are both reported.",
            "No production Manager rule, threshold or model has been changed.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("authorized TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=20,
                         options="-c default_transaction_read_only=on") as conn:
        report = run(conn)
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
