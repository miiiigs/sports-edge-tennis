"""Point-in-time surface availability for replay.

``tournament_editions.surface`` is mutable master data with no observation
timestamp, so current-day edition metadata cannot prove that a surface was known
at a historical replay cutoff ``T``. This module resolves surface only from
evidence that is itself PIT-filtered:

1. the match's own PIT-visible ``match_versions.surface`` (``source_observed_at``
   and ``as_of`` both ``<= T``);
2. PIT-visible surfaces from the same canonical tournament in an earlier or equal
   season.

Mutable edition metadata, tournament name, location, and player history are never
used to infer surface. Absent proof the result fails closed with
``SURFACE_NOT_AVAILABLE_PIT``; conflicting evidence fails closed with
``SURFACE_CONFLICT_AT_T``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

SURFACE_PIT_POLICY_VERSION = "surface_pit_v1"

SURFACE_KNOWN_AT_T = "SURFACE_KNOWN_AT_T"
SURFACE_NOT_AVAILABLE_PIT = "SURFACE_NOT_AVAILABLE_PIT"
SURFACE_CONFLICT_AT_T = "SURFACE_CONFLICT_AT_T"


@dataclass(frozen=True)
class SurfaceResolution:
    surface: str | None
    status: str
    authority: str | None
    policy_version: str = SURFACE_PIT_POLICY_VERSION


def _match_version_surface(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> str | None:
    row = conn.execute(
        """
        SELECT mv.surface::text
        FROM match_versions mv
        WHERE mv.match_id = %s
          AND mv.source_observed_at <= %s
          AND mv.as_of <= %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id, as_of, as_of),
    ).fetchone()
    return row[0] if row is not None and row[0] is not None else None


def _historical_tournament_surface(
    conn: psycopg.Connection,
    *,
    canonical_name: str,
    tour_scope: str,
    season_year: int,
    as_of: datetime,
) -> tuple[str | None, str]:
    rows = conn.execute(
        """
        SELECT te_hist.season_year, hist_mv.surface::text
        FROM tournaments t_hist
        JOIN tournament_editions te_hist
          ON te_hist.tournament_id = t_hist.tournament_id
        JOIN matches m_hist
          ON m_hist.tournament_edition_id = te_hist.tournament_edition_id
        JOIN match_versions hist_mv
          ON hist_mv.match_id = m_hist.match_id
        WHERE hist_mv.surface IS NOT NULL
          AND hist_mv.source_observed_at <= %s
          AND hist_mv.as_of <= %s
          AND te_hist.season_year <= %s
          AND t_hist.canonical_name = %s
          AND t_hist.tour_scope::text = %s
        ORDER BY te_hist.season_year DESC, hist_mv.as_of DESC, hist_mv.match_version_id DESC
        """,
        (as_of, as_of, season_year, canonical_name, tour_scope),
    ).fetchall()
    if not rows:
        return None, "NO_EVIDENCE"
    latest_year = int(rows[0][0])
    surfaces = {str(row[1]) for row in rows if int(row[0]) == latest_year}
    if len(surfaces) == 1:
        return next(iter(surfaces)), "HISTORICAL_MATCH_VERSION"
    return None, "CONFLICT"


def resolve_surface_pit(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
    canonical_name: str,
    tour_scope: str,
    season_year: int,
) -> SurfaceResolution:
    direct = _match_version_surface(conn, match_id=match_id, as_of=as_of)
    if direct is not None:
        return SurfaceResolution(
            surface=direct,
            status=SURFACE_KNOWN_AT_T,
            authority="MATCH_VERSION_PIT",
        )

    surface, evidence = _historical_tournament_surface(
        conn,
        canonical_name=canonical_name,
        tour_scope=tour_scope,
        season_year=season_year,
        as_of=as_of,
    )
    if evidence == "CONFLICT":
        return SurfaceResolution(
            surface=None,
            status=SURFACE_CONFLICT_AT_T,
            authority="HISTORICAL_MATCH_VERSION",
        )
    if surface is None:
        return SurfaceResolution(
            surface=None,
            status=SURFACE_NOT_AVAILABLE_PIT,
            authority=None,
        )
    return SurfaceResolution(
        surface=surface,
        status=SURFACE_KNOWN_AT_T,
        authority="HISTORICAL_MATCH_VERSION",
    )


__all__ = [
    "SURFACE_PIT_POLICY_VERSION",
    "SURFACE_KNOWN_AT_T",
    "SURFACE_NOT_AVAILABLE_PIT",
    "SURFACE_CONFLICT_AT_T",
    "SurfaceResolution",
    "resolve_surface_pit",
]
