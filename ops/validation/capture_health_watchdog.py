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
  cannot be de-duplicated across runs;
* a failed webhook delivery does not advance the deduplication state, so the
  next run retries instead of silently treating the alert as delivered.
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
    PIT_LEAD,
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

# A run row can be healthy while an individual fixture still has no forecast.
# This read-only query checks the work product, not only service uptime:
#   * OVERDUE: still scheduled, deadline (start - lead) already passed, but no
#     completed prediction run targeted it on time. It can still be saved only
#     if a run happens before the start. It must never be backdated.
#   * MISSED: the scheduled start already passed with no on-time completed
#     target. It is reported for the record and never converted into a forecast.
OVERDUE_PREDICTION_SQL = """
WITH candidate_matches AS (
    SELECT DISTINCT mv.match_id
    FROM match_versions mv
    WHERE mv.match_start_at >= %(missed_start)s
      AND mv.match_start_at <= %(overdue_end)s
)
SELECT
    latest.match_id,
    latest.match_start_at,
    CASE WHEN latest.match_start_at > %(as_of)s THEN 'OVERDUE' ELSE 'MISSED' END
        AS bucket
FROM candidate_matches c
CROSS JOIN LATERAL (
    SELECT
        mv.match_id,
        mv.match_start_at,
        mv.match_status::text AS match_status,
        mv.qualifying_flag,
        mv.tour_scope::text AS tour_scope
    FROM match_versions mv
    WHERE mv.match_id = c.match_id
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
    LIMIT 1
) latest
WHERE latest.match_status = 'SCHEDULED'
  AND latest.qualifying_flag = false
  AND latest.tour_scope IN ('ATP_MAIN', 'CHALLENGER')
  AND latest.match_start_at >= %(missed_start)s
  AND latest.match_start_at <= %(overdue_end)s
  AND NOT EXISTS (
      SELECT 1
      FROM prediction_run_targets t
      JOIN prediction_runs r ON r.prediction_run_id = t.prediction_run_id
      JOIN predictions p
        ON p.prediction_run_id = t.prediction_run_id
       AND p.match_id = t.match_id
      WHERE t.match_id = latest.match_id
        AND r.run_status = 'COMPLETED'
        AND r.experiment_id IS NULL
        AND p.experiment_id IS NULL
        AND p.prediction_locked_at <= latest.match_start_at - %(lead)s::interval
  )
ORDER BY latest.match_start_at, latest.match_id
"""

MAX_REPORTED_SLOTS = 48
MAX_REPORTED_FIXTURES = 48



def _timestamps(conn: psycopg.Connection, sql: str, start: datetime, end: datetime):
    rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
    return [row[0] for row in rows if row[0] is not None]


