from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES
from sports_edge_tennis.validation.manager_c_original_source import (
    TRAIN_START, VALIDATION_END, HISTORICAL_AS_OF, decode, grading,
    historical, read, summarize, make_manifest, original_sql,
)
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY
from sports_edge_tennis.validation.manager_c_forward_feature_ablation import _stats


def raw(mid, *, status="COMPLETED", winner=1, qualifying=False, late=False,
        known_delta=timedelta(hours=3)):
    start=datetime(2026,11,10,12,tzinfo=UTC)
    issue=start-timedelta(hours=2)
    return [(mid,mid+100,issue,start,"CHALLENGER",1,code,"PREDICTED",1,.72,
             issue-timedelta(minutes=1),None,None,None,None,status,winner,
             start+known_delta,mid*10+i,issue+timedelta(seconds=int(late)),
             "model","feature",1,mid+200,"HARD",qualifying,True,True)
            for i,code in enumerate(CODES[:2])]


def parse(rows):
    return decode(rows,start=TRAIN_START,end=VALIDATION_END,as_of=HISTORICAL_AS_OF)


def test_original_source_metadata_and_grading():
    rows=(raw(1)+raw(2,winner=2)+raw(3,status="WALKOVER",winner=None)
          +raw(4,status=None,winner=None)+raw(5,status="CANCELLED",winner=None,
                                                qualifying=True))
    d=parse(rows)
    summary=summarize(d["records"],d)
    assert summary["wlpv"]=={"W":1,"L":1,"P":1,"V":2}
    assert summary["cancelled_in_void"]==1
    assert summary["available"]["main_draw_verified"]==4
    assert summary["available"]["original_agent_probability_pair"]==5
    assert summary["available"]["any_agent_both_rating_samples_visible_unlinked"]==5
    assert summary["available"]["all_original_agent_version_ids"]==0
    assert "min_player_prior_match_count" in summary["not_proven_original_feature_values"]
    metrics=_stats(d["records"],{i:.72 for i in range(1,6)},5)
    assert (metrics["wins"],metrics["losses"],metrics["pending"],metrics["void"])==(1,1,1,2)
    assert metrics["settled"]==2


def test_late_original_lock_rejected():
    with pytest.raises(ValueError,match="late original prediction lock"):
        parse(raw(1,late=True))


def test_void_known_before_original_issue_is_unresolved():
    d=parse(raw(1,status="WALKOVER",winner=None,known_delta=-timedelta(hours=3)))
    assert d["records"][0]["research_grade"]=="P"


def test_original_sql_never_treats_unlinked_rating_rows_as_original_features():
    sql=original_sql()
    assert "p.prediction_run_id=o.rid" in sql
    assert "mv.ingested_at<=o.issued" in sql
    assert "rs.created_at<=p.as_of" in sql
    assert "rs.experiment_id IS NULL" in sql
    assert "min_player_prior_match_count" not in sql


def test_past_boundary_rejected_before_training():
    with pytest.raises(ValueError,match="future"):
        make_manifest({}, {}, boundary=VALIDATION_END,now=HISTORICAL_AS_OF,source_sha="a"*40)


def test_empty_migrated_postgres_original_read_only(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        data=read(conn,start=TRAIN_START,end=VALIDATION_END,as_of=HISTORICAL_AS_OF)
        assert data["records"]==[]
        assert len(data["full_source_sha256"])==64
        assert conn.execute("SELECT 1").fetchone()==(1,)
    with psycopg.connect(migrated_postgres) as conn:
        with pytest.raises(ValueError,match="autocommit"):
            read(conn,start=TRAIN_START,end=VALIDATION_END,as_of=HISTORICAL_AS_OF)
