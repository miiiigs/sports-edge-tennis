"""Pagination helper and dashboard table pagination contract."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from starlette.requests import Request

from sports_edge_tennis.dashboard import daily_history, model_pick_history
from sports_edge_tennis.dashboard.daily_bets import DailyBetRow, DailyBetSummary
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary,
    ModelPickResultsView,
    ModelPickRow,
)
from sports_edge_tennis.dashboard.pagination import (
    DEFAULT_PAGE_SIZE,
    build_page_url,
    paginate,
)
from sports_edge_tennis.dashboard.picks import MODEL_PICK, PickCard, TodayPicks


def _request(query: str = "", path: str = "/model-pick-results") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode("ascii"),
            "headers": [],
        }
    )


# ----------------------------------------------------------- helper


def test_paginate_zero_items_is_one_clean_page() -> None:
    rows, page = paginate([], page=1)
    assert rows == ()
    assert page.page == 1
    assert page.total_pages == 1
    assert page.start_item == 0
    assert page.end_item == 0
    assert page.has_previous is False
    assert page.has_next is False
    assert page.is_paginated is False


def test_paginate_single_item() -> None:
    rows, page = paginate(["a"], page=1)
    assert rows == ("a",)
    assert page.total_items == 1
    assert page.start_item == 1
    assert page.end_item == 1


def test_paginate_exactly_one_page() -> None:
    rows, page = paginate(list(range(20)), page=1, page_size=20)
    assert len(rows) == 20
    assert page.total_pages == 1
    assert page.has_next is False


def test_paginate_twenty_one_splits_into_two_pages() -> None:
    items = list(range(21))
    first, page1 = paginate(items, page=1, page_size=20)
    second, page2 = paginate(items, page=2, page_size=20)
    assert first == tuple(range(20))
    assert second == (20,)
    assert page1.total_pages == 2
    assert page2.page_numbers == (1, 2)
    assert page1.has_next is True
    assert page2.has_previous is True


def test_paginate_forty_and_forty_one() -> None:
    items = list(range(41))
    page2_rows, page2 = paginate(items[:40], page=2, page_size=20)
    assert len(page2_rows) == 20 and page2.total_pages == 2

    last_rows, last = paginate(items, page=3, page_size=20)
    assert last_rows == (40,)
    assert last.start_item == 41
    assert last.end_item == 41
    assert last.has_next is False


def test_paginate_final_partial_page() -> None:
    rows, page = paginate(list(range(45)), page=3, page_size=20)
    assert rows == tuple(range(40, 45))
    assert page.range_text == "Showing 41–45 of 45"


def test_paginate_invalid_pages_are_normalized() -> None:
    items = list(range(50))
    low_rows, low = paginate(items, page=0, page_size=20)
    assert low.page == 1 and low_rows == tuple(range(20))
    high_rows, high = paginate(items, page=999, page_size=20)
    assert high.page == high.total_pages == 3
    assert high_rows == tuple(range(40, 50))


def test_paginate_order_is_preserved() -> None:
    items = ["z", "y", "x", "w", "v"]
    rows, _ = paginate(items, page=1, page_size=2)
    assert rows == ("z", "y")


def test_paginate_clamps_page_size() -> None:
    rows, page = paginate(list(range(500)), page=1, page_size=10000)
    assert page.page_size == 100
    assert len(rows) == 100


def test_default_page_size_is_twenty() -> None:
    assert DEFAULT_PAGE_SIZE == 20


# ----------------------------------------------------------- build_page_url


def test_build_page_url_preserves_unrelated_params() -> None:
    request = _request("date=2026-09-28&history_page=2&detail_page=3")
    url = build_page_url(request, "detail_page", 4)
    assert url.startswith("/model-pick-results?")
    assert "date=2026-09-28" in url
    assert "history_page=2" in url
    assert "detail_page=4" in url


def test_build_page_url_drops_defaults() -> None:
    request = _request("date=2026-09-28&history_page=2")
    url = build_page_url(request, "history_page", 1)
    assert "history_page" not in url
    assert "date=2026-09-28" in url


# ----------------------------------------------------------- model picks route


def _model_row(i: int) -> ModelPickRow:
    return ModelPickRow(
        manager_consensus_result_id=i,
        picked_at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        match_start_at=datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        tournament="Test Open",
        player_1=f"Player {i}",
        player_2=f"Opponent {i}",
        selection=f"Player {i}",
        consensus_probability=Decimal("0.650000"),
        result_status="PENDING",
        is_value_bet=False,
        manager_policy_version="v1",
        model_regime_key=None,
        pick_type="MANAGER_CONSENSUS",
    )


def _day_summary(i: int) -> DailyModelPickSummary:
    return DailyModelPickSummary(
        decision_date=date(2026, 9, 10) + timedelta(days=i),
        picks=1,
        scored=0,
        pending=1,
        voids=0,
        wins=0,
        losses=0,
        hit_rate=None,
    )


def test_model_pick_results_paginates_detail_and_preserves_filters(monkeypatch):
    from sports_edge_tennis.dashboard.production_app import app

    view = ModelPickResultsView(
        all_summaries=tuple(_day_summary(i) for i in range(25)),
        aligned_summaries=tuple(_day_summary(i) for i in range(25)),
        selected_date=date(2026, 9, 28),
        selected_summary=DailyModelPickSummary(
            date(2026, 9, 28), 45, 0, 45, 0, 0, 0, None
        ),
        picks=tuple(_model_row(i) for i in range(45)),
    )

    monkeypatch.setattr(
        model_pick_history, "_load_history", lambda requested, tz=None: view
    )

    client = TestClient(app)
    page1 = client.get("/model-pick-results?date=2026-09-28").text
    assert "Showing 1–20 of 45" in page1

    page3 = client.get("/model-pick-results?date=2026-09-28&detail_page=3").text
    assert "Showing 41–45 of 45" in page3
    # The detail pager keeps the selected date and independent page params.
    assert "detail_page=2" in page1
    assert "date=2026-09-28" in page1
    # History is a separate table with its own page param.
    assert "history_page=2" in page1


def test_model_pick_results_history_page_is_independent(monkeypatch):
    from sports_edge_tennis.dashboard.production_app import app

    view = ModelPickResultsView(
        all_summaries=tuple(_day_summary(i) for i in range(25)),
        aligned_summaries=tuple(_day_summary(i) for i in range(25)),
        selected_date=date(2026, 9, 28),
        selected_summary=DailyModelPickSummary(
            date(2026, 9, 28), 45, 0, 45, 0, 0, 0, None
        ),
        picks=tuple(_model_row(i) for i in range(45)),
    )
    monkeypatch.setattr(
        model_pick_history, "_load_history", lambda requested, tz=None: view
    )

    client = TestClient(app)
    html = client.get(
        "/model-pick-results?date=2026-09-28&history_page=2&detail_page=3"
    ).text
    assert "Showing 21–25 of 25" in html  # history page 2
    assert "Showing 41–45 of 45" in html  # detail page 3
    # Each table's pager carries the other table's page so navigating one table
    # never resets the other.
    assert "detail_page=2" in html  # detail pager link
    assert "history_page=2" in html  # preserved on the detail pager links


# ----------------------------------------------------------- value bet route


def _bet_summary(i: int) -> DailyBetSummary:
    return DailyBetSummary(
        decision_date=date(2026, 9, 1) + timedelta(days=i),
        bets=1,
        settled=0,
        pending=1,
        wins=0,
        losses=0,
        voids=0,
        cancelled=0,
        one_unit_profit=Decimal(0),
        roi=None,
        hit_rate=None,
    )


def _bet_row(i: int) -> DailyBetRow:
    return DailyBetRow(
        decision_id=i,
        decision_at=datetime(2026, 9, 28, 9, 0, tzinfo=UTC),
        match_start_at=datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        tournament="Test Open",
        player_1=f"Player {i}",
        player_2=f"Opponent {i}",
        selection=f"Player {i}",
        market_decimal_price=Decimal("2.00"),
        bookmaker_code="UNIBET",
        consensus_probability=Decimal("0.650000"),
        settlement_status="PENDING",
        settled_at=None,
        profit_units=None,
        manager_policy_version="v1",
    )


def test_value_bet_results_paginates_history_and_detail(monkeypatch):
    from sports_edge_tennis.dashboard.production_app import app

    view = daily_history.ResultsView(
        all_summaries=tuple(_bet_summary(i) for i in range(25)),
        aligned_summaries=tuple(_bet_summary(i) for i in range(25)),
        selected_date=date(2026, 9, 28),
        selected_summary=_bet_summary(0),
        bets=tuple(_bet_row(i) for i in range(45)),
    )
    monkeypatch.setattr(daily_history, "_load_history", lambda requested, tz=None: view)

    client = TestClient(app)
    html = client.get("/value-bet-results?date=2026-09-28&detail_page=3").text
    assert "Showing 41–45 of 45" in html
    assert "date=2026-09-28" in html
    assert "detail_page=2" in html


# ----------------------------------------------------------- value route


def _card(i: int) -> PickCard:
    return PickCard(
        match_id=i,
        kind=MODEL_PICK,
        classification="HIGH_CONFIDENCE_PICK",
        player=f"Player {i}",
        opponent=f"Opponent {i}",
        win_probability=Decimal("0.650000"),
        tournament="Test Open",
        competition_tier="ATP_250",
        scheduled_start=datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
    )


def test_value_model_only_table_paginates(monkeypatch):
    from sports_edge_tennis.dashboard import app as dashboard_app
    from sports_edge_tennis.dashboard.production_app import app
    from sports_edge_tennis.dashboard.read_model import RunState

    cards = tuple(_card(i) for i in range(25))
    today_picks = TodayPicks(
        picks=cards,
        value_bet_count=0,
        model_pick_count=len(cards),
        blocked_count=0,
        evaluated_count=len(cards),
    )
    state = RunState(
        kind="POPULATED",
        run_date=date(2026, 9, 28),
        as_of=datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
        prediction_run_id=1,
        run_status="COMPLETED",
        matches=[],
    )
    monkeypatch.setattr(
        dashboard_app, "build_today_picks", lambda matches, market: today_picks
    )
    monkeypatch.setattr(
        dashboard_app, "_load_state", lambda requested_date, tz=None: state
    )
    monkeypatch.setattr(
        dashboard_app, "_load_issued_market_overlay", lambda requested_date, tz=None: {}
    )

    client = TestClient(app)
    page1 = client.get("/value?date=2026-09-28").text
    assert "Showing 1–20 of 25" in page1

    page2 = client.get("/value?date=2026-09-28&model_page=2").text
    assert "Showing 21–25 of 25" in page2
    # The pager keeps the selected date when moving between pages.
    assert "date=2026-09-28" in page2
