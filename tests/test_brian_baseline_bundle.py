"""The one-snapshot baseline must never silently lose a void or match."""
from datetime import UTC, datetime

import pytest

from ops.brian_baseline_bundle import assemble_baseline


def _ledger():
    return {
        "data_refreshed_at":"2026-11-12T04:06:00+00:00",
        "periods":{
            "fixed_nov05_nov09":{
                "days":[{
                    "day":"2026-11-09",
                    "original":{
                        "picks":4,"won":1,"lost":1,"void":1,"pending":1,
                    },
                    "qualified":{
                        "picks":2,"won":1,"lost":0,"void":1,"pending":0,
                    },
                }],
            },
        },
    }


def _matches():
    return {
        1: {"match_id":1,"status":"WON",
            "qualified_ge70":True,"issued_before_kickoff":True},
        2: {"match_id":2,"status":"LOST",
            "qualified_ge70":False,"issued_before_kickoff":True},
        3: {"match_id":3,"status":"VOID",
            "qualified_ge70":True,"issued_before_kickoff":True},
        4: {"match_id":4,"status":"PENDING",
            "qualified_ge70":False,"issued_before_kickoff":True},
    }


def _run(ledger=None, matches=None, pending=None):
    return assemble_baseline(
        ledger=ledger or _ledger(),
        nov9_matches=matches or _matches(),
        pending=pending or {"total":1,"items":[{"match_id":4}]},
        last_result_ingestion=datetime(2026,11,12,3,tzinfo=UTC),
        last_fixture_observation=datetime(2026,11,12,4,tzinfo=UTC),
    )


def test_valid_baseline_contains_all_four_outcome_states_and_times():
    x=_run()
    assert x["status"] == "PENDING_PROVIDER_VERIFICATION_REQUIRED"
    assert x["nov09"]["original_count"] == 4
    assert x["nov09"]["qualified_count"] == 2
    assert [x["status"] for x in x["nov09"]["all_original_match_records"]] == [
        "WON","LOST","VOID","PENDING",
    ]
    assert x["last_result_ingested_at"].startswith("2026-11-12")
    assert x["externally_verified"] is False
    assert x["tests_executed"] is False
    assert x["dashboard_parity_verified"] is False


def test_missing_void_or_replaced_winner_blocks_bundle():
    m=_matches()
    del m[3]
    with pytest.raises(ValueError,match="original first-issued"):
        _run(matches=m)
    m=_matches()
    m[3]["status"]="WON"
    with pytest.raises(ValueError,match="original won ledger"):
        _run(matches=m)


def test_unverified_pre_kickoff_and_pending_inventory_not_silent():
    m=_matches()
    m[3]["issued_before_kickoff"]=None
    data=_run(matches=m)
    assert data["status"]=="PIT_REVIEW_REQUIRED"
    assert data["nov09"]["unverified_pit_ids"]==[3]
    with pytest.raises(ValueError,match="pending summary"):
        _run(pending={"total":2,"items":[{"match_id":4}]})


def test_original_filter_population_is_immutable_by_canonical_match_id():
    m=_matches()
    m[2]["qualified_ge70"]=True
    with pytest.raises(ValueError,match=">=70"):
        _run(matches=m)
