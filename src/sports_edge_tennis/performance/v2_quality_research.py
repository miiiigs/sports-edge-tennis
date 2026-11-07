"""Precommitted Tennis V2 quality-gate research on immutable original Model Picks.

No production imports and no writes. All model features are original pick-time
observations; scored outcomes are used only for measurement or for strictly prior
calibrator training. The holdout is NEVER used to choose a gate. The calibration
and gate are NOT approved to publish live picks.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sports_edge_tennis.performance.calibration import (
    Sample, brier, expected_calibration_error, fit_platt, log_loss,
)

MANILA = ZoneInfo("Asia/Manila")
POLICY_VERSION = "TENNIS_V2_RESEARCH_20261107"
KNOWN_SURFACES = frozenset({"HARD", "CLAY", "GRASS", "CARPET"})
KNOWN_TIERS = frozenset({
    "GRAND_SLAM", "MASTERS_1000", "ATP_500", "ATP_250", "ATP_TOUR", "CHALLENGER",
})
PICK_TYPES = frozenset({"MANAGER_CONSENSUS", "A1_A2_WIDE_SPREAD"})
OUTCOMES = frozenset({"WON", "LOST", "VOID", "PENDING"})
POLICIES = ("original", "confidence70", "v2_agreement", "v2_tier_guard", "v2_calibrated")
CALIBRATION_MIN_GRADED = 200
MIN_DAILY_GRADED = 5


@dataclass(frozen=True)
class FrozenPick:
    match_id: int
    day: date
    picked_at: datetime
    kind: str
    side: int
    raw: float
    surface: str
    tier: str
    a1_side: int | None
    a2_side: int | None
    a1: float | None
    a2: float | None
    a3_status: str | None
    result: str
    result_as_of: datetime | None
    result_source_observed_at: datetime | None

    def agent_spread(self) -> float | None:
        return abs(self.a1 - self.a2) if self.a1 is not None and self.a2 is not None else None

    def agent_same_side(self) -> bool:
        return (self.side == self.a1_side == self.a2_side and self.agent_spread() is not None)


def _datetime(value: Any, *, required: bool = False) -> datetime | None:
    if value is None or value == "":
        if required:
            raise ValueError("missing required timestamp")
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed


def _probability(value: Any, *, required: bool = False) -> float | None:
    if value is None or value == "":
        if required:
            raise ValueError("missing original probability")
        return None
    v = float(value)
    if not isfinite(v) or not 0 <= v <= 1:
        raise ValueError("probability must be finite and in [0,1]")
    return v


def _side(value: Any) -> int | None:
    if value is None or value == "":
        return None
    v = int(value)
    if v not in (1, 2):
        raise ValueError("side must be 1 or 2")
    return v


def parse_export(payload: dict[str, Any]) -> tuple[FrozenPick, ...]:
    """Validate the read-only ops/calibration_dataset.sql JSON export."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("expected a JSON object with data list")
    seen: set[int] = set()
    out: list[FrozenPick] = []
    for row in payload["data"]:
        mid = int(row["match_id"])
        if mid in seen:
            raise ValueError("duplicate original sporting match")
        seen.add(mid)
        kind = str(row["aligned_pick_type"])
        result = str(row["result_status"])
        if kind not in PICK_TYPES or result not in OUTCOMES:
            raise ValueError("unsupported pick type or outcome")
        side = _side(row["selected_side"])
        if side is None:
            raise ValueError("missing original selected side")
        raw = _probability(row["aligned_probability"], required=True)
        picked_at = _datetime(row["picked_at"], required=True)
        day = date.fromisoformat(str(row["manila_date"]))
        if day > picked_at.astimezone(MANILA).date() + timedelta(days=365):
            raise ValueError("suspicious future match date; verify original schedule")
        # Empty/unknown features must abstain from quality gates, not be inferred.
        out.append(FrozenPick(
            match_id=mid, day=day, picked_at=picked_at, kind=kind, side=side,
            raw=raw, surface=str(row.get("surface") or "UNKNOWN").upper(),
            tier=str(row.get("tier") or "UNKNOWN").upper(),
            a1_side=_side(row.get("a1_side")), a2_side=_side(row.get("a2_side")),
            a1=_probability(row.get("a1_prob")), a2=_probability(row.get("a2_prob")),
            a3_status=(str(row["a3_status"]) if row.get("a3_status") is not None else None),
            result=result,
            result_as_of=_datetime(row.get("result_observed_at")),
            result_source_observed_at=_datetime(row.get("result_source_observed_at")),
        ))
    return tuple(sorted(out, key=lambda p: (p.day, p.picked_at, p.match_id)))


