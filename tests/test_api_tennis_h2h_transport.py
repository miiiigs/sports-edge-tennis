from __future__ import annotations

from datetime import datetime, timezone

import pytest

import sports_edge_tennis.live.transport as transport
from sports_edge_tennis.live.transport import TransportError, fetch_api_tennis_h2h


CLOCK = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


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
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_api_tennis_h2h_uses_documented_parameters(monkeypatch):
    fake = FakeRequestsGet(
        FakeResponse(json_data={"success": 1, "result": {"H2H": [], "firstPlayerResults": [], "secondPlayerResults": []}})
    )
    monkeypatch.setattr(transport.requests, "get", fake)

    received_at, result = fetch_api_tennis_h2h(
        "KEY123",
        "101",
        "202",
        clock=lambda: CLOCK,
    )

    url, kwargs = fake.calls[0]
    assert url.endswith("/tennis/")
    assert kwargs["params"] == {
        "method": "get_H2H",
        "APIkey": "KEY123",
        "first_player_key": "101",
        "second_player_key": "202",
    }
    assert received_at == CLOCK
    assert result["H2H"] == []


def test_api_tennis_h2h_failure_never_leaks_key(monkeypatch):
    fake = FakeRequestsGet(FakeResponse(status_code=403))
    monkeypatch.setattr(transport.requests, "get", fake)

    with pytest.raises(TransportError) as exc_info:
        fetch_api_tennis_h2h("SECRETKEY", "101", "202")

    assert exc_info.value.kind == "HTTP_403"
    assert "SECRETKEY" not in str(exc_info.value)


def test_api_tennis_h2h_requires_result_object(monkeypatch):
    fake = FakeRequestsGet(FakeResponse(json_data={"success": 1, "result": []}))
    monkeypatch.setattr(transport.requests, "get", fake)

    with pytest.raises(TransportError) as exc_info:
        fetch_api_tennis_h2h("KEY123", "101", "202")

    assert exc_info.value.kind == "PARSE"


def test_api_tennis_h2h_rejects_same_player_without_request(monkeypatch):
    fake = FakeRequestsGet(FakeResponse(json_data={"success": 1, "result": {}}))
    monkeypatch.setattr(transport.requests, "get", fake)

    with pytest.raises(ValueError):
        fetch_api_tennis_h2h("KEY123", "101", "101")

    assert fake.calls == []
