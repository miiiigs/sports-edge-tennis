"""Read-only server-to-server client for the independent Football platform.

The Football model, database, workers, scheduler and settlement runtime live in a
separate repository and runtime. This module is the *only* place the unified
Sports Edge UI talks to Football. It is deliberately small and defensive:

* transport is server-side only (the browser never calls Football directly);
* every request is a bounded-timeout GET against an explicit path allowlist;
* no state-changing endpoint (for example a fixtures refresh) can ever be called;
* malformed, unauthorised, timed-out or unreachable responses degrade honestly
  instead of raising into the Tennis request path.

The module also holds the presentation adapters that translate raw Football API
payloads into the client-facing view models used by the templates. Raw Football
JSON is never handed straight to a template.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

FOOTBALL_BASE_URL_ENV = "SPORTS_EDGE_FOOTBALL_BASE_URL"
FOOTBALL_TOKEN_ENV = "SPORTS_EDGE_CLIENT_API_TOKEN"
FOOTBALL_COOKIE_ENV = "SPORTS_EDGE_FOOTBALL_COOKIE"

DEFAULT_TIMEOUT_SECONDS = 4.0

# Only these read-only GET paths may ever be requested. Anything else (including
# every state-changing route) is refused before a socket is opened.
ALLOWED_GET_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/api/system/status",
        "/api/candidates",
        "/api/performance",
        "/api/predictions",
        "/api/analysis",
        "/api/fixtures/next",
        "/api/fixtures/today",
        "/api/fixtures/live",
        "/api/fixtures/upcoming",
    }
)

# Documented for operators and asserted by tests. Never callable through here.
STATE_CHANGING_PATHS: frozenset[str] = frozenset(
    {
        "/api/fixtures/refresh",
        "/login",
        "/signup",
        "/guest",
        "/logout",
    }
)

FOCUS_MARKETS: tuple[str, ...] = ("asian_handicap", "corner_total")

MARKET_LABELS: dict[str, str] = {
    "asian_handicap": "Asian Handicap",
    "corner_total": "Corners",
    "goals_total": "Goals",
    "btts": "Both Teams to Score",
    "1x2": "Match Result",
}

DECISION_LABELS: dict[str, str] = {
    "BET": "Bet",
    "NO_BET": "No bet",
    "VETOED": "Vetoed",
    "PENDING": "Pending",
    "AWAITING_MARKET": "Awaiting market",
}


# --------------------------------------------------------------------- transport


@dataclass(frozen=True)
class _HttpResult:
    status_code: int
    text: str


@dataclass(frozen=True)
class FootballResponse:
    """Outcome of one read-only Football request."""

    ok: bool
    state: str
    message: str
    data: dict[str, Any] | None = None


def _http_get(url: str, headers: dict[str, str], timeout: float) -> _HttpResult:
    """Single, injectable transport call. Replaced in tests."""
    response = requests.get(url, headers=headers, timeout=timeout)
    return _HttpResult(status_code=response.status_code, text=response.text)


def _base_url() -> str:
    return (os.environ.get(FOOTBALL_BASE_URL_ENV) or "").strip()


def _timeout() -> float:
    raw = (os.environ.get("SPORTS_EDGE_FOOTBALL_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(0.5, min(30.0, float(raw)))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _auth_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "sports-edge-unified-ui"}
    token = (os.environ.get(FOOTBALL_TOKEN_ENV) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    cookie = (os.environ.get(FOOTBALL_COOKIE_ENV) or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def request_get(path: str) -> FootballResponse:
    """Read one allowlisted Football path. Never raises for transport failures."""
    if path not in ALLOWED_GET_PATHS:
        return FootballResponse(
            ok=False,
            state="NOT_ALLOWED",
            message="Requested Football path is not on the read-only allowlist.",
        )

    base = _base_url()
    if not base:
        return FootballResponse(
            ok=False,
            state="NOT_CONFIGURED",
            message="Football integration is not configured.",
        )

    url = base.rstrip("/") + path
    try:
        result = _http_get(url, _auth_headers(), _timeout())
    except requests.Timeout:
        return FootballResponse(
            ok=False,
            state="TIMEOUT",
            message="Football did not respond in time.",
        )
    except requests.RequestException:
        return FootballResponse(
            ok=False,
            state="UNAVAILABLE",
            message="Football is temporarily unavailable.",
        )

    if result.status_code in (401, 403):
        return FootballResponse(
            ok=False,
            state="AUTH_REQUIRED",
            message="Football refused the read-only integration (authentication required).",
        )
    if result.status_code >= 400:
        return FootballResponse(
            ok=False,
            state="HTTP_ERROR",
            message="Football returned an error.",
        )
    try:
        data = json.loads(result.text)
    except (TypeError, ValueError):
        return FootballResponse(
            ok=False,
            state="MALFORMED",
            message="Football returned an unexpected response.",
        )
    if not isinstance(data, dict):
        return FootballResponse(
            ok=False,
            state="MALFORMED",
            message="Football returned an unexpected response.",
        )
    return FootballResponse(ok=True, state="OK", message="", data=data)


def load_candidates() -> FootballResponse:
    return request_get("/api/candidates")


def load_performance() -> FootballResponse:
    return request_get("/api/performance")


def load_system_status() -> FootballResponse:
    return request_get("/api/system/status")


def load_health() -> FootballResponse:
    return request_get("/health")


# ------------------------------------------------------------------ primitives


def _first(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_probability(value: Any) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number * 100:.1f}%"


def _fmt_price(value: Any) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number:.2f}"


def _fmt_edge(value: Any) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number * 100:+.1f}%"


def _fmt_units(value: Any) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number:+.2f}u"


def _fmt_rate(value: Any) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number * 100:.1f}%"


def market_label(market_family: str | None) -> str:
    key = str(market_family or "").strip()
    return MARKET_LABELS.get(key, key.replace("_", " ").title() or "—")


def decision_label(row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(label, tone)`` for the bet-decision half of a desk row.

    The model view and the bet decision are kept separate everywhere else; this
    only ever describes the decision.
    """
    group = str(_first(row, "decision_group") or "").strip().lower()
    state = str(_first(row, "current_decision_state", "desk_decision", "decision") or "").strip().upper()
    if group == "bet" or state == "BET":
        return "Bet", "value"
    if state == "VETOED":
        return "Vetoed", "blocked"
    if group == "no_bet" or state == "NO_BET":
        return "No bet", "neutral"
    return DECISION_LABELS.get(state, state.replace("_", " ").title() or "—"), "neutral"


