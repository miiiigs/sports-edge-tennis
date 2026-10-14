"""Contract regression tests: football_monitoring_v1 → sports_edge.monitoring_summary.v1.

The fixture mirrors the document built by Football's
``build_football_monitoring_payload`` (sports-edge-ai PR #122,
``code/src/sports_ai/dashboard/football_monitoring.py``): same top-level keys,
same ``operational`` keys, alert dicts without severity. If Football changes
that shape, these tests are where it should show up first.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sports_edge_tennis.performance.football_monitoring_adapter import (
    FOOTBALL_ALERT_SEVERITY,
    FOOTBALL_MONITORING_PATH,
    STATUS_UNKNOWN,
    adapt_football_monitoring,
)
from sports_edge_tennis.performance.monitoring_summary import CONTRACT_VERSION

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

FOOTBALL_V1_TOP_LEVEL_KEYS = {
    "schema_version",
    "sport",
    "generated_at",
    "read_only",
    "deployed_commit",
    "sources",
    "operational",
    "model_performance",
    "qualified_betting",
    "high_confidence_shadow",
}


@dataclass(frozen=True)
class _Response:
    """Same fields as dashboard.football_client.FootballResponse (PR #208)."""

    ok: bool
    state: str
    message: str = ""
    data: dict[str, Any] | None = None


def _football_doc(**overrides) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": "football_monitoring_v1",
        "sport": "football",
        "generated_at": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "deployed_commit": "abc1234",
        "sources": {"dashboard_db": True, "paper_db": True, "model_pick_db": True},
        "operational": {
            "schedulers": {
                "result_sync": {"enabled": True, "stale": False, "last_error_present": False},
                "market_collection": {"enabled": True, "stale": False, "last_error_present": False},
            },
            "latest_forward_model_pick_issued_at": "2026-10-14T08:10:00Z",
            "latest_prediction_generated_at": "2026-10-14T08:00:00Z",
            "prediction_coverage": {
                "window_hours": 72,
                "upcoming_fixtures": 12,
                "with_prediction": 12,
                "latest_ready": 10,
                "ready_without_model_pick": 0,
                "latest_not_ready_by_status": {"AWAITING_MARKET": 2},
                "ready_rate": 0.8333,
            },
            "settlement_backlog": {
                "lag_threshold_hours": 3,
                "open_past_kickoff": 0,
                "oldest_kickoff_utc": None,
                "max_lag_hours": None,
            },
            "providers": {
                "fixture_provider_state": "CONNECTED",
                "provider_budget_quota_state": "HEALTHY",
                "market_sync_state": "OK",
            },
            "last_issuance_cycle": {"written": 1, "canonical_duplicates": 0, "conflicts": 0},
            "odds_freshness": [
                {"provider": "x", "market": "asian_handicap", "age_minutes": 20.0, "stale": False},
                {"provider": "x", "market": "corner_total", "age_minutes": 400.0, "stale": True},
            ],
            "alerts": [],
        },
        "model_performance": {"forward": {"settled": 40}},
        "qualified_betting": {"value_bets": 0},
        "high_confidence_shadow": {"available": False},
    }
    doc.update(overrides)
    return doc


def _ok(doc: dict[str, Any]) -> _Response:
    return _Response(ok=True, state="OK", data=doc)


def _codes(envelope: dict[str, Any]) -> list[str]:
    return [alert["code"] for alert in envelope["alerts"]]


def test_fixture_matches_football_v1_top_level_shape():
    assert set(_football_doc()) == FOOTBALL_V1_TOP_LEVEL_KEYS


