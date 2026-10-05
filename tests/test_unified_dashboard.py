"""Unified Sports Edge dashboard: shell, Tennis aliases, Football integration.

These tests are deliberately offline. Football is a separate runtime, so the
client is exercised through its injectable transport and the page loaders are
monkeypatched. No test ever points the client at a live Football deployment.
"""

from __future__ import annotations

import requests
from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard import football_client as fb
from sports_edge_tennis.dashboard.production_app import app

TENNIS_PAGES = (
    "/today",
    "/value",
    "/performance",
    "/bet-history",
    "/value-bet-results",
    "/model-pick-results",
)
TENNIS_ALIASES = (
    "/tennis",
    "/tennis/performance",
    "/tennis/history",
    "/tennis/system",
)
FOOTBALL_PAGES = (
    "/football",
    "/football/performance",
    "/football/history",
    "/football/system",
)


def _ok(data):
    return fb.FootballResponse(ok=True, state="OK", message="", data=data)


def _degraded(state="UNAVAILABLE", message="Football is temporarily unavailable."):
    return fb.FootballResponse(ok=False, state=state, message=message)


CANDIDATES = {
    "rows": [
        {
            "market_family": "asian_handicap",
            "home_team_name": "Arsenal",
            "away_team_name": "Chelsea",
            "competition_name": "Premier League",
            "kickoff_local": "Oct 5, 8:00 PM",
            "model_probability": 0.61,
            "fair_odds": 1.9,
            "best_available_price": 2.05,
            "edge": 0.05,
            "decision_group": "bet",
            "current_decision_state": "BET",
            "line": -0.5,
            "bet_label": "Arsenal -0.5",
        },
        {
            "market_family": "corner_total",
            "home_team_name": "Leeds",
            "away_team_name": "Spurs",
            "competition_name": "Premier League",
            "kickoff_local": "Oct 5, 6:00 PM",
            "model_probability": 0.55,
            "fair_odds": 1.8,
            "best_available_price": 1.7,
            "edge": -0.02,
            "decision_group": "no_bet",
            "current_decision_state": "NO_BET",
            "line": 9.5,
            "bet_label": "Under 9.5 Corners",
        },
    ],
    "system_status": {},
}

PERFORMANCE = {
    "current_paper_summary": {
        "positions": 12,
        "settled_positions": 9,
        "wins": 5,
        "losses": 4,
        "voids": 0,
        "strike_rate": 0.55,
        "net_units": 1.2,
        "roi": 0.08,
        "average_clv": 0.01,
    },
    "market_breakdown": [
        {"key": "asian_handicap", "label": "Asian Handicap", "bets": 6, "settled": 5, "wins": 3, "losses": 2, "strike_rate": 0.6, "net_units": 0.9, "roi": 0.1, "low_sample": False},
        {"key": "corner_total", "label": "Corners", "bets": 6, "settled": 4, "wins": 2, "losses": 2, "strike_rate": 0.5, "net_units": 0.3, "roi": 0.05, "low_sample": True},
    ],
    "window_rows": [{"window": "7D", "suggested_bets": 3, "settled": 2, "wins": 1, "losses": 1, "roi": 0.04, "net_units": 0.2, "clv": 0.01}],
    "recent_paper_bets": [
        {"fixture_label": "A vs B", "competition_name": "PL", "kickoff_local": "2026-10-01 20:00", "market_label": "Asian Handicap", "bet_label": "A -0.5", "entry_odds": 1.95, "result_label": "WON", "net_pnl": 0.95, "clv_pct": 0.02}
    ],
    "client_history": {"normalized_count": 1},
    "current_streak": "W1",
    "clv_status_label": "+1.00%",
}

SYSTEM_STATUS = {
    "fixture_feed": {"state": "CONNECTED", "last_successful_sync_at": "2026-10-04T23:08:49Z"},
    "prediction_engine": {"state": "READY", "last_prediction_generated_at": "2026-10-04T23:00:00Z"},
    "market_sync": {"state": "SYNC_DISABLED"},
    "storage": {"state": "persistent"},
}

HEALTH = {
    "status": "ok",
    "fixture_provider_state": "CONNECTED",
    "last_fixture_sync_at": "2026-10-04T23:08:49Z",
    "market_sync_state": "SYNC_DISABLED",
    "prediction_snapshot_ready": True,
    "storage_mode": "persistent",
}


