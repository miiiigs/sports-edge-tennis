from __future__ import annotations

from datetime import UTC, datetime

import pytest

import sports_edge_tennis.live.oddspapi as oddspapi
from sports_edge_tennis.live.transport import TransportError


class _Response:
    def __init__(self, status_code=200, payload=None, headers=None, content=b"{}"):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = content

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


def test_tournament_odds_can_omit_optional_params(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return _Response(200, [])

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    oddspapi.fetch_oddspapi_tennis_odds_by_tournaments(
        "secret-key",
        tournament_ids=(20102,),
        include_bookmakers=False,
        include_optional_params=False,
    )
    assert calls == [
        (
            "https://api.oddspapi.io/v4/odds-by-tournaments",
            {"apiKey": "secret-key", "tournamentIds": "20102"},
        )
    ]


def test_sanitized_request_shape_encodes_plus_without_api_key():
    params = oddspapi.build_oddspapi_tournament_odds_params(
        api_key="super-secret-key",
        tournament_ids=(20102,),
    )
    shape = oddspapi.sanitized_request_shape(
        endpoint="/v4/odds-by-tournaments",
        params=params,
        base_url="https://api.oddspapi.io",
    )
    assert shape["endpoint"] == "/v4/odds-by-tournaments"
    assert "apiKey" not in shape["params"]
    assert "super-secret-key" not in shape["encoded_query"]
    assert "pinnacle%2B30" in shape["encoded_query"]


def test_fixture_odds_transport_uses_minimal_fixture_request(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return _Response(200, {"fixtureId": "id1202010274891198"})

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    received_at, payload = oddspapi.fetch_oddspapi_tennis_fixture_odds(
        "secret-key",
        fixture_id="id1202010274891198",
        clock=lambda: datetime(2026, 9, 22, 1, tzinfo=UTC),
    )
    assert received_at == datetime(2026, 9, 22, 1, tzinfo=UTC)
    assert payload["fixtureId"] == "id1202010274891198"
    assert calls == [
        (
            "https://api.oddspapi.io/v4/odds",
            {"apiKey": "secret-key", "fixtureId": "id1202010274891198"},
        )
    ]


def test_account_usage_parses_subscription_request_budget(monkeypatch):
    def fake_get(url, params, timeout):
        assert url.endswith("/v4/account")
        assert params["apiKey"] == "secret-key"
        return _Response(
            200,
            {
                "subscription": {
                    "request_limit": 5000,
                    "request_count": 123,
                }
            },
        )

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    usage = oddspapi.fetch_oddspapi_account("secret-key")
    assert usage.request_limit == 5000
    assert usage.request_count == 123
    assert usage.remaining == 4877


def test_request_budget_fails_closed_before_billable_call(monkeypatch):
    def fake_get(*args, **kwargs):
        raise AssertionError("billable request should be blocked")

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    with pytest.raises(TransportError) as exc:
        oddspapi.fetch_oddspapi_tennis_fixtures(
            "secret-key",
            from_at=datetime(2026, 9, 21, tzinfo=UTC),
            to_at=datetime(2026, 9, 22, tzinfo=UTC),
            budget=oddspapi.OddspapiRequestBudget(
                monthly_limit=5000,
                monthly_used=4501,
                monthly_reserve=500,
            ),
        )
    assert exc.value.kind == "ODDSPAPI_QUOTA_BUDGET"


def test_request_budget_is_cumulative_across_sequential_calls(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        return _Response(200, [])

    monkeypatch.setattr(oddspapi.requests, "get", fake_get)
    budget = oddspapi.OddspapiRequestBudget(
        monthly_limit=5000,
        monthly_used=4499,
        monthly_reserve=500,
    )
    oddspapi.fetch_oddspapi_tennis_fixtures(
        "secret-key",
        from_at=datetime(2026, 9, 21, tzinfo=UTC),
        to_at=datetime(2026, 9, 22, tzinfo=UTC),
        budget=budget,
    )
    assert budget.session_used == 1
    with pytest.raises(TransportError) as exc:
        oddspapi.fetch_oddspapi_tennis_odds_by_tournaments(
            "secret-key",
            tournament_ids=(1,),
            budget=budget,
        )
    assert exc.value.kind == "ODDSPAPI_QUOTA_BUDGET"
    assert len(calls) == 1


def test_request_budget_allows_normal_two_call_session(monkeypatch):
    monkeypatch.setattr(
        oddspapi.requests,
        "get",
        lambda *a, **k: _Response(200, []),
    )
    budget = oddspapi.OddspapiRequestBudget(
        monthly_limit=5000,
        monthly_used=3,
        monthly_reserve=500,
    )
    oddspapi.fetch_oddspapi_tennis_fixtures(
        "secret-key",
        from_at=datetime(2026, 9, 21, tzinfo=UTC),
        to_at=datetime(2026, 9, 22, tzinfo=UTC),
        budget=budget,
    )
    oddspapi.fetch_oddspapi_tennis_odds_by_tournaments(
        "secret-key",
        tournament_ids=(1,),
        budget=budget,
    )
    assert budget.session_used == 2


def test_request_budget_counts_processed_http_error_attempt(monkeypatch):
    monkeypatch.setattr(
        oddspapi.requests,
        "get",
        lambda *a, **k: _Response(403, {"code": "FORBIDDEN"}),
    )
    budget = oddspapi.OddspapiRequestBudget(
        monthly_limit=5000,
        monthly_used=3,
        monthly_reserve=500,
    )
    with pytest.raises(TransportError):
        oddspapi.fetch_oddspapi_tennis_fixtures(
            "secret-key",
            from_at=datetime(2026, 9, 21, tzinfo=UTC),
            to_at=datetime(2026, 9, 22, tzinfo=UTC),
            budget=budget,
        )
    assert budget.session_used == 1


def test_429_has_stable_secret_safe_classifier(monkeypatch):
    monkeypatch.setattr(
        oddspapi.requests,
        "get",
        lambda *a, **k: _Response(429, {"code": "REQUEST_LIMIT_EXCEEDED"}),
    )
    with pytest.raises(TransportError) as exc:
        oddspapi.fetch_oddspapi_tennis_fixtures(
            "super-secret-key",
            from_at=datetime(2026, 9, 21, tzinfo=UTC),
            to_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
    assert exc.value.kind == "HTTP_429"
    assert "super-secret-key" not in str(exc.value)


def test_http_error_details_are_allowlisted_and_secret_safe(monkeypatch):
    monkeypatch.setattr(
        oddspapi.requests,
        "get",
        lambda *a, **k: _Response(
            403,
            {
                "code": "BOOKMAKER_NOT_AVAILABLE",
                "message": "Bookmaker is not enabled",
                "details": {"message": "Use a subscribed bookmaker", "apiKey": "nested-secret"},
                "retryMs": 1000,
                "apiKey": "super-secret-key",
                "url": "https://api.oddspapi.io/v4/odds-by-tournaments?apiKey=super-secret-key",
                "headers": {"Authorization": "super-secret-key"},
            },
            headers={"content-type": "application/json"},
            content=b'{"code":"BOOKMAKER_NOT_AVAILABLE"}',
        ),
    )
    with pytest.raises(TransportError) as exc:
        oddspapi.fetch_oddspapi_tennis_odds_by_tournaments(
            "super-secret-key",
            tournament_ids=(1,),
        )
    assert exc.value.kind == "HTTP_403"
    assert exc.value.details["status_code"] == 403
    assert exc.value.details["content_type"] == "application/json"
    assert exc.value.details["response_size_bytes"] == len(
        b'{"code":"BOOKMAKER_NOT_AVAILABLE"}'
    )
    assert exc.value.details["provider_code"] == "BOOKMAKER_NOT_AVAILABLE"
    assert exc.value.details["provider_message"] == "Bookmaker is not enabled"
    assert exc.value.details["provider_details"] == {
        "message": "Use a subscribed bookmaker"
    }
    assert exc.value.details["retry_ms"] == 1000
    assert "apiKey" not in exc.value.details["provider"]
    assert "url" not in exc.value.details["provider"]
    assert "super-secret-key" not in str(exc.value)
    assert "super-secret-key" not in repr(exc.value.details)


def test_cooldown_helper_uses_injected_sleeper():
    sleeps = []
    oddspapi.wait_for_oddspapi_cooldown(1.25, sleeper=sleeps.append)
    assert sleeps == [1.25]


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
