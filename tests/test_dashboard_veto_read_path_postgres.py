"""PostgreSQL acceptance for the real VETOED dashboard read path.

The production loader used to select only ``decision_status = 'BET'`` while the
presentation layer supported ``VETOED``. Synthetic card tests therefore passed
even though a persisted safety veto could never reach the dashboard.

This test seeds the canonical production rows (predictions, Manager consensus,
Market/Veto decisions, offered guide quotes, results) and exercises the real
loaders plus the real FastAPI HTML/API routes. It intentionally does not mock
the decision query.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from fastapi.testclient import TestClient
from test_dashboard_issued_picks_postgres import (
    SHARP,
    TEST_DATE,
    _odds,
    _seed_configs,
    _seed_manager,
    _seed_match,
    _seed_run,
)

from sports_edge_tennis.dashboard import app as dashboard_app
from sports_edge_tennis.dashboard import daily_history, model_pick_history
from sports_edge_tennis.dashboard.guide_odds import get_issued_guide_odds
from sports_edge_tennis.dashboard.model_pick_history import (
    load_aligned_daily_model_picks,
)
from sports_edge_tennis.dashboard.picks import (
    BLOCKED,
    MODEL_PICK,
    VALUE_BET,
    build_today_picks,
)
from sports_edge_tennis.dashboard.production_app import app as production_app
from sports_edge_tennis.dashboard.read_model import (
    get_issued_day_state,
    get_issued_market_decisions,
)
from sports_edge_tennis.market import ensure_market_policy_version_v1
from sports_edge_tennis.veto.integration import (
    evaluate_and_persist_market_slate_with_veto,
)


class _KeepOpen:
    """The routes close the connection they are handed; keep the fixture open."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        return None


