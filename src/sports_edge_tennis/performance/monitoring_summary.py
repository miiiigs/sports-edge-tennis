"""Read-only operational monitoring summary for Sports Edge Tennis.

One payload answering "is the system alive and where is it going wrong":
latest production prediction cycle, provider feed runs (API-Tennis fixtures,
results, offered odds), sharp/offered odds freshness, OddsPapi request usage,
pending results by reason/age, calibration flags, and derived alerts.

It reads persisted evidence only. It never calls a provider, writes a row, or
changes any model, Manager, Market, Veto, staking or settlement behavior.

The payload follows the Tennis-owned cross-sport contract
``sports_edge.monitoring_summary.v1`` (docs/CROSS_SPORT_MONITORING_CONTRACT.md).
Sport-specific detail lives under ``tennis``; the shared envelope (``sport``,
``generated_at``, ``status``, ``alerts``) is what a future cross-sport view reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.performance.regimes import PRODUCTION_RUN_PREDICATE

CONTRACT_VERSION = "sports_edge.monitoring_summary.v1"
SPORT = "TENNIS"

STATUS_GREEN = "GREEN"
STATUS_AMBER = "AMBER"
STATUS_RED = "RED"
_SEVERITY_RANK = {STATUS_GREEN: 0, STATUS_AMBER: 1, STATUS_RED: 2}

# Hourly cadence + the existing LIVE_BATCH_GAP_ALERT 90-minute threshold.
PREDICTION_CYCLE_STALE_MINUTES = 90
# Hourly pending-result recovery runs inside the live batch.
RESULT_REFRESH_STALE_MINUTES = 180
SHARP_REFERENCE_STALE_HOURS = 6
OFFERED_ODDS_STALE_HOURS = 6
PROVIDER_FAILURE_WINDOW_HOURS = 24

RESULT_REFRESH_DATASET = "api_tennis_completed_result_refresh"


@dataclass(frozen=True)
class MonitoringAlert:
    code: str
    severity: str
    detail: str

    def as_json(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


def _minutes_since(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return (now - value).total_seconds() / 60.0


def evaluate_alerts(snapshot: dict[str, Any], *, now: datetime) -> list[MonitoringAlert]:
    """Pure alert rules over a loaded snapshot (unit-testable without a DB)."""
    alerts: list[MonitoringAlert] = []

    cycle_age = _minutes_since(now, snapshot.get("latest_prediction_cycle_at"))
    if cycle_age is None or cycle_age > PREDICTION_CYCLE_STALE_MINUTES:
        alerts.append(
            MonitoringAlert(
                "PREDICTION_CYCLE_STALE",
                STATUS_RED,
                "no finalized production prediction run"
                if cycle_age is None
                else f"last finalized production run {cycle_age:.0f} min ago",
            )
        )

    refresh_age = _minutes_since(now, snapshot.get("latest_result_refresh_at"))
    if refresh_age is None or refresh_age > RESULT_REFRESH_STALE_MINUTES:
        alerts.append(
            MonitoringAlert(
                "RESULT_REFRESH_STALE",
                STATUS_RED,
                "no completed API-Tennis result refresh"
                if refresh_age is None
                else f"last completed API-Tennis result refresh {refresh_age:.0f} min ago",
            )
        )

    sharp_age = _minutes_since(now, snapshot.get("latest_sharp_observed_at"))
    if sharp_age is None or sharp_age > SHARP_REFERENCE_STALE_HOURS * 60:
        alerts.append(
            MonitoringAlert(
                "SHARP_REFERENCE_STALE",
                STATUS_RED,
                "no SHARP/PINNACLE snapshot; Type A bets cannot qualify"
                if sharp_age is None
                else f"latest SHARP/PINNACLE snapshot {sharp_age / 60:.1f} h old; "
                "Type A bets cannot qualify",
            )
        )

    offered_age = _minutes_since(now, snapshot.get("latest_offered_observed_at"))
    if offered_age is None or offered_age > OFFERED_ODDS_STALE_HOURS * 60:
        alerts.append(
            MonitoringAlert(
                "OFFERED_ODDS_STALE",
                STATUS_AMBER,
                "no offered odds snapshot"
                if offered_age is None
                else f"latest offered odds snapshot {offered_age / 60:.1f} h old",
            )
        )

    failing = [
        feed
        for feed in snapshot.get("provider_feeds", ())
        if feed.get("failed_runs_window", 0) > 0
    ]
    for feed in failing:
        alerts.append(
            MonitoringAlert(
                "PROVIDER_FEED_FAILURES",
                STATUS_AMBER,
                f"{feed['source_system']}/{feed['source_dataset']}: "
                f"{feed['failed_runs_window']} failed of {feed['runs_window']} runs "
                f"in {PROVIDER_FAILURE_WINDOW_HOURS}h",
            )
        )

    pending = snapshot.get("pending") or {}
    stranded = int((pending.get("by_flag") or {}).get("OUTSIDE_RECOVERY_WINDOW", 0))
    if stranded:
        alerts.append(
            MonitoringAlert(
                "PENDING_RESULTS_STRANDED",
                STATUS_AMBER,
                f"{stranded} pending pick(s) older than the automatic recovery window",
            )
        )
    delayed = int((pending.get("by_reason") or {}).get("RESULT_DELAYED", 0))
    if delayed:
        alerts.append(
            MonitoringAlert(
                "PENDING_RESULTS_DELAYED",
                STATUS_AMBER,
                f"{delayed} started ≥6h ago, still re-observed, no terminal result",
            )
        )

    for flag in snapshot.get("calibration_flags", ()):
        alerts.append(MonitoringAlert(f"CALIBRATION_{flag}", STATUS_AMBER, flag))

    return alerts


def overall_status(alerts: list[MonitoringAlert]) -> str:
    worst = STATUS_GREEN
    for alert in alerts:
        if _SEVERITY_RANK[alert.severity] > _SEVERITY_RANK[worst]:
            worst = alert.severity
    return worst


def _load_snapshot(conn: psycopg.Connection, *, now: datetime) -> dict[str, Any]:
    latest_cycle = conn.execute(
        f"""
        SELECT max(pr.prediction_finalized_at), count(*) FILTER (
            WHERE pr.prediction_finalized_at >= %s
        )
        FROM prediction_runs pr
        WHERE {PRODUCTION_RUN_PREDICATE}
        """,
        (now - timedelta(hours=24),),
    ).fetchone()

    window_start = now - timedelta(hours=PROVIDER_FAILURE_WINDOW_HOURS)
    feeds = [
        {
            "source_system": str(row[0]),
            "source_dataset": str(row[1]),
            "latest_completed_at": row[2],
            "latest_failed_at": row[3],
            "runs_window": int(row[4]),
            "failed_runs_window": int(row[5]),
            "partial_runs_window": int(row[6]),
        }
        for row in conn.execute(
            """
            SELECT
                source_system,
                source_dataset,
                max(COALESCE(finished_at, started_at)) FILTER (
                    WHERE status::text = 'COMPLETED'
                ),
                max(COALESCE(finished_at, started_at)) FILTER (
                    WHERE status::text = 'FAILED'
                ),
                count(*) FILTER (WHERE started_at >= %(w)s),
                count(*) FILTER (WHERE started_at >= %(w)s AND status::text = 'FAILED'),
                count(*) FILTER (WHERE started_at >= %(w)s AND status::text = 'PARTIAL')
            FROM ingest_runs
            WHERE started_at >= %(since)s
            GROUP BY source_system, source_dataset
            ORDER BY source_system, source_dataset
            """,
            {"w": window_start, "since": now - timedelta(days=30)},
        ).fetchall()
    ]
    result_refresh = max(
        (
            feed["latest_completed_at"]
            for feed in feeds
            if feed["source_dataset"] == RESULT_REFRESH_DATASET
            and feed["latest_completed_at"] is not None
        ),
        default=None,
    )

    odds = [
        {
            "normalization_class": str(row[0]),
            "bookmaker_code": str(row[1]),
            "latest_observed_at": row[2],
            "snapshots_24h": int(row[3]),
        }
        for row in conn.execute(
            """
            SELECT source_system, bookmaker_code, max(source_observed_at),
                   count(*) FILTER (WHERE source_observed_at >= %s)
            FROM odds_snapshots
            WHERE source_observed_at >= %s
            GROUP BY source_system, bookmaker_code
            ORDER BY source_system, bookmaker_code
            """,
            (now - timedelta(hours=24), now - timedelta(days=60)),
        ).fetchall()
    ]
    sharp = max(
        (o["latest_observed_at"] for o in odds if o["normalization_class"] == "SHARP"),
        default=None,
    )
    offered = max(
        (o["latest_observed_at"] for o in odds if o["normalization_class"] == "OFFERED"),
        default=None,
    )

    oddspapi_usage = None
    if conn.execute(
        "SELECT to_regclass('public.oddspapi_request_ledger') IS NOT NULL"
    ).fetchone()[0]:
        month_start = now.astimezone(UTC).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        rows = conn.execute(
            """
            SELECT attempt_status::text, count(*)
            FROM oddspapi_request_ledger
            WHERE requested_at >= %s
            GROUP BY 1
            ORDER BY 1
            """,
            (month_start,),
        ).fetchall()
        oddspapi_usage = {
            "month_start": month_start.isoformat(),
            "requests_month_to_date": sum(int(r[1]) for r in rows),
            "by_attempt_status": {str(r[0]): int(r[1]) for r in rows},
        }

    return {
        "latest_prediction_cycle_at": latest_cycle[0] if latest_cycle else None,
        "prediction_cycles_24h": int(latest_cycle[1]) if latest_cycle else 0,
        "latest_result_refresh_at": result_refresh,
        "provider_feeds": feeds,
        "odds": odds,
        "latest_sharp_observed_at": sharp,
        "latest_offered_observed_at": offered,
        "oddspapi_usage": oddspapi_usage,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def load_monitoring_summary(
    conn: psycopg.Connection,
    *,
    now: datetime | None = None,
    include_calibration: bool = True,
) -> dict[str, Any]:
    reference = now or datetime.now(UTC)
    snapshot = _load_snapshot(conn, now=reference)

    from sports_edge_tennis.live.pending_result_diagnostics import (
        load_pending_result_diagnostics,
    )

    pending = load_pending_result_diagnostics(conn, now=reference)
    snapshot["pending"] = {
        "total": pending.total,
        "by_reason": pending.by_reason,
        "by_age": pending.by_age,
        "by_flag": pending.by_flag,
    }

    calibration_flags: list[str] = []
    if include_calibration:
        from sports_edge_tennis.performance.calibration_monitor import (
            load_calibration_health,
        )

        try:
            calibration_flags = list(
                load_calibration_health(
                    conn, reference_date=reference.date(), include_abstention=False
                ).get("flags", [])
            )
        except Exception:  # noqa: BLE001 - calibration is advisory in this view
            calibration_flags = ["CALIBRATION_UNAVAILABLE"]
    snapshot["calibration_flags"] = calibration_flags

    alerts = evaluate_alerts(snapshot, now=reference)
    return {
        "contract": CONTRACT_VERSION,
        "sport": SPORT,
        "generated_at": reference.isoformat(),
        "status": overall_status(alerts),
        "alerts": [alert.as_json() for alert in alerts],
        "read_only": True,
        "tennis": _jsonable(snapshot),
        "detail_endpoints": {
            "pending_results": "/api/dashboard/pending-results",
            "result_coverage": "/api/dashboard/result-coverage",
            "calibration_health": "/api/dashboard/calibration-health",
            "agent_abstention": "/api/dashboard/agent-abstention",
            "model_evidence": "/api/dashboard/model-evidence",
            "model_pick_results": "/api/dashboard/model-pick-results",
            "value_bet_results": "/api/dashboard/value-bet-results",
            "paper_bankroll": "/api/dashboard/paper-bankroll",
            "clv": "/api/dashboard/clv",
            "sharp_blockers": "/api/dashboard/sharp-blockers",
        },
    }


__all__ = [
    "CONTRACT_VERSION",
    "MonitoringAlert",
    "evaluate_alerts",
    "load_monitoring_summary",
    "overall_status",
]
