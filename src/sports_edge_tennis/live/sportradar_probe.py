"""Sportradar Tennis coverage benchmark with an explicit guarded surface-fill gate.

The benchmark path is read-only and does not feed A1/A2/A3. A separate
OFF-by-default, operator-authorized gate may fill one verified NULL tournament
edition surface after re-verifying the exact provider season evidence.
It answers the first integration questions needed before Sportradar can be
considered as a production data source:

* does the configured key authenticate successfully?
* how much ATP/Challenger catalog and daily-event coverage is visible?
* can known tournament gaps (for example Rennes) expose a surface?
* do known ambiguous player-name cases expose stable Sportradar IDs?
* are match statistics present on the benchmark day's summaries?

No other returned provider payload is persisted.
"""

from __future__ import annotations

import argparse
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

import psycopg

from sports_edge_tennis.data.model_identity_bridge import ensure_schema as ensure_model_identity_bridge_schema
from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar_benchmark import (
    benchmark_challenger_a2_stats,
    link_gap_target,
    load_gap_target_for_match,
    load_latest_gap_targets,
    provider_name_identity_evidence,
)
from sports_edge_tennis.live.sportradar_identity_bridge_apply import (
    ENABLED_ENV as IDENTITY_BRIDGE_ENABLED_ENV,
    EVENT_ID_ENV as IDENTITY_BRIDGE_EVENT_ID_ENV,
    MATCH_ID_ENV as IDENTITY_BRIDGE_MATCH_ID_ENV,
    run_optional_identity_bridge,
)
from sports_edge_tennis.live.sportradar_surface_apply import run_optional_surface_fill
from sports_edge_tennis.live.sportradar import (
    SportradarTransportError,
    fetch_competition_seasons,
    fetch_competitions,
    fetch_competitor_merge_mappings,
    fetch_daily_summaries,
    fetch_rankings,
    fetch_season_info,
)


DEFAULT_COMPETITION_QUERIES = ("Rennes",)
DEFAULT_PLAYER_QUERIES = ("Nakashima", "Blanch")
MAX_QUERY_MATCHES = 5
TRIAL_MIN_REQUEST_INTERVAL_SECONDS = 2.0
TRIAL_429_RETRY_SECONDS = 3.0
HISTORY_DEPTH_ENABLED_ENV = "SPORTRADAR_HISTORY_DEPTH_PROBE_ENABLED"
HISTORY_DEPTH_MAX_COMPETITIONS_ENV = "SPORTRADAR_HISTORY_DEPTH_MAX_COMPETITIONS"
HISTORY_DEPTH_MAX_PAGES_ENV = "SPORTRADAR_HISTORY_DEPTH_MAX_PAGES"
HISTORY_DEPTH_DEFAULT_COMPETITIONS = 3
HISTORY_DEPTH_DEFAULT_PAGES = 2
A3_EVIDENCE_ENABLED_ENV = "SPORTRADAR_A3_EVIDENCE_PROBE_ENABLED"
A3_EVIDENCE_MAX_PAIRS_ENV = "SPORTRADAR_A3_EVIDENCE_MAX_PAIRS"
A3_EVIDENCE_DEFAULT_PAIRS = 10
A3_EVIDENCE_MAX_PAIRS = 10
A3_EVIDENCE_HORIZON_DAYS_ENV = "SPORTRADAR_A3_EVIDENCE_HORIZON_DAYS"
A3_EVIDENCE_DEFAULT_HORIZON_DAYS = 3
A3_EVIDENCE_MAX_HORIZON_DAYS = 7
GAP_SURFACE_ENABLED_ENV = "SPORTRADAR_GAP_SURFACE_PROBE_ENABLED"
GAP_SURFACE_MAX_COMPETITIONS_ENV = "SPORTRADAR_GAP_SURFACE_MAX_COMPETITIONS"
GAP_SURFACE_DEFAULT_COMPETITIONS = 8
GAP_SURFACE_MAX_COMPETITIONS = 12


