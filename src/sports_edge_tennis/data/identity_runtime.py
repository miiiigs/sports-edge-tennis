"""Durable runtime identity-resolution provenance (migration 0025).

The Agent 1 identity bridge is a read-time mapping. A later replay can only be exact
if the *runtime* decision was persisted when it was made: which original player id was
mapped to which model player id, on which immutable match version, using which alias or
bridge event, at what observation time, against which reference-state digest.

This module is the writer/reader for ``identity_resolution_provenance``. It is
deliberately independent of the prediction write path:

* :func:`persist_identity_resolutions` accepts already-computed observations and
  writes them in one transaction. It validates the match version and run linkage and
  is idempotent per ``(prediction_run_id, match_id, original_player_id)``.
* :func:`load_identity_resolutions` reads them back and verifies each row's digest.
* :func:`observations_from_forward_capture` converts the outcome-free JSONL capture
  produced by the forward shadow into the same observations, so a first-issued capture
  can be integrated into the durable table without re-resolving identity.

Default behaviour for the live runner is unchanged: no caller passes observations, so
no row is written. Activation of the bridge is a separate, owner-approved change.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.data.model_identity import (
    METHOD_ALIAS,
    METHOD_AMBIGUOUS,
    METHOD_HISTORICAL_ID,
    METHOD_NO_CANDIDATE,
    METHOD_NO_HISTORICAL_CANDIDATE,
    METHOD_NO_PLAYER,
    METHOD_PERSISTED_BRIDGE,
)

SCHEMA_VERSION = "identity_resolution_provenance_v1"
CAPTURE_SOURCE_LIVE = "LIVE_RUNTIME"
CAPTURE_SOURCE_SHADOW = "FORWARD_SHADOW"
CAPTURE_SOURCES = (CAPTURE_SOURCE_LIVE, CAPTURE_SOURCE_SHADOW)
METHODS = (
    METHOD_HISTORICAL_ID,
    METHOD_PERSISTED_BRIDGE,
    METHOD_ALIAS,
    METHOD_AMBIGUOUS,
    METHOD_NO_CANDIDATE,
    METHOD_NO_HISTORICAL_CANDIDATE,
    METHOD_NO_PLAYER,
)


class IdentityProvenanceError(ValueError):
    """Raised when a runtime resolution record cannot be stored or verified."""


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise IdentityProvenanceError("identity provenance timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise IdentityProvenanceError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _digest64(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text.lower()):
        raise IdentityProvenanceError(f"{field_name} must be a 64-character hex digest")
    return text.lower()


@dataclass(frozen=True)
class IdentityResolutionObservation:
    """One exact player-resolution decision made by a runtime or shadow resolver."""

    prediction_run_id: int
    match_id: int
    match_version_id: int
    predicted_at: datetime
    original_player_id: int
    model_player_id: int
    method: str
    evidence: str
    name_source: str
    reference_policy: str
    reference_sha256: str
    capture_source: str = CAPTURE_SOURCE_LIVE
    candidate_count: int = 0
    matched_alias: str | None = None
    bridge_event_id: int | None = None
    visible_from: datetime | None = None
    source_record_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.prediction_run_id <= 0 or self.match_id <= 0 or self.match_version_id <= 0:
            raise IdentityProvenanceError("run, match and match version ids must be positive")
        if self.original_player_id <= 0 or self.model_player_id <= 0:
            raise IdentityProvenanceError("original and model player ids must be positive")
        if self.method not in METHODS:
            raise IdentityProvenanceError(f"unknown resolution method {self.method!r}")
        if not str(self.evidence).strip() or not str(self.name_source).strip():
            raise IdentityProvenanceError("evidence and name_source must be non-empty")
        if not str(self.reference_policy).strip():
            raise IdentityProvenanceError("reference_policy must be non-empty")
        if self.capture_source not in CAPTURE_SOURCES:
            raise IdentityProvenanceError(f"unknown capture_source {self.capture_source!r}")
        if self.candidate_count < 0:
            raise IdentityProvenanceError("candidate_count must be non-negative")
        _digest64(self.reference_sha256, field_name="reference_sha256")
        _digest64(self.source_record_sha256, field_name="source_record_sha256")
        predicted_at = _utc(self.predicted_at, field_name="predicted_at")
        object.__setattr__(self, "predicted_at", predicted_at)
        if self.visible_from is not None:
            visible_from = _utc(self.visible_from, field_name="visible_from")
            if visible_from > predicted_at:
                raise IdentityProvenanceError(
                    "visible_from cannot be later than the resolution observation time"
                )
            object.__setattr__(self, "visible_from", visible_from)

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "prediction_run_id": self.prediction_run_id,
            "match_id": self.match_id,
            "match_version_id": self.match_version_id,
            "predicted_at": _iso(self.predicted_at),
            "original_player_id": self.original_player_id,
            "model_player_id": self.model_player_id,
            "resolution_method": self.method,
            "evidence": self.evidence,
            "name_source": self.name_source,
            "candidate_count": self.candidate_count,
            "matched_alias": self.matched_alias,
            "bridge_event_id": self.bridge_event_id,
            "visible_from": _iso(self.visible_from),
            "reference_policy": self.reference_policy,
            "reference_sha256": self.reference_sha256,
            "source_record_sha256": self.source_record_sha256,
            "capture_source": self.capture_source,
        }

    @property
    def record_sha256(self) -> str:
        return _sha256(self.as_record())


@dataclass(frozen=True)
class PersistedIdentityResolution:
    identity_resolution_provenance_id: int
    observation: IdentityResolutionObservation
    record_sha256: str
    created_at: datetime

    @property
    def verified(self) -> bool:
        return self.record_sha256 == self.observation.record_sha256


def _validate_versions(
    conn: psycopg.Connection,
    observations: Sequence[IdentityResolutionObservation],
) -> None:
    """Fail closed unless each match version and run linkage was visible at observation."""
    if not observations:
        return
    version_ids = sorted({o.match_version_id for o in observations})
    rows = conn.execute(
        """
        SELECT match_version_id, match_id, as_of, source_observed_at, ingested_at
        FROM match_versions
        WHERE match_version_id = ANY(%s)
        """,
        (version_ids,),
    ).fetchall()
    by_id = {int(row[0]): row for row in rows}
    run_ids = sorted({o.prediction_run_id for o in observations})
    run_rows = conn.execute(
        """
        SELECT prediction_run_id, as_of
        FROM prediction_runs
        WHERE prediction_run_id = ANY(%s)
        """,
        (run_ids,),
    ).fetchall()
    run_as_of = {int(row[0]): row[1] for row in run_rows}
    for observation in observations:
        version = by_id.get(observation.match_version_id)
        if version is None:
            raise IdentityProvenanceError(
                f"match_version_id {observation.match_version_id} does not exist"
            )
        _, match_id, version_as_of, source_observed_at, ingested_at = version
        if int(match_id) != observation.match_id:
            raise IdentityProvenanceError(
                "match_version_id does not belong to the persisted match_id"
            )
        for label, value in (
            ("match_versions.as_of", version_as_of),
            ("match_versions.source_observed_at", source_observed_at),
            ("match_versions.ingested_at", ingested_at),
        ):
            if value is not None and value > observation.predicted_at:
                raise IdentityProvenanceError(
                    f"{label} is later than the resolution observation time"
                )
        run_time = run_as_of.get(observation.prediction_run_id)
        if observation.prediction_run_id not in run_as_of:
            raise IdentityProvenanceError(
                f"prediction_run_id {observation.prediction_run_id} does not exist"
            )
        if run_time is not None and run_time > observation.predicted_at:
            raise IdentityProvenanceError(
                "prediction run as_of is later than the resolution observation time"
            )


def persist_identity_resolutions(
    conn: psycopg.Connection,
    observations: Iterable[IdentityResolutionObservation],
    *,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Persist exact runtime resolutions atomically and idempotently.

    Re-persisting the same key with the same digest is a no-op. Re-persisting the same
    key with a different digest raises: a different decision for the same immutable
    run/match/player is a lineage conflict, not a correction.
    """
    records = list(observations)
    if not records:
        return {"inserted": 0, "existing": 0, "total": 0}
    _validate_versions(conn, records)
    inserted = 0
    existing = 0
    with conn.transaction():
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            keys = [
                (o.prediction_run_id, o.match_id, o.original_player_id) for o in batch
            ]
            prior = conn.execute(
                """
                SELECT prediction_run_id, match_id, original_player_id, record_sha256
                FROM identity_resolution_provenance
                WHERE (prediction_run_id, match_id, original_player_id)
                      IN (SELECT * FROM unnest(%s::bigint[], %s::bigint[], %s::bigint[]))
                """,
                (
                    [k[0] for k in keys],
                    [k[1] for k in keys],
                    [k[2] for k in keys],
                ),
            ).fetchall()
            prior_by_key = {
                (int(row[0]), int(row[1]), int(row[2])): str(row[3]) for row in prior
            }
            for observation in batch:
                key = (
                    observation.prediction_run_id,
                    observation.match_id,
                    observation.original_player_id,
                )
                if key in prior_by_key:
                    if prior_by_key[key] != observation.record_sha256:
                        raise IdentityProvenanceError(
                            "identity provenance lineage conflict: the same run/match/player "
                            "already has a different recorded digest"
                        )
                    existing += 1
                    continue
                record = observation.as_record()
                conn.execute(
                    """
                    INSERT INTO identity_resolution_provenance (
                        prediction_run_id, match_id, match_version_id, predicted_at,
                        original_player_id, model_player_id, resolution_method,
                        evidence, name_source, candidate_count, matched_alias,
                        bridge_event_id, visible_from, reference_policy,
                        reference_sha256, record_sha256, source_record_sha256,
                        capture_source
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        record["prediction_run_id"],
                        record["match_id"],
                        record["match_version_id"],
                        observation.predicted_at,
                        record["original_player_id"],
                        record["model_player_id"],
                        record["resolution_method"],
                        record["evidence"],
                        record["name_source"],
                        record["candidate_count"],
                        record["matched_alias"],
                        record["bridge_event_id"],
                        observation.visible_from,
                        record["reference_policy"],
                        record["reference_sha256"],
                        observation.record_sha256,
                        record["source_record_sha256"],
                        record["capture_source"],
                    ),
                )
                inserted += 1
    return {"inserted": inserted, "existing": existing, "total": len(records)}


def load_identity_resolutions(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    match_id: int | None = None,
) -> list[PersistedIdentityResolution]:
    """Read back persisted resolutions and verify their digests."""
    where = ["prediction_run_id = %s"]
    params: list[object] = [prediction_run_id]
    if match_id is not None:
        where.append("match_id = %s")
        params.append(match_id)
    rows = conn.execute(
        f"""
        SELECT identity_resolution_provenance_id, prediction_run_id, match_id,
               match_version_id, predicted_at, original_player_id, model_player_id,
               resolution_method, evidence, name_source, candidate_count,
               matched_alias, bridge_event_id, visible_from, reference_policy,
               reference_sha256, record_sha256, source_record_sha256, capture_source,
               created_at
        FROM identity_resolution_provenance
        WHERE {' AND '.join(where)}
        ORDER BY match_id, original_player_id
        """,
        tuple(params),
    ).fetchall()
    out: list[PersistedIdentityResolution] = []
    for row in rows:
        observation = IdentityResolutionObservation(
            prediction_run_id=int(row[1]),
            match_id=int(row[2]),
            match_version_id=int(row[3]),
            predicted_at=row[4],
            original_player_id=int(row[5]),
            model_player_id=int(row[6]),
            method=str(row[7]),
            evidence=str(row[8]),
            name_source=str(row[9]),
            candidate_count=int(row[10]),
            matched_alias=row[11],
            bridge_event_id=int(row[12]) if row[12] is not None else None,
            visible_from=row[13],
            reference_policy=str(row[14]),
            reference_sha256=str(row[15]),
            source_record_sha256=row[17],
            capture_source=str(row[18]),
        )
        persisted = PersistedIdentityResolution(
            identity_resolution_provenance_id=int(row[0]),
            observation=observation,
            record_sha256=str(row[16]),
            created_at=row[19],
        )
        if not persisted.verified:
            raise IdentityProvenanceError(
                "persisted identity provenance digest does not match its fields"
            )
        out.append(persisted)
    return out


def observations_from_forward_capture(
    records: Iterable[dict[str, Any]],
    *,
    capture_source: str = CAPTURE_SOURCE_SHADOW,
) -> list[IdentityResolutionObservation]:
    """Convert outcome-free forward-shadow capture records into durable observations.

    The capture's own ``record_sha256`` is preserved in ``source_record_sha256`` so the
    JSONL archive and the database row remain cross-verifiable.
    """
    observations: list[IdentityResolutionObservation] = []
    for record in records:
        if record.get("record_type", "capture") != "capture":
            continue
        if record.get("outcome_included") is not False:
            raise IdentityProvenanceError("capture record must explicitly exclude outcomes")
        if record.get("production_changed") is not False:
            raise IdentityProvenanceError("capture record must not claim a production change")
        reference = record.get("identity_reference") or {}
        reference_sha = _digest64(
            reference.get("reference_sha256"), field_name="reference_sha256"
        )
        if reference_sha is None:
            raise IdentityProvenanceError("capture record is missing a reference digest")
        predicted_at = datetime.fromisoformat(str(record["prediction_as_of"]))
        for slot, original_key, model_key in (
            (1, "live_player_1_id", "model_player_1_id"),
            (2, "live_player_2_id", "model_player_2_id"),
        ):
            resolution = record[f"player_{slot}_resolution"]
            observations.append(
                IdentityResolutionObservation(
                    prediction_run_id=int(record["prediction_run_id"]),
                    match_id=int(record["match_id"]),
                    match_version_id=int(record["match_version_id"]),
                    predicted_at=predicted_at,
                    original_player_id=int(record[original_key]),
                    model_player_id=int(record[model_key]),
                    method=str(resolution["method"]),
                    evidence=str(resolution["evidence"]),
                    name_source=str(resolution["name_source"]),
                    candidate_count=int(resolution.get("candidate_count") or 0),
                    matched_alias=resolution.get("matched_alias"),
                    bridge_event_id=(
                        int(resolution["bridge_event_id"])
                        if resolution.get("bridge_event_id") is not None
                        else None
                    ),
                    visible_from=(
                        datetime.fromisoformat(str(resolution["visible_from"]))
                        if resolution.get("visible_from")
                        else None
                    ),
                    reference_policy=str(reference.get("policy_version") or ""),
                    reference_sha256=reference_sha,
                    source_record_sha256=_digest64(
                        record.get("record_sha256"), field_name="record_sha256"
                    ),
                    capture_source=capture_source,
                )
            )
    return observations


def read_capture_archive(path: str | Path) -> list[dict[str, Any]]:
    """Read and hash-verify a forward-capture JSONL archive's capture records."""
    records: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("record_type", "capture") != "capture":
            continue
        stored = record.pop("record_sha256", None)
        if _digest64(stored, field_name="record_sha256") != _sha256(record):
            raise IdentityProvenanceError("capture record digest verification failed")
        record["record_sha256"] = stored
        records.append(record)
    return records


__all__ = [
    "CAPTURE_SOURCES",
    "CAPTURE_SOURCE_LIVE",
    "CAPTURE_SOURCE_SHADOW",
    "SCHEMA_VERSION",
    "IdentityProvenanceError",
    "IdentityResolutionObservation",
    "PersistedIdentityResolution",
    "load_identity_resolutions",
    "observations_from_forward_capture",
    "persist_identity_resolutions",
    "read_capture_archive",
]
