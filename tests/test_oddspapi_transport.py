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


def test_tournament_odds_uses_single_bookmaker_and_enforces_limits(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return _Response(200, [])

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    oddspapi.fetch_oddspapi_tennis_odds_by_tournaments(
        "secret-key",
        tournament_ids=(3, 3, 7, 9),
        bookmaker="pinnacle+30",
    )
    assert len(calls) == 1
    assert calls[0][1]["tournamentIds"] == "3,7,9"
    assert calls[0][1]["bookmaker"] == "pinnacle+30"
    assert "bookmakers" not in calls[0][1]

    with pytest.raises(ValueError, match="at most"):
        oddspapi.fetch_oddspapi_tennis_odds_by_tournaments(
            "secret-key",
            tournament_ids=tuple(range(6)),
            bookmaker="pinnacle+30",
        )


def test_tournament_odds_for_books_loops_per_book_and_chunks(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        return _Response(200, [])

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    monkeypatch.setattr(oddspapi.time, "sleep", lambda *_: None)
    _received, _rows, used = oddspapi.fetch_oddspapi_tennis_odds_for_books(
        "k",
        tournament_ids=(1, 2, 3, 4, 5, 6),
        bookmakers=("pinnacle+30", "1xbet"),
    )
    # 6 ids / 5 per request => 2 chunks per book * 2 books = 4 requests.
    assert used == 4
    assert [call["bookmaker"] for call in calls] == [
        "pinnacle+30",
        "pinnacle+30",
        "1xbet",
        "1xbet",
    ]


def test_rate_limited_request_retries(monkeypatch):
    attempts = {"n": 0}

    class _RateResponse:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, params, timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _RateResponse(
                429,
                {
                    "error": {
                        "message": "rate limited",
                        "details": "Please wait 0.5 seconds before making another request",
                    }
                },
            )
        return _RateResponse(200, [{"fixtureId": "f1"}])

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    monkeypatch.setattr(oddspapi.time, "sleep", lambda *_: None)
    _received, rows = oddspapi.fetch_oddspapi_tennis_fixtures(
        "k",
        from_at=datetime(2026, 9, 21, tzinfo=UTC),
        to_at=datetime(2026, 9, 22, tzinfo=UTC),
    )
    assert attempts["n"] == 2
    assert rows == [{"fixtureId": "f1"}]


def test_merge_odds_books_combines_book_payloads():
    fixture = {
        "fixtureId": "f1",
        "sportId": 12,
        "participant1Name": "A",
        "participant2Name": "B",
    }
    row_a = {"fixtureId": "f1", "bookmakerOdds": {"pinnacle+30": {"markets": {}}}}
    row_b = {"fixtureId": "f1", "bookmakerOdds": {"1xbet": {"markets": {}}}}
    merged = oddspapi.merge_oddspapi_odds_books([fixture], [row_a, row_b])
    assert len(merged) == 1
    assert set(merged[0]["bookmakerOdds"]) == {"pinnacle+30", "1xbet"}


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


def _trusted_board_row(fixture_id: str = "f1") -> dict:
    return {
        "fixtureId": fixture_id,
        "sportId": 12,
        "tournamentId": 44,
        "participant1Id": 101,
        "participant1Name": "Player One",
        "participant2Id": 202,
        "participant2Name": "Player Two",
        "startTime": "2026-09-21T13:00:00Z",
        "bookmakerOdds": {"pinnacle+30": {"markets": {}}},
    }


def test_merge_retains_board_rows_for_trusted_cached_identity():
    rows = oddspapi.merge_oddspapi_odds_books(
        [],
        [_trusted_board_row("f1")],
        trusted_fixture_ids=("f1",),
    )
    assert len(rows) == 1
    assert rows[0]["fixtureId"] == "f1"
    assert rows[0]["participant1Id"] == 101
    assert rows[0]["participant2Id"] == 202
    assert rows[0]["bookmakerOdds"] == {"pinnacle+30": {"markets": {}}}


def test_merge_drops_board_rows_without_trusted_identity():
    rows = oddspapi.merge_oddspapi_odds_books(
        [],
        [_trusted_board_row("f1")],
        trusted_fixture_ids=(),
    )
    assert rows == []


def test_merge_trusted_identity_only_retains_known_fixtures():
    rows = oddspapi.merge_oddspapi_odds_books(
        [],
        [_trusted_board_row("f1"), _trusted_board_row("f2")],
        trusted_fixture_ids=("f1",),
    )
    assert [row["fixtureId"] for row in rows] == ["f1"]


def test_merge_repeated_board_snapshot_stays_single_row():
    rows = oddspapi.merge_oddspapi_odds_books(
        [],
        [_trusted_board_row("f1"), _trusted_board_row("f1")],
        trusted_fixture_ids=("f1",),
    )
    assert len(rows) == 1
    assert set(rows[0]["bookmakerOdds"]) == {"pinnacle+30"}


def test_merge_board_prices_preserves_fixture_discovery_identity():
    fixture = {
        "fixtureId": "f1",
        "sportId": 12,
        "tournamentId": 44,
        "participant1Id": 101,
        "participant1Name": "Player One",
        "participant2Id": 202,
        "participant2Name": "Player Two",
        "startTime": "2026-09-21T13:00:00Z",
        "externalProviders": {"betradarId": 98765},
        "hasOdds": True,
    }
    # Tournament-board rows are allowed to be price-centric and omit names,
    # sport, start time, and external provider links.
    board = {
        "fixtureId": "f1",
        "tournamentId": 44,
        "bookmakerOdds": {"pinnacle+30": {"markets": {}}},
    }
    rows = oddspapi.merge_oddspapi_fixture_odds([fixture], [board])
    assert len(rows) == 1
    merged = rows[0]
    assert merged["participant1Name"] == "Player One"
    assert merged["participant2Name"] == "Player Two"
    assert merged["sportId"] == 12
    assert merged["startTime"] == "2026-09-21T13:00:00Z"
    assert merged["externalProviders"]["betradarId"] == 98765
    assert "bookmakerOdds" in merged


def test_merge_drops_board_rows_outside_discovered_fixture_scope():
    rows = oddspapi.merge_oddspapi_fixture_odds(
        [{"fixtureId": "f1"}],
        [{"fixtureId": "f2", "bookmakerOdds": {}}],
    )
    assert rows == []
