"""Guard against misreporting a hypothetically subtracted Manager C marginal cohort."""
import json
from pathlib import Path

EVIDENCE = Path(__file__).parents[1]/"docs/research/evidence/manager_c_marginal_real_20261123.json"


def test_exact_id_nested_original_manager_c_marginal_is_independently_archived():
    e=json.loads(EVIDENCE.read_text())
    r=e["report"]
    a=r["narrow"];b=r["broad"];extra=r["additional_only"]
    assert e["real_database_read_only_observation"] is True
    assert e["database_mutation"] is False
    assert e["api_requests_made"]==0
    assert r["status"]=="NESTED_VERIFIED_BY_EXACT_MATCH_IDS"
    assert r["selected_ids"]["narrow_missing_from_broad"]==0
    assert a["selected"]+extra["selected"]==b["selected"]==275
    assert a["settled"]+extra["settled"]==b["settled"]==256
    assert a["wins"]+extra["wins"]==b["wins"]==176
    assert a["losses"]+extra["losses"]==b["losses"]==80
    assert a["ungraded"]+extra["ungraded"]==b["ungraded"]==19
    assert (a["wins"],a["losses"])==(80,27)
    assert (extra["wins"],extra["losses"])==(96,53)
    assert extra["settled"]==149
    assert round(extra["accuracy"]*100,2)==64.43
    assert round(a["accuracy"]*100,2)==74.77
    assert round(b["accuracy"]*100,2)==68.75
    assert r["evaluation_first_run_targets"]==656
    assert r["validation_targets"]==1027
    assert r["historical_gap_to_72pct_broad"]==9
    assert r["historical_gap_to_75pct_broad"]==16
    assert extra["by_tour"]["CHALLENGER"]["selected"]==152
    assert extra["by_tour"]["ATP_MAIN"]["selected"]==6
    assert r["official_fixture_denominator"] is None
    assert r["independent_forward_validation"] is False
    assert len(r["dataset_sha256"])==64
    assert len(r["selected_ids"]["broad_only_ids_sha256"])==64
    assert e["research_class"].startswith("AMBER")
