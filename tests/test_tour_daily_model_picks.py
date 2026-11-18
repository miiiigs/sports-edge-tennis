"""Original first-issued daily split must preserve W/L/VOID/PENDING and strata."""
from datetime import UTC,date,datetime
import psycopg
import pytest
from fastapi.testclient import TestClient
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.tour_daily_model_picks import (
    summarize_daily,daily_tour_sql,load_tour_daily
)

DAY=date(2026,11,17)
NOW=datetime(2026,11,18,4,0,tzinfo=UTC)


def row(mid,tour="CHALLENGER",qualifying=False,pick_type="MANAGER_CONSENSUS",
        selected=1,status="COMPLETED",winner=1):
    return mid,DAY,tour,qualifying,pick_type,selected,status,winner


def test_daily_exact_tier_draw_and_manager_widespread_separation():
    rows=[row(1),row(2,winner=2),row(3,status="WALKOVER",winner=1),
          row(4,status=None,winner=None),
          row(5,tour="ATP_MAIN",qualifying=True,pick_type="A1_A2_WIDE_SPREAD")]
    result=summarize_daily(rows,start_day=DAY,end_day=DAY,as_of=NOW)
    day=result["daily"][0]
    assert (day["picks"],day["won"],day["lost"],day["void"],day["pending"])==(5,2,1,1,1)
    assert day["provisional"] is True
    assert sum(g["picks"] for g in day["groups"])==5
    assert any(g["tour"]=="ATP_TOUR" and g["draw"]=="QUALIFYING" and
               g["pick_type"]=="A1_A2_WIDE_SPREAD" for g in day["groups"])
    assert day["accuracy_ex_void_pending"]==pytest.approx(2/3)


def test_missing_pit_tour_not_silently_included_as_challenger():
    r=summarize_daily([row(1,tour=None)],start_day=DAY,end_day=DAY,as_of=NOW)
    assert r["unclassified_tour_picks"]==1
    assert r["daily"][0]["groups"][0]["tour"]=="UNCLASSIFIED_TOUR"


def test_duplicate_first_issued_match_is_rejected():
    with pytest.raises(ValueError,match="duplicate"):
        summarize_daily([row(1),row(1)],start_day=DAY,end_day=DAY,as_of=NOW)


def test_zero_fixture_dates_are_explicit_and_not_completed_today():
    r=summarize_daily([],start_day=DAY,end_day=date(2026,11,18),as_of=NOW)
    assert len(r["daily"])==2
    assert r["daily"][0]["picks"]==0 and r["daily"][0]["provisional"] is False
    assert r["daily"][1]["picks"]==0 and r["daily"][1]["provisional"] is True


def test_tour_daily_sql_reads_first_issued_at_pit_and_runs_on_postgres(migrated_postgres):
    sql=daily_tour_sql()
    assert "aligned_production_model_picks_cte" not in sql
    assert "mv0.as_of <= p.picked_at" in sql
    assert "mro.as_of <= %(as_of)s" in sql
    assert "mro.winner_side" in sql
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only=on")
        report=load_tour_daily(conn,start_day=DAY,end_day=DAY,as_of=NOW)
    assert report["all_dates"]["picks"]==0


def test_auth_route_registered_and_rejects_huge_windows():
    assert "/api/dashboard/tour-daily-model-picks" in {r.path for r in app.routes}
    resp=TestClient(app).get(
        "/api/dashboard/tour-daily-model-picks?start_date=2026-08-01&end_date=2026-12-31"
    )
    assert resp.status_code==422


def test_model_picks_page_exposes_tour_split_without_claiming_research_live():
    from pathlib import Path
    template=Path("src/sports_edge_tennis/dashboard/templates/model_pick_results.html").read_text()
    assert 'id="tour-daily-results"' in template
    assert '/api/dashboard/tour-daily-model-picks' in template
    assert "MANAGER_CONSENSUS" in template
    assert "A1_A2_WIDE_SPREAD" in template
    assert "accuracy_ex_void_pending" in template
    assert "PROVISIONAL" in template
    assert "credentials:\"same-origin\"" in template
    assert "textContent" in template
    assert "Historically qualified" not in template
