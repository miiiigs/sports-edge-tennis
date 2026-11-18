"""Rolling historical walk-forward evaluation for immutable Tennis Model Picks.

For EACH Manila match day, fit any calibration using only results that were
source-observed, internally observed AND ingested before that day's midnight.
The tested policies are predeclared and NEVER optimized against these test days.

This is retrospective evidence, NOT proof of prospective 73% accuracy.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from sports_edge_tennis.performance.calibration import brier, log_loss
from sports_edge_tennis.performance.v2_quality_research import (
    FrozenPick, MANILA, MIN_DAILY_GRADED, POLICIES, POLICY_VERSION,
    _a3_label, _calibrator, _confidence_label, _metrics, _segment,
    _spread_label, allows,
)

COUNT_KEYS = ("picks", "won", "lost", "void", "pending", "graded")

def reconcile_original_days(
    report: dict[str, Any], expected: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Fail closed if a replay differs from an independently frozen original ledger.

    Expected must cover every replay day and include PICKS/W/L/VOID/PENDING.
    This validates dataset identity, not the performance of any V2 gate.
    All inputs are daily aggregates, never player or match records.
    """
    observed = {
        fold["date"]: fold["policies"]["original"] for fold in report["folds"]
    }
    if set(expected) != set(observed):
        missing = sorted(set(observed) - set(expected))
        extra = sorted(set(expected) - set(observed))
        raise ValueError(
            f"original daily scorecard dates mismatch: missing={missing} extra={extra}"
        )
    for day in sorted(observed):
        row = expected[day]
        if not isinstance(row, dict):
            raise ValueError(f"invalid original scorecard row: {day}")
        for key in ("picks", "won", "lost", "void", "pending"):
            if key not in row or type(row[key]) is not int or row[key] < 0:
                raise ValueError(f"missing/invalid original {key}: {day}")
            if row[key] != observed[day][key]:
                raise ValueError(
                    f"original cohort mismatch: {day} {key} "
                    f"expected={row[key]} observed={observed[day][key]}"
                )
        if sum(row[k] for k in ("won", "lost", "void", "pending")) != row["picks"]:
            raise ValueError(f"inconsistent original daily accounting: {day}")
    return {
        "status": "RECONCILED_AGAINST_FROZEN_ORIGINAL_LEDGER",
        "days": len(observed),
        "original_picks": sum(v["picks"] for v in observed.values()),
        "original_graded": sum(v["graded"] for v in observed.values()),
    }



def acceptance_evidence(
    folds: list[dict[str, Any]],
    policy: str,
    *,
    current_day: date,
    coverage_floor: float = 0.45,
) -> dict[str, Any]:
    """Descriptive 73% daily evidence; never converts shadow into live acceptance.

    At least five graded, fully resolved matches per day; sufficient selection
    coverage versus the original population. No-pick days break consecutive
    runs. We report both the longest and the trailing consecutive streak.
    """
    if not 0 <= coverage_floor <= 1:
        raise ValueError("coverage_floor must be in [0,1]")
    statuses: list[dict[str, Any]] = []
    longest = running = 0
    for fold in folds:
        day = fold["date"]
        original = fold["policies"]["original"]
        candidate = fold["policies"][policy]
        original_picks = original["picks"]
        coverage = candidate["picks"] / original_picks if original_picks else None
        if date.fromisoformat(day) >= current_day:
            reason = "IN_PROGRESS"
        elif original_picks == 0:
            reason = "NO_ORIGINAL_PICKS"
        elif candidate["pending"] > 0:
            reason = "UNSETTLED"
        elif candidate["graded"] < MIN_DAILY_GRADED:
            reason = "INSUFFICIENT_GRADED"
        elif coverage is None or coverage < coverage_floor:
            reason = "LOW_COVERAGE"
        elif candidate["won"] * 100 < 73 * candidate["graded"]:
            reason = "BELOW_73"
        else:
            reason = "MET_73_RESEARCH_ONLY"
        passed = reason == "MET_73_RESEARCH_ONLY"
        running = running + 1 if passed else 0
        longest = max(longest, running)
        statuses.append({
            "date": day, "research_status": reason,
            "fully_settled": candidate["pending"] == 0,
            "coverage": coverage, "passes_proposed_gate": passed,
        })
    return {
        "research_only": True,
        "not_brian_official_acceptance": True,
        "proposed_min_graded": MIN_DAILY_GRADED,
        "proposed_coverage_floor": coverage_floor,
        "days_evaluated": len(folds),
        "days_passing_proposed_gate": sum(int(x["passes_proposed_gate"]) for x in statuses),
        "longest_consecutive_days": longest,
        "trailing_consecutive_days": running,
        "five_day_research_gate_met": running >= 5,
        "daily": statuses,
    }


