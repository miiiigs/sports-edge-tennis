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


def test_repair_requires_four_allowlisted_source_verified_walkovers():
    import inspect
    src=inspect.getsource(r)
    assert r.EXPECTED_RECOVERABLE == 4
    assert r.WALKOVER_MATCH_IDS == {133119, 133505, 133377, 133672}
    assert "WALKOVER_MATCH_IDS" in src
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


def test_repair_uses_only_existing_match_ids_and_append_only_result_path():
    import inspect
    source = inspect.getsource(r._apply)
    assert "ingest_api_tennis_source_a_fixtures" not in source
    assert "normalize_api_tennis_fixture_rounds" not in source
    assert "ApiTennisIngestedMatch" in source
    assert "_validate_identity" in source
    assert "_latest_result_semantic_key" in source
    assert "_persist_terminal_results" in source
    assert "conn.transaction()" in source


def test_completed_match_is_rejected_by_walkover_only_repair():
    class NoDatabaseLookupsAllowed:
        def execute(self,*args,**kwargs):
            raise AssertionError("no DB reads should occur for a wrong result class")
    candidate, reason = r._candidate(
        {"event_status":"Finished","event_winner":"First Player"},
        conn=NoDatabaseLookupsAllowed(),match_id=133119,event_key="test"
    )
    assert candidate is None
    assert reason == "NOT_A_VERIFIED_WALKOVER"


def test_pg_source_timestamp_is_before_its_insert_transaction(migrated_postgres):
    """Regression: CURRENT_TIMESTAMP freezes at BEGIN; Python now() after BEGIN can violate ingested_at>=source_observed_at."""
    import psycopg
    from datetime import timedelta
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        source_observed_at = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        with conn.transaction():
            ingested_default_clock = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
            assert ingested_default_clock >= source_observed_at
            assert ingested_default_clock - source_observed_at < timedelta(seconds=5)


def test_writer_gets_database_source_clock_before_begin():
    import inspect
    text=inspect.getsource(r._apply)
    assert 'now = conn.execute("SELECT clock_timestamp()").fetchone()[0]' in text
    assert text.index('now = conn.execute("SELECT clock_timestamp()")') < text.index('with conn.transaction():')
