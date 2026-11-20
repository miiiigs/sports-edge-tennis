"""Isolated, read-only historical provider re-observation of old pending picks.

Uses canonical API-Tennis event keys for exact identity matching. At most four
31-day window calls, no retries, no results, voids, odds, or database writes.
Source status, winner identity and fixture key are printed as compact audit
evidence; event disappearance is NOT proof of cancellation.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.live.credentials import require_api_tennis_key
from sports_edge_tennis.live.history_refresh import fetch_api_tennis_fixture_window
from sports_edge_tennis.live.pending_result_diagnostics import (
    FLAG_OUTSIDE_RECOVERY_WINDOW,
    load_pending_result_diagnostics,
)
from sports_edge_tennis.live.transport import TransportError

MAX_CALLS = 4
WINDOW_DAYS = 31
_STATUS = {
    "finished":"COMPLETED", "completed":"COMPLETED",
    "retired":"RETIRED", "walkover":"WALKOVER",
    "walk over":"WALKOVER", "cancelled":"CANCELLED",
    "canceled":"CANCELLED", "postponed":"POSTPONED",
    "not started":"SCHEDULED", "scheduled":"SCHEDULED",
    "upcoming":"SCHEDULED",
}
_TERMINAL = {"COMPLETED","RETIRED","WALKOVER","CANCELLED"}


def _status(raw: Any) -> str:
    token = " ".join(str(raw or "").casefold().split())
    return _STATUS.get(token, "UNRECOGNIZED" if token else "BLANK")


def _winner(raw: Any) -> int | None:
    token = " ".join(str(raw or "").casefold().split())
    return 1 if token in ("first player","first","player 1","player1","1") else (
        2 if token in ("second player","second","player 2","player2","2") else None
    )


def _windows(dates: set[date]) -> list[tuple[date,date]]:
    remaining = sorted(dates)
    windows = []
    while remaining:
        start = remaining[0]
        end = min(start + timedelta(days=WINDOW_DAYS - 1),
                  remaining[-1] + timedelta(days=5))
        windows.append((start, end))
        remaining = [d for d in remaining if d > end]
        if len(windows) > MAX_CALLS:
            raise ValueError("historical pending provider dates need too many calls")
    return windows


def _assessment(*, missing_source: bool, provider_rows: list[dict[str,Any]] | None) -> str:
    if missing_source:
        return "NO_CANONICAL_API_TENNIS_LINK"
    if provider_rows is None:
        return "NO_VERIFIED_PROVIDER_WINDOW"
    if not provider_rows:
        return "EVENT_NOT_RETURNED_DO_NOT_AUTO_VOID"
    if len(provider_rows) != 1:
        return "MULTIPLE_PROVIDER_ROWS_MANUAL_IDENTITY_REVIEW"
    row = provider_rows[0]
    status = _status(row.get("event_status"))
    winner = _winner(row.get("event_winner"))
    if status in _TERMINAL:
        if status in ("COMPLETED","RETIRED") and winner in (1,2):
            return "TERMINAL_WITH_WINNER_CANONICAL_REVIEW"
        if status == "WALKOVER":
            return "TERMINAL_WALKOVER_CONFIRM_VOID_POLICY"
        if status == "CANCELLED":
            return "TERMINAL_CANCELLED_CONFIRM_VOID_POLICY"
        return "TERMINAL_WINNER_MISSING_MANUAL_REVIEW"
    if status in ("SCHEDULED","POSTPONED"):
        return "PROVIDER_STILL_NONTERMINAL"
    return "UNRECOGNIZED_PROVIDER_STATUS_MANUAL_REVIEW"


def _emit(event: str, **fields: object) -> None:
    print(event + " " + json.dumps(fields, sort_keys=True, separators=(",", ":")), flush=True)


def main() -> int:
    now = datetime.now(UTC)
    try:
        with psycopg.connect(os.environ["TENNIS_DATABASE_URL"],
                            autocommit=True, connect_timeout=12) as conn:
            conn.execute("SET default_transaction_read_only=on")
            report = load_pending_result_diagnostics(
                conn, lookback_days=400, limit=2000, now=now,
            )
    except (KeyError, psycopg.Error, ValueError):
        _emit("AGED_PENDING_BLOCKED", reason="DB_UNAVAILABLE")
        return 0
    old = [item for item in report.items
           if FLAG_OUTSIDE_RECOVERY_WINDOW in item.flags
           and item.evidence.latest_match_start_at is not None]
    days = {item.evidence.latest_match_start_at.date() for item in old
            if item.evidence.api_tennis_event_key}
    try:
        chunks = _windows(days)
    except ValueError:
        _emit("AGED_PENDING_BLOCKED", reason="REQUEST_CAP_EXCEEDED",
              oldest_cases=len(old), source_dates=len(days))
        return 0
    if len(chunks) > MAX_CALLS:
        raise AssertionError("request guard violated")
    _emit("AGED_PENDING_BEGIN", oldest_cases=len(old),
          dates=len(days), requests_planned=len(chunks), max_calls=MAX_CALLS,
          snapshot=now.isoformat(), database_read_only=True, no_result_writes=True)
    try:
        key = require_api_tennis_key()
    except Exception:
        _emit("AGED_PENDING_BLOCKED", reason="API_TENNIS_KEY_UNAVAILABLE")
        return 0
    observed: dict[str, list[dict[str,Any]]] = {}
    statuses = []
    for low,high in chunks:
        try:
            received, payload = fetch_api_tennis_fixture_window(
                key, low, high, timeout=45,
            )
            for row in payload:
                if isinstance(row, dict) and row.get("event_key") is not None:
                    observed.setdefault(str(row["event_key"]), []).append(row)
            statuses.append((low, high, "SUCCESS"))
            _emit("AGED_PENDING_WINDOW", start=low.isoformat(),
                  end=high.isoformat(), status="SUCCESS",
                  raw_provider_rows=len(payload), observed_at=received.isoformat())
        except TransportError as ex:
            statuses.append((low,high,"FAILED"))
            _emit("AGED_PENDING_WINDOW", start=low.isoformat(),
                  end=high.isoformat(), status="FAILED", category=ex.kind)
    totals: Counter[str] = Counter()
    for item in old:
        ev = item.evidence
        linked = bool(ev.api_tennis_event_key)
        date_of = ev.latest_match_start_at.date()
        coverage = any(low<=date_of<=high and state=="SUCCESS" for low,high,state in statuses)
        rows = observed.get(ev.api_tennis_event_key,[]) if coverage else None
        verdict = _assessment(missing_source=not linked, provider_rows=rows)
        totals[verdict]+=1
        source_status = _status(rows[0].get("event_status")) if rows and len(rows)==1 else None
        source_winner = _winner(rows[0].get("event_winner")) if rows and len(rows)==1 else None
        _emit("AGED_PENDING_ITEM",
              match_id=ev.match_id, source_event_key=ev.api_tennis_event_key,
              last_canonical_status=ev.latest_match_status,
              reason=item.reason, flags=list(item.flags),
              source_status=source_status, source_winner_side=source_winner,
              verdict=verdict, provider_row_count=len(rows) if rows is not None else None)
    _emit("AGED_PENDING_DONE", cases=len(old), requests_made=len(chunks),
          complete_windows=sum(s=="SUCCESS" for _,_,s in statuses),
          verdicts=dict(sorted(totals.items())),
          no_auto_voids=True, db_writes=0, odds_requests=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
