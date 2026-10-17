from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import sports_edge_tennis.live.transport as transport
from sports_edge_tennis.live.transport import (
    TransportError,
    fetch_api_tennis_draw,
    fetch_api_tennis_fixtures,
    fetch_pinnapi_board,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, json_error=False):
        self.status_code = status_code
        self._json = json_data
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("invalid json")
        return self._json


class FakeRequestsGet:
    def __init__(self, *, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.response


@pytest.fixture
def fake_get(monkeypatch):
    fake = FakeRequestsGet(response=FakeResponse())
    monkeypatch.setattr(transport.requests, "get", fake)
    return fake


FIXTURES = [{"event_key": "7789001"}]
CLOCK = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
SLATE = date(2026, 9, 5)


def test_api_tennis_request_parameters(fake_get):
    fake_get.response = FakeResponse(json_data={"success": 1, "result": FIXTURES})
    fetch_api_tennis_fixtures("KEY123", SLATE, clock=lambda: CLOCK)
    url, kwargs = fake_get.calls[0]
    assert url.endswith("/tennis/")
    assert kwargs["params"]["method"] == "get_fixtures"
    assert kwargs["params"]["APIkey"] == "KEY123"
    assert kwargs["params"]["date_start"] == "2026-09-05"
    assert kwargs["params"]["date_stop"] == "2026-09-05"
    assert kwargs["params"]["timezone"] == "UTC"


def test_api_tennis_returns_received_at_and_fixtures(fake_get):
    fake_get.response = FakeResponse(json_data={"success": 1, "result": FIXTURES})
    received_at, fixtures = fetch_api_tennis_fixtures("KEY123", SLATE, clock=lambda: CLOCK)
    assert received_at == CLOCK
    assert fixtures == FIXTURES


def test_api_tennis_draw_request_and_surface_payload(fake_get):
    payload = {
        "tournament": {
            "tournament_key": "1214",
            "tournament_surface": "Hard",
            "tournament_season": "2026",
        }
    }
    fake_get.response = FakeResponse(json_data={"success": 1, "result": payload})
    received_at, result = fetch_api_tennis_draw(
        "KEY123",
        "1214",
        2026,
        clock=lambda: CLOCK,
    )
    url, kwargs = fake_get.calls[0]
    assert url.endswith("/tennis/")
    assert kwargs["params"] == {
        "method": "get_draw",
        "APIkey": "KEY123",
        "tournament_key": "1214",
        "tournament_season": "2026",
        "include_qualification": "1",
    }
    assert received_at == CLOCK
    assert result["tournament"]["tournament_surface"] == "Hard"


def test_api_tennis_draw_failure_never_leaks_key(fake_get):
    fake_get.response = FakeResponse(status_code=403)
    with pytest.raises(TransportError) as exc_info:
        fetch_api_tennis_draw("SECRETKEY", "1214", 2026)
    assert exc_info.value.kind == "HTTP_403"
    assert "SECRETKEY" not in str(exc_info.value)


def test_api_tennis_timeout(fake_get):
    fake_get.exc = transport.requests.exceptions.Timeout("slow")
    with pytest.raises(TransportError) as exc_info:
        fetch_api_tennis_fixtures("KEY123", SLATE)
    assert exc_info.value.kind == "TIMEOUT"
    assert "KEY123" not in str(exc_info.value)


def test_api_tennis_non_200(fake_get):
    fake_get.response = FakeResponse(status_code=401)
    with pytest.raises(TransportError) as exc_info:
        fetch_api_tennis_fixtures("KEY123", SLATE)
    assert exc_info.value.kind == "HTTP_401"
    assert "KEY123" not in str(exc_info.value)


def test_api_tennis_provider_error(fake_get):
    fake_get.response = FakeResponse(json_data={"success": 0})
    with pytest.raises(TransportError) as exc_info:
        fetch_api_tennis_fixtures("KEY123", SLATE)
    assert exc_info.value.kind == "PROVIDER_ERROR"


def test_api_tennis_invalid_json(fake_get):
    fake_get.response = FakeResponse(json_error=True)
    with pytest.raises(TransportError) as exc_info:
        fetch_api_tennis_fixtures("KEY123", SLATE)
    assert exc_info.value.kind == "PARSE"


@pytest.mark.parametrize(
    ("payload", "shape", "category"),
    [
        ({"success": 1}, "NULL", "UNCLASSIFIED"),
        ({"success": 1, "result": None}, "NULL", "UNCLASSIFIED"),
        ({"success": 1, "result": "NO_DATA SECRET_KEY"}, "STRING", "UNCLASSIFIED"),
        ({"success": 1, "result": {"error": "Too many requests SECRET_KEY"}}, "OBJECT", "QUOTA_OR_RATE"),
        ({"success": 1, "result": 42}, "NUMBER", "UNCLASSIFIED"),
        ({"success": 1, "result": False}, "BOOLEAN", "UNCLASSIFIED"),
    ],
)
def test_api_tennis_success_envelope_missing_list_is_safe_and_fail_closed(
    fake_get, payload, shape, category
):
    fake_get.response = FakeResponse(json_data=payload)
    with pytest.raises(TransportError) as err:
        fetch_api_tennis_fixtures("SECRET_KEY", SLATE, clock=lambda: CLOCK)
    assert err.value.kind == "PARSE"
    assert f"shape={shape}" in str(err.value)
    assert f"diagnostic={category}" in str(err.value)
    assert "SECRET_KEY" not in str(err.value)
    assert "NO_DATA" not in str(err.value)


def test_pinnapi_endpoint_and_header(fake_get):
    fake_get.response = FakeResponse(json_data={"events": []})
    fetch_pinnapi_board("KEYXYZ", clock=lambda: CLOCK)
    url, kwargs = fake_get.calls[0]
    assert url == "https://pinnapi.com/kit/v1/markets"
    assert kwargs["params"] == {"sport_id": "2", "event_type": "prematch"}
    assert kwargs["headers"]["x-portal-apikey"] == "KEYXYZ"


def test_pinnapi_never_leaks_key(fake_get):
    fake_get.response = FakeResponse(status_code=403)
    with pytest.raises(TransportError) as exc_info:
        fetch_pinnapi_board("SECRETKEY")
    assert "SECRETKEY" not in str(exc_info.value)
    assert exc_info.value.kind == "HTTP_403"


def test_pinnapi_returns_received_at_and_board(fake_get):
    payload = {"events": [{"event_id": "1"}]}
    fake_get.response = FakeResponse(json_data=payload)
    received_at, board = fetch_pinnapi_board("KEYXYZ", clock=lambda: CLOCK)
    assert received_at == CLOCK
    assert board == payload


def test_pinnapi_non_object_rejected(fake_get):
    fake_get.response = FakeResponse(json_data=["not", "an", "object"])
    with pytest.raises(TransportError) as exc_info:
        fetch_pinnapi_board("KEYXYZ")
    assert exc_info.value.kind == "PARSE"


def test_api_tennis_failed_payload_diagnostic_does_not_leak_provider_text(fake_get):
    fake_get.response = FakeResponse(
        json_data={"success": 0, "error": {"message": "Invalid APIkey KEY123"}},
    )
    with pytest.raises(TransportError) as exc_info:
        fetch_api_tennis_fixtures("KEY123", SLATE)
    assert exc_info.value.kind == "PROVIDER_ERROR"
    assert "diagnostic=AUTH_OR_KEY" in str(exc_info.value)
    assert "KEY123" not in str(exc_info.value)
    assert "Invalid APIkey" not in str(exc_info.value)


def test_api_tennis_failed_payload_quota_and_unknown_categories(fake_get):
    for error, expected in (
        ("Daily quota exceeded ACCOUNT_SECRET", "QUOTA_OR_RATE"),
        ("Subscription expired ACCOUNT_SECRET", "ACCOUNT_OR_PLAN"),
        ("Unidentified server response ACCOUNT_SECRET", "UNCLASSIFIED"),
    ):
        fake_get.response = FakeResponse(json_data={"success": 0, "result": error})
        with pytest.raises(TransportError) as exc_info:
            fetch_api_tennis_fixtures("KEY123", SLATE)
        assert exc_info.value.kind == "PROVIDER_ERROR"
        assert f"diagnostic={expected}" in str(exc_info.value)
        assert "ACCOUNT_SECRET" not in str(exc_info.value)


def test_failed_provider_envelope_categorization_never_promotes_to_failover():
    from sports_edge_tennis.live.provider_failures import classify_provider_failure
    assert classify_provider_failure("PROVIDER_ERROR").failover_eligible is False
    assert transport.api_tennis_failure_category(None) == "INVALID_ENVELOPE"
    assert transport.api_tennis_failure_category({"success": 0}) == "UNCLASSIFIED"


class _HeaderResponse(FakeResponse):
    def __init__(self, status_code, *, headers=None, json_data=None, json_error=False):
        super().__init__(status_code=status_code, json_data=json_data, json_error=json_error)
        self.headers = headers or {}


@pytest.mark.parametrize(
    ("response", "category"),
    [
        (
            _HeaderResponse(403, json_data={"message": "Your subscription has expired"}),
            "ACCOUNT_OR_PLAN",
        ),
        (_HeaderResponse(403, json_data={"detail": "Invalid API key"}), "AUTH_OR_KEY"),
        (_HeaderResponse(403, json_data={"error": "Too many requests"}), "QUOTA_OR_RATE"),
        (
            _HeaderResponse(403, headers={"cf-mitigated": "challenge"}, json_error=True),
            "EDGE_CHALLENGE",
        ),
        (
            _HeaderResponse(403, headers={"Content-Type": "text/html; charset=UTF-8"}),
            "HTML_RESPONSE",
        ),
        (_HeaderResponse(403, json_error=True), "UNCLASSIFIED"),
        (_HeaderResponse(403, json_data={"message": "nope"}), "UNCLASSIFIED"),
    ],
)
def test_pinnapi_non_200_carries_allowlisted_category(fake_get, response, category):
    fake_get.response = response
    with pytest.raises(TransportError) as exc_info:
        fetch_pinnapi_board("SECRETKEY")
    assert exc_info.value.kind == "HTTP_403"
    assert exc_info.value.category == category
    assert "SECRETKEY" not in str(exc_info.value)
    assert "subscription" not in str(exc_info.value).lower()


def test_pinnapi_category_tolerates_response_without_headers(fake_get):
    fake_get.response = FakeResponse(status_code=403)
    with pytest.raises(TransportError) as exc_info:
        fetch_pinnapi_board("SECRETKEY")
    assert exc_info.value.category == "UNCLASSIFIED"


class _ExplodingResponse(FakeResponse):
    """Unexpected response shape: headers and JSON access both blow up."""

    @property
    def headers(self):
        raise RuntimeError("SECRETKEY in a header dump")

    def json(self):
        raise RuntimeError("unexpected decoder failure")


def test_pinnapi_category_failure_never_replaces_transport_error(fake_get):
    fake_get.response = _ExplodingResponse(status_code=403)
    with pytest.raises(TransportError) as exc_info:
        fetch_pinnapi_board("SECRETKEY")
    assert exc_info.value.kind == "HTTP_403"
    assert exc_info.value.category == "UNCLASSIFIED"
    assert "SECRETKEY" not in str(exc_info.value)
