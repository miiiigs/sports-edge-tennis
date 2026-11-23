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
                     original_result,production_commit):
        checks.append((canonical_match_id,capture_at,match_start_at,production_commit))
        return {"version":"test","canonical_match_id":canonical_match_id}
    import sports_edge_tennis.validation.a3_soft_common_opponent_ess_shadow as shadow
    monkeypatch.setattr(shadow,"capture_provisional_live_shadow",stub_capture)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA","verified-commit")
    full_slate._capture_optional_a3_soft_shadow(
        Conn(),target=SimpleNamespace(match_id=134291,match_version_id=42),
        request=request(),original_result=object(),as_of=NOW)
    assert checks[0][1]==(42,NOW,NOW,NOW)
    assert checks[1][0]==134291
    assert checks[1][3]=="verified-commit"
    assert "TENNIS_A3_SOFT_PROSPECTIVE_SHADOW " in capsys.readouterr().out
