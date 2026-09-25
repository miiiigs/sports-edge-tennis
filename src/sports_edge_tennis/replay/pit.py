"""Point-in-time enforcement primitives for the replay harness.

The absolute rule is: at historical timestamp ``T`` the replay may use only
evidence available at or before ``T``. This module fails closed instead of
silently substituting current information.

Two distinct failure modes are represented:

- :class:`PointInTimeViolation` — a fact *is* timestamped after the cutoff. This
  is a leakage defect and always raises.
- missing evidence — the system marks ``DATA_NOT_AVAILABLE_PIT`` and abstains /
  fails closed; it never infers a substitute.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sports_edge_tennis.replay.contracts import PitInputProof


class PointInTimeViolation(ValueError):
    """Raised when evidence timestamped after the cutoff would be consumed."""


class PitIncompleteError(ValueError):
    """Raised when a caller requires complete PIT evidence that is not present."""


def require_tz_aware(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PointInTimeViolation(f"{field_name} must be a timezone-aware datetime")
    return value


def assert_not_after(
    cutoff: datetime,
    *,
    kind: str,
    timestamps: Iterable[datetime | None],
) -> None:
    """Raise when any non-null timestamp is later than ``cutoff``."""

    require_tz_aware(cutoff, field_name="cutoff")
    for observed in timestamps:
        if observed is None:
            continue
        require_tz_aware(observed, field_name=f"{kind}.observed_at")
        if observed > cutoff:
            raise PointInTimeViolation(
                f"{kind} evidence observed at {observed.isoformat()} is after "
                f"cutoff {cutoff.isoformat()}"
            )


def assert_decision_ordering(
    *,
    prediction_as_of: datetime,
    market_as_of: datetime,
) -> None:
    """Market evidence must never predate the blind prediction cutoff."""

    require_tz_aware(prediction_as_of, field_name="prediction_as_of")
    require_tz_aware(market_as_of, field_name="market_as_of")
    if market_as_of < prediction_as_of:
        raise PointInTimeViolation(
            "market_as_of must be >= prediction_as_of for a coherent replay run"
        )


def assert_odds_visible_at(
    odds_snapshots: Iterable[object],
    *,
    cutoff: datetime,
) -> None:
    """Fail closed if any odds row was observed or valid after ``cutoff``.

    The database loader already applies ``source_observed_at <= cutoff AND
    as_of <= cutoff``. This second, engine-level check defends the replay
    pipeline against a loader regression or a hand-built input bundle.
    """

    require_tz_aware(cutoff, field_name="cutoff")
    for snapshot in odds_snapshots:
        source_observed_at = getattr(snapshot, "source_observed_at", None)
        as_of = getattr(snapshot, "as_of", None)
        assert_not_after(
            cutoff,
            kind="odds_snapshot_source_observed_at",
            timestamps=(source_observed_at,),
        )
        assert_not_after(
            cutoff,
            kind="odds_snapshot_as_of",
            timestamps=(as_of,),
        )


def assert_result_not_visible_at_prediction(
    *,
    result_visible_at: datetime | None,
    prediction_as_of: datetime,
) -> None:
    """A scoring result must not have been observable at prediction time.

    If a result is knowable at or before the prediction cutoff, the observation
    is either mis-dated or the match was already decided, so replay fails closed.
    """

    if result_visible_at is None:
        return
    require_tz_aware(result_visible_at, field_name="result_visible_at")
    require_tz_aware(prediction_as_of, field_name="prediction_as_of")
    if result_visible_at <= prediction_as_of:
        raise PointInTimeViolation(
            "actual result was visible at or before the prediction cutoff"
        )


def proof_is_complete(proof: PitInputProof | None) -> bool:
    return proof is not None and proof.pit_complete


def assert_proof_visible(proof: PitInputProof | None, *, cutoff: datetime) -> None:
    """Verify a loader-supplied proof is visible at ``cutoff``."""

    if proof is None:
        raise PitIncompleteError("missing PIT proof")
    if not proof.pit_complete:
        raise PitIncompleteError("PIT proof is marked incomplete")
    if not proof.cutoff == cutoff:
        raise PointInTimeViolation(
            f"PIT proof cutoff {proof.cutoff.isoformat()} does not equal "
            f"replay cutoff {cutoff.isoformat()}"
        )
    assert_not_after(
        cutoff,
        kind=f"{proof.kind}.max_source_observed_at",
        timestamps=(proof.max_source_observed_at,),
    )
    assert_not_after(
        cutoff,
        kind=f"{proof.kind}.max_as_of",
        timestamps=(proof.max_as_of,),
    )


__all__ = [
    "PointInTimeViolation",
    "PitIncompleteError",
    "require_tz_aware",
    "assert_not_after",
    "assert_decision_ordering",
    "assert_odds_visible_at",
    "assert_result_not_visible_at_prediction",
    "proof_is_complete",
    "assert_proof_visible",
]
