"""Bounded, read-only ATP + Challenger provider and stored-history audit.

Intended for a single isolated Railway audit service, NOT the hourly runner.
Seven maximum API-Tennis requests: one tournament entitlement list, two
31-day or shorter fixture windows Nov 16-Jan 4, and four historical spot dates.
No odds calls, retries, DB writes, secret logs, or source payload persistence.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping

import psycopg
import requests

from sports_edge_tennis.data.sources.api_tennis_live import parse_api_tennis_fixtures
from sports_edge_tennis.dashboard.calendar_coverage import load_seasonal_coverage
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    normalize_api_tennis_upcoming_status,
)
from sports_edge_tennis.live.credentials import require_api_tennis_key
from sports_edge_tennis.live.history_refresh import fetch_api_tennis_fixture_window
from sports_edge_tennis.live.transport import (
    API_TENNIS_BASE_URL, TransportError, api_tennis_failure_category,
)

START = date(2026, 11, 16)
END = date(2027, 1, 4)
HISTORICAL_DATES = (
    date(2019, 10, 10), date(2024, 10, 10),
    date(2025, 10, 10), date(2026, 10, 10),
)
MAX_REQUESTS = 7
SPECIAL_MARKERS = (
    "atp finals", "next gen", "davis cup", "united cup",
    "atp cup", "laver cup",
)
ATP_TYPE = {"atp singles", "atp main singles", "atp men singles",
            "atp mens singles", "atp men's singles"}
CH_TYPE = {"atp challenger singles", "atp challenger men singles",
           "atp challenger mens singles", "atp challenger men's singles",
           "challenger singles", "challenger men singles",
           "challenger mens singles", "challenger men's singles"}


def _emit(prefix: str, **fields: object) -> None:
    # No payload, response exception, HTTP URL, API key, DSN or player name.
    print(prefix + " " + json.dumps(fields, sort_keys=True, separators=(",", ":")), flush=True)


def _tour_kind(row: Mapping[str, Any]) -> str | None:
    token = " ".join(str(row.get("event_type_type") or "").casefold().split())
    if token in ATP_TYPE:
        return "ATP_TOUR"
    if token in CH_TYPE:
        return "CHALLENGER"
    return None


def _draw_kind(row: Mapping[str, Any]) -> str:
    raw = row.get("qualifying_flag")
    if raw is None or str(raw).strip() == "":
        raw = row.get("event_qualification")
    if isinstance(raw, bool):
        return "QUALIFYING" if raw else "MAIN_DRAW"
    token = str(raw or "").casefold().strip()
    if token in {"true", "1", "yes", "t", "y"}:
        return "QUALIFYING"
    if token in {"false", "0", "no", "f", "n"}:
        return "MAIN_DRAW"
    round_text = str(row.get("tournament_round") or row.get("round_code") or row.get("round") or "").casefold()
    tail = round_text.rsplit(" - ", 1)[-1].strip()
    if ("qualifying" in tail or tail in {"qual", "q1", "q2", "q3"}):
        return "QUALIFYING"
    if tail in {"final", "semi-finals", "quarter-finals", "1/8-finals",
                "1/16-finals", "1/32-finals", "1/64-finals",
                "f", "sf", "qf", "r16", "r32", "r64", "r128"}:
        return "MAIN_DRAW"
    return "UNKNOWN"


def _special(row: Mapping[str, Any]) -> bool:
    name = str(row.get("tournament_name") or "")
    return any(token in name.casefold() for token in SPECIAL_MARKERS)


def _scan_window(
    rows: list[dict[str, Any]], received_at: datetime, seen: set[str],
    stats: dict[tuple[str, str], Counter[str]],
) -> None:
    for raw in rows:
        key = str(raw.get("event_key") or "").strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        group = _tour_kind(raw)
        if group is None:
            continue
        date_value = str(raw.get("event_date") or "")
        try:
            event_day = date.fromisoformat(date_value)
        except ValueError:
            continue
        if not START <= event_day <= END:
            continue
        monday = event_day - timedelta(days=event_day.weekday())
        week = monday.isoformat()
        stat = stats[(week, group)]
        stat["provider_unique"] += 1
        draw = _draw_kind(raw)
        stat["qualifying" if draw == "QUALIFYING" else "main_draw" if draw == "MAIN_DRAW" else "draw_unknown"] += 1
        if _special(raw):
            stat["special_scope_review"] += 1
        prepared = normalize_api_tennis_upcoming_status([raw], received_at=received_at)
        parsed, errors = parse_api_tennis_fixtures(prepared, received_at=received_at)
        if not parsed:
            stat["adapter_rejected"] += 1
            for item in errors:
                reason = str(item.get("reason") or "unknown").lower()
                if reason in {"unsupported_event_status", "unsupported_event_type",
                              "out_of_scope_event_type", "invalid_event_date"}:
                    stat["reject_" + reason] += 1
            continue
        rec = parsed[0]
        stat["adapter_accepted"] += 1
        if (
            not _special(raw)
            and rec.match_status == "SCHEDULED"
            and rec.match_start_at > received_at
        ):
            stat["scheduled_forward_candidates"] += 1
        if rec.match_status == "COMPLETED":
            stat["provider_completed"] += 1


def _tournament_entitlement(api_key: str) -> dict[str, object]:
    try:
        res = requests.get(API_TENNIS_BASE_URL, params={
            "method": "get_tournaments", "APIkey": api_key,
        }, timeout=30)
        if res.status_code != 200:
            return {"status": "HTTP_ERROR", "http_status": res.status_code}
        payload = res.json()
        if not isinstance(payload, dict) or payload.get("success") != 1:
            return {"status": "PROVIDER_ERROR", "category": api_tennis_failure_category(payload)}
        matches = payload.get("result")
        if not isinstance(matches, list):
            return {"status": "INVALID_RESULT"}
        groups = Counter()
        unique = set()
        for item in matches:
            if not isinstance(item, dict):
                continue
            group = _tour_kind(item)
            if group:
                groups[group] += 1
                unique.add((group, str(item.get("tournament_key") or "")))
        return {"status": "SUCCESS", "provider_entries": len(matches),
                "atp_tournaments": groups["ATP_TOUR"],
                "challenger_tournaments": groups["CHALLENGER"],
                "unique_in_scope_tournaments": len(unique)}
    except (requests.RequestException, ValueError, TypeError):
        return {"status": "TRANSPORT_OR_PARSE_ERROR"}


def _history_sample(api_key: str, day: date) -> dict[str, object]:
    try:
        observed, rows = fetch_api_tennis_fixture_window(
            api_key, day, day, timeout=35,
        )
        atp = 0
        ch = 0
        completed = 0
        for item in rows:
            if not isinstance(item, dict):
                continue
            group = _tour_kind(item)
            if group == "ATP_TOUR":
                atp += 1
            elif group == "CHALLENGER":
                ch += 1
            else:
                continue
            if str(item.get("event_status") or "").casefold() in {"finished", "completed"}:
                completed += 1
        return {"status": "SUCCESS", "atp_raw": atp, "challenger_raw": ch,
                "completed_raw": completed, "raw_all_sports": len(rows),
                "received_at": observed.isoformat()}
    except TransportError as e:
        return {"status": "FAILED", "category": e.kind}
    except (ValueError, TypeError):
        return {"status": "INVALID_RESULT"}


def _stored_history_report() -> dict[str, object]:
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        return {"status": "DB_NOT_CONFIGURED"}
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
            conn.execute("SET default_transaction_read_only = on")
            report = load_seasonal_coverage(
                conn, start_date=START, end_date=END,
            )
        return {"status": "SUCCESS", "history": report["history"],
                "weeks": [
                    {"week": w["week_start"], "persisted": w["fixtures"],
                     "forward_evaluated": w["forward_evaluated"]}
                    for w in report["weeks"]
                ]}
    except (psycopg.Error, ValueError, KeyError):
        return {"status": "DB_READ_FAILED"}


def main() -> int:
    now = datetime.now(UTC)
    _emit("SEASONAL_AUDIT_BEGIN", utc=now.isoformat(), version="V1",
          period_start=START.isoformat(), period_end=END.isoformat(),
          provider_request_cap=MAX_REQUESTS, may_mutate_data=False,
          uses_oddspapi=False)
    try:
        api_key = require_api_tennis_key()
    except Exception:
        _emit("SEASONAL_AUDIT_BLOCKED", reason="API_TENNIS_KEY_UNAVAILABLE")
        return 0

    calls = 0
    calls += 1
    _emit("SEASONAL_AUDIT_ENTITLEMENT", **_tournament_entitlement(api_key))
    stats: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    seen: set[str] = set()
    chunks = [
        (date(2026, 11, 16), date(2026, 12, 16)),
        (date(2026, 12, 17), date(2027, 1, 4)),
    ]
    statuses: list[tuple[str, str, str]] = []
    for low, high in chunks:
        calls += 1
        try:
            observed, rows = fetch_api_tennis_fixture_window(
                api_key, low, high, timeout=45,
            )
            _scan_window(rows, observed, seen, stats)
            status = "SUCCESS"
            _emit("SEASONAL_AUDIT_PROVIDER_WINDOW", start=low.isoformat(),
                  end=high.isoformat(), status=status,
                  provider_raw_rows=len(rows), received_at=observed.isoformat())
        except TransportError as exc:
            status = "FAILED"
            _emit("SEASONAL_AUDIT_PROVIDER_WINDOW", start=low.isoformat(),
                  end=high.isoformat(), status=status,
                  category=exc.kind)
        except (ValueError, TypeError):
            status = "FAILED"
            _emit("SEASONAL_AUDIT_PROVIDER_WINDOW", start=low.isoformat(),
                  end=high.isoformat(), status=status,
                  category="INVALID_ROWS")
        statuses.append((low.isoformat(), high.isoformat(), status))

    monday = START
    while monday <= END:
        key = monday.isoformat()
        period_status = "SUCCESS"
        week_end = min(monday + timedelta(days=6), END)
        for low, high, status in statuses:
            if date.fromisoformat(low) <= week_end and date.fromisoformat(high) >= monday:
                if status != "SUCCESS":
                    period_status = "PARTIAL_OR_FAILED"
        _emit(
            "SEASONAL_AUDIT_WEEK", week=key,
            week_end=week_end.isoformat(),
            provider_snapshot_status=period_status,
            calendar_completeness="FUTURE_DRAWS_MAY_BE_UNPUBLISHED",
            ATP_TOUR=dict(stats[(key, "ATP_TOUR")]),
            CHALLENGER=dict(stats[(key, "CHALLENGER")]),
        )
        monday += timedelta(days=7)

    for sample_day in HISTORICAL_DATES:
        calls += 1
        _emit("SEASONAL_AUDIT_HISTORY_PROVIDER",
              date=sample_day.isoformat(), **_history_sample(api_key, sample_day))

    local = _stored_history_report()
    _emit("SEASONAL_AUDIT_DB", **local)
    _emit("SEASONAL_AUDIT_DONE", utc=datetime.now(UTC).isoformat(),
          requests_attempted=calls,
          within_cap=calls <= MAX_REQUESTS,
          future_windows_succeeded=sum(int(s == "SUCCESS") for _, _, s in statuses),
          source="API_TENNIS",
          no_results_or_picks_rewritten=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
