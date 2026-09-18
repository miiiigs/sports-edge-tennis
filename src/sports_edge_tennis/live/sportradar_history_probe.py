"""Bounded read-only Sportradar historical-depth benchmark.

This probe answers a narrow provider-selection question before any historical
backfill is activated: for Challenger competitions visible in the current daily
slate, do recent prior seasons contain enough two-sided serve statistics for the
existing Agent 2 history accumulator?

Safety:
* read-only Sportradar requests only;
* no database connection or writes;
* no canonical mappings;
* no model/Manager/Market/Veto/staking changes;
* hard caps on sampled competitions and season-summary pages.
"""

from __future__ import annotations

import argparse
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar import (
    SportradarTransportError,
    fetch_competition_seasons,
    fetch_daily_summaries,
    fetch_season_summaries,
)
from sports_edge_tennis.live.sportradar_benchmark import benchmark_challenger_a2_stats
from sports_edge_tennis.live.sportradar_probe import (
    RequestPacer,
    TRIAL_429_RETRY_SECONDS,
    TRIAL_MIN_REQUEST_INTERVAL_SECONDS,
    _paced_fetch,
)


MAX_COMPETITIONS = 5
MAX_PAGES = 3
DEFAULT_COMPETITIONS = 3
DEFAULT_PAGES = 2
SEASON_SUMMARY_PAGE_SIZE = 200


@dataclass(frozen=True)
class ChallengerCompetition:
    competition_id: str
    name: str


@dataclass(frozen=True)
class PriorSeason:
    season_id: str
    name: str
    start_date: date | None
    end_date: date | None
    year: str


def _list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _day(value: Any) -> date | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _competition(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return {}
    context = event.get("sport_event_context")
    if not isinstance(context, Mapping):
        return {}
    competition = context.get("competition")
    return competition if isinstance(competition, Mapping) else {}


def _category_name(summary: Mapping[str, Any]) -> str:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return ""
    context = event.get("sport_event_context")
    if not isinstance(context, Mapping):
        return ""
    category = context.get("category")
    if not isinstance(category, Mapping):
        return ""
    return _text(category.get("name"))


def discover_current_challenger_singles(
    daily_payload: Mapping[str, Any],
    *,
    limit: int,
) -> tuple[ChallengerCompetition, ...]:
    """Return deterministic unique Challenger singles competitions from one slate."""
    if limit < 1:
        return ()

    found: dict[str, ChallengerCompetition] = {}
    for summary in _list(daily_payload.get("summaries")):
        competition = _competition(summary)
        competition_id = _text(competition.get("id"))
        name = _text(competition.get("name"))
        competition_type = _text(competition.get("type")).casefold()
        level = _text(competition.get("level")).casefold()
        category = _category_name(summary).casefold()
        challenger = "challenger" in level or "challenger" in name.casefold() or "challenger" in category
        if not challenger or competition_type != "singles" or not competition_id or not name:
            continue
        found.setdefault(
            competition_id,
            ChallengerCompetition(competition_id=competition_id, name=name),
        )
    return tuple(found[key] for key in sorted(found)[:limit])


def select_previous_season(
    seasons_payload: Mapping[str, Any],
    *,
    target_date: date,
) -> PriorSeason | None:
    """Choose the most recent season that ended before the benchmark date."""
    candidates: list[PriorSeason] = []
    for row in _list(seasons_payload.get("seasons")):
        season_id = _text(row.get("id"))
        if not season_id:
            continue
        end_date = _day(row.get("end_date"))
        if end_date is None or end_date >= target_date:
            continue
        candidates.append(
            PriorSeason(
                season_id=season_id,
                name=_text(row.get("name")),
                start_date=_day(row.get("start_date")),
                end_date=end_date,
                year=_text(row.get("year")),
            )
        )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item.end_date or date.min,
            item.start_date or date.min,
            item.season_id,
        ),
    )


def fetch_bounded_season_summaries(
    *,
    api_key: str,
    season_id: str,
    max_pages: int,
    transport: Mapping[str, str],
    pacer: RequestPacer,
    access_level: str,
    retry_429_seconds: float,
    fetcher: Callable[..., tuple[datetime, dict[str, Any]]] = fetch_season_summaries,
) -> tuple[dict[str, Any], int]:
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")

    combined: list[Mapping[str, Any]] = []
    pages = 0
    for page_index in range(max_pages):
        start = page_index * SEASON_SUMMARY_PAGE_SIZE
        _, payload = _paced_fetch(
            endpoint="season_summaries",
            fetcher=fetcher,
            args=(api_key, season_id),
            transport={**transport, "start": start},
            pacer=pacer,
            access_level=access_level,
            retry_429_seconds=retry_429_seconds,
        )
        page_rows = _list(payload.get("summaries"))
        combined.extend(page_rows)
        pages += 1
        print(
            "SPORTRADAR_HISTORY_PAGE "
            f"season_id={season_id} page={page_index + 1} start={start} "
            f"rows={len(page_rows)} writes=0",
            flush=True,
        )
        if len(page_rows) < SEASON_SUMMARY_PAGE_SIZE:
            break
    return {"summaries": combined}, pages


