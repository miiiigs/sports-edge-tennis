"""Versioned, immutable identity provenance for the model-identity bridge.

Why this exists
---------------
The bridge maps live provider player IDs onto the historical model namespace at read
time. That mapping depends on *mutable* reference data (``players.canonical_name``,
``player_aliases``) and on caller-dated bridge events. A retrospective replay can
therefore only be trusted if the exact reference state it used is recorded at the
moment of use.

This module provides that record without a schema migration:

* :func:`reference_fingerprint` computes a deterministic SHA-256 over every
  PIT-visible reference row that can influence one resolution. The digest is stored
  with the capture.
* :func:`resolve_with_provenance` returns the resolution plus the fingerprint, so a
  caller can persist "original id -> model id via evidence X, observed at T, against
  reference state H, on match version V".
* :func:`reference_drift_for` re-computes the digest later and reports whether the
  reference rows changed. An out-of-band alias re-pointing or player rename is
  therefore *detectable* even though the schema cannot version it yet.

The durable form is an append-only JSONL record (see
``validation.a1_identity_bridge_forward_shadow``). A database table for the same
facts is specified in ``specs/identity_provenance_v2_20261125.md`` and is reserved
for central integration PR #392; no migration number is allocated here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from sports_edge_tennis.data.model_identity import (
    IDENTITY_REFERENCE_POLICY_VERSION,
    IdentityResolution,
    resolve_model_player_ids_with_provenance,
)

IDENTITY_PROVENANCE_SCHEMA_VERSION = "identity_provenance_v1"

# Row families that can change a resolution. Each is fingerprinted from the same
# PIT view the resolver uses.
REFERENCE_FAMILIES = (
    "players",
    "player_aliases",
    "player_source_ids",
    "model_identity_bridge_events",
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class ReferenceFingerprint:
    """Deterministic digest over the PIT-visible identity reference state."""

    as_of: datetime
    policy_version: str
    player_count: int
    row_counts: dict[str, int]
    sha256: str

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": IDENTITY_PROVENANCE_SCHEMA_VERSION,
            "policy_version": self.policy_version,
            "as_of": _iso(self.as_of),
            "player_count": self.player_count,
            "row_counts": dict(sorted(self.row_counts.items())),
            "reference_sha256": self.sha256,
        }


@dataclass(frozen=True)
class ResolvedIdentity:
    """One player's resolution frozen together with the reference state it used."""

    resolution: IdentityResolution
    reference: ReferenceFingerprint

    def as_record(self) -> dict[str, Any]:
        resolution = self.resolution
        return {
            "player_id": resolution.player_id,
            "resolved_player_id": resolution.resolved_player_id,
            "bridged": resolution.bridged,
            "method": resolution.method,
            "evidence": resolution.evidence,
            "name_source": resolution.name_source,
            "candidate_count": resolution.candidate_count,
            "matched_alias": resolution.matched_alias,
            "bridge_event_id": resolution.bridge_event_id,
            "visible_from": _iso(resolution.visible_from),
            "observed_at": _iso(resolution.observed_at),
            "reference_policy": resolution.reference_policy,
            "reference_sha256": self.reference.sha256,
        }


def _reference_rows(
    conn: psycopg.Connection,
    *,
    player_ids: list[int],
) -> dict[str, list[list[Any]]]:
    """Read the reference rows that can influence a resolution.

    Only identity *linkage* columns are included. ``last_observed_at`` and
    ``updated_at`` are deliberately excluded: they move on every benign
    re-observation, so including them would make the digest drift constantly and hide
    the mutations that actually matter. A changed ``canonical_name`` or alias
    ``player_id`` still changes the digest.
    """
    players = conn.execute(
        """
        SELECT player_id, canonical_name, created_at
        FROM players
        WHERE player_id = ANY(%s)
        ORDER BY player_id
        """,
        (player_ids,),
    ).fetchall()
    aliases = conn.execute(
        """
        SELECT player_alias_id, player_id, source_system, normalized_alias,
               first_observed_at, created_at
        FROM player_aliases
        WHERE player_id = ANY(%s)
        ORDER BY player_alias_id
        """,
        (player_ids,),
    ).fetchall()
    sources = conn.execute(
        """
        SELECT player_source_id, player_id, source_system, source_player_id, created_at
        FROM player_source_ids
        WHERE player_id = ANY(%s)
        ORDER BY player_source_id
        """,
        (player_ids,),
    ).fetchall()
    bridges = conn.execute(
        """
        SELECT model_identity_bridge_event_id, live_player_id, action,
               historical_player_id, activated_at, evidence_as_of, created_at
        FROM model_identity_bridge_events
        WHERE live_player_id = ANY(%s) OR historical_player_id = ANY(%s)
        ORDER BY model_identity_bridge_event_id
        """,
        (player_ids, player_ids),
    ).fetchall()
    return {
        "players": [list(row) for row in players],
        "player_aliases": [list(row) for row in aliases],
        "player_source_ids": [list(row) for row in sources],
        "model_identity_bridge_events": [list(row) for row in bridges],
    }


