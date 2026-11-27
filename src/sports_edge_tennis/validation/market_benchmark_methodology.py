"""Reproducible Agent 1 versus market benchmark methodology.

This module implements the four pre-registered comparisons:

A. Agent 1 versus the market favourite on identical selected fixtures.
B. Agent 1 versus market-implied probabilities on every common eligible fixture.
C. Independently ranked Agent 1 and market selections at equivalent coverage.
D. A market-assisted candidate versus the market alone, only when a legitimate
   original-time evidence base exists.

It also compares three market probability definitions:

* one consistently available reference bookmaker (Pinnacle when present);
* conventional multi-book consensus;
* consensus after collapsing exact duplicate price pairs, because several
  "bookmakers" can publish the same price and are not independent information.

Every quote carries an explicit provenance class. Only ``ORIGINAL_TIME`` rows
may be used for A/B/D or described as a market edge. Late or backfilled quotes
are reported separately and never promoted. All metrics are computed on stored
fixtures; no provider call is made.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

BENCHMARK_VERSION = "MARKET_BENCHMARK_METHODOLOGY_V1"
MIN_ORIGINAL_TIME_FIXTURES = 100
MIN_ORIGINAL_TIME_FIXTURES_FOR_ASSISTED = 200
BOOTSTRAP_ITERATIONS = 4000
BOOTSTRAP_SEED = 20261127
CALIBRATION_BINS = 10
EPSILON = 1e-12


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _logit(value: float) -> float:
    clipped = min(1 - 1e-9, max(1e-9, value))
    return math.log(clipped / (1 - clipped))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


@dataclass(frozen=True)
class MarketQuotePair:
    match_id: int
    bookmaker_code: str
    source_system: str
    player_1_price: float
    player_2_price: float
    observed_at: datetime
    ingested_at: datetime
    provenance: str
    market_status: str = "OPEN"

    def __post_init__(self) -> None:
        if self.player_1_price <= 1 or self.player_2_price <= 1:
            raise ValueError("decimal prices must be greater than 1")
        if self.provenance not in (
            "ORIGINAL_TIME", "LATE", "BACKFILLED", "RETROSPECTIVE_ONLY",
        ):
            raise ValueError(f"unsupported provenance {self.provenance!r}")
        _utc(self.observed_at)
        _utc(self.ingested_at)

    @property
    def booksum(self) -> float:
        return 1 / self.player_1_price + 1 / self.player_2_price

    @property
    def market_p1(self) -> float:
        """Multiplicative de-vig probability that player 1 wins."""
        return (1 / self.player_1_price) / self.booksum

    @property
    def price_pair_key(self) -> tuple[float, float]:
        return (
            round(float(self.player_1_price), 6),
            round(float(self.player_2_price), 6),
        )

    @property
    def is_original_time(self) -> bool:
        return self.provenance == "ORIGINAL_TIME"


@dataclass(frozen=True)
class BenchmarkFixture:
    match_id: int
    winner: int
    model_p1: float
    scheduled_start_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.winner not in (1, 2):
            raise ValueError("winner must be 1 or 2")
        if not 0 < self.model_p1 < 1:
            raise ValueError("model_p1 must be strictly between 0 and 1")


def market_probability(
    quotes: Sequence[MarketQuotePair],
    *,
    method: str = "consensus_deduplicated",
    reference_bookmaker: str = "PINNACLE",
    provenance: str = "ORIGINAL_TIME",
) -> float | None:
    """Market-implied player-1 probability under one definition.

    ``provenance='ORIGINAL_TIME'`` is the only setting that may support a
    market-edge claim. ``provenance='RETROSPECTIVE'`` admits late/backfilled
    quotes and exists solely for an explicitly labelled descriptive comparison.
    """
    if provenance == "ORIGINAL_TIME":
        usable = [
            quote for quote in quotes
            if quote.is_original_time and quote.market_status == "OPEN"
        ]
    elif provenance == "RETROSPECTIVE":
        usable = [
            quote for quote in quotes
            if quote.provenance in ("LATE", "BACKFILLED", "RETROSPECTIVE_ONLY")
            and quote.market_status == "OPEN"
        ]
    else:
        raise ValueError(f"unsupported provenance filter {provenance!r}")
    if not usable:
        return None
    if method == "reference":
        reference = [
            quote for quote in usable
            if quote.bookmaker_code.casefold() == reference_bookmaker.casefold()
            and isinstance(quote, MarketQuotePair)
        ]
        if not reference:
            return None
        latest = max(reference, key=lambda item: (_utc(item.observed_at), item.bookmaker_code))
        return latest.market_p1
    if method == "consensus":
        return sum(quote.market_p1 for quote in usable) / len(usable)
    if method == "consensus_deduplicated":
        unique: dict[tuple[float, float], MarketQuotePair] = {}
        for quote in usable:
            unique.setdefault(quote.price_pair_key, quote)
        values = [quote.market_p1 for quote in unique.values()]
        return sum(values) / len(values) if values else None
    raise ValueError(f"unsupported market probability method {method!r}")


def duplicate_price_pair_accounting(
    quotes: Sequence[MarketQuotePair],
    *,
    provenance: str = "ORIGINAL_TIME",
) -> dict[str, Any]:
    """Count books and exact duplicate pairs, preserving each pair once."""
    if provenance == "ORIGINAL_TIME":
        usable = [quote for quote in quotes if quote.is_original_time]
    elif provenance == "RETROSPECTIVE":
        usable = [
            quote for quote in quotes
            if quote.provenance in ("LATE", "BACKFILLED", "RETROSPECTIVE_ONLY")
        ]
    else:
        raise ValueError(f"unsupported provenance filter {provenance!r}")
    groups: dict[tuple[float, float], list[str]] = {}
    for quote in usable:
        groups.setdefault(quote.price_pair_key, []).append(quote.bookmaker_code)
    duplicated = {
        f"{key[0]:.6f}/{key[1]:.6f}": sorted(set(books))
        for key, books in groups.items() if len(set(books)) > 1
    }
    return {
        "bookmakers": len({quote.bookmaker_code for quote in usable}),
        "price_pairs": len(groups),
        "exact_duplicate_pairs": len(duplicated),
        "duplicate_bookmaker_groups": duplicated,
        "independent_information_units": len(groups),
        "note": (
            "Several bookmakers publishing one identical price pair are not "
            "independent observations. De-duplicated consensus counts the pair once."
        ),
    }


def _binary_metrics(rows: Sequence[Mapping[str, Any]], p_key: str = "p1") -> dict[str, Any]:
    if not rows:
        return {"n": 0, "accuracy": None, "brier": None, "log_loss": None,
                "ece": None, "calibration_slope": None, "favourite_agreement": None}
    correct = 0
    brier = 0.0
    log_loss = 0.0
    for row in rows:
        p1 = float(row[p_key])
        outcome = 1.0 if int(row["winner"]) == 1 else 0.0
        predicted_side = 1 if p1 > 0.5 else 2
        correct += int(predicted_side == int(row["winner"]))
        brier += (p1 - outcome) ** 2
        log_loss -= outcome * math.log(max(p1, EPSILON)) + (1 - outcome) * math.log(
            max(1 - p1, EPSILON)
        )
    n = len(rows)
    bins: list[list[Mapping[str, Any]]] = [[] for _ in range(CALIBRATION_BINS)]
    for row in rows:
        p1 = float(row[p_key])
        index = min(CALIBRATION_BINS - 1, int(p1 * CALIBRATION_BINS))
        bins[index].append(row)
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        mean_p = sum(float(row[p_key]) for row in bucket) / len(bucket)
        observed = sum(1 for row in bucket if int(row["winner"]) == 1) / len(bucket)
        ece += (len(bucket) / n) * abs(mean_p - observed)
    slope = _logistic_slope(rows, p_key=p_key)
    agreement = sum(
        1 for row in rows
        if (float(row[p_key]) > 0.5) == (float(row.get("market_p1", row[p_key])) > 0.5)
    ) / n
    return {
        "n": n,
        "accuracy": correct / n,
        "brier": brier / n,
        "log_loss": log_loss / n,
        "ece": ece,
        "calibration_slope": slope,
        "favourite_agreement": agreement,
    }


def _logistic_slope(rows: Sequence[Mapping[str, Any]], *, p_key: str) -> float | None:
    """Fit ``logit(y) = a + b * logit(p)`` with Newton-Raphson."""
    if len(rows) < 30:
        return None
    beta = [0.0, 1.0]
    for _ in range(50):
        gradient = [0.0, 0.0]
        hessian = [[0.0, 0.0], [0.0, 0.0]]
        for row in rows:
            x = _logit(float(row[p_key]))
            y = 1.0 if int(row["winner"]) == 1 else 0.0
            eta = beta[0] + beta[1] * x
            p = _sigmoid(eta)
            weight = max(p * (1 - p), 1e-9)
            error = y - p
            gradient[0] += error
            gradient[1] += error * x
            hessian[0][0] += weight
            hessian[0][1] += weight * x
            hessian[1][0] += weight * x
            hessian[1][1] += weight * x * x
        det = hessian[0][0] * hessian[1][1] - hessian[0][1] * hessian[1][0]
        if abs(det) < 1e-12:
            return None
        delta0 = (hessian[1][1] * gradient[0] - hessian[0][1] * gradient[1]) / det
        delta1 = (-hessian[1][0] * gradient[0] + hessian[0][0] * gradient[1]) / det
        beta[0] += delta0
        beta[1] += delta1
        if abs(delta0) + abs(delta1) < 1e-10:
            break
    return beta[1]


def _quantile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def odds_distribution(quotes: Sequence[MarketQuotePair]) -> dict[str, Any]:
    original = [quote for quote in quotes if quote.is_original_time]
    favourites = [min(quote.player_1_price, quote.player_2_price) for quote in original]
    underdogs = [max(quote.player_1_price, quote.player_2_price) for quote in original]
    return {
        "n": len(original),
        "favourite_price": {
            "p10": _quantile(favourites, 0.1),
            "p50": _quantile(favourites, 0.5),
            "p90": _quantile(favourites, 0.9),
        },
        "underdog_price": {
            "p10": _quantile(underdogs, 0.1),
            "p50": _quantile(underdogs, 0.5),
            "p90": _quantile(underdogs, 0.9),
        },
    }


def _paired_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str,
    right: str,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Match-clustered percentile interval for Brier and accuracy differences.

    Positive Brier improvement means the left arm scored worse (higher Brier).
    Positive accuracy improvement means the left arm was more accurate.
    """
    if not rows:
        return {"brier_improvement_95": None, "accuracy_improvement_95": None, "n": 0}
    by_match: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_match.setdefault(int(row["match_id"]), []).append(row)
    clusters = list(by_match.values())
    rng = random.Random(seed)
    brier_diffs: list[float] = []
    accuracy_diffs: list[float] = []
    for _ in range(iterations):
        sample = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        flat = [row for cluster in sample for row in cluster]
        brier_left = sum(
            (float(row[left]) - (1 if int(row["winner"]) == 1 else 0)) ** 2
            for row in flat
        ) / len(flat)
        brier_right = sum(
            (float(row[right]) - (1 if int(row["winner"]) == 1 else 0)) ** 2
            for row in flat
        ) / len(flat)
        acc_left = sum(
            int((float(row[left]) > 0.5) == (int(row["winner"]) == 1))
            for row in flat
        ) / len(flat)
        acc_right = sum(
            int((float(row[right]) > 0.5) == (int(row["winner"]) == 1))
            for row in flat
        ) / len(flat)
        brier_diffs.append(brier_left - brier_right)
        accuracy_diffs.append(acc_left - acc_right)
    return {
        "n": len(rows),
        "match_clusters": len(clusters),
        "brier_improvement_95": [
            _quantile(brier_diffs, 0.025),
            _quantile(brier_diffs, 0.975),
        ],
        "accuracy_improvement_95": [
            _quantile(accuracy_diffs, 0.025),
            _quantile(accuracy_diffs, 0.975),
        ],
    }


