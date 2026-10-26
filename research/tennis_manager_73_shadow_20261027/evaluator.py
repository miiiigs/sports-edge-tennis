"""Research-only, point-in-time Manager confidence threshold validation.

Uses the existing calibration-ledger export shape. Never publishes picks,
mutates results, changes policies, or treats a historical threshold as a
guaranteed live win rate.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


CANDIDATES = (0.65, 0.70, 0.75, 0.80)
MIN_VALIDATION_SCORED = 50
MIN_HOLDOUT_SCORED = 50
MIN_RETENTION = 0.70
MIN_VALIDATION_UPLIFT = 0.03


@dataclass(frozen=True)
class Pick:
    match_id: int
    day: date
    confidence: float
    result: str
    regime: str


def parse_ledger(raw: dict) -> list[Pick]:
    """Fail closed on missing source lineage, invalid probabilities or duplicates."""
    records = raw.get("data")
    if not isinstance(records, list):
        raise ValueError("ledger.data must be a list")
    picks = []
    seen: set[int] = set()
    for record in records:
        if record.get("aligned_pick_type") != "MANAGER_CONSENSUS":
            continue
        match_id = int(record["match_id"])
        if match_id in seen:
            raise ValueError(f"duplicate Manager sporting match id {match_id}")
        seen.add(match_id)
        day = date.fromisoformat(record["manila_date"])
        prob = float(record["aligned_probability"])
        if not 0 <= prob <= 1:
            raise ValueError(f"invalid probability for {match_id}")
        result = str(record["result_status"])
        if result not in {"WON", "LOST", "VOID", "PENDING"}:
            raise ValueError(f"unknown settlement state {result}")
        regime = str(record.get("model_regime_key") or "")
        picks.append(Pick(match_id, day, prob, result, regime))
    return sorted(picks, key=lambda x: (x.day, x.match_id))


def _stats(picks: list[Pick], cutoff: float) -> dict:
    included = [x for x in picks if x.confidence >= cutoff]
    wins = sum(x.result == "WON" for x in included)
    losses = sum(x.result == "LOST" for x in included)
    pending = sum(x.result == "PENDING" for x in included)
    voids = sum(x.result == "VOID" for x in included)
    scored = wins + losses
    return {
        "picked": len(included),
        "scored": scored,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "voids": voids,
        "win_rate": wins / scored if scored else None,
    }


def _streak(picks: list[Pick], cutoff: float) -> dict:
    """Calendar days with zero selections also break a 73% daily streak."""
    if not picks:
        return {"longest": 0, "last_five_all_met": False}
    by_day: dict[date, list[Pick]] = {}
    for item in picks:
        if item.confidence >= cutoff:
            by_day.setdefault(item.day, []).append(item)
    first = min(x.day for x in picks)
    last = max(x.day for x in picks)
    longest = current = 0
    day = first
    statuses: dict[date, bool] = {}
    while day <= last:
        rows = by_day.get(day, [])
        wins = sum(x.result == "WON" for x in rows)
        losses = sum(x.result == "LOST" for x in rows)
        pending = sum(x.result == "PENDING" for x in rows)
        qualified = bool(
            wins + losses and not pending and 100 * wins >= 73 * (wins + losses)
        )
        statuses[day] = qualified
        current = current + 1 if qualified else 0
        longest = max(longest, current)
        day += timedelta(days=1)
    last_five = [last - timedelta(days=d) for d in range(5)]
    return {
        "longest": longest,
        "last_five_all_met": all(statuses.get(day, False) for day in last_five),
    }


def _period(rows: list[Pick], cutoff: float) -> dict:
    metrics = _stats(rows, cutoff)
    metrics["calendar_streak"] = _streak(rows, cutoff)
    return metrics


def evaluate(picks: list[Pick]) -> dict:
    """Select a candidate on VALIDATION only; independently report HOLDOUT."""
    if not picks:
        return {"status": "BLOCKED_NO_MANAGER_PICKS"}
    regimes = {p.regime for p in picks}
    if len(regimes) != 1 or "" in regimes:
        return {
            "status": "BLOCKED_MISSING_OR_MIXED_POINT_IN_TIME_MODEL_REGIME",
            "manager_rows": len(picks),
            "regimes_present": sorted(regimes),
        }
    calendar_days = sorted({p.day for p in picks})
    if len(calendar_days) < 20:
        return {"status": "BLOCKED_INSUFFICIENT_CALENDAR_DAYS", "days": len(calendar_days)}
    # Split on whole calendar days to prevent same-day and match leakage.
    i = len(calendar_days) * 3 // 5
    j = len(calendar_days) * 4 // 5
    if i == 0 or i == j or j == len(calendar_days):
        raise ValueError("invalid chronological partition")
    train = [p for p in picks if p.day < calendar_days[i]]
    validation = [p for p in picks if calendar_days[i] <= p.day < calendar_days[j]]
    holdout = [p for p in picks if p.day >= calendar_days[j]]
    train_stats = _stats(train, 0)
    base_val = _stats(validation, 0)
    base_test = _stats(holdout, 0)
    if (base_val["scored"] < MIN_VALIDATION_SCORED
            or base_test["scored"] < MIN_HOLDOUT_SCORED):
        return {
            "status": "BLOCKED_INSUFFICIENT_VALIDATION_OR_HOLDOUT",
            "training_scored": train_stats["scored"],
            "validation_scored": base_val["scored"],
            "holdout_scored": base_test["scored"],
        }
    trials = []
    for cutoff in CANDIDATES:
        val = _stats(validation, cutoff)
        retained = val["picked"] / base_val["picked"] if base_val["picked"] else 0
        uplift = (
            val["win_rate"] - base_val["win_rate"]
            if val["win_rate"] is not None and base_val["win_rate"] is not None
            else None
        )
        eligible = (
            val["scored"] >= MIN_VALIDATION_SCORED
            and retained >= MIN_RETENTION
            and uplift is not None and uplift >= MIN_VALIDATION_UPLIFT
        )
        trials.append({
            "threshold": cutoff, "validation": val,
            "validation_retention": retained, "validation_uplift": uplift,
            "eligible": eligible,
        })
    viable = [t for t in trials if t["eligible"]]
    # Never evaluate the holdout to CHOOSE a cutoff. Tie-break on retention.
    selected = (
        sorted(
            viable,
            key=lambda x: (-x["validation_uplift"], -x["validation_retention"], x["threshold"]),
        )[0]
        if viable else None
    )
    evidence = {
        "status": "HOLDOUT_EVALUATED_RESEARCH_ONLY" if selected else "NO_VALIDATED_CANDIDATE",
        "model_regime_key": next(iter(regimes)),
        "method": "fixed confidence candidates, chronological day split, validation-only selection",
        "splits": {
            "train": [calendar_days[0].isoformat(), calendar_days[i - 1].isoformat()],
            "validation": [calendar_days[i].isoformat(), calendar_days[j - 1].isoformat()],
            "holdout": [calendar_days[j].isoformat(), calendar_days[-1].isoformat()],
        },
        "baseline_validation": _period(validation, 0),
        "baseline_holdout": _period(holdout, 0),
        "candidates_validation": trials,
        "selected_threshold": selected["threshold"] if selected else None,
        "selected_holdout": _period(holdout, selected["threshold"]) if selected else None,
        "live_approval": False,
        "important": (
            "Retrospective experimental screen only. No claim of >=73% for five "
            "future days; evaluate 5-day calendar coverage, statistical uncertainty, "
            "odds/ROI where valid, multiple-testing risk and forward shadow results."
        ),
    }
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Manager 73% shadow evaluator")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(parse_ledger(json.loads(args.ledger.read_text(encoding="utf-8-sig"))))
    content = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
