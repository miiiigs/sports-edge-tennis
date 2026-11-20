"""Chronological weight selection must never fit from later outcomes."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.a1_a2_blend_shadow import Pair
from sports_edge_tennis.validation.a1_a2_weight_selection_shadow import (
    WEIGHTS, ObservedPair, _weighted, evaluate, run
)


START=datetime(2026,9,1,12,tzinfo=UTC)
ASOF=datetime(2026,11,20,12,tzinfo=UTC)


def fixture(day:int,num:int,*,flip_holdout:bool=False,late_validation:bool=False)->ObservedPair:
    start=START+timedelta(days=day)
    y=int(num<14)
    if flip_holdout and day>=40:
        y=1-y
    # A1 has strong 80%-correct synthetic forecast; A2 is inverted.
    p1=.8 if num<14 else .2
    known=start+timedelta(hours=2)
    if late_validation and 30<=day<40:
        known=START+timedelta(days=47)
    return ObservedPair(
        pair=Pair(
            match_id=1+day*100+num,
            issued_at=start-timedelta(hours=1),
            start_at=start,
            tour="CHALLENGER" if num<16 else "ATP_TOUR",
            surface="HARD" if num%2 else "CLAY",
            cluster_id=str(day//3),
            a1_p1=p1,a2_p1=1-p1,winner_p1=y,
        ),result_known_at=known
    )


def corpus(**kwargs):
    return [fixture(day,num,**kwargs) for day in range(50) for num in range(20)]


def test_search_weights_are_fixed_and_preserve_orientation():
    item=fixture(1,1).pair
    assert WEIGHTS==(0.50,0.75,0.25,1.00,0.00)
    assert _weighted(item,1.0)==pytest.approx(item.a1_p1)
    assert _weighted(item,0.0)==pytest.approx(item.a2_p1)
    assert _weighted(item,.5)==pytest.approx(.5)
    with pytest.raises(ValueError):
        _weighted(item,.83)


def test_chronological_validator_chooses_a1_on_synthetic_and_never_claims_live_release():
    r=evaluate(corpus(),as_of=ASOF)
    assert r["status"]=="EXPLORATORY_HISTORICAL_HOLDOUT_SCORED"
    assert r["first_coissued_pairs"]==1000
    assert r["splits"]["validation_pre_holdout_settled"]==200
    assert r["splits"]["holdout_settled"]==200
    assert r["chosen_weight_a1"]==1.0
    assert r["selected_vs_fixed50"]["brier_improvement"]>0
    assert r["selected_vs_fixed50"]["winner_coverage_difference"]>0
    assert r["holdout_models"][str(1.0)]["winner_accuracy"]==1.0
    assert r["per_tour"]["ATP_TOUR"]["n"]>0
    assert r["research_only"] and not r["db_writes"]
    assert not r["release_eligible"]
    assert r["historical_holdout_previously_inspected"] is True


def test_holdout_winner_labels_cannot_change_selected_weight():
    a=evaluate(corpus(),as_of=ASOF)
    b=evaluate(corpus(flip_holdout=True),as_of=ASOF)
    assert a["chosen_weight_a1"]==b["chosen_weight_a1"]
    assert a["dataset_sha256"]!=b["dataset_sha256"]


def test_late_validation_outcomes_excluded_from_weight_fitting():
    r=evaluate(corpus(late_validation=True),as_of=ASOF)
    assert r["status"]=="INSUFFICIENT_STRICT_VALIDATION_OR_HOLDOUT"
    assert r["splits"]["validation_pre_holdout_settled"]==0


def test_duplicate_canonical_first_issued_pair_rejected():
    data=corpus()
    with pytest.raises(ValueError,match="duplicate"):
        evaluate(data+[data[1]],as_of=ASOF)


def test_migrated_postgres_empty_stays_unscored_and_read_only(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        result=run(conn,as_of=ASOF)
    assert result["source_rows"]==0
    assert result["status"]=="INSUFFICIENT_DATES"
    assert result["db_writes"] is False
