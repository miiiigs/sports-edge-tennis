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


# --------------------------------------------------------------------------
# Contract-faithful Football fixtures.
#
# These mirror the real ``sports-edge-ai`` JSON shapes (validated against
# ``services.get_betting_desk_payload`` / ``get_dashboard_payload``,
# ``paper_trading_performance`` and ``_build_system_status``). The Tennis tests
# stay offline: the Football repository is never imported, only mirrored here.
# --------------------------------------------------------------------------

# Live ``GET /api/candidates`` (``get_candidates_payload`` -> desk payload). The
# canonical collection is the top-level ``"rows"`` list.
CANDIDATES = {
    "connected": True,
    "reason": "Current desk rows are staged as provider market data becomes available.",
    "quota_waiting_reset": False,
    "quota_state": "AVAILABLE",
    "rows": [
        {
            "fixture_id": "api-football:100",
            "competition_name": "Premier League",
            "kickoff_utc": "2026-10-05T12:00:00+00:00",
            "kickoff_local": "2026-10-05 20:00",
            "kickoff_time_local": "20:00",
            "home_team_name": "Arsenal",
            "away_team_name": "Chelsea",
            "market_family": "asian_handicap",
            "market_label": "Asian Handicap",
            "selection": "HOME",
            "selection_label": "Arsenal",
            "bet_label": "Arsenal -0.5",
            "line": -0.5,
            "model_probability": 0.61,
            "fair_odds": 1.90,
            "model_fair_decimal_price": 1.90,
            "best_offer_odds": 2.05,
            "best_available_price": 2.05,
            "edge": 0.05,
            "settlement_aware_edge": 0.05,
            "decision": "PRELIMINARY_CANDIDATE",
            "desk_decision": "BET",
            "current_decision_state": "BET",
            "decision_group": "bet",
            "reason": "Qualified",
        },
        {
            "fixture_id": "api-football:101",
            "competition_name": "Premier League",
            "kickoff_utc": "2026-10-05T10:00:00+00:00",
            "kickoff_local": "2026-10-05 18:00",
            "kickoff_time_local": "18:00",
            "home_team_name": "Leeds",
            "away_team_name": "Spurs",
            "market_family": "corner_total",
            "market_label": "Corners",
            "selection": "UNDER",
            "selection_label": "Under 9.5",
            "bet_label": "Under 9.5 Corners",
            "line": 9.5,
            "model_probability": 0.55,
            "fair_odds": 1.80,
            "model_fair_decimal_price": 1.80,
            "best_offer_odds": 1.70,
            "best_available_price": 1.70,
            "edge": -0.02,
            "settlement_aware_edge": -0.02,
            "decision": "NO_BET",
            "desk_decision": "NO_BET_VALUE",
            "current_decision_state": "NO_BET_VALUE",
            "decision_group": "no_bet",
            "reason": "Insufficient value",
        },
    ],
    "visible_rows": [],
    "pagination": {"page": 1, "total_items": 2, "total_pages": 1},
    "preliminary_candidates": [],
    "blocked_candidates": [],
    "no_bet_candidates": [],
    "supported_markets": ["asian_handicap", "corner_total"],
    "research_markets": ["1x2", "btts", "goals_total"],
    "blocking_reasons": {},
    "summary": {"rows": 2, "bets": 1, "no_bets": 1, "blocked": 0},
    "system_status": {},
}

