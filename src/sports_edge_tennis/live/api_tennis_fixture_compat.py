"""Production compatibility enrichment for API-Tennis upcoming fixtures.

API-Tennis may return a blank ``event_status`` for prematch fixtures, exposes
round metadata as ``tournament_round``, and its fixture payload does not
consistently include court surface. This module keeps compatibility fixes
narrowly scoped and fail-closed:

* only safely identified future non-live fixtures get ``Not Started`` status;
* documented tournament-round suffixes are mapped to the canonical round codes
  already understood by the model; unknown wording remains unresolved;
* explicit ``event_qualification`` is copied into the canonical qualifying flag
  only when a fixture did not already provide one;
* missing surface is filled from current-season API-Tennis tournament metadata,
  the live draw endpoint, or one unanimously consistent prior-season surface for
  the same stable API-Tennis tournament id.

No model thresholds or betting policy are changed here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any, Callable, Iterable, Mapping

import psycopg

from sports_edge_tennis.data.normalize import normalize_tournament_identity
from sports_edge_tennis.data.sources.api_tennis_live import (
    ApiTennisIngestOutcome,
    ingest_api_tennis_source_a_fixtures,
)
from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.transport import TransportError, fetch_api_tennis_draw


_BLANK_FINAL_RESULTS = {"", "-"}
_NON_LIVE_VALUES = {"0", "false", "no"}
_SUPPORTED_SURFACES = {
    "hard": "Hard",
    "clay": "Clay",
    "grass": "Grass",
    "carpet": "Carpet",
}
_ROUND_SUFFIX_MAP = {
    "final": "F",
    "semi-finals": "SF",
    "quarter-finals": "QF",
    "1/8-finals": "R16",
    "1/16-finals": "R32",
    "1/32-finals": "R64",
    "1/64-finals": "R128",
}
_TRUE_VALUES = {"1", "true", "t", "yes", "y"}
_FALSE_VALUES = {"0", "false", "f", "no", "n"}
_TEAM_EVENT_SURFACE_FALLBACK_MARKERS = (
    "davis cup",
    "billie jean king cup",
    "bjk cup",
    "fed cup",
    "united cup",
    "atp cup",
)
FetchDraw = Callable[[str, str, int], tuple[datetime, dict[str, Any]]]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _future_start_utc(fixture: Mapping[str, Any], received_at: datetime) -> bool:
    event_date = _text(fixture.get("event_date"))
    event_time = _text(fixture.get("event_time"))
    if not event_date or not event_time:
        return False
    try:
        parsed_date = date.fromisoformat(event_date)
        normalized_time = event_time if len(event_time.split(":")) != 2 else f"{event_time}:00"
        parsed_time = time.fromisoformat(normalized_time)
    except ValueError:
        return False
    if parsed_time.tzinfo is not None:
        return False
    if received_at.tzinfo is None:
        return False
    start_at = datetime.combine(parsed_date, parsed_time, tzinfo=UTC)
    return start_at > received_at.astimezone(UTC)


def _is_safe_blank_upcoming_fixture(
    fixture: Mapping[str, Any],
    *,
    received_at: datetime,
) -> bool:
    if _text(fixture.get("event_status")):
        return False

    event_live = _text(fixture.get("event_live")).lower()
    if event_live not in _NON_LIVE_VALUES:
        return False

    if _text(fixture.get("event_winner")):
        return False

    final_result = _text(fixture.get("event_final_result"))
    if final_result not in _BLANK_FINAL_RESULTS:
        return False

    return _future_start_utc(fixture, received_at)


def normalize_api_tennis_upcoming_status(
    fixtures: Iterable[Mapping[str, Any]],
    *,
    received_at: datetime,
) -> list[dict[str, Any]]:
    """Promote only safely identified blank-status future fixtures to scheduled."""
    normalized: list[dict[str, Any]] = []
    for fixture in fixtures:
        row = dict(fixture)
        if _is_safe_blank_upcoming_fixture(row, received_at=received_at):
            row["event_status"] = "Not Started"
        normalized.append(row)
    return normalized


def _canonical_tournament_round(value: Any) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    suffix = raw.rsplit(" - ", 1)[-1].strip().casefold()
    return _ROUND_SUFFIX_MAP.get(suffix)


def _provider_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = _text(value).casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def normalize_api_tennis_fixture_rounds(
    fixtures: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Bridge documented provider round/qualification fields to canonical fields.

    Existing canonical/legacy round fields win. Only exact reviewed
    ``tournament_round`` suffixes are mapped; unknown provider wording remains
    missing so Agent 2 can fail closed rather than infer a scoring rule.
    """
    normalized: list[dict[str, Any]] = []
    for fixture in fixtures:
        row = dict(fixture)
        existing_round = any(
            _text(row.get(field_name))
            for field_name in ("round", "event_round", "round_code")
        )
        if not existing_round:
            round_code = _canonical_tournament_round(row.get("tournament_round"))
            if round_code is not None:
                row["round_code"] = round_code

        if not _text(row.get("qualifying_flag")):
            qualifying = _provider_bool(row.get("event_qualification"))
            if qualifying is not None:
                row["qualifying_flag"] = qualifying

        normalized.append(row)
    return normalized


