"""Pin original no-network future-slate provider cache feasibility evidence."""
import json
from pathlib import Path

F=Path(__file__).parents[1]/"docs/research/evidence/oddspapi_future_quota_dryrun_20261123.json"


def test_future_odds_availability_real_census_fail_closed():
    e=json.loads(F.read_text())
    r=e["result"];c=r["counts"]
    assert e["no_new_requests_or_cost"] is True
    assert r["input_unique_canonical_fixture_count"]==103
    assert c["internal_eligible_main_singles"]==71
    assert c["identity_cache_missing"]==69
    assert c["warm_verified_oddspapi_identity"]==2
    assert c["qualifying_or_unknown_draw_excluded"]==26
    assert c["outside_prematch_or_horizon"]==6
    assert (69+2+26+6)==103
    assert len(r["missing_or_invalid_cache_sample"])==69
    assert r["estimated_bookmaker_queries_per_cycle_IF_APPROVED"]==3
    assert r["verified_warm_fixture_count"]==2
    assert r["provider_identity_not_equivalent_to_valid_odds"] is True
    assert r["not_all_official_fixture_denominator"] is True
    assert r["no_cold_unknown_tournament_requests_planned"] is True
    assert r["no_provider_calls_made"] is True
    assert r["no_database_writes"] is True
    assert r["no_manager_pick_or_market_gate_change"] is True