# ----------------------------------------------------------------- view models


@dataclass(frozen=True)
class FootballPick:
    fixture: str
    competition: str | None
    kickoff: str | None
    market: str
    market_label: str
    selection: str
    line: str | None
    model_probability: str | None
    fair_price: str | None
    available_price: str | None
    edge: str | None
    decision: str
    decision_tone: str
    is_bet: bool
    is_focus_market: bool
    reason: str | None
    stake: str | None = None


@dataclass(frozen=True)
class PicksView:
    available: bool
    state: str
    message: str
    picks: tuple[FootballPick, ...] = ()
    total: int = 0
    bet_count: int = 0
    no_bet_count: int = 0
    ah_count: int = 0
    corners_count: int = 0
    other_count: int = 0


@dataclass(frozen=True)
class Metric:
    label: str
    value: str
    note: str | None = None


@dataclass(frozen=True)
class MarketBreakdownRow:
    label: str
    bets: int
    settled: int
    wins: int
    losses: int
    strike_rate: str | None
    net_units: str | None
    roi: str | None
    low_sample: bool


@dataclass(frozen=True)
class PerformanceView:
    available: bool
    state: str
    message: str
    metrics: tuple[Metric, ...] = ()
    markets: tuple[MarketBreakdownRow, ...] = ()
    windows: tuple[dict[str, Any], ...] = ()
    clv_label: str | None = None
    streak: str | None = None


