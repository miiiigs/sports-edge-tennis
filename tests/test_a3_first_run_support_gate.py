"""Original-run A3 diagnostic classification, temporal matching and no prediction."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG, temporal_weight,
)
from sports_edge_tennis.validation.a3_first_run_support_gate import (
    _support_stratum, query, run, summarize,
)

START=datetime(2026,10,15,tzinfo=UTC)
END=datetime(2026,11,21,tzinfo=UTC)
ASOF=datetime(2026,11,21,3,30,tzinfo=UTC)
MATCH=datetime(2026,11,17,12,tzinfo=UTC)


def row(mid=1,*,diag=True,issued_minutes=90,
        status="ABSTAINED",reason="INSUFFICIENT_MATCHUP_SUPPORT",
        surface="HARD",raw=0,eff=0,shared=0,eligible=0,q=0,
        identity="RESOLVED",tour="ATP_MAIN"):
    issued=MATCH-timedelta(minutes=issued_minutes)
    return (
        mid,mid+200,issued-timedelta(minutes=2),issued,
        MATCH,tour,surface,status,reason,
        issued-timedelta(minutes=1) if diag else None,
        identity if diag else None,
        raw if diag else None,Decimal(str(eff)) if diag else None,
        shared if diag else None,eligible if diag else None,
        Decimal(str(q)) if diag else None,
        "NONE" if diag else None,reason if diag else None,
        status if diag else None,0 if diag else None,
        0 if diag else None,0 if diag else None,0 if diag else None,
        issued-timedelta(seconds=30) if diag else None,
    )


def report(rows):
    return summarize(rows,start=START,end=END,as_of=ASOF)


def test_empty_and_missing_original_diagnostics_not_reconstructed():
    o=report([row(1,diag=False)])
    assert o["overall"]["valid_first_run_targets"]==1
    assert o["overall"]["NO_SAME_RUN_DIAGNOSTIC"]==1
    assert o["matched_diagnostic_classification"]=={}
    assert o["no_new_predictions"] is True
    assert o["selection_denom"].startswith("ORIGINAL_ON_TIME")


def test_raw_shared_relationship_filtered_vs_truly_absent():
    o=report([row(1,shared=10,eligible=0,q=0),
              row(2,shared=0,eligible=0,q=0)])
    z=o["matched_diagnostic_classification"]
    assert z["RAW_COMMON_OPPONENTS_ALL_FILTERED_OUT"]==1
    assert z["NO_DIRECT_OR_SHARED_OPPONENT_HISTORY"]==1
    assert o["support_gap_counters"]["SOME_RAW_SHARED_OPPONENTS_EXCLUDED_BY_EDGE_OR_DECAY"]==1


def test_effective_sample_threshold_not_bypassed_by_raw_matches():
    o=report([row(1,raw=3,eff="2.1"),
              row(2,raw=4,eff="3.1")])
    v=o["matched_diagnostic_classification"]
    assert v["DIRECT_MATCHES_BUT_LOW_EFFECTIVE_SAMPLE"]==1
    assert v["DIRECT_THRESHOLD_POSSIBLE_REQUIRES_SIGNAL_CHECK"]==1


def test_common_opponent_q_below_gate_is_separately_reported():
    o=report([row(1,shared=9,eligible=6,q=5),
              row(2,shared=10,eligible=8,q=16)])
    assert o["matched_diagnostic_classification"]["FIVE_COMMON_OPPONENTS_BUT_Q_BELOW_EIGHT"]==1
    assert o["matched_diagnostic_classification"]["NETWORK_THRESHOLD_POSSIBLE_REQUIRES_SIGNAL_CHECK"]==1
    assert o["support_gap_counters"]["QUALIFIED_NETWORK_COUNT_BUT_LOW_Q"]==1


def test_unknown_surface_and_challenger_are_explicit():
    o=report([row(1,surface=None,reason="UNKNOWN_SURFACE",tour="CHALLENGER")])
    assert o["tour"]["CHALLENGER"]["valid_first_run_targets"]==1
    assert o["support_gap_counters"]["UNKNOWN_SURFACE_AS_OF_ISSUE"]==1
    assert o["overall"]["A3_ABSTAINED"]==1


def test_single_older_common_opponent_event_can_not_reach_effective_one():
    assert DEFAULT_CONFIG.network_edge_raw_support==1
    assert DEFAULT_CONFIG.network_edge_effective_support==Decimal("1")
    weight=temporal_weight(MATCH-timedelta(days=1),MATCH)
    assert Decimal("0")<weight<Decimal("1")
    assert weight<DEFAULT_CONFIG.network_edge_effective_support


def test_status_mismatch_never_used_and_duplicate_ids_rejected():
    r=list(row(1))
    r[18]="PREDICTED"
    z=report([tuple(r)])
    assert z["overall"]["MATCHED_DIAGNOSTIC"]==0 if "MATCHED_DIAGNOSTIC" in z["overall"] else True
    assert z["tour"]["ATP_MAIN"]["STATUS_MISMATCH_CANNOT_USE"]==1
    with pytest.raises(ValueError,match="duplicate first-run"):
        report([row(1),row(1)])


def test_late_original_prediction_excluded_and_created_future_rejected():
    o=report([row(1,issued_minutes=4)])
    assert o["overall"]["unsafe_first_run_excluded"]==1
    assert "valid_first_run_targets" not in o["overall"]
    r=list(row(2))
    r[23]=ASOF+timedelta(hours=1)
    with pytest.raises(ValueError,match="future or ambiguous diagnostic"):
        report([tuple(r)])


def test_impossible_graph_data_fail_closed():
    with pytest.raises(ValueError,match="impossible"):
        report([row(1,shared=2,eligible=4)])


def test_query_time_fences_and_no_mutation(migrated_postgres):
    sql=query()
    assert "d.evaluated_at>=fr.run_as_of" in sql
    assert "d.evaluated_at<=fr.issued_at" in sql
    assert "mv.ingested_at<=fr.issued_at" in sql
    assert "pr.experiment_id IS NULL" in sql
    assert "INSERT" not in sql and "UPDATE " not in sql
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        out=run(conn,start=START,end=END,as_of=ASOF)
    assert out["overall"]=={}
    assert out["matched_diagnostic_classification"]=={}
    assert out["no_data_writes"] is True
