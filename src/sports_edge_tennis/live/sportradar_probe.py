"""Read-only Sportradar Tennis coverage benchmark.

The probe deliberately performs no database writes and does not feed A1/A2/A3.
It answers the first integration questions needed before Sportradar can be
considered as a production data source:

* does the configured key authenticate successfully?
* how much ATP/Challenger catalog and daily-event coverage is visible?
* can known tournament gaps (for example Rennes) expose a surface?
* do known ambiguous player-name cases expose stable Sportradar IDs?
* are match statistics present on the benchmark day's summaries?

No returned provider payload is persisted.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from sports_edge_tennis.live.credentials import load_secret
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
        return run_probe(
            api_key=api_key,
            target_date=args.date,
            access_level=access_level,
            language=language,
            competition_queries=tuple(args.competition_queries or DEFAULT_COMPETITION_QUERIES),
            player_queries=tuple(args.player_queries or DEFAULT_PLAYER_QUERIES),
        )
    except ValueError as exc:
        print(
            f"SPORTRADAR_PROBE_BLOCKED reason={type(exc).__name__} writes=0",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
