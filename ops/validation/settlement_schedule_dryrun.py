"""Dry-run the settlement scheduler from an explicit candidate export.

No provider, database or wager access. Produces one sporting plan and one
bookmaker plan per candidate. A blocked plan is visible, never silently skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from sports_edge_tennis.ops.settlement_schedule import (
    ACTION_BLOCKED,
    SettlementCandidate,
    plan_settlements,
)


def _instant(value):
    if value is None:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return moment


def _candidate(payload, rules):
    return SettlementCandidate(
        decision_id=int(payload["decision_id"]),
        match_id=int(payload["match_id"]),
        scheduled_start=_instant(payload["scheduled_start"]),
        original_start=_instant(payload["original_start"]),
        revised_start=_instant(payload.get("revised_start")),
        actual_start=_instant(payload.get("actual_start")),
        result_status=str(payload["result_status"]),
        result_observed_at=_instant(payload.get("result_observed_at")),
        result_revision=int(payload.get("result_revision", 1)),
        source_system=str(payload["source_system"]),
        source_dataset=str(payload["source_dataset"]),
        source_reference=payload.get("source_reference"),
        source_approved=bool(payload.get("source_approved")),
        source_participants=tuple(payload["source_participants"])
        if payload.get("source_participants")
        else None,
        canonical_participants=tuple(payload["canonical_participants"]),
        winner_player_id=payload.get("winner_player_id"),
        selection_player_id=payload.get("selection_player_id"),
        current_settlement_revision=payload.get("current_settlement_revision"),
        current_sporting_grade=payload.get("current_sporting_grade"),
        bookmaker=payload.get("bookmaker"),
        bookmaker_rule_version=payload.get("bookmaker_rule_version"),
        bookmaker_rule_reference=payload.get("bookmaker_rule_reference"),
        bookmaker_grade=payload.get("bookmaker_grade"),
        approved_bookmaker_rules=rules,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--out-json")
    args = parser.parse_args(argv)
    with open(args.input_json, encoding="utf-8") as handle:
        payload = json.load(handle)
    as_of = _instant(payload["as_of"])
    stale_after = timedelta(hours=float(payload.get("stale_after_hours", 24)))
    rules = {
        (bookmaker, rule): reference
        for bookmaker, rule, reference in payload.get("approved_bookmaker_rules", [])
    }
    candidates = [_candidate(item, rules) for item in payload["candidates"]]
    plans = plan_settlements(
        candidates, as_of=as_of, stale_after=stale_after
    )
    counts: dict[str, int] = {}
    for plan in plans:
        key = f"{plan.pathway}:{plan.action}:{plan.reason}"
        counts[key] = counts.get(key, 0) + 1
    blocked = [
        plan.to_payload() for plan in plans if plan.action == ACTION_BLOCKED
    ]
    output = {
        "mode": "SETTLEMENT_SCHEDULE_DRY_RUN",
        "provider_calls": 0,
        "database_writes": 0,
        "as_of": as_of.isoformat(),
        "candidate_count": len(candidates),
        "plan_count": len(plans),
        "counts": dict(sorted(counts.items())),
        "blocked_count": len(blocked),
        "blocked": blocked,
        "plans": [plan.to_payload() for plan in plans],
    }
    text = json.dumps(output, indent=2, sort_keys=True)
    print(text)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0 if not blocked else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
