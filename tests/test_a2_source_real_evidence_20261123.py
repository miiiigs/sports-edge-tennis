"""Reconcile original pinned real A2 serve/return source coverage evidence."""
import json
from pathlib import Path

SOURCE=Path(__file__).parents[1]/"docs/research/evidence/a2_source_availability_real_20261123.json"


def test_a2_real_database_cohort_counts_and_explicit_license_guard():
    a=json.loads(SOURCE.read_text())
    r=a["report"];t=r["totals"]
    assert a["evidence_class"]=="AMBER_ASOF_SOURCE_COUNTS_NOT_ORIGINAL_PIT"
    assert (t["recorded_matches"],t["two_sided"],
            t["essential_complete"],t["extended_complete"])==(
                134290,122724,120689,116364)
    assert sum(d["recorded_matches"] for d in r["by_tour"].values())==134290
    assert r["by_tour"]["ATP_MAIN"]["recorded_matches"]==42362
    assert r["by_tour"]["ATP_MAIN"]["essential_complete"]==37520
    assert r["by_tour"]["CHALLENGER"]["recorded_matches"]==91928
    assert r["by_tour"]["CHALLENGER"]["essential_complete"]==83169
    sources=a["source_partition_essential_complete"]
    assert sources["sackmann"]==115678
    assert sources["other_sources"]==5011
    assert sources["sackmann"]+sources["other_sources"]==t["essential_complete"]
    assert r["source_licenses_verified"] is False
    assert r["PIT_for_each_original_prediction_verified"] is False
    assert r["provider_identity_bridge_applied"] is False
    assert r["exact_raw_player_id_lower_bound_only"] is True
    assert r["production_model_changed"] is False
    assert r["db_writes"] is False
    assert a["external_provider_calls"]==0
