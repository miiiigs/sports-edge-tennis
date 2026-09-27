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

from sports_edge_tennis.data.normalize import normalize_tournament_identity

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


def _surface_rows(
    conn: psycopg.Connection,
    *,
    tournament_ids: list[int],
    season_year: int,
    as_of: datetime,
) -> list[tuple[object, object]]:
    return conn.execute(
        """
        SELECT te_hist.season_year, hist_mv.surface::text
        FROM tournament_editions te_hist
        JOIN matches m_hist
          ON m_hist.tournament_edition_id = te_hist.tournament_edition_id
        JOIN match_versions hist_mv
          ON hist_mv.match_id = m_hist.match_id
        WHERE te_hist.tournament_id = ANY(%s)
          AND hist_mv.surface IS NOT NULL
          AND hist_mv.source_observed_at <= %s
          AND hist_mv.as_of <= %s
          AND te_hist.season_year <= %s
        ORDER BY te_hist.season_year DESC, hist_mv.as_of DESC, hist_mv.match_version_id DESC
        """,
        (tournament_ids, as_of, as_of, season_year),
    ).fetchall()


def _unanimous_latest(rows: list[tuple[object, object]]) -> tuple[str | None, bool]:
    latest_year = int(rows[0][0])
    surfaces = {str(row[1]) for row in rows if int(row[0]) == latest_year}
    if len(surfaces) == 1:
        return next(iter(surfaces)), True
    return None, False


def _consistent(rows: list[tuple[object, object]]) -> tuple[str | None, bool]:
    """Require every PIT-visible historical surface to agree (normalized fallback)."""
    surfaces = {str(row[1]) for row in rows}
    if len(surfaces) == 1:
        return next(iter(surfaces)), True
    return None, False


def _historical_tournament_surface(
    conn: psycopg.Connection,
    *,
    canonical_name: str,
    tour_scope: str,
    season_year: int,
    as_of: datetime,
) -> tuple[str | None, str]:
    exact_ids = [
        int(row[0])
        for row in conn.execute(
            """
            SELECT t_hist.tournament_id
            FROM tournaments t_hist
            WHERE t_hist.canonical_name = %s
              AND t_hist.tour_scope::text = %s
            """,
            (canonical_name, tour_scope),
        ).fetchall()
    ]
    if exact_ids:
        rows = _surface_rows(
            conn, tournament_ids=exact_ids, season_year=season_year, as_of=as_of
        )
        if rows:
            surface, unanimous = _unanimous_latest(rows)
            return (
                (surface, "HISTORICAL_MATCH_VERSION") if unanimous else (None, "CONFLICT")
            )

    # Documented Challenger edition suffix: the live "Columbus" and the historical
    # "Columbus CH" are the same event. Requires a distinct, non-live history corpus
    # tournament in the same scope and *every* PIT-visible historical season (across
    # every matched identity) to agree on one surface. Any historical surface change
    # fails closed.
    target_identity = normalize_tournament_identity(canonical_name)
    if not target_identity:
        return None, "NO_EVIDENCE"
    candidate_rows = conn.execute(
        """
        SELECT DISTINCT t_hist.tournament_id, t_hist.canonical_name
        FROM tournaments t_hist
        JOIN tournament_source_ids tsi_hist
          ON tsi_hist.tournament_id = t_hist.tournament_id
         AND tsi_hist.source_system = 'SACKMANN'
        WHERE t_hist.tour_scope::text = %s
          AND t_hist.canonical_name <> %s
        """,
        (tour_scope, canonical_name),
    ).fetchall()
    matched_ids = sorted(
        int(row[0])
        for row in candidate_rows
        if normalize_tournament_identity(str(row[1])) == target_identity
    )
    if not matched_ids:
        return None, "NO_EVIDENCE"
    rows = _surface_rows(
        conn, tournament_ids=matched_ids, season_year=season_year, as_of=as_of
    )
    if not rows:
        return None, "NO_EVIDENCE"
    surface, consistent = _consistent(rows)
    return (surface, "HISTORICAL_MATCH_VERSION_NORMALIZED") if consistent else (None, "CONFLICT")


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
        authority=evidence,
    )


__all__ = [
    "SURFACE_CONFLICT_AT_T",
    "SURFACE_KNOWN_AT_T",
    "SURFACE_NOT_AVAILABLE_PIT",
    "SURFACE_PIT_POLICY_VERSION",
    "SurfaceResolution",
    "resolve_surface_pit",
]
