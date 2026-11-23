"""Read-only actual-DB A1 shuffled-label adapter never fabricates PIT clocks."""
from datetime import UTC, datetime, timedelta
import pytest
from sports_edge_tennis.validation.a1_realdata_negative_control import (
    SQL, read_records,
)

CUT = datetime(2026,11,4,tzinfo=UTC)
END = datetime(2026,11,21,tzinfo=UTC)
ASOF = datetime(2026,11,23,tzinfo=UTC)


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
    def execute(self, sql, params=None):
        self.calls.append((sql,params))
        return self
    def fetchall(self):
        return self.rows


def row(mid=101, *, start=None, known=None, version=None, winner=1):
    start = start or datetime(2026,11,10,12,tzinfo=UTC)
    known = known or start+timedelta(hours=3)
    version = version or start-timedelta(hours=2)
    return (2026,mid,101,202,"HARD","CHALLENGER","CHALLENGER",3,
            False,start,"COMPLETED",winner,known,known,known,
            version,version,version)


def test_query_restricts_both_result_and_fixture_ingestion_temporally():
    assert "r.ingested_at <= %(as_of)s" in SQL
    assert "mv.ingested_at <= CASE" in SQL
    assert "mv.match_start_at - INTERVAL '30 minutes'" in SQL
    assert "r.result_visible_at >= v.match_start_at" in SQL
    assert "r.result_visible_at <= %(cutoff)s" in SQL


def test_read_only_real_fixture_converts_and_preserves_clocks():
    conn=Connection([row()])
    records,digest=read_records(conn,train_cutoff=CUT,
                                evaluation_end=END,as_of=ASOF)
    assert len(records)==1
    assert records[0].match_id==101
    assert records[0].winner_side==1
    assert len(digest)==64
    assert conn.calls[0][0]=="SET default_transaction_read_only=on"
    assert conn.calls[1][1]["cutoff"]==CUT


def test_poststart_or_ingested_late_evaluation_fixture_refused():
    start=datetime(2026,11,10,12,tzinfo=UTC)
    conn=Connection([row(version=start-timedelta(minutes=20))])
    with pytest.raises(ValueError,match="post-issue"):
        read_records(conn,train_cutoff=CUT,evaluation_end=END,as_of=ASOF)


def test_historical_training_version_after_cutoff_refused():
    start=datetime(2026,10,20,12,tzinfo=UTC)
    known=datetime(2026,10,22,12,tzinfo=UTC)
    version=datetime(2026,11,5,12,tzinfo=UTC)
    with pytest.raises(ValueError,match="post-cutoff"):
        read_records(Connection([row(start=start,known=known,version=version)]),
                     train_cutoff=CUT,evaluation_end=END,as_of=ASOF)


def test_duplicate_match_ids_refused():
    with pytest.raises(ValueError,match="duplicate"):
        read_records(Connection([row(),row()]),train_cutoff=CUT,
                     evaluation_end=END,as_of=ASOF)


def test_time_order_guard():
    with pytest.raises(ValueError,match="cutoff"):
        read_records(Connection([]),train_cutoff=END,
                     evaluation_end=CUT,as_of=ASOF)
