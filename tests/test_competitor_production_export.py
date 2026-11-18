"""Live-original first-issue export contract for independent competitor comparisons."""
from datetime import UTC,datetime,timedelta
from decimal import Decimal
import psycopg
from sports_edge_tennis.validation import competitor_production_export as x
from sports_edge_tennis.validation.competitor_forward_audit import compare


def row(mid=1,side=2,prob="0.73",status="COMPLETED",winner=2,
        start=None,finalized=None,tour="ATP_MAIN",qual=False):
    start=start or datetime(2026,11,18,18,tzinfo=UTC)
    finalized=finalized or datetime(2026,11,18,15,tzinfo=UTC)
    return (mid,"MANAGER_CONSENSUS",side,Decimal(prob),finalized,start,
            tour,qual,11,12,120,"tennis_manager_consensus","v2",status,winner)


def test_selected_second_player_is_complemented_and_winner_orientation_correct():
    fx,fc,rej=x.normalize([row()])
    assert rej=={}
    assert len(fx)==len(fc)==1
    assert fc[0]["p_player1"]=="0.27"
    assert fx[0]["winner_side"]=="2"
    report=compare(fx,fc)
    assert report["settled_unique_matches"]==1
    assert report["per_tour"][0]["settled_evaluated"]==1
    assert report["per_tour"][0]["hit_rate"]==1


def test_retired_void_and_missing_results_excluded_from_brier():
    fx,fc,rej=x.normalize([row(1,status="RETIRED"),row(2,status=None,winner=None)])
    assert rej=={}
    report=compare(fx,fc)
    assert report["settled_unique_matches"]==0
    assert report["pending_or_void_matches"]==2


def test_late_or_missing_first_issuance_rejected_even_with_result():
    start=datetime(2026,11,18,18,tzinfo=UTC)
    close=datetime(2026,11,18,17,45,tzinfo=UTC)
    fx,fc,rej=x.normalize([row(finalized=close,start=start)])
    assert not fx and not fc and rej["NO_AUTHENTIC_30MIN_PREMATCH_ISSUANCE"]==1


def test_canonical_identity_duplicates_or_provisional_data_fail_closed():
    import pytest
    with pytest.raises(ValueError,match="duplicate"):
        x.normalize([row(),row()])
    fx,fc,rej=x.normalize([row(prob="1")])
    assert not fx and rej["INVALID_PROBABILITY_OR_POLICY"]==1


def test_read_only_query_compiles_on_migrated_database(migrated_postgres):
    now=datetime(2026,11,18,22,tzinfo=UTC)
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        output=x.run(conn,as_of=now,start_at=now-timedelta(days=7),end_at=now)
    assert output["fixtures"]==[]
    assert output["report"]["export_stats"]["external_vendor_matches"]==0
    assert "pr.prediction_finalized_at <= %(as_of)s" in x.source_sql()
    assert "mv0.as_of <= p.picked_at" in x.source_sql()
