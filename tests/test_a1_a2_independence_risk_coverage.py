"""Safe paired A1/A2 research: no full-eligible or production Manager claims."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.a1_a2_blend_shadow import Pair
from sports_edge_tennis.validation.a1_a2_independence_risk_coverage import (
    _corr, _mcnemar, evaluate, run,
)


BASE = datetime(2026, 10, 1, 12, tzinfo=UTC)


def corpus(n: int = 40, *, unsettled: int = 0) -> list[Pair]:
    result = []
    for i in range(n):
        y = i % 2
        a1_right = i % 4 != 0
        a2_right = i % 3 != 0
        a1_side = y if a1_right else 1-y
        a2_side = y if a2_right else 1-y
        start = BASE + timedelta(days=i//2)
        result.append(Pair(
            match_id=1000+i,
            issued_at=start-timedelta(hours=2),
            start_at=start,
            tour="ATP_TOUR" if i % 2 else "CHALLENGER",
            surface="HARD",
            cluster_id=str(i//4),
            a1_p1=.8 if a1_side else .2,
            a2_p1=.7 if a2_side else .3,
            winner_p1=None if i < unsettled else y,
        ))
    return result


def test_matched_independence_metrics_have_honest_denominators():
    report = evaluate(corpus())
    assert report["paired_originals"] == 40
    assert report["paired_settled"] == 40
    assert report["status"] == "RESEARCH_DIAGNOSTICS_COMPLETE_NO_LIVE_MANAGER_INFERENCE"
    assert sum(report["correctness_overlap"].values()) == 40
    assert report["direction_agreement"]["agreed"] + report["direction_agreement"]["disagreed"] == 40
    assert report["mcnemar_a1_vs_a2"]["paired_samples"] == 40
    assert report["per_tour"]["ATP_TOUR"]["settled"] == 20
    assert report["per_tour"]["CHALLENGER"]["settled"] == 20
    curve = report["accuracy_by_paired_cohort_selection_fraction"]["a1_surface_elo"]
    assert curve["0.4"]["pair_cohort_denominator"] == 40
    assert curve["0.4"]["selections"] == 16
    assert curve["0.4"]["full_eligible_match_coverage"] is None
    assert report["full_eligible_fixture_denominator"] is None
    assert not report["full_eligible_match_coverage_verified"]
    assert not report["third_agent_assessed"]
    assert not report["actual_manager_compared"]
    assert not report["release_eligible"]
    assert not report["db_writes"]


def test_pending_and_void_never_graded_and_no_false_sufficiency_claim():
    data = corpus(25, unsettled=10)
    result = evaluate(data)
    assert result["paired_originals"] == 25
    assert result["paired_settled"] == 15
    assert result["pending_void_or_unsettled"] == 10
    assert result["status"] == "INSUFFICIENT_SETTLED_PAIRED_COHORT"
    assert "accuracy_by_paired_cohort_selection_fraction" not in result


def test_exact_mcnemar_small_sample_and_empty():
    out = _mcnemar([True, True, False, False], [False, False, False, False])
    assert out["a_only_correct"] == 2
    assert out["b_only_correct"] == 0
    assert out["mcnemar_exact_two_sided_p"] == pytest.approx(.5)
    assert _mcnemar([True], [True])["mcnemar_exact_two_sided_p"] == 1.0
    with pytest.raises(ValueError, match="unpaired"):
        _mcnemar([True], [])


def test_correlation_fails_closed_when_constant_or_unpaired():
    assert _corr([0., 1., 0.], [0., 1., 0.]) == pytest.approx(1)
    assert _corr([0., 0.], [0., 1.]) is None
    with pytest.raises(ValueError):
        _corr([0.], [0., 1.])


def test_duplicate_and_late_or_naive_originals_rejected():
    p = corpus()
    with pytest.raises(ValueError, match="duplicate"):
        evaluate(p + [p[0]])
    invalid = [Pair(**{**p[0].__dict__, "issued_at": p[0].start_at})]
    with pytest.raises(ValueError, match="non-prematch"):
        evaluate(invalid)
    invalid_naive = [Pair(**{**p[0].__dict__, "start_at": p[0].start_at.replace(tzinfo=None)})]
    with pytest.raises(ValueError, match="naive"):
        evaluate(invalid_naive)


def test_historical_subset_stable_to_input_order_and_tracks_odd_fraction():
    a = evaluate(corpus(n=41))
    b = evaluate(list(reversed(corpus(n=41))))
    assert a["dataset_sha256"] == b["dataset_sha256"]
    for model in ("a1_surface_elo", "a2_serve_return_markov", "equal_log_odds_blend"):
        assert (a["accuracy_by_paired_cohort_selection_fraction"][model]
                == b["accuracy_by_paired_cohort_selection_fraction"][model])
    assert a["accuracy_by_paired_cohort_selection_fraction"]["a1_surface_elo"]["0.4"]["selections"] == 17


def test_database_source_runs_read_only_against_migrated_schema(migrated_postgres):
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        result = run(conn, as_of=datetime(2026, 11, 21, tzinfo=UTC))
    assert result["source_rows"] == 0
    assert result["paired_originals"] == 0
    assert result["db_writes"] is False
    assert result["status"] == "INSUFFICIENT_SETTLED_PAIRED_COHORT"
