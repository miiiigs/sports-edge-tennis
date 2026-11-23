"""Pre-match two-way bookmaker baseline must never use later/closing prices."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.pit_bookmaker_model_picks_benchmark import (
    MarketPair, evaluate, normalize, run, source_sql,
)

START=datetime(2026,10,20,17,tzinfo=UTC)
ISSUED=START-timedelta(hours=2)
ASOF=datetime(2026,11,20,21,30,tzinfo=UTC)


def record(mid=1, *,odds_at=None, odds1=1.6,odds2=2.5,
           order_conflict=False, status="COMPLETED",start=START):
    # Identical select-column order to the source SQL, including A1 and A2.
    return (mid,2000,ISSUED,1,.72,"MANAGER_CONSENSUS",9,
            "CHALLENGER","HARD",start,101,202,order_conflict,
            status,1,start+timedelta(hours=3),
            1,.75,1,.65,"SHARP","PINNACLE",odds1,odds2,
            odds_at or ISSUED-timedelta(minutes=2),0)


def test_sql_uses_exact_original_cutoff_not_hindsight_and_same_book():
    sql=source_sql()
    assert "aligned_production_model_picks_cte" not in sql
    for part in (
        "o.source_observed_at<=p.picked_at",
        "o.as_of<=p.picked_at",
        "o.ingested_at<=p.picked_at",
        "v.ingested_at<=p.picked_at",
        "prior.ingested_at<=p.picked_at",
        "o.market_type='MATCH_WINNER'",
        "o.selection_type IN ('PLAYER_1','PLAYER_2')",
        "o.source_system,o.bookmaker_code,o.selection_type",
        "HAVING count(*)=2",
        "FILTER (WHERE status='OPEN')=2",
        "interval '5 minutes'",
        "interval '6 hours'",
        "FROM aligned_model_picks",
        "GREATEST(as_of,source_observed_at,ingested_at)"
    ):
        assert part in sql


def test_pit_quote_requires_same_book_two_sides_preissued_not_closed_price():
    pairs,summary,issues=normalize([record()],as_of=ASOF)
    assert len(pairs)==1
    assert summary["settled_pit_market_pairs"]==1
    assert pairs[0].market_p1==pytest.approx((1/1.6)/(1/1.6+1/2.5))
    assert pairs[0].picked_p1==pytest.approx(.72)
    assert pairs[0].a1_p1==pytest.approx(.75)
    assert issues=={}
    bad=replace(pairs[0],odds1=1.01,odds2=1.01)
    assert bad.favourite_confidence==.5  # never arbitrary P1 favourite


def test_quote_at_or_after_prediction_is_rejected_even_when_pre_match():
    x=list(record())
    x[-2]=ISSUED+timedelta(seconds=1)
    pairs,_,issues=normalize([tuple(x)],as_of=ASOF)
    assert pairs==[]
    assert issues["INVALID_PAIR_TIMESTAMP_OR_STALENESS"]==1


def test_player_order_change_and_late_prediction_blocked():
    a=list(record(1));a[12]=True
    b=list(record(2));b[2]=START-timedelta(minutes=10)
    pair,_,issues=normalize([tuple(a),tuple(b)],as_of=ASOF)
    assert pair==[]
    assert issues["HISTORIC_PLAYER_ORDER_CONFLICT"]==1
    assert issues["LATE_MODEL_PICK"]==1


def test_missing_two_sided_quote_and_noncompleted_do_not_count_wins():
    n=list(record(1));n[22]=None;n[23]=None;n[24]=None
    v=list(record(2));v[13]="DEFAULTED";v[14]=None
    pairs,summary,issues=normalize([tuple(n),tuple(v)],as_of=ASOF)
    assert len(pairs)==1
    assert pairs[0].winner_p1 is None
    assert summary["pending_or_void_pit_picks"]==1
    assert summary["settled_pit_picks"]==1
    assert issues["NO_TIMELY_TWO_SIDED_SAME_BOOK_QUOTE"]==1


def test_abnormal_booksum_rejected_before_benchmarking():
    pairs,_,issues=normalize([record(odds1=1.02,odds2=1.02)],as_of=ASOF)
    assert pairs==[]
    assert issues["ABNORMAL_TWO_WAY_BOOKSUM"]==1


def test_same_matched_cohort_scores_market_and_ai_and_coverage():
    common=normalize([record()],as_of=ASOF)[0][0]
    xs=[]
    for n in range(100):
        p=(.52+n*.0035)
        # book favourite higher p, result matches favourite 70% of time
        y=int(n%10<7)
        xs.append(replace(common,match_id=n+1,edition_id=n//8+1,
                          odds1=1.02/p,odds2=1.02/(1-p),
                          winner_p1=y,
                          picked_p1=.72,
                          a1_p1=.75,a2_p1=.65))
    result=evaluate(
        xs,{"first_issued_model_picks":100,"pit_eligible_model_picks":100,
            "settled_pit_picks":100},{},as_of=ASOF
    )
    assert result["status"]=="EVALUATED_HISTORICAL_SAME_CUTOFF_MARKET"
    assert result["same_fixture_market_vs_original"]["market"]["n"]==100
    assert result["same_fixture_market_vs_original"]["original_model"]["n"]==100
    assert result["same_fixture_market_vs_original"]["a1"]["n"]==100
    assert result["top_market_strength_coverage"]["0.5"]["n"]==50
    assert result["top_market_strength_coverage"]["0.4"]["n"]==40
    assert result["top_market_strength_coverage"]["0.25"]["n"]==25
    assert result["paired_cluster_uncertainty"]["edition_count"]>=4
    assert result["market_price_only_if_ingested_before_original_pick"] is True
    assert result["historical_closing_odds_used"] is False
    assert result["full_market_eligible_census_done"] is False
    assert result["research_only"] is True
    assert result["release_eligible"] is False
    assert result["db_writes"] is False


def test_migrated_database_query_empty_does_not_invent_bookmaker_accuracy(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        result=run(conn,as_of=ASOF)
    assert result["status"]=="INSUFFICIENT_PIT_MARKET_MATCHES"
    assert result["sql_first_issued_rows"]==0
    assert result["db_writes"] is False