# Client-first dashboard payload shape (``get_dashboard_payload``) with
# ``today_desk_rows``. These rows use ``current_decision_group``/``desk_label``
# and only the humanized ``market_label`` plus ``market_odds``.
CANDIDATES_DASHBOARD = {
    "today_desk_rows": [
        {
            "fixture_id": "api-football:200",
            "competition_name": "Championship",
            "kickoff_utc": "2026-10-05T18:00:00+00:00",
            "kickoff_local": "2026-10-05 18:00",
            "kickoff_time_local": "18:00",
            "home_team_name": "Leeds",
            "away_team_name": "Spurs",
            "market_label": "Asian Handicap",
            "bet_label": "Leeds +0.5",
            "selection_label": "Leeds",
            "model_probability": 0.61,
            "market_odds": 2.05,
            "fair_odds": 1.90,
            "edge": 0.05,
            "desk_label": "BET",
            "current_decision_state": "BET",
            "current_decision_group": "bet",
            "desk_note": "Qualified",
        },
        {
            "fixture_id": "api-football:201",
            "competition_name": "Championship",
            "kickoff_utc": "2026-10-05T19:00:00+00:00",
            "kickoff_local": "2026-10-05 19:00",
            "kickoff_time_local": "19:00",
            "home_team_name": "Watford",
            "away_team_name": "Norwich",
            "market_label": "Corners",
            "bet_label": "Under 9.5 Corners",
            "selection_label": "Under 9.5",
            "model_probability": 0.55,
            "market_odds": 1.70,
            "fair_odds": 1.80,
            "edge": -0.02,
            "desk_label": "NO BET",
            "current_decision_state": "NO_BET_VALUE",
            "current_decision_group": "no_bet",
            "desk_note": "Insufficient value",
        },
    ],
    "visible_today_desk_rows": [],
    "today_desk_pagination": {"page": 1, "total_items": 2, "total_pages": 1},
    "current_slate_rows": [],
    "current_slate_summary": {},
    "other_decision_rows": [],
    "client_match_cards": [],
    "system_status": {},
}

PERFORMANCE = {
    # ``current_paper_summary`` is ``paper_trading_performance`` output. All
    # percentage-like values are fractional (0.55 == 55%).
    "current_paper_summary": {
        "positions": 12,
        "settled_positions": 9,
        "pending_positions": 3,
        "wins": 5,
        "losses": 4,
        "pushes": 1,
        "half_wins": 1,
        "half_losses": 0,
        "voids": 0,
        "strike_rate": 0.55,
        "net_units": 1.2,
        "roi": 0.08,
        "average_clv": 0.01,
        "clv_sample": 7,
        "clv_coverage": 0.78,
    },
    "market_breakdown": [
        {"key": "asian_handicap", "label": "Asian Handicap", "bets": 6, "settled": 5, "wins": 3, "losses": 2, "strike_rate": 0.6, "net_units": 0.9, "roi": 0.1, "low_sample": False},
        {"key": "corner_total", "label": "Corners", "bets": 6, "settled": 4, "wins": 2, "losses": 2, "strike_rate": 0.5, "net_units": 0.3, "roi": 0.05, "low_sample": True},
    ],
    "window_rows": [
        {"window": "7D", "suggested_bets": 3, "settled": 2, "wins": 1, "losses": 1, "strike_rate": 0.5, "average_odds": 2.1, "roi": 0.04, "net_units": 0.2, "clv": 0.01}
    ],
    # ``recent_paper_bets`` is ``_paper_position_display_rows`` output.
    "recent_paper_bets": [
        {
            "fixture_id": "api-football:300",
            "fixture_label": "A vs B",
            "competition_name": "PL",
            "kickoff_local": "2026-10-01 20:00",
            "market": "asian_handicap",
            "market_label": "Asian Handicap",
            "bet_label": "A -0.5",
            "selection_label": "A",
            "entry_odds": 1.95,
            "trade_state": "WON",
            "result_label": "WON",
            "net_pnl": 0.95,
            "clv_pct": 0.02,
        }
    ],
    "client_history": {
        "available": True,
        "normalized_count": 1,
        "recent_settled": [],
        "open_positions": [],
    },
    "current_streak": "W1",
    "clv_status_label": "+1.00%",
}

SYSTEM_STATUS = {
    "fixture_feed": {"state": "CONNECTED", "last_successful_sync_at": "2026-10-04T23:08:49Z", "last_sync_at": "2026-10-04T23:08:49Z"},
    "prediction_engine": {"state": "READY", "last_prediction_generated_at": "2026-10-04T23:00:00Z"},
    "odds_feed": {"state": "CONNECTED", "provider": "the-odds-api", "reason": ""},
    "market_sync": {"state": "SYNC_DISABLED", "last_sync_at": "2026-10-04T23:05:00Z"},
    "storage": {"state": "READY", "mode": "persistent"},
}

