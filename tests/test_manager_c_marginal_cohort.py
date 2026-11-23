"""Brian Nov23 P0: exact nested Manager C marginal admission, fail closed."""
from datetime import UTC, datetime, timedelta
import pytest

from sports_edge_tennis.validation.manager_c_marginal_cohort import (
    POLICY, analyze, _original_agent_evidence, _summary,
)

SPLIT = datetime(2026, 11, 4, tzinfo=UTC)


def record(mid, *, validation=False, confidence=.8, winner=1, tour="ATP_MAIN",
           candidate=True):
    start = datetime(2026, 10, 20, 12, tzinfo=UTC) if validation else (
        datetime(2026, 11, 10, 12, tzinfo=UTC))
    return {
        "match_id": mid, "start_at": start,
        "issued_at": start - timedelta(hours=1), "tour": tour,
        "winner": winner, "candidates": {
            "original_manager": None,
            "B_guarded2_floor60_spread20": None,
            POLICY: (1, confidence) if candidate else None,
        },
    }


def test_actual_id_nesting_used_before_saying_marginal_96_53():
    validation = [
        record(i, validation=True, confidence=.90-.03*i)
        for i in range(1, 11)
    ]
    holdout = [
        record(100+i, confidence=.90-.03*i,
               tour="CHALLENGER" if i % 2 else "ATP_MAIN",
               winner=2 if i==4 else 1)
        for i in range(1, 11)
    ]
    agents = {
        103: {
            "AGENT_1_SURFACE_ELO": (1, .84),
            "AGENT_2_HIERARCHICAL_MARKOV": (2, .48),
        },
        104: {
            "AGENT_1_SURFACE_ELO": (1, .83),
            "AGENT_2_HIERARCHICAL_MARKOV": (1, .74),
        },
    }
    z = analyze(validation+holdout, validation_end=SPLIT,
                original_agents=agents, dataset_sha256="deadbeef")
    assert z["status"] == "NESTED_VERIFIED_BY_EXACT_MATCH_IDS"
    assert z["selected_ids"]["narrow_missing_from_broad"] == 0
    assert z["selected_ids"]["narrow"] == 2
    assert z["selected_ids"]["broad"] == 4
    assert z["selected_ids"]["broad_only"] == 2
    assert z["narrow"]["wins"] == 2
    assert z["additional_only"]["wins"] == 1
    assert z["additional_only"]["losses"] == 1
    assert z["additional_only"]["a1_a2"]["counts"] == {"AGREE":1, "DISAGREE":1}
    assert z["broad"]["ungraded"] == 0
    assert z["historical_gap_to_75pct_broad"] == 0
    assert z["conditional_coverage"]["additional"] == .2
    assert z["official_fixture_denominator"] is None
    assert z["independent_forward_validation"] is False
    assert z["not_available_from_validated_first_run_source"]
    assert len(z["selected_ids"]["broad_only_ids_sha256"]) == 64


def test_ungraded_selection_is_not_counted_as_loss():
    rows = [record(1, winner=1),
            record(2, winner=None),
            record(3, winner=2)]
    s = _summary(rows, agent_evidence={})
    assert (s["selected"], s["settled"], s["ungraded"]) == (3,2,1)
    assert (s["wins"], s["losses"]) == (1,1)


def test_not_enough_candidate_capacity_never_fabricates_marginal_cohort():
    validation = [
        record(i, validation=True, candidate=i<=2) for i in range(1,11)
    ]
    holdout = [record(100+i) for i in range(1,11)]
    z = analyze(validation+holdout, validation_end=SPLIT)
    assert z["status"] == "UNREACHABLE_VALIDATION_CANDIDATE_CAPACITY"
    assert z["all_official_eligible_denominator"] is None


def test_duplicate_original_match_id_fails_closed():
    x = record(1)
    with pytest.raises(ValueError,match="duplicate"):
        analyze([record(2,validation=True),x,dict(x)],validation_end=SPLIT)


def test_hindsight_outcome_changes_cannot_change_frozen_confidence_or_membership():
    validation = [
        record(i, validation=True, confidence=.90-.03*i)
        for i in range(1,11)
    ]
    holdout = [
        record(100+i, confidence=.90-.03*i, winner=1)
        for i in range(1,11)
    ]
    a=analyze(validation+holdout,validation_end=SPLIT)
    flipped=[dict(r,winner=2) for r in holdout]
    b=analyze(validation+flipped,validation_end=SPLIT)
    assert a["cutoffs_fitted_earlier_validation_only"]==b["cutoffs_fitted_earlier_validation_only"]
    assert a["selected_ids"]["broad_only_ids_sha256"]==b["selected_ids"]["broad_only_ids_sha256"]
    assert a["additional_only"]["wins"]!=b["additional_only"]["wins"]


def test_raw_agent_evidence_uses_same_pit_source_codes_and_normalized_sides():
    # First-issue raw source_sql columns 0..17; original side2 .7 -> player1 .3.
    row = (123, 1, datetime(2026,11,10,10,tzinfo=UTC),
           datetime(2026,11,10,12,tzinfo=UTC),
           "ATP_MAIN", 55, "AGENT_1_SURFACE_ELO",
           "PREDICTED", 2, .7, datetime(2026,11,10,9,tzinfo=UTC),
           "NO_BET", "REJECTED", None, None,
           "COMPLETED", 1, datetime(2026,11,10,16,tzinfo=UTC))
    x = _original_agent_evidence([row])
    assert x[123]["AGENT_1_SURFACE_ELO"] == (2, pytest.approx(.3))
