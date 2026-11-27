"""Marginal-cohort and information-quality research for frozen Agent 1.

Research only.  This module never trains on outcomes, never changes a production
policy and never reads a database.  It consumes the committed, sanitised
settled-population export and the local identity ledger and produces
deterministic cohort statistics.

The frozen policies under study are the identity-bridged (corrected) Agent 1
thresholds:

* primary   ``0.668060``  (development 45 % rank, 2026-09-10..2026-10-31)
* secondary ``0.648708``  (development 50 % rank)
* conservative ``0.683286`` (development 40 % rank)

They are reproduced from the development period, never fitted on the November
holdout.  Nothing in this module may be used to move those thresholds.

Statistical conventions
-----------------------
Wilson
    Score interval for a single proportion.
Newcombe
    Hybrid score interval (method 10) for a difference of proportions.
Odds ratio
    Haldane-Anscombe corrected with a Woolf log interval.
SMD
    Cohen's d, error cohort minus correct cohort, for continuous factors.
Bootstrap
    Calendar-day block resampling (days are the dependence unit), deterministic
    seed.  Reported as a percentile interval and always labelled research-only.
FDR
    Benjamini-Hochberg over the exploratory factor screen, never confirmatory.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from statistics import mean, median
from typing import Any

VERSION = "A1_MARGINAL_RESEARCH_V1"

DEV_END = "2026-10-31"
EVAL_START = "2026-11-01"
EVAL_END = "2026-11-25"

CONSERVATIVE_THRESHOLD = 0.683286
PRIMARY_THRESHOLD = 0.668060
SECONDARY_THRESHOLD = 0.648708

CONFIDENCE_QUANTUM = 6
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 20261127
Z95 = 1.959963984540054

EXPERIENCE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("0-4", 0, 4),
    ("5-9", 5, 9),
    ("10-19", 10, 19),
    ("20-49", 20, 49),
    ("50-99", 50, 99),
    ("100+", 100, 10**9),
)

INACTIVITY_BANDS: tuple[tuple[str, float, float], ...] = (
    ("0-7d", 0.0, 7.0),
    ("8-30d", 7.000001, 30.0),
    ("31-90d", 30.000001, 90.0),
    ("91-180d", 90.000001, 180.0),
    ("181d+", 180.000001, 10**9),
)

CONFIDENCE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("0.50-0.60", 0.0, 0.599999),
    ("0.60-0.70", 0.599999, 0.699999),
    ("0.70-0.80", 0.699999, 0.799999),
    ("0.80-0.90", 0.799999, 0.899999),
    ("0.90-1.00", 0.899999, 1.000001),
)

RATING_GAP_BANDS: tuple[tuple[str, float, float], ...] = (
    ("0-49", 0.0, 49.999),
    ("50-99", 49.999, 99.999),
    ("100-199", 99.999, 199.999),
    ("200+", 199.999, 10**9),
)

SURFACE_EXPERIENCE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("0", 0, 0),
    ("1-4", 1, 4),
    ("5-9", 5, 9),
    ("10+", 10, 10**9),
)


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------
def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def confidence(p1: float) -> float:
    """Production confidence quantum: ``max(p1, 1-p1)`` rounded to 6 dp."""
    if not math.isfinite(p1) or not 0.0 <= p1 <= 1.0:
        raise ValueError(f"invalid probability {p1!r}")
    return round(max(p1, 1.0 - p1), CONFIDENCE_QUANTUM)


def selection_side(p1: float) -> int:
    if not math.isfinite(p1) or not 0.0 <= p1 <= 1.0:
        raise ValueError(f"invalid probability {p1!r}")
    return 1 if p1 >= 0.5 else 2


def logit(p: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError(f"logit requires 0<p<1, got {p!r}")
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def z_value(alpha: float = 0.05) -> float:
    """Two-sided normal critical value without SciPy."""
    # Acklam's inverse normal CDF approximation.
    p = 1.0 - alpha / 2.0
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
    )


def wilson_interval(wins: int, n: int, z: float = Z95) -> list[float] | None:
    if n <= 0:
        return None
    p = wins / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return [max(0.0, centre - half), min(1.0, centre + half)]


def newcombe_difference(w1: int, n1: int, w2: int, n2: int, z: float = Z95) -> dict[str, Any]:
    """Newcombe hybrid score interval for ``p1 - p2`` (method 10)."""
    if n1 <= 0 or n2 <= 0:
        return {"difference": None, "ci95": None}
    p1, p2 = w1 / n1, w2 / n2
    l1, u1 = wilson_interval(w1, n1, z)  # type: ignore[misc]
    l2, u2 = wilson_interval(w2, n2, z)  # type: ignore[misc]
    diff = p1 - p2
    lower = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    upper = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return {"difference": diff, "ci95": [max(-1.0, lower), min(1.0, upper)]}


def odds_ratio_interval(a: float, b: float, c: float, d: float, z: float = Z95) -> dict[str, Any]:
    """Haldane-Anscombe odds ratio with a Woolf log interval.

    ``a``/``b`` are the exposed failure/success cells and ``c``/``d`` the
    unexposed failure/success cells.  A zero cell adds 0.5 to every cell.
    """
    if min(a, b, c, d) == 0:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    odds = (a * d) / (b * c)
    se = math.sqrt(1.0 / a + 1.0 / b + 1.0 / c + 1.0 / d)
    return {
        "odds_ratio": odds,
        "ci95": [math.exp(math.log(odds) - z * se), math.exp(math.log(odds) + z * se)],
    }


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float | None:
    """Exact two-sided Fisher p for a 2x2 table (fixed margins)."""
    if min(a, b, c, d) < 0:
        raise ValueError("counts must be non-negative")
    row1, row2, col1 = a + b, c + d, a + c
    n = row1 + row2
    if n == 0:
        return None
    lo = max(0, col1 - row2)
    hi = min(row1, col1)
    log_den = _log_comb(n, col1)

    def prob(x: int) -> float:
        return math.exp(_log_comb(row1, x) + _log_comb(row2, col1 - x) - log_den)

    observed = prob(a)
    total = 0.0
    for x in range(lo, hi + 1):
        p = prob(x)
        if p <= observed + 1e-12:
            total += p
    return min(1.0, total)


def paired_proportion_difference(
    model_correct: Sequence[bool],
    market_correct: Sequence[bool],
    z: float = Z95,
) -> dict[str, Any]:
    """Paired difference ``model - market`` with McNemar exact p and Wald CI."""
    if len(model_correct) != len(market_correct) or not model_correct:
        return {"difference": None, "ci95": None, "mcnemar_p": None, "n": 0}
    n = len(model_correct)
    b = sum(1 for m, k in zip(model_correct, market_correct, strict=True) if m and not k)
    c = sum(1 for m, k in zip(model_correct, market_correct, strict=True) if k and not m)
    diff = (b - c) / n
    variance = ((b + c) - (b - c) ** 2 / n) / (n * n)
    se = math.sqrt(max(variance, 0.0))
    mcnemar = _binomial_two_sided(b, b + c) if (b + c) else 1.0
    return {
        "n": n,
        "difference": diff,
        "ci95": [diff - z * se, diff + z * se],
        "discordant_model_only": b,
        "discordant_market_only": c,
        "mcnemar_p": mcnemar,
    }


def _binomial_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial p-value (probability-ordering rule)."""
    if n <= 0:
        return 1.0
    probs = [math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(n + 1)]
    observed = probs[k]
    return min(1.0, sum(prob for prob in probs if prob <= observed + 1e-15))


def smd(x_error: Sequence[float], x_correct: Sequence[float]) -> dict[str, Any]:
    """Cohen's d (error minus correct) with an analytic normal interval."""
    n1, n2 = len(x_error), len(x_correct)
    if n1 < 2 or n2 < 2:
        return {"smd": None, "ci95": None, "n_error": n1, "n_correct": n2}
    m1, m2 = mean(x_error), mean(x_correct)
    s1 = _sample_var(x_error)
    s2 = _sample_var(x_correct)
    pooled = math.sqrt(((n1 - 1) * s1 + (n2 - 1) * s2) / (n1 + n2 - 2))
    if pooled == 0:
        return {"smd": None, "ci95": None, "n_error": n1, "n_correct": n2}
    d = (m1 - m2) / pooled
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
    return {
        "smd": d,
        "ci95": [d - Z95 * se, d + Z95 * se],
        "mean_error": m1,
        "mean_correct": m2,
        "n_error": n1,
        "n_correct": n2,
    }


def _sample_var(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / (n - 1)


def brier(pairs: Iterable[tuple[float, int]]) -> float | None:
    data = list(pairs)
    if not data:
        return None
    return mean((p - y) ** 2 for p, y in data)


def log_loss(pairs: Iterable[tuple[float, int]]) -> float | None:
    data = list(pairs)
    if not data:
        return None
    eps = 1e-15
    return -mean(y * math.log(min(max(p, eps), 1 - eps)) + (1 - y) * math.log(min(max(1 - p, eps), 1 - eps)) for p, y in data)


def calibration_slope(ps: Sequence[float], ys: Sequence[int]) -> dict[str, Any]:
    """Logistic recalibration ``y ~ a + b*logit(p)`` fitted by IRLS.

    ``b < 1`` means the model is overconfident in log-odds terms.  This is a
    descriptive reliability diagnostic, not a new probability model.  Returns
    ``None`` when the fit is separated or non-finite.
    """
    if len(ps) != len(ys) or len(ps) < 20:
        return {"n": len(ps), "slope": None, "intercept": None, "status": "INSUFFICIENT"}
    xs = [logit(min(max(p, 1e-6), 1 - 1e-6)) for p in ps]
    beta = [0.0, 1.0]  # intercept, slope
    for _ in range(100):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys, strict=True):
            eta = beta[0] + beta[1] * x
            mu = sigmoid(eta)
            w = max(mu * (1.0 - mu), 1e-12)
            r = y - mu
            g0 += r
            g1 += r * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            return {"n": len(ps), "slope": None, "intercept": None, "status": "SINGULAR"}
        step0 = (h11 * g0 - h01 * g1) / det
        step1 = (-h01 * g0 + h00 * g1) / det
        beta[0] += step0
        beta[1] += step1
        if max(abs(step0), abs(step1)) < 1e-10:
            break
    if not all(math.isfinite(v) for v in beta):
        return {"n": len(ps), "slope": None, "intercept": None, "status": "NON_FINITE"}
    return {
        "n": len(ps),
        "slope": beta[1],
        "intercept": beta[0],
        "status": "OK",
    }


def expected_calibration_error(
    ps: Sequence[float],
    ys: Sequence[int],
    bands: Sequence[tuple[str, float, float]] = CONFIDENCE_BANDS,
) -> dict[str, Any]:
    n = len(ps)
    if n == 0:
        return {"ece": None, "bands": []}
    total = 0.0
    rows = []
    for label, lo, hi in bands:
        idx = [i for i, p in enumerate(ps) if lo <= p <= hi]
        if not idx:
            continue
        conf = mean(ps[i] for i in idx)
        obs = mean(ys[i] for i in idx)
        total += len(idx) / n * abs(conf - obs)
        rows.append(
            {
                "band": label,
                "n": len(idx),
                "mean_confidence": conf,
                "observed": obs,
                "residual": conf - obs,
            }
        )
    return {"ece": total, "bands": rows}


def murphy_decomposition(
    ps: Sequence[float],
    ys: Sequence[int],
    bands: Sequence[tuple[str, float, float]] = CONFIDENCE_BANDS,
) -> dict[str, Any]:
    """Murphy reliability/resolution/uncertainty on fixed confidence bins."""
    n = len(ps)
    if n == 0:
        return {"reliability": None, "resolution": None, "uncertainty": None}
    base = mean(ys)
    reliability = resolution = 0.0
    for _label, lo, hi in bands:
        idx = [i for i, p in enumerate(ps) if lo <= p <= hi]
        if not idx:
            continue
        conf = mean(ps[i] for i in idx)
        obs = mean(ys[i] for i in idx)
        reliability += len(idx) / n * (conf - obs) ** 2
        resolution += len(idx) / n * (obs - base) ** 2
    return {
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": base * (1.0 - base),
        "brier_check": reliability - resolution + base * (1.0 - base),
    }


