"""Deterministic, point-in-time surface authority for the tennis corpus.

Surface is *evidence*, never inferred. This module exposes a single explicit
authority hierarchy and never overwrites stronger evidence with weaker evidence:

1. ``MATCH_VERSION``   - the match-version surface recorded by the match provider;
2. ``TOURNAMENT_EDITION`` - the tournament-edition surface, but only when it was
   learned at or before the replay cutoff (``updated_at <= as_of``).

Anything else remains ``UNKNOWN`` (fail-closed). Country, city, month, tournament
name similarity and player tendencies are never used. When a stronger and a weaker
source disagree the disagreement is recorded as a conflict and the stronger source
wins.

The functions are read-only. Authoritative edition-surface recovery already exists
in ``live.authoritative_surface_recovery``; this module is the audit/regression
authority that proves what the models may legitimately read at a point in time.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

SURFACE_SOURCE_MATCH_VERSION = "MATCH_VERSION"
SURFACE_SOURCE_TOURNAMENT_EDITION = "TOURNAMENT_EDITION"
SURFACE_SOURCE_UNKNOWN = "UNKNOWN"

SURFACE_SOURCE_PRIORITY = (
    SURFACE_SOURCE_MATCH_VERSION,
    SURFACE_SOURCE_TOURNAMENT_EDITION,
    SURFACE_SOURCE_UNKNOWN,
)

SUPPORTED_SURFACES = frozenset({"HARD", "CLAY", "GRASS", "CARPET"})


@dataclass(frozen=True)
class SurfaceConflict:
    stronger_source: str
    weaker_source: str
    stronger_surface: str
    weaker_surface: str
    detail: str

    def to_payload(self) -> dict[str, str]:
        return {
            "stronger_source": self.stronger_source,
            "weaker_source": self.weaker_source,
            "stronger_surface": self.stronger_surface,
            "weaker_surface": self.weaker_surface,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SurfaceResolution:
    match_id: int
    resolved_surface: str | None
    source: str
    as_of: datetime
    conflicts: tuple[SurfaceConflict, ...]
    evidence: tuple[str, ...]

    @property
    def is_known(self) -> bool:
        return self.resolved_surface is not None

    def to_payload(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "resolved_surface": self.resolved_surface,
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "conflicts": [conflict.to_payload() for conflict in self.conflicts],
            "evidence": list(self.evidence),
        }


def _normalize_surface(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _version_surfaces(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> list[tuple[str, datetime]]:
    rows = conn.execute(
        """
        SELECT mv.surface::text, mv.as_of, mv.source_observed_at, mv.match_version_id
        FROM match_versions mv
        WHERE mv.match_id = %s
          AND mv.as_of <= %s
          AND mv.source_observed_at <= %s
          AND mv.surface IS NOT NULL
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        """,
        (match_id, as_of, as_of),
    ).fetchall()
    return [(str(row[0]), row[1]) for row in rows]


def _edition_surface(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> tuple[str, datetime] | None:
    row = conn.execute(
        """
        SELECT te.surface::text, te.updated_at
        FROM matches m
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        WHERE m.match_id = %s
          AND te.surface IS NOT NULL
          AND te.updated_at <= %s
        """,
        (match_id, as_of),
    ).fetchone()
    if row is None:
        return None
    return str(row[0]), row[1]


def resolve_authoritative_surface(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> SurfaceResolution:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    evidence: list[str] = []
    conflicts: list[SurfaceConflict] = []

    version_surfaces = _version_surfaces(conn, match_id=match_id, as_of=as_of)
    version_surface = version_surfaces[0][0] if version_surfaces else None
    distinct_version_surfaces = {surface for surface, _ in version_surfaces}
    if version_surface is not None:
        evidence.append(f"match_version_surface={version_surface}")
    if len(distinct_version_surfaces) > 1:
        conflicts.append(
            SurfaceConflict(
                stronger_source=SURFACE_SOURCE_MATCH_VERSION,
                weaker_source=SURFACE_SOURCE_MATCH_VERSION,
                stronger_surface=version_surface or "",
                weaker_surface=version_surfaces[-1][0],
                detail="conflicting_match_version_surfaces_using_latest_pit_version",
            )
        )

    edition = _edition_surface(conn, match_id=match_id, as_of=as_of)
    edition_surface = edition[0] if edition is not None else None
    if edition_surface is not None:
        evidence.append(f"tournament_edition_surface={edition_surface}")

    if version_surface is not None:
        if edition_surface is not None and edition_surface != version_surface:
            conflicts.append(
                SurfaceConflict(
                    stronger_source=SURFACE_SOURCE_MATCH_VERSION,
                    weaker_source=SURFACE_SOURCE_TOURNAMENT_EDITION,
                    stronger_surface=version_surface,
                    weaker_surface=edition_surface,
                    detail="edition_surface_overridden_by_match_version",
                )
            )
        return SurfaceResolution(
            match_id=match_id,
            resolved_surface=version_surface,
            source=SURFACE_SOURCE_MATCH_VERSION,
            as_of=as_of,
            conflicts=tuple(conflicts),
            evidence=tuple(evidence),
        )

    if edition_surface is not None:
        return SurfaceResolution(
            match_id=match_id,
            resolved_surface=edition_surface,
            source=SURFACE_SOURCE_TOURNAMENT_EDITION,
            as_of=as_of,
            conflicts=tuple(conflicts),
            evidence=tuple(evidence),
        )

    return SurfaceResolution(
        match_id=match_id,
        resolved_surface=None,
        source=SURFACE_SOURCE_UNKNOWN,
        as_of=as_of,
        conflicts=tuple(conflicts),
        evidence=tuple(evidence),
    )


def build_surface_authority_report(
    conn: psycopg.Connection,
    *,
    match_ids: Iterable[int],
    as_of: datetime,
) -> dict[str, Any]:
    resolutions = [
        resolve_authoritative_surface(conn, match_id=int(match_id), as_of=as_of)
        for match_id in sorted({int(value) for value in match_ids})
    ]
    source_counts: dict[str, int] = {}
    for resolution in resolutions:
        source_counts[resolution.source] = source_counts.get(resolution.source, 0) + 1
    return {
        "as_of": as_of.isoformat(),
        "matches": len(resolutions),
        "known": sum(1 for resolution in resolutions if resolution.is_known),
        "unknown": sum(1 for resolution in resolutions if not resolution.is_known),
        "unknown_surface": SURFACE_SOURCE_UNKNOWN,
        "source_counts": source_counts,
        "conflicts": sum(len(resolution.conflicts) for resolution in resolutions),
        "resolutions": [resolution.to_payload() for resolution in resolutions],
    }


__all__ = [
    "SUPPORTED_SURFACES",
    "SURFACE_SOURCE_MATCH_VERSION",
    "SURFACE_SOURCE_PRIORITY",
    "SURFACE_SOURCE_TOURNAMENT_EDITION",
    "SURFACE_SOURCE_UNKNOWN",
    "SurfaceConflict",
    "SurfaceResolution",
    "build_surface_authority_report",
    "resolve_authoritative_surface",
]