def overdue_bounds(
    as_of: datetime,
    *,
    prediction_lead: timedelta,
    missed_lookback: timedelta,
) -> tuple[datetime, datetime]:
    """Return the missed lookback start and the upcoming overdue horizon end."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if prediction_lead <= timedelta(0):
        raise ValueError("prediction_lead must be positive")
    if missed_lookback < timedelta(0):
        raise ValueError("missed_lookback must not be negative")
    return as_of - missed_lookback, as_of + prediction_lead


def build_overdue_report(
    conn: psycopg.Connection,
    *,
    as_of: datetime,
    prediction_lead: timedelta = PIT_LEAD,
    missed_lookback: timedelta = timedelta(hours=2),
) -> dict[str, object]:
    """Report fixtures whose on-time forecast is overdue or already missed.

    Read-only. The query proves whether a completed prediction run actually
    targeted the fixture before its ``start - lead`` deadline. A missing row is
    reported as missed or overdue; it is never inserted, backdated or inferred.
    """
    missed_start, overdue_end = overdue_bounds(
        as_of,
        prediction_lead=prediction_lead,
        missed_lookback=missed_lookback,
    )
    rows = conn.execute(
        OVERDUE_PREDICTION_SQL,
        {
            "as_of": as_of,
            "missed_start": missed_start,
            "overdue_end": overdue_end,
            "lead": f"{int(prediction_lead.total_seconds())} seconds",
        },
    ).fetchall()
    overdue = [
        {
            "match_id": int(row[0]),
            "match_start_at": row[1].isoformat(),
            "classification": "OVERDUE",
        }
        for row in rows
        if str(row[2]) == "OVERDUE"
    ]
    missed = [
        {
            "match_id": int(row[0]),
            "match_start_at": row[1].isoformat(),
            "classification": "MISSED",
        }
        for row in rows
        if str(row[2]) != "OVERDUE"
    ]
    report: dict[str, object] = {
        "status": "HEALTHY" if not rows else "FAILED",
        "as_of": as_of.isoformat(),
        "prediction_lead_minutes": prediction_lead.total_seconds() / 60,
        "missed_lookback_minutes": missed_lookback.total_seconds() / 60,
        "overdue_count": len(overdue),
        "missed_count": len(missed),
        "overdue": overdue[:MAX_REPORTED_FIXTURES],
        "missed": missed[:MAX_REPORTED_FIXTURES],
        "truncated": len(overdue) > MAX_REPORTED_FIXTURES
        or len(missed) > MAX_REPORTED_FIXTURES,
        "backdated": False,
    }
    return report


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
    prediction_lead: timedelta = PIT_LEAD,
    missed_lookback: timedelta = timedelta(hours=2),
) -> dict[str, object]:
    report: dict[str, object] = {
        "version": "TENNIS_CAPTURE_HEALTH_WATCHDOG_20261125_V3",
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

    overdue = build_overdue_report(
        conn,
        as_of=window_end,
        prediction_lead=prediction_lead,
        missed_lookback=missed_lookback,
    )
    report["overdue_predictions"] = overdue
    if overdue["status"] != "HEALTHY":
        failures.append("overdue_predictions")

    report["status"] = "HEALTHY" if not failures else "FAILED"
    report["failed_datasets"] = failures
    report["total_missing_execution_slots"] = total_missing
    return report


def _next_state(
    previous_state: dict[str, object] | None,
    decision,
    *,
    notification_failed: bool,
    observed_at: datetime,
    error: str | None,
) -> dict[str, object]:
    """Advance deduplication state only after successful notification delivery."""
    if not notification_failed:
        return decision.state
    state = dict(previous_state or {})
    state.setdefault("active_fingerprint", None)
    state["last_delivery_error"] = error
    state["last_delivery_failed_at"] = observed_at.isoformat()
    state["last_action"] = "DELIVERY_FAILED"
    state["last_detail"] = decision.detail
    state["last_observed_at"] = observed_at.isoformat()
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-hours", type=int, default=6)
    parser.add_argument("--expected-minutes", type=int, default=60)
    parser.add_argument(
        "--prediction-lead-minutes",
        type=int,
        default=30,
        help="on-time forecast deadline before scheduled start",
    )
    parser.add_argument(
        "--missed-lookback-minutes",
        type=int,
        default=120,
        help="how far back to report fixtures whose on-time forecast was missed",
    )
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
    prediction_lead = timedelta(minutes=args.prediction_lead_minutes)
    missed_lookback = timedelta(minutes=args.missed_lookback_minutes)
    if prediction_lead <= timedelta(0):
        parser.error("--prediction-lead-minutes must be positive")
    if missed_lookback < timedelta(0):
        parser.error("--missed-lookback-minutes must not be negative")

    with psycopg.connect(dsn, autocommit=True, connect_timeout=20) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='60s'")
        report = build_report(
            conn,
            window_start=window_start,
            window_end=window_end,
            expected_interval=expected,
            prediction_lead=prediction_lead,
            missed_lookback=missed_lookback,
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
        overdue = report.get("overdue_predictions") or {}
        payload = {
            "version": report["version"],
            "action": decision.action,
            "detail": decision.detail,
            "status": report["status"],
            "failed_datasets": report["failed_datasets"],
            "total_missing_execution_slots": report[
                "total_missing_execution_slots"
            ],
            "overdue_predictions": {
                "status": overdue.get("status"),
                "overdue_count": overdue.get("overdue_count", 0),
                "missed_count": overdue.get("missed_count", 0),
            },
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

    # Only advance the deduplication state after a successful delivery (or when
    # no delivery was required). A failed notifier must not suppress the retry:
    # otherwise exit code 2 is the only trace of an alert the owner never saw.
    _write_state(
        args.state_path,
        _next_state(
            previous_state,
            decision,
            notification_failed=notification_failed,
            observed_at=window_end,
            error=report["notification"].get("error"),
        ),
    )

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