def _eligible_before(pick: FrozenPick, cutoff: datetime) -> bool:
    """A terminal result can train only if BOTH timestamps precede the holdout."""
    return (
        pick.day < cutoff.astimezone(MANILA).date()
        and pick.picked_at < cutoff
        and pick.result in {"WON", "LOST"}
        and pick.result_as_of is not None
        and pick.result_source_observed_at is not None
        and pick.result_as_of <= cutoff
        and pick.result_source_observed_at <= cutoff
    )


def _calibrator(picks: Iterable[FrozenPick], cutoff: datetime):
    training = tuple(p for p in picks if _eligible_before(p, cutoff))
    sample = [Sample(p.raw, int(p.result == "WON")) for p in training]
    losses = sum(s.outcome == 0 for s in sample)
    wins = len(sample) - losses
    # Reject small or one-sided data and pathological inverse calibration.
    if len(sample) < CALIBRATION_MIN_GRADED or min(wins, losses) < 20:
        return None, training
    model = fit_platt(sample)
    if model is None or not isfinite(model.intercept) or not isfinite(model.slope) or model.slope <= 0:
        return None, training
    return model, training


def allows(p: FrozenPick, policy: str, calibrator=None) -> bool:
    if policy == "original":
        return True
    if policy == "confidence70":
        return p.raw >= 0.70
    if policy not in POLICIES:
        raise ValueError("unknown V2 research policy")
    if p.surface not in KNOWN_SURFACES or p.tier not in KNOWN_TIERS:
        return False
    if not p.agent_same_side() or p.agent_spread() >= 0.10:
        return False
    if policy == "v2_agreement":
        return p.raw >= 0.70
    if policy == "v2_tier_guard":
        return p.raw >= (0.76 if p.tier == "CHALLENGER" else 0.70)
    if calibrator is None:
        return False
    return calibrator.predict(p.raw) >= 0.73


def _metrics(rows: Iterable[FrozenPick]) -> dict[str, Any]:
    group = tuple(rows)
    scored = tuple(p for p in group if p.result in ("WON", "LOST"))
    won = sum(p.result == "WON" for p in scored)
    lost = len(scored) - won
    pairs = [(p.raw, int(p.result == "WON")) for p in scored]
    return {
        "picks": len(group), "won": won, "lost": lost,
        "void": sum(p.result == "VOID" for p in group),
        "pending": sum(p.result == "PENDING" for p in group),
        "graded": len(scored), "win_rate": won / len(scored) if scored else None,
        "average_confidence": sum(p.raw for p in scored) / len(scored) if scored else None,
        "brier": brier(pairs), "log_loss": log_loss(pairs),
        "calibration_error": expected_calibration_error(pairs),
    }


def _segment(rows: tuple[FrozenPick, ...], fn) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[FrozenPick]] = defaultdict(list)
    for p in rows:
        buckets[fn(p)].append(p)
    return {name: _metrics(tuple(values)) for name, values in sorted(buckets.items())}


