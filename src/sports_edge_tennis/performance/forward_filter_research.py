"""Pre-registered forward-only Model Pick filter monitoring (research, never selection).

Enrollment begins 2026-10-18 UTC, after this hypothesis was registered. Only
real persisted first-qualifying production picks enter the report. Filter inputs
are the pre-start snapshot stored alongside each original pick, never match-end
values. No thresholds, model parameters, predictions, or stakes are changed.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from sports_edge_tennis.performance.calibration_monitor import (
    PickObservation,
    hypothetical_filter,
)

FORWARD_START_UTC = date(2026, 10, 18)
MIN_BASELINE_SETTLED = 150
MIN_FILTER_REMOVED_SETTLED = 30

_FILTERS: tuple[tuple[str, dict[str, bool]], ...] = (
    ("baseline", {}),
    ("exclude_unknown_surface_wide", {"exclude_unknown_surface_wide": True}),
    ("exclude_high_divergence_wide", {"exclude_high_divergence_wide": True}),
    (
        "exclude_both",
        {"exclude_unknown_surface_wide": True, "exclude_high_divergence_wide": True},
    ),
)


def build_forward_filter_report(
    observations: Sequence[PickObservation], *, report_date_utc: date
) -> dict[str, Any]:
    """Compare fixed shadow policies over newly issued predictions, with no writes.

    Returned figures are descriptive and never authorize automatic promotion.
    Per-date end cutoff is for reproducibility only; only run on the current UTC
    date, because latest terminal results must not leak into past-as-of reports.
    """
    if report_date_utc < FORWARD_START_UTC:
        return {
            "forward_start_utc": FORWARD_START_UTC.isoformat(),
            "report_date_utc": report_date_utc.isoformat(),
            "status": "NOT_STARTED",
            "enrolled_picks": 0,
            "variants": {},
        }

    cohort = [
        obs
        for obs in observations
        if FORWARD_START_UTC <= obs.prediction_date <= report_date_utc
    ]
    ids = [obs.match_id for obs in cohort]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate sporting matches in pre-registered forward cohort")

    variants: dict[str, dict[str, Any]] = {}
    baseline_settled = sum(obs.outcome is not None for obs in cohort)
    for name, params in _FILTERS:
        result = hypothetical_filter(list(cohort), **params)
        removed_settled = result["removed_wins"] + result["removed_losses"]
        retained_settled = result["retained_wins"] + result["retained_losses"]
        variants[name] = {
            "enrolled": result["retained_picks"],
            "settled": retained_settled,
            "wins": result["retained_wins"],
            "losses": result["retained_losses"],
            "accuracy": result["retained_hit_rate"],
            "removed": result["removed_picks"],
            "removed_settled": removed_settled,
            "removed_wins": result["removed_wins"],
            "removed_losses": result["removed_losses"],
            "coverage_reduction": result["coverage_reduction"],
            "research_status": (
                "BASELINE"
                if name == "baseline"
                else (
                    "INSUFFICIENT_EVIDENCE"
                    if baseline_settled < MIN_BASELINE_SETTLED
                    or removed_settled < MIN_FILTER_REMOVED_SETTLED
                    else "ELIGIBLE_FOR_MANUAL_REVIEW_ONLY"
                )
            ),
        }

    return {
        "forward_start_utc": FORWARD_START_UTC.isoformat(),
        "report_date_utc": report_date_utc.isoformat(),
        "status": "RESEARCH_ONLY",
        "enrolled_picks": len(cohort),
        "settled_picks": baseline_settled,
        "baseline_accuracy": variants["baseline"]["accuracy"],
        "variants": variants,
        "notes": [
            "Pre-registered policies apply only to future-issued production picks.",
            "No automatic filter activation; compare with coverage and calibration before review.",
            "A market-alignment test needs historical point-in-time Pinnacle pairs and is not included.",
            "This is not evidence of profit or prospective improvement before sufficient outcomes.",
        ],
    }


__all__ = ["FORWARD_START_UTC", "build_forward_filter_report"]