def _augment_identity_bridge_exact_links(
    *,
    dsn: str | None,
    daily: Mapping[str, Any],
    exact_links: Sequence[tuple[Any, Any]],
    env: Mapping[str, str] | None = None,
    connect: Callable[..., Any] = psycopg.connect,
    target_loader: Callable[..., Any] = load_gap_target_for_match,
    linker: Callable[..., Any] = link_gap_target,
) -> tuple[tuple[Any, Any], ...]:
    """Read-only fallback for an authorized match that aged out of latest-batch gaps."""
    values = os.environ if env is None else env
    current_links = tuple(exact_links)
    if not _flag_enabled(values.get(IDENTITY_BRIDGE_ENABLED_ENV)):
        return current_links

    raw_match_id = _text(values.get(IDENTITY_BRIDGE_MATCH_ID_ENV))
    requested_event_id = _text(values.get(IDENTITY_BRIDGE_EVENT_ID_ENV))
    try:
        requested_match_id = int(raw_match_id)
    except ValueError:
        return current_links
    if requested_match_id <= 0 or not requested_event_id:
        return current_links

    for target, candidate in current_links:
        if (
            target.match_id == requested_match_id
            and candidate.event_id == requested_event_id
        ):
            print(
                "SPORTRADAR_IDENTITY_BRIDGE_REVALIDATION "
                f"status=CURRENT_BATCH match_id={requested_match_id} "
                f"event_id={requested_event_id} writes=0",
                flush=True,
            )
            return current_links

    if not dsn:
        print(
            "SPORTRADAR_IDENTITY_BRIDGE_REVALIDATION "
            f"status=NO_DATABASE match_id={requested_match_id} writes=0",
            flush=True,
        )
        return current_links

    with connect(dsn, autocommit=False) as conn:
        target = target_loader(conn, match_id=requested_match_id)
        if target is None:
            conn.rollback()
            print(
                "SPORTRADAR_IDENTITY_BRIDGE_REVALIDATION "
                f"status=NO_PREMATCH_TARGET match_id={requested_match_id} writes=0",
                flush=True,
            )
            return current_links

        result = linker(target, daily)
        candidate = result.candidates[0] if len(result.candidates) == 1 else None
        conn.rollback()

    if (
        result.status != "EXACT"
        or candidate is None
        or candidate.event_id != requested_event_id
    ):
        print(
            "SPORTRADAR_IDENTITY_BRIDGE_REVALIDATION "
            f"status={result.status} match_id={requested_match_id} "
            f"requested_event_id={requested_event_id} "
            f"candidate_event_id={candidate.event_id if candidate else '-'} "
            f"candidate_count={len(result.candidates)} writes=0",
            flush=True,
        )
        return current_links

    print(
        "SPORTRADAR_IDENTITY_BRIDGE_REVALIDATION "
        f"status=EXACT match_id={requested_match_id} "
        f"event_id={candidate.event_id} "
        f"prediction_as_of={target.prediction_as_of.isoformat()} "
        f"start_delta_min={candidate.start_delta_minutes if candidate.start_delta_minutes is not None else '-'} "
        "writes=0",
        flush=True,
    )
    return current_links + ((target, candidate),)


class RequestPacer:
    """Process-local request pacer for Sportradar trial QPS limits."""

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        self.min_interval_seconds = float(min_interval_seconds)
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_started_at: float | None = None

    def wait(self) -> None:
        now = self._monotonic()
        if self._last_started_at is not None:
            remaining = self.min_interval_seconds - (now - self._last_started_at)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_started_at = now

    def sleep_for(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)


def _paced_fetch(
    *,
    endpoint: str,
    fetcher: Callable[..., tuple[datetime, dict[str, Any]]],
    args: tuple[Any, ...],
    transport: Mapping[str, str],
    pacer: RequestPacer,
    access_level: str,
    retry_429_seconds: float,
) -> tuple[datetime, dict[str, Any]]:
    """Execute one named request with bounded trial pacing and one 429 retry."""
    pacer.wait()
    try:
        result = fetcher(*args, **transport)
        print(
            f"SPORTRADAR_REQUEST endpoint={endpoint} status=OK retry=0",
            flush=True,
        )
        return result
    except SportradarTransportError as exc:
        print(
            "SPORTRADAR_REQUEST "
            f"endpoint={endpoint} status=ERROR kind={exc.kind} retry=0",
            flush=True,
        )
        if exc.kind != "HTTP_429" or access_level != "trial":
            raise

    print(
        "SPORTRADAR_RATE_LIMIT "
        f"endpoint={endpoint} retry=1 wait={retry_429_seconds:.1f}s",
        flush=True,
    )
    pacer.sleep_for(retry_429_seconds)
    pacer.wait()
    try:
        result = fetcher(*args, **transport)
        print(
            f"SPORTRADAR_REQUEST endpoint={endpoint} status=OK retry=1",
            flush=True,
        )
        return result
    except SportradarTransportError as exc:
        print(
            "SPORTRADAR_REQUEST "
            f"endpoint={endpoint} status=ERROR kind={exc.kind} retry=1",
            flush=True,
        )
        raise