def benjamini_hochberg(pvalues: Sequence[float | None], alpha: float = 0.05) -> list[float | None]:
    """BH adjusted p-values; ``None`` entries are preserved."""
    indexed = [(i, p) for i, p in enumerate(pvalues) if p is not None]
    m = len(indexed)
    adjusted: list[float | None] = [None] * len(pvalues)
    if m == 0:
        return adjusted
    indexed.sort(key=lambda x: x[1])  # type: ignore[arg-type,return-value]
    running = 1.0
    for rank, (i, p) in enumerate(reversed(indexed), start=1):
        value = min(1.0, (p or 0.0) * m / (m - rank + 1))
        running = min(running, value)
        adjusted[i] = running
    return adjusted


def day_block_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    statistic: Callable[[Sequence[Mapping[str, Any]]], float | None],
    *,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Percentile interval from resampling whole calendar days."""
    by_day: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[str(row["date"])].append(row)
    days = sorted(by_day)
    if len(days) < 5:
        return {"status": "INSUFFICIENT_DAY_CLUSTERS", "days": len(days), "reps": 0}
    rng = random.Random(seed)
    values = []
    for _ in range(reps):
        sample: list[Mapping[str, Any]] = []
        for _ in range(len(days)):
            sample.extend(by_day[rng.choice(days)])
        value = statistic(sample)
        if value is not None and math.isfinite(value):
            values.append(value)
    if not values:
        return {"status": "NO_FINITE_REPLICATES", "days": len(days), "reps": reps}
    values.sort()

    def quantile(q: float) -> float:
        idx = int(q * (len(values) - 1))
        return values[idx]

    return {
        "status": "RESEARCH_ONLY_DAY_BLOCK_BOOTSTRAP",
        "days": len(days),
        "reps": reps,
        "seed": seed,
        "ci95": [quantile(0.025), quantile(0.975)],
        "median": quantile(0.5),
    }


# ---------------------------------------------------------------------------
# Population parsing
# ---------------------------------------------------------------------------
def _float(raw: Mapping[str, str], key: str) -> float | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _int(raw: Mapping[str, str], key: str) -> int | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    return int(float(value))


def normalise_round(code: str | None) -> str:
    if not code:
        return "UNKNOWN"
    token = code.strip().upper().replace(" ", "_")
    mapping = {
        "R128": "R128", "R64": "R64", "R32": "R32", "R16": "R16",
        "ROUND_OF_128": "R128", "ROUND_OF_64": "R64", "ROUND_OF_32": "R32",
        "ROUND_OF_16": "R16", "QUARTERFINAL": "QF", "QUARTERFINALS": "QF",
        "SEMIFINAL": "SF", "SEMIFINALS": "SF", "FINAL": "F", "F": "F",
        "QF": "QF", "SF": "SF", "Q1": "QUAL", "Q2": "QUAL", "Q3": "QUAL",
        "QUALIFYING": "QUAL", "RR": "RR", "ROUND_ROBIN": "RR",
    }
    return mapping.get(token, "OTHER")


def parse_population(raw_rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for raw in raw_rows:
        p1_original = _float(raw, "p1_original")
        p1_corrected = _float(raw, "p1_corrected")
        if p1_original is None or p1_corrected is None:
            continue
        winner_side = _int(raw, "winner_side")
        if winner_side not in (1, 2):
            continue
        side_original = selection_side(p1_original)
        side_corrected = selection_side(p1_corrected)
        matches_original = {
            "p1": _float(raw, "overall_matches_original_p1"),
            "p2": _float(raw, "overall_matches_original_p2"),
        }
        matches_corrected = {
            "p1": _float(raw, "overall_matches_corrected_p1"),
            "p2": _float(raw, "overall_matches_corrected_p2"),
        }
        surface_original = {
            "p1": _float(raw, "surface_matches_original_p1"),
            "p2": _float(raw, "surface_matches_original_p2"),
        }
        surface_corrected = {
            "p1": _float(raw, "surface_matches_corrected_p1"),
            "p2": _float(raw, "surface_matches_corrected_p2"),
        }
        overall_original = {
            "p1": _float(raw, "overall_rating_original_p1"),
            "p2": _float(raw, "overall_rating_original_p2"),
        }
        overall_corrected = {
            "p1": _float(raw, "overall_rating_corrected_p1"),
            "p2": _float(raw, "overall_rating_corrected_p2"),
        }
        surface_rating_original = {
            "p1": _float(raw, "surface_rating_original_p1"),
            "p2": _float(raw, "surface_rating_original_p2"),
        }
        surface_rating_corrected = {
            "p1": _float(raw, "surface_rating_corrected_p1"),
            "p2": _float(raw, "surface_rating_corrected_p2"),
        }
        effective_original = {
            "p1": _float(raw, "effective_rating_original_p1"),
            "p2": _float(raw, "effective_rating_original_p2"),
        }
        effective_corrected = {
            "p1": _float(raw, "effective_rating_corrected_p1"),
            "p2": _float(raw, "effective_rating_corrected_p2"),
        }
        inactivity_original = {
            "p1": _float(raw, "inactivity_days_original_p1"),
            "p2": _float(raw, "inactivity_days_original_p2"),
        }
        inactivity_corrected = {
            "p1": _float(raw, "inactivity_days_corrected_p1"),
            "p2": _float(raw, "inactivity_days_corrected_p2"),
        }
        resolution = {}
        for side in ("p1", "p2"):
            resolution[side] = {
                "live_id": _int(raw, f"resolution_{side}_live"),
                "model_id": _int(raw, f"resolution_{side}_model"),
                "method": raw.get(f"resolution_{side}_method") or "UNKNOWN",
                "evidence": raw.get(f"resolution_{side}_evidence") or "",
                "name_source": raw.get(f"resolution_{side}_name_source") or "",
                "candidate_count": _int(raw, f"resolution_{side}_candidate_count") or 0,
                "bridged": (raw.get(f"resolution_{side}_bridged") or "").lower() == "true",
            }
        observation = {
            "prediction_id": int(raw["prediction_id"]),
            "match_id": int(raw["match_id"]),
            "date": raw["date"],
            "as_of": raw["as_of"],
            "match_start_at": raw["match_start_at"],
            "tour": raw.get("tour") or "",
            "competition_tier": raw.get("competition_tier") or "",
            "round_code": normalise_round(raw.get("round_code")),
            "surface": raw.get("surface") or "",
            "qualifying": (raw.get("qualifying_flag") or "").lower() == "true",
            "season_year": _int(raw, "season_year"),
            "p1_original": p1_original,
            "p1_corrected": p1_corrected,
            "confidence_original": confidence(p1_original),
            "confidence_corrected": confidence(p1_corrected),
            "side_original": side_original,
            "side_corrected": side_corrected,
            "winner_side": winner_side,
            "correct_original": side_original == winner_side,
            "correct_corrected": side_corrected == winner_side,
            "matches_original": matches_original,
            "matches_corrected": matches_corrected,
            "surface_matches_original": surface_original,
            "surface_matches_corrected": surface_corrected,
            "overall_original": overall_original,
            "overall_corrected": overall_corrected,
            "surface_rating_original": surface_rating_original,
            "surface_rating_corrected": surface_rating_corrected,
            "effective_original": effective_original,
            "effective_corrected": effective_corrected,
            "inactivity_original": inactivity_original,
            "inactivity_corrected": inactivity_corrected,
            "resolution": resolution,
            "market_p1": _float(raw, "market_p1"),
            "market_age_hours": _float(raw, "market_age_hours"),
            "market_bookmaker_count": _int(raw, "market_bookmaker_count"),
        }
        observation["favourite_side"] = side_corrected
        observation["underdog_side"] = 2 if side_corrected == 1 else 1
        observation["side_changed"] = side_original != side_corrected
        observation["probability_delta"] = p1_corrected - p1_original
        # A market quote is eligible only if it existed at or before the forecast
        # and is no more than 24 hours old.  Quotes observed after the forecast
        # are excluded from every benchmark and factor to avoid look-ahead.
        age = observation["market_age_hours"]
        observation["market_eligible"] = (
            observation["market_p1"] is not None
            and age is not None
            and 0.0 <= age <= 24.0
        )
        observations.append(observation)
    return observations


# ---------------------------------------------------------------------------
# Cohort construction
# ---------------------------------------------------------------------------
def _ranked(rows: Sequence[Mapping[str, Any]], p_key: str) -> list[Mapping[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (-r[p_key], int(r["prediction_id"]), int(r["match_id"])),
    )


def coverage_count(n: int, fraction: float) -> int:
    if fraction == 1.0:
        return n
    return math.floor(fraction * n + 1e-9)


def development_thresholds(
    observations: Sequence[Mapping[str, Any]],
    fractions: Sequence[float] = (0.40, 0.45, 0.50),
) -> dict[str, Any]:
    """Exact confidence thresholds selected on the development period only."""
    dev = [o for o in observations if str(o["date"]) <= DEV_END]
    ranked = _ranked(dev, "confidence_corrected")
    out: dict[str, Any] = {"development_rows": len(dev)}
    for fraction in fractions:
        k = coverage_count(len(ranked), fraction)
        threshold = ranked[k - 1]["confidence_corrected"] if k > 0 else None
        out[f"{round(fraction * 100)}"] = {
            "fraction": fraction,
            "development_selections": k,
            "threshold": threshold,
        }
    return out


def holdout_rows(observations: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [o for o in observations if EVAL_START <= str(o["date"]) <= EVAL_END]


def frozen_cohorts(
    observations: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, list[Mapping[str, Any]]]:
    """Frozen-threshold cohorts on the untouched November holdout."""
    t40 = thresholds["40"]["threshold"]
    t45 = thresholds["45"]["threshold"]
    t50 = thresholds["50"]["threshold"]
    rows = holdout_rows(observations)
    return {
        "holdout": rows,
        "core_40": [o for o in rows if o["confidence_corrected"] >= t40],
        "marginal_40_45": [
            o for o in rows if t45 <= o["confidence_corrected"] < t40
        ],
        "base_45": [o for o in rows if o["confidence_corrected"] >= t45],
        "marginal_45_50": [
            o for o in rows if t50 <= o["confidence_corrected"] < t45
        ],
        "base_50": [o for o in rows if o["confidence_corrected"] >= t50],
        "below_50": [o for o in rows if o["confidence_corrected"] < t50],
    }


def retrospective_bands(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """The 3,223-row corrected ranking split at the 40/45/50 % ranks."""
    ranked = _ranked(observations, "confidence_corrected")
    n = len(ranked)
    k40, k45, k50 = coverage_count(n, 0.40), coverage_count(n, 0.45), coverage_count(n, 0.50)
    return {
        "ranked": ranked,
        "top_40": ranked[:k40],
        "band_40_45": ranked[k40:k45],
        "band_45_50": ranked[k45:k50],
        "band_below_50": ranked[k50:],
        "cutoffs": {"k40": k40, "k45": k45, "k50": k50},
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def rate(wins: int, n: int) -> float | None:
    return wins / n if n else None


def cohort_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    probability_key: str = "p1_corrected",
    correct_key: str = "correct_corrected",
) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "wins": 0, "accuracy": None, "wilson95": None}
    wins = sum(1 for r in rows if r[correct_key])
    ps = []
    for r in rows:
        p = r[probability_key]
        side = r["side_corrected"] if probability_key == "p1_corrected" else r["side_original"]
        ps.append(p if side == 1 else 1.0 - p)
    ys = [1 if r[correct_key] else 0 for r in rows]
    by_tier = {}
    for tier in ("ATP_MAIN", "CHALLENGER"):
        subset = [r for r in rows if r["tour"] == tier]
        if subset:
            w = sum(1 for r in subset if r[correct_key])
            by_tier[tier] = {
                "n": len(subset),
                "wins": w,
                "accuracy": w / len(subset),
                "wilson95": wilson_interval(w, len(subset)),
            }
    surface_known = [r for r in rows if r["surface"]]
    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "accuracy": wins / n,
        "wilson95": wilson_interval(wins, n),
        "mean_confidence": mean(ps),
        "calibration_gap": mean(ps) - (wins / n),
        "brier": brier(zip(ps, ys, strict=True)),
        "log_loss": log_loss(zip(ps, ys, strict=True)),
        "mean_min_matches": mean(
            min(_value(r["matches_corrected"]["p1"]), _value(r["matches_corrected"]["p2"]))
            for r in rows
        ),
        "median_min_matches": median(
            min(_value(r["matches_corrected"]["p1"]), _value(r["matches_corrected"]["p2"]))
            for r in rows
        ),
        "surface_known": len(surface_known),
        "surface_missing": n - len(surface_known),
        "by_tier": by_tier,
        "atp_share": by_tier.get("ATP_MAIN", {}).get("n", 0) / n,
    }


def _value(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _band(value: float | None, bands: Sequence[tuple[str, float, float]], missing: str = "MISSING") -> str:
    if value is None:
        return missing
    for label, lo, hi in bands:
        if lo <= value <= hi:
            return label
    return missing


def experience_band(matches: float | None) -> str:
    if matches is None:
        return "MISSING"
    for label, lo, hi in EXPERIENCE_BANDS:
        if lo <= matches <= hi:
            return label
    return "100+"


def surface_experience_band(matches: float | None) -> str:
    if matches is None:
        return "MISSING"
    for label, lo, hi in SURFACE_EXPERIENCE_BANDS:
        if lo <= matches <= hi:
            return label
    return "10+"


def information_state(observation: Mapping[str, Any]) -> str:
    """Coarse information-state label, explicitly not a claim of inexperience."""
    fav = observation["favourite_side"]
    dog = observation["underdog_side"]
    fav_matches = observation["matches_corrected"][f"p{fav}"]
    dog_matches = observation["matches_corrected"][f"p{dog}"]
    fav_original = observation["matches_original"][f"p{fav}"]
    dog_original = observation["matches_original"][f"p{dog}"]
    fav_res = observation["resolution"][f"p{fav}"]
    dog_res = observation["resolution"][f"p{dog}"]
    fav_unavailable = fav_res["method"] == "NO_HISTORICAL_CANDIDATE" and (fav_matches or 0) == 0
    dog_unavailable = dog_res["method"] == "NO_HISTORICAL_CANDIDATE" and (dog_matches or 0) == 0
    recovered_any = (
        (fav_matches or 0) > (fav_original or 0)
        or (dog_matches or 0) > (dog_original or 0)
    )
    if fav_unavailable and dog_unavailable:
        return "BOTH_NO_OBSERVABLE_HISTORY"
    if fav_unavailable:
        return "FAVOURITE_NO_OBSERVABLE_HISTORY"
    if dog_unavailable:
        return "UNDERDOG_NO_OBSERVABLE_HISTORY"
    if (fav_matches or 0) < 10 and (dog_matches or 0) < 10:
        return "BOTH_LIMITED"
    if (fav_matches or 0) < 10:
        return "FAVOURITE_LIMITED"
    if (dog_matches or 0) < 10:
        return "UNDERDOG_LIMITED"
    if recovered_any:
        return "BOTH_ESTABLISHED_RECOVERED_HISTORY"
    return "BOTH_ESTABLISHED"


def factor_values(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Observable, outcome-independent factors for one settled forecast."""
    fav = observation["favourite_side"]
    dog = observation["underdog_side"]
    fav_key, dog_key = f"p{fav}", f"p{dog}"
    eff = observation["effective_corrected"]
    surf = observation["surface_rating_corrected"]
    overall = observation["overall_corrected"]
    matches = observation["matches_corrected"]
    surface_matches = observation["surface_matches_corrected"]
    inactivity = observation["inactivity_corrected"]
    resolved = observation["resolution"]
    fav_res, dog_res = resolved[fav_key], resolved[dog_key]
    elo_gap = _value(eff[fav_key]) - _value(eff[dog_key])
    surface_gap = _value(surf[fav_key]) - _value(surf[dog_key])
    min_matches = min(_value(matches["p1"]), _value(matches["p2"]))
    min_surface = min(_value(surface_matches["p1"]), _value(surface_matches["p2"]))
    inactivity_fav = inactivity[fav_key]
    inactivity_dog = inactivity[dog_key]
    inactivity_values = [v for v in (inactivity_fav, inactivity_dog) if v is not None]
    delta_fav = _value(matches[fav_key]) - _value(observation["matches_original"][fav_key])
    delta_dog = _value(matches[dog_key]) - _value(observation["matches_original"][dog_key])
    divergence_fav = abs(_value(overall[fav_key]) - _value(surf[fav_key]))
    market = observation["market_p1"] if observation.get("market_eligible") else None
    market_available = market is not None
    market_conf = None
    market_agrees = None
    market_gap = None
    if market is not None:
        market_side = 1 if market >= 0.5 else 2
        market_conf = max(market, 1 - market)
        market_agrees = market_side == fav
        market_gap = observation["confidence_corrected"] - market_conf
    any_ambiguous = fav_res["method"] == "AMBIGUOUS" or dog_res["method"] == "AMBIGUOUS"
    any_no_history = (
        fav_res["method"] == "NO_HISTORICAL_CANDIDATE"
        or dog_res["method"] == "NO_HISTORICAL_CANDIDATE"
    )
    return {
        "confidence": observation["confidence_corrected"],
        "elo_gap": elo_gap,
        "abs_elo_gap": abs(elo_gap),
        "surface_elo_gap": surface_gap,
        "abs_surface_elo_gap": abs(surface_gap),
        "favourite_matches": _value(matches[fav_key]),
        "underdog_matches": _value(matches[dog_key]),
        "min_matches": min_matches,
        "favourite_surface_matches": _value(surface_matches[fav_key]),
        "min_surface_matches": min_surface,
        "inactivity_favourite": inactivity_fav,
        "inactivity_underdog": inactivity_dog,
        "max_inactivity": max(inactivity_values) if inactivity_values else None,
        "delta_matches_favourite": delta_fav,
        "delta_matches_underdog": delta_dog,
        "max_delta_matches": max(delta_fav, delta_dog),
        "divergence_favourite": divergence_fav,
        "market_confidence": market_conf,
        "market_gap": market_gap,
        "tier_challenger": observation["tour"] == "CHALLENGER",
        "tier_atp": observation["tour"] == "ATP_MAIN",
        "surface_missing": observation["surface"] == "",
        "round_qualifying": observation["round_code"] == "QUAL",
        "round_unknown": observation["round_code"] == "UNKNOWN",
        "any_ambiguous": any_ambiguous,
        "any_no_history": any_no_history,
        "fav_no_history": fav_res["method"] == "NO_HISTORICAL_CANDIDATE",
        "dog_no_history": dog_res["method"] == "NO_HISTORICAL_CANDIDATE",
        "fav_recovered": delta_fav > 0,
        "dog_recovered": delta_dog > 0,
        "history_recovered": delta_fav > 0 or delta_dog > 0,
        "min_matches_lt10": min_matches < 10,
        "fav_matches_lt10": _value(matches[fav_key]) < 10,
        "dog_matches_lt10": _value(matches[dog_key]) < 10,
        "min_surface_lt5": min_surface < 5,
        "fav_surface_lt5": _value(surface_matches[fav_key]) < 5,
        "inactivity_fav_gt90": inactivity_fav is not None and inactivity_fav > 90,
        "inactivity_any_gt90": any(v is not None and v > 90 for v in inactivity_values),
        "inactivity_missing": inactivity_fav is None or inactivity_dog is None,
        "side_changed": observation["side_changed"],
        "market_available": market_available,
        "market_disagrees": market_agrees is False,
        "elo_gap_lt50": abs(elo_gap) < 50,
        "elo_gap_gt200": abs(elo_gap) >= 200,
        "surface_gap_lt25": abs(surface_gap) < 25,
        "divergence_fav_gt100": divergence_fav >= 100,
        "candidate_multi": max(fav_res["candidate_count"], dog_res["candidate_count"]) > 1,
        "qualifying": observation["qualifying"],
        "experience_favourite": experience_band(matches[fav_key]),
        "experience_underdog": experience_band(matches[dog_key]),
        "experience_min": experience_band(min_matches),
        "surface_experience_min": surface_experience_band(min_surface),
        "inactivity_band_favourite": _band(inactivity_fav, INACTIVITY_BANDS),
        "inactivity_band_underdog": _band(inactivity_dog, INACTIVITY_BANDS),
        "inactivity_band_max": _band(
            max(inactivity_values) if inactivity_values else None, INACTIVITY_BANDS
        ),
        "confidence_band": _band(observation["confidence_corrected"], CONFIDENCE_BANDS),
        "rating_gap_band": _band(abs(elo_gap), RATING_GAP_BANDS),
        "round": observation["round_code"],
        "information_state": information_state(observation),
        "fav_no_observable_history": fav_res["method"] == "NO_HISTORICAL_CANDIDATE"
        and _value(matches[fav_key]) == 0,
        "dog_no_observable_history": dog_res["method"] == "NO_HISTORICAL_CANDIDATE"
        and _value(matches[dog_key]) == 0,
    }


