from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sports_edge_tennis.replay.contracts import PitInputProof
from sports_edge_tennis.replay.pit import (
    PitIncompleteError,
    PointInTimeViolation,
    assert_decision_ordering,
    assert_not_after,
    assert_odds_visible_at,
    assert_proof_visible,
    assert_result_not_visible_at_prediction,
    require_tz_aware,
)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 12, hour, minute, tzinfo=timezone.utc)


class _Odds:
    def __init__(self, *, source_observed_at, as_of):
        self.source_observed_at = source_observed_at
        self.as_of = as_of


def test_require_tz_aware_rejects_naive():
    with pytest.raises(PointInTimeViolation):
        require_tz_aware(datetime(2026, 9, 12, 10, 0), field_name="x")


def test_assert_not_after_allows_equal_and_before():
    assert_not_after(_utc(10), kind="k", timestamps=(_utc(9), _utc(10), None))


def test_assert_not_after_rejects_future():
    with pytest.raises(PointInTimeViolation):
        assert_not_after(_utc(10), kind="k", timestamps=(_utc(9), _utc(11)))


def test_odds_visible_at_rejects_future_odds():
    odds = [_Odds(source_observed_at=_utc(9, 50), as_of=_utc(9, 50))]
    assert_odds_visible_at(odds, cutoff=_utc(10))

    future = [_Odds(source_observed_at=_utc(10, 30), as_of=_utc(10, 30))]
    with pytest.raises(PointInTimeViolation):
        assert_odds_visible_at(future, cutoff=_utc(10))


def test_decision_ordering_requires_market_after_prediction():
    assert_decision_ordering(prediction_as_of=_utc(8), market_as_of=_utc(10))
    with pytest.raises(PointInTimeViolation):
        assert_decision_ordering(prediction_as_of=_utc(10), market_as_of=_utc(9))


def test_result_must_not_be_visible_at_prediction():
    assert_result_not_visible_at_prediction(
        result_visible_at=_utc(14), prediction_as_of=_utc(8)
    )
    with pytest.raises(PointInTimeViolation):
        assert_result_not_visible_at_prediction(
            result_visible_at=_utc(8), prediction_as_of=_utc(8)
        )
    with pytest.raises(PointInTimeViolation):
        assert_result_not_visible_at_prediction(
            result_visible_at=_utc(7), prediction_as_of=_utc(8)
        )


def test_proof_visible_requires_matching_cutoff_and_complete_evidence():
    proof = PitInputProof(
        kind="ODDS",
        cutoff=_utc(10),
        max_source_observed_at=_utc(9),
        max_as_of=_utc(9),
        evidence_count=3,
        snapshot_sha256="a" * 64,
    )
    assert_proof_visible(proof, cutoff=_utc(10))

    with pytest.raises(PointInTimeViolation):
        assert_proof_visible(proof, cutoff=_utc(11))

    incomplete = PitInputProof(
        kind="ODDS",
        cutoff=_utc(10),
        max_source_observed_at=_utc(9),
        max_as_of=_utc(9),
        evidence_count=3,
        snapshot_sha256="a" * 64,
        pit_complete=False,
    )
    with pytest.raises(PitIncompleteError):
        assert_proof_visible(incomplete, cutoff=_utc(10))

    with pytest.raises(PitIncompleteError):
        assert_proof_visible(None, cutoff=_utc(10))


def test_proof_with_future_evidence_fails_closed():
    proof = PitInputProof(
        kind="ODDS",
        cutoff=_utc(10),
        max_source_observed_at=_utc(9),
        max_as_of=_utc(11),
        evidence_count=1,
        snapshot_sha256="b" * 64,
    )
    with pytest.raises(PointInTimeViolation):
        assert_proof_visible(proof, cutoff=_utc(10))
