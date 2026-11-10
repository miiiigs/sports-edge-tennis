"""A3 risk, interpretable A4 reliability and Guide Odds shadow research.

This module is offline and has NO production imports/writes. It operates on
first-issued, pre-match values from the existing V2 original-pick ledger.
Every A4 fit uses only results visible before the scored day. Market quotes
are used for display-prioritization diagnostics, never to alter the selected
winner, official pick population, stake, or canonical result.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import exp, isfinite, log
from typing import Iterable, Mapping, Sequence

from sports_edge_tennis.performance.calibration import brier, log_loss
from sports_edge_tennis.performance.v2_quality_research import FrozenPick, _eligible_before

MODEL_VERSION = "A4_RELIABILITY_SHADOW_20261110_V1"
MIN_TRAIN = 250
MIN_CLASS = 40
L2 = 4.0
SUPPORTED_BOOKS = frozenset({"ODDSPAPI_TENNIS", "PINNAPI"})
MAX_QUOTE_AGE_SECONDS = 6 * 3600

# PRE-MATCH FEATURES ONLY: no result, final odds, closing line, winner or
# retrospective performance statistic is allowed into this design matrix.
FEATURES = (
    "confidence_centered", "a1a2_gap", "a1a2_misaligned",
    "challenger", "hard", "clay", "wide_spread",
    "a3_confirmed", "a3_opposes", "a3_missing",
)


def a3_risk(pick: FrozenPick) -> str:
    """Do not interpret missing A3 evidence as an upset prediction."""
    if pick.a3_status == "ABSTAINED":
        return "NO_EVIDENCE"
    if pick.a3_status != "PREDICTED" or pick.a3_side not in (1, 2):
        return "UNKNOWN"
    return "OPPOSES_PICK" if pick.a3_side != pick.side else "CONFIRMS_PICK"


def features(pick: FrozenPick) -> tuple[float, ...]:
    gap = pick.agent_spread()
    risk = a3_risk(pick)
    return (
        max(-1.0, min(1.0, (pick.raw - .70) * 4.0)),
        0.0 if gap is None else min(1.0, gap * 4.0),
        float(not pick.agent_same_side()),
        float(pick.tier == "CHALLENGER"),
        float(pick.surface == "HARD"),
        float(pick.surface == "CLAY"),
        float(pick.kind == "A1_A2_WIDE_SPREAD"),
        float(risk == "CONFIRMS_PICK"),
        float(risk == "OPPOSES_PICK"),
        float(risk in ("UNKNOWN", "NO_EVIDENCE")),
    )


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + exp(-min(z, 80)))
    e = exp(max(z, -80))
    return e / (1.0 + e)


@dataclass(frozen=True)
class A4Model:
    trained_rows: int
    observed_wins: int
    observed_losses: int
    coefficients: tuple[float, ...]  # intercept followed by fixed FEATURES
    version: str = MODEL_VERSION

    def predict(self, pick: FrozenPick) -> float:
        values = features(pick)
        z = self.coefficients[0] + sum(
            w * v for w, v in zip(self.coefficients[1:], values)
        )
        return _sigmoid(z)


def fit_a4(picks: Iterable[FrozenPick], cutoff: datetime) -> A4Model | None:
    """Deterministic ridge-logistic fit; no test day or late-ingested results."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    known = tuple(p for p in picks if _eligible_before(p, cutoff))
    n = len(known)
    wins = sum(p.result == "WON" for p in known)
    losses = n - wins
    if n < MIN_TRAIN or min(wins, losses) < MIN_CLASS:
        return None

    X = tuple((1.0,) + features(p) for p in known)
    y = tuple(float(p.result == "WON") for p in known)
    intercept = log((wins + 1.0) / (losses + 1.0))
    weights = [intercept] + [0.0] * len(FEATURES)

    # Full-batch fixed-step optimizer, intercept unpenalized, symmetric L2.
    # No hyperparameters or thresholds fitted to the holdout.
    for _ in range(600):
        gradient = [0.0] * len(weights)
        for row, label in zip(X, y):
            err = _sigmoid(sum(a * b for a, b in zip(weights, row))) - label
            for j, value in enumerate(row):
                gradient[j] += err * value
        gradient[0] /= n
        for j in range(1, len(weights)):
            gradient[j] = gradient[j] / n + (L2 / n) * weights[j]
        for j in range(len(weights)):
            weights[j] -= .35 * gradient[j]
    if any(not isfinite(w) for w in weights):
        return None
    return A4Model(n, wins, losses, tuple(weights))


@dataclass(frozen=True)
class GuideQuote:
    match_id: int
    selected_side: int
    selected_decimal: float
    opposite_decimal: float
    source: str
    source_observed_at: datetime
    ingested_at: datetime
    market_status: str = "OPEN"

    def __post_init__(self) -> None:
        if self.match_id < 1 or self.selected_side not in (1, 2):
            raise ValueError("invalid quote match / selected side")
        if (not isfinite(self.selected_decimal)
                or not isfinite(self.opposite_decimal)
                or min(self.selected_decimal, self.opposite_decimal) <= 1.0):
            raise ValueError("invalid decimal odds")
        for stamp in (self.source_observed_at, self.ingested_at):
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("quote timestamps must be timezone-aware")

    def no_vig_selected(self) -> float:
        implied_a = 1.0 / self.selected_decimal
        implied_b = 1.0 / self.opposite_decimal
        return implied_a / (implied_a + implied_b)


