from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping

import psycopg

from sports_edge_tennis.live.transport import TransportError, fetch_api_tennis_draw


DEFAULT_HORIZON_DAYS = 3
DEFAULT_YEARS_BACK = 2
DEFAULT_MAX_TOURNAMENTS = 6
DEFAULT_MAX_KEYS_PER_TOURNAMENT = 2
_SUPPORTED_SURFACES = {
    "hard": "HARD",
    "clay": "CLAY",
    "grass": "GRASS",
    "carpet": "CARPET",
}

FetchDraw = Callable[[str, str, int], tuple[datetime, dict[str, Any]]]


@dataclass(frozen=True)
class SurfaceHistoryProbeTarget:
    tournament_id: int
    tournament_name: str
    season_year: int
    tournament_keys: tuple[str, ...]


@dataclass(frozen=True)
class SurfaceHistoryProbeOutcome:
    targets: int
    provider_calls: int
    candidates: int
    conflicts: int
    no_evidence: int
    unavailable_calls: int


def _normalize_surface(value: Any) -> str | None:
    if value is None:
        return None
    return _SUPPORTED_SURFACES.get(str(value).strip().casefold())


def _draw_surface(payload: Mapping[str, Any]) -> str | None:
    tournament = payload.get("tournament")
    if not isinstance(tournament, Mapping):
        return None
    return _normalize_surface(tournament.get("tournament_surface"))


def load_probe_targets(
    conn: psycopg.Connection,
    *,
    as_of: datetime | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    max_tournaments: int = DEFAULT_MAX_TOURNAMENTS,
) -> tuple[SurfaceHistoryProbeTarget, ...]:
    """Load upcoming tournaments whose latest scheduled match still lacks surface.

    This is observation only. Targets must already have API-Tennis tournament identity;
    no name-based provider lookup is performed.
    """
    if horizon_days < 1:
        raise ValueError("horizon_days must be at least 1")
    if max_tournaments < 1:
        raise ValueError("max_tournaments must be at least 1")

    observed_at = as_of or datetime.now(UTC)
    horizon_end = observed_at + timedelta(days=horizon_days)
    rows = conn.execute(
        """
        WITH latest_match AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.surface::text AS match_surface,
                mv.match_status::text AS match_status,
                mv.match_start_at
            FROM match_versions mv
            WHERE mv.source_observed_at <= %s
              AND mv.as_of <= %s
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT
            t.tournament_id,
            t.canonical_name,
            te.season_year,
            ARRAY_AGG(DISTINCT tsi.source_tournament_id ORDER BY tsi.source_tournament_id)
        FROM latest_match lm
        JOIN matches m ON m.match_id = lm.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN tournament_source_ids tsi
          ON tsi.tournament_id = t.tournament_id
         AND tsi.source_system = 'API_TENNIS'
        WHERE lm.match_status = 'SCHEDULED'
          AND lm.match_start_at >= %s
          AND lm.match_start_at <= %s
          AND lm.match_surface IS NULL
          AND te.surface IS NULL
        GROUP BY t.tournament_id, t.canonical_name, te.season_year
        ORDER BY MIN(lm.match_start_at), t.tournament_id
        LIMIT %s
        """,
        (observed_at, observed_at, observed_at, horizon_end, max_tournaments),
    ).fetchall()

    targets: list[SurfaceHistoryProbeTarget] = []
    for tournament_id, tournament_name, season_year, tournament_keys in rows:
        keys = tuple(
            dict.fromkeys(str(key) for key in (tournament_keys or ()) if key is not None)
        )
        if not keys:
            continue
        targets.append(
            SurfaceHistoryProbeTarget(
                tournament_id=int(tournament_id),
                tournament_name=str(tournament_name),
                season_year=int(season_year),
                tournament_keys=keys,
            )
        )
    return tuple(targets)