def _normalize_surface(value: Any) -> str | None:
    return _SUPPORTED_SURFACES.get(_text(value).lower())


def _fixture_season(fixture: Mapping[str, Any]) -> int | None:
    explicit = _text(fixture.get("tournament_season"))
    if explicit:
        try:
            return int(explicit)
        except ValueError:
            pass
    event_date = _text(fixture.get("event_date"))
    if not event_date:
        return None
    try:
        return date.fromisoformat(event_date).year
    except ValueError:
        return None


def _allows_prior_season_surface_fallback(
    fixture: Mapping[str, Any],
) -> bool:
    """Return False for team competitions whose host tie can change surface.

    This is a conservative safety gate only. It never infers a surface from the
    tournament name; it merely prevents historical tournament-level metadata from
    being promoted into a new team tie.
    """
    values = (
        _text(fixture.get("tournament_name")),
        _text(fixture.get("event_type_type")),
    )
    normalized = " ".join(values).casefold()
    return not any(marker in normalized for marker in _TEAM_EVENT_SURFACE_FALLBACK_MARKERS)


def _stored_api_tennis_surface(
    conn: psycopg.Connection,
    *,
    tournament_key: str,
    season_year: int,
) -> str | None:
    rows = conn.execute(
        """
        SELECT DISTINCT te.surface::text
        FROM tournament_source_ids tsi
        JOIN tournament_editions te ON te.tournament_id = tsi.tournament_id
        WHERE tsi.source_system = 'API_TENNIS'
          AND tsi.source_tournament_id = %s
          AND te.season_year = %s
          AND te.surface IS NOT NULL
        ORDER BY te.surface::text
        """,
        (tournament_key, season_year),
    ).fetchall()
    surfaces = {_normalize_surface(row[0]) for row in rows}
    surfaces.discard(None)
    if len(surfaces) != 1:
        return None
    return next(iter(surfaces))


def _stored_api_tennis_prior_surface(
    conn: psycopg.Connection,
    *,
    tournament_key: str,
    season_year: int,
) -> str | None:
    """Return prior-season surface only when stable provider identity is unanimous.

    ``source_tournament_id`` is the API-Tennis tournament identity used to create the
    canonical tournament, while season lives on ``tournament_editions``. Reusing prior
    metadata is therefore safe only when every stored earlier edition with a supported
    surface agrees. Any historical surface change or conflicting metadata fails closed.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT te.surface::text
        FROM tournament_source_ids tsi
        JOIN tournament_editions te ON te.tournament_id = tsi.tournament_id
        WHERE tsi.source_system = 'API_TENNIS'
          AND tsi.source_tournament_id = %s
          AND te.season_year < %s
          AND te.surface IS NOT NULL
        ORDER BY te.surface::text
        """,
        (tournament_key, season_year),
    ).fetchall()
    surfaces = {_normalize_surface(row[0]) for row in rows}
    surfaces.discard(None)
    if len(surfaces) != 1:
        return _stored_canonical_prior_surface(
            conn, tournament_key=tournament_key, season_year=season_year
        )
    return next(iter(surfaces))


def _stored_canonical_prior_surface(
    conn: psycopg.Connection,
    *,
    tournament_key: str,
    season_year: int,
) -> str | None:
    """Cross-source deterministic prior surface for the same canonical tournament.

    Resolves the canonical tournament linked to this API-Tennis provider id, then
    finds canonical tournaments in the SAME tour scope whose normalized identity
    matches (e.g. historical ``"... CH"`` editions of a Challenger event). Only
    prior seasons are considered, so there is no future leakage. A single
    consistent surface is required; any conflict or absence fails closed.
    """
    if not tournament_key:
        return None
    anchor = conn.execute(
        """
        SELECT t.canonical_name, t.tour_scope::text
        FROM tournament_source_ids tsi
        JOIN tournaments t ON t.tournament_id = tsi.tournament_id
        WHERE tsi.source_system = 'API_TENNIS'
          AND tsi.source_tournament_id = %s
        LIMIT 1
        """,
        (tournament_key,),
    ).fetchone()
    if anchor is None:
        return None
    normalized_anchor = normalize_tournament_identity(str(anchor[0]))
    scope = str(anchor[1])
    rows = conn.execute(
        """
        SELECT DISTINCT t.canonical_name, te.surface::text
        FROM tournaments t
        JOIN tournament_editions te ON te.tournament_id = t.tournament_id
        WHERE t.tour_scope = %s
          AND te.season_year < %s
          AND te.surface IS NOT NULL
        """,
        (scope, season_year),
    ).fetchall()
    surfaces = {
        _normalize_surface(surface)
        for name, surface in rows
        if normalize_tournament_identity(str(name)) == normalized_anchor
    }
    surfaces.discard(None)
    if len(surfaces) != 1:
        return None
    return next(iter(surfaces))