def _utc_date() -> date:
    return datetime.now(timezone.utc).date()


def _list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains(value: Any, query: str) -> bool:
    return query.casefold() in _text(value).casefold()


def _flag_enabled(value: str | None) -> bool:
    return _text(value).casefold() in {"1", "true", "yes", "on"}


def _run_optional_history_depth(
    *,
    api_key: str,
    target_date: date,
    access_level: str,
    language: str,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(HISTORY_DEPTH_ENABLED_ENV)):
        return 0

    try:
        max_competitions = int(
            _text(values.get(HISTORY_DEPTH_MAX_COMPETITIONS_ENV))
            or str(HISTORY_DEPTH_DEFAULT_COMPETITIONS)
        )
        max_pages = int(
            _text(values.get(HISTORY_DEPTH_MAX_PAGES_ENV))
            or str(HISTORY_DEPTH_DEFAULT_PAGES)
        )
    except ValueError as exc:
        raise ValueError("Sportradar history depth bounds must be integers") from exc

    if access_level == "trial":
        sleep(TRIAL_MIN_REQUEST_INTERVAL_SECONDS)

    if runner is None:
        from sports_edge_tennis.live.sportradar_history_probe import (
            run_probe as history_runner,
        )

        runner = history_runner

    print(
        "SPORTRADAR_HISTORY_DEPTH_DISPATCH "
        f"date={target_date.isoformat()} max_competitions={max_competitions} "
        f"max_pages={max_pages} writes=0",
        flush=True,
    )
    return runner(
        api_key=api_key,
        target_date=target_date,
        access_level=access_level,
        language=language,
        max_competitions=max_competitions,
        max_pages=max_pages,
    )


def _run_optional_a3_evidence(
    conn: psycopg.Connection,
    *,
    api_key: str,
    as_of: datetime,
    access_level: str,
    language: str,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(A3_EVIDENCE_ENABLED_ENV)):
        return 0

    try:
        max_pairs = int(
            _text(values.get(A3_EVIDENCE_MAX_PAIRS_ENV))
            or str(A3_EVIDENCE_DEFAULT_PAIRS)
        )
        horizon_days = int(
            _text(values.get(A3_EVIDENCE_HORIZON_DAYS_ENV))
            or str(A3_EVIDENCE_DEFAULT_HORIZON_DAYS)
        )
    except ValueError as exc:
        raise ValueError("Sportradar A3 bounds must be integers") from exc
    if not 1 <= max_pairs <= A3_EVIDENCE_MAX_PAIRS:
        raise ValueError(
            f"Sportradar A3 max pairs must be between 1 and {A3_EVIDENCE_MAX_PAIRS}"
        )
    if not 1 <= horizon_days <= A3_EVIDENCE_MAX_HORIZON_DAYS:
        raise ValueError(
            "Sportradar A3 horizon days must be between "
            f"1 and {A3_EVIDENCE_MAX_HORIZON_DAYS}"
        )

    if access_level == "trial":
        sleep(TRIAL_MIN_REQUEST_INTERVAL_SECONDS)

    if runner is None:
        from sports_edge_tennis.live.sportradar_a3_probe import (
            run_probe as a3_runner,
        )

        runner = a3_runner

    print(
        "SPORTRADAR_A3_DISPATCH "
        f"as_of={as_of.isoformat()} max_pairs={max_pairs} "
        f"horizon_days={horizon_days} writes=0",
        flush=True,
    )
    return runner(
        conn,
        api_key=api_key,
        as_of=as_of,
        max_pairs=max_pairs,
        horizon_days=horizon_days,
        access_level=access_level,
        language=language,
    )


