"""Read-only Sportradar coverage comparator for Agent 3 raw evidence.

The target cohort intentionally mirrors the existing API-Tennis A3 comparator:
latest completed non-experiment A3 abstentions, known surface, both players with
existing API-Tennis identities, and match start inside the same forward horizon.

Sportradar is used only to answer the provider-coverage question for those exact
canonical matches. No returned provider data is persisted.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar import (
    SportradarTransportError,
    fetch_competitor_summaries,
    fetch_competitor_vs_competitor,
    fetch_daily_summaries,
)
from sports_edge_tennis.live.sportradar_benchmark import deterministic_name_keys
from sports_edge_tennis.live.sportradar_probe import (
    RequestPacer,
    TRIAL_429_RETRY_SECONDS,
    TRIAL_MIN_REQUEST_INTERVAL_SECONDS,
    _paced_fetch,
)


AGENT_CODE = "AGENT_3_INTRANSITIVITY"
DIRECT_SUPPORT_THRESHOLD = 2
SHARED_SUPPORT_THRESHOLD = 5
DEFAULT_MAX_PAIRS = 10
MAX_PAIRS = 10
DEFAULT_HORIZON_DAYS = 3
MAX_HORIZON_DAYS = 7
MAX_START_DELTA_MINUTES = 180


@dataclass(frozen=True)
class A3SportradarTarget:
    match_id: int
    first_name: str
    second_name: str
    match_start_at: datetime
    surface: str


@dataclass(frozen=True)
class LinkedPair:
    event_id: str
    first_id: str
    second_id: str
    start_delta_minutes: int


@dataclass(frozen=True)
class A3SportradarEvidence:
    direct_matches: int
    first_recent_matches: int
    second_recent_matches: int
    shared_opponents: int

    @property
    def direct_support_met(self) -> bool:
        return self.direct_matches >= DIRECT_SUPPORT_THRESHOLD

    @property
    def shared_support_met(self) -> bool:
        return self.shared_opponents >= SHARED_SUPPORT_THRESHOLD


def _list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: Any) -> datetime | None:
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
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _competition(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return {}
    context = event.get("sport_event_context")
    if not isinstance(context, Mapping):
        return {}
    competition = context.get("competition")
    return competition if isinstance(competition, Mapping) else {}


def _in_scope_singles(summary: Mapping[str, Any]) -> bool:
    competition = _competition(summary)
    if _text(competition.get("type")).casefold() != "singles":
        return False

    level = _text(competition.get("level")).casefold()
    name = _text(competition.get("name")).casefold()
    event = summary.get("sport_event")
    category_name = ""
    if isinstance(event, Mapping):
        context = event.get("sport_event_context")
        if isinstance(context, Mapping):
            category = context.get("category")
            if isinstance(category, Mapping):
                category_name = _text(category.get("name")).casefold()

    return (
        "challenger" in level
        or "challenger" in name
        or category_name == "atp"
        or level.startswith("atp_")
        or "atp " in name
    )


def _is_completed(summary: Mapping[str, Any]) -> bool:
    status = summary.get("sport_event_status")
    if not isinstance(status, Mapping):
        return False
    return _text(status.get("status")).casefold() in {"closed", "ended"}


def _event_competitors(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return []
    return _list(event.get("competitors"))


def _name_match(left: str, right: str) -> bool:
    return bool(deterministic_name_keys(left) & deterministic_name_keys(right))


def link_target(
    target: A3SportradarTarget,
    daily_payload: Mapping[str, Any],
) -> tuple[str, tuple[LinkedPair, ...]]:
    candidates: list[LinkedPair] = []
    for summary in _list(daily_payload.get("summaries")):
        if not _in_scope_singles(summary):
            continue
        competitors = _event_competitors(summary)
        if len(competitors) != 2:
            continue

        first, second = competitors
        direct = _name_match(target.first_name, _text(first.get("name"))) and _name_match(
            target.second_name, _text(second.get("name"))
        )
        reverse = _name_match(target.first_name, _text(second.get("name"))) and _name_match(
            target.second_name, _text(first.get("name"))
        )
        if direct == reverse:
            continue

        event = summary.get("sport_event")
        if not isinstance(event, Mapping):
            continue
        event_id = _text(event.get("id"))
        start_at = _parse_time(event.get("start_time"))
        if not event_id or start_at is None:
            continue

        delta = int(
            abs(
                (
                    target.match_start_at.astimezone(UTC) - start_at
                ).total_seconds()
            )
            // 60
        )
        p1 = first if direct else second
        p2 = second if direct else first
        first_id = _text(p1.get("id"))
        second_id = _text(p2.get("id"))
        if not first_id or not second_id:
            continue
        candidates.append(LinkedPair(event_id, first_id, second_id, delta))

    in_window = tuple(
        candidate
        for candidate in candidates
        if candidate.start_delta_minutes <= MAX_START_DELTA_MINUTES
    )
    if len(in_window) == 1:
        return "EXACT", in_window
    if len(in_window) > 1:
        return "AMBIGUOUS", in_window
    if candidates:
        return "TIME_MISMATCH", tuple(candidates)
    return "UNRESOLVED", ()


def _eligible_summaries(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in _list(payload.get("summaries"))
        if _in_scope_singles(row) and _is_completed(row)
    ]


def _opponent_ids(payload: Mapping[str, Any], focal_id: str) -> set[str]:
    opponents: set[str] = set()
    for summary in _eligible_summaries(payload):
        competitors = _event_competitors(summary)
        ids = [_text(c.get("id")) for c in competitors]
        if focal_id not in ids:
            continue
        for competitor_id in ids:
            if competitor_id and competitor_id != focal_id:
                opponents.add(competitor_id)
    return opponents


def summarize_evidence(
    h2h_payload: Mapping[str, Any],
    first_payload: Mapping[str, Any],
    second_payload: Mapping[str, Any],
    *,
    first_id: str,
    second_id: str,
) -> A3SportradarEvidence:
    direct = _eligible_summaries(h2h_payload)
    first_recent = _eligible_summaries(first_payload)
    second_recent = _eligible_summaries(second_payload)
    first_opponents = _opponent_ids(first_payload, first_id) - {second_id}
    second_opponents = _opponent_ids(second_payload, second_id) - {first_id}

    return A3SportradarEvidence(
        direct_matches=len(direct),
        first_recent_matches=len(first_recent),
        second_recent_matches=len(second_recent),
        shared_opponents=len(first_opponents & second_opponents),
    )


def load_targets(
    conn: psycopg.Connection,
    *,
    as_of: datetime,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    limit: int = DEFAULT_MAX_PAIRS,
) -> tuple[A3SportradarTarget, ...]:
    """Mirror the API-Tennis A3 probe target-selection contract exactly."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if not 1 <= horizon_days <= MAX_HORIZON_DAYS:
        raise ValueError(f"horizon_days must be between 1 and {MAX_HORIZON_DAYS}")
    if not 1 <= limit <= MAX_PAIRS:
        raise ValueError(f"limit must be between 1 and {MAX_PAIRS}")

    horizon_end = as_of + timedelta(days=horizon_days)
    rows = conn.execute(
        """
        WITH latest_a3 AS (
            SELECT DISTINCT ON (p.match_id)
                p.match_id,
                p.as_of,
                p.prediction_status::text AS prediction_status
            FROM predictions p
            JOIN prediction_runs pr
              ON pr.prediction_run_id = p.prediction_run_id
            WHERE pr.prediction_contract_version = 'predictive_agent_result_v1'
              AND pr.run_status = 'COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
              AND pr.experiment_id IS NULL
              AND p.experiment_id IS NULL
              AND p.agent_code = %s
            ORDER BY
                p.match_id,
                pr.prediction_finalized_at DESC,
                p.prediction_id DESC
        )
        SELECT
            latest_a3.match_id,
            cp1.canonical_name,
            cp2.canonical_name,
            mv.match_start_at,
            mv.surface::text
        FROM latest_a3
        JOIN LATERAL (
            SELECT player_1_id, player_2_id, match_start_at, surface
            FROM match_versions
            WHERE match_id = latest_a3.match_id
              AND as_of <= latest_a3.as_of
              AND source_observed_at <= latest_a3.as_of
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN player_source_ids api_p1
          ON api_p1.player_id = mv.player_1_id
         AND api_p1.source_system = 'API_TENNIS'
        JOIN player_source_ids api_p2
          ON api_p2.player_id = mv.player_2_id
         AND api_p2.source_system = 'API_TENNIS'
        JOIN players cp1 ON cp1.player_id = mv.player_1_id
        JOIN players cp2 ON cp2.player_id = mv.player_2_id
        WHERE latest_a3.prediction_status = 'ABSTAINED'
          AND mv.surface IS NOT NULL
          AND mv.match_start_at >= %s
          AND mv.match_start_at < %s
        ORDER BY mv.match_start_at, latest_a3.match_id
        LIMIT %s
        """,
        (AGENT_CODE, as_of, horizon_end, limit),
    ).fetchall()

    return tuple(
        A3SportradarTarget(
            match_id=int(row[0]),
            first_name=str(row[1]),
            second_name=str(row[2]),
            match_start_at=row[3],
            surface=str(row[4]),
        )
        for row in rows
    )