# ---------------------------------------------------------------------------
# Phase 1 — factor screen
# ---------------------------------------------------------------------------
BINARY_FACTORS: tuple[str, ...] = (
    "tier_challenger",
    "surface_missing",
    "round_qualifying",
    "round_unknown",
    "any_ambiguous",
    "any_no_history",
    "fav_no_history",
    "dog_no_history",
    "history_recovered",
    "fav_recovered",
    "dog_recovered",
    "min_matches_lt10",
    "fav_matches_lt10",
    "dog_matches_lt10",
    "min_surface_lt5",
    "fav_surface_lt5",
    "inactivity_fav_gt90",
    "inactivity_any_gt90",
    "inactivity_missing",
    "side_changed",
    "market_available",
    "market_disagrees",
    "elo_gap_lt50",
    "elo_gap_gt200",
    "surface_gap_lt25",
    "divergence_fav_gt100",
    "candidate_multi",
    "qualifying",
)

CONTINUOUS_FACTORS: tuple[str, ...] = (
    "confidence",
    "elo_gap",
    "abs_elo_gap",
    "surface_elo_gap",
    "abs_surface_elo_gap",
    "favourite_matches",
    "underdog_matches",
    "min_matches",
    "favourite_surface_matches",
    "min_surface_matches",
    "inactivity_favourite",
    "inactivity_underdog",
    "max_inactivity",
    "delta_matches_favourite",
    "delta_matches_underdog",
    "max_delta_matches",
    "divergence_favourite",
    "market_confidence",
    "market_gap",
)


def binary_error_association(
    rows: Sequence[Mapping[str, Any]],
    factor: str,
    *,
    correct_key: str = "correct_corrected",
) -> dict[str, Any]:
    present = [r for r in rows if factor_values(r)[factor]]
    absent = [r for r in rows if not factor_values(r)[factor]]
    errors_present = sum(1 for r in present if not r[correct_key])
    errors_absent = sum(1 for r in absent if not r[correct_key])
    a, b = errors_present, len(present) - errors_present
    c, d = errors_absent, len(absent) - errors_absent
    p_present = a / len(present) if present else None
    p_absent = c / len(absent) if absent else None
    diff = (
        None
        if p_present is None or p_absent is None
        else newcombe_difference(a, len(present), c, len(absent))
    )
    odds = odds_ratio_interval(a, b, c, d) if present and absent else {"odds_ratio": None, "ci95": None}
    p_value = fisher_exact_two_sided(a, b, c, d) if present and absent else None
    return {
        "factor": factor,
        "n_present": len(present),
        "n_absent": len(absent),
        "error_rate_present": p_present,
        "error_rate_absent": p_absent,
        "risk_difference": None if diff is None else diff["difference"],
        "risk_difference_ci95": None if diff is None else diff["ci95"],
        "odds_ratio": odds["odds_ratio"],
        "odds_ratio_ci95": odds["ci95"],
        "fisher_p": p_value,
    }


def continuous_error_association(
    rows: Sequence[Mapping[str, Any]],
    factor: str,
    *,
    correct_key: str = "correct_corrected",
) -> dict[str, Any]:
    errors = [
        v for r in rows
        if not r[correct_key] and (v := factor_values(r)[factor]) is not None
    ]
    correct = [
        v for r in rows
        if r[correct_key] and (v := factor_values(r)[factor]) is not None
    ]
    result = smd(errors, correct)
    return {
        "factor": factor,
        "n_error": result["n_error"],
        "n_correct": result["n_correct"],
        "mean_error": result.get("mean_error"),
        "mean_correct": result.get("mean_correct"),
        "smd": result["smd"],
        "smd_ci95": result["ci95"],
        "missing_error": sum(
            1 for r in rows if not r[correct_key] and factor_values(r)[factor] is None
        ),
        "missing_correct": sum(
            1 for r in rows if r[correct_key] and factor_values(r)[factor] is None
        ),
    }


