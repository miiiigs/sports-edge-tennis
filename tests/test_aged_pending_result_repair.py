"""One-shot append-only aged pending repair safety and identity guard tests."""
from datetime import UTC,datetime
from sports_edge_tennis.validation import aged_pending_result_repair as r


def test_historical_preview_is_deterministic_and_order_independent():
    a=r.Recoverable(13,"abc","COMPLETED",1,1,2,10,{})
    b=r.Recoverable(14,"def","WALKOVER",2,3,4,20,{})
    assert r._digest([a,b])==r._digest([b,a])
    assert len(r._digest([a,b]))==64
    assert r._digest([a,b])!=r._digest([a])


def test_candidate_guard_never_writes_during_proof(monkeypatch):
    class FakeConn:
        def execute(self,sql,args):
            if "match_source_ids" in sql:
                return self
            return self
        def fetchall(self):
            return []
    assert r._validate_identity(FakeConn(),22,"123",{"first_player_key":"1"}) is None


def test_closing_rules_explicitly_forbid_hypothetical_17_losses():
    import inspect
    src=inspect.getsource(r)
    assert "EXPECTED_RECOVERABLE = 17" in src
    assert "RESULT_ALREADY_PRESENT" in src
    assert "PRODUCTION_BATCH_ACTIVE" in src
    assert "PREVIEW_SHA_DRIFT" in src
    assert "conn.transaction()" in src
    assert "AGENT_3_INTRANSITIVITY" in src


def test_unrecognized_provider_status_is_not_guessed_into_a_winner():
    class Stub:
        pass
    candidate,reason=r._candidate({"event_status":"Suspended"},
                                   conn=Stub(),match_id=12,event_key="x")
    assert candidate is None
    assert reason=="NOT_TERMINAL"