def run_probe(
    *,
    api_key: str,
    target_date: date,
    access_level: str,
    language: str,
    max_competitions: int,
    max_pages: int,
) -> int:
    if not 1 <= max_competitions <= MAX_COMPETITIONS:
        raise ValueError(f"max_competitions must be between 1 and {MAX_COMPETITIONS}")
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")

    min_interval = TRIAL_MIN_REQUEST_INTERVAL_SECONDS if access_level == "trial" else 0.0
    retry_429_seconds = TRIAL_429_RETRY_SECONDS if access_level == "trial" else 0.0
    pacer = RequestPacer(min_interval)
    transport = {"access_level": access_level, "language": language}

    print(
        "SPORTRADAR_HISTORY_START "
        f"date={target_date.isoformat()} max_competitions={max_competitions} "
        f"max_pages={max_pages} writes=0",
        flush=True,
    )

    try:
        _, daily = _paced_fetch(
            endpoint="daily_summaries",
            fetcher=fetch_daily_summaries,
            args=(api_key, target_date),
            transport=transport,
            pacer=pacer,
            access_level=access_level,
            retry_429_seconds=retry_429_seconds,
        )
    except SportradarTransportError as exc:
        print(
            f"SPORTRADAR_HISTORY_UNAVAILABLE stage=daily kind={exc.kind} writes=0",
            flush=True,
        )
        return 0

    competitions = discover_current_challenger_singles(
        daily,
        limit=max_competitions,
    )
    print(
        "SPORTRADAR_HISTORY_DISCOVERY "
        f"challenger_singles={len(competitions)} writes=0",
        flush=True,
    )

    total = Counter()
    sampled = 0
    for competition in competitions:
        try:
            _, seasons = _paced_fetch(
                endpoint="competition_seasons",
                fetcher=fetch_competition_seasons,
                args=(api_key, competition.competition_id),
                transport=transport,
                pacer=pacer,
                access_level=access_level,
                retry_429_seconds=retry_429_seconds,
            )
        except SportradarTransportError as exc:
            print(
                "SPORTRADAR_HISTORY_COMPETITION_UNAVAILABLE "
                f"competition_id={competition.competition_id} kind={exc.kind} writes=0",
                flush=True,
            )
            continue

        prior = select_previous_season(seasons, target_date=target_date)
        if prior is None:
            print(
                "SPORTRADAR_HISTORY_COMPETITION "
                f"competition_id={competition.competition_id} "
                f"name={competition.name!r} prior_season=NONE writes=0",
                flush=True,
            )
            continue

        try:
            combined, pages = fetch_bounded_season_summaries(
                api_key=api_key,
                season_id=prior.season_id,
                max_pages=max_pages,
                transport=transport,
                pacer=pacer,
                access_level=access_level,
                retry_429_seconds=retry_429_seconds,
            )
        except SportradarTransportError as exc:
            print(
                "SPORTRADAR_HISTORY_SEASON_UNAVAILABLE "
                f"competition_id={competition.competition_id} "
                f"season_id={prior.season_id} kind={exc.kind} writes=0",
                flush=True,
            )
            continue

        coverage = benchmark_challenger_a2_stats(combined)
        sampled += 1
        total["matches"] += coverage.challenger_total
        total["closed"] += coverage.closed_total
        total["with_statistics"] += coverage.summaries_with_statistics
        total["a2_core_ready"] += coverage.a2_core_ready
        total["serve_detail_ready"] += coverage.serve_detail_ready
        total["closed_with_statistics"] += coverage.closed_with_statistics
        total["closed_a2_core_ready"] += coverage.closed_a2_core_ready
        print(
            "SPORTRADAR_HISTORY_COMPETITION "
            f"competition_id={competition.competition_id} name={competition.name!r} "
            f"season_id={prior.season_id} season={prior.name!r} year={prior.year or '-'} "
            f"pages={pages} matches={coverage.challenger_total} closed={coverage.closed_total} "
            f"with_statistics={coverage.summaries_with_statistics} "
            f"a2_core_ready={coverage.a2_core_ready} "
            f"serve_detail_ready={coverage.serve_detail_ready} "
            f"closed_a2_core_ready={coverage.closed_a2_core_ready} writes=0",
            flush=True,
        )

    closed = total["closed"]
    closed_core = total["closed_a2_core_ready"]
    closed_rate = 0.0 if closed == 0 else closed_core / closed
    print(
        "SPORTRADAR_HISTORY_SUMMARY "
        f"competitions_sampled={sampled} matches={total['matches']} "
        f"closed={closed} with_statistics={total['with_statistics']} "
        f"a2_core_ready={total['a2_core_ready']} "
        f"serve_detail_ready={total['serve_detail_ready']} "
        f"closed_with_statistics={total['closed_with_statistics']} "
        f"closed_a2_core_ready={closed_core} closed_a2_core_rate={closed_rate:.4f} "
        "writes=0",
        flush=True,
    )
    return 0


def _utc_date() -> date:
    return datetime.now(timezone.utc).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded read-only Sportradar recent historical depth benchmark"
    )
    parser.add_argument("--date", type=date.fromisoformat, default=_utc_date())
    parser.add_argument("--max-competitions", type=int, default=DEFAULT_COMPETITIONS)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_PAGES)
    args = parser.parse_args(argv)

    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        print("SPORTRADAR_HISTORY_BLOCKED reason=NO_API_KEY writes=0", flush=True)
        return 2

    access_level = os.environ.get("SPORTRADAR_TENNIS_ACCESS_LEVEL", "trial").strip() or "trial"
    language = os.environ.get("SPORTRADAR_TENNIS_LANGUAGE", "en").strip() or "en"

    try:
        return run_probe(
            api_key=api_key,
            target_date=args.date,
            access_level=access_level,
            language=language,
            max_competitions=args.max_competitions,
            max_pages=args.max_pages,
        )
    except ValueError as exc:
        print(
            f"SPORTRADAR_HISTORY_BLOCKED reason={type(exc).__name__} writes=0",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