def _common_rows(
    fixtures: Sequence[BenchmarkFixture],
    quotes_by_match: Mapping[int, Sequence[MarketQuotePair]],
    method: str,
    reference_bookmaker: str,
    provenance: str = "ORIGINAL_TIME",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        quotes = quotes_by_match.get(fixture.match_id, ())
        market = market_probability(
            quotes, method=method, reference_bookmaker=reference_bookmaker,
            provenance=provenance,
        )
        if market is None:
            continue
        rows.append({
            "match_id": fixture.match_id,
            "winner": fixture.winner,
            "p1": fixture.model_p1,
            "market_p1": market,
        })
    return rows


def selection_accuracy(rows: Sequence[Mapping[str, Any]], p_key: str) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "accuracy": None}
    correct = sum(
        1 for row in rows
        if (float(row[p_key]) > 0.5) == (int(row["winner"]) == 1)
    )
    return {"n": len(rows), "accuracy": correct / len(rows)}


def top_k_accuracy(rows: Sequence[Mapping[str, Any]], p_key: str, k: int) -> dict[str, Any]:
    """Rank by confidence ``abs(p-0.5)`` and score the top K at equal coverage."""
    ordered = sorted(rows, key=lambda row: (-abs(float(row[p_key]) - 0.5), row["match_id"]))
    chosen = ordered[:k]
    return selection_accuracy(chosen, p_key=p_key) | {"k": k}


