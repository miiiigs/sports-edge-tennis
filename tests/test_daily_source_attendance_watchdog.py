"""Independent source attendance watchdog never modifies selection or results."""
from datetime import date,datetime,UTC
from sports_edge_tennis.live import production_batch as mod
from sports_edge_tennis.dashboard import source_attendance


def test_watchdog_reports_historical_missing_days_without_mutations(monkeypatch):
    messages=[]
    monkeypatch.setattr(mod,"_log",messages.append)
    class ReadOnly:
        def __enter__(self): return self
        def __exit__(self,*args): pass
    monkeypatch.setattr(mod.psycopg,"connect",lambda *args,**kwargs: ReadOnly())
    def fake_load(conn, *, first,last,as_of):
        return {"days":[{
          "date":"2026-11-14","canonical_fixtures_now":17,
          "source_observed_before_30min":0,"ingestion_jobs":0,
          "prediction_runs":0,"source_attendance":"HISTORICAL_SOURCE_AND_RUN_GAP",
        }]}
    monkeypatch.setattr(source_attendance,"load_source_attendance",fake_load)
    mod._source_attendance_cycle("read-only-mock")
    assert any("DAILY_SOURCE_COVERAGE_WARNING" in x for x in messages)
    assert any("auto_grade=FALSE" in x for x in messages)
    assert not any("DAILY_SOURCE_ATTENDANCE_UNAVAILABLE" in x for x in messages)


def test_watchdog_failure_does_not_abort_live_operation(monkeypatch):
    messages=[]
    monkeypatch.setattr(mod,"_log",messages.append)
    def fail(*args,**kwargs):
        raise RuntimeError("test only")
    monkeypatch.setattr(mod.psycopg,"connect",fail)
    assert mod._source_attendance_cycle("unused") is None
    assert messages and "DAILY_SOURCE_ATTENDANCE_UNAVAILABLE" in messages[0]
