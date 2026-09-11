"""Bounded, read-only production diagnostic for Pinnacle reconciliation.

This intentionally avoids the Agent/Manager pipeline. It reuses an already
persisted prediction run, fetches the Pinnacle board once, evaluates current
reconciliation behavior inside a transaction that is always rolled back, and
prints one best-name candidate per model match. No secrets or raw payloads are
logged.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.transport import fetch_pinnapi_board
from sports_edge_tennis.odds.providers.pinnapi import (
    new_pinnapi_board_observation_id,
    parse_pinnapi_board,
    to_pinnapi_event_evidence,
)
from sports_edge_tennis.odds.reconciliation import (
    _load_candidates,
    normalize_pinnapi_participant_name,
    reconcile_pinnapi_event,
)


def _log(message: str) -> None:
    print(message, flush=True)


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _latest_prediction_run_id(conn: psycopg.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT prediction_run_id
        FROM manager_consensus_results
        GROUP BY prediction_run_id
        ORDER BY prediction_run_id DESC
        LIMIT 1
        """
    ).fetchone()
    return None if row is None else int(row[0])


def _candidate_ids(conn: psycopg.Connection, prediction_run_id: int) -> tuple[int, ...]:
    rows = conn.execute(
        """
        SELECT DISTINCT match_id
        FROM manager_consensus_results
        WHERE prediction_run_id = %s
        ORDER BY match_id
        """,
        (prediction_run_id,),
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _pair_similarity(p1: str, p2: str, home: str, away: str) -> float:
    direct = (_similarity(p1, home) + _similarity(p2, away)) / 2.0
    reverse = (_similarity(p1, away) + _similarity(p2, home)) / 2.0
    return max(direct, reverse)


def _safe_text(value: str | None) -> str:
    if not value:
        return "-"
    return " ".join(value.replace("\n", " ").replace("\r", " ").split())[:120]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Pinnacle reconciliation diagnostic")
    parser.add_argument("--prediction-run-id", type=int)
    args = parser.parse_args(argv)

    dsn = _dsn()
    key = load_secret("PINNAPI_KEY")
    if not dsn:
        _log("SHARP_DIAG_BLOCKED no_database")
        return 2
    if not key:
        _log("SHARP_DIAG_BLOCKED no_pinnapi_key")
        return 2

    with psycopg.connect(dsn, autocommit=False) as conn:
        prediction_run_id = args.prediction_run_id or _latest_prediction_run_id(conn)
        if prediction_run_id is None:
            _log("SHARP_DIAG_BLOCKED no_prediction_run")
            conn.rollback()
            return 2
        match_ids = _candidate_ids(conn, prediction_run_id)
        if not match_ids:
            _log(f"SHARP_DIAG_BLOCKED prediction_run_id={prediction_run_id} no_candidates")
            conn.rollback()
            return 2

        as_of = datetime.now(timezone.utc)
        candidates = _load_candidates(conn, candidate_match_ids=match_ids, as_of=as_of)
        received_at, payload = fetch_pinnapi_board(key)
        parsed = parse_pinnapi_board(
            payload,
            received_at=received_at,
            board_observation_id=new_pinnapi_board_observation_id(),
        )
        events = tuple(parsed.events)
        _log(
            "SHARP_DIAG_START "
            f"prediction_run_id={prediction_run_id} candidates={len(candidates)} "
            f"board_events={len(events)} parse_rejected={len(parsed.rejected_events)}"
        )

        reason_counts: Counter[str] = Counter()
        resolved_match_ids: set[int] = set()
        for event in events:
            evidence = to_pinnapi_event_evidence(event)
            result = reconcile_pinnapi_event(
                conn,
                evidence=evidence,
                candidate_match_ids=match_ids,
                as_of=as_of,
            )
            reason_counts[result.reason or result.status] += 1
            if result.match is not None:
                resolved_match_ids.add(result.match.match_id)

        for match_id in match_ids:
            candidate = candidates.get(match_id)
            if candidate is None:
                _log(f"SHARP_DIAG_CANDIDATE match_id={match_id} no_pit_candidate")
                continue
            p1 = normalize_pinnapi_participant_name(candidate.player_1_name)
            p2 = normalize_pinnapi_participant_name(candidate.player_2_name)
            best = None
            best_score = -1.0
            for event in events:
                evidence = to_pinnapi_event_evidence(event)
                home = normalize_pinnapi_participant_name(evidence.home_name)
                away = normalize_pinnapi_participant_name(evidence.away_name)
                score = _pair_similarity(p1, p2, home, away)
                if score > best_score:
                    best_score = score
                    best = evidence
            if best is None:
                _log(
                    f"SHARP_DIAG_CANDIDATE match_id={match_id} "
                    f"p1={_safe_text(candidate.player_1_name)!r} "
                    f"p2={_safe_text(candidate.player_2_name)!r} no_board_event"
                )
                continue
            start_delta_min = None
            if candidate.match.match_start_at is not None:
                start_delta_min = int(
                    abs(
                        candidate.match.match_start_at.astimezone(timezone.utc)
                        - best.scheduled_start_utc
                    ).total_seconds()
                    // 60
                )
            _log(
                "SHARP_DIAG_CANDIDATE "
                f"match_id={match_id} resolved={match_id in resolved_match_ids} "
                f"p1={_safe_text(candidate.player_1_name)!r} "
                f"p2={_safe_text(candidate.player_2_name)!r} "
                f"best_event={best.provider_event_id_text} "
                f"home={_safe_text(best.home_name)!r} away={_safe_text(best.away_name)!r} "
                f"similarity={best_score:.3f} start_delta_min={start_delta_min} "
                f"league={_safe_text(best.league_name)!r}"
            )

        reason_text = " ".join(
            f"{reason}={count}" for reason, count in sorted(reason_counts.items())
        )
        _log(
            "SHARP_DIAG_REASONS "
            f"prediction_run_id={prediction_run_id} {reason_text or 'none'}"
        )
        _log(
            "SHARP_DIAG_DONE "
            f"prediction_run_id={prediction_run_id} resolved_matches={len(resolved_match_ids)}"
        )
        conn.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