def _known_at(pick: FrozenPick, cutoff: datetime) -> bool:
    """A known terminal result needs all three verified visibility timestamps."""
    return (
        pick.result in ("WON", "LOST", "VOID")
        and pick.result_as_of is not None
        and pick.result_source_observed_at is not None
        and pick.result_ingested_at is not None
        and pick.result_as_of <= cutoff
        and pick.result_source_observed_at <= cutoff
        and pick.result_ingested_at <= cutoff
    )


def _seen_result(pick: FrozenPick, cutoff: datetime) -> FrozenPick:
    if _known_at(pick, cutoff):
        return pick
    return replace(pick, result="PENDING")


def walk_forward(
    picks: Iterable[FrozenPick],
    *,
    start: date,
    end: date,
    evaluated_at: datetime,
) -> dict[str, Any]:
    """Test fixed policies day-by-day using strictly earlier training only.

    Returned metrics are aggregates and do not disclose player or match IDs.
    A revised policy motivated by a test-day loss cannot treat that same day
    as independent holdout. Keep this separate from a later frozen live shadow.
    """
    if start > end:
        raise ValueError("reversed date interval")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must include timezone")

    picks = tuple(picks)
    if len({p.match_id for p in picks}) != len(picks):
        raise ValueError("duplicate first-issued match ID")

    today_local = evaluated_at.astimezone(MANILA).date()
    if start > today_local:
        raise ValueError("cannot evaluate days beyond evaluated_at")
    if end > today_local:
        end = today_local

    # Reporting must never use results unavailable by the evaluation instant,
    # even if future outcomes exist in an export generated much later.
    visible = tuple(
        _seen_result(p, evaluated_at)
        for p in picks
        if p.picked_at <= evaluated_at
    )
    totals: dict[str, dict[str, int]] = {
        policy: {k: 0 for k in COUNT_KEYS} |
                {"avoided_losses": 0, "missed_winners": 0, "days_met_73": 0}
        for policy in POLICIES
    }
    folds = []
    d = start
    while d <= end:
        cutoff = datetime.combine(d, time.min, tzinfo=MANILA)
        # Match-date midnight can be LATER than the issuance of an early
        # original prediction. For every original pick, freeze calibration
        # before the earliest of its ISSUE TIME and its Manila match-day
        # midnight; never score with a future-informed fitted model.
        cohort = tuple(p for p in visible if p.day == d)
        cohort_metrics = _metrics(cohort)
        calibrators: dict[datetime, tuple[Any, tuple[FrozenPick, ...]]] = {}
        for original in cohort:
            fit_cutoff = min(cutoff, original.picked_at)
            if fit_cutoff not in calibrators:
                calibrators[fit_cutoff] = _calibrator(visible, fit_cutoff)
        def candidate_calibrator(p: FrozenPick):
            return calibrators[min(cutoff, p.picked_at)][0]

        report: dict[str, Any] = {}
        for policy in POLICIES:
            selected = tuple(p for p in cohort if allows(p, policy, candidate_calibrator(p)))
            excluded = tuple(p for p in cohort if not allows(p, policy, candidate_calibrator(p)))
            m = _metrics(selected)
            missed = sum(p.result == "WON" for p in excluded)
            avoided = sum(p.result == "LOST" for p in excluded)
            passes = (
                d < today_local and m["pending"] == 0
                and m["graded"] >= MIN_DAILY_GRADED
                and m["won"] * 100 >= 73 * m["graded"]
            )
            report[policy] = {
                **m, "coverage": len(selected) / len(cohort) if cohort else None,
                "missed_winners": missed, "avoided_losses": avoided,
                "passes_73_daily": passes,
                "insufficient_sample": m["graded"] < MIN_DAILY_GRADED,
            }
            for key in COUNT_KEYS:
                totals[policy][key] += m[key]
            totals[policy]["missed_winners"] += missed
            totals[policy]["avoided_losses"] += avoided
            totals[policy]["days_met_73"] += int(passes)

        scored = tuple(p for p in cohort if p.result in ("WON", "LOST"))
        actual = [int(p.result == "WON") for p in scored]
        calibrated = [
            (candidate_calibrator(p).predict(p.raw), int(p.result == "WON"))
            for p in scored if candidate_calibrator(p) is not None
        ]
        trained_sizes = [
            len(calibrators[min(cutoff, p.picked_at)][1]) for p in cohort
        ]
        folds.append({
            "date": d.isoformat(),
            "calibration_training": {
                "fit_cutoff_rule": "MIN(MATCH_DAY_MIDNIGHT,FIRST_ISSUED_AT)",
                "prior_visible_graded_min": min(trained_sizes) if trained_sizes else 0,
                "prior_visible_graded_max": max(trained_sizes) if trained_sizes else 0,
                "fitted_picks": sum(
                    candidate_calibrator(p) is not None for p in cohort
                ),
                "unfitted_picks": sum(
                    candidate_calibrator(p) is None for p in cohort
                ),
                "calibrator_available": (
                    bool(cohort) and all(
                        candidate_calibrator(p) is not None for p in cohort
                    )
                ),
            },
            "original_cohort": cohort_metrics,
            "diagnostics": {
                "pick_type": _segment(cohort, lambda p: p.kind),
                "agent_agreement": _segment(cohort, _spread_label),
                "a3_confirmation": _segment(cohort, _a3_label),
                "confidence_bucket": _segment(cohort, _confidence_label),
                "surface": _segment(cohort, lambda p: p.surface),
                "tier": _segment(cohort, lambda p: p.tier),
            },
            "daily_calibration": {
                "raw_brier": brier(list(zip((p.raw for p in scored), actual))),
                "calibrated_brier": brier(calibrated),
                "raw_log_loss": log_loss(list(zip((p.raw for p in scored), actual))),
                "calibrated_log_loss": log_loss(calibrated),
                "calibrated_graded": len(calibrated),
            },
            "policies": report,
        })
        d += timedelta(days=1)

    summaries: dict[str, Any] = {}
    for policy, counts in totals.items():
        n = counts["won"] + counts["lost"]
        original = totals["original"]["picks"]
        summaries[policy] = {
            **counts,
            "win_rate": counts["won"] / n if n else None,
            "coverage": counts["picks"] / original if original else None,
            "calendar_days_evaluated": len(folds),
        }

    return {
        "policy_version": POLICY_VERSION,
        "evaluation_mode": "RETROSPECTIVE_WALK_FORWARD",
        "not_prospective_validation": True,
        "live_selection_changed": False,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "evaluated_at": evaluated_at.isoformat(),
        "timezone": "Asia/Manila",
        "rule_selection": "PREDECLARED_FIXED_CANDIDATES_ONLY",
        "acceptance_evidence": {
            policy: acceptance_evidence(folds, policy, current_day=today_local)
            for policy in POLICIES
        },
        "folds": folds,
        "summary": summaries,
        "warning": (
            "Retrospective policy comparisons are exploratory when candidate "
            "rules were designed after observing these dates. Chronological "
            "training and verified point-in-time inputs prevent mechanical "
            "future-result leakage but do not undo research selection bias. "
            "Freeze a policy for genuinely new consecutive settled days."
        ),
    }
