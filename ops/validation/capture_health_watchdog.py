"""Read-only capture-cadence watchdog for the tennis live pipeline.

The 13-16 November 2026 discovery gap produced no failed request rows: the
scheduler simply stopped triggering executions. This watchdog checks the
expected hourly cadence itself and exits non-zero when the fixture-ingestion run
stream, the live-batch health stream or the completed prediction-run stream is
silent.

It also reconciles observed executions against the *expected* schedule slots,
so a report can distinguish "95 missed hourly executions" from a shorter or
longer gap without relying on run rows that never existed.

It performs no writes, makes no provider calls and does not alter production.
Schedule it outside the live runner (for example a separate cron/worker or an
external uptime service) so a stopped runner cannot silence its own watchdog.

Notification behaviour:

* an incident notification is sent once per distinct failing dataset set;
* an unchanged incident is suppressed (no repeated pages);
* a recovery notification is sent once when the failing set becomes empty;
* state is persisted in ``--state-path`` so duplicate suppression survives
  process restarts. Without a state path, notifications are still emitted but
  cannot be de-duplicated across runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.validation.fixture_lifecycle import (
    NOTIFY_NONE,
    decide_monitoring_notification,
    detect_capture_gaps,
    expected_execution_slots,
    reconcile_expected_executions,
)

DATASETS = {
    "fixture_ingestion": (
        "SELECT started_at FROM ingest_runs "
        "WHERE source_system='API_TENNIS' "
        "AND source_dataset='api_tennis_scheduled_fixtures' "
        "AND status IN ('COMPLETED','PARTIAL') "
        "AND started_at >= %(start)s AND started_at <= %(end)s "
        "ORDER BY started_at"
    ),
    "live_batch_health": (
        "SELECT checked_at FROM data_health_checks "
        "WHERE source_system='SPORTS_EDGE_TENNIS' "
        "AND dataset_name='LIVE_BATCH_RUNTIME' "
        "AND checked_at >= %(start)s AND checked_at <= %(end)s "
        "ORDER BY checked_at"
    ),
    "completed_prediction_runs": (
        "SELECT prediction_finalized_at FROM prediction_runs "
        "WHERE run_status='COMPLETED' AND experiment_id IS NULL "
        "AND prediction_finalized_at IS NOT NULL "
        "AND prediction_finalized_at >= %(start)s "
        "AND prediction_finalized_at <= %(end)s "
        "ORDER BY prediction_finalized_at"
    ),
}

MAX_REPORTED_SLOTS = 48


def _timestamps(conn: psycopg.Connection, sql: str, start: datetime, end: datetime):
    rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
    return [row[0] for row in rows if row[0] is not None]


def _load_state(path: str | None) -> dict[str, object] | None:
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_state(path: str | None, state: dict[str, object]) -> None:
    if not path:
        return
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def _post_webhook(url: str, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status >= 300:
            raise RuntimeError(f"webhook returned HTTP {response.status}")


def build_report(
    conn: psycopg.Connection,
    *,
    window_start: datetime,
    window_end: datetime,
    expected_interval: timedelta,
) -> dict[str, object]:
    report: dict[str, object] = {
        "version": "TENNIS_CAPTURE_HEALTH_WATCHDOG_20261125_V2",
        "read_only": True,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "expected_interval_minutes": expected_interval.total_seconds() / 60,
        "datasets": {},
    }
    failures: list[str] = []
    total_missing = 0
    for name, sql in DATASETS.items():
        heartbeats = _timestamps(conn, sql, window_start, window_end)
        health = detect_capture_gaps(
            heartbeats,
            window_start=window_start,
            window_end=window_end,
            expected_interval=expected_interval,
        )
        expected = expected_execution_slots(
            window_start, window_end, expected_interval
        )
        reconciliation = reconcile_expected_executions(
            heartbeats,
            window_start=window_start,
            window_end=window_end,
            expected_interval=expected_interval,
        )
        total_missing += reconciliation.missing_count
        report["datasets"][name] = {
            "status": health.status,
            "heartbeat_count": health.heartbeat_count,
            "first_heartbeat_at": (
                None if health.first_heartbeat_at is None
                else health.first_heartbeat_at.isoformat()
            ),
            "last_heartbeat_at": (
                None if health.last_heartbeat_at is None
                else health.last_heartbeat_at.isoformat()
            ),
            "stale_seconds": health.stale_seconds,
            "gaps": list(health.gaps),
            "expected_execution_count": len(expected),
            "matched_execution_count": reconciliation.matched_slot_count,
            "missing_execution_count": reconciliation.missing_count,
            "missing_execution_slots": [
                slot.isoformat()
                for slot in reconciliation.missing_slots[:MAX_REPORTED_SLOTS]
            ],
            "missing_execution_slots_truncated": (
                reconciliation.missing_count > MAX_REPORTED_SLOTS
            ),
            "duplicate_execution_slots": list(reconciliation.duplicate_slots),
            "unexpected_execution_count": len(
                reconciliation.unexpected_executions
            ),
        }
        if health.status != "HEALTHY":
            failures.append(name)

    report["status"] = "HEALTHY" if not failures else "FAILED"
    report["failed_datasets"] = failures
    report["total_missing_execution_slots"] = total_missing
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-hours", type=int, default=6)
    parser.add_argument("--expected-minutes", type=int, default=60)
    parser.add_argument("--as-of", help="ISO-8601 UTC cutoff; defaults to now")
    parser.add_argument("--out-json")
    parser.add_argument(
        "--state-path",
        help="JSON file used to suppress duplicate incident notifications",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.getenv("TENNIS_CAPTURE_WATCHDOG_WEBHOOK_URL"),
        help="optional HTTP endpoint for incident/recovery notifications",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="evaluate and persist state without sending a notification",
    )
    args = parser.parse_args(argv)
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    window_end = (
        datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC)
    )
    if window_end.tzinfo is None:
        parser.error("--as-of must be timezone-aware")
    window_start = window_end - timedelta(hours=args.window_hours)
    expected = timedelta(minutes=args.expected_minutes)

    with psycopg.connect(dsn, autocommit=True, connect_timeout=20) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='60s'")
        report = build_report(
            conn,
            window_start=window_start,
            window_end=window_end,
            expected_interval=expected,
        )

    previous_state = _load_state(args.state_path)
    decision = decide_monitoring_notification(
        report, previous_state, observed_at=window_end
    )
    report["notification"] = {
        "action": decision.action,
        "detail": decision.detail,
        "fingerprint": decision.fingerprint,
        "webhook_configured": bool(args.webhook_url),
        "sent": False,
    }

    notification_failed = False
    if decision.action != NOTIFY_NONE and args.webhook_url and not args.no_notify:
        payload = {
            "version": report["version"],
            "action": decision.action,
            "detail": decision.detail,
            "status": report["status"],
            "failed_datasets": report["failed_datasets"],
            "total_missing_execution_slots": report[
                "total_missing_execution_slots"
            ],
            "window_start": report["window_start"],
            "window_end": report["window_end"],
            "observed_at": window_end.isoformat(),
        }
        try:
            _post_webhook(args.webhook_url, payload)
        except (urllib.error.URLError, RuntimeError) as exc:
            notification_failed = True
            report["notification"]["error"] = str(exc)
        else:
            report["notification"]["sent"] = True

    _write_state(args.state_path, decision.state)

    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    if notification_failed:
        return 2
    return 0 if report["status"] == "HEALTHY" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
