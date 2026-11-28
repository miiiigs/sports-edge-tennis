from __future__ import annotations

from sports_edge_tennis.validation.agent2_specialist_audit import (
    CATEGORIES,
    audit_architecture,
    evaluate_category,
    substantial_disagreement,
    well_supported,
)

ARCHITECTURE = audit_architecture()


def _row(i: int, *, winner: int, a1: float, a2: float,
         matches: int = 20, points: int = 1000) -> dict:
    return {
        "match_id": i,
        "winner": winner,
        "a1_p1": a1,
        "a2_p1": a2,
        "p1_matches": matches,
        "p2_matches": matches,
        "p1_service_points": points,
        "p2_service_points": points,
        "p1_return_points": points,
        "p2_return_points": points,
    }


def test_architecture_audit_reads_the_real_configuration():
    assert ARCHITECTURE["present"]["surface_and_tour_shrinkage"] is True
    assert ARCHITECTURE["present"]["minimum_prior_usable_matches"] == 5
    assert ARCHITECTURE["present"]["minimum_prior_service_points"] == 250
    assert ARCHITECTURE["present"]["minimum_prior_return_points"] == 250
    assert ARCHITECTURE["present"]["one_sided_history_abstains"] is True
    # These extensions are genuinely absent, so the audit must not claim them.
    assert ARCHITECTURE["not_implemented"]["recency"] is True
    assert ARCHITECTURE["not_implemented"]["opponent_strength"] is True
    assert ARCHITECTURE["not_implemented"]["uncertainty"] is True


def test_specialist_categories_are_support_based_not_outcome_based():
    supported = _row(1, winner=1, a1=0.8, a2=0.2)
    unsupported = _row(2, winner=1, a1=0.8, a2=0.2, matches=4, points=100)
    assert well_supported(supported) is True
    assert well_supported(unsupported) is False
    assert substantial_disagreement(supported) is True
    codes = {category.code for category in CATEGORIES}
    assert "WELL_SUPPORTED_BOTH" in codes
    assert "WELL_SUPPORTED_AND_DISAGREEMENT" in codes


def test_agent2_specialist_gate_requires_a_paired_interval_not_a_point_estimate():
    rows = []
    match_id = 0
    # 120 fixtures where Agent 2 is right and Agent 1 is wrong.
    for _ in range(60):
        match_id += 1
        rows.append(_row(match_id, winner=1, a1=0.2, a2=0.8))
    for _ in range(60):
        match_id += 1
        rows.append(_row(match_id, winner=2, a1=0.8, a2=0.2))
    # 20 fixtures in the other direction, so both directions are represented.
    for _ in range(20):
        match_id += 1
        rows.append(_row(match_id, winner=1, a1=0.8, a2=0.2))

    category = next(c for c in CATEGORIES if c.code == "WELL_SUPPORTED_AND_DISAGREEMENT")
    result = evaluate_category(rows, category)
    assert result["n"] == 140
    assert result["disagreement"]["a2_only_correct"] == 120
    assert result["disagreement"]["a1_only_correct"] == 20
    assert result["a2"]["brier"] < result["a1"]["brier"]
    assert result["gate"]["minimum_fixtures"] == 100
    assert result["verdict"] == "A2_INCREMENTAL_VALUE_SUPPORTED"


def test_small_or_unsupported_category_reports_insufficient_evidence():
    rows = [_row(i, winner=1, a1=0.8, a2=0.2) for i in range(10)]
    category = next(c for c in CATEGORIES if c.code == "WELL_SUPPORTED_BOTH")
    result = evaluate_category(rows, category)
    assert result["n"] == 10
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
