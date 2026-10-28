"""Research-only, strictly chronological multi-factor quality selection.

Original Results are immutable. This is a hypothetical *abstention* on
first-production ALIGNED Model Picks, never a new prediction or live policy.
All feature gates use only values frozen at the original pick time.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path

VALID_OUTCOMES = {"WON", "LOST", "VOID", "PENDING"}
VALID_TYPES = {"MANAGER_CONSENSUS", "A1_A2_WIDE_SPREAD"}
SURFACES = {"HARD", "CLAY", "GRASS", "CARPET"}
MIN_TRAIN_GRADED = 40
MIN_TRAIN_COVERAGE = 0.45
MIN_TRAIN_UPLIFT = 0.03
MIN_HOLDOUT_DAILY_GRADED = 5


@dataclass(frozen=True)
class Match:
    id: int
    day: date
    picked_at: datetime
    kind: str
    probability: float
    side: int
    surface: str
    tier: str
    a1_side: int | None
    a2_side: int | None
    a1_probability: float | None
    a2_probability: float | None
    result: str
    result_as_of: datetime | None
    result_source_as_of: datetime | None


def _date(v) -> date:
    return date.fromisoformat(str(v))


def _timestamp(v) -> datetime | None:
    if v is None or str(v).strip() == "":
        return None
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("timestamp without timezone")
    return d.astimezone(timezone.utc)


def parse(payload) -> tuple[Match, ...]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("expected canonical JSON data array")
    seen = set()
    out = []
    for r in rows:
        mid = int(r["match_id"])
        if mid in seen:
            raise ValueError("duplicate original aligned sporting match")
        seen.add(mid)
        kind = str(r["aligned_pick_type"])
        if kind not in VALID_TYPES:
            raise ValueError("unrecognized aligned pick population")
        result = str(r["result_status"])
        if result not in VALID_OUTCOMES:
            raise ValueError("unrecognized outcome")
        proba = float(r["aligned_probability"])
        if not math.isfinite(proba) or not 0 <= proba <= 1:
            raise ValueError("invalid original confidence")
        side = int(r["selected_side"])
        if side not in (1, 2):
            raise ValueError("invalid picked side")
        def optional_side(key):
            v = r.get(key)
            return int(v) if v in (1, 2, "1", "2") else None
        def optional_probability(key):
            v = r.get(key)
            if v is None:
                return None
            v = float(v)
            if not math.isfinite(v) or not 0 <= v <= 1:
                raise ValueError("invalid agent probability")
            return v
        out.append(Match(
            mid, _date(r["manila_date"]), _timestamp(r["picked_at"]),
            kind, proba, side, str(r.get("surface") or "UNKNOWN").upper(),
            str(r.get("tier") or "UNKNOWN").upper(),
            optional_side("a1_side"), optional_side("a2_side"),
            optional_probability("a1_prob"), optional_probability("a2_prob"),
            result, _timestamp(r.get("result_observed_at")),
            _timestamp(r.get("result_source_observed_at")),
        ))
    return tuple(sorted(out, key=lambda x: (x.day, x.picked_at, x.id)))


def agent_agrees(p: Match, max_spread: float) -> bool:
    return (
        p.a1_side == p.side == p.a2_side
        and p.a1_probability is not None
        and p.a2_probability is not None
        and abs(p.a1_probability - p.a2_probability) < max_spread
    )


POLICIES = (
    "original",
    "confidence70",
    "known_surface",
    "quality_balanced",
    "quality_strict",
    "quality_tier_guard",
)


def allowed(p: Match, policy: str) -> bool:
    if policy == "original":
        return True
    if policy == "confidence70":
        return p.probability >= 0.70
    if policy == "known_surface":
        return p.surface in SURFACES
    if policy == "quality_balanced":
        # A stronger Challenger gate is a prespecified research hypothesis,
        # not an observed uplift or model calibration claim.
        minimum = 0.76 if p.tier == "CHALLENGER" else 0.70
        return (
            p.tier in ("CHALLENGER", "ATP_TOUR", "MASTERS_1000")
            and p.surface in SURFACES
            and p.probability >= minimum
            and agent_agrees(p, 0.10)
        )
    if policy == "quality_strict":
        return (
            p.tier in ("CHALLENGER", "ATP_TOUR", "MASTERS_1000")
            and p.surface in SURFACES
            and p.probability >= 0.80
            and agent_agrees(p, 0.05)
        )
    if policy == "quality_tier_guard":
        return (
            p.tier in ("CHALLENGER", "ATP_TOUR", "MASTERS_1000")
            and p.surface in SURFACES
            and p.probability >= (0.80 if p.tier == "CHALLENGER" else 0.70)
        )
    raise ValueError("unknown candidate policy")


def report(matches: tuple[Match, ...], policy: str) -> dict:
    retained = tuple(p for p in matches if allowed(p, policy))
    omitted = tuple(p for p in matches if not allowed(p, policy))
    count = lambda pool, result: sum(p.result == result for p in pool)
    won, lost = count(retained, "WON"), count(retained, "LOST")
    void, pending = count(retained, "VOID"), count(retained, "PENDING")
    assert won + lost + void + pending == len(retained)
    return dict(
        original=len(matches), retained=len(retained),
        coverage=len(retained)/len(matches) if matches else 0.0,
        won=won, lost=lost, void=void, pending=pending,
        win_rate=won/(won+lost) if won+lost else None,
        missed_winners=count(omitted, "WON"),
        avoided_losses=count(omitted, "LOST"),
    )


def as_of_cutoff(day: date) -> datetime:
    # Midnight at the start of a Manila date, UTC+08.
    return datetime.combine(day, time.min, tzinfo=timezone.utc).replace(
        hour=16
    ) - __import__("datetime").timedelta(days=1)


def train_eligible(p: Match, cutoff: datetime) -> bool:
    return (
        p.picked_at < cutoff
        and p.day < (cutoff + __import__("datetime").timedelta(hours=8)).date()
        and p.result in {"WON", "LOST"}
        and p.result_as_of is not None
        and p.result_source_as_of is not None
        and p.result_as_of <= cutoff
        and p.result_source_as_of <= cutoff
    )


def evaluate(matches: tuple[Match, ...], *, validation_start: date,
             validation_end: date) -> dict:
    if validation_start > validation_end:
        raise ValueError("validation window is reversed")
    cutoff = as_of_cutoff(validation_start)
    train = tuple(p for p in matches if train_eligible(p, cutoff))
    holdout = tuple(p for p in matches if validation_start <= p.day <= validation_end)
    train_baseline = report(train, "original")
    research = []
    for policy in POLICIES[1:]:
        m = report(train, policy)
        delta = (
            m["win_rate"] - train_baseline["win_rate"]
            if m["win_rate"] is not None and train_baseline["win_rate"] is not None
            else None
        )
        qualifies = (
            m["won"] + m["lost"] >= MIN_TRAIN_GRADED
            and train_baseline["won"]+train_baseline["lost"] >= MIN_TRAIN_GRADED
            and m["coverage"] >= MIN_TRAIN_COVERAGE
            and delta is not None and delta >= MIN_TRAIN_UPLIFT
        )
        research.append({"policy": policy, "training": m,
                         "train_lift": delta, "eligible_for_shadow": qualifies})
    candidates = [x for x in research if x["eligible_for_shadow"]]
    chosen = sorted(
        candidates, key=lambda x: (-x["train_lift"], -x["training"]["coverage"], x["policy"])
    )[0]["policy"] if candidates else None

    daily = []
    d = validation_start
    while d <= validation_end:
        subset = tuple(p for p in holdout if p.day == d)
        variants = {}
        for policy in POLICIES:
            m = report(subset, policy)
            m["day_73_status"] = (
                "NO_ORIGINAL_PICKS" if not subset else
                "NO_RETAINED_PICKS" if not m["retained"] else
                "UNSETTLED" if m["pending"] else
                "TOO_FEW_GRADED" if m["won"] + m["lost"] < MIN_HOLDOUT_DAILY_GRADED else
                "REPLAY_MET_73_NOT_LIVE" if m["won"]*100 >= 73*(m["won"]+m["lost"]) else
                "REPLAY_BELOW_73"
            )
            variants[policy] = m
        daily.append({"day": d.isoformat(), "policies": variants})
        d += __import__("datetime").timedelta(days=1)
    return {
        "status": "RESEARCH_ONLY_NOT_DEPLOYED",
        "cohort": "ALL_ORIGINAL_ALIGNED_MODEL_PICKS",
        "train_cutoff_utc_exclusive": cutoff.isoformat(),
        "train_scored": train_baseline["won"] + train_baseline["lost"],
        "chosen_without_holdout_outcomes": chosen,
        "training_candidates": research,
        "daily": daily,
        "holdout_original_picks": len(holdout),
        "deployed": False,
        "note": (
            "All Oct24+ comparison rates are hypothetical abstentions on original "
            "Model Picks. Candidate is selected solely by outcomes already "
            "observed before holdout. Do not turn backtest into original Results. "
            "No 73% five-day promise. Five-day actual tracker remains separate."
        ),
    }


def main() -> int:
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument("--ledger", type=Path, required=True)
    arg.add_argument("--start", type=date.fromisoformat, default=date(2026, 10, 24))
    arg.add_argument("--end", type=date.fromisoformat, default=date(2026, 11, 1))
    arg.add_argument("--output", type=Path)
    a = arg.parse_args()
    result = evaluate(parse(json.loads(a.ledger.read_text(encoding="utf-8-sig"))),
                      validation_start=a.start, validation_end=a.end)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if a.output:
        if a.output.exists():
            raise FileExistsError("refusing to overwrite existing research report")
        a.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
