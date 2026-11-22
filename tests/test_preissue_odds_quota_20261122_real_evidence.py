"""Frozen real pre-issue full-slate cache request-budget evidence."""
from pathlib import Path
import json


def test_real_preissue_fixture_identity_coverage_and_no_new_requests():
    p=Path(__file__).parents[1]/"docs/research/forward_captures/preissue_oddspapi_quota_dryrun_20261122.json"
    r=json.loads(p.read_text())
    assert r["version"]=="PREISSUE_ODDSPAPI_FULL_SLATE_DRYRUN_V1"
    assert r["input_unique_canonical_fixture_count"]==99
    assert r["counts"]=={
        "identity_cache_missing":71,
        "internal_eligible_main_singles":74,
        "outside_prematch_or_horizon":4,
        "qualifying_or_unknown_draw_excluded":21,
        "warm_verified_oddspapi_identity":3,
    }
    assert sum([r["counts"]["internal_eligible_main_singles"],
                r["counts"]["qualifying_or_unknown_draw_excluded"],
                r["counts"]["outside_prematch_or_horizon"]])==99
    assert r["verified_warm_fixture_count"]==3
    assert r["warm_unique_tournament_count"]==1
    assert r["provisionally_schedulable_warm_fixture_count"]==3
    assert r["deferred_warm_fixture_count"]==0
    assert r["estimated_bookmaker_queries_per_cycle_IF_APPROVED"]==3
    assert r["estimated_monthly_queries_if_same_cadence"]==2232
    assert r["per_cycle_quota_limit"]==5
    assert r["no_provider_calls_made"] is True
    assert r["no_database_writes"] is True
    assert r["no_new_subscription_or_incremental_bill"] is True
    assert r["provider_identity_not_equivalent_to_valid_odds"] is True
    assert r["requires_separate_approval_before_actual_request_work"] is True
    assert r["estimated_repairable_future_coverage"] is None
    assert r["not_all_official_fixture_denominator"] is True