def find_competitions(
    payload: Mapping[str, Any],
    query: str,
    *,
    limit: int = MAX_QUERY_MATCHES,
) -> tuple[Mapping[str, Any], ...]:
    matches = [
        row
        for row in _list(payload.get("competitions"))
        if _contains(row.get("name"), query)
    ]
    return tuple(matches[:limit])


def _ranked_competitors(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for ranking in _list(payload.get("rankings")):
        for ranked in _list(ranking.get("competitor_rankings")):
            competitor = ranked.get("competitor")
            if isinstance(competitor, Mapping):
                rows.append(competitor)
    return rows


def _summary_competitors(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for summary in _list(payload.get("summaries")):
        sport_event = summary.get("sport_event")
        if not isinstance(sport_event, Mapping):
            continue
        rows.extend(_list(sport_event.get("competitors")))
    return rows


def find_player_candidates(
    rankings_payload: Mapping[str, Any],
    daily_payload: Mapping[str, Any],
    query: str,
    *,
    limit: int = MAX_QUERY_MATCHES,
) -> tuple[Mapping[str, Any], ...]:
    candidates: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for competitor in _ranked_competitors(rankings_payload) + _summary_competitors(daily_payload):
        name = _text(competitor.get("name"))
        competitor_id = _text(competitor.get("id"))
        if not name or not competitor_id or not _contains(name, query):
            continue
        if competitor_id in seen:
            continue
        seen.add(competitor_id)
        candidates.append(competitor)
        if len(candidates) >= limit:
            break
    return tuple(candidates)


def select_season(
    payload: Mapping[str, Any],
    *,
    target_date: date,
) -> Mapping[str, Any] | None:
    seasons = _list(payload.get("seasons"))
    if not seasons:
        return None

    def parsed_day(raw: Any) -> date | None:
        try:
            return date.fromisoformat(_text(raw))
        except ValueError:
            return None

    for season in seasons:
        start = parsed_day(season.get("start_date"))
        end = parsed_day(season.get("end_date"))
        if start is not None and end is not None and start <= target_date <= end:
            return season

    ranked = sorted(
        seasons,
        key=lambda row: (
            _text(row.get("year")),
            _text(row.get("start_date")),
            _text(row.get("id")),
        ),
        reverse=True,
    )
    return ranked[0]


def normalize_sportradar_surface(value: Any) -> str | None:
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


def _season_surface(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    season = payload.get("season")
    season_info = season.get("info") if isinstance(season, Mapping) else None
    raw = _text(season_info.get("surface")) if isinstance(season_info, Mapping) else ""
    return raw, normalize_sportradar_surface(raw)


def _run_optional_gap_surface_probe(
    *,
    exact_links: Sequence[tuple[Any, Any]],
    api_key: str,
    target_date: date,
    paced: Callable[..., tuple[datetime, dict[str, Any]]],
    env: Mapping[str, str] | None = None,
) -> None:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(GAP_SURFACE_ENABLED_ENV)):
        return

    try:
        max_competitions = int(
            _text(values.get(GAP_SURFACE_MAX_COMPETITIONS_ENV))
            or str(GAP_SURFACE_DEFAULT_COMPETITIONS)
        )
    except ValueError as exc:
        raise ValueError("Sportradar gap surface max competitions must be an integer") from exc
    if not 1 <= max_competitions <= GAP_SURFACE_MAX_COMPETITIONS:
        raise ValueError(
            f"Sportradar gap surface max competitions must be between 1 and {GAP_SURFACE_MAX_COMPETITIONS}"
        )

    grouped: dict[str, list[tuple[Any, Any]]] = {}
    missing_competition_id = 0
    for target, candidate in exact_links:
        if getattr(target, "surface_status", None) != "UNRESOLVED":
            continue
        competition_id = _text(getattr(candidate, "competition_id", ""))
        if not competition_id:
            missing_competition_id += 1
            continue
        grouped.setdefault(competition_id, []).append((target, candidate))

    selected_groups = list(grouped.items())[:max_competitions]
    resolved_matches = 0
    unresolved_matches = missing_competition_id
    requests_planned = len(selected_groups) * 2
    print(
        "SPORTRADAR_GAP_SURFACE_START "
        f"exact_links={len(exact_links)} unique_competitions={len(grouped)} "
        f"probed_competitions={len(selected_groups)} max_competitions={max_competitions} "
        f"requests_planned_max={requests_planned} writes=0",
        flush=True,
    )

    for competition_id, rows in selected_groups:
        try:
            _, seasons = paced(
                "gap_competition_seasons",
                fetch_competition_seasons,
                api_key,
                competition_id,
            )
            selected = select_season(seasons, target_date=target_date)
            if selected is None:
                for target, candidate in rows:
                    unresolved_matches += 1
                    print(
                        "SPORTRADAR_GAP_SURFACE_CANDIDATE "
                        f"match_id={target.match_id} status=NO_SEASON "
                        f"competition_id={competition_id} "
                        f"competition={candidate.competition_name!r} "
                        "season_id=- raw_surface=- canonical_surface=- mapping_changed=0",
                        flush=True,
                    )
                continue

            season_id = _text(selected.get("id"))
            if not season_id:
                for target, candidate in rows:
                    unresolved_matches += 1
                    print(
                        "SPORTRADAR_GAP_SURFACE_CANDIDATE "
                        f"match_id={target.match_id} status=NO_SEASON_ID "
                        f"competition_id={competition_id} "
                        f"competition={candidate.competition_name!r} "
                        "season_id=- raw_surface=- canonical_surface=- mapping_changed=0",
                        flush=True,
                    )
                continue

            _, info = paced(
                "gap_season_info",
                fetch_season_info,
                api_key,
                season_id,
            )
            raw_surface, canonical_surface = _season_surface(info)
            status = "EXACT_SURFACE" if canonical_surface is not None else "UNSUPPORTED_SURFACE"
            for target, candidate in rows:
                if canonical_surface is None:
                    unresolved_matches += 1
                else:
                    resolved_matches += 1
                print(
                    "SPORTRADAR_GAP_SURFACE_CANDIDATE "
                    f"match_id={target.match_id} status={status} "
                    f"competition_id={competition_id} "
                    f"competition={candidate.competition_name!r} "
                    f"season_id={season_id} raw_surface={raw_surface or '-'} "
                    f"canonical_surface={canonical_surface or '-'} mapping_changed=0",
                    flush=True,
                )
        except SportradarTransportError as exc:
            unresolved_matches += len(rows)
            print(
                "SPORTRADAR_GAP_SURFACE_UNAVAILABLE "
                f"competition_id={competition_id} kind={exc.kind} "
                f"affected_matches={len(rows)} writes=0",
                flush=True,
            )

    deferred = sum(len(rows) for _, rows in list(grouped.items())[max_competitions:])
    unresolved_matches += deferred
    print(
        "SPORTRADAR_GAP_SURFACE_SUMMARY "
        f"exact_links={len(exact_links)} resolved_matches={resolved_matches} "
        f"unresolved_matches={unresolved_matches} deferred_matches={deferred} "
        f"probed_competitions={len(selected_groups)} writes=0",
        flush=True,
    )


def summary_has_statistics(summary: Mapping[str, Any]) -> bool:
    value = summary.get("statistics")
    return isinstance(value, Mapping) and bool(value)


def _summary_level(summary: Mapping[str, Any]) -> str:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return ""
    context = event.get("sport_event_context")
    if not isinstance(context, Mapping):
        return ""
    competition = context.get("competition")
    if not isinstance(competition, Mapping):
        return ""
    return _text(competition.get("level")).casefold()


def _summary_name(summary: Mapping[str, Any]) -> str:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return ""
    context = event.get("sport_event_context")
    if not isinstance(context, Mapping):
        return ""
    competition = context.get("competition")
    if not isinstance(competition, Mapping):
        return ""
    return _text(competition.get("name"))


def run_probe(
    *,
    api_key: str,
    target_date: date,
    access_level: str,
    language: str,
    competition_queries: Sequence[str],
    player_queries: Sequence[str],
) -> int:
    transport = {"access_level": access_level, "language": language}
    min_interval = (
        TRIAL_MIN_REQUEST_INTERVAL_SECONDS if access_level == "trial" else 0.0
    )
    pacer = RequestPacer(min_interval)
    retry_429_seconds = (
        TRIAL_429_RETRY_SECONDS if access_level == "trial" else 0.0
    )

    def paced(
        endpoint: str,
        fetcher: Callable[..., tuple[datetime, dict[str, Any]]],
        *args: Any,
    ) -> tuple[datetime, dict[str, Any]]:
        return _paced_fetch(
            endpoint=endpoint,
            fetcher=fetcher,
            args=args,
            transport=transport,
            pacer=pacer,
            access_level=access_level,
            retry_429_seconds=retry_429_seconds,
        )

    print(
        "SPORTRADAR_PROBE_START "
        f"date={target_date.isoformat()} access={access_level} language={language} "
        f"min_request_interval={min_interval:.1f}s "
        f"retry_429_wait={retry_429_seconds:.1f}s writes=0",
        flush=True,
    )

    try:
        _, competitions = paced("competitions", fetch_competitions, api_key)
        _, rankings = paced("rankings", fetch_rankings, api_key)
        _, daily = paced("daily_summaries", fetch_daily_summaries, api_key, target_date)
        _, merges = paced(
            "competitor_merge_mappings",
            fetch_competitor_merge_mappings,
            api_key,
        )
    except SportradarTransportError as exc:
        print(f"SPORTRADAR_PROBE_UNAVAILABLE kind={exc.kind} writes=0", flush=True)
        return 0

    competition_rows = _list(competitions.get("competitions"))
    summary_rows = _list(daily.get("summaries"))
    stat_rows = sum(1 for row in summary_rows if summary_has_statistics(row))
    challenger_rows = sum(
        1
        for row in summary_rows
        if "challenger" in _summary_level(row)
        or "challenger" in _summary_name(row).casefold()
    )
    merge_rows = _list(merges.get("mappings"))
    print(
        "SPORTRADAR_COVERAGE "
        f"competitions={len(competition_rows)} daily_summaries={len(summary_rows)} "
        f"challenger_daily={challenger_rows} summaries_with_statistics={stat_rows} "
        f"merge_mappings={len(merge_rows)}",
        flush=True,
    )

    a2_coverage = benchmark_challenger_a2_stats(daily)
    missing_text = ",".join(
        f"{reason}:{count}" for reason, count in a2_coverage.missing_reasons
    ) or "-"
    print(
        "SPORTRADAR_A2_COMPLETENESS "
        f"challenger_total={a2_coverage.challenger_total} "
        f"closed_total={a2_coverage.closed_total} "
        f"with_statistics={a2_coverage.summaries_with_statistics} "
        f"two_sided_statistics={a2_coverage.two_sided_statistics} "
        f"a2_core_ready={a2_coverage.a2_core_ready} "
        f"serve_detail_ready={a2_coverage.serve_detail_ready} "
        f"closed_with_statistics={a2_coverage.closed_with_statistics} "
        f"closed_a2_core_ready={a2_coverage.closed_a2_core_ready} "
        f"missing={missing_text} writes=0",
        flush=True,
    )

    exact_gap_links: list[tuple[Any, Any]] = []
    exact_surface_links: list[tuple[Any, Any]] = []

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print(
            "SPORTRADAR_GAP_BENCHMARK_BLOCKED reason=NO_DATABASE writes=0",
            flush=True,
        )
    else:
        try:
            with psycopg.connect(dsn, autocommit=True) as schema_conn:
                bridge_schema_status = ensure_model_identity_bridge_schema(schema_conn)
            print(
                "SPORTRADAR_MODEL_IDENTITY_BRIDGE_SCHEMA "
                f"status={bridge_schema_status} migration=0013",
                flush=True,
            )
            with psycopg.connect(dsn, autocommit=False) as conn:
                prediction_run_id, run_as_of, gap_targets = load_latest_gap_targets(
                    conn,
                    target_date=target_date,
                )
                if prediction_run_id is None or run_as_of is None:
                    print(
                        "SPORTRADAR_GAP_BENCHMARK_BLOCKED "
                        f"reason=NO_COMPLETED_RUN date={target_date.isoformat()} writes=0",
                        flush=True,
                    )
                else:
                    print(
                        "SPORTRADAR_GAP_BENCHMARK_START "
                        f"latest_prediction_run_id={prediction_run_id} "
                        "batch_window_minutes=15 "
                        f"as_of={run_as_of.isoformat()} gaps={len(gap_targets)} writes=0",
                        flush=True,
                    )
                    link_counts: Counter[str] = Counter()
                    for target in gap_targets:
                        result = link_gap_target(target, daily)
                        link_counts[result.status] += 1
                        candidate = result.candidates[0] if len(result.candidates) == 1 else None
                        if result.status == "EXACT" and candidate is not None:
                            exact_gap_links.append((target, candidate))
                            if target.surface_status == "UNRESOLVED":
                                exact_surface_links.append((target, candidate))

                        p1_identity = None
                        p2_identity = None
                        if result.status == "EXACT" and candidate is not None:
                            if target.player_1_identity_status == "LIVE_UNRESOLVED":
                                p1_identity = provider_name_identity_evidence(
                                    conn,
                                    live_player_id=target.player_1_id,
                                    provider_name=candidate.player_1_sportradar_name,
                                    as_of=target.prediction_as_of,
                                )
                            if target.player_2_identity_status == "LIVE_UNRESOLVED":
                                p2_identity = provider_name_identity_evidence(
                                    conn,
                                    live_player_id=target.player_2_id,
                                    provider_name=candidate.player_2_sportradar_name,
                                    as_of=target.prediction_as_of,
                                )

                        print(
                            "SPORTRADAR_GAP_LINK "
                            f"match_id={target.match_id} status={result.status} "
                            f"p1_status={target.player_1_identity_status} "
                            f"p2_status={target.player_2_identity_status} "
                            f"surface_status={target.surface_status} "
                            f"candidate_count={len(result.candidates)} "
                            f"event_id={candidate.event_id if candidate else '-'} "
                            f"competition_id={candidate.competition_id if candidate else '-'} "
                            f"p1_sr={candidate.player_1_sportradar_id if candidate else '-'} "
                            f"p2_sr={candidate.player_2_sportradar_id if candidate else '-'} "
                            f"p1_provider_identity={p1_identity.status if p1_identity else '-'} "
                            f"p1_provider_pit_ids={','.join(str(value) for value in p1_identity.pit_candidates[:3]) if p1_identity and p1_identity.pit_candidates else '-'} "
                            f"p2_provider_identity={p2_identity.status if p2_identity else '-'} "
                            f"p2_provider_pit_ids={','.join(str(value) for value in p2_identity.pit_candidates[:3]) if p2_identity and p2_identity.pit_candidates else '-'} "
                            f"start_delta_min={candidate.start_delta_minutes if candidate and candidate.start_delta_minutes is not None else '-'} "
                            "mapping_changed=0",
                            flush=True,
                        )
                    counts_text = " ".join(
                        f"{status.lower()}={count}"
                        for status, count in sorted(link_counts.items())
                    ) or "none=0"
                    print(
                        "SPORTRADAR_GAP_BENCHMARK_SUMMARY "
                        f"latest_prediction_run_id={prediction_run_id} "
                        f"batch_window_minutes=15 gaps={len(gap_targets)} "
                        f"{counts_text} writes=0",
                        flush=True,
                    )
                conn.rollback()
        except psycopg.Error:
            print(
                "SPORTRADAR_GAP_BENCHMARK_UNAVAILABLE kind=DATABASE writes=0",
                flush=True,
            )

    _run_optional_gap_surface_probe(
        exact_links=tuple(exact_surface_links),
        api_key=api_key,
        target_date=target_date,
        paced=paced,
    )

    try:
        run_optional_surface_fill(
            dsn=dsn,
            api_key=api_key,
            access_level=access_level,
            language=language,
        )
    except (ValueError, RuntimeError, psycopg.Error, SportradarTransportError) as exc:
        print(
            "SPORTRADAR_SURFACE_APPLY_BLOCKED "
            f"reason={type(exc).__name__} writes=0",
            flush=True,
        )
        return 2

    identity_bridge_links = _augment_identity_bridge_exact_links(
        dsn=dsn,
        daily=daily,
        exact_links=tuple(exact_gap_links),
    )

    try:
        run_optional_identity_bridge(
            dsn=dsn,
            exact_links=identity_bridge_links,
        )
    except (ValueError, RuntimeError, OSError, psycopg.Error) as exc:
        print(
            "SPORTRADAR_IDENTITY_BRIDGE_APPLY_BLOCKED "
            f"reason={type(exc).__name__} writes=0",
            flush=True,
        )
        return 2

    for query in competition_queries:
        matches = find_competitions(competitions, query)
        print(
            "SPORTRADAR_COMPETITION_QUERY "
            f"query={query!r} matches={len(matches)}",
            flush=True,
        )
        for competition in matches:
            competition_id = _text(competition.get("id"))
            if not competition_id:
                continue
            try:
                _, seasons = paced(
                    "competition_seasons",
                    fetch_competition_seasons,
                    api_key,
                    competition_id,
                )
                selected = select_season(seasons, target_date=target_date)
                if selected is None:
                    print(
                        "SPORTRADAR_COMPETITION "
                        f"query={query!r} competition_id={competition_id} "
                        f"name={_text(competition.get('name'))!r} season=NONE surface=-",
                        flush=True,
                    )
                    continue
                season_id = _text(selected.get("id"))
                _, info = paced(
                    "season_info",
                    fetch_season_info,
                    api_key,
                    season_id,
                )
                season = info.get("season")
                season_info = (
                    season.get("info")
                    if isinstance(season, Mapping)
                    else None
                )
                surface = (
                    _text(season_info.get("surface"))
                    if isinstance(season_info, Mapping)
                    else ""
                )
                print(
                    "SPORTRADAR_COMPETITION "
                    f"query={query!r} competition_id={competition_id} "
                    f"name={_text(competition.get('name'))!r} "
                    f"season_id={season_id} surface={surface or '-'}",
                    flush=True,
                )
            except SportradarTransportError as exc:
                print(
                    "SPORTRADAR_COMPETITION_UNAVAILABLE "
                    f"query={query!r} competition_id={competition_id} kind={exc.kind}",
                    flush=True,
                )

    for query in player_queries:
        candidates = find_player_candidates(rankings, daily, query)
        printable = ",".join(
            f"{_text(row.get('id'))}:{_text(row.get('name'))}"
            for row in candidates
        ) or "-"
        print(
            "SPORTRADAR_PLAYER_QUERY "
            f"query={query!r} candidates={len(candidates)} values={printable}",
            flush=True,
        )

    print("SPORTRADAR_PROBE_SUMMARY status=OK writes=0", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Sportradar Tennis coverage benchmark"
    )
    parser.add_argument("--date", type=date.fromisoformat, default=_utc_date())
    parser.add_argument(
        "--competition-query",
        action="append",
        dest="competition_queries",
        default=None,
    )
    parser.add_argument(
        "--player-query",
        action="append",
        dest="player_queries",
        default=None,
    )
    args = parser.parse_args(argv)

    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        print("SPORTRADAR_PROBE_BLOCKED reason=NO_API_KEY writes=0", flush=True)
        return 2

    access_level = os.environ.get(
        "SPORTRADAR_TENNIS_ACCESS_LEVEL", "trial"
    ).strip() or "trial"
    language = os.environ.get(
        "SPORTRADAR_TENNIS_LANGUAGE", "en"
    ).strip() or "en"
    response_format = os.environ.get(
        "SPORTRADAR_TENNIS_FORMAT", "json"
    ).strip() or "json"
    if response_format != "json":
        print("SPORTRADAR_PROBE_BLOCKED reason=FORMAT_MUST_BE_JSON writes=0", flush=True)
        return 2

    try:
        status = run_probe(
            api_key=api_key,
            target_date=args.date,
            access_level=access_level,
            language=language,
            competition_queries=tuple(args.competition_queries or DEFAULT_COMPETITION_QUERIES),
            player_queries=tuple(args.player_queries or DEFAULT_PLAYER_QUERIES),
        )
        if status != 0:
            return status

        if _flag_enabled(os.environ.get(A3_EVIDENCE_ENABLED_ENV)):
            dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
            if not dsn:
                print("SPORTRADAR_A3_BLOCKED reason=NO_DATABASE writes=0", flush=True)
                return 2
            with psycopg.connect(dsn, autocommit=True) as conn:
                a3_status = _run_optional_a3_evidence(
                    conn,
                    api_key=api_key,
                    as_of=datetime.now(timezone.utc),
                    access_level=access_level,
                    language=language,
                )
            if a3_status != 0:
                return a3_status

        return _run_optional_history_depth(
            api_key=api_key,
            target_date=args.date,
            access_level=access_level,
            language=language,
        )
    except ValueError as exc:
        print(
            f"SPORTRADAR_PROBE_BLOCKED reason={type(exc).__name__} writes=0",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
