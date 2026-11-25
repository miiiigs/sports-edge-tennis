"""Audit already-observed Railway forward logs, without querying or grading matches.

A successful audit proves consistency of this bounded export, not independent
receipt, official fixture completeness, or future forecasting improvement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

PREFIX = "TENNIS_FROZEN_PROSPECTIVE_"
KINDS = (PREFIX + "CANDIDATE", PREFIX + "SUMMARY", PREFIX + "HEARTBEAT", "LIVE_BATCH_SUCCESS")


def _clock(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("naive clock")
    return dt.astimezone(UTC)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def audit(export: dict) -> dict:
    _require(export["schema"] == "railway_forward_log_export_v1", "unknown export schema")
    start, end = map(_clock, (export["window_start_utc"], export["window_end_utc"]))
    _require(start < end, "invalid export window")
    logs = export["logs"]
    seen_counts = Counter(row["message"].split(" ", 1)[0] for row in logs)
    _require(set(seen_counts) <= set(KINDS), "unexpected log kind")
    _require(all(seen_counts[k] == export["counts"][k] for k in KINDS), "export count mismatch")
    _require(all(seen_counts[k] < export["per_query_limit"] for k in KINDS), "possible truncated export")
    pending = []
    summaries = []
    heartbeat = None
    seen_matches = set()
    totals = Counter()
    selections = Counter()
    previous = start
    cycles = []
    batch_receipts = []
    tours = Counter()

    for row in logs:
        receipt = _clock(row["timestamp"])
        _require(previous <= receipt < end, "out-of-order or out-of-window log")
        previous = receipt
        kind, payload = row["message"].split(" ", 1)
        if kind == KINDS[3]:
            _require(heartbeat is not None and not pending, "batch missing complete observer heartbeat")
            cycles.append(heartbeat)
            batch_receipts.append(receipt)
            heartbeat = None
            summaries = []
            continue
        data = json.loads(payload)
        _require(heartbeat is None, "observer data after heartbeat without batch")
        if kind == KINDS[0]:
            issued, captured, scheduled = map(_clock, (
                data["original_issued_at_utc"], data["captured_at_utc"], data["scheduled_start_at_utc"]
            ))
            _require(issued <= captured <= receipt <= scheduled - timedelta(minutes=30),
                     "late or inconsistent capture receipt")
            _require(data["outcomes_available_during_capture"] is False, "outcome already available")
            _require(data["not_a_published_model_pick"] is True, "not a research record")
            _require(data["tour"] in ("ATP_MAIN", "CHALLENGER"), "out-of-scope tour")
            match = data["canonical_match_id"]
            _require(match not in seen_matches, "duplicate canonical first issue")
            seen_matches.add(match)
            tours[data["tour"]] += 1
            pending.append(data)
        elif kind == KINDS[1]:
            capture = _clock(data["capture_time_utc"])
            _require(capture <= receipt, "summary before capture")
            _require(len(pending) == data["first_issued_future_targets"], "missing candidate records")
            _require(all(_clock(r["captured_at_utc"]) == capture for r in pending), "mixed capture batches")
            _require(all(r["first_prediction_run_id"] in data["filtered_to_first_run_ids"]
                         for r in pending), "wrong original run")
            _require(all(r["version"] == data["version"] and
                         r["frozen_research_definition_commit"] == data["research_freeze_code_sha"]
                         for r in pending), "mixed schema or frozen definition")
            _require(data["settled_match_results_at_capture"] == 0, "settled at capture")
            _require([r["canonical_match_id"] for r in pending] ==
                     sorted(r["canonical_match_id"] for r in pending), "noncanonical record ordering")
            raw = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in pending)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            _require(digest == data["utc_capture_records_sha256"], "capture digest mismatch")
            for name, cfg in data["frozen_rules"].items():
                _require(all(name in r["frozen_research_policies"] and
                             r["frozen_research_policies"][name]["frozen_confidence_cutoff"] == cfg["threshold"]
                             for r in pending), "frozen policy mismatch")
                count = sum(r["frozen_research_policies"][name]["status"] == "SELECTED" for r in pending)
                _require(count == data["selection_counts"][name], "selection count mismatch")
                selections[name] += count
            totals["verified_summaries"] += 1
            totals["records"] += len(pending)
            summaries.append(data)
            pending = []
        elif kind == KINDS[2]:
            _require(not pending, "candidate records without summary")
            _require(data["attempted"] == data["successful"] + data["failed"], "invalid heartbeat counters")
            _require(data["successful"] == len(summaries), "missing capture summary")
            _require(data["records_logged"] == sum(s["first_issued_future_targets"] for s in summaries),
                     "heartbeat record count mismatch")
            totals["attempted"] += data["attempted"]
            totals["successful"] += data["successful"]
            totals["failed"] += data["failed"]
            heartbeat = data
    _require(not pending and not summaries and heartbeat is None, "incomplete export tail")
    _require(bool(cycles), "no completed batch cycles")
    # Coverage of observed completion hours is not independent evidence that
    # every scheduled job actually ran. Report absent hours explicitly when
    # the bounded export covers whole UTC hours; never infer missing fixtures.
    expected_hours = []
    if start.minute == start.second == start.microsecond == 0 and end.minute == end.second == end.microsecond == 0:
        expected_hours = [start + timedelta(hours=i) for i in range(int((end - start).total_seconds() // 3600))]
    observed_hours = Counter(t.replace(minute=0, second=0, microsecond=0) for t in batch_receipts)
    return {
        "status": "RECONCILED_EXPORT_ONLY",
        "batch_cycles": len(cycles),
        "capture_summaries": totals["verified_summaries"],
        "attempted": totals["attempted"],
        "successful": totals["successful"],
        "failed": totals["failed"],
        "unique_first_issued_research_targets": len(seen_matches),
        "selection_counts": dict(sorted(selections.items())),
        "research_targets_by_tour": dict(sorted(tours.items())),
        "expected_hour_slots_in_export": len(expected_hours) if expected_hours else None,
        "observed_completion_hour_slots": len(observed_hours),
        "hours_without_success_marker": [t.isoformat() for t in expected_hours if t not in observed_hours],
        "hours_with_multiple_success_markers": [t.isoformat() for t, n in sorted(observed_hours.items()) if n > 1],
        "independent_job_schedule_verified": False,
        "independent_receipt_verified": False,
        "persistent_storage_verified": False,
        "official_eligible_coverage_verified": False,
        "settlement_verified": False,
        "production_model_changed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(json.loads(args.export.read_text(encoding="utf-8"))), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