@dataclass(frozen=True)
class HistoryRow:
    date: str | None
    fixture: str
    competition: str | None
    market: str
    selection: str
    line: str | None
    price: str | None
    result: str
    units: str | None
    clv: str | None


@dataclass(frozen=True)
class HistoryView:
    available: bool
    state: str
    message: str
    rows: tuple[HistoryRow, ...] = ()
    total: int = 0
    capped: bool = False


@dataclass(frozen=True)
class SystemRow:
    label: str
    value: str
    tone: str = "neutral"


@dataclass(frozen=True)
class SystemView:
    available: bool
    state: str
    message: str
    rows: tuple[SystemRow, ...] = ()


def _unavailable(view_cls, response: FootballResponse, *, message: str | None = None):
    return view_cls(
        available=False,
        state=response.state,
        message=message or response.message or "Football is temporarily unavailable.",
    )


def _fixture_label(row: dict[str, Any]) -> str:
    label = _first(row, "fixture_label")
    if label:
        return str(label)
    home = _first(row, "home_team_name")
    away = _first(row, "away_team_name")
    if home and away:
        return f"{home} vs {away}"
    return str(_first(row, "fixture_id") or "Fixture unavailable")


def build_picks_view(response: FootballResponse) -> PicksView:
    if not response.ok or response.data is None:
        return _unavailable(PicksView, response)

    data = response.data
    rows = data.get("rows")
    if not isinstance(rows, list):
        # A connected desk with no current rows is a valid, empty state.
        rows = []

    picks: list[FootballPick] = []
    ah = corners = bets = no_bets = other = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        market = str(_first(row, "market_family", "market") or "")
        if market == "asian_handicap":
            ah += 1
        elif market == "corner_total":
            corners += 1
        else:
            other += 1
        label, tone = decision_label(row)
        is_bet = label == "Bet"
        if is_bet:
            bets += 1
        else:
            no_bets += 1
        line = _first(row, "line")
        picks.append(
            FootballPick(
                fixture=_fixture_label(row),
                competition=_first(row, "competition_name"),
                kickoff=_first(row, "kickoff_local", "kickoff_time_local", "kickoff_utc"),
                market=market_label(market),
                market_label=market,
                selection=str(
                    _first(row, "bet_label", "selection_label", "selection") or "—"
                ),
                line=None if line is None else f"{_num(line):g}" if _num(line) is not None else str(line),
                model_probability=_fmt_probability(_first(row, "model_probability")),
                fair_price=_fmt_price(_first(row, "fair_odds", "model_fair_decimal_price")),
                available_price=_fmt_price(_first(row, "best_available_price", "best_offer_odds")),
                edge=_fmt_edge(_first(row, "edge", "settlement_aware_edge")),
                decision=label,
                decision_tone=tone,
                is_bet=is_bet,
                is_focus_market=market in FOCUS_MARKETS,
                reason=_first(row, "reason"),
            )
        )

    focus_first = sorted(picks, key=lambda pick: 0 if pick.is_focus_market else 1)
    return PicksView(
        available=True,
        state="OK",
        message="",
        picks=tuple(focus_first),
        total=len(picks),
        bet_count=bets,
        no_bet_count=no_bets,
        ah_count=ah,
        corners_count=corners,
        other_count=other,
    )


def build_overview_football(response: FootballResponse) -> PicksView:
    """A compact Football summary for the overview launchpad."""
    return build_picks_view(response)


