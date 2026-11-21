"""Guard chronological selective prediction research against look-ahead."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.a1_a2_blend_shadow import Pair
from sports_edge_tennis.validation.a1_a2_weight_selection_shadow import ObservedPair
from sports_edge_tennis.validation.a1_a2_validation_frozen_selection import (
    _freeze_cutoff, evaluate, run,
)

BASE=datetime(2026,9,1,12,tzinfo=UTC)
CUTOFF=datetime(2026,11,21,3,30,tzinfo=UTC)


def corpus(*, flip_later:bool=False, late_validation:bool=False,
           add_ungraded:bool=False)->list[ObservedPair]:
    rows=[]
    for day in range(48):
        start=BASE+timedelta(days=day)
        for n in range(10):
            y=int(n%2==0)
            if flip_later and day>=36:
                y=1-y
            c1=.55+.04*(n%8)
            c2=.51+.03*(n%8)
            known=start+timedelta(hours=3)
            if late_validation and 24<=day<36:
                known=BASE+timedelta(days=44)
            ungraded=add_ungraded and day>=36 and n==0
            rows.append(ObservedPair(
                pair=Pair(
                    match_id=day*100+n+1,
                    issued_at=start-timedelta(hours=2),
                    start_at=start,
                    tour="ATP_TOUR" if n<3 else "CHALLENGER",
                    surface="HARD",cluster_id=str(day//4),
                    a1_p1=c1 if n%2==0 else 1-c1,
                    a2_p1=c2 if n%2==0 else 1-c2,
                    winner_p1=None if ungraded else y,
                ),
                result_known_at=None if ungraded else known,
            ))
    return rows


def test_frozen_earlier_cutoffs_never_change_after_later_winners_flip():
    a=evaluate(corpus(),as_of=CUTOFF)
    b=evaluate(corpus(flip_later=True),as_of=CUTOFF)
    assert a["status"]=="VALIDATION_FROZEN_SHADOW_HISTORICAL_HOLDOUT"
    assert a["validation_outcomes_known_before_holdout"]==120
    assert a["holdout_settled"]==120
    assert a["full_eligible_match_coverage_verified"] is False
    assert a["thresholds_derived_without_future_holdout_data"] is True
    assert a["untouched_independent_holdout"] is False
    assert a["release_eligible"] is False
    for frac in ("0.25","0.4","0.5","0.75","1.0"):
        for name in ("a1_surface_elo","a2_serve_return_markov","equal_log_odds_blend"):
            f=a["validation_frozen_curves"][frac]["models"][name]
            g=b["validation_frozen_curves"][frac]["models"][name]
            assert f["cutoff_frozen_from_validation"]==g["cutoff_frozen_from_validation"]
            assert f["issued_selected"]==g["issued_selected"]
    assert (a["holdout_full_models"]["a1_surface_elo"]["winner_accuracy"]
            != b["holdout_full_models"]["a1_surface_elo"]["winner_accuracy"])


def test_future_known_validation_results_excluded_from_selection_training():
    r=evaluate(corpus(late_validation=True),as_of=CUTOFF)
    assert r["status"]=="INSUFFICIENT_STRICT_VALIDATION_OR_HOLDOUT"
    assert r["validation_outcomes_known_before_holdout"]==0
    assert "validation_frozen_curves" not in r


def test_ungraded_holdout_is_in_issuance_coverage_but_never_scored():
    r=evaluate(corpus(add_ungraded=True),as_of=CUTOFF)
    assert r["status"]=="VALIDATION_FROZEN_SHADOW_HISTORICAL_HOLDOUT"
    assert r["holdout_issued"]==120
    assert r["holdout_settled"]==108
    assert r["holdout_ungraded"]==12
    full=r["validation_frozen_curves"]["1.0"]["models"]["a1_surface_elo"]
    assert full["issued_selected"]==120
    assert full["settled_selected"]==108
    assert full["ungraded_selected"]==12
    assert full["wins"]+full["losses"]==108
    assert full["issued_paired_cohort_coverage"]==1
    assert full["full_eligible_fixture_coverage"] is None


def test_original_probability_ties_cannot_be_arbitrarily_split():
    obs=corpus()
    limited=obs[:20]
    cutoff=_freeze_cutoff(limited,"a1_surface_elo",.4)
    # With ten repeated confidence levels, ties are naturally shared.
    assert cutoff is not None
    c=[o for o in limited if max(o.pair.a1_p1,1-o.pair.a1_p1)>=cutoff-1e-12]
    assert len(c)>=8


def test_no_duplicate_or_future_outcomes_are_allowed():
    data=corpus()
    with pytest.raises(ValueError,match="duplicate"):
        evaluate(data+[data[0]],as_of=CUTOFF)
    bad=replace(data[0],result_known_at=CUTOFF+timedelta(days=1))
    with pytest.raises(ValueError,match="unsafe result visibility"):
        evaluate([bad],as_of=CUTOFF)
    late=replace(data[0],pair=replace(data[0].pair,issued_at=data[0].pair.start_at+timedelta(minutes=1)))
    with pytest.raises(ValueError,match="after match start"):
        evaluate([late],as_of=CUTOFF)


def test_reordering_inputs_preserves_cohort_and_cutoffs():
    a=evaluate(corpus(),as_of=CUTOFF)
    b=evaluate(list(reversed(corpus())),as_of=CUTOFF)
    assert a["dataset_sha256"]==b["dataset_sha256"]
    assert a["validation_outcomes_known_before_holdout"]==b["validation_outcomes_known_before_holdout"]
    for frac in ("0.25","0.4","0.5","0.75","1.0"):
        for model in ("a1_surface_elo","a2_serve_return_markov","equal_log_odds_blend"):
            x=a["validation_frozen_curves"][frac]["models"][model]
            y=b["validation_frozen_curves"][frac]["models"][model]
            assert x["cutoff_frozen_from_validation"]==y["cutoff_frozen_from_validation"]
            assert x["wins"]==y["wins"]
            assert x["losses"]==y["losses"]


def test_empty_migrated_database_is_unscored_and_read_only(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        r=run(conn,as_of=CUTOFF)
    assert r["source_rows"]==0
    assert r["status"]=="INSUFFICIENT_CHRONOLOGICAL_MATCH_DATES"
    assert r["db_writes"] is False
