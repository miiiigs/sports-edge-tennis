"""Read-only API-Tennis H2H coverage probe for Agent 3 abstentions.

The probe asks a bounded number of provider questions and never persists returned
H2H/recent-form rows. Its purpose is to measure whether API-Tennis can add enough
direct or shared-opponent evidence to justify a later dedupe-aware ingestion lane.

Safety contract:
* only production-live, completed prediction runs are considered;
* experiment predictions are excluded;
* only future matches with a known current surface are probed, which avoids the
  separate UNKNOWN_SURFACE abstention class;
* provider results are filtered to the ATP/Challenger singles scope and Agent 3's
  four-year lookback before support counts are reported;
* provider-call failures are observational and never fail the validation pipeline;
* no prediction, Manager, Market, Veto, staking, settlement, or canonical history
  state is written or changed.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

import psycopg

from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.transport import TransportError, fetch_api_tennis_h2h


AGENT_CODE = "AGENT_3_INTRANSITIVITY"
DEFAULT_MAX_PAIRS = 10
MAX_PAIRS = 25
DEFAULT_HORIZON_DAYS = 3
MAX_HORIZON_DAYS = 7
LOOKBACK_DAYS = 1460

_ATP_MAIN_TYPE_TOKENS = {
    "atp singles",
    "atp main singles",
    "atp men singles",
    "atp mens singles",
    "atp men's singles",
}
_CHALLENGER_TYPE_TOKENS = {
    "atp challenger singles",
    "atp challenger men singles",
    "atp challenger mens singles",
    "atp challenger men's singles",
    "challenger singles",
    "challenger men singles",
    "challenger mens singles",
    "challenger men's singles",
}
_TERMINAL_STATUS_TOKENS = {"finished", "completed", "retired", "walkover"}


@dataclass(frozen=True)
class A3H2HProbeTarget:
    match_id: int
    first_player_key: str
    second_player_key: str
    match_start_at: datetime
    surface: str


@dataclass(frozen=True)
class A3H2HProbeEvidence:
    raw_direct_rows: int
    raw_first_player_rows: int
    raw_second_player_rows: int
    eligible_direct_rows: int
    eligible_first_player_rows: int
    eligible_second_player_rows: int
    shared_opponent_count: int

    @property
    def raw_direct_support_met(self) -> bool:
        return self.eligible_direct_rows >= 2

    @property
    def raw_shared_support_met(self) -> bool:
        return self.shared_opponent_count >= 5


FetchH2H = Callable[[str, str, str], tuple[datetime, dict[str, Any]]]


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _in_scope_singles(row: Mapping[str, Any]) -> bool:
    token = _normalized(row.get("event_type_type"))
    return token in _ATP_MAIN_TYPE_TOKENS or token in _CHALLENGER_TYPE_TOKENS


def _event_date(row: Mapping[str, Any]) -> date | None:
    raw = _text(row.get("event_date"))
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _eligible_result(row: Mapping[str, Any], *, observed_at: datetime) -> bool:
    if not _in_scope_singles(row):
        return False
    if _normalized(row.get("event_status")) not in _TERMINAL_STATUS_TOKENS:
        return False
    if _normalized(row.get("event_winner")) not in {
        "first player",
        "first",
        "player 1",
        "player1",
        "1",
        "second player",
        "second",
        "player 2",
        "player2",
        "2",
    }:
        return False
    played_on = _event_date(row)
    if played_on is None:
        return False
    observed_date = observed_at.astimezone(UTC).date()
    return observed_date - timedelta(days=LOOKBACK_DAYS) <= played_on <= observed_date


def _rows(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _dedupe(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    deduped: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        event_key = _text(row.get("event_key")) or f"ROW:{index}"
        if event_key in seen:
            continue
        seen.add(event_key)
        deduped.append(row)
    return deduped


def _opponent_key(row: Mapping[str, Any], player_key: str) -> str | None:
    first = _text(row.get("first_player_key"))
    second = _text(row.get("second_player_key"))
    player = _text(player_key)
    if not player:
        return None
    if first == player and second and second != player:
        return second
    if second == player and first and first != player:
        return first
    return None


def summarize_h2h_payload(
    payload: Mapping[str, Any],
    *,
    first_player_key: str,
    second_player_key: str,
    observed_at: datetime,
) -> A3H2HProbeEvidence:
    """Measure potential raw Agent 3 support without persisting provider rows."""
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")

    direct_raw = _dedupe(_rows(payload, "H2H"))
    first_raw = _dedupe(_rows(payload, "firstPlayerResults"))
    second_raw = _dedupe(_rows(payload, "secondPlayerResults"))

    direct = [row for row in direct_raw if _eligible_result(row, observed_at=observed_at)]
    first = [row for row in first_raw if _eligible_result(row, observed_at=observed_at)]
    second = [row for row in second_raw if _eligible_result(row, observed_at=observed_at)]

    first_opponents = {
        opponent
        for row in first
        if (opponent := _opponent_key(row, first_player_key)) is not None
        and opponent != _text(second_player_key)
    }
    second_opponents = {
        opponent
        for row in second
        if (opponent := _opponent_key(row, second_player_key)) is not None
        and opponent != _text(first_player_key)
    }

    return A3H2HProbeEvidence(
        raw_direct_rows=len(direct_raw),
        raw_first_player_rows=len(first_raw),
        raw_second_player_rows=len(second_raw),
        eligible_direct_rows=len(direct),
        eligible_first_player_rows=len(first),
        eligible_second_player_rows=len(second),
        shared_opponent_count=len(first_opponents.intersection(second_opponents)),
    )


def load_probe_targets(
    conn: psycopg.Connection,
    *,
    as_of: datetime,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    limit: int = DEFAULT_MAX_PAIRS,
) -> tuple[A3H2HProbeTarget, ...]:
    """Load bounded upcoming known-surface A3 abstentions from the latest live run."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if not 1 <= horizon_days <= MAX_HORIZON_DAYS:
        raise ValueError(f"horizon_days must be between 1 and {MAX_HORIZON_DAYS}")
    if not 1 <= limit <= MAX_PAIRS:
        raise ValueError(f"limit must be between 1 and {MAX_PAIRS}")

    horizon_end = as_of + timedelta(days=horizon_days)
    rows = conn.execute(
        """
        WITH latest_run AS (
            SELECT prediction_run_id
            FROM prediction_runs
            WHERE prediction_contract_version = 'predictive_agent_result_v1'
              AND run_status = 'COMPLETED'
              AND prediction_finalized_at IS NOT NULL
              AND experiment_id IS NULL
            ORDER BY prediction_finalized_at DESC, prediction_run_id DESC
            LIMIT 1
        )
        SELECT
            p.match_id,
            p1.source_player_id,
            p2.source_player_id,
            mv.match_start_at,
            mv.surface::text
        FROM predictions p
        JOIN latest_run lr
          ON lr.prediction_run_id = p.prediction_run_id
        JOIN LATERAL (
            SELECT player_1_id, player_2_id, match_start_at, surface
            FROM match_versions
            WHERE match_id = p.match_id
              AND as_of <= p.as_of
              AND source_observed_at <= p.as_of
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN player_source_ids p1
          ON p1.player_id = mv.player_1_id
         AND p1.source_system = 'API_TENNIS'
        JOIN player_source_ids p2
          ON p2.player_id = mv.player_2_id
         AND p2.source_system = 'API_TENNIS'
        WHERE p.agent_code = %s
          AND p.prediction_status = 'ABSTAINED'
          AND p.experiment_id IS NULL
          AND mv.surface IS NOT NULL
          AND mv.match_start_at >= %s
          AND mv.match_start_at < %s
        ORDER BY mv.match_start_at, p.match_id
        LIMIT %s
        """,
        (AGENT_CODE, as_of, horizon_end, limit),
    ).fetchall()
    return tuple(
        A3H2HProbeTarget(
            match_id=int(row[0]),
            first_player_key=str(row[1]),
            second_player_key=str(row[2]),
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
    fetch_h2h: FetchH2H = fetch_api_tennis_h2h,
) -> int:
    targets = load_probe_targets(
        conn,
        as_of=as_of,
        horizon_days=horizon_days,
        limit=max_pairs,
    )
    print(
        "A3_H2H_PROBE_START "
        f"targets={len(targets)} max_pairs={max_pairs} horizon_days={horizon_days}",
        flush=True,
    )

    successes = 0
    failures = 0
    direct_threshold = 0
    shared_threshold = 0
    for target in targets:
        try:
            observed_at, payload = fetch_h2h(
                api_key,
                target.first_player_key,
                target.second_player_key,
            )
        except TransportError as exc:
            failures += 1
            print(
                "A3_H2H_PROBE_UNAVAILABLE "
                f"match_id={target.match_id} kind={exc.kind}",
                flush=True,
            )
            continue

        evidence = summarize_h2h_payload(
            payload,
            first_player_key=target.first_player_key,
            second_player_key=target.second_player_key,
            observed_at=observed_at,
        )
        successes += 1
        direct_threshold += int(evidence.raw_direct_support_met)
        shared_threshold += int(evidence.raw_shared_support_met)
        print(
            "A3_H2H_PROBE "
            f"match_id={target.match_id} surface={target.surface} "
            f"direct={evidence.eligible_direct_rows}/{evidence.raw_direct_rows} "
            f"first_recent={evidence.eligible_first_player_rows}/{evidence.raw_first_player_rows} "
            f"second_recent={evidence.eligible_second_player_rows}/{evidence.raw_second_player_rows} "
            f"shared_opponents={evidence.shared_opponent_count} "
            f"direct_raw_threshold_met={int(evidence.raw_direct_support_met)} "
            f"shared_raw_threshold_met={int(evidence.raw_shared_support_met)}",
            flush=True,
        )

    print(
        "A3_H2H_PROBE_SUMMARY "
        f"targets={len(targets)} successes={successes} failures={failures} "
        f"direct_raw_threshold_met={direct_threshold} "
        f"shared_raw_threshold_met={shared_threshold} "
        "writes=0",
        flush=True,
    )
    # Provider coverage failures are evidence, not a validation-pipeline failure.
    # Invalid configuration/database access is handled separately by main().
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Agent 3 API-Tennis H2H coverage probe")
    parser.add_argument("--dsn", default=_db_dsn())
    parser.add_argument("--max-pairs", type=int, default=DEFAULT_MAX_PAIRS)
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    args = parser.parse_args(argv)

    if not args.dsn:
        print("A3_H2H_PROBE_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    if not 1 <= args.max_pairs <= MAX_PAIRS:
        print(f"A3_H2H_PROBE_BLOCKED reason=MAX_PAIRS_RANGE_1_{MAX_PAIRS}", flush=True)
        return 2
    if not 1 <= args.horizon_days <= MAX_HORIZON_DAYS:
        print(
            f"A3_H2H_PROBE_BLOCKED reason=HORIZON_DAYS_RANGE_1_{MAX_HORIZON_DAYS}",
            flush=True,
        )
        return 2

    try:
        api_key = load_secret("API_TENNIS_API_KEY")
    except MissingCredentialError:
        api_key = None
    if not api_key:
        print("A3_H2H_PROBE_BLOCKED reason=NO_API_TENNIS_KEY", flush=True)
        return 2

    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            return run_probe(
                conn,
                api_key=api_key,
                as_of=datetime.now(UTC),
                max_pairs=args.max_pairs,
                horizon_days=args.horizon_days,
            )
    except (ValueError, psycopg.Error) as exc:
        print(f"A3_H2H_PROBE_BLOCKED reason={type(exc).__name__}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