def _market_breakdown_rows(data: dict[str, Any]) -> list[MarketBreakdownRow]:
    rows = data.get("market_breakdown")
    if not isinstance(rows, list):
        return []
    market_order = {key: index for index, key in enumerate(FOCUS_MARKETS)}
    keyed: list[tuple[int, MarketBreakdownRow]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(_first(row, "key", "market") or "")
        if not key:
            continue
        keyed.append(
            (
                market_order.get(key, len(FOCUS_MARKETS)),
                MarketBreakdownRow(
                    label=str(_first(row, "label") or market_label(key)),
                    bets=int(_num(_first(row, "bets")) or 0),
                    settled=int(_num(_first(row, "settled")) or 0),
                    wins=int(_num(_first(row, "wins")) or 0),
                    losses=int(_num(_first(row, "losses")) or 0),
                    strike_rate=_fmt_rate(_first(row, "strike_rate")),
                    net_units=_fmt_units(_first(row, "net_units")),
                    roi=_fmt_rate(_first(row, "roi")),
                    low_sample=bool(_first(row, "low_sample")),
                ),
            )
        )
    keyed.sort(key=lambda item: item[0])
    return [item[1] for item in keyed]


def build_performance_view(response: FootballResponse) -> PerformanceView:
    if not response.ok or response.data is None:
        return _unavailable(PerformanceView, response)

    data = response.data
    summary = data.get("current_paper_summary")
    if not isinstance(summary, dict):
        summary = {}

    def metric(label: str, key: str, formatter: Callable[[Any], str | None]) -> Metric:
        formatted = formatter(summary.get(key))
        return Metric(label=label, value=formatted if formatted is not None else "—")

    metrics = (
        metric("Bets", "positions", lambda v: None if _num(v) is None else str(int(_num(v)))),
        metric("Settled", "settled_positions", lambda v: None if _num(v) is None else str(int(_num(v)))),
        metric("Wins", "wins", lambda v: None if _num(v) is None else str(int(_num(v)))),
        metric("Losses", "losses", lambda v: None if _num(v) is None else str(int(_num(v)))),
        metric("Voids", "voids", lambda v: None if _num(v) is None else str(int(_num(v)))),
        metric("Win rate", "strike_rate", _fmt_rate),
        metric("Units", "net_units", _fmt_units),
        metric("ROI", "roi", _fmt_rate),
        metric("CLV", "average_clv", _fmt_rate),
    )

    windows = data.get("window_rows")
    if not isinstance(windows, list):
        windows = []

    return PerformanceView(
        available=True,
        state="OK",
        message="",
        metrics=metrics,
        markets=tuple(_market_breakdown_rows(data)),
        windows=tuple(row for row in windows if isinstance(row, dict)),
        clv_label=_first(data, "clv_status_label"),
        streak=_first(data, "current_streak"),
    )


def _history_row(row: dict[str, Any]) -> HistoryRow:
    line = _first(row, "line")
    return HistoryRow(
        date=_first(row, "kickoff_local", "settled_at", "decision_timestamp"),
        fixture=_fixture_label(row),
        competition=_first(row, "competition_name"),
        market=str(_first(row, "market_label") or market_label(str(_first(row, "market") or ""))),
        selection=str(_first(row, "bet_label", "selection_label", "selection") or "—"),
        line=None if line is None else (f"{_num(line):g}" if _num(line) is not None else str(line)),
        price=_fmt_price(_first(row, "entry_odds", "available_market_price", "decision_odds")),
        result=str(_first(row, "result_label", "trade_state", "result") or "Pending"),
        units=_fmt_units(_first(row, "net_pnl", "net_units", "profit_units", "gross_return")),
        clv=_fmt_rate(_first(row, "clv_pct", "clv")),
    )


def build_history_view(response: FootballResponse) -> HistoryView:
    if not response.ok or response.data is None:
        return _unavailable(HistoryView, response)

    data = response.data
    raw = data.get("recent_paper_bets")
    if not isinstance(raw, list):
        raw = []
    rows = tuple(_history_row(row) for row in raw if isinstance(row, dict))
    # ``client_history`` is capped server-side by Football; surface that honestly.
    capped = False
    client_history = data.get("client_history")
    if isinstance(client_history, dict):
        capped = bool(_first(client_history, "normalized_count")) and len(rows) < int(
            _num(_first(client_history, "normalized_count")) or 0
        )
    return HistoryView(
        available=True,
        state="OK",
        message="",
        rows=rows,
        total=len(rows),
        capped=capped,
    )


def _tone_for_state(state: str | None) -> str:
    text = str(state or "").upper()
    if text in {"OK", "HEALTHY", "CONNECTED", "READY", "FRESH", "ACTIVE", "RUNNING"}:
        return "value"
    if text in {"DEGRADED", "WARNING", "STALE", "PARTIAL", "DISABLED"}:
        return "blocked"
    return "neutral"


def _row(label: str, value: Any, *, tone: str = "neutral") -> SystemRow:
    if value is None or value == "" or value == "NOT AVAILABLE":
        return SystemRow(label=label, value="—", tone="neutral")
    return SystemRow(label=label, value=str(value), tone=tone)


def build_system_view(
    status_response: FootballResponse, health_response: FootballResponse
) -> SystemView:
    status = status_response.data if status_response.ok and status_response.data else None
    health = health_response.data if health_response.ok and health_response.data else None

    if status is None and health is None:
        return _unavailable(SystemView, status_response)

    rows: list[SystemRow] = []
    fixture_feed = status.get("fixture_feed") if isinstance(status, dict) else None
    prediction_engine = status.get("prediction_engine") if isinstance(status, dict) else None

    provider_state = _first(fixture_feed or {}, "state") or _first(health or {}, "fixture_provider_state")
    last_sync = _first(fixture_feed or {}, "last_successful_sync_at", "last_sync_at") or _first(
        health or {}, "last_fixture_sync_at"
    )
    rows.append(_row("Data feed", provider_state, tone=_tone_for_state(provider_state)))
    rows.append(_row("Last update", last_sync))

    model_state = _first(prediction_engine or {}, "state") or (
        "Ready" if _first(health or {}, "prediction_snapshot_ready") in (True, "YES", "true") else None
    )
    rows.append(_row("Prediction engine", model_state, tone=_tone_for_state(model_state)))
    rows.append(
        _row(
            "Last prediction",
            _first(prediction_engine or {}, "last_prediction_generated_at"),
        )
    )

    market_state = _first(status or {}, "market_sync") if isinstance(status, dict) else None
    if isinstance(market_state, dict):
        market_state = _first(market_state, "state")
    if market_state is None:
        market_state = _first(health or {}, "market_sync_state")
    rows.append(_row("Market evidence", market_state, tone=_tone_for_state(market_state)))

    storage = _first(status or {}, "storage") if isinstance(status, dict) else None
    if isinstance(storage, dict):
        storage = _first(storage, "state", "mode")
    if storage is None:
        storage = _first(health or {}, "storage_mode")
    rows.append(_row("Storage", storage))

    state = "OK"
    if not status_response.ok and not health_response.ok:
        state = status_response.state
    return SystemView(available=True, state=state, message="", rows=tuple(rows))


__all__ = [
    "ALLOWED_GET_PATHS",
    "DECISION_LABELS",
    "FOCUS_MARKETS",
    "FOOTBALL_BASE_URL_ENV",
    "FOOTBALL_COOKIE_ENV",
    "FOOTBALL_TOKEN_ENV",
    "MARKET_LABELS",
    "STATE_CHANGING_PATHS",
    "FootballPick",
    "FootballResponse",
    "HistoryRow",
    "HistoryView",
    "MarketBreakdownRow",
    "Metric",
    "PerformanceView",
    "PicksView",
    "SystemRow",
    "SystemView",
    "build_history_view",
    "build_overview_football",
    "build_performance_view",
    "build_picks_view",
    "build_system_view",
    "load_candidates",
    "load_health",
    "load_performance",
    "load_system_status",
    "market_label",
    "request_get",
]