def _guide_quote(
    conn,
    *,
    match_id: int,
    side: int,
    price: str,
    bookmaker: str,
    snapshot: str,
    observed: datetime,
    received: datetime,
) -> None:
    """Persist a real offered quote with auditable receipt provenance."""
    ingest_id = conn.execute(
        """
        INSERT INTO ingest_runs (
            source_system, source_dataset, started_at, finished_at, status
        ) VALUES ('ODDSPAPI', %s, %s, %s, 'COMPLETED')
        RETURNING ingest_run_id
        """,
        (snapshot, observed, received),
    ).fetchone()[0]
    mapping_id = conn.execute(
        """
        INSERT INTO provider_bookmaker_mappings (
            provider_code, provider_bookmaker_key, mapping_version,
            bookmaker_code, normalization_class, effective_from
        ) VALUES ('ODDSPAPI', %s, 1, %s, 'OFFERED', %s)
        RETURNING provider_bookmaker_mapping_id
        """,
        (snapshot, bookmaker, observed - timedelta(days=30)),
    ).fetchone()[0]
    raw_id = conn.execute(
        """
        INSERT INTO raw_odds_observations (
            ingest_run_id, provider_code, provider_event_id,
            provider_observation_key, received_at, observation_time,
            observation_time_basis, payload_hash, payload_json,
            adapter_version, normalizer_version
        ) VALUES (%s, 'ODDSPAPI', %s, %s, %s, %s,
                  'RECEIVE_TIME', %s, '{}'::jsonb, 'test', 'test')
        RETURNING raw_odds_observation_id
        """,
        (ingest_id, snapshot, snapshot, received, received, f"hash-{snapshot}"),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type,
            selection_type, decimal_price, market_status,
            source_snapshot_id, source_record_hash,
            source_observed_at, as_of, ingested_at,
            raw_odds_observation_id, provider_bookmaker_mapping_id, odds_origin
        )
        VALUES (%s, 'OFFERED', %s, 'MATCH_WINNER', %s, %s, 'OPEN',
                %s, %s, %s, %s, %s, %s, %s, 'LIVE_PROVIDER')
        """,
        (
            match_id,
            bookmaker,
            "PLAYER_1" if side == 1 else "PLAYER_2",
            price,
            snapshot,
            f"hash-{snapshot}",
            observed,
            received,
            received,
            raw_id,
            mapping_id,
        ),
    )


def _result(conn, match_id: int, outcome: str, winner: int | None, when: datetime) -> None:
    conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (match_id, outcome, winner, when, when),
    )


def test_real_vetoed_decision_reaches_dashboard_without_becoming_a_bet(
    db: psycopg.Connection, monkeypatch
) -> None:
    configs = _seed_configs(db)
    issued_at = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    market_at = datetime(2026, 9, 25, 9, 45, tzinfo=UTC)

    # Guide quotes before issue require a match schedule already observable and
    # ingested at their receipt time; the default test fixture ingests "now".
    fixture_seen = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)

    def _historical_match(*args, **kwargs):
        return _seed_match(*args, match_version_seen=fixture_seen, **kwargs)

    m1_no_quote = _historical_match(
        db, "NoQuote", datetime(2026, 9, 25, 13, 0, tzinfo=UTC)
    )
    m2_value = _historical_match(db, "Value", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    m3_veto = _historical_match(
        db,
        "Veto",
        datetime(2026, 9, 25, 15, 0, tzinfo=UTC),
        status="WALKOVER",
    )
    m4_tracking = _historical_match(
        db, "Tracking", datetime(2026, 9, 25, 16, 0, tzinfo=UTC)
    )
    m5_guide = _historical_match(db, "Guide", datetime(2026, 9, 25, 17, 0, tzinfo=UTC))

    run_id = _seed_run(
        db,
        issued_at,
        [
            (m1_no_quote, 1, "0.620", 1, "0.600"),
            (m2_value, 1, "0.620", 1, "0.600"),
            (m3_veto, 2, "0.620", 2, "0.600"),
            (m4_tracking, 1, "0.700", 1, "0.550"),
            (m5_guide, 1, "0.620", 1, "0.600"),
        ],
        configs,
    )
    for mid, side in ((m1_no_quote, 1), (m2_value, 1), (m3_veto, 2), (m5_guide, 1)):
        _seed_manager(
            db,
            run_id,
            mid,
            issued_at,
            status="CONSENSUS",
            side=side,
            prob="0.62",
        )
    _seed_manager(
        db,
        run_id,
        m4_tracking,
        issued_at,
        status="NO_BET",
        reason="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
        spread="0.15",
    )

    # m2 is a real Value Bet. m3 qualifies for Market but the WALKOVER status
    # triggers the kill-only Veto, so its persisted status must be VETOED.
    for mid, prices in (
        (m2_value, ("2.05", "1.75")),
        (m3_veto, ("1.85", "2.10")),
        (m5_guide, ("1.55", "2.40")),
    ):
        _odds(db, mid, "PLAYER_1", "2.00", f"sharp-{mid}", "SHARP", "PINNACLE", issued_at)
        _odds(db, mid, "PLAYER_2", "2.00", f"sharp-{mid}", "SHARP", "PINNACLE", issued_at)
        _odds(db, mid, "PLAYER_1", prices[0], f"offer-{mid}", "SOFT", "UNIBET", issued_at)
        _odds(db, mid, "PLAYER_2", prices[1], f"offer-{mid}", "SOFT", "UNIBET", issued_at)

    ensure_market_policy_version_v1(db)
    market_policy = db.execute(
        "SELECT market_policy_version_id FROM market_policy_versions "
        "ORDER BY market_policy_version_id LIMIT 1"
    ).fetchone()[0]
    evaluate_and_persist_market_slate_with_veto(
        db,
        prediction_run_id=run_id,
        market_policy_version_id=market_policy,
        decision_as_of=issued_at,
        market_execution_now=market_at,
        sharp_reference=SHARP,
    )
    persisted = db.execute(
        "SELECT match_id, decision_status::text FROM decisions "
        "WHERE prediction_run_id = %s ORDER BY match_id",
        (run_id,),
    ).fetchall()
    assert (m3_veto, "VETOED") in persisted
    assert (m2_value, "BET") in persisted
    assert (m1_no_quote, "NO_BET") in persisted

    # Real guide quotes: m2 was available before issue; m3 first appeared after
    # issue (must be labelled as a guide, not publication-time odds).
    _guide_quote(
        db,
        match_id=m2_value,
        side=1,
        price="2.05",
        bookmaker="UNIBET",
        snapshot="guide-m2",
        observed=datetime(2026, 9, 25, 8, 50, tzinfo=UTC),
        received=datetime(2026, 9, 25, 8, 55, tzinfo=UTC),
    )
    _guide_quote(
        db,
        match_id=m3_veto,
        side=2,
        price="2.10",
        bookmaker="UNIBET",
        snapshot="guide-m3",
        observed=datetime(2026, 9, 25, 9, 30, tzinfo=UTC),
        received=datetime(2026, 9, 25, 9, 35, tzinfo=UTC),
    )
    _guide_quote(
        db,
        match_id=m5_guide,
        side=1,
        price="1.55",
        bookmaker="UNIBET",
        snapshot="guide-m5",
        observed=datetime(2026, 9, 25, 8, 45, tzinfo=UTC),
        received=datetime(2026, 9, 25, 8, 50, tzinfo=UTC),
    )

    result_at = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    _result(db, m1_no_quote, "COMPLETED", 1, result_at)
    _result(db, m2_value, "COMPLETED", 2, result_at)
    _result(db, m5_guide, "WALKOVER", 1, result_at)

    state = get_issued_day_state(db, TEST_DATE)
    market = get_issued_market_decisions(db, TEST_DATE)
    guide = get_issued_guide_odds(db, decision_date=TEST_DATE)

    # The production loader must include the VETOED row, not only BET rows.
    assert market[m3_veto].decision_status == "VETOED"
    assert market[m2_value].decision_status == "BET"
    assert m1_no_quote not in market  # NO_BET is evidence, not a primary card
    assert set(guide) == {m2_value, m3_veto, m5_guide}
    assert guide[m2_value].selected_side == 1
    assert guide[m3_veto].selected_side == 2
    assert guide[m3_veto].issued_before_quote is True  # quote arrived after issue

    result_rows = load_aligned_daily_model_picks(db, decision_date=TEST_DATE)
    result_by_match = {
        row.match_id: row.result_status
        for row in result_rows
        if row.match_id is not None
    }
    picks = build_today_picks(state.matches, market, guide, result_by_match)
    by_match = {pick.match_id: pick for pick in picks.picks}
    assert len(by_match) == len(picks.picks) == 5

    assert by_match[m2_value].kind == VALUE_BET
    assert by_match[m2_value].is_actionable_bet is True
    assert by_match[m2_value].guide_odds_price == Decimal("2.05")
    assert by_match[m2_value].guide_bookmaker == "UNIBET"

    blocked = by_match[m3_veto]
    assert blocked.kind == BLOCKED
    assert blocked.is_actionable_bet is False
    assert blocked.stake_text is None
    assert blocked.guide_quote_after_issue is True
    assert blocked.guide_odds_note.startswith("Quote first available after")

    tracking = by_match[m4_tracking]
    assert tracking.kind == MODEL_PICK
    assert tracking.is_wide_spread is True
    assert tracking.win_probability is None
    assert tracking.is_actionable_bet is False
    assert tracking.stake_text is None

    assert by_match[m1_no_quote].kind == MODEL_PICK
    assert by_match[m1_no_quote].guide_odds_price is None
    assert by_match[m1_no_quote].guide_odds_display_text == "Unavailable"

    # Count invariant, explicitly including the blocked row.
    assert picks.manager_qualified_selection_count == 4
    assert picks.qualified_model_pick_count == 3
    assert picks.blocked_count == 1
    assert picks.wide_spread_model_pick_count == 1
    assert picks.total_model_pick_count == 4
    assert picks.value_bet_count == 1
    assert picks.total == 5
    assert picks.manager_qualified_selection_count == (
        picks.qualified_model_pick_count + picks.blocked_count
    )

    # Exercise the real read routes, not only the pure builder.
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    monkeypatch.setattr(dashboard_app, "_connect", lambda: _KeepOpen(db))
    monkeypatch.setattr(dashboard_app, "_latest_activity", lambda: None)
    monkeypatch.setattr(dashboard_app, "_load_live_health", lambda: None)
    monkeypatch.setattr(dashboard_app, "_load_upcoming", list)
    client = TestClient(dashboard_app.app)

    html = client.get(f"/today?date={TEST_DATE.isoformat()}")
    assert html.status_code == 200
    page = html.text
    assert "4 Manager-qualified selections" in page
    assert page.count('<article class="pick-card') == 4
    assert page.count('<article class="no-bet-card">') == 1
    assert 'id="tracking-only-disclosure"' in page
    assert "NOT BETTING" in page and "Blocked by safety check" in page
    assert "2.05" in page and "21/20" in page
    assert "UNIBET" in page
    assert "1.55" in page and "11/20" in page
    assert "No verified price for this match and player" in page
    assert "First available after selection" in page

    api = client.get(f"/api/dashboard/today?date={TEST_DATE.isoformat()}")
    assert api.status_code == 200
    body = api.json()
    counts = body["pick_counts"]
    assert counts["total"] == len(body["picks"]) == 5
    assert counts["manager_qualified_selections"] == 4
    assert counts["qualified_model_picks"] == 3
    assert counts["blocked"] == 1
    assert counts["wide_spread_model_picks"] == 1
    assert counts["total_model_picks"] == 4
    assert counts["value_bets"] == 1
    assert counts["manager_qualified_selections"] == (
        counts["qualified_model_picks"] + counts["blocked"]
    )

    api_blocked = next(
        pick
        for pick in body["picks"]
        if pick["kind"] == BLOCKED
    )
    assert api_blocked["is_actionable_bet"] is False
    assert api_blocked["stake"] is None
    assert api_blocked["guide_odds"] == "2.10"
    assert api_blocked["guide_quote_after_issue"] is True
    assert api_blocked["result_status"] == "PENDING"

    api_tracking = next(
        pick for pick in body["picks"] if pick["probability_basis"] == "A1_A2_TRACKING_AVERAGE"
    )
    assert api_tracking["is_actionable_bet"] is False
    assert api_tracking["stake"] is None
    assert api_tracking["tracking_probability"] is not None

    # Real outcomes render from the aligned results ledger.
    result_by_player = {pick["player"]: pick["result_status"] for pick in body["picks"]}
    assert result_by_player["NoQuote A"] == "WON"
    assert result_by_player["Value A"] == "LOST"
    assert result_by_player["Guide A"] == "VOID"

    value = client.get(f"/api/dashboard/value?date={TEST_DATE.isoformat()}")
    assert value.status_code == 200
    value_body = value.json()
    assert [pick["player"] for pick in value_body["value_bets"]] == ["Value A"]
    assert [pick["player"] for pick in value_body["blocked"]] == ["Veto B"]

    # Historical read-only Value routes still compose against the same DB.
    assert client.get("/value").status_code == 200
    monkeypatch.setattr(daily_history, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "_connect", lambda: _KeepOpen(db))
    monkeypatch.setattr(daily_history, "_latest_activity", lambda: None)
    monkeypatch.setattr(model_pick_history, "DEMO_MODE", False)
    monkeypatch.setattr(model_pick_history, "_connect", lambda: _KeepOpen(db))
    monkeypatch.setattr(model_pick_history, "_latest_activity", lambda: None)
    production_client = TestClient(production_app)
    assert production_client.get("/value-bet-results").status_code == 200
    assert production_client.get("/model-pick-results").status_code == 200