def factor_screen(
    rows: Sequence[Mapping[str, Any]],
    *,
    correct_key: str = "correct_corrected",
    label: str = "",
) -> dict[str, Any]:
    binary = [binary_error_association(rows, f, correct_key=correct_key) for f in BINARY_FACTORS]
    continuous = [
        continuous_error_association(rows, f, correct_key=correct_key)
        for f in CONTINUOUS_FACTORS
    ]
    pvalues = [row["fisher_p"] for row in binary] + [
        None if row["smd"] is None else _smd_p(row["smd"], row["n_error"], row["n_correct"])
        for row in continuous
    ]
    adjusted = benjamini_hochberg(pvalues)
    for row, q in zip(binary, adjusted[: len(binary)], strict=True):
        row["fdr_q"] = q
    for row, q in zip(continuous, adjusted[len(binary):], strict=True):
        row["fdr_q"] = q
    return {
        "label": label,
        "n": len(rows),
        "errors": sum(1 for r in rows if not r[correct_key]),
        "binary": binary,
        "continuous": continuous,
        "note": (
            "Exploratory factor screen. p-values are unadjusted Fisher exact / "
            "normal-approximation values with Benjamini-Hochberg q-values; no "
            "factor is confirmatory and no correction is applied to forecasts."
        ),
    }


def _smd_p(d: float, n1: int, n2: int) -> float | None:
    if n1 < 2 or n2 < 2:
        return None
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
    if se == 0:
        return None
    z = abs(d) / se
    return math.erfc(z / math.sqrt(2.0))


def cohort_profile(
    cohort: Sequence[Mapping[str, Any]],
    reference: Sequence[Mapping[str, Any]],
    *,
    reference_label: str = "core",
) -> dict[str, Any]:
    """Prevalence of each binary factor in a cohort versus a reference."""
    out = []
    for factor in BINARY_FACTORS:
        cohort_present = sum(1 for r in cohort if factor_values(r)[factor])
        ref_present = sum(1 for r in reference if factor_values(r)[factor])
        if not cohort or not reference:
            continue
        a = cohort_present
        b = len(cohort) - a
        c = ref_present
        d = len(reference) - c
        diff = newcombe_difference(a, len(cohort), c, len(reference))
        odds = odds_ratio_interval(a, b, c, d)
        out.append(
            {
                "factor": factor,
                "cohort_prevalence": a / len(cohort),
                "reference_prevalence": c / len(reference),
                "prevalence_difference": None if diff is None else diff["difference"],
                "prevalence_difference_ci95": None if diff is None else diff["ci95"],
                "odds_ratio": odds["odds_ratio"],
                "odds_ratio_ci95": odds["ci95"],
                "fisher_p": fisher_exact_two_sided(a, b, c, d),
            }
        )
    continuous = []
    for factor in CONTINUOUS_FACTORS:
        cohort_values = [v for r in cohort if (v := factor_values(r)[factor]) is not None]
        ref_values = [v for r in reference if (v := factor_values(r)[factor]) is not None]
        result = smd(cohort_values, ref_values)
        continuous.append(
            {
                "factor": factor,
                "cohort_mean": mean(cohort_values) if cohort_values else None,
                "reference_mean": mean(ref_values) if ref_values else None,
                "smd": result["smd"],
                "smd_ci95": result["ci95"],
                "cohort_missing": len(cohort) - len(cohort_values),
                "reference_missing": len(reference) - len(ref_values),
            }
        )
    return {
        "reference": reference_label,
        "cohort_n": len(cohort),
        "reference_n": len(reference),
        "binary": out,
        "continuous": continuous,
    }


def phase1(
    observations: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    cohorts = frozen_cohorts(observations, thresholds)
    bands = retrospective_bands(observations)
    holdout = cohorts["holdout"]
    summaries = {
        name: cohort_summary(cohorts[name]) for name in (
            "core_40", "marginal_40_45", "base_45", "marginal_45_50",
            "base_50", "below_50", "holdout",
        )
    }
    retrospective = {
        "top_40": cohort_summary(bands["top_40"]),
        "band_40_45": cohort_summary(bands["band_40_45"]),
        "band_45_50": cohort_summary(bands["band_45_50"]),
        "band_below_50": cohort_summary(bands["band_below_50"]),
        "cutoffs": bands["cutoffs"],
    }
    marginal_pool = list(cohorts["marginal_40_45"]) + list(cohorts["marginal_45_50"])
    screens = {
        "holdout_all": factor_screen(holdout, label="Nov holdout (1,025)"),
        "full_settled": factor_screen(observations, label="3,223 settled"),
        "marginal_pool": factor_screen(marginal_pool, label="frozen marginal 40-50 pool (101)"),
        "retrospective_bands": factor_screen(
            list(bands["band_40_45"]) + list(bands["band_45_50"]),
            label="retrospective 40-50 bands (322)",
        ),
    }
    profiles = {
        "marginal_40_45_vs_core": cohort_profile(
            cohorts["marginal_40_45"], cohorts["core_40"], reference_label="core_40"
        ),
        "marginal_45_50_vs_base_45": cohort_profile(
            cohorts["marginal_45_50"], cohorts["base_45"], reference_label="base_45"
        ),
        "marginal_pool_vs_core": cohort_profile(
            marginal_pool, cohorts["core_40"], reference_label="core_40"
        ),
    }
    pooled_vs_core = newcombe_difference(
        sum(1 for r in marginal_pool if r["correct_corrected"]),
        len(marginal_pool),
        sum(1 for r in cohorts["core_40"] if r["correct_corrected"]),
        len(cohorts["core_40"]),
    )
    pooled_vs_holdout = newcombe_difference(
        sum(1 for r in marginal_pool if r["correct_corrected"]),
        len(marginal_pool),
        sum(1 for r in holdout if r["correct_corrected"]),
        len(holdout),
    )
    return {
        "thresholds": thresholds,
        "cohorts": summaries,
        "retrospective_bands": retrospective,
        "marginal_pool": cohort_summary(marginal_pool),
        "marginal_pool_vs_core": pooled_vs_core,
        "marginal_pool_vs_holdout": pooled_vs_holdout,
        "factor_screens": screens,
        "profiles": profiles,
        "missingness": missingness_report(observations),
        "notes": [
            "The frozen cohorts use the development-period thresholds exactly; no November outcome enters the thresholds.",
            "The retrospective bands are descriptive ranks inside the 3,223-row corrected ranking and are not a policy.",
            "Marginal samples are small (45 and 56 selections). Every factor test is exploratory.",
        ],
    }


def missingness_report(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(observations)
    fields = {
        "round_code_unknown": sum(1 for o in observations if o["round_code"] == "UNKNOWN"),
        "surface_missing": sum(1 for o in observations if not o["surface"]),
        "market_missing_or_ineligible": sum(
            1 for o in observations if not o.get("market_eligible")
        ),
        "market_quoted_any_age": sum(
            1 for o in observations if o["market_p1"] is not None
        ),
        "market_eligible_pre_forecast_0_24h": sum(
            1 for o in observations if o.get("market_eligible")
        ),
        "inactivity_favourite_missing": sum(
            1 for o in observations if o["inactivity_corrected"][f"p{o['favourite_side']}"] is None
        ),
        "inactivity_underdog_missing": sum(
            1 for o in observations if o["inactivity_corrected"][f"p{o['underdog_side']}"] is None
        ),
        "any_ambiguous_slot": sum(
            1 for o in observations
            if o["resolution"]["p1"]["method"] == "AMBIGUOUS"
            or o["resolution"]["p2"]["method"] == "AMBIGUOUS"
        ),
        "any_no_history_slot": sum(
            1 for o in observations
            if o["resolution"]["p1"]["method"] == "NO_HISTORICAL_CANDIDATE"
            or o["resolution"]["p2"]["method"] == "NO_HISTORICAL_CANDIDATE"
        ),
    }
    return {"n": n, "counts": fields, "shares": {k: v / n for k, v in fields.items()}}


# ---------------------------------------------------------------------------
# Phase 2 — experience and newcomer-favourite
# ---------------------------------------------------------------------------
EXPERIENCE_LABELS = [label for label, _lo, _hi in EXPERIENCE_BANDS]


def experience_table(
    observations: Sequence[Mapping[str, Any]],
    *,
    side: str = "favourite",
    correct_key: str = "correct_corrected",
) -> dict[str, Any]:
    rows = []
    for label in EXPERIENCE_LABELS:
        subset = [o for o in observations if _experience_of(o, side) == label]
        if not subset:
            continue
        summary = cohort_summary(subset, correct_key=correct_key)
        summary["band"] = label
        rows.append(summary)
    return {"side": side, "bands": rows}


def _experience_of(observation: Mapping[str, Any], side: str) -> str:
    if side == "favourite":
        return experience_band(observation["matches_corrected"][f"p{observation['favourite_side']}"])
    if side == "underdog":
        return experience_band(observation["matches_corrected"][f"p{observation['underdog_side']}"])
    return experience_band(
        min(
            _value(observation["matches_corrected"]["p1"]),
            _value(observation["matches_corrected"]["p2"]),
        )
    )


def phase2(
    observations: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
    *,
    observability: Mapping[int, Mapping[str, bool]] | None = None,
) -> dict[str, Any]:
    holdout = holdout_rows(observations)
    cohorts = frozen_cohorts(observations, thresholds)
    marginal_pool = list(cohorts["marginal_40_45"]) + list(cohorts["marginal_45_50"])
    holdout_ids = {int(o["prediction_id"]) for o in holdout}
    marginal_ids = {int(o["prediction_id"]) for o in marginal_pool}
    observed = observability or {}

    def metrics_for(rows: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
        out = {}
        for label in EXPERIENCE_LABELS:
            subset = [
                r for r in rows
                if _experience_of(r, side) == label
            ]
            if not subset:
                out[label] = {"n": 0}
                continue
            out[label] = cohort_summary(subset)
            out[label]["band"] = label
            out[label]["share_of_rows"] = len(subset) / len(rows) if rows else None
        return out

    states = sorted({information_state(o) for o in observations})

    def state_table(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        table = {}
        for state in sorted({information_state(o) for o in rows}):
            subset = [o for o in rows if information_state(o) == state]
            table[state] = cohort_summary(subset)
            table[state]["n"] = len(subset)
            table[state]["accuracy_original"] = _original_accuracy(subset)
            table[state]["brier_original"] = _original_brier(subset)
            table[state]["log_loss_original"] = _original_log_loss(subset)
            table[state]["marginal_n"] = sum(
                1 for o in subset if int(o["prediction_id"]) in marginal_ids
            )
        return table

    state_metrics = state_table(observations)
    for state in states:
        subset = [o for o in observations if information_state(o) == state]
        state_metrics[state]["holdout_n"] = sum(
            1 for o in subset if int(o["prediction_id"]) in holdout_ids
        )

    recovery_classes = {}
    definitions = {
        "any_recovered_history": lambda o: factor_values(o)["history_recovered"],
        "favourite_recovered_history": lambda o: factor_values(o)["fav_recovered"],
        "underdog_recovered_history": lambda o: factor_values(o)["dog_recovered"],
        "no_recovered_history": lambda o: not factor_values(o)["history_recovered"],
        "favourite_no_observable_history": lambda o: factor_values(o)["fav_no_observable_history"],
        "underdog_no_observable_history": lambda o: factor_values(o)["dog_no_observable_history"],
        "any_ambiguous_identity": lambda o: factor_values(o)["any_ambiguous"],
        "any_no_history": lambda o: factor_values(o)["any_no_history"],
    }
    for name, predicate in definitions.items():
        subset = [o for o in observations if predicate(o)]
        recovery_classes[name] = cohort_summary(subset)
        recovery_classes[name]["accuracy_original"] = _original_accuracy(subset)

    # Zero-history observability.  An empty rating history is not evidence of
    # genuine inexperience: the player may still appear in the observed recent
    # result history (lower-level competition absent from the rating corpus) or
    # may not appear anywhere we can see.
    zero_history: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "wins": 0})
    for o in observations:
        flags = observed.get(int(o["prediction_id"]), {})
        for side in ("p1", "p2"):
            if _value(o["matches_corrected"][side]) != 0:
                continue
            role = "favourite" if side == f"p{o['favourite_side']}" else "underdog"
            seen = bool(flags.get(side))
            label = (
                "OBSERVED_ELSEWHERE_LOWER_LEVEL_ABSENT"
                if seen
                else "NO_OBSERVED_PRIOR_MATCH_UNVERIFIED"
            )
            key = f"{label}:{role}"
            zero_history[key]["n"] += 1
            zero_history[key]["wins"] += int(o["correct_corrected"])

    # Same fixtures, both histories: original vs corrected metrics by corrected
    # experience band.
    identical = {}
    for label in EXPERIENCE_LABELS:
        subset = [o for o in observations if _experience_of(o, "favourite") == label]
        identical[label] = {
            "n": len(subset),
            "original": _original_metrics(subset),
            "corrected": cohort_summary(subset),
        }

    # How the bridge changes the favourite's experience class.
    transitions: Counter[tuple[str, str]] = Counter()
    transition_outcomes: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "wins_original": 0}
    )
    for o in observations:
        fav = o["favourite_side"]
        original_band = experience_band(o["matches_original"][f"p{fav}"])
        corrected_band = experience_band(o["matches_corrected"][f"p{fav}"])
        transitions[(original_band, corrected_band)] += 1
        key = (original_band, corrected_band)
        transition_outcomes[key]["n"] += 1
        transition_outcomes[key]["wins"] += int(o["correct_corrected"])
        transition_outcomes[key]["wins_original"] += int(o["correct_original"])
    transition_rows = [
        {
            "original_band": a,
            "corrected_band": b,
            "n": n,
            "corrected_accuracy": transition_outcomes[(a, b)]["wins"] / n,
            "original_accuracy": transition_outcomes[(a, b)]["wins_original"] / n,
        }
        for (a, b), n in sorted(transitions.items())
    ]

    # The decisive comparison: limited favourites under the original namespace
    # whose corrected history is established.
    moved_to_established = [
        o for o in observations
        if experience_band(o["matches_original"][f"p{o['favourite_side']}"]) in ("0-4", "5-9")
        and experience_band(o["matches_corrected"][f"p{o['favourite_side']}"]) in ("20-49", "50-99", "100+")
    ]
    stayed_limited = [
        o for o in observations
        if experience_band(o["matches_original"][f"p{o['favourite_side']}"]) in ("0-4", "5-9")
        and experience_band(o["matches_corrected"][f"p{o['favourite_side']}"]) in ("0-4", "5-9")
    ]
    moved_paired = paired_proportion_difference(
        [o["correct_corrected"] for o in moved_to_established],
        [o["correct_original"] for o in moved_to_established],
    )
    stayed_paired = paired_proportion_difference(
        [o["correct_corrected"] for o in stayed_limited],
        [o["correct_original"] for o in stayed_limited],
    )

    # Independent observation: a zero corrected history that still appears in
    # the observed recent result history is not evidence of genuine inexperience.
    # The mapping is supplied by the caller through the optional
    # ``observed_recent_matches`` argument; when absent this is "unverified".
    return {
        "holdout": {
            "favourite": metrics_for(holdout, "favourite"),
            "underdog": metrics_for(holdout, "underdog"),
            "min": metrics_for(holdout, "min"),
        },
        "full_settled": {
            "favourite": metrics_for(observations, "favourite"),
            "underdog": metrics_for(observations, "underdog"),
            "min": metrics_for(observations, "min"),
        },
        "marginal_pool": {
            "favourite": metrics_for(marginal_pool, "favourite"),
            "underdog": metrics_for(marginal_pool, "underdog"),
            "min": metrics_for(marginal_pool, "min"),
        },
        "information_states": state_metrics,
        "zero_history_observability": {
            key: {
                **value,
                "accuracy": value["wins"] / value["n"] if value["n"] else None,
            }
            for key, value in sorted(zero_history.items())
        },
        "identical_fixtures_favourite_band": identical,
        "favourite_band_transitions": transition_rows,
        "bridge_resolution": {
            "original_limited_to_corrected_established_n": len(moved_to_established),
            "original_limited_to_corrected_established_corrected": cohort_summary(moved_to_established),
            "original_limited_to_corrected_established_original": _original_metrics(moved_to_established),
            "original_limited_stayed_limited_n": len(stayed_limited),
            "original_limited_stayed_limited_corrected": cohort_summary(stayed_limited),
            "original_limited_stayed_limited_original": _original_metrics(stayed_limited),
            "moved_to_established_paired_difference": moved_paired,
            "stayed_limited_paired_difference": stayed_paired,
        },
        "recovery_classes": recovery_classes,
        "holdout_information_states": state_table(holdout),
        "marginal_information_states": state_table(marginal_pool),
        "note": (
            "Experience bands count observed prior matches in the resolved namespace. "
            "A zero count is 'no observable history', never proof of genuine inexperience."
        ),
    }


