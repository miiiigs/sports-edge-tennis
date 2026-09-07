from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import sports_edge_tennis.live.transport as transport
from sports_edge_tennis.live.transport import (
    TransportError,
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
