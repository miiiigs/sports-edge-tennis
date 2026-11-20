"""Frozen validation-only Elo confidence shrinkage; no imaginary win gains."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.a1_pit_calibration_shadow import FirstIssue
from sports_edge_tennis.validation.a1_elo_margin_shrinkage_shadow import (
    SLOPES, evaluate, run, scaled_probability,
)


def dt(day:int, *, hour:int=12)->datetime:
    return datetime(2026,9,1,12,tzinfo=UTC)+timedelta(days=day,hours=hour-12)


def rows(*, flip_holdout:bool=False, delay_validation:bool=False):
    data=[]
    for day in range(40):
        for n in range(10):
            start=dt(day)
            # 70% genuine win accuracy, but raw A1 is 90% confident.
            y=int(n<7)
            if flip_holdout and day>=32:
                y=1-y
            known=start+timedelta(hours=2)
            if delay_validation and 24<=day<32:
                known=dt(35)
            data.append(FirstIssue(
                match_id=day*100+n+1,
                issued_at=start-timedelta(hours=1),
                start_at=start,
                probability=.90, outcome=y, result_known_at=known,
                tour="CHALLENGER" if n<7 else "ATP_MAIN",
                surface="HARD" if n%2 else "CLAY",
            ))
    return data


def test_elo_log_odds_slope_preserves_winner_and_respects_raw():
    assert scaled_probability(.7,1.0)==pytest.approx(.7)
    assert .5<scaled_probability(.7,.7)<.7
    assert scaled_probability(.9,1.1)>.9
    for p in (.500001,.52,.68,.83,.999):
        for slope in SLOPES:
            assert .5<scaled_probability(p,slope)<1


@pytest.mark.parametrize("p,slope",[(.49,1),(.85,0),(.8,float("nan"))])
def test_invalid_probability_or_slope_fails_closed(p,slope):
    with pytest.raises(ValueError):
        scaled_probability(p,slope)


def test_chronological_validation_selects_shrinkage_and_preserves_accuracy():
    r=evaluate(rows(),as_of=datetime(2026,11,20,tzinfo=UTC))
    assert r["status"]=="EVALUATED_RESEARCH_ONLY"
    assert r["first_issued_pre_match"]==400
    assert r["splits"]["validation_settled_known_before_holdout"]==80
    assert r["splits"]["holdout_settled"]==80
    assert r["selected_from_validation"]==.7
    assert r["holdout_brier_improvement"]>0
    assert r["holdout_log_loss_improvement"]>0
    assert r["holdout_accuracy_difference"]==0
    assert r["holdout_date_cluster_ci"]["status"]=="RESEARCH_ONLY_DATE_CLUSTER_BOOTSTRAP"
    assert r["release_eligible"] is False
    assert r["db_writes"] is False
    assert r["live_model_or_manager_changed"] is False
    assert r["not_a_new_weighted_elo_model"] is True
    assert r["holdout_raw"]["scored"]==r["holdout_selected"]["scored"]==80
    assert r["per_tour"]["CHALLENGER"]["selected"]["scored"]>0


def test_holdout_outcomes_never_influence_preselected_slope():
    cutoff=datetime(2026,11,20,tzinfo=UTC)
    a=evaluate(rows(),as_of=cutoff)
    b=evaluate(rows(flip_holdout=True),as_of=cutoff)
    assert a["selected_from_validation"]==b["selected_from_validation"]
    assert a["dataset_sha256"]!=b["dataset_sha256"]
    assert a["splits"]==b["splits"]


def test_validation_results_known_after_holdout_cutoff_are_not_used():
    r=evaluate(rows(delay_validation=True),as_of=datetime(2026,11,20,tzinfo=UTC))
    assert r["status"]=="INSUFFICIENT_NONLEAKY_VALIDATION_OR_HOLDOUT"
    assert r["splits"]["validation_settled_known_before_holdout"]==0
    assert "holdout_selected" not in r


def test_research_rejects_duplicate_canonical_fixtures():
    original=rows()
    with pytest.raises(ValueError,match="repeated canonical"):
        evaluate(original+[original[0]],as_of=datetime(2026,11,20,tzinfo=UTC))


def test_read_only_sql_schema_is_compatible_with_migrations(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as c:
        result=run(c,as_of=datetime(2026,11,20,tzinfo=UTC))
    assert result["source_rows"]==0
    assert result["status"]=="INSUFFICIENT_UNIQUE_DATES"
    assert result["db_writes"] is False
