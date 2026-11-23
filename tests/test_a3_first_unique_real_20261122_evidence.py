"""Original 22 Nov first-unique A3 historical support report provenance."""
from collections import Counter
from pathlib import Path
import hashlib
import json

PATH=Path(__file__).parents[1]/"docs/research/forward_captures/a3_first_unique_support_20261122.json"


def test_real_first_unique_support_report_has_zero_unproved_new_winner_claims():
    r=json.loads(PATH.read_text())
    assert r["version"]=="A3_UNIQUE_FIRST_SUPPORT_FRONTIER_V1"
    assert r["first_unique_match_diagnostics_count"]==2127
    assert r["original_a3_predicted"]==8
    assert r["original_a3_abstained_logged"]==2119
    assert r["raw_graph_soft_candidate_hypothesis_count"]==1336
    assert r["candidate_sample_count"]==100
    assert r["first_unique_match_category_counts"]=={
        "BELOW_NORMAL_NETWORK_SHARED_OR_Q_GATE":528,
        "BOTH_HISTORY_IDENTITIES_UNRESOLVED":1,
        "LATE_OR_NON_FUTURE_EVALUATION":9,
        "NO_RAW_SHARED_OPPONENTS":158,
        "ONE_OR_BOTH_PLAYERS_ZERO_MATCH_HISTORY":70,
        "ORIGINAL_A3_PREDICTED":8,
        "POTENTIALLY_SUPPORTED_BUT_SIGNAL_OR_OTHER_GATE":152,
        "RAW_SHARED_BUT_ZERO_ELIGIBLE_EDGES":846,
        "UNKNOWN_SURFACE":355,
    }
    assert sum(r["first_unique_match_category_counts"].values())==2127
    assert len(r["candidate_samples"])==100
    assert len({x["match_id"] for x in r["candidate_samples"]})==100
    assert hashlib.sha256(json.dumps(
        r["candidate_samples"],sort_keys=True,separators=(",",":")
    ).encode()).hexdigest()=="403e984710a99b016234df6d2fad154e1a3cd761cd697ea296ca71c418b0a693"
    assert all(not x["true_counterfactual_a3_probability_available"]
               for x in r["candidate_samples"])
    assert all(x["original_status"]=="ABSTAINED" for x in r["candidate_samples"])
    assert r["exact_soft_edge_posterior_replay_performed"] is False
    assert r["no_outcomes_or_accuracy_computed"] is True
    assert r["individual_historical_edge_source_availability_unverified"] is True
    assert r["no_provider_calls"] and r["no_db_writes"] and r["no_production_changes"]


def test_atp_challenger_cohort_counts_are_exact_not_repeated_cycle_counts():
    r=json.loads(PATH.read_text())
    m=r["by_tour_categories"]
    assert set(m)=={"ATP_MAIN","CHALLENGER"}
    assert sum(sum(q.values()) for q in m.values())==r["first_unique_match_diagnostics_count"]
    assert m["ATP_MAIN"]["ORIGINAL_A3_PREDICTED"]==4
    assert m["CHALLENGER"]["ORIGINAL_A3_PREDICTED"]==4