def _draw_surface(payload: Mapping[str, Any]) -> str | None:
    tournament = payload.get("tournament")
    if not isinstance(tournament, Mapping):
        return None
    return _normalize_surface(tournament.get("tournament_surface"))


def enrich_api_tennis_fixture_surfaces(
    conn: psycopg.Connection,
    fixtures: Iterable[Mapping[str, Any]],
    *,
    api_key: str | None,
    fetch_draw: FetchDraw = fetch_api_tennis_draw,
) -> list[dict[str, Any]]:
    """Fill missing fixture surface without guessing tournament identity.

    Surface is tournament-level metadata. We preserve any valid fixture surface,
    prefer current-season stored API-Tennis truth, then ask the live draw endpoint.
    When a successful draw response has no usable surface, one prior-season fallback
    is allowed only if all earlier editions for the same stable API-Tennis tournament
    id agree on a single supported surface. Transport failures remain fail-closed.
    """
    rows = [dict(fixture) for fixture in fixtures]
    cache: dict[tuple[str, int], tuple[str | None, str]] = {}

    for row in rows:
        existing = _normalize_surface(row.get("surface") or row.get("event_surface"))
        if existing is not None:
            continue

        tournament_key = _text(row.get("tournament_key"))
        season_year = _fixture_season(row)
        if not tournament_key or season_year is None:
            continue
        cache_key = (tournament_key, season_year)

        if cache_key not in cache:
            stored = _stored_api_tennis_surface(
                conn,
                tournament_key=tournament_key,
                season_year=season_year,
            )
            if stored is not None:
                cache[cache_key] = (stored, "DB")
            elif api_key:
                try:
                    _received_at, payload = fetch_draw(api_key, tournament_key, season_year)
                    draw_surface = _draw_surface(payload)
                    if draw_surface is not None:
                        cache[cache_key] = (draw_surface, "DRAW")
                    else:
                        prior = None
                        if _allows_prior_season_surface_fallback(row):
                            prior = _stored_api_tennis_prior_surface(
                                conn,
                                tournament_key=tournament_key,
                                season_year=season_year,
                            )
                        if prior is not None:
                            cache[cache_key] = (prior, "DB_PRIOR_SEASON")
                        else:
                            kind = (
                                "DRAW_NO_SURFACE"
                                if _allows_prior_season_surface_fallback(row)
                                else "DRAW_NO_SURFACE_TEAM_EVENT"
                            )
                            print(
                                "API_TENNIS_SURFACE_UNAVAILABLE "
                                f"tournament_key={tournament_key} season={season_year} "
                                f"kind={kind}",
                                flush=True,
                            )
                            cache[cache_key] = (None, kind)
                except TransportError as exc:
                    print(
                        "API_TENNIS_SURFACE_UNAVAILABLE "
                        f"tournament_key={tournament_key} season={season_year} kind={exc.kind}",
                        flush=True,
                    )
                    cache[cache_key] = (None, "DRAW_FAILED")
            else:
                prior = None
                if _allows_prior_season_surface_fallback(row):
                    prior = _stored_api_tennis_prior_surface(
                        conn,
                        tournament_key=tournament_key,
                        season_year=season_year,
                    )
                if prior is not None:
                    cache[cache_key] = (prior, "DB_PRIOR_SEASON")
                else:
                    cache[cache_key] = (
                        None,
                        "NO_API_KEY"
                        if _allows_prior_season_surface_fallback(row)
                        else "NO_API_KEY_TEAM_EVENT",
                    )

            surface, source = cache[cache_key]
            if surface is not None:
                print(
                    "API_TENNIS_SURFACE "
                    f"tournament_key={tournament_key} season={season_year} "
                    f"surface={surface.upper()} source={source}",
                    flush=True,
                )

        surface, _source = cache[cache_key]
        if surface is not None:
            row["surface"] = surface

    return rows


def ingest_api_tennis_source_a_fixtures_with_upcoming_status(
    conn: psycopg.Connection,
    fixtures: list[dict[str, Any]],
    received_at: datetime,
) -> ApiTennisIngestOutcome:
    normalized = normalize_api_tennis_upcoming_status(fixtures, received_at=received_at)
    normalized = normalize_api_tennis_fixture_rounds(normalized)
    enriched = enrich_api_tennis_fixture_surfaces(
        conn,
        normalized,
        api_key=load_secret("API_TENNIS_API_KEY"),
    )
    return ingest_api_tennis_source_a_fixtures(
        conn,
        enriched,
        received_at=received_at,
    )