def clv(entry_price: float, closing_price: float) -> dict[str, float]:
    """Closing-line value in probability and price terms."""
    if entry_price <= 1 or closing_price <= 1:
        raise ValueError("prices must be greater than 1")
    return {
        "entry_implied_probability": 1 / entry_price,
        "closing_implied_probability": 1 / closing_price,
        "probability_clv": (1 / entry_price) - (1 / closing_price),
        "price_clv_pct": (entry_price / closing_price - 1) * 100,
    }


def mcnemar_exact(b: int, c: int) -> dict[str, Any]:
    """Two-sided exact McNemar test for two paired binary selectors."""
    if b < 0 or c < 0:
        raise ValueError("discordant counts must be non-negative")
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "n": 0, "p_value": 1.0}
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / (2 ** n)
    return {"b": b, "c": c, "n": n, "p_value": min(1.0, 2 * tail)}


def benchmark_report(
    *,
    fixtures: Sequence[BenchmarkFixture],
    quotes_by_match: Mapping[int, Sequence[MarketQuotePair]],
    reference_bookmaker: str = "PINNACLE",
    include_retrospective: bool = True,
) -> dict[str, Any]:
    """Build the four-comparison report, refusing unsupported claims."""
    original_fixtures = [
        fixture for fixture in fixtures
        if any(
            quote.is_original_time
            for quote in quotes_by_match.get(fixture.match_id, ())
        )
    ]
    report: dict[str, Any] = {
        "version": BENCHMARK_VERSION,
        "fixtures": len(fixtures),
        "fixtures_with_original_time_quote": len(original_fixtures),
        "reference_bookmaker": reference_bookmaker,
        "provider_calls": 0,
        "comparisons": {},
        "market_definitions": {},
        "limitations": [
            "Only ORIGINAL_TIME quotes may support a market-edge claim.",
            "Late or backfilled quotes are descriptive retrospective evidence.",
            "Duplicate price pairs are not independent information.",
            "No profitability or market-superiority claim is made from retrospective quotes.",
        ],
    }

    methods = ("reference", "consensus", "consensus_deduplicated")
    common_by_method = {
        method: _common_rows(fixtures, quotes_by_match, method, reference_bookmaker)
        for method in methods
    }
    for method, rows in common_by_method.items():
        report["market_definitions"][method] = {
            "coverage": len(rows),
            "_metrics": _binary_metrics(rows) if rows else None,
        }

    eligible = common_by_method["consensus_deduplicated"]
    market_assisted_available = len(eligible) >= MIN_ORIGINAL_TIME_FIXTURES_FOR_ASSISTED

    # A. identical selected fixtures: model pick versus market favourite.
    a_rows = [
        row for row in eligible
        if (float(row["p1"]) > 0.5) != (float(row["market_p1"]) > 0.5)
    ]
    b = sum(
        1 for row in a_rows
        if (float(row["p1"]) > 0.5) == (int(row["winner"]) == 1)
        and (float(row["market_p1"]) > 0.5) != (int(row["winner"]) == 1)
    )
    c = sum(
        1 for row in a_rows
        if (float(row["market_p1"]) > 0.5) == (int(row["winner"]) == 1)
        and (float(row["p1"]) > 0.5) != (int(row["winner"]) == 1)
    )
    report["comparisons"]["A_identical_selected"] = {
        "definition": (
            "Fixtures where Agent 1 and the de-duplicated market consensus "
            "favour different players; both arms scored on the same fixtures."
        ),
        "fixtures": len(a_rows),
        "model": selection_accuracy(a_rows, "p1"),
        "market": selection_accuracy(a_rows, "market_p1"),
        "model_accuracy_when_disagreeing": selection_accuracy(a_rows, "p1")["accuracy"],
        "market_accuracy_when_disagreeing": selection_accuracy(a_rows, "market_p1")["accuracy"],
        "mcnemar_exact": mcnemar_exact(b, c),
        "available": bool(a_rows),
    }

    # B. all common eligible fixtures.
    report["comparisons"]["B_all_common"] = {
        "definition": "Every fixture with both an Agent 1 probability and original-time market data.",
        "available": bool(eligible),
        "coverage": len(eligible) / len(fixtures) if fixtures else None,
        "model": _binary_metrics(eligible) if eligible else None,
        "market": _binary_metrics(
            [{**row, "p1": row["market_p1"]} for row in eligible]
        ) if eligible else None,
        "paired": _paired_bootstrap(eligible, left="p1", right="market_p1")
        if eligible else None,
    }

    # C. independently ranked at equivalent coverage.
    if eligible:
        k = len(eligible)
        report["comparisons"]["C_independent_ranking"] = {
            "definition": (
                "Each arm ranks fixtures independently by its own confidence; "
                "both are scored at the same K."
            ),
            "model": top_k_accuracy(eligible, "p1", k),
            "market": top_k_accuracy(eligible, "market_p1", k),
            "k": k,
            "available": True,
        }
    else:
        report["comparisons"]["C_independent_ranking"] = {"available": False, "k": 0}

    # D. market-assisted candidate, only with a pre-registered evidence base.
    report["comparisons"]["D_market_assisted"] = {
        "definition": (
            "Equal-weight model/market probability on the common eligible set. "
            "This is a pre-registered candidate, not a fitted or promoted model."
        ),
        "available": market_assisted_available,
        "minimum_fixtures": MIN_ORIGINAL_TIME_FIXTURES_FOR_ASSISTED,
        "reason": (
            None if market_assisted_available
            else "INSUFFICIENT_ORIGINAL_TIME_EVIDENCE"
        ),
    }
    if market_assisted_available:
        assisted = [
            {**row, "assisted_p1": 0.5 * float(row["p1"]) + 0.5 * float(row["market_p1"])}
            for row in eligible
        ]
        report["comparisons"]["D_market_assisted"].update({
            "market": _binary_metrics([{**row, "p1": row["market_p1"]} for row in assisted]),
            "assisted": _binary_metrics(assisted, p_key="assisted_p1"),
            "paired": _paired_bootstrap(assisted, left="assisted_p1", right="market_p1"),
        })

    all_quotes = [
        quote for fixture in fixtures
        for quote in quotes_by_match.get(fixture.match_id, ())
    ]
    report["odds_distribution"] = odds_distribution(all_quotes)
    report["duplicate_pairs"] = duplicate_price_pair_accounting(all_quotes)
    report["original_time_gate"] = {
        "minimum_fixtures": MIN_ORIGINAL_TIME_FIXTURES,
        "met": len(original_fixtures) >= MIN_ORIGINAL_TIME_FIXTURES,
        "fixtures": len(original_fixtures),
    }

    if include_retrospective:
        retrospective_fixtures = [
            fixture for fixture in fixtures
            if not any(
                quote.is_original_time
                for quote in quotes_by_match.get(fixture.match_id, ())
            ) and any(
                quote.provenance in ("LATE", "BACKFILLED", "RETROSPECTIVE_ONLY")
                for quote in quotes_by_match.get(fixture.match_id, ())
            )
        ]
        retrospective_by_method = {
            method: _common_rows(
                retrospective_fixtures, quotes_by_match, method, reference_bookmaker,
                provenance="RETROSPECTIVE",
            )
            for method in methods
        }
        report["retrospective"] = {
            "fixtures": len(retrospective_fixtures),
            "reported_separately": True,
            "never_used_for_market_edge": True,
            "label": "LATE_OR_BACKFILLED_DESCRIPTIVE_ONLY",
            "market_definitions": {
                method: {
                    "coverage": len(rows),
                    "model": _binary_metrics(rows) if rows else None,
                    "market": _binary_metrics(
                        [{**row, "p1": row["market_p1"]} for row in rows]
                    ) if rows else None,
                    "paired_model_vs_market": _paired_bootstrap(
                        rows, left="p1", right="market_p1"
                    ) if rows else None,
                }
                for method, rows in retrospective_by_method.items()
            },
            "duplicate_pairs": duplicate_price_pair_accounting(
                [
                    quote for fixture in retrospective_fixtures
                    for quote in quotes_by_match.get(fixture.match_id, ())
                ],
                provenance="RETROSPECTIVE",
            ),
        }
    report["clv"] = {
        "available": False,
        "reason": "NO_ORIGINAL_TIME_ENTRY_AND_CLOSING_PAIR",
        "note": (
            "Closing-line value requires an original-time entry quote and a "
            "separately observed closing quote. Late quotes are not a substitute."
        ),
    }
    return report
