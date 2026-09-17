from sports_edge_tennis.runner.targets import _identity_gap_reason


def test_identity_gap_reason_no_alias_candidate():
    assert (
        _identity_gap_reason(alias_candidate_count=0, eligible_candidate_count=0)
        == "NO_HISTORICAL_ALIAS"
    )


def test_identity_gap_reason_single_alias_without_pit_history():
    assert (
        _identity_gap_reason(alias_candidate_count=1, eligible_candidate_count=0)
        == "ALIAS_WITHOUT_PIT_HISTORY"
    )


def test_identity_gap_reason_multiple_aliases_without_unique_pit_history():
    assert (
        _identity_gap_reason(alias_candidate_count=3, eligible_candidate_count=0)
        == "MULTIPLE_ALIAS_CANDIDATES_WITHOUT_UNIQUE_PIT_HISTORY"
    )


def test_identity_gap_reason_ambiguous_pit_eligible_candidates():
    assert (
        _identity_gap_reason(alias_candidate_count=3, eligible_candidate_count=2)
        == "AMBIGUOUS_PIT_ELIGIBLE"
    )