def _patch_football(monkeypatch, *, candidates=None, performance=None, status=None, health=None):
    monkeypatch.setattr(fb, "load_candidates", lambda: candidates if candidates is not None else _ok(CANDIDATES))
    monkeypatch.setattr(fb, "load_performance", lambda: performance if performance is not None else _ok(PERFORMANCE))
    monkeypatch.setattr(fb, "load_system_status", lambda: status if status is not None else _ok(SYSTEM_STATUS))
    monkeypatch.setattr(fb, "load_health", lambda: health if health is not None else _ok(HEALTH))


def _client(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    return TestClient(app)


# --------------------------------------------------------------- tennis safety


def test_existing_tennis_pages_still_load(monkeypatch):
    client = _client(monkeypatch)
    for path in TENNIS_PAGES:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, path


def test_tennis_namespace_aliases_load(monkeypatch):
    _patch_football(monkeypatch)
    client = _client(monkeypatch)
    for path in TENNIS_ALIASES:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, path


def test_tennis_alias_matches_legacy_page(monkeypatch):
    client = _client(monkeypatch)
    alias = client.get("/tennis", follow_redirects=False)
    legacy = client.get("/today", follow_redirects=False)
    assert alias.status_code == 200
    assert "Today's Picks" in alias.text
    assert len(alias.text) == len(legacy.text)


# ------------------------------------------------------------------ shared ui


def test_sport_selector_shows_active_sport(monkeypatch):
    _patch_football(monkeypatch)
    client = _client(monkeypatch)

    tennis = client.get("/tennis")
    assert 'href="/tennis" class="sport-link is-active"' in tennis.text
    assert 'href="/football" class="sport-link"' in tennis.text

    football = client.get("/football")
    assert 'href="/football" class="sport-link is-active"' in football.text


def test_football_navigation_links_are_correct(monkeypatch):
    _patch_football(monkeypatch)
    client = _client(monkeypatch)
    response = client.get("/football")
    for label, href in (
        ("Picks", "/football"),
        ("Performance", "/football/performance"),
        ("History", "/football/history"),
        ("System", "/football/system"),
    ):
        assert f'href="{href}"' in response.text
        assert f">{label}<" in response.text


def test_tennis_navigation_labels_are_preserved(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/tennis")
    for label in ("Today", "Value", "Model Picks", "Value Bets", "Performance"):
        assert f">{label}<" in response.text


# ------------------------------------------------------------ football success


def test_football_pages_load_with_valid_response(monkeypatch):
    _patch_football(monkeypatch)
    client = _client(monkeypatch)
    picks = client.get("/football")
    assert picks.status_code == 200
    assert "Asian Handicap" in picks.text
    assert "Corners" in picks.text
    assert "Arsenal -0.5" in picks.text

    performance = client.get("/football/performance")
    assert performance.status_code == 200
    assert "Football Performance" in performance.text

    history = client.get("/football/history")
    assert history.status_code == 200
    assert "A vs B" in history.text

    system = client.get("/football/system")
    assert system.status_code == 200
    assert "CONNECTED" in system.text


def test_football_picks_separates_model_and_bet_decision(monkeypatch):
    _patch_football(monkeypatch)
    client = _client(monkeypatch)
    response = client.get("/football")
    # Model view and bet decision are distinct columns; a no-bet row stays no-bet.
    assert "Model view" in response.text
    assert "Bet decision" in response.text
    assert "No bet" in response.text
    assert "Bet" in response.text


# ------------------------------------------------------------ failure isolation


def test_football_timeout_creates_degraded_state(monkeypatch):
    def raise_timeout(url, headers, timeout):
        raise requests.Timeout()

    monkeypatch.setattr(fb, "_http_get", raise_timeout)
    monkeypatch.setenv(fb.FOOTBALL_BASE_URL_ENV, "https://football.example.test")
    response = fb.request_get("/api/candidates")
    assert response.ok is False
    assert response.state == "TIMEOUT"


def test_football_500_creates_degraded_state(monkeypatch):
    monkeypatch.setattr(fb, "_http_get", lambda *a, **k: fb._HttpResult(500, "server error"))
    monkeypatch.setenv(fb.FOOTBALL_BASE_URL_ENV, "https://football.example.test")
    response = fb.request_get("/api/candidates")
    assert response.ok is False
    assert response.state == "HTTP_ERROR"


def test_football_401_is_reported_honestly(monkeypatch):
    monkeypatch.setattr(fb, "_http_get", lambda *a, **k: fb._HttpResult(401, "unauthorized"))
    monkeypatch.setenv(fb.FOOTBALL_BASE_URL_ENV, "https://football.example.test")
    response = fb.request_get("/api/candidates")
    assert response.ok is False
    assert response.state == "AUTH_REQUIRED"


def test_malformed_football_json_fails_safely(monkeypatch):
    monkeypatch.setenv(fb.FOOTBALL_BASE_URL_ENV, "https://football.example.test")
    for body in ("<html>not json</html>", "[]", '"just a string"'):
        monkeypatch.setattr(fb, "_http_get", lambda *a, _body=body, **k: fb._HttpResult(200, _body))
        response = fb.request_get("/api/candidates")
        assert response.ok is False
        assert response.state == "MALFORMED"


def test_football_outage_does_not_break_tennis(monkeypatch):
    _patch_football(monkeypatch, candidates=_degraded(), performance=_degraded(), status=_degraded(), health=_degraded())
    client = _client(monkeypatch)

    football = client.get("/football")
    assert football.status_code == 200
    assert "temporarily unavailable" in football.text

    tennis = client.get("/tennis")
    assert tennis.status_code == 200
    assert "Today's Picks" in tennis.text
    assert "temporarily unavailable" not in tennis.text


def test_unconfigured_football_does_not_call_network(monkeypatch):
    monkeypatch.delenv(fb.FOOTBALL_BASE_URL_ENV, raising=False)

    def explode(*args, **kwargs):
        raise AssertionError("no network call may occur when Football is unconfigured")

    monkeypatch.setattr(fb, "_http_get", explode)
    response = fb.request_get("/api/candidates")
    assert response.ok is False
    assert response.state == "NOT_CONFIGURED"
    client = _client(monkeypatch)
    assert client.get("/football").status_code == 200


# ------------------------------------------------------------------- safety


def test_secrets_and_backend_url_are_never_rendered(monkeypatch):
    sentinel_url = "https://football-internal-sentinel.example.test"
    sentinel_token = "token-sentinel-should-never-render"
    monkeypatch.setenv(fb.FOOTBALL_BASE_URL_ENV, sentinel_url)
    monkeypatch.setenv(fb.FOOTBALL_TOKEN_ENV, sentinel_token)
    _patch_football(monkeypatch)
    client = _client(monkeypatch)

    for path in (*FOOTBALL_PAGES, "/overview"):
        body = client.get(path).text
        assert sentinel_url not in body, path
        assert sentinel_token not in body, path


def test_state_changing_endpoints_are_not_proxied(monkeypatch):
    assert fb.STATE_CHANGING_PATHS.isdisjoint(fb.ALLOWED_GET_PATHS)
    assert "/api/fixtures/refresh" in fb.STATE_CHANGING_PATHS

    def explode(*args, **kwargs):
        raise AssertionError("state-changing Football endpoint must never be called")

    monkeypatch.setattr(fb, "_http_get", explode)
    monkeypatch.setenv(fb.FOOTBALL_BASE_URL_ENV, "https://football.example.test")
    response = fb.request_get("/api/fixtures/refresh")
    assert response.ok is False
    assert response.state == "NOT_ALLOWED"


def test_football_client_is_get_only():
    source = fb.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "requests.post" not in text
    assert "requests.put" not in text
    assert "requests.delete" not in text
    assert "requests.patch" not in text


def test_invalid_football_path_is_refused_before_transport(monkeypatch):
    monkeypatch.setenv(fb.FOOTBALL_BASE_URL_ENV, "https://football.example.test")

    def explode(*args, **kwargs):
        raise AssertionError("unknown path must not reach transport")

    monkeypatch.setattr(fb, "_http_get", explode)
    assert fb.request_get("/admin/secret").state == "NOT_ALLOWED"


# ------------------------------------------------------------------- overview


def test_overview_keeps_sports_independent(monkeypatch):
    _patch_football(monkeypatch)
    client = _client(monkeypatch)
    response = client.get("/overview")
    assert response.status_code == 200
    assert "Tennis" in response.text
    assert "Football" in response.text
    # Each sport is independent; the page says so explicitly and offers no
    # combined profitability or bankroll figure.
    assert "no combined" in response.text.lower()
    assert "total sports edge" not in response.text.lower()
