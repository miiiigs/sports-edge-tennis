"""Source attendance is evidence of fixture/pipeline presence, not inferred wins."""
from datetime import UTC,date,datetime
import psycopg
from fastapi.testclient import TestClient
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.source_attendance import (
    reason,summarize,load_source_attendance
)

NOW=datetime(2026,11,19,1,0,tzinfo=UTC)


def test_historical_weekend_gap_reports_source_and_no_runs():
    rows=[(date(2026,11,14),17,2,15,0,0,0,0,0,0,0),
          (date(2026,11,15),63,1,62,52,0,0,0,0,0,0),
          (date(2026,11,16),73,2,71,59,0,0,0,0,0,0)]
    data=summarize(rows,first=date(2026,11,14),
                   last=date(2026,11,16),as_of=NOW)
    assert all(d["source_attendance"]=="HISTORICAL_SOURCE_AND_RUN_GAP" for d in data["days"])
    assert sum(d["canonical_fixtures_now"] for d in data["days"])==153
    assert all(d["source_observed_before_30min"]==0 for d in data["days"])


def test_zero_canonical_fixtures_is_not_proof_tennis_had_none():
    assert reason(source_fixtures=0,observed_by_30m=0,prediction_runs=0,
                  ingest_runs=0,day=date(2026,11,14),now_day=date(2026,11,19))\
        =="NO_CANONICAL_FIXTURES_NOT_PROOF_OF_EMPTY_TENNIS"


def test_current_day_cannot_be_flagged_permanent_missing():
    assert reason(source_fixtures=0,observed_by_30m=0,prediction_runs=0,
                  ingest_runs=0,day=date(2026,11,19),now_day=date(2026,11,19))\
        =="DATE_IN_PROGRESS"


def test_zero_date_is_explicit_and_only_reported_with_database_evidence(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only=on")
        out=load_source_attendance(conn,first=date(2026,11,14),
                                   last=date(2026,11,16),as_of=NOW)
    assert len(out["days"])==3
    assert all(d["canonical_fixtures_now"]==0 for d in out["days"])


def test_registered_read_only_route_and_bound():
    assert "/api/dashboard/source-attendance" in {r.path for r in app.routes}
    out=TestClient(app).get(
      "/api/dashboard/source-attendance?start_date=2026-08-01&end_date=2026-12-01")
    assert out.status_code==422
