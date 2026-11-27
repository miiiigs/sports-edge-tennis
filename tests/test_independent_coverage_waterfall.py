"""Contract tests for the independent coverage waterfall.

The central rule: an unknown official denominator is ``None``, never a zero or
an internal ratio presented as official coverage.
"""
from __future__ import annotations

import pytest

from ops.validation import independent_coverage_waterfall_20261127 as evidence
from sports_edge_tennis.validation.independent_coverage_waterfall import (
    EXTERNAL_COVERAGE_COMPLETE,
    EXTERNAL_COVERAGE_UNKNOWN,
    FIXTURE_STAGES,
    SELECTION_STAGES,
    aggregate_fixture_totals,
    coverage_from_totals,
    coverage_waterfall,
    selection_track,
)


def _record(identifier, **stages):
    return {"fixture_id": identifier, **stages}


def test_fixture_waterfall_counts_and_missing_at_each_stage():
    records = [
        _record("a", DISCOVERED=True, MAPPED=True, EVALUATED=True),
        _record("b", DISCOVERED=True, MAPPED=True, EVALUATED=False),
        _record("c", DISCOVERED=True, MAPPED=False, EVALUATED=False),
    ]
    result = coverage_waterfall(
        records,
        stages=FIXTURE_STAGES,
        track="FIXTURE",
        official_total=None,
        official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
    )
    stages = {item["stage"]: item for item in result["stages"]}
    assert stages["DISCOVERED"]["count"] == 3
    assert stages["MAPPED"]["count"] == 2
    assert stages["MAPPED"]["missing_from_previous"] == 1
    assert stages["MAPPED"]["missing_identified"] == ["c"]
    assert stages["EVALUATED"]["count"] == 1
    assert set(stages["EVALUATED"]["missing_identified"]) == {"b"}
    assert result["external_coverage"] is None
    assert result["external_coverage_status"] == EXTERNAL_COVERAGE_UNKNOWN


def test_unknown_stage_breaks_the_chain_instead_of_reporting_zero():
    records = [
        _record("a", DISCOVERED=True, MAPPED=None, EVALUATED=False),
        _record("b", DISCOVERED=True, MAPPED=None, EVALUATED=False),
    ]
    result = coverage_waterfall(
        records,
        stages=FIXTURE_STAGES,
        track="FIXTURE",
        official_total=None,
        official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
    )
    stages = {item["stage"]: item for item in result["stages"]}
    assert stages["DISCOVERED"]["count"] == 2
    assert stages["MAPPED"]["count"] is None
    assert stages["EVALUATED"]["count"] is None
    assert stages["MAPPED"]["unknown_reason"] == "STAGE_EVIDENCE_UNAVAILABLE"


def test_complete_official_export_computes_external_coverage():
    totals = {"DISCOVERED": 90, "MAPPED": 80, "EVALUATED": 70}
    result = coverage_from_totals(
        totals,
        stages=FIXTURE_STAGES,
        track="FIXTURE",
        official_total=100,
        official_export_status=EXTERNAL_COVERAGE_COMPLETE,
    )
    stages = {item["stage"]: item for item in result["stages"]}
    assert result["official_total_is_known"] is True
    assert stages["DISCOVERED"]["missing_from_previous"] == 10
    assert stages["DISCOVERED"]["coverage_of_official"] == pytest.approx(0.90)
    assert result["external_coverage"] == pytest.approx(0.70)


def test_non_nested_evidence_fails_closed():
    records = [
        _record("a", DISCOVERED=True, MAPPED=False, EVALUATED=True),
    ]
    with pytest.raises(ValueError, match="EVALUATED=True"):
        coverage_waterfall(
            records,
            stages=FIXTURE_STAGES,
            track="FIXTURE",
            official_total=None,
            official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
        )


def test_duplicate_ids_and_missing_stage_values_fail_closed():
    duplicate = [
        _record("a", DISCOVERED=True, MAPPED=True, EVALUATED=True),
        _record("a", DISCOVERED=True, MAPPED=True, EVALUATED=True),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        coverage_waterfall(
            duplicate,
            stages=FIXTURE_STAGES,
            track="FIXTURE",
            official_total=None,
            official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
        )

    missing = [{"fixture_id": "x", "DISCOVERED": True, "MAPPED": "yes", "EVALUATED": None}]
    with pytest.raises(ValueError, match="must be bool or None"):
        coverage_waterfall(
            missing,
            stages=FIXTURE_STAGES,
            track="FIXTURE",
            official_total=None,
            official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
        )


def test_selection_track_never_infers_missing_delivery_stages():
    records = [
        {"selection_id": "s1", "SELECTED": True},
        {"selection_id": "s2", "SELECTED": True},
    ]
    result = selection_track(records)
    stages = {item["stage"]: item for item in result["stages"]}
    assert stages["SELECTED"]["count"] == 2
    for stage in SELECTION_STAGES[1:]:
        assert stages[stage]["count"] is None
    assert result["external_coverage"] is None


def test_committed_aggregate_totals_are_internally_monotone():
    rows = [
        {
            "stored_matches": 10,
            "no_provider_source_id": 1,
            "pit_visible": 8,
            "a1_predicted": 8,
            "manager_consensus": 2,
            "settled_completed": 7,
        }
    ]
    totals = aggregate_fixture_totals(rows)
    assert totals == {
        "DISCOVERED": 10,
        "MAPPED": 9,
        "PIT_VISIBLE": 8,
        "EVALUATED": 8,
        "SELECTED": 2,
        "SETTLED_FIXTURE": 7,
    }
    result = coverage_from_totals(
        totals,
        stages=FIXTURE_STAGES,
        track="FIXTURE",
        official_total=None,
        official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
    )
    stages = {item["stage"]: item for item in result["stages"]}
    assert [stages[s]["count"] for s in FIXTURE_STAGES] == [10, 9, 8]


def test_evidence_keeps_external_denominator_unknown_and_inclusive():
    payload = evidence.build_evidence(evidence.SOURCE)
    external = payload["external_populations"]
    assert external["official_eligible"] is None
    assert external["external_coverage"] is None
    assert external["external_coverage_status"] == EXTERNAL_COVERAGE_UNKNOWN
    assert "UNDISCOVERED_OFFICIAL_FIXTURES" in external["external_denominator_includes"]
    assert "FAILED_OR_MISSING_FORECASTS" in external["external_denominator_includes"]
    assert payload["coverage_contract"]["undiscovered_removed_from_denominator"] is False
    assert payload["fixture_track"]["external_coverage"] is None
    assert payload["selection_track"]["external_coverage"] is None