def _original_accuracy(rows: Sequence[Mapping[str, Any]]) -> float | None:
    return sum(1 for r in rows if r["correct_original"]) / len(rows) if rows else None


def _original_brier(rows: Sequence[Mapping[str, Any]]) -> float | None:
    pairs = []
    for r in rows:
        p = r["p1_original"]
        pairs.append((p if r["side_original"] == 1 else 1.0 - p, int(r["correct_original"])))
    return brier(pairs)


def _original_log_loss(rows: Sequence[Mapping[str, Any]]) -> float | None:
    pairs = []
    for r in rows:
        p = r["p1_original"]
        pairs.append((p if r["side_original"] == 1 else 1.0 - p, int(r["correct_original"])))
    return log_loss(pairs)


def _original_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    wins = sum(1 for r in rows if r["correct_original"])
    return {
        "n": len(rows),
        "wins": wins,
        "accuracy": wins / len(rows),
        "wilson95": wilson_interval(wins, len(rows)),
        "brier": _original_brier(rows),
        "log_loss": _original_log_loss(rows),
        "mean_confidence": mean(r["confidence_original"] for r in rows),
    }


# ---------------------------------------------------------------------------
# Phase 3 — ATP versus Challenger
# ---------------------------------------------------------------------------
def _stratified_tier(
    rows: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], str],
    *,
    min_cell: int = 10,
) -> dict[str, Any]:
    strata: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(lambda: {"ATP_MAIN": [], "CHALLENGER": []})
    for row in rows:
        if row["tour"] in ("ATP_MAIN", "CHALLENGER"):
            strata[key_fn(row)][row["tour"]].append(row)
    cells = []
    for label in sorted(strata):
        atp = strata[label]["ATP_MAIN"]
        ch = strata[label]["CHALLENGER"]
        if not atp and not ch:
            continue
        cells.append(
            {
                "stratum": label,
                "atp_n": len(atp),
                "atp_wins": sum(1 for r in atp if r["correct_corrected"]),
                "atp_accuracy": rate(sum(1 for r in atp if r["correct_corrected"]), len(atp)),
                "challenger_n": len(ch),
                "challenger_wins": sum(1 for r in ch if r["correct_corrected"]),
                "challenger_accuracy": rate(sum(1 for r in ch if r["correct_corrected"]), len(ch)),
                "common_support": len(atp) >= min_cell and len(ch) >= min_cell,
            }
        )
    common = [c for c in cells if c["common_support"]]
    weights = [c["atp_n"] + c["challenger_n"] for c in common]
    total_weight = sum(weights)
    standardized_atp = (
        sum(c["atp_accuracy"] * w for c, w in zip(common, weights, strict=True)) / total_weight
        if total_weight
        else None
    )
    standardized_ch = (
        sum(c["challenger_accuracy"] * w for c, w in zip(common, weights, strict=True)) / total_weight
        if total_weight
        else None
    )
    # Mantel-Haenszel odds ratio (CH vs ATP, win outcome).
    num = den = 0.0
    for c in common:
        n = c["atp_n"] + c["challenger_n"]
        a = c["challenger_wins"]
        b = c["challenger_n"] - a
        cc = c["atp_wins"]
        d = c["atp_n"] - cc
        num += a * d / n
        den += b * cc / n
    mh_or = num / den if den else None
    atp_total = sum(1 for r in rows if r["tour"] == "ATP_MAIN")
    ch_total = sum(1 for r in rows if r["tour"] == "CHALLENGER")
    atp_common = sum(c["atp_n"] for c in common)
    ch_common = sum(c["challenger_n"] for c in common)
    return {
        "cells": cells,
        "common_support_cells": len(common),
        "total_cells": len(cells),
        "standardized_atp_accuracy": standardized_atp,
        "standardized_challenger_accuracy": standardized_ch,
        "standardized_difference_ch_minus_atp": (
            standardized_ch - standardized_atp
            if standardized_atp is not None and standardized_ch is not None
            else None
        ),
        "mantel_haenszel_odds_ratio_ch_vs_atp": mh_or,
        "common_support": {
            "atp_rows_in_common_cells": atp_common,
            "atp_rows_total": atp_total,
            "atp_share": atp_common / atp_total if atp_total else None,
            "challenger_rows_in_common_cells": ch_common,
            "challenger_rows_total": ch_total,
            "challenger_share": ch_common / ch_total if ch_total else None,
        },
        "min_cell": min_cell,
    }