def run_probe(
    conn: psycopg.Connection,
    *,
    api_key: str,
    as_of: datetime,
    max_pairs: int = DEFAULT_MAX_PAIRS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    access_level: str,
    language: str,
) -> int:
    targets = load_targets(
        conn,
        as_of=as_of,
        horizon_days=horizon_days,
        limit=max_pairs,
    )
    min_interval = (
        TRIAL_MIN_REQUEST_INTERVAL_SECONDS if access_level == "trial" else 0.0
    )
    retry_429_seconds = (
        TRIAL_429_RETRY_SECONDS if access_level == "trial" else 0.0
    )
    pacer = RequestPacer(min_interval)
    transport = {"access_level": access_level, "language": language}
    daily_cache: dict[date, dict[str, Any]] = {}

    print(
        "SPORTRADAR_A3_START "
        f"as_of={as_of.isoformat()} targets={len(targets)} "
        f"max_pairs={max_pairs} horizon_days={horizon_days} writes=0",
        flush=True,
    )

    exact = 0
    successes = 0
    direct_met = 0
    shared_met = 0
    unavailable = 0

    for target in targets:
        target_date = target.match_start_at.astimezone(UTC).date()
        daily = daily_cache.get(target_date)
        if daily is None:
            try:
                _, daily = _paced_fetch(
                    endpoint=f"daily_summaries_{target_date.isoformat()}",
                    fetcher=fetch_daily_summaries,
                    args=(api_key, target_date),
                    transport=transport,
                    pacer=pacer,
                    access_level=access_level,
                    retry_429_seconds=retry_429_seconds,
                )
            except SportradarTransportError as exc:
                unavailable += 1
                print(
                    "SPORTRADAR_A3_UNAVAILABLE "
                    f"stage=daily date={target_date.isoformat()} kind={exc.kind} writes=0",
                    flush=True,
                )
                continue
            daily_cache[target_date] = daily

        link_status, candidates = link_target(target, daily)
        if link_status != "EXACT":
            print(
                "SPORTRADAR_A3_LINK "
                f"match_id={target.match_id} status={link_status} "
                f"candidate_count={len(candidates)} writes=0",
                flush=True,
            )
            continue

        exact += 1
        pair = candidates[0]
        try:
            _, h2h = _paced_fetch(
                endpoint="competitor_vs_competitor",
                fetcher=fetch_competitor_vs_competitor,
                args=(api_key, pair.first_id, pair.second_id),
                transport=transport,
                pacer=pacer,
                access_level=access_level,
                retry_429_seconds=retry_429_seconds,
            )
            _, first = _paced_fetch(
                endpoint="competitor_summaries_first",
                fetcher=fetch_competitor_summaries,
                args=(api_key, pair.first_id),
                transport=transport,
                pacer=pacer,
                access_level=access_level,
                retry_429_seconds=retry_429_seconds,
            )
            _, second = _paced_fetch(
                endpoint="competitor_summaries_second",
                fetcher=fetch_competitor_summaries,
                args=(api_key, pair.second_id),
                transport=transport,
                pacer=pacer,
                access_level=access_level,
                retry_429_seconds=retry_429_seconds,
            )
        except SportradarTransportError as exc:
            unavailable += 1
            print(
                "SPORTRADAR_A3_UNAVAILABLE "
                f"match_id={target.match_id} kind={exc.kind} writes=0",
                flush=True,
            )
            continue

        evidence = summarize_evidence(
            h2h,
            first,
            second,
            first_id=pair.first_id,
            second_id=pair.second_id,
        )
        successes += 1
        direct_met += int(evidence.direct_support_met)
        shared_met += int(evidence.shared_support_met)
        print(
            "SPORTRADAR_A3_PAIR "
            f"match_id={target.match_id} surface={target.surface} "
            f"direct={evidence.direct_matches} "
            f"first_recent={evidence.first_recent_matches} "
            f"second_recent={evidence.second_recent_matches} "
            f"shared_opponents={evidence.shared_opponents} "
            f"direct_raw_threshold_met={int(evidence.direct_support_met)} "
            f"shared_raw_threshold_met={int(evidence.shared_support_met)} writes=0",
            flush=True,
        )

    print(
        "SPORTRADAR_A3_SUMMARY "
        f"targets={len(targets)} exact_links={exact} successes={successes} "
        f"unavailable={unavailable} daily_dates={len(daily_cache)} "
        f"direct_raw_threshold_met={direct_met} "
        f"shared_raw_threshold_met={shared_met} writes=0",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Sportradar Agent 3 evidence comparator"
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--max-pairs", type=int, default=DEFAULT_MAX_PAIRS)
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    args = parser.parse_args(argv)

    if not args.dsn:
        print("SPORTRADAR_A3_BLOCKED reason=NO_DATABASE writes=0", flush=True)
        return 2
    if not 1 <= args.max_pairs <= MAX_PAIRS:
        print(
            f"SPORTRADAR_A3_BLOCKED reason=MAX_PAIRS_RANGE_1_{MAX_PAIRS} writes=0",
            flush=True,
        )
        return 2
    if not 1 <= args.horizon_days <= MAX_HORIZON_DAYS:
        print(
            f"SPORTRADAR_A3_BLOCKED reason=HORIZON_DAYS_RANGE_1_{MAX_HORIZON_DAYS} writes=0",
            flush=True,
        )
        return 2

    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        print("SPORTRADAR_A3_BLOCKED reason=NO_API_KEY writes=0", flush=True)
        return 2

    access_level = (
        os.environ.get("SPORTRADAR_TENNIS_ACCESS_LEVEL", "trial").strip() or "trial"
    )
    language = os.environ.get("SPORTRADAR_TENNIS_LANGUAGE", "en").strip() or "en"

    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            return run_probe(
                conn,
                api_key=api_key,
                as_of=datetime.now(UTC),
                max_pairs=args.max_pairs,
                horizon_days=args.horizon_days,
                access_level=access_level,
                language=language,
            )
    except (ValueError, psycopg.Error) as exc:
        print(
            f"SPORTRADAR_A3_BLOCKED reason={type(exc).__name__} writes=0",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
