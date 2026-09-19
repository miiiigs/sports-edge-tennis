from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

import sports_edge_tennis.live.target_fixtures as target_fixtures
from sports_edge_tennis.live.sportradar import SportradarTransportError
from sports_edge_tennis.live.transport import TransportError


def test_sportradar_fixture_transport_adapts_summary_list(monkeypatch):
    received = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    captured = {}

    def fake_fetch(api_key, slate_date, **kwargs):
        captured.update(
            api_key=api_key,
            slate_date=slate_date,
            **kwargs,
        )
        return received, {
            "summaries": [
                {"sport_event": {"id": "sr:event:1"}},
                "ignore-non-object",
            ]
        }

    monkeypatch.setattr(target_fixtures, "fetch_daily_summaries", fake_fetch)
    monkeypatch.setenv("SPORTRADAR_TENNIS_ACCESS_LEVEL", "production")
    monkeypatch.setenv("SPORTRADAR_TENNIS_LANGUAGE", "en")

    observed_at, rows = target_fixtures.fetch_sportradar_fixture_rows(
        "secret",
        date(2026, 9, 19),
    )

    assert observed_at == received
    assert rows == [{"sport_event": {"id": "sr:event:1"}}]
    assert captured["access_level"] == "production"
    assert captured["language"] == "en"


def test_sportradar_fixture_transport_converts_secret_safe_error(monkeypatch):
    def fail(*args, **kwargs):
        raise SportradarTransportError("HTTP_429", "rate limited")

    monkeypatch.setattr(target_fixtures, "fetch_daily_summaries", fail)
    with pytest.raises(TransportError) as exc:
        target_fixtures.fetch_sportradar_fixture_rows(
            "secret",
            date(2026, 9, 19),
        )
    assert exc.value.kind == "HTTP_429"
    assert "secret" not in str(exc.value)


def test_sportradar_fixture_transport_requires_summary_list(monkeypatch):
    monkeypatch.setattr(
        target_fixtures,
        "fetch_daily_summaries",
        lambda *a, **k: (
            datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
            {"summaries": {}},
        ),
    )
    with pytest.raises(TransportError, match="summaries list"):
        target_fixtures.fetch_sportradar_fixture_rows(
            "secret",
            date(2026, 9, 19),
        )


def test_target_ingest_enables_new_events_only_inside_target_adapter(monkeypatch):
    captured = {}
    sentinel = object()

    def fake_ingest(conn, payload, **kwargs):
        captured["conn"] = conn
        captured["payload"] = payload
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        target_fixtures,
        "ingest_sportradar_daily_summaries",
        fake_ingest,
    )
    conn = object()
    received = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    result = target_fixtures.ingest_sportradar_fixture_rows(
        conn,
        [{"sport_event": {"id": "sr:event:1"}}],
        received,
    )

    assert result is sentinel
    assert captured["conn"] is conn
    assert captured["payload"]["summaries"][0]["sport_event"]["id"] == "sr:event:1"
    assert captured["received_at"] == received
    assert captured["allow_new_events"] is True