def _spread_label(p: FrozenPick) -> str:
    gap = p.agent_spread()
    if not p.agent_same_side():
        return "missing_or_disagreeing_side"
    if gap < .025:
        return "same_side_0_2.5pt"
    if gap < .05:
        return "same_side_2.5_5pt"
    if gap < .10:
        return "same_side_5_10pt"
    return "same_side_10pt_plus"


def _confidence_label(p: FrozenPick) -> str:
    v = p.raw
    if v < .70:
        return "below_70"
    if v < .80:
        return "70_80"
    if v < .90:
        return "80_90"
    return "90_plus"


def audit(
    picks: tuple[FrozenPick, ...],
    *,
    start: date,
    end: date,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Freeze all calibration on outcomes visible strictly before start."""
    if start > end:
        raise ValueError("reversed holdout")
    as_of = as_of or datetime.now(MANILA).date()
    cutoff = datetime.combine(start, time.min, tzinfo=MANILA)
    calibrator, training = _calibrator(picks, cutoff)
    holdout = tuple(p for p in picks if start <= p.day <= end)
    if len({p.match_id for p in picks}) != len(picks):
        raise ValueError("duplicate match")
    all_results: dict[str, Any] = {}
    for policy in POLICIES:
        picked = tuple(p for p in holdout if allows(p, policy, calibrator))
        omitted = tuple(p for p in holdout if not allows(p, policy, calibrator))
        m = _metrics(picked)
        daily = {}
        day = start
        while day <= end:
            day_picks = tuple(p for p in picked if p.day == day)
            d = _metrics(day_picks)
            # A zero-pick or underpowered day is not a 73% success.
            d["passes_73_daily"] = (
                day < as_of
                and d["pending"] == 0 and d["graded"] >= MIN_DAILY_GRADED
                and d["won"] * 100 >= 73 * d["graded"]
            )
            daily[day.isoformat()] = d
            day += timedelta(days=1)
        all_results[policy] = {
            **m, "coverage": len(picked) / len(holdout) if holdout else None,
            "avoided_losses": sum(p.result == "LOST" for p in omitted),
            "missed_winners": sum(p.result == "WON" for p in omitted),
            "daily": daily,
        }
    training_m = _metrics(training)
    holdout_m = _metrics(holdout)
    scored = tuple(p for p in holdout if p.result in {"WON", "LOST"})
    original_pairs = [(p.raw, int(p.result == "WON")) for p in scored]
    calibrated_pairs = (
        [(calibrator.predict(p.raw), int(p.result == "WON")) for p in scored]
        if calibrator is not None else []
    )
    return {
        "policy_version": POLICY_VERSION,
        "status": "RESEARCH_ONLY_NOT_DEPLOYED",
        "source": "original_first_issued_PIT_aligned_model_picks",
        "holdout": {"start": start.isoformat(), "end": end.isoformat(), "cutoff": cutoff.isoformat(), "as_of": as_of.isoformat()},
        "training": {
            **training_m, "earliest_results_only": True,
            "calibrator_eligible": calibrator is not None,
            "platt_slope": calibrator.slope if calibrator else None,
            "platt_intercept": calibrator.intercept if calibrator else None,
        },
        "holdout_calibration": {
            "raw_brier": brier(original_pairs),
            "raw_log_loss": log_loss(original_pairs),
            "calibrated_brier": brier(calibrated_pairs),
            "calibrated_log_loss": log_loss(calibrated_pairs),
        },
        "overall": holdout_m,
        "diagnostics": {
            "pick_type": _segment(holdout, lambda p: p.kind),
            "surface": _segment(holdout, lambda p: p.surface),
            "competition_tier": _segment(holdout, lambda p: p.tier),
            "confidence_bucket": _segment(holdout, _confidence_label),
            "agent_agreement": _segment(holdout, _spread_label),
        },
        "policies": all_results,
        "limitations": (
            "No live selection is changed. Holdout outcomes never choose gates "
            "or train calibration. No 73 percent guarantee. Daily graded N, "
            "coverage and missed winners must accompany any win rate."
        ),
    }
