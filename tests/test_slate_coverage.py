"""Day-scoped fixtures: no green batch when another date has unknown coverage."""

from sports_edge_tennis.live.slate_coverage import (
    classify_slate_fixture_coverage,
)


def _classify(*, seen=20, ingested=20, rejected=0, targets=12, reasons=()):
    return classify_slate_fixture_coverage(
        seen=seen, ingested=ingested, rejected=rejected,
        targets=targets, rejection_reasons=reasons,
    )


def test_known_in_scope_scheduled_slate_is_healthy():
    r = _classify()
    assert r.status == "HEALTHY"
    assert not r.is_warning


def test_unsupported_numerical_one_is_warning_not_scheduled():
    r = _classify(
        seen=140, ingested=0, rejected=140, targets=0,
        reasons=(("out_of_scope_event_type", 89),
                 ("unsupported_event_status", 51)),
    )
    assert r.status == "WARNING"
    assert r.reason == "UNSUPPORTED_EVENT_STATUS"
    assert r.unknown_status_rows == 51
    assert r.unverified_rejections == 51
    assert r.targets == 0


def test_unknown_status_with_other_valid_targets_still_warns():
    r = _classify(
        seen=40, ingested=37, rejected=3, targets=20,
        reasons=(("unsupported_event_status", 3),),
    )
    assert r.is_warning
    assert r.unknown_status_rows == 3


def test_all_out_of_scope_only_is_a_legitimate_empty_slate():
    r = _classify(
        seen=16, ingested=0, rejected=16, targets=0,
        reasons=(("out_of_scope_event_type", 16),),
    )
    assert r.status == "EMPTY_OUT_OF_SCOPE"
    assert not r.is_warning


def test_no_fixture_observation_does_not_invent_an_expected_fixture():
    r = _classify(seen=0, ingested=0, rejected=0, targets=0)
    assert r.status == "EMPTY_NO_SOURCE"
    assert r.reason == "NO_PROVIDER_FIXTURES_OBSERVED"
    assert not r.is_warning


def test_all_ingested_but_no_scheduled_targets_is_explicit():
    r = _classify(seen=7, ingested=7, rejected=0, targets=0)
    assert r.status == "EMPTY_NO_SCHEDULED"
    assert not r.is_warning


def test_one_player_identity_conflict_is_not_masked_by_other_targets():
    r = _classify(
        seen=140, ingested=50, rejected=90, targets=50,
        reasons=(("out_of_scope_event_type", 89),
                 ("event_identity_conflict:player_1", 1)),
    )
    assert r.status == "WARNING"
    assert r.reason == "UNVERIFIED_IN_SCOPE_REJECTIONS"
    assert r.unverified_rejections == 1


def test_rejected_with_missing_reason_is_warning_not_scope_assumption():
    r = _classify(seen=4, ingested=0, rejected=4, targets=0)
    assert r.status == "WARNING"
    assert r.unverified_rejections == 4


def test_reconciliation_mismatch_warns_even_with_valid_target_count():
    r = _classify(seen=10, ingested=9, rejected=0, targets=9)
    assert r.status == "WARNING"
    assert r.reason == "RECONCILIATION_MISMATCH"


def test_negative_counts_fail_closed():
    import pytest
    with pytest.raises(ValueError):
        _classify(seen=-1)
    with pytest.raises(ValueError):
        _classify(seen=1, ingested=0, rejected=1, targets=0,
                  reasons=(("unsupported_event_status", -1),))


def test_overreported_out_of_scope_reason_cannot_hide_behind_healthy_targets():
    # Ten observed, eight ingested, two rejected: a scope reason of three
    # is contradictory even when five valid targets exist on the same date.
    r = _classify(
        seen=10, ingested=8, rejected=2, targets=5,
        reasons=(("out_of_scope_event_type", 3),),
    )
    assert r.status == "WARNING"
    assert r.reason == "RECONCILIATION_MISMATCH"


def test_overreported_out_of_scope_reason_is_not_legitimate_empty_slate():
    r = _classify(
        seen=3, ingested=0, rejected=3, targets=0,
        reasons=(("out_of_scope_event_type", 4),),
    )
    assert r.status == "WARNING"
    assert r.reason == "RECONCILIATION_MISMATCH"