def reference_fingerprint(
    conn: psycopg.Connection,
    *,
    player_ids: Iterable[int],
    as_of: datetime,
    policy_version: str = IDENTITY_REFERENCE_POLICY_VERSION,
) -> ReferenceFingerprint:
    """Compute the deterministic reference-state digest for the given players."""
    ids = sorted({int(pid) for pid in player_ids})
    rows = _reference_rows(conn, player_ids=ids)
    row_counts = {family: len(rows[family]) for family in REFERENCE_FAMILIES}
    digest = _sha256(
        {
            "schema_version": IDENTITY_PROVENANCE_SCHEMA_VERSION,
            "policy_version": policy_version,
            "as_of": _iso(as_of),
            "rows": rows,
        }
    )
    return ReferenceFingerprint(
        as_of=as_of,
        policy_version=policy_version,
        player_count=len(ids),
        row_counts=row_counts,
        sha256=digest,
    )


def resolve_with_provenance(
    conn: psycopg.Connection,
    *,
    player_ids: Iterable[int],
    as_of: datetime,
    reference_policy: str = "strict",
    allow_provider_alias_keys: bool = False,
    alias_refresh_gate: bool = False,
) -> tuple[dict[int, ResolvedIdentity], ReferenceFingerprint]:
    """Resolve identities and freeze them against one reference-state digest."""
    ids = sorted({int(pid) for pid in player_ids})
    reference = reference_fingerprint(conn, player_ids=ids, as_of=as_of)
    resolutions = resolve_model_player_ids_with_provenance(
        conn,
        player_ids=ids,
        as_of=as_of,
        reference_policy=reference_policy,
        allow_provider_alias_keys=allow_provider_alias_keys,
        alias_refresh_gate=alias_refresh_gate,
    )
    return (
        {
            player_id: ResolvedIdentity(resolution=resolution, reference=reference)
            for player_id, resolution in resolutions.items()
        },
        reference,
    )


def reference_drift_for(
    conn: psycopg.Connection,
    *,
    player_ids: Iterable[int],
    recorded: dict[str, Any],
) -> dict[str, Any]:
    """Recompute a recorded fingerprint and report whether reference rows moved.

    ``recorded`` is a :meth:`ReferenceFingerprint.as_record` payload (as stored in a
    capture record). Returns ``drift=false`` when the digest still matches, otherwise
    both digests and the row-count delta. This is how an out-of-band alias
    re-pointing or player rename becomes visible even without a versioned table.
    """
    as_of = recorded["as_of"]
    if isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of)
    current = reference_fingerprint(
        conn,
        player_ids=player_ids,
        as_of=as_of,
        policy_version=str(
            recorded.get("policy_version", IDENTITY_REFERENCE_POLICY_VERSION)
        ),
    )
    recorded_counts = recorded.get("row_counts", {}) or {}
    return {
        "recorded_reference_sha256": recorded.get("reference_sha256"),
        "current_reference_sha256": current.sha256,
        "drift": current.sha256 != recorded.get("reference_sha256"),
        "recorded_row_counts": recorded_counts,
        "current_row_counts": current.row_counts,
        "row_count_delta": {
            family: current.row_counts.get(family, 0)
            - int(recorded_counts.get(family, 0))
            for family in REFERENCE_FAMILIES
        },
    }


__all__ = [
    "IDENTITY_PROVENANCE_SCHEMA_VERSION",
    "REFERENCE_FAMILIES",
    "ReferenceFingerprint",
    "ResolvedIdentity",
    "reference_drift_for",
    "reference_fingerprint",
    "resolve_with_provenance",
]
