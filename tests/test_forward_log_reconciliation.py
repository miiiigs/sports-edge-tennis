import json
from pathlib import Path

import pytest

from sports_edge_tennis.validation.forward_log_reconciliation import KINDS, audit

EVIDENCE = Path(__file__).resolve().parents[1] / "docs/research/forward_captures/railway_forward_20261125_0000_0400Z.json"


@pytest.fixture
def export():
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def test_actual_railway_export_reconciles(export):
    result = audit(export)
    assert result["batch_cycles"] == 4
    assert result["capture_summaries"] == 6
    assert result["unique_first_issued_research_targets"] == 6
    assert result["attempted"] == result["successful"] == 6
    assert result["failed"] == 0
    assert result["selection_counts"] == {
        "A1A2_equal_log_odds_blend": 2,
        "B_guarded2_floor60_spread20": 2,
        "C_uncalibrated_mean_floor60": 2,
        "original_manager": 3,
        "production_original_as_issued": 3,
    }
    assert result["independent_receipt_verified"] is False
    assert result["persistent_storage_verified"] is False
    assert result["official_eligible_coverage_verified"] is False
    assert result["settlement_verified"] is False


def test_missing_candidate_even_if_export_count_adjusted(export):
    row = next(r for r in export["logs"] if r["message"].startswith(KINDS[0] + " "))
    export["logs"].remove(row)
    export["counts"][KINDS[0]] -= 1
    with pytest.raises(ValueError, match="missing candidate"):
        audit(export)


def test_tampered_candidate_digest(export):
    row = next(r for r in export["logs"] if r["message"].startswith(KINDS[0] + " "))
    data = json.loads(row["message"].split(" ", 1)[1])
    data["original_manager_reason"] = "TAMPERED"
    row["message"] = KINDS[0] + " " + json.dumps(data)
    with pytest.raises(ValueError, match="digest mismatch"):
        audit(export)


def test_missing_summary_even_if_export_count_adjusted(export):
    row = next(r for r in export["logs"] if r["message"].startswith(KINDS[1] + " "))
    export["logs"].remove(row)
    export["counts"][KINDS[1]] -= 1
    with pytest.raises(ValueError, match="missing capture summary"):
        audit(export)


def test_truncated_export(export):
    export["per_query_limit"] = 6
    with pytest.raises(ValueError, match="truncated export"):
        audit(export)


def test_late_receipt_rejected(export):
    row = next(r for r in export["logs"] if r["message"].startswith(KINDS[0] + " "))
    data = json.loads(row["message"].split(" ", 1)[1])
    data["scheduled_start_at_utc"] = row["timestamp"]
    row["message"] = KINDS[0] + " " + json.dumps(data)
    with pytest.raises(ValueError, match="late or inconsistent"):
        audit(export)


def test_incomplete_tail_rejected(export):
    removed = export["logs"].pop()
    assert removed["message"].startswith(KINDS[3] + " ")
    export["counts"][KINDS[3]] -= 1
    with pytest.raises(ValueError, match="incomplete export tail"):
        audit(export)


def test_full_day_actual_archive_and_hourly_markers():
    path = EVIDENCE.with_name("railway_forward_20261124_full_day.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    result = audit(data)
    assert result["batch_cycles"] == 24
    assert result["capture_summaries"] == result["attempted"] == result["successful"] == 50
    assert result["failed"] == 0
    assert result["unique_first_issued_research_targets"] == 51
    assert result["research_targets_by_tour"] == {"ATP_MAIN": 1, "CHALLENGER": 50}
    assert result["selection_counts"] == {
        "A1A2_equal_log_odds_blend": 12,
        "B_guarded2_floor60_spread20": 10,
        "C_uncalibrated_mean_floor60": 7,
        "original_manager": 4,
        "production_original_as_issued": 5,
    }
    assert result["expected_hour_slots_in_export"] == 24
    assert result["observed_completion_hour_slots"] == 24
    assert result["hours_without_success_marker"] == []
    assert result["independent_job_schedule_verified"] is False


def test_whole_missing_cycle_reported_as_hour_gap(export):
    removed = [row for row in export["logs"] if row["timestamp"].startswith("2026-11-25T00:")]
    export["logs"] = [row for row in export["logs"] if row not in removed]
    for row in removed:
        export["counts"][row["message"].split(" ", 1)[0]] -= 1
    result = audit(export)
    assert result["batch_cycles"] == 3
    assert result["hours_without_success_marker"] == ["2026-11-25T00:00:00+00:00"]
    assert result["independent_job_schedule_verified"] is False
