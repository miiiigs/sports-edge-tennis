"""Bounded authoritative surface recovery from exact Sportradar metadata.

Terminal A proved the current-slate A2 coverage gap is dominated by matches with
``surface = NULL`` and no stored edition surface. This module recovers the surface
from the authoritative provider competition/season metadata (never from name,
city, month, or any heuristic) and persists it at the tournament-edition level so
the already-deployed deterministic A2 surface resolver can use it.

Evidence is deduplicated by exact ``competition_id`` (season list) and exact
``season_id`` (season info), so one provider response repairs every linked match
in that edition. Requests are bounded and the run fails closed on ambiguity or a
non-200 provider response.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Callable

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar import (
    fetch_competition_seasons,
    fetch_season_info,
)
from sports_edge_tennis.live.sportradar_surface_apply import (
    ALLOWED_SURFACES,
    _season_rows,
    _season_surface,
    normalize_surface,
)

DEFAULT_MAX_REQUESTS = 20
DEFAULT_MAX_EDITIONS = 40


@dataclass(frozen=True)
class UnresolvedEdition:
    tournament_edition_id: int
    tournament_id: int
    canonical_name: str
    season_year: int
    competition_id: str
    match_ids: tuple[int, ...]


@dataclass(frozen=True)
class SurfaceRecoveryOutcome:
    editions_considered: int
    editions_recovered: int
    editions_unresolved: int
    provider_requests: int
    surfaces_recovered: tuple[str, ...]
    stopped_reason: str


def load_unresolved_editions(
    conn: psycopg.Connection,
    *,
    max_editions: int = DEFAULT_MAX_EDITIONS,
    player_ids: list[int] | tuple[int, ...] | None = None,
) -> list[UnresolvedEdition]:
    """Editions with a NULL surface that have an exact Sportradar competition id.

    Only editions that contain at least one match whose latest version surface is
    NULL are considered. If ``player_ids`` is given, only editions involving those
    players are returned.
    """
    player_filter = ""
    params: dict[str, object] = {"max_editions": max_editions}
    if player_ids:
        player_filter = """
          AND EXISTS (
              SELECT 1 FROM matches pm
              JOIN match_versions pmv ON pmv.match_id = pm.match_id
              WHERE pm.tournament_edition_id = te.tournament_edition_id
                AND (pmv.player_1_id = ANY(%(player_ids)s) OR pmv.player_2_id = ANY(%(player_ids)s))
          )
        """
        params["player_ids"] = list(player_ids)

    rows = conn.execute(
        f"""
        SELECT
            te.tournament_edition_id,
            te.tournament_id,
            t.canonical_name,
            te.season_year,
            tsi.source_tournament_id,
            ARRAY(
                SELECT DISTINCT m.match_id
                FROM matches m
                JOIN match_versions mv ON mv.match_id = m.match_id
                WHERE m.tournament_edition_id = te.tournament_edition_id
                  AND mv.surface IS NULL
                ORDER BY m.match_id
            ) AS match_ids
        FROM tournament_editions te
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN tournament_source_ids tsi
          ON tsi.tournament_id = t.tournament_id
         AND tsi.source_system = 'SPORTRADAR'
        WHERE te.surface IS NULL
          {player_filter}
        GROUP BY te.tournament_edition_id, te.tournament_id, t.canonical_name,
                 te.season_year, tsi.source_tournament_id
        ORDER BY te.season_year DESC, te.tournament_edition_id
        LIMIT %(max_editions)s
        """,
        params,
    ).fetchall()

    editions: list[UnresolvedEdition] = []
    for edition_id, tournament_id, name, season_year, competition_id, match_ids in rows:
        ids = tuple(int(value) for value in (match_ids or ()))
        if not ids:
            continue
        editions.append(
            UnresolvedEdition(
                tournament_edition_id=int(edition_id),
                tournament_id=int(tournament_id),
                canonical_name=str(name),
                season_year=int(season_year),
                competition_id=str(competition_id),
                match_ids=ids,
            )
        )
    return editions


def _select_season_by_year(
    seasons_payload: Mapping[str, Any],
    *,
    season_year: int,
) -> str | None:
    matches = [
        str(row.get("id"))
        for row in _season_rows(seasons_payload)
        if str(row.get("year") or "").strip() == str(season_year)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _edition_has_conflicting_surface(
    conn: psycopg.Connection,
    *,
    edition_id: int,
    canonical_surface: str,
) -> bool:
    rows = conn.execute(
        """
        SELECT DISTINCT mv.surface::text
        FROM matches m
        JOIN match_versions mv ON mv.match_id = m.match_id
        WHERE m.tournament_edition_id = %s AND mv.surface IS NOT NULL
        """,
        (edition_id,),
    ).fetchall()
    existing = {str(row[0]) for row in rows}
    return bool(existing) and existing != {canonical_surface}


def _fill_edition_surface(
    conn: psycopg.Connection,
    *,
    edition: UnresolvedEdition,
    canonical_surface: str,
    raw_surface: str,
    provider_observed_at: datetime,
    checked_at: datetime,
) -> bool:
    updated = conn.execute(
        """
        UPDATE tournament_editions
        SET surface = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE tournament_edition_id = %s
          AND surface IS NULL
        RETURNING tournament_edition_id
        """,
        (canonical_surface, edition.tournament_edition_id),
    ).fetchone()
    if updated is None:
        return False
    notes = (
        f"competition_id={edition.competition_id} season_year={edition.season_year} "
        f"raw_surface={raw_surface} canonical_surface={canonical_surface} "
        f"tournament={edition.canonical_name} edition_id={edition.tournament_edition_id} "
        f"match_count={len(edition.match_ids)}"
    )
    conn.execute(
        """
        INSERT INTO data_health_checks (
            source_system, dataset_name, checked_at, latest_observation_at,
            freshness_state, coverage_ratio, unmatched_count, error_count, status, notes
        )
        VALUES ('SPORTRADAR', 'AUTHORITATIVE_SURFACE_RECOVERY', %s, %s,
                'FRESH', 1.0, 0, 0, 'HEALTHY', %s)
        """,
        (checked_at, provider_observed_at, notes),
    )
    return True


def recover_authoritative_surfaces(
    conn: psycopg.Connection,
    *,
    api_key: str,
    access_level: str = "trial",
    language: str = "en",
    max_requests: int = DEFAULT_MAX_REQUESTS,
    max_editions: int = DEFAULT_MAX_EDITIONS,
    player_ids: list[int] | tuple[int, ...] | None = None,
    fetch_seasons: Callable[..., tuple[datetime, dict[str, Any]]] = fetch_competition_seasons,
    fetch_info: Callable[..., tuple[datetime, dict[str, Any]]] = fetch_season_info,
) -> SurfaceRecoveryOutcome:
    editions = load_unresolved_editions(
        conn, max_editions=max_editions, player_ids=player_ids
    )
    requests = 0
    recovered = 0
    unresolved = 0
    surfaces: list[str] = []
    seasons_cache: dict[str, tuple[datetime, dict[str, Any]] | None] = {}
    info_cache: dict[str, tuple[datetime, dict[str, Any]] | None] = {}
    stopped_reason = "editions_exhausted"

    for edition in editions:
        if requests >= max_requests:
            stopped_reason = "request_cap"
            break

        if edition.competition_id not in seasons_cache:
            if requests >= max_requests:
                stopped_reason = "request_cap"
                break
            try:
                seasons_cache[edition.competition_id] = fetch_seasons(
                    api_key, edition.competition_id, access_level=access_level, language=language
                )
                requests += 1
            except Exception:  # noqa: BLE001 - provider transport failure
                seasons_cache[edition.competition_id] = None
        seasons_payload = seasons_cache.get(edition.competition_id)
        if seasons_payload is None:
            unresolved += 1
            _log_unresolved(edition, "NO_SEASONS")
            continue

        season_id = _select_season_by_year(seasons_payload[1], season_year=edition.season_year)
        if season_id is None:
            unresolved += 1
            _log_unresolved(edition, "NO_SEASON_FOR_YEAR")
            continue

        if season_id not in info_cache:
            if requests >= max_requests:
                stopped_reason = "request_cap"
                break
            try:
                info_cache[season_id] = fetch_info(
                    api_key, season_id, access_level=access_level, language=language
                )
                requests += 1
            except Exception:  # noqa: BLE001 - provider transport failure
                info_cache[season_id] = None
        info_payload = info_cache.get(season_id)
        if info_payload is None:
            unresolved += 1
            _log_unresolved(edition, "NO_SEASON_INFO")
            continue

        raw_surface, canonical_surface = _season_surface(info_payload[1])
        if canonical_surface is None or canonical_surface not in ALLOWED_SURFACES:
            unresolved += 1
            _log_unresolved(edition, "NO_SURFACE_IN_METADATA")
            continue
        if _edition_has_conflicting_surface(
            conn, edition_id=edition.tournament_edition_id, canonical_surface=canonical_surface
        ):
            unresolved += 1
            _log_unresolved(edition, "CONFLICTING_VERSION_SURFACE")
            continue

        checked_at = datetime.now(UTC)
        provider_observed_at = max(seasons_payload[0], info_payload[0])
        with conn.transaction():
            if _fill_edition_surface(
                conn,
                edition=edition,
                canonical_surface=canonical_surface,
                raw_surface=raw_surface,
                provider_observed_at=provider_observed_at,
                checked_at=checked_at,
            ):
                recovered += 1
                surfaces.append(canonical_surface)
                print(
                    "AUTHORITATIVE_SURFACE_RECOVERED "
                    f"edition_id={edition.tournament_edition_id} "
                    f"competition_id={edition.competition_id} season={edition.season_year} "
                    f"surface={canonical_surface} match_count={len(edition.match_ids)}",
                    flush=True,
                )
            else:
                unresolved += 1
                _log_unresolved(edition, "UPDATE_NOOP")

    return SurfaceRecoveryOutcome(
        editions_considered=len(editions),
        editions_recovered=recovered,
        editions_unresolved=unresolved,
        provider_requests=requests,
        surfaces_recovered=tuple(surfaces),
        stopped_reason=stopped_reason,
    )


def _log_unresolved(edition: UnresolvedEdition, reason: str) -> None:
    print(
        "AUTHORITATIVE_SURFACE_UNRESOLVED "
        f"edition_id={edition.tournament_edition_id} competition_id={edition.competition_id} "
        f"season={edition.season_year} reason={reason}",
        flush=True,
    )


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded authoritative Sportradar surface recovery"
    )
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--max-editions", type=int, default=DEFAULT_MAX_EDITIONS)
    parser.add_argument("--player-ids", default=None, help="Comma-separated canonical player IDs to scope")
    parser.add_argument("--dsn", default=_db_dsn())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.dsn:
        print("AUTHORITATIVE_SURFACE_RECOVERY_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    access_level = load_secret("SPORTRADAR_TENNIS_ACCESS_LEVEL") or "trial"
    language = load_secret("SPORTRADAR_TENNIS_LANGUAGE") or "en"
    if not args.dry_run and not api_key:
        print("AUTHORITATIVE_SURFACE_RECOVERY_BLOCKED reason=MISSING_API_KEY", flush=True)
        return 2

    player_ids = None
    if args.player_ids:
        player_ids = [int(value) for value in str(args.player_ids).split(",") if value.strip()]

    with psycopg.connect(args.dsn, autocommit=False) as conn:
        outcome = recover_authoritative_surfaces(
            conn,
            api_key=api_key or "",
            access_level=access_level,
            language=language,
            max_requests=0 if args.dry_run else args.max_requests,
            max_editions=args.max_editions,
            player_ids=player_ids,
        )
        if not args.dry_run:
            conn.commit()

    print(
        "AUTHORITATIVE_SURFACE_RECOVERY "
        f"editions_considered={outcome.editions_considered} "
        f"editions_recovered={outcome.editions_recovered} "
        f"editions_unresolved={outcome.editions_unresolved} "
        f"provider_requests={outcome.provider_requests} "
        f"stopped_reason={outcome.stopped_reason}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
