from __future__ import annotations

from datetime import UTC, datetime

import pytest

import sports_edge_tennis.live.oddspapi as oddspapi
from sports_edge_tennis.live.transport import TransportError


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_fixture_transport_uses_tennis_pregame_filters(monkeypatch):
    seen = {}

    def fake_get(url, params, timeout):
        seen.update(url=url, params=params, timeout=timeout)
        return _Response(200, [{"fixtureId": "f1"}])

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    received_at, rows = oddspapi.fetch_oddspapi_tennis_fixtures(
        "secret-key",
        from_at=datetime(2026, 9, 21, tzinfo=UTC),
        to_at=datetime(2026, 9, 24, tzinfo=UTC),
        clock=lambda: datetime(2026, 9, 21, 1, tzinfo=UTC),
    )
    assert received_at == datetime(2026, 9, 21, 1, tzinfo=UTC)
    assert rows == [{"fixtureId": "f1"}]
    assert seen["url"].endswith("/v4/fixtures")
    assert seen["params"]["sportId"] == 12
    assert seen["params"]["statusId"] == 0
    assert seen["params"]["hasOdds"] == "true"
    assert seen["params"]["bookmakers"] == "pinnacle+30"
    assert seen["params"]["apiKey"] == "secret-key"


def test_tournament_odds_batches_all_ids_in_one_call(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return _Response(200, [])

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    oddspapi.fetch_oddspapi_tennis_odds_by_tournaments(
        "secret-key",
        tournament_ids=(3, 3, 7, 9),
    )
    assert len(calls) == 1
    assert calls[0][1]["tournamentIds"] == "3,7,9"
    assert (
        calls[0][1]["bookmakers"]
        == "pinnacle+30,1xbet,unibet,bet365"
    )


def test_transport_errors_never_include_api_key(monkeypatch):
    monkeypatch.setattr(
        oddspapi.requests,
        "get",
        lambda *a, **k: _Response(401, {}),
    )
    with pytest.raises(TransportError) as exc:
        oddspapi.fetch_oddspapi_tennis_fixtures(
            "super-secret-key",
            from_at=datetime(2026, 9, 21, tzinfo=UTC),
            to_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
    assert "super-secret-key" not in str(exc.value)
