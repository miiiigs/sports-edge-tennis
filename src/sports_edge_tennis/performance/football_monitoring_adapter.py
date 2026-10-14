"""Server-side adapter: Football ``football_monitoring_v1`` → ``sports_edge.monitoring_summary.v1``.

Football (repository ``sports-edge-ai``, PR #122) exposes
``GET /api/monitoring/football`` with its own schema, ``football_monitoring_v1``:
alerts are ``{"code": ..., <context>}`` without a severity, and detail lives
under ``operational`` / ``model_performance`` / ``qualified_betting``. The
unified Tennis dashboard reads every sport through the Tennis-owned envelope
(docs/CROSS_SPORT_MONITORING_CONTRACT.md), so this module translates one into
the other.

Transport is not done here. The input is the result of the unified
dashboard's read-only Football client (PR #208,
``dashboard/football_client.request_get``): ``ok``, ``state``, ``data``. That
client is server-side only, GET-only against an allowlist, bounded-timeout and
sends ``SPORTS_EDGE_CLIENT_API_TOKEN`` as a Bearer header; the token and base
URL never reach this module's output or the browser.

Rules:

* Unknown Football alert codes are kept, namespaced and AMBER, never dropped.
* Football unreachable, unauthorised, not configured, malformed, a different
  schema version, or a stale document → envelope ``status=UNKNOWN`` (or AMBER
  for staleness) with an adapter alert. An unreadable sport is never GREEN.
* Only a whitelisted subset of Football fields is passed through; nothing is
  computed or invented on Football's behalf.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Protocol

from sports_edge_tennis.performance.monitoring_summary import (
    CONTRACT_VERSION,
    STATUS_AMBER,
    STATUS_GREEN,
    STATUS_RED,
)

ADAPTER_VERSION = "football_monitoring_v1_to_sports_edge_v1.1"
FOOTBALL_SCHEMA_VERSION = "football_monitoring_v1"
FOOTBALL_MONITORING_PATH = "/api/monitoring/football"
SPORT = "FOOTBALL"

#: Envelope status when the sport's monitoring could not be read at all.
STATUS_UNKNOWN = "UNKNOWN"

#: Football's own document is computed per request; older than this means a
#: cache or proxy is serving an old copy.
STALE_AFTER_MINUTES = 15
#: Tolerated clock skew before a future ``generated_at`` is flagged.
FUTURE_SKEW_MINUTES = 5

#: Stable severity for every Football alert code known in football_monitoring_v1.
FOOTBALL_ALERT_SEVERITY: dict[str, str] = {
    "SCHEDULER_STALE": STATUS_RED,
    "NO_READY_PREDICTIONS_FOR_UPCOMING": STATUS_RED,
    "ALL_ODDS_STALE": STATUS_RED,
    "FIXTURE_PROVIDER_NOT_CONNECTED": STATUS_RED,
    "SCHEDULER_LAST_CYCLE_ERROR": STATUS_AMBER,
    "READY_FIXTURES_WITHOUT_PICK": STATUS_AMBER,
    "SETTLEMENT_LAG": STATUS_AMBER,
    "PROVIDER_QUOTA_NOT_HEALTHY": STATUS_AMBER,
}

#: Context keys on a Football alert that are safe to render as detail text.
_ALERT_CONTEXT_KEYS = (
    "scheduler",
    "count",
    "upcoming",
    "state",
    "hours_since_success",
    "oldest_kickoff_utc",
    "threshold_minutes",
)

#: Transport states from the PR #208 Football client → adapter alert.
_TRANSPORT_ALERTS: dict[str, tuple[str, str]] = {
    "NOT_CONFIGURED": ("FOOTBALL_NOT_CONFIGURED", "Football integration is not configured."),
    "AUTH_REQUIRED": (
        "FOOTBALL_MONITORING_AUTH_FAILED",
        "Football refused the read-only monitoring request (authentication).",
    ),
    "TIMEOUT": ("FOOTBALL_MONITORING_UNAVAILABLE", "Football did not respond in time."),
    "UNAVAILABLE": ("FOOTBALL_MONITORING_UNAVAILABLE", "Football is unreachable."),
    "HTTP_ERROR": ("FOOTBALL_MONITORING_UNAVAILABLE", "Football returned an HTTP error."),
    "MALFORMED": ("FOOTBALL_CONTRACT_MISMATCH", "Football returned a non-JSON or non-object body."),
    "NOT_ALLOWED": (
        "FOOTBALL_MONITORING_NOT_ALLOWLISTED",
        f"{FOOTBALL_MONITORING_PATH} is not on the read-only Football allowlist.",
    ),
}


class FootballResponseLike(Protocol):
    """Shape of ``dashboard.football_client.FootballResponse`` (PR #208)."""

    ok: bool
    state: str
    data: Mapping[str, Any] | None


def _alert(code: str, severity: str, detail: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail}


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _status(alerts: list[dict[str, str]], *, readable: bool) -> str:
    if not readable:
        return STATUS_UNKNOWN
    severities = {alert["severity"] for alert in alerts}
    if STATUS_RED in severities:
        return STATUS_RED
    if STATUS_AMBER in severities:
        return STATUS_AMBER
    return STATUS_GREEN


def _envelope(
    *,
    now: datetime,
    status: str,
    alerts: list[dict[str, str]],
    football: dict[str, Any] | None,
    source_generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "contract": CONTRACT_VERSION,
        "sport": SPORT,
        "generated_at": now.isoformat(),
        "source_generated_at": source_generated_at,
        "status": status,
        "alerts": alerts,
        "read_only": True,
        "adapter": {
            "version": ADAPTER_VERSION,
            "source_schema": FOOTBALL_SCHEMA_VERSION,
            "source_path": FOOTBALL_MONITORING_PATH,
        },
        "football": football,
    }


def _translate_alert(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("code"), str):
        return _alert(
            "FOOTBALL_MALFORMED_ALERT", STATUS_AMBER, "Football alert without a string code."
        )
    code = raw["code"].strip().upper()
    context = ", ".join(
        f"{key}={raw[key]}" for key in _ALERT_CONTEXT_KEYS if raw.get(key) is not None
    )
    known = code in FOOTBALL_ALERT_SEVERITY
    severity = FOOTBALL_ALERT_SEVERITY.get(code, STATUS_AMBER)
    prefix = "FOOTBALL_" if known else "FOOTBALL_UNMAPPED_"
    detail = context or code
    if not known:
        detail = f"unmapped Football alert {code}" + (f" ({context})" if context else "")
    return _alert(f"{prefix}{code}", severity, detail)


def _passthrough(data: Mapping[str, Any]) -> dict[str, Any]:
    """Whitelisted Football fields only; values are Football's, untouched."""
    operational = data.get("operational") if isinstance(data.get("operational"), Mapping) else {}
    schedulers = operational.get("schedulers") if isinstance(operational.get("schedulers"), Mapping) else {}
    odds = operational.get("odds_freshness") if isinstance(operational.get("odds_freshness"), list) else []
    return {
        "deployed_commit": data.get("deployed_commit"),
        "sources": data.get("sources") if isinstance(data.get("sources"), Mapping) else None,
        "latest_forward_model_pick_issued_at": operational.get("latest_forward_model_pick_issued_at"),
        "latest_prediction_generated_at": operational.get("latest_prediction_generated_at"),
        "prediction_coverage": operational.get("prediction_coverage"),
        "settlement_backlog": operational.get("settlement_backlog"),
        "providers": operational.get("providers"),
        "schedulers_stale": sorted(
            name
            for name, beat in schedulers.items()
            if isinstance(beat, Mapping) and beat.get("stale") is True
        ),
        "odds_feeds": len(odds),
        "odds_feeds_stale": sum(
            1 for row in odds if isinstance(row, Mapping) and row.get("stale") is True
        ),
    }


def adapt_football_monitoring(
    response: FootballResponseLike, *, now: datetime | None = None
) -> dict[str, Any]:
    """Translate one Football monitoring response into the shared envelope."""
    reference = (now or datetime.now(UTC)).astimezone(UTC)

    if not response.ok or response.data is None:
        code, detail = _TRANSPORT_ALERTS.get(
            response.state,
            ("FOOTBALL_MONITORING_UNAVAILABLE", "Football monitoring could not be read."),
        )
        return _envelope(
            now=reference,
            status=STATUS_UNKNOWN,
            alerts=[_alert(code, STATUS_RED, detail)],
            football=None,
        )

    data = response.data
    schema = data.get("schema_version")
    if schema != FOOTBALL_SCHEMA_VERSION:
        return _envelope(
            now=reference,
            status=STATUS_UNKNOWN,
            alerts=[
                _alert(
                    "FOOTBALL_CONTRACT_MISMATCH",
                    STATUS_RED,
                    f"expected schema_version={FOOTBALL_SCHEMA_VERSION}, got {schema!r}",
                )
            ],
            football=None,
        )

    generated = _parse_ts(data.get("generated_at"))
    if generated is None:
        return _envelope(
            now=reference,
            status=STATUS_UNKNOWN,
            alerts=[
                _alert(
                    "FOOTBALL_CONTRACT_MISMATCH",
                    STATUS_RED,
                    "generated_at missing or not a timezone-aware ISO-8601 timestamp",
                )
            ],
            football=None,
        )

    alerts: list[dict[str, str]] = []
    age = reference - generated
    if age > timedelta(minutes=STALE_AFTER_MINUTES):
        alerts.append(
            _alert(
                "FOOTBALL_MONITORING_STALE",
                STATUS_AMBER,
                f"Football monitoring generated {age.total_seconds() / 60:.0f} min ago",
            )
        )
    elif age < -timedelta(minutes=FUTURE_SKEW_MINUTES):
        alerts.append(
            _alert(
                "FOOTBALL_MONITORING_CLOCK_SKEW",
                STATUS_AMBER,
                f"Football generated_at is {-age.total_seconds() / 60:.0f} min in the future",
            )
        )

    if data.get("read_only") is not True:
        alerts.append(
            _alert(
                "FOOTBALL_FIELD_MISSING",
                STATUS_AMBER,
                "read_only is not true in the Football document",
            )
        )

    operational = data.get("operational")
    if not isinstance(operational, Mapping):
        alerts.append(
            _alert(
                "FOOTBALL_FIELD_MISSING",
                STATUS_AMBER,
                "operational block missing; Football health cannot be judged",
            )
        )
    else:
        raw_alerts = operational.get("alerts")
        if not isinstance(raw_alerts, list):
            alerts.append(
                _alert(
                    "FOOTBALL_FIELD_MISSING",
                    STATUS_AMBER,
                    "operational.alerts missing or not a list",
                )
            )
        else:
            alerts.extend(_translate_alert(raw) for raw in raw_alerts)

    return _envelope(
        now=reference,
        status=_status(alerts, readable=True),
        alerts=alerts,
        football=_passthrough(data),
        source_generated_at=generated.isoformat(),
    )


__all__ = [
    "ADAPTER_VERSION",
    "FOOTBALL_ALERT_SEVERITY",
    "FOOTBALL_MONITORING_PATH",
    "FOOTBALL_SCHEMA_VERSION",
    "STATUS_UNKNOWN",
    "adapt_football_monitoring",
]