def test_healthy_football_document_is_green_in_shared_envelope():
    envelope = adapt_football_monitoring(_ok(_football_doc()), now=NOW)

    assert envelope["contract"] == CONTRACT_VERSION
    assert envelope["sport"] == "FOOTBALL"
    assert envelope["status"] == "GREEN"
    assert envelope["alerts"] == []
    assert envelope["read_only"] is True
    assert envelope["adapter"]["source_schema"] == "football_monitoring_v1"
    assert envelope["adapter"]["source_path"] == FOOTBALL_MONITORING_PATH
    assert envelope["source_generated_at"] == "2026-10-14T08:59:00+00:00"
    football = envelope["football"]
    assert football["prediction_coverage"]["upcoming_fixtures"] == 12
    assert football["odds_feeds"] == 2 and football["odds_feeds_stale"] == 1
    assert football["schedulers_stale"] == []
    # Only whitelisted fields cross over; model/betting detail stays in Football.
    assert "model_performance" not in football and "qualified_betting" not in football


@pytest.mark.parametrize("code,severity", sorted(FOOTBALL_ALERT_SEVERITY.items()))
def test_every_known_football_alert_has_a_stable_severity(code, severity):
    doc = _football_doc()
    doc["operational"]["alerts"] = [{"code": code, "count": 3}]
    envelope = adapt_football_monitoring(_ok(doc), now=NOW)
    assert envelope["alerts"] == [
        {"code": f"FOOTBALL_{code}", "severity": severity, "detail": "count=3"}
    ]
    assert envelope["status"] == severity


def test_scheduler_stale_detail_and_red_status():
    doc = _football_doc()
    doc["operational"]["schedulers"]["result_sync"]["stale"] = True
    doc["operational"]["alerts"] = [
        {"code": "SCHEDULER_STALE", "scheduler": "result_sync", "hours_since_success": 30.5},
        {"code": "SETTLEMENT_LAG", "count": 4, "oldest_kickoff_utc": "2026-10-13T18:00:00Z"},
    ]
    envelope = adapt_football_monitoring(_ok(doc), now=NOW)
    assert envelope["status"] == "RED"
    assert envelope["alerts"][0]["detail"] == "scheduler=result_sync, hours_since_success=30.5"
    assert envelope["football"]["schedulers_stale"] == ["result_sync"]


def test_unknown_football_alert_is_kept_as_amber_not_dropped():
    doc = _football_doc()
    doc["operational"]["alerts"] = [{"code": "NEW_THING", "state": "X"}]
    envelope = adapt_football_monitoring(_ok(doc), now=NOW)
    assert envelope["alerts"] == [
        {
            "code": "FOOTBALL_UNMAPPED_NEW_THING",
            "severity": "AMBER",
            "detail": "unmapped Football alert NEW_THING (state=X)",
        }
    ]
    assert envelope["status"] == "AMBER"


def test_malformed_alert_entry_is_reported():
    doc = _football_doc()
    doc["operational"]["alerts"] = ["oops", {"count": 1}]
    envelope = adapt_football_monitoring(_ok(doc), now=NOW)
    assert _codes(envelope) == ["FOOTBALL_MALFORMED_ALERT", "FOOTBALL_MALFORMED_ALERT"]


@pytest.mark.parametrize(
    "state,code",
    [
        ("NOT_CONFIGURED", "FOOTBALL_NOT_CONFIGURED"),
        ("AUTH_REQUIRED", "FOOTBALL_MONITORING_AUTH_FAILED"),
        ("TIMEOUT", "FOOTBALL_MONITORING_UNAVAILABLE"),
        ("UNAVAILABLE", "FOOTBALL_MONITORING_UNAVAILABLE"),
        ("HTTP_ERROR", "FOOTBALL_MONITORING_UNAVAILABLE"),
        ("MALFORMED", "FOOTBALL_CONTRACT_MISMATCH"),
        ("NOT_ALLOWED", "FOOTBALL_MONITORING_NOT_ALLOWLISTED"),
        ("SOMETHING_NEW", "FOOTBALL_MONITORING_UNAVAILABLE"),
    ],
)
def test_transport_failures_are_unknown_never_green(state, code):
    envelope = adapt_football_monitoring(_Response(ok=False, state=state), now=NOW)
    assert envelope["status"] == STATUS_UNKNOWN
    assert _codes(envelope) == [code]
    assert envelope["football"] is None
    assert envelope["contract"] == CONTRACT_VERSION


