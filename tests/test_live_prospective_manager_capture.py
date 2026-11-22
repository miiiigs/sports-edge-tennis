"""Optional forward research observer never mutates live Manager or market path."""
from datetime import UTC,datetime,timedelta
import json
import inspect
import pytest

from sports_edge_tennis.live import batch
from sports_edge_tennis.live import prospective_manager_capture as capture
from sports_edge_tennis.validation.prospective_manager_shadow_capture import FROZEN

NOW=datetime(2026,11,22,14,39,tzinfo=UTC)


class FakeConn:
    def __init__(self):
        self.closed=False
    def __enter__(self):
        return self
    def __exit__(self,*args):
        self.closed=True


def test_disabled_observer_makes_no_db_query_or_log():
    logs=[]
    def must_not_connect(*a,**k):
        raise AssertionError("disabled must never open database")
    r=capture.observe_first_issued_runs(
        dsn="not-used",run_ids=[101],enabled=False,
        log=logs.append,capture_at=NOW,connector=must_not_connect)
    assert r is None
    assert logs==[]


def test_new_first_issue_only_connected_read_only_and_logged(monkeypatch):
    logs=[]
    state={}
    def connect(dsn,**kwargs):
        state["dsn"]=dsn
        state["connect"]=kwargs
        c=FakeConn()
        state["conn"]=c
        return c
    def fake_run(conn,*,captured_at,start,end,source_sha,only_run_ids):
        assert conn is state["conn"]
        assert only_run_ids=={21,23}
        assert captured_at==NOW
        assert start==NOW+timedelta(minutes=30)
        assert end==NOW+timedelta(days=4)
        assert source_sha==capture.POLICY_FROZEN_SOURCE_SHA
        return {
            "first_issued_future_targets":1,
            "genuine_future_shadow_records":[{
                "canonical_match_id":333,
                "frozen_research_policies":{
                    "production_original_as_issued":{"status":"ABSTAINED"}
                },
                "outcomes_available_during_capture":False,
                "not_a_published_model_pick":True,
            }],
            "utc_capture_records_sha256":"frozen-test-hash",
        }
    # Parameter typo in fake_run declaration caught by Python syntax if incorrect.
    monkeypatch.setattr(capture,"run",fake_run)
    result=capture.observe_first_issued_runs(
        dsn="read-only-dsn",run_ids=[21,23],enabled=True,
        log=logs.append,capture_at=NOW,connector=connect)
    assert state["dsn"]=="read-only-dsn"
    assert state["connect"]=={"autocommit":True,"connect_timeout":10}
    assert state["conn"].closed is True
    assert result["first_issued_future_targets"]==1
    assert len(logs)==2
    assert logs[0].startswith("TENNIS_FROZEN_PROSPECTIVE_CANDIDATE ")
    assert logs[1].startswith("TENNIS_FROZEN_PROSPECTIVE_SUMMARY ")
    assert json.loads(logs[1].split(" ",1)[1])["utc_capture_records_sha256"]=="frozen-test-hash"


def test_invalid_run_ids_fail_closed_without_db():
    assert capture.observe_first_issued_runs(
        dsn="unused",run_ids=[None],enabled=True,
        log=lambda s:None,capture_at=NOW,
        connector=lambda *a,**k: (_ for _ in ()).throw(AssertionError())
    ) is None


def test_live_hook_after_issue_is_opt_in_nonblocking():
    source=inspect.getsource(batch._run_batch)
    assert 'if os.environ.get("TENNIS_FORWARD_MANAGER_SHADOW_CAPTURE") == "1":' in source
    assert "observe_first_issued_runs(" in source
    assert "TENNIS_FROZEN_SHADOW_CAPTURE_UNAVAILABLE" in source
    # Capture occurs after the runner has finalized the actual match forecasts.
    assert source.index("prediction_run_ids.append(result.prediction_run_id)")<source.index("observe_first_issued_runs(")
    assert len(FROZEN)==5
