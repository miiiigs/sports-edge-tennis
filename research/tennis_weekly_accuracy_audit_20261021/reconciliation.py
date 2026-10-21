"""Reconciliation and experiment tables for the weekly Manager audit."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from .dataset import (
    CONFLICTED,
    LOST,
    MANAGER_CONSENSUS,
    PENDING,
    VALUE_BET,
    VETOED,
    VOID,
    WON,
    AlignedPick,
    Dataset,
    Pick,
    aligned_picks,
    in_window,
    local_date,
    manager_primary,
)
from .filters import PREDECLARED_FILTERS
from .metrics import (
    AccuracyMetrics,
    accuracy_metrics,
    evaluate_filter,
    grouped_metrics,
    probability_bands,
)

DEFAULT_MINIMUM_PROBABILITY = 0.60
DEFAULT_HOLDOUT_START = date(2026, 10, 13)


@dataclass(frozen=True)
class PopulationSummary:
    label: str
    picks: int
    scored: int
    wins: int
    losses: int
    pending: int
    voids: int
    conflicted: int
    metrics: AccuracyMetrics

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "picks": self.picks,
            "scored": self.scored,
            "wins": self.wins,
            "losses": self.losses,
            "pending": self.pending,
            "voids": self.voids,
            "conflicted": self.conflicted,
            **{
                key: value
                for key, value in self.metrics.to_dict().items()
                if key not in {"n", "wins", "losses"}
            },
        }


def summarize(label: str, picks: Iterable[Pick]) -> PopulationSummary:
    rows = tuple(picks)
    metrics = accuracy_metrics(rows)
    statuses = [pick.result_status for pick in rows]
    return PopulationSummary(
        label=label,
        picks=len(rows),
        scored=metrics.n,
        wins=metrics.wins,
        losses=metrics.losses,
        pending=statuses.count(PENDING),
        voids=statuses.count(VOID),
        conflicted=statuses.count(CONFLICTED),
        metrics=metrics,
    )


def summarize_aligned(label: str, picks: Sequence[AlignedPick]) -> PopulationSummary:
    return summarize(label, (item.pick for item in picks))


def best_case_accuracy(summary: PopulationSummary) -> float | None:
    denominator = summary.scored + summary.pending
    if denominator == 0:
        return None
    return (summary.wins + summary.pending) / denominator


def worst_case_accuracy(summary: PopulationSummary) -> float | None:
    denominator = summary.scored + summary.pending
    if denominator == 0:
        return None
    return summary.wins / denominator


def rank_by_risk(picks: Sequence[Pick]) -> list[Pick]:
    """Largest unverified model-minus-market gap first, then probability."""

    return sorted(
        picks,
        key=lambda pick: (
            -(pick.model_minus_sharp if pick.model_minus_sharp is not None else -1.0),
            -pick.probability,
        ),
    )


def week_ledger(picks: Sequence[Pick], timezone_name: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for pick in sorted(picks, key=lambda item: (item.match_start_at, item.match_id)):
        rows.append(
            {
                "match_id": pick.match_id,
                "local_date": local_date(pick, timezone_name).isoformat(),
                "population": pick.population,
                "tournament": pick.tournament,
                "tier": pick.tier,
                "surface": pick.surface,
                "round": pick.round_code,
                "selection": pick.picked_player_name,
                "probability": round(pick.probability, 6),
                "picked_at": pick.picked_at.isoformat(),
                "match_start_at": pick.match_start_at.isoformat()
                if pick.match_start_at
                else None,
                "model_minus_sharp": pick.model_minus_sharp,
                "sharp_picked_probability": pick.sharp_picked_probability,
                "probability_spread": pick.probability_spread,
                "qualification_mismatch": pick.qualification_mismatch,
                "result": pick.result_status,
                "winner_side": pick.winner_side,
                "is_value_bet": pick.value_bet.is_value_bet,
                "is_vetoed": pick.value_bet.is_vetoed,
            }
        )
    return rows


def flat_stake_roi(picks: Sequence[Pick]) -> dict[str, object]:
    """One unit per settled bet at the recorded issued price."""

    settled = [
        pick
        for pick in picks
        if pick.result_status in {WON, LOST} and pick.value_bet.price is not None
    ]
    profit = 0.0
    for pick in settled:
        price = float(pick.value_bet.price or 0.0)
        profit += (price - 1.0) if pick.won else -1.0
    return {
        "bets": len(settled),
        "profit_units": round(profit, 4),
        "roi": round(profit / len(settled), 6) if settled else None,
    }


def _value_bet_slice(
    value_bets: Sequence[Pick], *, label: str, predicate: Callable[[Pick], bool]
) -> dict[str, object]:
    subset = [pick for pick in value_bets if predicate(pick)]
    metrics = accuracy_metrics(subset)
    return {
        "slice": label,
        "picks": len(subset),
        "scored": metrics.n,
        "wins": metrics.wins,
        "accuracy": metrics.accuracy,
        "mean_probability": metrics.mean_probability,
        **flat_stake_roi(subset),
    }


def value_bet_slices(value_bets: Sequence[Pick]) -> list[dict[str, object]]:
    """Value Bet outcomes by claimed edge and by issued price.

    These slices diagnose adverse selection; they are not Manager accuracy
    filters and never enter the Manager denominator.
    """

    conditions = (
        ("edge <= 20pp", lambda pick: (pick.value_bet.edge_pct or 0.0) <= 0.20),
        ("edge 20-35pp", lambda pick: 0.20 < (pick.value_bet.edge_pct or 0.0) <= 0.35),
        ("edge 35-60pp", lambda pick: 0.35 < (pick.value_bet.edge_pct or 0.0) <= 0.60),
        ("edge > 60pp", lambda pick: (pick.value_bet.edge_pct or 0.0) > 0.60),
        ("issued price < 2.00", lambda pick: (pick.value_bet.price or 0.0) < 2.00),
        ("issued price >= 2.00", lambda pick: (pick.value_bet.price or 0.0) >= 2.00),
    )
    return [
        _value_bet_slice(value_bets, label=label, predicate=predicate)
        for label, predicate in conditions
    ]


def value_bet_reconciliation(value_bets: Sequence[Pick]) -> dict[str, object]:
    published = [pick for pick in value_bets if pick.value_bet.published_at is not None]
    settlement_counts = {status: 0 for status in (WON, LOST, VOID, PENDING, CONFLICTED)}
    for pick in value_bets:
        settlement_counts[pick.result_status] += 1
    return {
        "label": "Actual Value Bets",
        "picks": len(value_bets),
        "scored": sum(1 for pick in value_bets if pick.scored),
        "wins": sum(1 for pick in value_bets if pick.won),
        "settlement": settlement_counts,
        "accuracy": (
            sum(1 for pick in value_bets if pick.won)
            / sum(1 for pick in value_bets if pick.scored)
            if any(pick.scored for pick in value_bets)
            else None
        ),
        "mean_probability": (
            sum(pick.probability for pick in value_bets if pick.scored)
            / sum(1 for pick in value_bets if pick.scored)
            if any(pick.scored for pick in value_bets)
            else None
        ),
        "published": {
            "picks": len(published),
            "scored": sum(1 for pick in published if pick.scored),
            "wins": sum(1 for pick in published if pick.won),
        },
        "flat_stake": flat_stake_roi(value_bets),
        "published_flat_stake": flat_stake_roi(published),
    }


def experiment_table(
    primary: Sequence[Pick],
    *,
    window: tuple[date, date],
    timezone_name: str,
    holdout_start: date = DEFAULT_HOLDOUT_START,
) -> dict[str, object]:
    week = [
        pick
        for pick in primary
        if window[0] <= local_date(pick, timezone_name) <= window[1]
    ]
    train = [
        pick for pick in primary if local_date(pick, timezone_name) < holdout_start
    ]
    holdout = [
        pick for pick in primary if local_date(pick, timezone_name) >= holdout_start
    ]
    rows: list[dict[str, object]] = []
    for selection_filter in PREDECLARED_FILTERS:
        full_effect = evaluate_filter(
            primary,
            name=selection_filter.name,
            description=selection_filter.description,
            rejects=selection_filter.rejects,
        )
        train_effect = evaluate_filter(
            train,
            name=selection_filter.name,
            description=selection_filter.description,
            rejects=selection_filter.rejects,
        )
        holdout_effect = evaluate_filter(
            holdout,
            name=selection_filter.name,
            description=selection_filter.description,
            rejects=selection_filter.rejects,
        )
        week_effect = evaluate_filter(
            week,
            name=selection_filter.name,
            description=selection_filter.description,
            rejects=selection_filter.rejects,
        )
        rows.append(
            {
                "filter": selection_filter.name,
                "description": selection_filter.description,
                "full": full_effect.to_dict(),
                "train": train_effect.to_dict(),
                "holdout": holdout_effect.to_dict(),
                "week": week_effect.to_dict(),
            }
        )
    return {
        "holdout_start": holdout_start.isoformat(),
        "week": {"start": window[0].isoformat(), "end": window[1].isoformat()},
        "base": {
            "full": accuracy_metrics(primary).to_dict(),
            "train": accuracy_metrics(train).to_dict(),
            "holdout": accuracy_metrics(holdout).to_dict(),
            "week": accuracy_metrics(week).to_dict(),
        },
        "filters": rows,
    }


def subgroup_table(primary: Sequence[Pick]) -> dict[str, object]:
    return {
        "bands": probability_bands(primary),
        "surface": grouped_metrics(primary, lambda pick: pick.surface or "MISSING"),
        "tier": grouped_metrics(primary, lambda pick: pick.tier or "MISSING"),
        "market_disagreement": grouped_metrics(
            primary, lambda pick: _market_bucket(pick.model_minus_sharp)
        ),
        "qualification": grouped_metrics(
            primary,
            lambda pick: "MISMATCH" if pick.qualification_mismatch else "COHERENT",
        ),
        "sharp_underdog": grouped_metrics(
            primary,
            lambda pick: (
                "NO_SHARP"
                if pick.sharp_picked_probability is None
                else (
                    "SHARP_FAVOURITE"
                    if pick.sharp_picked_probability >= 0.50
                    else "SHARP_UNDERDOG"
                )
            ),
        ),
    }


def _market_bucket(value: float | None) -> str:
    if value is None:
        return "NO_PRE_PICK_SHARP"
    if value > 0.20:
        return "MODEL_OVER_SHARP_GT_20PP"
    if value > 0.10:
        return "MODEL_OVER_SHARP_10_20PP"
    if value >= -0.10:
        return "AGREEMENT_WITHIN_10PP"
    return "MODEL_BELOW_SHARP_GT_10PP"


def reconcile_week(
    dataset: Dataset,
    *,
    start: date,
    end: date,
    timezone_name: str,
    minimum_probability: float = DEFAULT_MINIMUM_PROBABILITY,
    holdout_start: date = DEFAULT_HOLDOUT_START,
) -> dict[str, object]:
    manager_rows = dataset.by_population(MANAGER_CONSENSUS)
    manager_window = in_window(manager_rows, start, end, timezone_name)
    primary = tuple(
        pick for pick in manager_window if pick.probability >= minimum_probability
    )
    primary_all = manager_primary(dataset, minimum_probability=minimum_probability)
    aligned_window = aligned_picks(
        in_window(
            dataset.picks,
            start,
            end,
            timezone_name,
        ),
        minimum_probability=minimum_probability,
    )
    value_bets = in_window(dataset.by_population(VALUE_BET), start, end, timezone_name)
    vetoed = in_window(dataset.by_population(VETOED), start, end, timezone_name)

    manager_summary = summarize("Manager Model Picks (first-qualified, >=60%)", primary)
    manager_all_summary = summarize(
        "Manager first evaluations (all probabilities)", manager_window
    )
    manager_tracking = summarize(
        "A1/A2 wide-spread tracking (all-time context)",
        dataset.by_population("wide_spread_tracking"),
    )
    aligned_summary = summarize_aligned(
        "Aligned Model Picks (dashboard headline)", aligned_window
    )
    aligned_manager = summarize_aligned(
        "Aligned picks issued as Manager CONSENSUS",
        [item for item in aligned_window if item.pick_type == "MANAGER_CONSENSUS"],
    )
    aligned_tracking = summarize_aligned(
        "Aligned picks issued as A1/A2 tracking",
        [item for item in aligned_window if item.pick_type == "A1_A2_WIDE_SPREAD"],
    )
    vb_recon = value_bet_reconciliation(value_bets)
    vb_slices = value_bet_slices(dataset.by_population(VALUE_BET))
    veto_summary = summarize("Market Veto-blocked", vetoed)

    week_effects = experiment_table(
        primary_all,
        window=(start, end),
        timezone_name=timezone_name,
        holdout_start=holdout_start,
    )
    return {
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone": timezone_name,
            "minimum_probability": minimum_probability,
            "holdout_start": holdout_start.isoformat(),
        },
        "dataset": {
            "sha256": dataset.sha256,
            "rows": len(dataset.picks),
            "populations": dataset.populations,
            "manifest": {
                key: dataset.manifest.get(key)
                for key in (
                    "extract_cutoff",
                    "extracted_at",
                    "row_count",
                    "row_sha256",
                    "dataset_schema_version",
                )
            },
        },
        "populations": {
            "manager_primary": manager_summary.to_dict(),
            "manager_all_probabilities": manager_all_summary.to_dict(),
            "wide_spread_tracking_all_time": manager_tracking.to_dict(),
            "aligned": aligned_summary.to_dict(),
            "aligned_manager_subset": aligned_manager.to_dict(),
            "aligned_tracking_subset": aligned_tracking.to_dict(),
            "value_bets": vb_recon,
            "value_bet_slices": vb_slices,
            "vetoed": veto_summary.to_dict(),
        },
        "best_case": {
            "manager_primary": best_case_accuracy(manager_summary),
            "aligned": best_case_accuracy(aligned_summary),
            "worst_case_manager_primary": worst_case_accuracy(manager_summary),
            "worst_case_aligned": worst_case_accuracy(aligned_summary),
        },
        "manager_week_ledger": week_ledger(primary, timezone_name),
        "aligned_week_ledger": week_ledger(
            [item.pick for item in aligned_window], timezone_name
        ),
        "subgroups": subgroup_table(primary_all),
        "experiments": week_effects,
    }


__all__ = [
    "DEFAULT_HOLDOUT_START",
    "DEFAULT_MINIMUM_PROBABILITY",
    "PopulationSummary",
    "best_case_accuracy",
    "experiment_table",
    "flat_stake_roi",
    "rank_by_risk",
    "reconcile_week",
    "subgroup_table",
    "summarize",
    "summarize_aligned",
    "value_bet_reconciliation",
    "value_bet_slices",
    "week_ledger",
    "worst_case_accuracy",
]