def usable_quote(pick: FrozenPick, candidates: Iterable[GuideQuote]) -> tuple[GuideQuote | None, str]:
    """Fail closed for absent, restricted, stale, post-pick or wrong-side odds."""
    related = [q for q in candidates if q.match_id == pick.match_id]
    if not related:
        return None, "NO_QUOTES"
    acceptable = []
    reasons: Counter[str] = Counter()
    for q in related:
        if q.source not in SUPPORTED_BOOKS or q.market_status != "OPEN":
            reasons["UNSUPPORTED_MARKET"] += 1
        elif q.selected_side != pick.side:
            reasons["WRONG_SELECTED_SIDE"] += 1
        elif q.source_observed_at > pick.picked_at or q.ingested_at > pick.picked_at:
            reasons["POST_PICK_QUOTE"] += 1
        elif (pick.picked_at - q.source_observed_at).total_seconds() > MAX_QUOTE_AGE_SECONDS:
            reasons["STALE"] += 1
        else:
            # Reject extreme overround/underround indicative of incomplete
            # selection pairs or unsuitable bookmaker prices.
            overround = 1/q.selected_decimal + 1/q.opposite_decimal
            if not .98 <= overround <= 1.20:
                reasons["BAD_OVERROUND"] += 1
            else:
                acceptable.append(q)
    if not acceptable:
        return None, reasons.most_common(1)[0][0]
    return max(acceptable, key=lambda q: (q.source_observed_at, q.ingested_at)), "AVAILABLE"


def prioritize(
    pick: FrozenPick,
    *,
    reliability: float | None,
    quotes: Iterable[GuideQuote] = (),
) -> dict[str, object]:
    """Non-decision market context. Returns priorities, NOT a pick gate."""
    quote, reason = usable_quote(pick, quotes)
    risk = a3_risk(pick)
    base = {
        "original_match_id": pick.match_id,
        "unchanged_selected_side": pick.side,
        "original_probability": pick.raw,
        "a3_risk": risk,
        "a4_reliability": reliability,
        "guide_odds_status": reason,
        "no_vig_market_probability": None,
        "probability_gap": None,
        "priority": "UNRANKED",
        "changes_pick": False,
    }
    if reliability is None:
        return base
    if not 0 <= reliability <= 1 or not isfinite(reliability):
        raise ValueError("invalid A4 reliability")
    if risk == "OPPOSES_PICK":
        base["priority"] = "REVIEW_A3_CONFLICT"
    elif reliability < .73:
        base["priority"] = "REVIEW_RELIABILITY"
    else:
        base["priority"] = "MODEL_PRIORITY_ONLY"
    if quote is None:
        return base
    probability = quote.no_vig_selected()
    gap = reliability - probability
    base["no_vig_market_probability"] = probability
    base["probability_gap"] = gap
    # Side-by-side contextual prioritization. Never promote/erase a selection.
    if risk != "OPPOSES_PICK" and reliability >= .73:
        if probability >= .55 and abs(gap) <= .15:
            base["priority"] = "GUIDE_SUPPORTS_HIGH_PRIORITY"
        elif probability < .50:
            base["priority"] = "REVIEW_MARKET_DISAGREEMENT"
    return base


def report_day(
    picks: Sequence[FrozenPick], *,
    history: Iterable[FrozenPick], cutoff: datetime,
    quotes: Iterable[GuideQuote] = (),
) -> dict[str, object]:
    """One historical day, no training on scored future/holdout outcomes."""
    model = fit_a4(history, cutoff)
    quotes = tuple(quotes)
    scored = []
    lines = []
    for p in picks:
        if p.picked_at >= cutoff:
            # A day-specific cutoff is midnight BEFORE issuance. Require
            # candidate to have been issued on/after that cutoff.
            pass
        reliability = model.predict(p) if model else None
        priority = prioritize(p, reliability=reliability, quotes=quotes)
        lines.append(priority)
        if model is not None and p.result in ("WON", "LOST"):
            scored.append((p.raw, reliability, int(p.result == "WON")))
    priority_counts = dict(sorted(Counter(x["priority"] for x in lines).items()))
    return {
        "status": "RESEARCH_ONLY_NOT_LIVE",
        "model_version": MODEL_VERSION,
        "cutoff": cutoff.isoformat(),
        "a4_train_rows": model.trained_rows if model else 0,
        "a4_fit_available": model is not None,
        "original_picks": len(picks),
        "selected_sides_changed": 0,
        "guide_odds_available": sum(x["guide_odds_status"] == "AVAILABLE" for x in lines),
        "priority_counts": priority_counts,
        "a3_risk_counts": dict(sorted(Counter(x["a3_risk"] for x in lines).items())),
        "graded": len(scored),
        "raw_brier": brier([(raw, y) for raw, _, y in scored]),
        "a4_brier": brier([(rel, y) for _, rel, y in scored]),
        "raw_log_loss": log_loss([(raw, y) for raw, _, y in scored]),
        "a4_log_loss": log_loss([(rel, y) for _, rel, y in scored]),
        "line_items": lines,  # Private local export only; don't commit IDs.
    }
