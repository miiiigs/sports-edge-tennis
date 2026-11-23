"""Immutable first prospective research capture validates original Railway digest."""
from collections import Counter
from datetime import datetime,timedelta,UTC
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).parents[1]
STEM="prospective_manager_20261122T143952Z"
BASE=ROOT/"docs/research/forward_captures"


def test_original_prospective_capture_reconciles_all_90_before_any_outcome():
    summary=json.loads((BASE/(STEM+"_summary.json")).read_text())
    raw=(BASE/(STEM+".jsonl")).read_text().splitlines()
    assert len(raw)==summary["first_issued_future_targets"]==90
    assert summary["research_freeze_code_sha"]=="177ec8146561c4e06d28e3e490dad38421388035"
    assert summary["utc_capture_records_sha256"]=="e11a6393140c86e5e76d2493818fd0ce9301585d33b35d75024c6f1961e34627"
    assert summary["settled_match_results_at_capture"]==0
    assert summary["official_complete_fixture_denominator"] is None

    captured=datetime.fromisoformat(summary["capture_time_utc"])
    digest=hashlib.sha256()
    ids=set()
    counts=Counter()
    for line in raw:
        record=json.loads(line)
        mid=record["canonical_match_id"]
        assert mid not in ids
        ids.add(mid)
        assert record["version"]==summary["version"]
        assert datetime.fromisoformat(record["captured_at_utc"])==captured
        assert datetime.fromisoformat(record["original_issued_at_utc"])<=captured
        assert datetime.fromisoformat(record["scheduled_start_at_utc"])>=captured+timedelta(minutes=30)
        assert record["outcomes_available_during_capture"] is False
        assert record["new_feature_data_used"] is False
        assert record["not_a_published_model_pick"] is True
        assert set(record["frozen_research_policies"])==set(summary["frozen_rules"])
        for name,choice in record["frozen_research_policies"].items():
            assert choice["frozen_confidence_cutoff"]==summary["frozen_rules"][name]["threshold"]
            if choice["status"]=="SELECTED":
                assert choice["side"] in (1,2)
                assert 0<float(choice["p1"])<1
                counts[name]+=1
            else:
                assert choice["side"] is None
        digest.update((json.dumps(record,sort_keys=True,separators=(",",":"))+"\n").encode())
    assert digest.hexdigest()==summary["utc_capture_records_sha256"]
    assert counts==summary["selection_counts"]
    assert summary["selection_counts"]=={
        "production_original_as_issued":14,
        "original_manager":9,
        "B_guarded2_floor60_spread20":14,
        "C_uncalibrated_mean_floor60":13,
        "A1A2_equal_log_odds_blend":19,
    }


def test_capture_is_old_reference_not_an_outcome_backfill():
    summary=json.loads((BASE/(STEM+"_summary.json")).read_text())
    assert summary["no_database_writes"] is True
    assert summary["no_market_api_calls"] is True
    assert summary["cannot_claim_future_accuracy_until_outcomes"] is True
    assert summary["original_Railway_deployment"]=="f5591b15-afde-4e5a-b036-31420c5ca094"
    assert "2026-11-22T14:39:53" in summary["original_railway_log_timestamp"]
    assert summary["window_start_utc"].startswith("2026-11-22T15:09:")
