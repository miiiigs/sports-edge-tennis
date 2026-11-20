"""Dashboard time zone selection: presentation-only, UTC storage preserved."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.timezone import (
    DEFAULT_TIMEZONE,
    SUPPORTED_TIMEZONES,
    TIMEZONE_COOKIE_NAME,
    dashboard_today,
    format_dashboard_datetime,
    format_dashboard_time,
    is_supported_key,
    is_valid_timezone_name,
    local_date,
    resolve_dashboard_timezone,
    utc_bounds_for_local_date,
)

UTC_ZONE = resolve_dashboard_timezone("utc")
PHILIPPINES = resolve_dashboard_timezone("philippines")
LONDON = resolve_dashboard_timezone("london")


def test_supported_zone_keys_and_iana_names_are_fixed() -> None:
    assert [tz.key for tz in SUPPORTED_TIMEZONES] == [
        "utc",
        "philippines",
        "london",
    ]
    assert PHILIPPINES.iana == "Asia/Manila"
    assert PHILIPPINES.display_label == "Philippines"
    assert PHILIPPINES.short_label == "PHT"
    # London is deliberately NOT UTC+0; DST must be resolved from the zone.
    assert LONDON.iana == "Europe/London"
    assert LONDON.display_label == "London / UK"


def test_default_timezone_is_utc() -> None:
    assert DEFAULT_TIMEZONE.key == "utc"
    assert resolve_dashboard_timezone(None).key == "utc"
    assert resolve_dashboard_timezone("").key == "utc"


def test_each_selector_key_resolves() -> None:
    assert resolve_dashboard_timezone("utc").key == "utc"
    assert resolve_dashboard_timezone("philippines").key == "philippines"
    assert resolve_dashboard_timezone("london").key == "london"
    # Case-insensitive and whitespace tolerant, still only the fixed keys.
    assert resolve_dashboard_timezone("  PHILIPPINES ").key == "philippines"
    assert is_supported_key("london")


def test_invalid_timezone_falls_back_safely() -> None:
    for raw in (
        "America/New_York",
        "Europe/Paris",
        "UTC'; DROP TABLE decisions; --",
        "mars",
        None,
    ):
        assert resolve_dashboard_timezone(raw).key == "utc"
    assert not is_supported_key("America/New_York")
    assert not is_valid_timezone_name("America/New_York")
    assert is_valid_timezone_name("Asia/Manila")


def test_manila_crosses_midnight_while_utc_does_not() -> None:
    instant = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)

    assert local_date(instant, PHILIPPINES) == date(2026, 9, 28)
    assert local_date(instant, UTC_ZONE) == date(2026, 9, 27)
    assert format_dashboard_time(instant, PHILIPPINES) == "04:00 PHT"
    assert format_dashboard_time(instant, UTC_ZONE) == "20:00 UTC"


def test_london_dates_use_zoneinfo_not_a_fixed_offset() -> None:
    # 23:30 UTC in summer is already the next day in London (BST).
    summer = datetime(2026, 7, 15, 23, 30, tzinfo=UTC)
    assert local_date(summer, LONDON) == date(2026, 7, 16)
    # The same clock time in winter (GMT) is still the same day.
    winter = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    assert local_date(winter, LONDON) == date(2026, 1, 15)


def test_display_labels_for_each_zone() -> None:
    winter = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    summer = datetime(2026, 7, 15, 14, 30, tzinfo=UTC)

    assert format_dashboard_time(winter, UTC_ZONE) == "14:30 UTC"
    assert format_dashboard_time(summer, UTC_ZONE) == "14:30 UTC"
    assert format_dashboard_time(winter, PHILIPPINES) == "22:30 PHT"
    assert format_dashboard_time(summer, PHILIPPINES) == "22:30 PHT"
    # London follows the actual date: GMT in winter, BST in summer.
    assert format_dashboard_time(winter, LONDON) == "14:30 GMT"
    assert format_dashboard_time(summer, LONDON) == "15:30 BST"
    assert format_dashboard_datetime(winter, LONDON) == "15 Jan 14:30 GMT"
    assert format_dashboard_datetime(summer, LONDON) == "15 Jul 15:30 BST"


def test_london_dst_transition_day_has_23_hours() -> None:
    # UK DST starts on the last Sunday of March 2026 (2026-03-29).
    start, end = utc_bounds_for_local_date(date(2026, 3, 29), LONDON)
    assert start == datetime(2026, 3, 29, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 29, 23, 0, tzinfo=UTC)
    assert end - start == timedelta(hours=23)


def test_london_dst_fall_back_day_has_25_hours() -> None:
    # UK clocks go back on the last Sunday of October 2026 (2026-10-25).
    start, end = utc_bounds_for_local_date(date(2026, 10, 25), LONDON)
    assert start == datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
    assert end - start == timedelta(hours=25)


def test_dashboard_today_uses_selected_zone() -> None:
    # dashboard_today must be a function of the zone, not a global UTC date.
    assert dashboard_today(UTC_ZONE) == datetime.now(UTC).date()
    assert dashboard_today(PHILIPPINES) == datetime.now(PHILIPPINES.zone).date()
    assert dashboard_today(LONDON) == datetime.now(LONDON.zone).date()


# ------------------------------------------------------------------ HTTP


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    return TestClient(app)


def test_valid_tz_query_sets_cookie_and_redirects(client) -> None:
    response = client.get("/today?tz=philippines", follow_redirects=False)

    assert response.status_code == 303
    assert "tz=" not in response.headers["location"]
    cookie = response.headers["set-cookie"]
    assert TIMEZONE_COOKIE_NAME in cookie
    assert "philippines" in cookie


def test_invalid_tz_query_is_ignored(client) -> None:
    response = client.get("/today?tz=mars", follow_redirects=False)

    assert response.status_code == 200
    assert TIMEZONE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_cookie_persists_the_selection(client) -> None:
    client.cookies.set(TIMEZONE_COOKIE_NAME, "london")

    body = client.get("/today").text

    assert 'value="london" selected' in body
    assert "London / UK" in body


def test_invalid_cookie_falls_back_to_utc(client) -> None:
    client.cookies.set(TIMEZONE_COOKIE_NAME, "America/New_York")

    body = client.get("/today").text

    assert 'value="utc" selected' in body


def test_selected_zone_changes_default_today_date(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    captured: dict[str, date | None] = {}

    def fake_state(requested_date, tz=None):
        captured[tz.key if tz else "?"] = requested_date
        return dashboard_app.RunState(
            kind="EMPTY_SLATE",
            run_date=requested_date,
            as_of=None,
            prediction_run_id=None,
            run_status="COMPLETED",
        )

    def fake_today(tz):
        return {
            "utc": date(2026, 9, 27),
            "philippines": date(2026, 9, 28),
            "london": date(2026, 9, 27),
        }[tz.key]

    monkeypatch.setattr(dashboard_app, "_load_state", fake_state)
    monkeypatch.setattr(dashboard_app, "_latest_activity", lambda: None)
    monkeypatch.setattr(dashboard_app, "_load_live_health", lambda: None)
    monkeypatch.setattr(dashboard_app, "dashboard_today", fake_today)

    client = TestClient(app, follow_redirects=True)

    client.cookies.set(TIMEZONE_COOKIE_NAME, "utc")
    client.get("/today")
    client.cookies.set(TIMEZONE_COOKIE_NAME, "philippines")
    client.get("/today")

    assert captured["utc"] == date(2026, 9, 27)
    assert captured["philippines"] == date(2026, 9, 28)


def test_dashboard_json_exposes_display_timezone_metadata(client) -> None:
    body = client.get("/api/dashboard/today").json()

    assert body["display_timezone"]["key"] == "utc"
    assert body["display_timezone"]["iana"] == "UTC"
    # Canonical timestamps remain machine-readable ISO-8601.
    if body["as_of"] is not None:
        assert "T" in body["as_of"]


def test_today_direct_date_can_match_fixed_manila_73_percent_cohort(client) -> None:
    # A selected UTC or London calendar date is not automatically the same
    # membership as the 73% scorecard's fixed Asia/Manila calendar date.
    utc = client.get("/today?date=2026-10-27")
    assert utc.status_code == 200
    assert 'aria-label="Manila scorecard date alignment"' in utc.text
    assert "You are currently viewing UTC dates" in utc.text
    assert 'href="/today?date=2026-10-27&amp;tz=philippines"' in utc.text
    assert "same Manila date as the 73% scorecard" in utc.text

    ph = client.get("/today?date=2026-10-27&tz=philippines")
    assert ph.status_code == 200
    assert 'aria-label="Manila scorecard date alignment"' not in ph.text
    assert "Original aligned results" not in ph.text  # demo has no scored ledger
    assert "Philippines" in ph.text


def test_today_without_date_offers_current_manila_day_not_yesterday_utc(client) -> None:
    page = client.get("/today")
    assert page.status_code == 200
    assert 'href="/today?tz=philippines"' in page.text
    assert "Switch to today's Manila Model Picks" in page.text

    client.cookies.set(TIMEZONE_COOKIE_NAME, "london")
    london = client.get("/today?date=2026-10-28")
    assert london.status_code == 200
    assert "You are currently viewing London / UK dates" in london.text
    assert 'href="/today?date=2026-10-28&amp;tz=philippines"' in london.text