def _market_compare(
    rows: Sequence[Mapping[str, Any]],
    predicate: Callable[[Mapping[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    quoted = [r for r in rows if r["market_p1"] is not None]
    selector = predicate or (lambda r: bool(r.get("market_eligible")))
    eligible = [r for r in quoted if selector(r)]
    out: dict[str, Any] = {
        "quoted_any_age": len(quoted),
        "selected_rows": len(eligible),
        "age_rule": "0 <= market_age_hours <= 24 (quote at or before forecast, at most 24h old)",
    }
    for tier in ("ATP_MAIN", "CHALLENGER"):
        subset = [r for r in eligible if r["tour"] == tier]
        if not subset:
            out[tier] = {"n": 0}
            continue
        model_wins = sum(1 for r in subset if r["correct_corrected"])
        market_flags = [
            (1 if r["market_p1"] >= 0.5 else 2) == r["winner_side"] for r in subset
        ]
        market_wins = sum(market_flags)
        paired = paired_proportion_difference(
            [r["correct_corrected"] for r in subset], market_flags
        )
        out[tier] = {
            "n": len(subset),
            "model_wins": model_wins,
            "model_accuracy": model_wins / len(subset),
            "market_wins": market_wins,
            "market_accuracy": market_wins / len(subset),
            "paired_difference_model_minus_market": paired["difference"],
            "model_minus_market_ci95": paired["ci95"],
            "mcnemar_p": paired["mcnemar_p"],
            "discordant_model_only": paired["discordant_model_only"],
            "discordant_market_only": paired["discordant_market_only"],
        }
    return out


def phase3(
    observations: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    holdout = holdout_rows(observations)
    cohorts = frozen_cohorts(observations, thresholds)
    selected = cohorts["base_50"]
    factor_cache: dict[int, dict[str, Any]] = {}

    def factors(row: Mapping[str, Any]) -> dict[str, Any]:
        key = id(row)
        if key not in factor_cache:
            factor_cache[key] = factor_values(row)
        return factor_cache[key]

    key_fns = {
        "confidence_band": lambda r: factors(r)["confidence_band"],
        "experience_min": lambda r: factors(r)["experience_min"],
        "identity_completeness": lambda r: (
            "ANY_AMBIGUOUS" if factors(r)["any_ambiguous"]
            else "ANY_NO_HISTORY" if factors(r)["any_no_history"]
            else "COMPLETE"
        ),
        "rating_gap_band": lambda r: factors(r)["rating_gap_band"],
        "surface_experience_min": lambda r: factors(r)["surface_experience_min"],
        "round": lambda r: factors(r)["round"],
        "inactivity_max": lambda r: factors(r)["inactivity_band_max"],
    }
    overall = {}
    for tier in ("ATP_MAIN", "CHALLENGER"):
        subset = [r for r in observations if r["tour"] == tier]
        overall[tier] = cohort_summary(subset)
    strata = {name: _stratified_tier(observations, fn) for name, fn in key_fns.items()}
    selected_strata = {name: _stratified_tier(selected, fn) for name, fn in key_fns.items()}
    # Paired market comparison on the same quoted fixtures.
    market = _market_compare(observations)
    market_selected = _market_compare(selected)
    market_age_ge_0 = _market_compare(
        observations,
        lambda r: r["market_p1"] is not None
        and r["market_age_hours"] is not None
        and r["market_age_hours"] >= 0,
    )
    market_all_quotes = _market_compare(
        observations, lambda r: r["market_p1"] is not None
    )
    market_selected_age_ge_0 = _market_compare(
        selected,
        lambda r: r["market_p1"] is not None
        and r["market_age_hours"] is not None
        and r["market_age_hours"] >= 0,
    )
    market_tier_gap = None
    model_tier_gap_on_market_eligible = None
    if market.get("ATP_MAIN", {}).get("n") and market.get("CHALLENGER", {}).get("n"):
        market_tier_gap = newcombe_difference(
            market["CHALLENGER"]["market_wins"], market["CHALLENGER"]["n"],
            market["ATP_MAIN"]["market_wins"], market["ATP_MAIN"]["n"],
        )
        model_tier_gap_on_market_eligible = newcombe_difference(
            market["CHALLENGER"]["model_wins"], market["CHALLENGER"]["n"],
            market["ATP_MAIN"]["model_wins"], market["ATP_MAIN"]["n"],
        )
    # Tier gap inside the frozen holdout selection.
    holdout_gap = {}
    for name, subset in (("holdout", holdout), ("base_50", selected), ("core_40", cohorts["core_40"])):
        atp = [r for r in subset if r["tour"] == "ATP_MAIN"]
        ch = [r for r in subset if r["tour"] == "CHALLENGER"]
        holdout_gap[name] = {
            "atp_n": len(atp),
            "atp_accuracy": rate(sum(1 for r in atp if r["correct_corrected"]), len(atp)),
            "challenger_n": len(ch),
            "challenger_accuracy": rate(sum(1 for r in ch if r["correct_corrected"]), len(ch)),
            "difference_ch_minus_atp": (
                None
                if not atp or not ch
                else sum(1 for r in ch if r["correct_corrected"]) / len(ch)
                - sum(1 for r in atp if r["correct_corrected"]) / len(atp)
            ),
            "ci95": (
                None
                if not atp or not ch
                else newcombe_difference(
                    sum(1 for r in ch if r["correct_corrected"]), len(ch),
                    sum(1 for r in atp if r["correct_corrected"]), len(atp),
                )["ci95"]
            ),
        }
    return {
        "overall": overall,
        "crude_difference_ch_minus_atp": (
            overall["CHALLENGER"]["accuracy"] - overall["ATP_MAIN"]["accuracy"]
        ),
        "strata_full": strata,
        "strata_base_50": selected_strata,
        "holdout_and_selected": holdout_gap,
        "market_full": market,
        "market_base_50": market_selected,
        "market_tier_gap_ch_minus_atp": market_tier_gap,
        "model_tier_gap_on_market_eligible": model_tier_gap_on_market_eligible,
        "market_sensitivity_age_ge_0": market_age_ge_0,
        "market_sensitivity_all_quotes": market_all_quotes,
        "market_sensitivity_base_50_age_ge_0": market_selected_age_ge_0,
        "common_support_limitation": (
            "Direct standardization uses only strata with at least 10 ATP and 10 Challenger "
            "fixtures. ATP rows are scarce: 577 of 3,223. Cells without ATP support are "
            "reported, not extrapolated."
        ),
    }


# ---------------------------------------------------------------------------
# Phase 4 — uncertainty audit and research candidate
# ---------------------------------------------------------------------------
def uncertainty_audit(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    factor_cache: dict[int, dict[str, Any]] = {}

    def factors(row: Mapping[str, Any]) -> dict[str, Any]:
        key = id(row)
        if key not in factor_cache:
            factor_cache[key] = factor_values(row)
        return factor_cache[key]

    dimensions = {
        "overall_min_matches": lambda r: factors(r)["experience_min"],
        "favourite_matches": lambda r: experience_band(factors(r)["favourite_matches"]),
        "surface_experience_min": lambda r: factors(r)["surface_experience_min"],
        "candidate_count": lambda r: "MULTI" if factors(r)["candidate_multi"] else "SINGLE",
        "identity_completeness": lambda r: (
            "AMBIGUOUS" if factors(r)["any_ambiguous"]
            else "NO_HISTORY" if factors(r)["any_no_history"]
            else "COMPLETE"
        ),
        "inactivity_max": lambda r: factors(r)["inactivity_band_max"],
        "divergence_favourite": lambda r: (
            "100+" if factors(r)["divergence_favourite"] >= 100
            else "50-99" if factors(r)["divergence_favourite"] >= 50
            else "0-49"
        ),
        "rating_gap_band": lambda r: factors(r)["rating_gap_band"],
        "market_available": lambda r: "YES" if factors(r)["market_available"] else "NO",
    }
    out = {}
    for name, key_fn in dimensions.items():
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in observations:
            groups[key_fn(row)].append(row)
        rows = []
        for label in sorted(groups):
            subset = groups[label]
            summary = cohort_summary(subset)
            ps = []
            ys = []
            for r in subset:
                p = r["p1_corrected"]
                ps.append(p if r["side_corrected"] == 1 else 1.0 - p)
                ys.append(int(r["correct_corrected"]))
            slope = calibration_slope(ps, ys)
            ece = expected_calibration_error(ps, ys)
            murphy = murphy_decomposition(ps, ys)
            rows.append(
                {
                    "stratum": label,
                    **summary,
                    "calibration_slope": slope["slope"],
                    "calibration_intercept": slope["intercept"],
                    "calibration_status": slope["status"],
                    "ece": ece["ece"],
                    "murphy_reliability": murphy["reliability"],
                    "murphy_resolution": murphy["resolution"],
                    "murphy_uncertainty": murphy["uncertainty"],
                }
            )
        out[name] = rows
    return {
        "dimensions": out,
        "note": (
            "The model probability is an estimated winning probability; these strata "
            "measure whether the reliability of the supporting information is reflected. "
            "A calibration slope below 1 means the log-odds are too wide for that stratum."
        ),
    }


def reliability_flag_analysis(
    observations: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """Pre-registered information-reliability flag (no probability change).

    The flag is a diagnostic, not a selection or probability policy.  It marks a
    forecast when the supporting information is weak: the favourite has fewer
    than five observed matches, a player slot is unresolved, the shorter surface
    history is under five matches, or the favourite's overall and surface
    ratings diverge by 100+ Elo.  The rule was written before the November
    evaluation and uses no outcome.
    """

    def flags(o: Mapping[str, Any]) -> dict[str, bool]:
        f = factor_values(o)
        return {
            "favourite_lt5_matches": f["favourite_matches"] < 5,
            "unresolved_identity": f["any_ambiguous"] or f["any_no_history"],
            "surface_lt5": f["min_surface_matches"] < 5,
            "rating_divergence_100": f["divergence_favourite"] >= 100,
        }

    def flagged(o: Mapping[str, Any]) -> bool:
        return any(flags(o).values())

    def score(o: Mapping[str, Any]) -> int:
        return sum(1 for v in flags(o).values() if v)

    dev = [o for o in observations if str(o["date"]) <= DEV_END]
    holdout = holdout_rows(observations)

    def evaluate(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, Any]:
        flag_rows = [o for o in rows if flagged(o)]
        clean_rows = [o for o in rows if not flagged(o)]
        return {
            "label": label,
            "n": len(rows),
            "flagged_n": len(flag_rows),
            "flagged_share": len(flag_rows) / len(rows) if rows else None,
            "flagged": cohort_summary(flag_rows),
            "unflagged": cohort_summary(clean_rows),
            "error_rate_difference_flagged_minus_unflagged": (
                None
                if not flag_rows or not clean_rows
                else (1 - cohort_summary(flag_rows)["accuracy"])
                - (1 - cohort_summary(clean_rows)["accuracy"])
            ),
            "risk_difference_ci95": (
                None
                if not flag_rows or not clean_rows
                else newcombe_difference(
                    sum(1 for o in flag_rows if not o["correct_corrected"]), len(flag_rows),
                    sum(1 for o in clean_rows if not o["correct_corrected"]), len(clean_rows),
                )["ci95"]
            ),
        }

    by_score = {}
    for value in range(5):
        subset = [o for o in observations if score(o) == value]
        if subset:
            by_score[str(value)] = cohort_summary(subset)
            by_score[str(value)]["n"] = len(subset)

    cohorts = frozen_cohorts(observations, thresholds)
    marginal = list(cohorts["marginal_40_45"]) + list(cohorts["marginal_45_50"])
    high_risk = [o for o in observations if score(o) >= 3]
    return {
        "version": "A1_INFORMATION_RELIABILITY_FLAG_V1",
        "rule": (
            "FLAGGED if favourite observed matches <5 OR any unresolved identity "
            "OR minimum surface history <5 OR favourite overall/surface divergence >=100 Elo."
        ),
        "probability_changed": False,
        "selection_changed": False,
        "development": evaluate(dev, "development"),
        "holdout": evaluate(holdout, "holdout"),
        "full": evaluate(observations, "full"),
        "high_risk_3plus": {
            "development": cohort_summary([o for o in dev if score(o) >= 3]),
            "holdout": cohort_summary([o for o in holdout if score(o) >= 3]),
            "full": cohort_summary(high_risk),
            "marginal_pool_n": sum(1 for o in marginal if score(o) >= 3),
            "marginal_pool_errors": sum(1 for o in marginal if score(o) >= 3 and not o["correct_corrected"]),
            "marginal_pool_total_errors": sum(1 for o in marginal if not o["correct_corrected"]),
        },
        "by_risk_score": by_score,
        "note": (
            "Diagnostic only. It does not change p, the selected side, the frozen "
            "thresholds or any production policy. A single factor has no aggregate "
            "discrimination; only 3+ simultaneous risk factors separate."
        ),
    }


def fit_information_shrinkage(
    development: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Pre-registered research candidate: reliability-conditional logit shrink.

    The candidate is deliberately narrow.  It is fitted on the development
    period only and applies to a fixture only when the *favourite* has fewer
    than 10 observed prior matches **and** the forecast is in the 0.60-0.75
    confidence band, where the marginal cohorts live.  It never changes the
    selected side and never changes a frozen threshold.  The fit is a single
    scalar slope on log-odds; if the observed calibration slope is not below
    0.95 the candidate is declared unjustified and no adjustment is emitted.
    """
    subset = [
        o for o in development
        if o["confidence_corrected"] < 0.75
        and o["confidence_corrected"] >= 0.60
        and _value(o["matches_corrected"][f"p{o['favourite_side']}"]) < 10
    ]
    ps = []
    ys = []
    for o in subset:
        p = o["p1_corrected"]
        ps.append(p if o["side_corrected"] == 1 else 1.0 - p)
        ys.append(int(o["correct_corrected"]))
    slope = calibration_slope(ps, ys)
    justified = (
        slope["status"] == "OK"
        and slope["slope"] is not None
        and slope["slope"] < 0.95
        and len(subset) >= 100
    )
    return {
        "definition": "favourite <10 prior matches AND confidence in [0.60,0.75)",
        "development_n": len(subset),
        "development_accuracy": rate(sum(ys), len(ys)),
        "development_mean_confidence": mean(ps) if ps else None,
        "fitted_calibration_slope": slope["slope"],
        "fitted_calibration_intercept": slope["intercept"],
        "fit_status": slope["status"],
        "justified": justified,
        "candidate_slope": slope["slope"] if justified else None,
        "note": (
            "Fitted on 2026-09-10..2026-10-31 only. A slope >= 0.95 means the "
            "evidence does not justify a separate candidate; no adjustment is "
            "then evaluated or recommended."
        ),
    }


def evaluate_information_shrinkage(
    observations: Sequence[Mapping[str, Any]],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    if not candidate.get("justified") or candidate.get("candidate_slope") is None:
        return {"status": "NOT_JUSTIFIED", "evaluated": False}
    slope = float(candidate["candidate_slope"])
    holdout = holdout_rows(observations)
    target = [
        o for o in holdout
        if o["confidence_corrected"] < 0.75
        and o["confidence_corrected"] >= 0.60
        and _value(o["matches_corrected"][f"p{o['favourite_side']}"]) < 10
    ]
    if not target:
        return {"status": "NO_HOLDOUT_ROWS", "evaluated": False}
    before, after = [], []
    for o in target:
        p = o["p1_corrected"]
        selected = p if o["side_corrected"] == 1 else 1.0 - p
        y = int(o["correct_corrected"])
        shrunk = sigmoid(slope * logit(min(max(selected, 1e-6), 1 - 1e-6)))
        before.append((selected, y))
        after.append((shrunk, y))
    return {
        "status": "RESEARCH_ONLY_EVALUATED",
        "evaluated": True,
        "holdout_n": len(target),
        "holdout_accuracy": rate(sum(y for _p, y in before), len(before)),
        "mean_confidence_before": mean(p for p, _y in before),
        "mean_confidence_after": mean(p for p, _y in after),
        "brier_before": brier(before),
        "brier_after": brier(after),
        "log_loss_before": log_loss(before),
        "log_loss_after": log_loss(after),
        "winner_side_changes": 0,
        "note": (
            "Shrinkage cannot change the selected side or any frozen threshold. "
            "This is a calibration diagnostic on the November holdout, not a "
            "policy candidate for promotion."
        ),
    }


# ---------------------------------------------------------------------------
# Phase 5 — inactivity and surface transitions
# ---------------------------------------------------------------------------
def inactivity_analysis(
    observations: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    cohorts = frozen_cohorts(observations, thresholds)
    marginal_pool = list(cohorts["marginal_40_45"]) + list(cohorts["marginal_45_50"])
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for o in observations:
        groups[factor_values(o)["inactivity_band_max"]].append(o)
    tables = {}
    for label in [b[0] for b in INACTIVITY_BANDS] + ["MISSING"]:
        subset = groups.get(label, [])
        if not subset:
            continue
        tables[label] = {
            "all": cohort_summary(subset),
            "holdout": cohort_summary([r for r in subset if r in cohorts["holdout"]]),
            "marginal": cohort_summary([r for r in subset if r in marginal_pool]),
            "favourite_side": _inactivity_side_table(subset, "favourite"),
            "underdog_side": _inactivity_side_table(subset, "underdog"),
        }
    # Recovered-history inactivity: original vs corrected namespace.
    recovered = [o for o in observations if factor_values(o)["history_recovered"]]
    recovered_rows = []
    for o in recovered:
        fav = o["favourite_side"]
        original = o["inactivity_original"][f"p{fav}"]
        corrected = o["inactivity_corrected"][f"p{fav}"]
        if original is not None and corrected is not None:
            recovered_rows.append(corrected - original)
    return {
        "bands": tables,
        "recovered_history_favourite_inactivity_change_days": {
            "n": len(recovered_rows),
            "mean": mean(recovered_rows) if recovered_rows else None,
            "median": median(recovered_rows) if recovered_rows else None,
            "share_improved": (
                sum(1 for v in recovered_rows if v < 0) / len(recovered_rows)
                if recovered_rows else None
            ),
        },
        "off_season_status": "BLOCKED_NO_JUNE_AUGUST_OR_JANUARY_POPULATION",
        "withdrawal_status": "BLOCKED_NO_WITHDRAWAL_RECORDS_IN_SETTLED_POPULATION",
    }


def _inactivity_side_table(rows: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    atp = [r for r in rows if r["tour"] == "ATP_MAIN"]
    ch = [r for r in rows if r["tour"] == "CHALLENGER"]
    return {
        "atp_n": len(atp),
        "atp_accuracy": rate(sum(1 for r in atp if r["correct_corrected"]), len(atp)),
        "challenger_n": len(ch),
        "challenger_accuracy": rate(sum(1 for r in ch if r["correct_corrected"]), len(ch)),
    }


def surface_transition_table(
    observations: Sequence[Mapping[str, Any]],
    ledger_by_prediction: Mapping[int, Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify the favourite's first/second/third match on a new surface.

    The observed result history begins 2026-09-01, so the classification is
    left-censored and the first weeks of the target window have little prior
    support.  Missing prior matches are reported, never imputed.
    """
    timeline: dict[int, list[tuple[str, str | None, int]]] = defaultdict(list)
    for match in history:
        surface = match.get("surface")
        start = match.get("match_start_at")
        if start is None:
            continue
        for side in ("player_1_id", "player_2_id"):
            pid = match.get(side)
            if pid is None:
                continue
            timeline[int(pid)].append((str(start), surface, int(match["match_id"])))
    for entries in timeline.values():
        entries.sort()

    def classify(pid: int | None, start: str, current_surface: str | None) -> dict[str, Any]:
        if pid is None:
            return {"state": "NO_LIVE_ID", "prior_matches": 0, "gap_days": None}
        prior = [x for x in timeline.get(int(pid), []) if x[0] < start and x[1] is not None]
        if not prior:
            return {"state": "NO_PRIOR_OBSERVED_MATCH", "prior_matches": 0, "gap_days": None}
        last_start, last_surface, _mid = prior[-1]
        gap = _days_between(last_start, start)
        if current_surface is None or last_surface is None:
            return {"state": "UNKNOWN_SURFACE", "prior_matches": len(prior), "gap_days": gap}
        if last_surface != current_surface:
            return {"state": "FIRST_ON_NEW_SURFACE", "prior_matches": len(prior), "gap_days": gap}
        run = 0
        for _start, surface, _mid in reversed(prior):
            if surface == current_surface:
                run += 1
            else:
                break
        state = "SECOND_ON_SURFACE" if run == 1 else "THIRD_ON_SURFACE" if run == 2 else "CONTINUING"
        return {"state": state, "prior_matches": len(prior), "gap_days": gap}

    cohort_rows = []
    for o in observations:
        ledger = ledger_by_prediction.get(int(o["prediction_id"]))
        if not ledger:
            continue
        fav = o["favourite_side"]
        dog = o["underdog_side"]
        fav_pid = ledger.get(f"p{fav}_live")
        dog_pid = ledger.get(f"p{dog}_live")
        fav_info = classify(fav_pid, o["match_start_at"], o["surface"] or None)
        dog_info = classify(dog_pid, o["match_start_at"], o["surface"] or None)
        cohort_rows.append(
            {
                **o,
                "surface_state": fav_info["state"],
                "surface_prior": fav_info["prior_matches"],
                "surface_gap_days": fav_info["gap_days"],
                "underdog_surface_state": dog_info["state"],
                "underdog_surface_prior": dog_info["prior_matches"],
            }
        )

    def aggregate(rows: Sequence[Mapping[str, Any]], key: str = "surface_state") -> dict[str, Any]:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        out = {}
        for state in sorted(groups):
            subset = groups[state]
            out[state] = cohort_summary(subset)
            out[state]["n"] = len(subset)
        return out

    cohorts = frozen_cohorts(observations, thresholds)
    marginal_pool = list(cohorts["marginal_40_45"]) + list(cohorts["marginal_45_50"])
    marginal_ids = {o["prediction_id"] for o in marginal_pool}
    core_ids = {o["prediction_id"] for o in cohorts["core_40"]}
    full = aggregate(cohort_rows)
    marginal = aggregate([r for r in cohort_rows if r["prediction_id"] in marginal_ids])
    core = aggregate([r for r in cohort_rows if r["prediction_id"] in core_ids])
    # Transition state distribution and accuracy by surface experience.
    cross = {}
    for state in sorted({r["surface_state"] for r in cohort_rows}):
        subset = [r for r in cohort_rows if r["surface_state"] == state]
        cross[state] = {
            "n": len(subset),
            "mean_min_surface_matches": mean(
                min(_value(r["surface_matches_corrected"]["p1"]), _value(r["surface_matches_corrected"]["p2"]))
                for r in subset
            ),
            "mean_abs_elo_gap": mean(abs(factor_values(r)["elo_gap"]) for r in subset),
            "mean_abs_surface_elo_gap": mean(abs(factor_values(r)["surface_elo_gap"]) for r in subset),
            "mean_inactivity": mean(
                factor_values(r)["max_inactivity"]
                for r in subset
                if factor_values(r)["max_inactivity"] is not None
            ) if any(factor_values(r)["max_inactivity"] is not None for r in subset) else None,
        }
    coverage = {
        "settled_rows": len(observations),
        "rows_with_live_ids": len(cohort_rows),
        "rows_with_prior_observed_match": sum(1 for r in cohort_rows if r["surface_prior"] > 0),
        "share_with_prior": (
            sum(1 for r in cohort_rows if r["surface_prior"] > 0) / len(cohort_rows)
            if cohort_rows else None
        ),
    }
    return {
        "coverage": coverage,
        "full": full,
        "marginal": marginal,
        "core": core,
        "underdog_full": aggregate(cohort_rows, "underdog_surface_state"),
        "underdog_marginal": aggregate(
            [r for r in cohort_rows if r["prediction_id"] in marginal_ids],
            "underdog_surface_state",
        ),
        "both_in_transition": {
            "n": sum(
                1 for r in cohort_rows
                if r["surface_state"] in ("FIRST_ON_NEW_SURFACE", "SECOND_ON_SURFACE", "THIRD_ON_SURFACE")
                and r["underdog_surface_state"] in ("FIRST_ON_NEW_SURFACE", "SECOND_ON_SURFACE", "THIRD_ON_SURFACE")
            )
        },
        "state_profile": cross,
        "history_window": {
            "first_observed_match": min((x[0] for xs in timeline.values() for x in xs), default=None),
            "last_observed_match": max((x[0] for xs in timeline.values() for x in xs), default=None),
            "left_censored": True,
        },
        "note": (
            "Surface transitions are observed only inside the API result history from "
            "2026-09-01. No off-season or January inference is made."
        ),
    }


def _days_between(start_iso: str, end_iso: str) -> float | None:
    try:
        from datetime import datetime
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        return (end - start).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Phase 6 — deterministic identity audit and census
# ---------------------------------------------------------------------------
def identity_ledger(
    observations: Sequence[Mapping[str, Any]],
    ledger_by_prediction: Mapping[int, Mapping[str, Any]],
    *,
    sample_size: int = 20,
) -> dict[str, Any]:
    """Deterministic, outcome-independent player sample and verification ledger.

    Ranking rule (fixed before looking at any outcome): the maximum absolute
    effective-rating change across a model identity's settled slots, descending,
    then model player id ascending.  The four model identities that receive more
    than one live id are mandatory and are added first; the remainder is filled
    from the ranking.  No outcome field enters the rule.
    """
    model_stats: dict[int, dict[str, Any]] = {}
    resolved_methods = {"ALIAS", "PERSISTED_BRIDGE", "HISTORICAL_ID"}
    resolved_slots = 0
    unresolved_slots = 0
    pair_days: Counter[tuple[int, int, str]] = Counter()
    for o in observations:
        ids = sorted(
            int(o["resolution"][s]["live_id"]) for s in ("p1", "p2")
            if o["resolution"][s]["live_id"] is not None
        )
        if len(ids) == 2:
            pair_days[(ids[0], ids[1], str(o["date"]))] += 1
    duplicate_pairs = {key for key, count in pair_days.items() if count > 1}
    duplicate_live_ids: set[int] = set()
    for low, high, _day in duplicate_pairs:
        duplicate_live_ids.add(low)
        duplicate_live_ids.add(high)
    for o in observations:
        for side in ("p1", "p2"):
            res = o["resolution"][side]
            if res["method"] not in resolved_methods:
                unresolved_slots += 1
                continue
            resolved_slots += 1
            model = res["model_id"]
            if model is None:
                unresolved_slots += 1
                resolved_slots -= 1
                continue
            key = int(model)
            delta_eff = abs(_value(o["effective_corrected"][side]) - _value(o["effective_original"][side]))
            delta_overall = abs(_value(o["overall_corrected"][side]) - _value(o["overall_original"][side]))
            delta_surface = abs(_value(o["surface_rating_corrected"][side]) - _value(o["surface_rating_original"][side]))
            delta_matches = _value(o["matches_corrected"][side]) - _value(o["matches_original"][side])
            entry = model_stats.setdefault(
                key,
                {
                    "model_player_id": key,
                    "live_ids": set(),
                    "slots": 0,
                    "max_abs_effective_delta": 0.0,
                    "max_abs_overall_delta": 0.0,
                    "max_abs_surface_delta": 0.0,
                    "max_recovered_matches": 0.0,
                    "max_abs_probability_delta": 0.0,
                    "methods": Counter(),
                    "candidate_counts": Counter(),
                    "evidence": Counter(),
                    "name_sources": Counter(),
                    "aliases": set(),
                    "matches_before_min": None,
                    "matches_before_max": None,
                    "matches_after_min": None,
                    "matches_after_max": None,
                    "overall_delta_min": None,
                    "overall_delta_max": None,
                    "surface_delta_min": None,
                    "surface_delta_max": None,
                    "probability_delta_min": None,
                    "probability_delta_max": None,
                    "best_slot": None,
                },
            )
            entry["slots"] += 1
            entry["live_ids"].add(res["live_id"])
            entry["methods"][res["method"]] += 1
            entry["candidate_counts"][res["candidate_count"]] += 1
            entry["evidence"][res["evidence"]] += 1
            entry["name_sources"][res["name_source"]] += 1
            before_matches = _value(o["matches_original"][side])
            after_matches = _value(o["matches_corrected"][side])
            overall_delta = _value(o["overall_corrected"][side]) - _value(o["overall_original"][side])
            surface_delta = _value(o["surface_rating_corrected"][side]) - _value(o["surface_rating_original"][side])
            probability_delta = (
                o["p1_corrected"] if side == "p1" else 1.0 - o["p1_corrected"]
            ) - (
                o["p1_original"] if side == "p1" else 1.0 - o["p1_original"]
            )
            for key, value in (
                ("matches_before", before_matches),
                ("matches_after", after_matches),
                ("overall_delta", overall_delta),
                ("surface_delta", surface_delta),
                ("probability_delta", probability_delta),
            ):
                low_key, high_key = f"{key}_min", f"{key}_max"
                entry[low_key] = value if entry[low_key] is None else min(entry[low_key], value)
                entry[high_key] = value if entry[high_key] is None else max(entry[high_key], value)
            ledger = ledger_by_prediction.get(int(o["prediction_id"]))
            alias = None
            if ledger:
                alias = (ledger.get(f"{side}_resolution") or {}).get("matched_alias")
            if alias:
                entry["aliases"].add(str(alias))
            probability_delta = abs(o["probability_delta"])
            entry["max_abs_overall_delta"] = max(entry["max_abs_overall_delta"], delta_overall)
            entry["max_abs_surface_delta"] = max(entry["max_abs_surface_delta"], delta_surface)
            entry["max_recovered_matches"] = max(entry["max_recovered_matches"], delta_matches)
            entry["max_abs_probability_delta"] = max(entry["max_abs_probability_delta"], probability_delta)
            current_best = entry["best_slot"]
            better = (
                current_best is None
                or delta_eff > entry["max_abs_effective_delta"]
                or (
                    delta_eff == entry["max_abs_effective_delta"]
                    and int(o["prediction_id"]) < int(current_best["prediction_id"])
                )
            )
            if better:
                entry["max_abs_effective_delta"] = delta_eff
                entry["best_slot"] = {
                    "prediction_id": o["prediction_id"],
                    "match_id": o["match_id"],
                    "date": o["date"],
                    "tour": o["tour"],
                    "round": o["round_code"],
                    "surface": o["surface"],
                    "side": side,
                    "live_player_id": res["live_id"],
                    "model_player_id": key,
                    "method": res["method"],
                    "evidence": res["evidence"],
                    "name_source": res["name_source"],
                    "candidate_count": res["candidate_count"],
                    "bridged": res["bridged"],
                    "matches_before": _value(o["matches_original"][side]),
                    "matches_after": _value(o["matches_corrected"][side]),
                    "recovered_matches": delta_matches,
                    "overall_before": _value(o["overall_original"][side]),
                    "overall_after": _value(o["overall_corrected"][side]),
                    "surface_before": _value(o["surface_rating_original"][side]),
                    "surface_after": _value(o["surface_rating_corrected"][side]),
                    "effective_before": _value(o["effective_original"][side]),
                    "effective_after": _value(o["effective_corrected"][side]),
                    "probability_before": o["p1_original"] if side == "p1" else 1.0 - o["p1_original"],
                    "probability_after": o["p1_corrected"] if side == "p1" else 1.0 - o["p1_corrected"],
                    "side_changed": o["side_changed"],
                }

    multi_live = sorted(
        (entry for entry in model_stats.values() if len(entry["live_ids"]) > 1),
        key=lambda e: e["model_player_id"],
    )
    ranked = sorted(
        model_stats.values(),
        key=lambda e: (-e["max_abs_effective_delta"], e["model_player_id"]),
    )
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for entry in multi_live:
        selected.append(entry)
        seen.add(entry["model_player_id"])
    for entry in ranked:
        if len(selected) >= sample_size:
            break
        if entry["model_player_id"] in seen:
            continue
        selected.append(entry)
        seen.add(entry["model_player_id"])
    selected.sort(key=lambda e: (-e["max_abs_effective_delta"], e["model_player_id"]))
    for entry in selected:
        entry["duplicate_pair_flag"] = any(
            int(live) in duplicate_live_ids for live in entry["live_ids"]
        )
    return {
        "sampling_rule": (
            "Mandatory: model identities with more than one live id (4). Then the highest "
            "maximum absolute effective-rating change across settled slots, ties by model id "
            "ascending, until 20 unique model identities. Outcome fields are not used."
        ),
        "population_slots": sum(1 for o in observations for _ in ("p1", "p2")),
        "resolved_slots": resolved_slots,
        "unresolved_slots": unresolved_slots,
        "resolved_methods": sorted(resolved_methods),
        "unique_live_players": len({o["resolution"][s]["live_id"] for o in observations for s in ("p1", "p2")}),
        "unique_model_players": len(model_stats),
        "multi_live_model_identities": len(multi_live),
        "selected": [_serialise_model_entry(entry) for entry in selected[:sample_size]],
        "note": (
            "Aliases are public competition names retained for identity verification. "
            "No contact details, account identifiers or provider payloads are present."
        ),
    }


def _serialise_model_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    detail = entry["best_slot"] or {}
    verification = {
        "alias_count": len(entry["aliases"]),
        "single_live_id": len(entry["live_ids"]) == 1,
        "matches_not_reduced": (
            detail.get("matches_after") is not None
            and detail.get("matches_before") is not None
            and detail["matches_after"] >= detail["matches_before"]
        ),
        "candidate_ambiguity": max(entry["candidate_counts"].keys()) if entry["candidate_counts"] else None,
        "same_day_duplicate_pair_flag": bool(entry.get("duplicate_pair_flag")),
        "method": detail.get("method"),
        "evidence": detail.get("evidence"),
        "name_source": detail.get("name_source"),
        "provenance": (
            "tennis_data alias / PIT canonical name already in the production corpus; "
            "Sackmann-associated lineage, commercial training rights not verified"
        ),
    }
    return {
        "model_player_id": entry["model_player_id"],
        "live_player_ids": sorted(entry["live_ids"]),
        "live_id_count": len(entry["live_ids"]),
        "settled_slots": entry["slots"],
        "aliases": sorted(entry["aliases"]),
        "methods": dict(entry["methods"]),
        "candidate_counts": {str(k): v for k, v in entry["candidate_counts"].items()},
        "evidence": dict(entry["evidence"]),
        "name_sources": dict(entry["name_sources"]),
        "matches_before_range": [entry["matches_before_min"], entry["matches_before_max"]],
        "matches_after_range": [entry["matches_after_min"], entry["matches_after_max"]],
        "overall_rating_delta_range": [entry["overall_delta_min"], entry["overall_delta_max"]],
        "surface_rating_delta_range": [entry["surface_delta_min"], entry["surface_delta_max"]],
        "probability_delta_range": [entry["probability_delta_min"], entry["probability_delta_max"]],
        "max_abs_effective_rating_delta": entry["max_abs_effective_delta"],
        "max_abs_overall_rating_delta": entry["max_abs_overall_delta"],
        "max_abs_surface_rating_delta": entry["max_abs_surface_delta"],
        "max_recovered_matches": entry["max_recovered_matches"],
        "max_abs_probability_delta": entry["max_abs_probability_delta"],
        "verification": verification,
        "detail": entry["best_slot"],
    }


def method_risk_supplement(
    observations: Sequence[Mapping[str, Any]],
    ledger_by_prediction: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    """Method-diversity and duplicate/merge risk cases outside the top-20 sample."""
    resolved_methods = {"ALIAS", "PERSISTED_BRIDGE", "HISTORICAL_ID"}
    persisted = []
    ambiguous = []
    alias_to_models: dict[str, set[int]] = defaultdict(set)
    model_to_aliases: dict[int, set[str]] = defaultdict(set)
    for o in observations:
        ledger = ledger_by_prediction.get(int(o["prediction_id"]))
        for side in ("p1", "p2"):
            res = o["resolution"][side]
            delta = abs(_value(o["effective_corrected"][side]) - _value(o["effective_original"][side]))
            case = {
                "prediction_id": o["prediction_id"],
                "match_id": o["match_id"],
                "date": o["date"],
                "tour": o["tour"],
                "side": side,
                "live_player_id": res["live_id"],
                "model_player_id": res["model_id"],
                "method": res["method"],
                "evidence": res["evidence"],
                "candidate_count": res["candidate_count"],
                "abs_effective_rating_delta": delta,
                "matches_before": _value(o["matches_original"][side]),
                "matches_after": _value(o["matches_corrected"][side]),
                "alias": None,
            }
            if ledger:
                case["alias"] = (ledger.get(f"{side}_resolution") or {}).get("matched_alias")
            if res["method"] == "PERSISTED_BRIDGE":
                persisted.append(case)
            if res["method"] == "AMBIGUOUS" or res["candidate_count"] > 1:
                ambiguous.append(case)
            if (
                res["method"] in resolved_methods
                and res["model_id"] is not None
                and case["alias"]
            ):
                alias_to_models[str(case["alias"])].add(int(res["model_id"]))
                model_to_aliases[int(res["model_id"])].add(str(case["alias"]))
    ambiguous.sort(key=lambda c: (-c["abs_effective_rating_delta"], c["prediction_id"], c["side"]))
    alias_collisions = sorted(
        (
            {"alias": alias, "model_player_ids": sorted(models), "model_count": len(models)}
            for alias, models in alias_to_models.items()
            if len(models) > 1
        ),
        key=lambda c: (-c["model_count"], c["alias"]),
    )
    split_models = sorted(
        (
            {"model_player_id": model, "aliases": sorted(aliases), "alias_count": len(aliases)}
            for model, aliases in model_to_aliases.items()
            if len(aliases) > 1
        ),
        key=lambda c: (-c["alias_count"], c["model_player_id"]),
    )
    # Same unordered live pair on the same calendar day.
    pair_days: Counter[tuple[int, int, str]] = Counter()
    for o in observations:
        ids = sorted(
            int(o["resolution"][s]["live_id"]) for s in ("p1", "p2")
            if o["resolution"][s]["live_id"] is not None
        )
        if len(ids) == 2:
            pair_days[(ids[0], ids[1], str(o["date"]))] += 1
    duplicate_pairs = [
        {"player_pair": [a, b], "date": day, "fixtures": n}
        for (a, b, day), n in sorted(pair_days.items())
        if n > 1
    ]
    return {
        "persisted_bridge_cases": persisted,
        "ambiguous_or_multi_candidate_top10": ambiguous[:10],
        "ambiguous_or_multi_candidate_count": len(ambiguous),
        "alias_to_multiple_models": alias_collisions[:20],
        "alias_collision_count": len(alias_collisions),
        "model_to_multiple_aliases": split_models[:20],
        "split_model_count": len(split_models),
        "same_day_player_pair_duplicates": duplicate_pairs,
        "same_day_duplicate_count": len(duplicate_pairs),
        "note": (
            "These are fail-closed risk flags, not proof of an incorrect merge. "
            "Alias collisions need source-level review before any production use."
        ),
    }


def slot_census(
    ledger_rows: Sequence[Mapping[str, Any]],
    *,
    expected_slots: int | None = None,
) -> dict[str, Any]:
    """Strict v3 per-slot census for the 3,776-target ledger.

    One record is emitted for every slot, including ``NO_PLAYER`` and
    ``NO_CANDIDATE``.  The 3,778-target aggregate snapshot is not available
    offline; the arithmetic that closes its five-slot reporting gap is recorded
    explicitly.
    """
    records = []
    for row in ledger_rows:
        for side in ("p1", "p2"):
            live = row.get(f"{side}_live")
            res = row.get(f"{side}_resolution") or {}
            method = res.get("method") or "NO_METHOD"
            fallback = res.get("resolved_player_id")
            if live is None:
                status = "NO_PLAYER"
            elif method == "NO_HISTORICAL_CANDIDATE":
                status = "NO_CANDIDATE"
            elif method == "AMBIGUOUS":
                status = "AMBIGUOUS"
            elif method in ("ALIAS", "PERSISTED_BRIDGE", "HISTORICAL_ID"):
                status = "RESOLVED"
            else:
                status = "UNCLASSIFIED"
            records.append(
                {
                    "prediction_id": row["prediction_id"],
                    "match_id": row["match_id"],
                    "side": side,
                    "live_player_id": live,
                    "model_player_id": fallback if status == "RESOLVED" else None,
                    "fallback_player_id": fallback if status != "RESOLVED" else None,
                    "method": method,
                    "evidence": res.get("evidence") or "none",
                    "candidate_count": res.get("candidate_count") or 0,
                    "bridged": bool(res.get("bridged")),
                    "status": status,
                }
            )
    methods = Counter(r["method"] for r in records)
    statuses = Counter(r["status"] for r in records)
    method_classified = sum(1 for r in records if r["method"] != "NO_METHOD")
    resolved = sum(1 for r in records if r["status"] == "RESOLVED")
    return {
        "targets": len(ledger_rows),
        "slots": len(records),
        "expected_slots": expected_slots or len(ledger_rows) * 2,
        "method_classified_slots": method_classified,
        "method_unclassified_slots": len(records) - method_classified,
        "resolved_slots": resolved,
        "unresolved_slots": len(records) - resolved,
        "methods": dict(methods),
        "statuses": dict(statuses),
        "records": records,
    }


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, Counter):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    return str(value)
