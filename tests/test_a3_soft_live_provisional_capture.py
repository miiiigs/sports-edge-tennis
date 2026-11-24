"""Actual in-run Agent3 shadow proposals, no manager override or replay."""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3HistorySnapshot, PredictionRequest, predict,
)
from sports_edge_tennis.validation.a3_soft_common_opponent_ess_shadow import (
    PROFILES, capture_provisional_live_shadow,
)
from sports_edge_tennis.runner import full_slate

NOW=datetime(2026,11,23,12,0,tzinfo=UTC)


def request():
    return PredictionRequest(
        player_1_id=10,player_2_id=20,surface="HARD",
        history_snapshot=Agent3HistorySnapshot(),as_of=NOW,
    )


def test_live_prestart_shadow_preserves_original_abstention_and_three_variants():
    req=request()
    original=predict(req)
    r=capture_provisional_live_shadow(
        req,canonical_match_id=134291,
        capture_at=NOW+timedelta(seconds=15),
        match_start_at=NOW+timedelta(hours=5),
        original_result=original,
        production_commit="abc123",
    )
    assert r["canonical_match_id"]==134291
    assert r["version"]=="A3_LIVE_SOFT_PIT_PROVISIONAL_V1"
    assert r["original_agent3_as_computed"]["status"]=="ABSTAINED"
    assert set(r["research_soft_common_opponent_variants"])==set(PROFILES)
    assert r["no_result_winner_used"] is True
    assert r["actual_frozen_live_snapshot_not_retrospective_replay"] is True
    assert r["independent_underlying_source_ingestion_verification"] is False
    assert r["first_issued_unique_canonical_prediction_not_yet_verified"] is True
    assert r["production_model_pick_unchanged"] is True
    assert r["cannot_promote_to_production"] is True
    d={k:v for k,v in r.items() if k!="record_sha256"}
    digest=hashlib.sha256(json.dumps(d,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    assert digest==r["record_sha256"]


def test_live_shadow_refuses_late_or_fabricated_capture():
    req=request()
    original=predict(req)
    with pytest.raises(ValueError,match="pre-match"):
        capture_provisional_live_shadow(
            req,canonical_match_id=9,
            capture_at=NOW+timedelta(minutes=31),
            match_start_at=NOW+timedelta(hours=1),
            original_result=original,
        )
    with pytest.raises(ValueError,match="canonical"):
        capture_provisional_live_shadow(
            req,canonical_match_id=0,
            capture_at=NOW,match_start_at=NOW+timedelta(hours=1),
            original_result=original,
        )


def test_full_slate_shadow_reads_exact_asof_original_match_version(monkeypatch,capsys):
    checks=[]
    class Conn:
        def execute(self,sql,params):
            checks.append((sql,params))
            return self
        def fetchone(self):
            return (NOW+timedelta(hours=6),)
    def stub_capture(request,*,canonical_match_id,capture_at,match_start_at,
                     original_result,production_commit,**metadata):
        checks.append((canonical_match_id,capture_at,match_start_at,production_commit,metadata))
        return {"version":"test","canonical_match_id":canonical_match_id}
    import sports_edge_tennis.validation.a3_soft_common_opponent_ess_shadow as shadow
    monkeypatch.setattr(shadow,"capture_provisional_live_shadow",stub_capture)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA","verified-commit")
    record = full_slate._capture_optional_a3_soft_shadow(
        Conn(),target=SimpleNamespace(match_id=134291,match_version_id=42),
        request=request(),original_result=object(),as_of=NOW,prediction_run_id=77)
    assert checks[0][1]==(42,134291,NOW,NOW,NOW)
    assert checks[1][0]==134291
    assert checks[1][3]=="verified-commit"
    assert checks[1][4]["prediction_run_id"] == 77
    assert checks[1][4]["match_version_id"] == 42
    assert record["canonical_match_id"] == 134291


@pytest.mark.parametrize("autocommit", [False, True])
@pytest.mark.parametrize("failure", ["sql", "python", "none"])
def test_postgres_shadow_preserves_production_transaction(
    postgres_dsn, monkeypatch, capsys, autocommit, failure,
):
    import psycopg
    from psycopg.pq import TransactionStatus

    target = SimpleNamespace(match_id=134291, match_version_id=42)
    with psycopg.connect(postgres_dsn, autocommit=autocommit) as conn:
        conn.execute("CREATE TEMP TABLE a3_shadow_tx_probe (id integer PRIMARY KEY)")
        if not autocommit:
            conn.commit()
        conn.execute("INSERT INTO a3_shadow_tx_probe VALUES (1)")
        before = conn.info.transaction_status

        def probe(connection, **kwargs):
            assert connection is conn
            if failure == "sql":
                connection.execute("SELECT 1/0")
            if failure == "python":
                raise ValueError("bad optional research evidence")
            return {"canonical_match_id": target.match_id}

        monkeypatch.setattr(full_slate, "_capture_optional_a3_soft_shadow", probe)
        assert full_slate._observe_a3_soft_shadow(
            conn, target=target, request=request(), original_result=object(),
            as_of=NOW, prediction_run_id=77,
        ) is (failure == "none")
        assert conn.info.transaction_status == before
        assert before == (TransactionStatus.IDLE if autocommit else TransactionStatus.INTRANS)
        conn.execute("INSERT INTO a3_shadow_tx_probe VALUES (2)")
        assert conn.execute("SELECT id FROM a3_shadow_tx_probe ORDER BY id").fetchall() == [(1,), (2,)]
        conn.commit()
        assert conn.execute("SELECT count(*) FROM a3_shadow_tx_probe").fetchone() == (2,)
        output = capsys.readouterr().out
        if failure == "sql":
            assert '"sqlstate": "22012"' in output
        assert ("TENNIS_A3_SOFT_PROSPECTIVE_SHADOW" in output) is (failure == "none")


def test_live_shadow_metadata_identifies_evaluation_not_first_issue():
    req = request()
    kwargs = dict(canonical_match_id=134291, capture_at=NOW,
                  match_start_at=NOW+timedelta(hours=5),
                  original_result=predict(req), prediction_run_id=77,
                  match_version_id=42, tour_scope="CHALLENGER")
    one = capture_provisional_live_shadow(req, **kwargs)
    two = capture_provisional_live_shadow(req, **{
        **kwargs, "capture_at": NOW+timedelta(seconds=1)})
    assert one["evaluation_key"] == two["evaluation_key"]
    assert one["record_sha256"] != two["record_sha256"]
    assert one["original_match_version_id"] == 42
    assert one["first_issued_unique_canonical_prediction_not_yet_verified"]


def test_shadow_logging_failure_is_nonblocking(monkeypatch):
    def broken_print(*args, **kwargs):
        raise OSError("closed log pipe")
    monkeypatch.setattr("builtins.print", broken_print)
    assert full_slate._emit_a3_shadow_event("TEST", {}) is False