def test_different_schema_version_is_a_contract_mismatch():
    envelope = adapt_football_monitoring(
        _ok(_football_doc(schema_version="football_monitoring_v2")), now=NOW
    )
    assert envelope["status"] == STATUS_UNKNOWN
    assert _codes(envelope) == ["FOOTBALL_CONTRACT_MISMATCH"]
    assert "football_monitoring_v2" in envelope["alerts"][0]["detail"]


def test_missing_schema_version_is_a_contract_mismatch():
    doc = _football_doc()
    del doc["schema_version"]
    envelope = adapt_football_monitoring(_ok(doc), now=NOW)
    assert envelope["status"] == STATUS_UNKNOWN


@pytest.mark.parametrize("value", [None, "", "yesterday", "2026-10-14T08:59:00"])
def test_unusable_generated_at_is_a_contract_mismatch(value):
    envelope = adapt_football_monitoring(_ok(_football_doc(generated_at=value)), now=NOW)
    assert envelope["status"] == STATUS_UNKNOWN
    assert _codes(envelope) == ["FOOTBALL_CONTRACT_MISMATCH"]


def test_stale_document_is_amber():
    envelope = adapt_football_monitoring(
        _ok(_football_doc(generated_at=(NOW - timedelta(minutes=16)).isoformat())), now=NOW
    )
    assert _codes(envelope) == ["FOOTBALL_MONITORING_STALE"]
    assert envelope["status"] == "AMBER"
    assert adapt_football_monitoring(
        _ok(_football_doc(generated_at=(NOW - timedelta(minutes=15)).isoformat())), now=NOW
    )["alerts"] == []


def test_future_document_beyond_skew_is_flagged():
    envelope = adapt_football_monitoring(
        _ok(_football_doc(generated_at=(NOW + timedelta(minutes=6)).isoformat())), now=NOW
    )
    assert _codes(envelope) == ["FOOTBALL_MONITORING_CLOCK_SKEW"]


def test_missing_operational_block_is_amber_with_null_fields():
    doc = _football_doc()
    del doc["operational"]
    envelope = adapt_football_monitoring(_ok(doc), now=NOW)
    assert _codes(envelope) == ["FOOTBALL_FIELD_MISSING"]
    assert envelope["status"] == "AMBER"
    assert envelope["football"]["prediction_coverage"] is None
    assert envelope["football"]["odds_feeds"] == 0


def test_missing_alerts_list_is_amber():
    doc = _football_doc()
    del doc["operational"]["alerts"]
    envelope = adapt_football_monitoring(_ok(doc), now=NOW)
    assert _codes(envelope) == ["FOOTBALL_FIELD_MISSING"]


def test_read_only_false_is_flagged():
    envelope = adapt_football_monitoring(_ok(_football_doc(read_only=False)), now=NOW)
    assert _codes(envelope) == ["FOOTBALL_FIELD_MISSING"]


def test_adapter_never_emits_credentials_or_base_url(monkeypatch):
    token = "tok-should-never-appear"
    base = "https://football.internal.example"
    monkeypatch.setenv("SPORTS_EDGE_CLIENT_API_TOKEN", token)
    monkeypatch.setenv("SPORTS_EDGE_FOOTBALL_BASE_URL", base)
    for response in (
        _ok(_football_doc()),
        _Response(ok=False, state="AUTH_REQUIRED", message=f"Bearer {token} at {base}"),
    ):
        text = json.dumps(adapt_football_monitoring(response, now=NOW))
        assert token not in text
        assert base not in text


def test_adapter_does_not_mutate_the_football_document():
    doc = _football_doc()
    doc["operational"]["alerts"] = [{"code": "SETTLEMENT_LAG", "count": 1}]
    before = copy.deepcopy(doc)
    adapt_football_monitoring(_ok(doc), now=NOW)
    assert doc == before
