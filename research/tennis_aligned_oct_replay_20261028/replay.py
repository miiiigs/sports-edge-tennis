"""Research-only counterfactual replay of original ALIGNED tennis Model Picks.

No changes to published picks, outcomes, model policies or betting records.
An audit of skipped historical losers is not a deployable future strategy.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

AUDIT_DATES = tuple(date(2026, 10, d) for d in (24, 25, 26, 27))
VALID_OUTCOMES = {"WON", "LOST", "VOID", "PENDING"}
VALID_PICK_TYPES = {"MANAGER_CONSENSUS", "A1_A2_WIDE_SPREAD"}
KNOWN_SURFACES = {"CLAY", "HARD", "GRASS"}
PRETRAIN_MIN_SCORED = 40
MIN_TRAIN_RETENTION = 0.70
MIN_TRAIN_UPLIFT = 0.03


@dataclass(frozen=True)
class Pick:
    match_id: int
    day: date
    probability: float
    surface: str
    pick_type: str
    outcome: str
    picked_at: str
    tier: str = "UNKNOWN"


@dataclass(frozen=True)
class Rule:
    key: str
    minimum_probability: float = 0.60
    require_known_surface: bool = False

    def permits(self, p: Pick) -> bool:
        return (
            p.probability >= self.minimum_probability
            and (not self.require_known_surface or p.surface in KNOWN_SURFACES)
        )


RULES = (
    Rule("original_baseline"),
    Rule("aligned_prob_70", 0.70),
    Rule("aligned_prob_75", 0.75),
    Rule("aligned_prob_80", 0.80),
    Rule("known_surface_only", 0.60, True),
    Rule("aligned_prob_70_known_surface", 0.70, True),
)


def parse_ledger(payload: dict) -> list[Pick]:
    """Require canonical first-production aligned fields; no result-based filters."""
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("expected canonical SQL export JSON with data array")
    seen = set()
    out = []
    for r in rows:
        match_id = int(r["match_id"])
        if match_id in seen:
            raise ValueError(f"duplicate original sporting match: {match_id}")
        seen.add(match_id)
        ptype = str(r["aligned_pick_type"])
        if ptype not in VALID_PICK_TYPES:
            raise ValueError("unexpected aligned pick type")
        probability = float(r["aligned_probability"])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("invalid original point-in-time aligned probability")
        result = str(r["result_status"])
        if result not in VALID_OUTCOMES:
            raise ValueError("unknown result classification")
        timestamp = str(r["picked_at"])
        if not timestamp:
            raise ValueError("missing original prediction timestamp")
        day = date.fromisoformat(str(r["manila_date"]))
        surface = str(r.get("surface") or "UNKNOWN").upper()
        out.append(Pick(match_id, day, probability, surface, ptype, result, timestamp, str(r.get("tier") or "UNKNOWN").upper()))
    return sorted(out, key=lambda x: (x.day, x.picked_at, x.match_id))


def stats(picks: list[Pick], rule: Rule) -> dict:
    kept = [p for p in picks if rule.permits(p)]
    skipped = [p for p in picks if not rule.permits(p)]
    count = lambda rows, outcome: sum(p.outcome == outcome for p in rows)
    wins = count(kept, "WON")
    losses = count(kept, "LOST")
    void = count(kept, "VOID")
    pending = count(kept, "PENDING")
    scored = wins + losses
    if scored + void + pending != len(kept):
        raise ValueError("inconsistent original result ledger")
    return {
        "original": len(picks),
        "retained": len(kept),
        "coverage": len(kept) / len(picks) if picks else None,
        "wins": wins,
        "losses": losses,
        "void": void,
        "pending": pending,
        "scored": scored,
        "win_rate": wins / scored if scored else None,
        "skipped_wins": count(skipped, "WON"),
        "skipped_losses": count(skipped, "LOST"),
        "skipped_void": count(skipped, "VOID"),
        "skipped_pending": count(skipped, "PENDING"),
        "day_status": (
            "NO_ORIGINAL_PICKS" if not picks else
            "NO_RETAINED_PICKS" if not kept else
            "UNSETTLED" if pending else
            "NO_GRADED_PICKS" if not scored else
            "MET_73_PROVISIONAL_REPLAY" if wins * 100 >= 73 * scored else
            "BELOW_73_PROVISIONAL_REPLAY"
        ),
    }



def losing_pick_diagnostics(picks: list[Pick]) -> dict:
    """Descriptive, pre-match groupings. Post-result group mining is not validation."""
    if not picks:
        return {}
    confidence_groups = {
        "60_to_69": lambda p: 0.60 <= p.probability < 0.70,
        "70_to_79": lambda p: 0.70 <= p.probability < 0.80,
        "80_plus": lambda p: p.probability >= 0.80,
    }
    dimensions = {
        "confidence": {name: [p for p in picks if pred(p)] for name, pred in confidence_groups.items()},
        "surface": {key: [p for p in picks if p.surface == key] for key in sorted(set(p.surface for p in picks))},
        "tournament_tier": {key: [p for p in picks if p.tier == key] for key in sorted(set(p.tier for p in picks))},
        "aligned_pick_type": {key: [p for p in picks if p.pick_type == key] for key in sorted(set(p.pick_type for p in picks))},
    }
    out = {}
    for name, groups in dimensions.items():
        out[name] = {
            key: {
                "picks": len(rows),
                "won": sum(p.outcome == "WON" for p in rows),
                "lost": sum(p.outcome == "LOST" for p in rows),
                "void": sum(p.outcome == "VOID" for p in rows),
                "pending": sum(p.outcome == "PENDING" for p in rows),
                "win_rate": (
                    sum(p.outcome == "WON" for p in rows) /
                    sum(p.outcome in {"WON", "LOST"} for p in rows)
                ) if any(p.outcome in {"WON", "LOST"} for p in rows) else None,
            }
            for key, rows in groups.items()
            if rows
        }
    return out



def replay(picks: list[Pick]) -> dict:
    """No selecting a strategy using the Oct24-27 outcomes.

    Candidate selection is allowed only on data strictly preceding Oct24.
    The Oct24-27 rows are shown as unretuned, out-of-time counterfactuals.
    """
    earlier = [p for p in picks if p.day < AUDIT_DATES[0]]
    four_days = [p for p in picks if p.day in AUDIT_DATES]
    if not four_days:
        return {
            "status": "BLOCKED_NO_OCT24_27_ALIGNED_LEDGER",
            "original_history_preserved": True,
            "candidate_deployed": False,
        }
    orig_earlier = stats(earlier, RULES[0])
    candidates = []
    for rule in RULES[1:]:
        before = stats(earlier, rule)
        retention = before["coverage"] or 0.0
        uplift = (
            before["win_rate"] - orig_earlier["win_rate"]
            if before["win_rate"] is not None
            and orig_earlier["win_rate"] is not None else None
        )
        qualifies = (
            orig_earlier["scored"] >= PRETRAIN_MIN_SCORED
            and before["scored"] >= PRETRAIN_MIN_SCORED
            and retention >= MIN_TRAIN_RETENTION
            and uplift is not None and uplift >= MIN_TRAIN_UPLIFT
            and before["pending"] == 0 and orig_earlier["pending"] == 0
        )
        candidates.append({
            "rule": rule.key,
            "pre_oct24_scored": before["scored"],
            "pre_oct24_coverage": before["coverage"],
            "pre_oct24_accuracy": before["win_rate"],
            "pre_oct24_uplift_vs_original": uplift,
            "pre_oct24_eligible_for_shadow": qualifies,
        })
    eligible = [v for v in candidates if v["pre_oct24_eligible_for_shadow"]]
    selected = (
        sorted(
            eligible,
            key=lambda v: (-v["pre_oct24_uplift_vs_original"], -v["pre_oct24_coverage"], v["rule"]),
        )[0]["rule"]
        if eligible else None
    )
    output = []
    for rule in RULES:
        days = []
        for day in AUDIT_DATES:
            d = stats([p for p in four_days if p.day == day], rule)
            days.append({"date": day.isoformat(), **d})
        total = stats(four_days, rule)
        output.append({
            "rule": rule.key,
            "pre_oct24_selected": rule.key == selected,
            "four_day_total": total,
            "daily": days,
            "all_four_days_met_73": all(
                d["day_status"] == "MET_73_PROVISIONAL_REPLAY" for d in days
            ),
            "all_four_days_fully_settled": all(d["pending"] == 0 for d in days),
        })
    return {
        "status": "COUNTERFACTUAL_ONLY_NOT_ORIGINAL_PERFORMANCE",
        "objective": "test candidate eligibility from prior data then audit Oct24-27 without retrospective retuning",
        "original_history_preserved": True,
        "candidate_deployed": False,
        "oct24_27_used_to_select_candidate": False,
        "original_ledger_required": "ops/calibration_dataset.sql with original point-in-time fields",
        "target": ">=73% each fully settled day across ALL original aligned Model Picks",
        "baseline_prior_scored": orig_earlier["scored"],
        "observed_oct24_27_loss_diagnostics": losing_pick_diagnostics(four_days),
        "diagnostics_warning": (
            "The slice percentages describe observed outcomes only. Do not call a "
            "surface, confidence or tier a cause or select a rule based on these "
            "October 24–27 outcomes."
        ),
        "selected_from_prior_period_only": selected,
        "prior_period_selection_trials": candidates,
        "daily_counterfactuals": output,
        "interpretation": (
            "These are hypothetical abstentions on ALREADY ISSUED historical picks, "
            "not retroactive changes, nor a full model rerun of matches never "
            "picked. Pending and no-pick days cannot be called achieved. "
            "Do not activate a threshold without independent forward shadow."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline October 24–27 aligned-pick what-if study")
    ap.add_argument("--ledger", required=True, type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    report = replay(parse_ledger(json.loads(args.ledger.read_text(encoding="utf-8-sig"))))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
