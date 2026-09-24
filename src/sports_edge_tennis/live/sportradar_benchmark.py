"""Read-only Sportradar benchmark helpers for current Tennis data gaps.

This module is intentionally observational. It never persists Sportradar IDs,
statistics, surfaces, or canonical mappings. The validation probe uses it to:

* measure whether Challenger daily summaries contain the exact statistics the
  current Agent 2 history accumulator can consume; and
* compare current unresolved ATP/Challenger production targets with Sportradar
  sport-event and competitor IDs using deterministic participant aliases plus a
  bounded start time window.

Any proposed canonical/provider mapping remains a later reviewed change.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import psycopg

from sports_edge_tennis.data.model_identity_bridge import LIVE_FIXTURE_SOURCE_SYSTEMS
from sports_edge_tennis.data.normalize import normalize_person_lookup, tennis_data_aliases
from sports_edge_tennis.runner.targets import (
    _model_identity_status,
    _resolve_model_player_id,
    _resolve_model_surface,
)


A2_CORE_STAT_FIELDS = (
    "first_serve_points_won",
    "second_serve_points_won",
    "service_points_won",
    "service_points_lost",
)
A2_DETAIL_STAT_FIELDS = (
    "first_serve_successful",
    "second_serve_successful",
)
DEFAULT_LINK_MAX_START_DELTA_MINUTES = 180
LATEST_BATCH_WINDOW_MINUTES = 15


@dataclass(frozen=True)
class ChallengerA2Coverage:
    challenger_total: int
    closed_total: int
    summaries_with_statistics: int
    two_sided_statistics: int
    a2_core_ready: int
    serve_detail_ready: int
    closed_with_statistics: int
    closed_a2_core_ready: int
    missing_reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class CanonicalGapTarget:
    match_id: int
    prediction_as_of: datetime
    player_1_id: int
    player_1_name: str
    player_2_id: int
    player_2_name: str
    match_start_at: datetime
    tournament_id: int
    tournament_name: str
    season_year: int
    player_1_identity_status: str
    player_2_identity_status: str
    surface_status: str


@dataclass(frozen=True)
class SportradarLinkCandidate:
    event_id: str
    competition_id: str
    competition_name: str
    start_at: datetime | None
    start_delta_minutes: int | None
    player_1_sportradar_id: str
    player_1_sportradar_name: str
    player_2_sportradar_id: str
    player_2_sportradar_name: str


@dataclass(frozen=True)
class GapLinkResult:
    status: str
    candidates: tuple[SportradarLinkCandidate, ...]


@dataclass(frozen=True)
class ProviderNameIdentityEvidence:
    status: str
    alias_candidates: tuple[int, ...]
    pit_candidates: tuple[int, ...]


def provider_name_identity_evidence(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    provider_name: str,
    as_of: datetime,
) -> ProviderNameIdentityEvidence:
    """Read-only historical identity evidence from one exact provider participant name.

    This is intentionally stricter than name matching alone. It uses the same
    TENNIS_DATA alias and PIT-visible completed-history conditions as the production
    live-to-history bridge. It never mutates canonical identity.
    """
    normalized_aliases = sorted(deterministic_name_keys(provider_name))
    if not normalized_aliases:
        return ProviderNameIdentityEvidence("NO_NORMALIZED_ALIAS", (), ())

    alias_rows = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> ALL(%s)
          )
        ORDER BY pa.player_id
        """,
        (normalized_aliases, live_player_id, list(LIVE_FIXTURE_SOURCE_SYSTEMS)),
    ).fetchall()
    alias_ids = tuple(int(row[0]) for row in alias_rows)
    if not alias_ids:
        return ProviderNameIdentityEvidence("NO_HISTORICAL_ALIAS", (), ())

    pit_rows = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> ALL(%s)
          )
          AND EXISTS (
              SELECT 1
              FROM match_result_observations hist_mro
              JOIN match_versions hist_mv
                ON hist_mv.match_id = hist_mro.match_id
               AND hist_mv.source_observed_at <= hist_mro.source_observed_at
               AND hist_mv.as_of <= hist_mro.as_of
              WHERE (hist_mv.player_1_id = pa.player_id OR hist_mv.player_2_id = pa.player_id)
                AND hist_mro.outcome_status = 'COMPLETED'
                AND hist_mro.source_observed_at <= %s
                AND hist_mro.as_of <= %s
          )
        ORDER BY pa.player_id
        """,
        (
            normalized_aliases,
            live_player_id,
            list(LIVE_FIXTURE_SOURCE_SYSTEMS),
            as_of,
            as_of,
        ),
    ).fetchall()
    pit_ids = tuple(int(row[0]) for row in pit_rows)
    if not pit_ids:
        return ProviderNameIdentityEvidence("ALIAS_WITHOUT_PIT_HISTORY", alias_ids, ())
    if len(pit_ids) == 1:
        return ProviderNameIdentityEvidence("UNIQUE_PIT_CANDIDATE", alias_ids, pit_ids)
    return ProviderNameIdentityEvidence("AMBIGUOUS_PIT_CANDIDATES", alias_ids, pit_ids)


def _list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_timestamp(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _competition(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return {}
    context = event.get("sport_event_context")
    if not isinstance(context, Mapping):
        return {}
    competition = context.get("competition")
    return competition if isinstance(competition, Mapping) else {}


def summary_is_challenger(summary: Mapping[str, Any]) -> bool:
    competition = _competition(summary)
    level = _text(competition.get("level")).casefold()
    name = _text(competition.get("name")).casefold()
    category_name = ""
    event = summary.get("sport_event")
    if isinstance(event, Mapping):
        context = event.get("sport_event_context")
        if isinstance(context, Mapping):
            category = context.get("category")
            if isinstance(category, Mapping):
                category_name = _text(category.get("name")).casefold()
    return (
        "challenger" in level
        or "challenger" in name
        or "challenger" in category_name
    )


def _summary_is_closed(summary: Mapping[str, Any]) -> bool:
    status = summary.get("sport_event_status")
    if not isinstance(status, Mapping):
        return False
    return _text(status.get("status")).casefold() in {"closed", "ended"}


def _statistics_competitors(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    statistics = summary.get("statistics")
    if not isinstance(statistics, Mapping):
        return []
    totals = statistics.get("totals")
    if not isinstance(totals, Mapping):
        return []
    return _list(totals.get("competitors"))


def _stats_mapping(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("statistics")
    return value if isinstance(value, Mapping) else {}


def _present_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _competitor_core_ready(row: Mapping[str, Any]) -> bool:
    stats = _stats_mapping(row)
    if not all(_present_number(stats.get(field)) for field in A2_CORE_STAT_FIELDS):
        return False
    service_points_played = int(stats["service_points_won"]) + int(stats["service_points_lost"])
    service_points_won = int(stats["first_serve_points_won"]) + int(
        stats["second_serve_points_won"]
    )
    return service_points_played > 0 and 0 <= service_points_won <= service_points_played


def _competitor_detail_ready(row: Mapping[str, Any]) -> bool:
    if not _competitor_core_ready(row):
        return False
    stats = _stats_mapping(row)
    return all(_present_number(stats.get(field)) for field in A2_DETAIL_STAT_FIELDS)


def benchmark_challenger_a2_stats(
    daily_payload: Mapping[str, Any],
) -> ChallengerA2Coverage:
    challenger_total = 0
    closed_total = 0
    summaries_with_statistics = 0
    two_sided_statistics = 0
    a2_core_ready = 0
    serve_detail_ready = 0
    closed_with_statistics = 0
    closed_a2_core_ready = 0
    missing: Counter[str] = Counter()

    for summary in _list(daily_payload.get("summaries")):
        if not summary_is_challenger(summary):
            continue
        challenger_total += 1
        closed = _summary_is_closed(summary)
        if closed:
            closed_total += 1

        rows = _statistics_competitors(summary)
        if not rows:
            missing["NO_STATISTICS"] += 1
            continue
        summaries_with_statistics += 1
        if closed:
            closed_with_statistics += 1

        if len(rows) != 2:
            missing["NOT_TWO_COMPETITORS"] += 1
            continue
        two_sided_statistics += 1

        event_missing_fields: set[str] = set()
        for row in rows:
            stats = _stats_mapping(row)
            for field in A2_CORE_STAT_FIELDS:
                if not _present_number(stats.get(field)):
                    event_missing_fields.add(field)
            for field in A2_DETAIL_STAT_FIELDS:
                if not _present_number(stats.get(field)):
                    event_missing_fields.add(field)
        for field in sorted(event_missing_fields):
            missing[field] += 1

        core_ready = all(_competitor_core_ready(row) for row in rows)
        if core_ready:
            a2_core_ready += 1
            if closed:
                closed_a2_core_ready += 1
        if all(_competitor_detail_ready(row) for row in rows):
            serve_detail_ready += 1

    return ChallengerA2Coverage(
        challenger_total=challenger_total,
        closed_total=closed_total,
        summaries_with_statistics=summaries_with_statistics,
        two_sided_statistics=two_sided_statistics,
        a2_core_ready=a2_core_ready,
        serve_detail_ready=serve_detail_ready,
        closed_with_statistics=closed_with_statistics,
        closed_a2_core_ready=closed_a2_core_ready,
        missing_reasons=tuple(sorted(missing.items())),
    )


def deterministic_name_keys(name: str) -> frozenset[str]:
    values = {_text(name)}
    raw = _text(name)
    if "," in raw:
        surname, given = (part.strip() for part in raw.split(",", 1))
        if surname and given:
            values.add(f"{given} {surname}")

    expanded = set(values)
    for value in tuple(values):
        expanded.update(tennis_data_aliases(value))

    return frozenset(
        key
        for key in (normalize_person_lookup(value) for value in expanded if value)
        if key
    )


def _name_matches(canonical_name: str, sportradar_name: str) -> bool:
    return bool(
        deterministic_name_keys(canonical_name)
        & deterministic_name_keys(sportradar_name)
    )


def _event_competitors(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return []
    return _list(event.get("competitors"))


def _candidate_for_summary(
    target: CanonicalGapTarget,
    summary: Mapping[str, Any],
) -> SportradarLinkCandidate | None:
    # Gap targets already come from the accepted production target set. Do not
    # re-filter Sportradar candidates to Challenger only here, otherwise ATP-main
    # gaps (for example Davis Cup) can never be benchmarked.
    competitors = _event_competitors(summary)
    if len(competitors) != 2:
        return None

    first, second = competitors
    first_name = _text(first.get("name"))
    second_name = _text(second.get("name"))
    direct = _name_matches(target.player_1_name, first_name) and _name_matches(
        target.player_2_name, second_name
    )
    reverse = _name_matches(target.player_1_name, second_name) and _name_matches(
        target.player_2_name, first_name
    )
    if direct == reverse:
        # Neither orientation matched, or both orientations are ambiguous.
        return None

    player_1 = first if direct else second
    player_2 = second if direct else first
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return None
    event_id = _text(event.get("id"))
    if not event_id:
        return None
    start_at = _parse_timestamp(event.get("start_time"))
    start_delta_minutes = (
        None
        if start_at is None
        else int(abs((target.match_start_at.astimezone(timezone.utc) - start_at).total_seconds()) // 60)
    )
    competition = _competition(summary)
    return SportradarLinkCandidate(
        event_id=event_id,
        competition_id=_text(competition.get("id")),
        competition_name=_text(competition.get("name")),
        start_at=start_at,
        start_delta_minutes=start_delta_minutes,
        player_1_sportradar_id=_text(player_1.get("id")),
        player_1_sportradar_name=_text(player_1.get("name")),
        player_2_sportradar_id=_text(player_2.get("id")),
        player_2_sportradar_name=_text(player_2.get("name")),
    )


def link_gap_target(
    target: CanonicalGapTarget,
    daily_payload: Mapping[str, Any],
    *,
    max_start_delta_minutes: int = DEFAULT_LINK_MAX_START_DELTA_MINUTES,
) -> GapLinkResult:
    if max_start_delta_minutes < 0:
        raise ValueError("max_start_delta_minutes must be non-negative")

    candidates = tuple(
        candidate
        for summary in _list(daily_payload.get("summaries"))
        if (candidate := _candidate_for_summary(target, summary)) is not None
    )
    in_window = tuple(
        candidate
        for candidate in candidates
        if candidate.start_delta_minutes is not None
        and candidate.start_delta_minutes <= max_start_delta_minutes
    )

    if len(in_window) == 1:
        return GapLinkResult("EXACT", in_window)
    if len(in_window) > 1:
        return GapLinkResult("AMBIGUOUS", in_window)
    if len(candidates) == 1:
        return GapLinkResult("TIME_MISMATCH", candidates)
    if len(candidates) > 1:
        return GapLinkResult("PAIR_AMBIGUOUS", candidates)
    return GapLinkResult("UNRESOLVED", ())


def load_latest_gap_targets(
    conn: psycopg.Connection,
    *,
    target_date: date,
) -> tuple[int | None, datetime | None, tuple[CanonicalGapTarget, ...]]:
    """Load unresolved targets from the latest completed hourly production batch.

    One live batch may create more than one prediction run because each slate date is
    scored separately. Treating only the numerically latest run as "latest" silently
    drops the earlier slates. We therefore anchor on the latest completed run and
    include completed non-experiment runs from the preceding bounded batch window.
    Each match is deduplicated to its newest run and keeps that run's own as-of time
    for point-in-time identity and surface resolution.
    """
    run_row = conn.execute(
        """
        SELECT prediction_run_id, as_of
        FROM prediction_runs
        WHERE run_status = 'COMPLETED'
          AND experiment_id IS NULL
          AND (as_of AT TIME ZONE 'UTC')::date = %s
        ORDER BY as_of DESC, prediction_run_id DESC
        LIMIT 1
        """,
        (target_date,),
    ).fetchone()
    if run_row is None:
        return None, None, ()

    prediction_run_id = int(run_row[0])
    run_as_of = run_row[1]
    rows = conn.execute(
        """
        WITH batch_runs AS (
            SELECT prediction_run_id, as_of
            FROM prediction_runs
            WHERE run_status = 'COMPLETED'
              AND experiment_id IS NULL
              AND as_of >= %s - make_interval(mins => %s)
              AND as_of <= %s
        ),
        latest_targets AS (
            SELECT DISTINCT ON (prt.match_id)
                prt.match_id,
                br.prediction_run_id,
                br.as_of AS prediction_as_of
            FROM prediction_run_targets prt
            JOIN batch_runs br
              ON br.prediction_run_id = prt.prediction_run_id
            ORDER BY
                prt.match_id,
                br.as_of DESC,
                br.prediction_run_id DESC
        )
        SELECT
            lt.match_id,
            lt.prediction_as_of,
            mv.player_1_id,
            p1.canonical_name,
            mv.player_2_id,
            p2.canonical_name,
            mv.match_start_at,
            mv.surface::text,
            te.surface::text,
            mv.tour_scope::text,
            mv.competition_tier::text,
            t.tournament_id,
            t.canonical_name,
            te.season_year
        FROM latest_targets lt
        JOIN LATERAL (
            SELECT mv_inner.*
            FROM match_versions mv_inner
            WHERE mv_inner.match_id = lt.match_id
              AND mv_inner.source_observed_at <= lt.prediction_as_of
              AND mv_inner.as_of <= lt.prediction_as_of
            ORDER BY
                mv_inner.as_of DESC,
                mv_inner.source_observed_at DESC,
                mv_inner.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        JOIN matches m ON m.match_id = lt.match_id
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        ORDER BY lt.match_id
        """,
        (run_as_of, LATEST_BATCH_WINDOW_MINUTES, run_as_of),
    ).fetchall()

    gaps: list[CanonicalGapTarget] = []
    for row in rows:
        (
            match_id,
            target_as_of,
            player_1_id,
            player_1_name,
            player_2_id,
            player_2_name,
            match_start_at,
            live_surface,
            edition_surface,
            tour_scope,
            _competition_tier,
            tournament_id,
            tournament_name,
            season_year,
        ) = row

        model_player_1_id = _resolve_model_player_id(
            conn,
            player_id=int(player_1_id),
            as_of=target_as_of,
        )
        model_player_2_id = _resolve_model_player_id(
            conn,
            player_id=int(player_2_id),
            as_of=target_as_of,
        )
        player_1_status = _model_identity_status(
            conn,
            live_player_id=int(player_1_id),
            model_player_id=model_player_1_id,
        )
        player_2_status = _model_identity_status(
            conn,
            live_player_id=int(player_2_id),
            model_player_id=model_player_2_id,
        )
        model_surface, surface_status = _resolve_model_surface(
            conn,
            tournament_id=int(tournament_id),
            canonical_name=str(tournament_name),
            tour_scope=str(tour_scope),
            season_year=int(season_year),
            live_surface=live_surface,
            edition_surface=edition_surface,
            as_of=target_as_of,
        )
        if (
            player_1_status != "LIVE_UNRESOLVED"
            and player_2_status != "LIVE_UNRESOLVED"
            and model_surface is not None
        ):
            continue

        gaps.append(
            CanonicalGapTarget(
                match_id=int(match_id),
                prediction_as_of=target_as_of,
                player_1_id=int(player_1_id),
                player_1_name=str(player_1_name),
                player_2_id=int(player_2_id),
                player_2_name=str(player_2_name),
                match_start_at=match_start_at,
                tournament_id=int(tournament_id),
                tournament_name=str(tournament_name),
                season_year=int(season_year),
                player_1_identity_status=player_1_status,
                player_2_identity_status=player_2_status,
                surface_status=surface_status,
            )
        )

    return prediction_run_id, run_as_of, tuple(gaps)


__all__ = [
    "A2_CORE_STAT_FIELDS",
    "A2_DETAIL_STAT_FIELDS",
    "ChallengerA2Coverage",
    "CanonicalGapTarget",
    "GapLinkResult",
    "SportradarLinkCandidate",
    "benchmark_challenger_a2_stats",
    "deterministic_name_keys",
    "link_gap_target",
    "load_latest_gap_targets",
    "summary_is_challenger",
]