def run_probe(
    conn: psycopg.Connection,
    *,
    api_key: str,
    as_of: datetime | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    years_back: int = DEFAULT_YEARS_BACK,
    max_tournaments: int = DEFAULT_MAX_TOURNAMENTS,
    max_keys_per_tournament: int = DEFAULT_MAX_KEYS_PER_TOURNAMENT,
    fetch_draw: FetchDraw = fetch_api_tennis_draw,
) -> SurfaceHistoryProbeOutcome:
    """Probe prior-season API-Tennis draws for unresolved surface evidence.

    The probe never writes provider evidence back to the database and never changes
    fixture/model state. A surface is only reported as a candidate when every usable
    prior-season draw sampled for the tournament agrees.
    """
    if years_back < 1:
        raise ValueError("years_back must be at least 1")
    if max_keys_per_tournament < 1:
        raise ValueError("max_keys_per_tournament must be at least 1")

    targets = load_probe_targets(
        conn,
        as_of=as_of,
        horizon_days=horizon_days,
        max_tournaments=max_tournaments,
    )
    print(
        "SURFACE_HISTORY_PROBE_START "
        f"targets={len(targets)} horizon_days={horizon_days} years_back={years_back} "
        f"max_tournaments={max_tournaments} max_keys_per_tournament={max_keys_per_tournament}",
        flush=True,
    )

    provider_calls = 0
    candidates = 0
    conflicts = 0
    no_evidence = 0
    unavailable_calls = 0

    for target in targets:
        observed_surfaces: set[str] = set()
        sampled = 0
        keys = target.tournament_keys[:max_keys_per_tournament]
        for tournament_key in keys:
            for offset in range(1, years_back + 1):
                probe_year = target.season_year - offset
                provider_calls += 1
                try:
                    _received_at, payload = fetch_draw(api_key, tournament_key, probe_year)
                except TransportError as exc:
                    unavailable_calls += 1
                    print(
                        "SURFACE_HISTORY_PROBE_UNAVAILABLE "
                        f"tournament_id={target.tournament_id} tournament_key={tournament_key} "
                        f"season={probe_year} kind={exc.kind} mapping_changed=0",
                        flush=True,
                    )
                    continue

                sampled += 1
                surface = _draw_surface(payload)
                if surface is None:
                    print(
                        "SURFACE_HISTORY_PROBE_SAMPLE "
                        f"tournament_id={target.tournament_id} tournament_key={tournament_key} "
                        f"season={probe_year} surface=- mapping_changed=0",
                        flush=True,
                    )
                    continue
                observed_surfaces.add(surface)
                print(
                    "SURFACE_HISTORY_PROBE_SAMPLE "
                    f"tournament_id={target.tournament_id} tournament_key={tournament_key} "
                    f"season={probe_year} surface={surface} mapping_changed=0",
                    flush=True,
                )

        if len(observed_surfaces) == 1:
            candidates += 1
            surface = next(iter(observed_surfaces))
            print(
                "SURFACE_HISTORY_PROBE_CANDIDATE "
                f"tournament_id={target.tournament_id} "
                f"tournament=\"{target.tournament_name.replace(chr(34), chr(39))}\" "
                f"season={target.season_year} surface={surface} sampled={sampled} "
                f"keys={','.join(keys)} mapping_changed=0",
                flush=True,
            )
        elif len(observed_surfaces) > 1:
            conflicts += 1
            print(
                "SURFACE_HISTORY_PROBE_CONFLICT "
                f"tournament_id={target.tournament_id} season={target.season_year} "
                f"surfaces={','.join(sorted(observed_surfaces))} sampled={sampled} "
                "mapping_changed=0",
                flush=True,
            )
        else:
            no_evidence += 1
            print(
                "SURFACE_HISTORY_PROBE_NO_EVIDENCE "
                f"tournament_id={target.tournament_id} season={target.season_year} "
                f"sampled={sampled} keys={','.join(keys)} mapping_changed=0",
                flush=True,
            )

    outcome = SurfaceHistoryProbeOutcome(
        targets=len(targets),
        provider_calls=provider_calls,
        candidates=candidates,
        conflicts=conflicts,
        no_evidence=no_evidence,
        unavailable_calls=unavailable_calls,
    )
    print(
        "SURFACE_HISTORY_PROBE_SUMMARY "
        f"targets={outcome.targets} provider_calls={outcome.provider_calls} "
        f"candidates={outcome.candidates} conflicts={outcome.conflicts} "
        f"no_evidence={outcome.no_evidence} unavailable_calls={outcome.unavailable_calls} "
        "mapping_changed=0",
        flush=True,
    )
    return outcome
