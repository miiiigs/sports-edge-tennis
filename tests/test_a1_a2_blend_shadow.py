"""Elo+Markov shadow never mixes fixtures or late/void truth."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.a1_a2_blend_shadow import (
    Pair, candidate_probabilities, evaluate, normalize, run, source_sql,
)


def _d(day=1, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def _row(mid=1, status="COMPLETED", winner=1, a1_side=1, a1_p=.7,
         a2_side=1, a2_p=.9, issued=None, start=None, known=None):
    issued=issued or _d(1,8)
    start=start or _d(1,12)
    known=known or start+timedelta(hours=2)
    return (mid, issued, issued, issued, start, "CHALLENGER","HARD",123,
            a1_side,a1_p,a2_side,a2_p,status,winner,known)


def test_oriented_probabilities_and_50_50_no_arbitrary_player_one_bet():
    rows,_=normalize([
        _row(mid=1,a1_side=1,a1_p=.8,a2_side=2,a2_p=.8)
    ],as_of=_d(3))
    p=candidate_probabilities(rows[0])
    assert abs(p["a1_surface_elo"]-.8)<1e-9
    assert abs(p["a2_serve_return_markov"]-.2)<1e-9
    assert abs(p["equal_linear_blend"]-.5)<1e-9
    assert abs(p["equal_log_odds_blend"]-.5)<1e-9


def test_skip_invalid_pit_results_and_defaulted_even_if_winner_attributed():
    rs,rej=normalize([
        _row(mid=1,status="DEFAULTED",winner=1),
        _row(mid=2,status="COMPLETED",winner=2),
        _row(mid=3,status="COMPLETED",winner=1,known=_d(1,11)),
        _row(mid=4,issued=_d(1,11),start=_d(1,11)),
    ],as_of=_d(3))
    assert [(x.match_id,x.winner_p1) for x in rs]==[(1,None),(2,0)]
    assert rej["UNSAFE_RESULT_TIMELINE_OR_WINNER"]==1
    assert rej["LATE_OR_INVALID_PAIRED_ISSUANCE"]==1


def test_query_pairs_same_prediction_run_and_excludes_experiments():
    sql=source_sql()
    assert "a2.prediction_run_id = a1.prediction_run_id" in sql
    assert "DISTINCT ON (a1.match_id)" in sql
    assert "pr.experiment_id IS NULL" in sql
    assert "a1.experiment_id IS NULL" in sql
    assert "a2.experiment_id IS NULL" in sql
    assert "pr.prediction_finalized_at" in sql
    assert "v.ingested_at <= fp.a1_as_of" in sql
    assert "mro.ingested_at <= %(as_of)s" in sql
    assert "outcome_status = 'COMPLETED'" not in sql


def _pairs(days=40, markov_better=False):
    data=[]
    for i in range(days):
        start=datetime(2026,9,1,12,tzinfo=UTC)+timedelta(days=i)
        for n in range(4):
            y=1 if n%2==0 else 0
            a1=.66 if y else .34
            a2=(.80 if y else .20) if markov_better else (
                .51 if y else .49
            )
            data.append(Pair(
                match_id=i*100+n,issued_at=start-timedelta(hours=2),
                start_at=start,tour="ATP_TOUR" if n<2 else "CHALLENGER",
                surface="HARD",cluster_id=str(i//7),
                a1_p1=a1,a2_p1=a2,winner_p1=y,
            ))
    return data


def test_research_blend_has_paired_outcomes_no_vendor_or_live_rule_changes():
    rep=evaluate(_pairs(markov_better=True))
    assert rep["status"]=="EVALUATED_HISTORICAL_PAIRED_SHADOW"
    assert rep["holdout_paired_scored"]==32
    assert rep["holdout_models"]["a1_surface_elo"]["scored_probabilities"]==32
    assert rep["holdout_models"]["equal_log_odds_blend"]["scored_probabilities"]==32
    assert rep["holdout_models"]["equal_log_odds_blend"]["brier"] < rep["holdout_models"]["a1_surface_elo"]["brier"]
    assert rep["vs_a1_positive_means_lower_error"]["equal_log_odds_blend"]["paired_brier_improvement"] > 0
    assert rep["competitor_vendor_data_imported"] is False
    assert rep["production_rules_modified"] is False
    assert rep["release_eligible"] is False


def test_no_distinct_days_means_no_claimed_model_comparison():
    one=_pairs(days=1)
    rep=evaluate(one)
    assert rep["status"]=="INSUFFICIENT_DISTINCT_MATCH_DATES"
    assert "holdout_models" not in rep


def test_read_only_query_schema_compatible_with_migrated_postgres(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        rep=run(conn,as_of=datetime(2026,11,20,tzinfo=UTC))
    assert rep["source_rows"]==0
    assert rep["db_writes"] is False
    assert rep["status"]=="INSUFFICIENT_DISTINCT_MATCH_DATES"
