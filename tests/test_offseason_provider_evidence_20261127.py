from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/research/evidence"


def test_provider_reconciliation_evidence_is_measured_and_non_zero():
    report = json.loads(
        (EVIDENCE / "provider_reconciliation_real_20261127.json").read_text()
    )
    providers = report["coverage"]["providers"]
    oddspapi = providers["ODDSPAPI"]
    assert oddspapi["raw_responses"] > 10_000
    assert oddspapi["normalized_quotes"] > 20_000
    assert oddspapi["valid_two_sided_markets"] > 10_000
    assert oddspapi["rejection_reasons"]["NORMALIZED"] > 10_000
    # The zero-normalization diagnosis is disproven by the stored corpus.
    assert oddspapi["normalized_quotes"] > 0
    # Strict original-time comparisons remain zero; the report must not claim
    # a market edge from retrospectively acquired quotes.
    assert oddspapi["original_time_qualified_comparisons"] == 0
    assert providers["API_TENNIS"]["original_time_qualified_comparisons"] == 0

    delay = report["delay"]
    assert delay["fixtures"] > 500
    assert delay["fixtures_with_positive_issue_to_price"] > 500
    assert delay["issue_to_first_price"]["p50"] > 10 * 3600
    # Most of the lag is batch scheduling, not persistence or provider latency.
    assert delay["batch_schedule_wait"]["p50"] > delay["post_identity_provider_wait"]["p50"]
    assert delay["persistence_wait"]["p50"] < 10
    assert delay["bookmaker_discovery_after_price"] >= 0

    assert report["catalogue_diagnostics"]["fixtures_with_alerts"] == 0
    assert report["fixture_metadata"]["with_updated_at"] == report["fixture_metadata"]["fixtures"]
    assert report["fixture_metadata"]["with_betradar_external_id"] > 0
    assert report["provider_overlap"]["matches_with_both_normalized_books"] == 0
    assert report["api_tennis_entitlement"]["status"] == "ENTITLEMENT_ONLY"
    assert report["api_tennis_entitlement"]["may_make_billable_requests"] is False
    assert report["api_tennis_entitlement"]["billable_calls_made_by_this_probe"] == 0
    assert report["balanced_160_target"]["ATP_MAIN"]["shortfall"] == 80
    assert report["balanced_160_target"]["substituted_from_other_competition"] is False
    assert report["pinnacle_availability"]["current_oddspapi_plan_includes_pinnacle"] is True

    delay_file = json.loads(
        (EVIDENCE / "acquisition_delay_real_20261127.json").read_text()
    )
    assert delay_file["fixtures"] == delay["fixtures"]
    assert delay_file["issue_to_first_price"]["p50"] == delay["issue_to_first_price"]["p50"]
    assert delay_file["fixtures_detail"]


def test_manager_cohort_evidence_reproduces_the_36_fixture_answer():
    report = json.loads(
        (EVIDENCE / "manager_c_cohort_reconciliation_real_20261127.json").read_text()
    )
    reconciliation = report["manager_reconciliation"]
    assert reconciliation["measured"] == {
        "settled_eligible": 599,
        "manager_corrected_retained": 254,
        "manager_abstained": 345,
        "common_support": 218,
        "manager_original_retained": 283,
    }
    assert all(reconciliation["matches_published"].values())
    difference = reconciliation["difference"]
    assert difference["manager_corrected_minus_common_support"] == 36
    assert difference["corrected_only_not_in_original"] == 36
    assert difference["original_only_not_in_corrected"] == 65
    assert difference["unexplained_common_support_entries"] == 0

    paired = report["cohorts"]["all"]["paired_a1_vs_manager"]
    assert paired["shared"] == 254
    assert paired["side_flips"] == 8
    assert paired["left_only_correct"] == 5
    assert paired["right_only_correct"] == 3
    assert paired["mcnemar_exact"]["p_value"] == 0.7265625

    verdicts = {
        item["category"]: item["verdict"]
        for item in report["agent2_specialist"]["categories"]
    }
    assert set(verdicts.values()) == {"INSUFFICIENT_EVIDENCE"}
    assert report["agent2_specialist"]["agent3_gate_relaxed"] is False


def test_market_benchmark_evidence_keeps_original_and_retrospective_separate():
    report = json.loads(
        (EVIDENCE / "market_benchmark_real_20261127.json").read_text()
    )
    for population in report["populations"].values():
        assert population["original_time_fixtures"] == 0
        assert population["retrospective"]["never_used_for_market_edge"] is True
        assert population["retrospective"]["label"] == "LATE_OR_BACKFILLED_DESCRIPTIVE_ONLY"
        assert population["clv"]["available"] is False
        definitions = population["retrospective"]["market_definitions"]
        assert definitions["consensus"]["market"]["brier"] is not None
        assert definitions["consensus_deduplicated"]["market"]["brier"] is not None
    all_population = report["populations"]["top800_by_confidence_all"]
    assert all_population["n"] == 800
    assert all_population["retrospective"]["fixtures"] > 300
    assert all_population["duplicate_pairs"]["exact_duplicate_pairs"] > 0
