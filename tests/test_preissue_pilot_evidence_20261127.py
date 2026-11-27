from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/research/evidence"


def test_rehearsal_evidence_covers_every_failure_mode_and_promotes_nothing_late():
    report = json.loads(
        (EVIDENCE / "preissue_pilot_rehearsal_20261127.json").read_text())
    assert all(report["assertions"].values())
    assert report["production_side_effects"] == {
        "provider_calls": 0, "database_writes": 0, "production_budget_used": 0,
    }
    assert report["actual_reservations"] == report["provider_calls"] > 0
    assert report["original_time_qualified"] == 3
    late = report["scenarios"]["late_response"]
    assert late["qualified"] == 0
    assert late["forecast"]["abstained"] is True
    assert late["results"][0]["observation"]["qualification"] == "LATE_RESPONSE"
    assert report["scenarios"]["retrospective_reconstruction"]["qualified"] == 0
    assert report["scenarios"]["lost_acknowledgement"]["qualified"] == 0


def test_readiness_evidence_keeps_requests_and_qualification_at_zero():
    report = json.loads(
        (EVIDENCE / "preissue_pilot_readiness_real_20261127.json").read_text())
    assert report["provider_calls"] == 0
    assert report["actual_request_reservations"] == 0
    assert report["genuine_price_observations"] == 0
    assert report["original_time_qualifications"] == 0

    for provider in ("ODDSPAPI", "API_TENNIS"):
        audit = report["strict_qualification_audit"][provider]
        assert audit["qualified_markets"] == 0
        assert audit["status_counts"].get("ORIGINAL_TIME_QUALIFIED", 0) == 0

    identity = report["oddspapi_identity_continuity"]
    assert identity["state_counts"]["AFTER_PRICE"] > 0
    assert report["actual_competitive_start_persistence"][
        "explicit_actual_competitive_start_stored"] is False
    assert report["tournament_edition_mapping"][
        "oddspapi_tournament_source_ids"] == 0
    assert report["tournament_edition_mapping"][
        "cached_oddspapi_editions_missing_end_date"] == report[
            "tournament_edition_mapping"]["cached_oddspapi_editions"]

    calendar = report["pilot_calendar_dry_run"]
    assert calendar["planned_fixtures"] == 0
    assert calendar["provider_calls"] == 0
    assert calendar["actual_request_reservations"] == 0
    assert calendar["original_time_prices"] == 0
    assert report["entitlements"]["API_TENNIS"]["may_make_billable_requests"] is False
    assert report["entitlements"]["ODDSPAPI"]["may_make_billable_requests"] is False
    assert report["balanced_160_target"]["ATP_MAIN"]["shortfall"] == 80
    assert report["balanced_160_target"]["substituted_from_other_competition"] is False
    assert report["outstanding_approvals"]
