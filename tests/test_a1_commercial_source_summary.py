import pytest
from sports_edge_tennis.validation.a1_commercial_source_summary import summarize, verify_frozen_ledger

def test_independent_sources_and_cutoff_mismatch():
    rows = [
        dict(match_id=1,status="SOURCE_UNKNOWN",result_observation_id=10,match_version_id=20,
             result_ingest_source="SACKMANN",result_ingest_dataset="results",result_ingest_version="v1",
             identity_source="UNKNOWN_NOT_TRACED"),
        dict(match_id=2,status="EXPECTED_HISTORY_AS_OF_MISMATCH",result_observation_id=11,
             match_version_id=21,result_ingest_source="API_TENNIS",identity_source="UNKNOWN_NOT_TRACED"),
        dict(match_id=3,status="RESULT_UNAVAILABLE",result_observation_id=None,match_version_id=None),
    ]
    out = summarize({"records":rows,"ledger_matches":3})
    assert sum(x["matches"] for x in out["results"]) == 3
    assert any(x["source"] == "SACKMANN" and x["matches"] == 1 for x in out["results"])
    assert any(x["source"] == "CUTOFF_MISMATCH" for x in out["results"])
    assert not any(x["source"] == "API_TENNIS" for x in out["results"])
    assert out["identity_untraced_matches"] == 2
    assert out["can_promote"] is False
    with pytest.raises(ValueError,match="denominator"):
        summarize({"records":rows+[rows[0]],"ledger_matches":4})
    with pytest.raises(ValueError,match="digest"):
        verify_frozen_ledger(b"not the frozen ledger")