HEALTH = {
    "status": "ok",
    "fixture_provider_state": "CONNECTED",
    "last_fixture_sync_at": "2026-10-04T23:08:49Z",
    "market_sync_state": "SYNC_DISABLED",
    "last_market_sync_at": "2026-10-04T23:05:00Z",
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


# ------------------------------------------------- contract-faithful football


def test_live_candidates_desk_rows_produce_picks():
    view = fb.build_picks_view(_ok(CANDIDATES))
    assert view.available is True
    assert view.total == 2
    assert view.ah_count == 1
    assert view.corners_count == 1
    assert view.bet_count == 1
    assert view.no_bet_count == 1

    ah = next(pick for pick in view.picks if pick.market_label == "asian_handicap")
    assert ah.decision == "Bet"
    assert ah.is_focus_market is True
    assert ah.model_probability == "61.0%"
    assert ah.fair_price == "1.90"
    assert ah.available_price == "2.05"
    assert ah.edge == "+5.0%"

    corners = next(pick for pick in view.picks if pick.market_label == "corner_total")
    assert corners.decision == "No bet"
    assert corners.is_bet is False


def test_dashboard_today_desk_rows_produce_picks():
    view = fb.build_picks_view(_ok(CANDIDATES_DASHBOARD))
    assert view.available is True
    assert view.total == 2
    assert view.ah_count == 1
    assert view.corners_count == 1

    bets = [pick for pick in view.picks if pick.is_bet]
    assert [pick.decision for pick in bets] == ["Bet"]
    assert bets[0].market == "Asian Handicap"
    assert bets[0].selection == "Leeds +0.5"
    assert bets[0].model_probability == "61.0%"
    assert bets[0].edge == "+5.0%"
    assert bets[0].available_price == "2.05"

    no_bet = next(pick for pick in view.picks if not pick.is_bet)
    assert no_bet.decision == "No bet"


def test_bet_recognized_from_authoritative_decision_fields():
    assert fb.decision_label(
        {"market_family": "asian_handicap", "current_decision_state": "BET", "current_decision_group": "bet"}
    ) == ("Bet", "value")
    assert fb.decision_label(
        {"desk_decision": "BET", "decision_group": "bet"}
    ) == ("Bet", "value")
    assert fb.decision_label(
        {"desk_label": "BET", "current_decision_state": "BET"}
    ) == ("Bet", "value")


def test_no_bet_and_blocked_states_are_not_promoted_to_bet():
    assert fb.decision_label({"current_decision_state": "NO_BET_VALUE", "current_decision_group": "no_bet"})[0] == "No bet"
    assert fb.decision_label({"current_decision_group": "blocked"})[0] == "Blocked"
    assert fb.decision_label({"current_decision_group": "unsupported"})[0] == "Unsupported"
    assert fb.decision_label({"current_decision_state": "VETOED"})[0] == "Vetoed"
    # An unknown future state must never be rendered as a bet.
    assert fb.decision_label({"current_decision_state": "SOMETHING_NEW"})[0] != "Bet"


def test_empty_today_desk_rows_is_an_honest_empty_slate():
    payload = {"today_desk_rows": [], "today_desk_pagination": {}, "system_status": {}}
    view = fb.build_picks_view(_ok(payload))
    assert view.available is True
    assert view.state == "OK"
    assert view.total == 0
    assert view.picks == ()


def test_unexpected_candidate_contract_is_not_masqueraded_as_empty():
    view = fb.build_picks_view(_ok({"unexpected": "shape"}))
    assert view.available is False
    assert view.state == "CONTRACT_MISMATCH"
    assert "not an empty slate" in view.message


def test_malformed_candidate_rows_are_rejected():
    view = fb.build_picks_view(_ok({"today_desk_rows": {"not": "a list"}}))
    assert view.available is False
    assert view.state == "MALFORMED"


def test_probability_price_and_edge_units_are_fractional():
    # Football emits fractional probabilities/edges and decimal odds. The
    # adapter must not apply heuristic divide-by-100 logic.
    assert fb._fmt_probability(0.61) == "61.0%"
    assert fb._fmt_edge(0.05) == "+5.0%"
    assert fb._fmt_edge(-0.02) == "-2.0%"
    assert fb._fmt_rate(0.55) == "55.0%"
    assert fb._fmt_price(1.9) == "1.90"
    assert fb._fmt_price(2.05) == "2.05"


def test_performance_view_maps_real_payload():
    view = fb.build_performance_view(_ok(PERFORMANCE))
    assert view.available is True
    values = {metric.label: metric.value for metric in view.metrics}
    assert values["Bets"] == "12"
    assert values["Settled"] == "9"
    assert values["Pending"] == "3"
    assert values["Wins"] == "5"
    assert values["Losses"] == "4"
    assert values["Voids"] == "0"
    assert values["Pushes"] == "1"
    assert values["Win rate"] == "55.0%"
    assert values["Units"] == "+1.20u"
    assert values["ROI"] == "8.0%"
    assert values["CLV"] == "1.0%"
    assert view.markets[0].label == "Asian Handicap"
    assert view.markets[0].strike_rate == "60.0%"
    assert view.streak == "W1"
    assert view.clv_label == "+1.00%"


def test_history_view_maps_real_recent_paper_bets():
    view = fb.build_history_view(_ok(PERFORMANCE))
    assert view.available is True
    assert view.total == 1
    row = view.rows[0]
    assert row.fixture == "A vs B"
    assert row.competition == "PL"
    assert row.market == "Asian Handicap"
    assert row.selection == "A -0.5"
    assert row.price == "1.95"
    assert row.result == "WON"
    assert row.units == "+0.95u"
    assert row.clv == "2.0%"


def test_history_view_falls_back_to_client_history():
    payload = {
        "client_history": {
            "normalized_count": 1,
            "open_positions": [],
            "recent_settled": [
                {
                    "fixture_label": "C vs D",
                    "competition_name": "PL",
                    "kickoff_local": "2026-10-01 20:00",
                    "market": "corner_total",
                    "market_label": "Corners",
                    "bet_label": "Over 9.5 Corners",
                    "entry_odds": 1.9,
                    "trade_state": "LOST",
                    "net_pnl": -1.0,
                    "clv_pct": -0.01,
                }
            ],
        }
    }
    view = fb.build_history_view(_ok(payload))
    assert view.available is True
    assert view.total == 1
    assert view.rows[0].fixture == "C vs D"
    assert view.rows[0].result == "LOST"
    assert view.rows[0].units == "-1.00u"


def test_history_view_fallback_excludes_open_positions():
    # ``/football/history`` is settled bet history. When canonical
    # ``recent_paper_bets`` is unavailable, the client-history fallback must
    # surface settled rows only; an open/pending position must never be shown
    # here simply because canonical settled rows are absent or empty.
    payload = {
        "client_history": {
            "normalized_count": 2,
            "open_positions": [
                {
                    "fixture_label": "Open Team C vs Team D",
                    "competition_name": "PL",
                    "kickoff_local": "2026-10-02 20:00",
                    "market": "asian_handicap",
                    "market_label": "Asian Handicap",
                    "bet_label": "Team C -0.5",
                    "entry_odds": 1.95,
                    "trade_state": "OPEN",
                }
            ],
            "recent_settled": [
                {
                    "fixture_label": "Settled Team A vs Team B",
                    "competition_name": "PL",
                    "kickoff_local": "2026-10-01 20:00",
                    "market": "asian_handicap",
                    "market_label": "Asian Handicap",
                    "bet_label": "Team A -0.5",
                    "entry_odds": 1.95,
                    "trade_state": "WON",
                    "net_pnl": 0.95,
                    "clv_pct": 0.02,
                }
            ],
        }
    }
    view = fb.build_history_view(_ok(payload))
    assert view.available is True
    assert view.total == 1
    fixtures = [row.fixture for row in view.rows]
    assert "Settled Team A vs Team B" in fixtures
    assert "Open Team C vs Team D" not in fixtures


def test_history_view_without_settled_rows_is_honest_empty():
    # No canonical settled rows and no settled client history: the page must be
    # honestly empty rather than populated with open positions.
    payload = {
        "client_history": {
            "normalized_count": 1,
            "open_positions": [
                {
                    "fixture_label": "Open Team C vs Team D",
                    "market": "asian_handicap",
                    "bet_label": "Team C -0.5",
                    "trade_state": "OPEN",
                }
            ],
            "recent_settled": [],
        }
    }
    view = fb.build_history_view(_ok(payload))
    assert view.available is True
    assert view.total == 0
    assert view.rows == ()


def test_system_view_uses_real_status_keys():
    view = fb.build_system_view(_ok(SYSTEM_STATUS), _ok(HEALTH))
    assert view.available is True
    values = {row.label: row.value for row in view.rows}
    assert values["Data feed"] == "CONNECTED"
    assert values["Prediction engine"] == "READY"
    assert values["Market evidence"] == "CONNECTED"
    assert values["Storage"] == "READY"


def test_system_view_falls_back_to_market_sync_and_health():
    without_odds_feed = {"fixture_feed": {"state": "CONNECTED"}, "market_sync": {"state": "SYNC_DISABLED"}}
    view = fb.build_system_view(_ok(without_odds_feed), _degraded())
    values = {row.label: row.value for row in view.rows}
    assert values["Market evidence"] == "SYNC_DISABLED"

    health_only = fb.build_system_view(_degraded("UNAVAILABLE"), _ok(HEALTH))
    assert health_only.available is True
    health_values = {row.label: row.value for row in health_only.rows}
    assert health_values["Data feed"] == "CONNECTED"
    assert health_values["Market evidence"] == "SYNC_DISABLED"
    assert health_values["Storage"] == "persistent"


def test_football_picks_page_renders_contract_faithful_rows(monkeypatch):
    _patch_football(monkeypatch, candidates=_ok(CANDIDATES_DASHBOARD))
    client = _client(monkeypatch)
    response = client.get("/football")
    assert response.status_code == 200
    assert "Leeds +0.5" in response.text
    assert "61.0%" in response.text
    assert "+5.0%" in response.text


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


def test_root_redirects_to_overview(monkeypatch):
    _patch_football(monkeypatch)
    client = _client(monkeypatch)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/overview"


def test_overview_degrades_when_tennis_summary_fails(monkeypatch):
    import sports_edge_tennis.dashboard.unified_routes as ur

    _patch_football(monkeypatch)
    monkeypatch.setattr(
        ur,
        "_tennis_overview",
        lambda tz: {
            "available": False,
            "latest_activity": None,
            "pick_count": None,
            "value_bet_count": None,
            "model_pick_count": None,
            "system_state": None,
            "failed": True,
        },
    )
    client = _client(monkeypatch)
    response = client.get("/overview")
    assert response.status_code == 200
    assert "Football" in response.text


def test_overview_survives_unexpected_football_loader_error(monkeypatch):
    import sports_edge_tennis.dashboard.unified_routes as ur

    def boom():
        raise RuntimeError("unexpected in-process Football failure")

    monkeypatch.setattr(fb, "load_candidates", boom)
    monkeypatch.setattr(
        ur,
        "_tennis_overview",
        lambda tz: {
            "available": False,
            "latest_activity": None,
            "pick_count": None,
            "value_bet_count": None,
            "model_pick_count": None,
            "system_state": None,
            "failed": True,
        },
    )
    client = _client(monkeypatch)
    response = client.get("/overview")
    assert response.status_code == 200
    assert "temporarily unavailable" in response.text.lower()
