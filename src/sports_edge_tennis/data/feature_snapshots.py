"""Immutable, run-linked prediction feature snapshots.

Purpose: prove which numeric features a specific prediction actually consumed,
rather than reconstructing them later from mutable state. One row per
``predictions.prediction_id``; UPDATE/DELETE are blocked by an append-only
trigger (migration 0023).

Only values genuinely available at prediction time are stored. In particular
``rating_uncertainty`` is NULL unless a producer computed it; this module never
derives or backfills it.

Definition used for ``identity_complete``: both canonical players have at least
one prior match in the shared rating pool (``overall_match_count > 0``). It is a
history-completeness flag, not a claim that a rating metric was validated, and
it is distinct from *identity* completeness in the OddsPapi/player-source sense.

Lineage: a snapshot is bound to the immutable prediction row, its run, match,
match version with the same player orientation, and the agent configuration
version that produced it. The database enforces all four bindings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sports_edge_tennis.agents.agent1_surface_elo import (
    SUPPORTED_SURFACES,
    PlayerRatingState,
)

SNAPSHOT_TABLE = "prediction_feature_snapshots"
SNAPSHOT_CONTRACT_VERSION = "prediction_feature_snapshot_v2"


class FeatureSnapshotError(ValueError):
    """Raised when a feature snapshot is structurally invalid or immutable."""


def _utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise FeatureSnapshotError(f"{field_name} must be a datetime")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _decimal(value: Decimal | int | str | None, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FeatureSnapshotError(f"{field_name} must be Decimal-compatible") from exc
    if not result.is_finite():
        raise FeatureSnapshotError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True)
class PredictionFeatureSnapshot:
    prediction_id: int
    prediction_run_id: int
    match_id: int
    match_version_id: int
    agent_config_version_id: int
    agent_code: str
    player_1_id: int
    player_2_id: int
    surface: str | None
    player_1_overall_rating: Decimal | None
    player_2_overall_rating: Decimal | None
    player_1_overall_match_count: int | None
    player_2_overall_match_count: int | None
    player_1_surface_rating: Decimal | None
    player_2_surface_rating: Decimal | None
    player_1_surface_match_count: int | None
    player_2_surface_match_count: int | None
    rating_uncertainty: Decimal | None
    rating_uncertainty_source: str | None
    identity_complete: bool
    surface_known: bool
    main_draw_verified: bool
    model_version: str
    feature_version: str
    as_of: datetime
    prediction_locked_at: datetime

    def __post_init__(self) -> None:
        for name in ("prediction_id", "prediction_run_id", "match_id",
                     "match_version_id", "agent_config_version_id",
                     "player_1_id", "player_2_id"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise FeatureSnapshotError(f"{name} must be a positive integer")
        if self.player_1_id == self.player_2_id:
            raise FeatureSnapshotError("player_1_id and player_2_id must differ")
        if not self.agent_code or not self.agent_code.strip():
            raise FeatureSnapshotError("agent_code must be non-empty")
        for name in ("model_version", "feature_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise FeatureSnapshotError(f"{name} must be non-empty")
        if self.surface is not None and self.surface not in SUPPORTED_SURFACES:
            raise FeatureSnapshotError(
                f"surface must be one of {SUPPORTED_SURFACES} or None"
            )
        for name in ("player_1_overall_match_count", "player_2_overall_match_count",
                     "player_1_surface_match_count", "player_2_surface_match_count"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise FeatureSnapshotError(f"{name} must be a non-negative integer")
        for name in ("player_1_overall_rating", "player_2_overall_rating",
                     "player_1_surface_rating", "player_2_surface_rating",
                     "rating_uncertainty"):
            object.__setattr__(
                self, name, _decimal(getattr(self, name), field_name=name)
            )
        if self.rating_uncertainty is not None:
            if not self.rating_uncertainty_source or not self.rating_uncertainty_source.strip():
                raise FeatureSnapshotError(
                    "rating_uncertainty requires rating_uncertainty_source"
                )
        elif self.rating_uncertainty_source is not None:
            raise FeatureSnapshotError(
                "rating_uncertainty_source requires rating_uncertainty"
            )
        as_of = _utc(self.as_of, field_name="as_of")
        locked = _utc(self.prediction_locked_at, field_name="prediction_locked_at")
        if as_of > locked:
            raise FeatureSnapshotError("as_of must not be after prediction_locked_at")
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "prediction_locked_at", locked)
        if self.identity_complete and (
            self.player_1_overall_match_count is None
            or self.player_2_overall_match_count is None
        ):
            raise FeatureSnapshotError(
                "identity_complete requires both overall match counts"
            )
        if self.surface_known and (
            self.surface is None
            or self.player_1_surface_match_count is None
            or self.player_2_surface_match_count is None
        ):
            raise FeatureSnapshotError(
                "surface_known requires surface and both surface counts"
            )

    def to_row(self) -> tuple[object, ...]:
        return (
            self.prediction_id,
            self.prediction_run_id,
            self.match_id,
            self.match_version_id,
            self.agent_config_version_id,
            self.agent_code,
            self.player_1_id,
            self.player_2_id,
            self.surface,
            self.player_1_overall_rating,
            self.player_2_overall_rating,
            self.player_1_overall_match_count,
            self.player_2_overall_match_count,
            self.player_1_surface_rating,
            self.player_2_surface_rating,
            self.player_1_surface_match_count,
            self.player_2_surface_match_count,
            self.rating_uncertainty,
            self.rating_uncertainty_source,
            self.identity_complete,
            self.surface_known,
            self.main_draw_verified,
            self.model_version,
            self.feature_version,
            self.as_of,
            self.prediction_locked_at,
        )


@dataclass(frozen=True)
class Agent1FeatureContext:
    """The *exact* Agent 1 rating inputs used for one prediction request.

    ``PlayerRatingState`` is frozen, so holding these two objects is a genuine
    point-in-time capture. It is deliberately built from the per-request
    resolution (including cold-start defaults), never from a replay that may be
    advanced to a later cutoff afterwards.
    """

    player_1: PlayerRatingState
    player_2: PlayerRatingState
    surface: str | None
    qualifying_flag: bool
    model_version: str
    feature_version: str
    as_of: datetime
    rating_uncertainty: Decimal | None = None
    rating_uncertainty_source: str | None = None

    def to_snapshot(
        self,
        *,
        prediction_id: int,
        prediction_run_id: int,
        match_id: int,
        match_version_id: int,
        agent_config_version_id: int,
        prediction_locked_at: datetime,
    ) -> PredictionFeatureSnapshot:
        snapshot = build_agent1_feature_snapshot(
            prediction_id=prediction_id,
            prediction_run_id=prediction_run_id,
            match_id=match_id,
            match_version_id=match_version_id,
            agent_config_version_id=agent_config_version_id,
            player_1=self.player_1,
            player_2=self.player_2,
            surface=self.surface,
            qualifying_flag=self.qualifying_flag,
            model_version=self.model_version,
            feature_version=self.feature_version,
            as_of=self.as_of,
            prediction_locked_at=prediction_locked_at,
            rating_uncertainty=self.rating_uncertainty,
            rating_uncertainty_source=self.rating_uncertainty_source,
        )
        return snapshot


def build_agent1_feature_snapshot(
    *,
    prediction_id: int,
    prediction_run_id: int,
    match_id: int,
    match_version_id: int,
    agent_config_version_id: int,
    player_1: PlayerRatingState,
    player_2: PlayerRatingState,
    surface: str | None,
    qualifying_flag: bool,
    model_version: str,
    feature_version: str,
    as_of: datetime,
    prediction_locked_at: datetime,
    rating_uncertainty: Decimal | None = None,
    rating_uncertainty_source: str | None = None,
) -> PredictionFeatureSnapshot:
    """Build a snapshot from the exact states Agent 1 used for a prediction."""
    if player_1.player_id == player_2.player_id:
        raise FeatureSnapshotError("player states must describe different players")
    if player_1.player_id <= 0 or player_2.player_id <= 0:
        raise FeatureSnapshotError("player state ids must be positive")
    if surface is not None and surface not in SUPPORTED_SURFACES:
        raise FeatureSnapshotError(f"unsupported surface {surface!r}")
    surface_known = surface is not None
    p1_surface_rating = player_1.surface_ratings.get(surface) if surface_known else None
    p2_surface_rating = player_2.surface_ratings.get(surface) if surface_known else None
    # A player with no surface history still has a genuine count of zero; using
    # dict.get() here would fabricate NULL and make every cold-start snapshot
    # unsaveable. Absent surface ratings stay NULL (never blended).
    p1_surface_count = player_1.surface_match_count(surface) if surface_known else None
    p2_surface_count = player_2.surface_match_count(surface) if surface_known else None
    identity_complete = (
        player_1.overall_match_count > 0 and player_2.overall_match_count > 0
    )
    return PredictionFeatureSnapshot(
        prediction_id=prediction_id,
        prediction_run_id=prediction_run_id,
        match_id=match_id,
        match_version_id=match_version_id,
        agent_config_version_id=agent_config_version_id,
        agent_code="AGENT_1_SURFACE_ELO",
        player_1_id=player_1.player_id,
        player_2_id=player_2.player_id,
        surface=surface if surface_known else None,
        player_1_overall_rating=player_1.overall_rating,
        player_2_overall_rating=player_2.overall_rating,
        player_1_overall_match_count=player_1.overall_match_count,
        player_2_overall_match_count=player_2.overall_match_count,
        player_1_surface_rating=p1_surface_rating,
        player_2_surface_rating=p2_surface_rating,
        player_1_surface_match_count=p1_surface_count,
        player_2_surface_match_count=p2_surface_count,
        rating_uncertainty=rating_uncertainty,
        rating_uncertainty_source=rating_uncertainty_source,
        identity_complete=identity_complete,
        surface_known=surface_known,
        main_draw_verified=not qualifying_flag,
        model_version=model_version,
        feature_version=feature_version,
        as_of=as_of,
        prediction_locked_at=prediction_locked_at,
    )


_INSERT_SQL = f"""
INSERT INTO {SNAPSHOT_TABLE} (
    prediction_id,
    prediction_run_id,
    match_id,
    match_version_id,
    agent_config_version_id,
    agent_code,
    player_1_id,
    player_2_id,
    surface,
    player_1_overall_rating,
    player_2_overall_rating,
    player_1_overall_match_count,
    player_2_overall_match_count,
    player_1_surface_rating,
    player_2_surface_rating,
    player_1_surface_match_count,
    player_2_surface_match_count,
    rating_uncertainty,
    rating_uncertainty_source,
    identity_complete,
    surface_known,
    main_draw_verified,
    model_version,
    feature_version,
    as_of,
    prediction_locked_at
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s::tennis_surface,
    %s::numeric, %s::numeric, %s, %s,
    %s::numeric, %s::numeric, %s, %s,
    %s::numeric, %s, %s, %s::boolean, %s, %s, %s, %s, %s
)
RETURNING prediction_feature_snapshot_id
"""


def persist_feature_snapshot(conn, snapshot: PredictionFeatureSnapshot) -> int:
    """Insert one immutable snapshot. A duplicate prediction_id is rejected."""
    row = conn.execute(_INSERT_SQL, snapshot.to_row()).fetchone()
    if row is None:  # pragma: no cover - driver contract guard
        raise FeatureSnapshotError("snapshot insert returned no id")
    return int(row[0])


def load_feature_snapshot(conn, *, prediction_id: int) -> dict[str, object] | None:
    row = conn.execute(
        f"""SELECT prediction_feature_snapshot_id, prediction_id, prediction_run_id,
                   match_id, match_version_id, agent_config_version_id,
                   agent_code::text, player_1_id, player_2_id, surface::text,
                   player_1_overall_rating, player_2_overall_rating,
                   player_1_overall_match_count, player_2_overall_match_count,
                   player_1_surface_rating, player_2_surface_rating,
                   player_1_surface_match_count, player_2_surface_match_count,
                   rating_uncertainty, rating_uncertainty_source,
                   identity_complete, surface_known, main_draw_verified,
                   model_version, feature_version, as_of, prediction_locked_at
            FROM {SNAPSHOT_TABLE} WHERE prediction_id = %s""",
        (prediction_id,),
    ).fetchone()
    if row is None:
        return None
    keys = (
        "prediction_feature_snapshot_id", "prediction_id", "prediction_run_id",
        "match_id", "match_version_id", "agent_config_version_id",
        "agent_code", "player_1_id", "player_2_id", "surface",
        "player_1_overall_rating", "player_2_overall_rating",
        "player_1_overall_match_count", "player_2_overall_match_count",
        "player_1_surface_rating", "player_2_surface_rating",
        "player_1_surface_match_count", "player_2_surface_match_count",
        "rating_uncertainty", "rating_uncertainty_source",
        "identity_complete", "surface_known", "main_draw_verified",
        "model_version", "feature_version", "as_of", "prediction_locked_at",
    )
    return dict(zip(keys, row, strict=True))


__all__ = [
    "Agent1FeatureContext",
    "FeatureSnapshotError",
    "PredictionFeatureSnapshot",
    "SNAPSHOT_CONTRACT_VERSION",
    "SNAPSHOT_TABLE",
    "build_agent1_feature_snapshot",
    "load_feature_snapshot",
    "persist_feature_snapshot",
]
