"""Guarded one-shot surface enrichment from exact Sportradar evidence.

This module is intentionally OFF by default. It only fills a NULL canonical
Tournament Edition surface after an operator-authorized, bounded provider
re-verification. It never overwrites a conflicting known surface.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Callable

import psycopg

from sports_edge_tennis.live.sportradar import (
    fetch_competition_seasons,
    fetch_season_info,
)

ENABLED_ENV = "SPORTRADAR_SURFACE_APPLY_ENABLED"
AUTH_ENV = "SPORTRADAR_SURFACE_APPLY_AUTHORIZATION"
AUTH_TOKEN = "SPORTRADAR_EXACT_SURFACE_FILL_V1"
MATCH_IDS_ENV = "SPORTRADAR_SURFACE_APPLY_MATCH_IDS"
COMPETITION_ID_ENV = "SPORTRADAR_SURFACE_APPLY_COMPETITION_ID"
SEASON_ID_ENV = "SPORTRADAR_SURFACE_APPLY_SEASON_ID"
SURFACE_ENV = "SPORTRADAR_SURFACE_APPLY_SURFACE"
MAX_MATCH_IDS = 10
ALLOWED_SURFACES = {"HARD", "CLAY", "GRASS", "CARPET"}
TRIAL_MIN_INTERVAL_SECONDS = 2.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _flag_enabled(value: Any) -> bool:
    return _text(value).casefold() in {"1", "true", "yes", "on"}


def normalize_surface(value: Any) -> str | None:
    raw = _text(value).casefold()
    if not raw:
        return None
    if "hard" in raw:
        return "HARD"
    if "clay" in raw:
        return "CLAY"
    if "grass" in raw:
        return "GRASS"
    if "carpet" in raw:
        return "CARPET"
    return None


def _parse_match_ids(raw: Any) -> tuple[int, ...]:
    values = [part.strip() for part in _text(raw).split(",") if part.strip()]
    if not values:
        raise ValueError("Sportradar surface apply match IDs are required")
    try:
        match_ids = tuple(sorted(dict.fromkeys(int(value) for value in values)))
    except ValueError as exc:
        raise ValueError("Sportradar surface apply match IDs must be integers") from exc
    if not 1 <= len(match_ids) <= MAX_MATCH_IDS:
        raise ValueError(
            f"Sportradar surface apply match IDs must contain 1 to {MAX_MATCH_IDS} IDs"
        )
    return match_ids


def _season_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = payload.get("seasons")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _season_surface(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    season = payload.get("season")
    if not isinstance(season, Mapping):
        return "", None
    info = season.get("info")
    if not isinstance(info, Mapping):
        return "", None
    raw = _text(info.get("surface"))
    return raw, normalize_surface(raw)


def apply_surface_fill(
    conn: psycopg.Connection,
    *,
    match_ids: tuple[int, ...],
    competition_id: str,
    season_id: str,
    canonical_surface: str,
    raw_surface: str,
    provider_observed_at: datetime,
    checked_at: datetime,
) -> tuple[str, int]:
    rows = conn.execute(
        """
        SELECT
            m.match_id,
            m.tournament_edition_id,
            te.surface::text,
            te.season_year,
            te.edition_name,
            t.canonical_name
        FROM matches m
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t
          ON t.tournament_id = te.tournament_id
        WHERE m.match_id = ANY(%s)
        ORDER BY m.match_id
        """,
        (list(match_ids),),
    ).fetchall()

    found_ids = tuple(int(row[0]) for row in rows)
    if found_ids != match_ids:
        raise ValueError(
            f"Sportradar surface apply expected matches {list(match_ids)} "
            f"but found {list(found_ids)}"
        )

    edition_ids = {int(row[1]) for row in rows}
    if len(edition_ids) != 1:
        raise ValueError("Sportradar surface apply matches do not share one tournament edition")
    edition_id = next(iter(edition_ids))

    current_surfaces = {row[2] for row in rows}
    non_null_surfaces = {value for value in current_surfaces if value is not None}
    if non_null_surfaces:
        if non_null_surfaces == {canonical_surface}:
            print(
                "SPORTRADAR_SURFACE_APPLY "
                f"status=EXISTING edition_id={edition_id} surface={canonical_surface} "
                f"matches={','.join(str(value) for value in match_ids)} writes=0",
                flush=True,
            )
            return "EXISTING", edition_id
        raise ValueError(
            "Sportradar surface apply refuses conflicting existing surface "
            f"{sorted(non_null_surfaces)}"
        )

    updated = conn.execute(
        """
        UPDATE tournament_editions
        SET surface = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE tournament_edition_id = %s
          AND surface IS NULL
        RETURNING tournament_edition_id
        """,
        (canonical_surface, edition_id),
    ).fetchone()
    if updated is None:
        raise RuntimeError("Sportradar surface apply lost NULL-surface update race")

    tournament_name = str(rows[0][5])
    edition_name = str(rows[0][4])
    season_year = int(rows[0][3])
    notes = (
        f"competition_id={competition_id} season_id={season_id} "
        f"raw_surface={raw_surface} canonical_surface={canonical_surface} "
        f"tournament={tournament_name} edition={edition_name} season_year={season_year} "
        f"match_ids={','.join(str(value) for value in match_ids)}"
    )
    conn.execute(
        """
        INSERT INTO data_health_checks (
            source_system,
            dataset_name,
            checked_at,
            latest_observation_at,
            freshness_state,
            coverage_ratio,
            unmatched_count,
            error_count,
            status,
            notes
        )
        VALUES ('SPORTRADAR', 'SURFACE_RESOLUTION_APPLY', %s, %s,
                'FRESH', 1.0, 0, 0, 'HEALTHY', %s)
        """,
        (checked_at, provider_observed_at, notes),
    )

    print(
        "SPORTRADAR_SURFACE_APPLY "
        f"status=UPDATED edition_id={edition_id} surface={canonical_surface} "
        f"matches={','.join(str(value) for value in match_ids)} writes=1",
        flush=True,
    )
    return "UPDATED", edition_id


def run_optional_surface_fill(
    *,
    dsn: str | None,
    api_key: str,
    access_level: str,
    language: str,
    env: Mapping[str, str] | None = None,
    fetch_seasons: Callable[..., tuple[datetime, dict[str, Any]]] = fetch_competition_seasons,
    fetch_info: Callable[..., tuple[datetime, dict[str, Any]]] = fetch_season_info,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(ENABLED_ENV)):
        return 0
    if not dsn:
        raise ValueError("Sportradar surface apply requires database connection")
    if _text(values.get(AUTH_ENV)) != AUTH_TOKEN:
        raise ValueError("Sportradar surface apply authorization token mismatch")

    match_ids = _parse_match_ids(values.get(MATCH_IDS_ENV))
    competition_id = _text(values.get(COMPETITION_ID_ENV))
    season_id = _text(values.get(SEASON_ID_ENV))
    expected_surface = _text(values.get(SURFACE_ENV)).upper()
    if not competition_id or not season_id:
        raise ValueError("Sportradar surface apply competition and season IDs are required")
    if expected_surface not in ALLOWED_SURFACES:
        raise ValueError("Sportradar surface apply expected surface is invalid")

    transport = {"access_level": access_level, "language": language}
    seasons_observed_at, seasons = fetch_seasons(api_key, competition_id, **transport)
    matching_seasons = [
        row for row in _season_rows(seasons) if _text(row.get("id")) == season_id
    ]
    if len(matching_seasons) != 1:
        raise ValueError(
            f"Sportradar surface apply expected season {season_id} was not uniquely present"
        )

    if access_level == "trial":
        sleep(2.0)
    info_observed_at, info = fetch_info(api_key, season_id, **transport)
    raw_surface, canonical_surface = _season_surface(info)
    if canonical_surface != expected_surface:
        raise ValueError(
            "Sportradar surface apply provider surface mismatch "
            f"expected={expected_surface} raw={raw_surface or '-'} "
            f"canonical={canonical_surface or '-'}"
        )

    checked_at = datetime.now(UTC)
    provider_observed_at = max(seasons_observed_at, info_observed_at)
    with psycopg.connect(dsn, autocommit=False) as conn:
        apply_surface_fill(
            conn,
            match_ids=match_ids,
            competition_id=competition_id,
            season_id=season_id,
            canonical_surface=expected_surface,
            raw_surface=raw_surface,
            provider_observed_at=provider_observed_at,
            checked_at=checked_at,
        )
        conn.commit()
    return 0
