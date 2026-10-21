"""Brian's read-only Guide Odds contract for mobile Tennis Model Picks."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.guide_odds import (
    GuideOddsQuote,
    get_issued_guide_odds,
)
from sports_edge_tennis.dashboard.market_overlay import MarketDecisionView
from sports_edge_tennis.dashboard.picks import build_today_picks
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.read_model import MatchView

NOW = datetime(2026, 10, 19, 7, 10, tzinfo=UTC)


def _insert_offer(db, *, match_id, observed, received, price, tag):
    """Persist one complete OFFERED quote with its raw/receipt lineage."""
    ingest_id = db.execute(
        """
        INSERT INTO ingest_runs (
            source_system, source_dataset, started_at, finished_at, status
        ) VALUES ('ODDSPAPI', %s, %s, %s, 'COMPLETED')
        RETURNING ingest_run_id
        """,
        (f"test_guide_{tag}", observed, received),
    ).fetchone()[0]
    mapping_id = db.execute(
        """
        INSERT INTO provider_bookmaker_mappings (
            provider_code, provider_bookmaker_key, mapping_version,
            bookmaker_code, normalization_class, effective_from
        ) VALUES ('ODDSPAPI', 'bet365', 1, 'BET365', 'OFFERED', %s)
        RETURNING provider_bookmaker_mapping_id
        """,
        (observed - timedelta(days=30),),
    ).fetchone()[0]
    raw_id = db.execute(
        """
        INSERT INTO raw_odds_observations (
            ingest_run_id, provider_code, provider_event_id,
            provider_observation_key, received_at, observation_time,
            observation_time_basis, payload_hash, payload_json,
            adapter_version, normalizer_version
        ) VALUES (%s, 'ODDSPAPI', %s, %s,
                  %s, %s, 'RECEIVE_TIME', %s, '{}'::jsonb,
                  'test', 'test')
        RETURNING raw_odds_observation_id
        """,
        (ingest_id, f"test-event-{tag}", f"test-observation-{tag}",
         received, received, f"test-sha-{tag}"),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type,
            selection_type, decimal_price, market_status,
            source_snapshot_id, source_observed_at, as_of, ingested_at,
            raw_odds_observation_id, provider_bookmaker_mapping_id, odds_origin
        ) VALUES
            (%s, 'OFFERED', 'BET365', 'MATCH_WINNER', 'PLAYER_1', %s,
             'OPEN', %s, %s, %s, %s, %s, %s, 'LIVE_PROVIDER'),
            (%s, 'OFFERED', 'BET365', 'MATCH_WINNER', 'PLAYER_2', %s,
             'OPEN', %s, %s, %s, %s, %s, %s, 'LIVE_PROVIDER')
        """,
        (match_id, price, f"test-price-{tag}-1", observed, received, received,
         raw_id, mapping_id,
         match_id, Decimal("2.10"), f"test-price-{tag}-2", observed, received,
         received, raw_id, mapping_id),
    )


def _match(*, match_id=11, selected_side=1):
    return MatchView(
        match_id=match_id,
        player_1="Player A",
        player_2="Player B",
        tournament="ATP Example",
        competition_tier="ATP_250",
        scheduled_start=NOW,
        agents={},
        manager_status="CONSENSUS",
        selected_side=selected_side,
        consensus_probability=Decimal("0.80"),
        reason_code="NONE",
        active_agent_count=2,
        abstaining_agent_count=1,
        player_1_price=None,
        player_2_price=None,
        sharp_source_observed_at=None,
    )


def _quote(match_id=11, side=1, price="1.50"):
    return GuideOddsQuote(
        match_id=match_id,
        selected_side=side,
        decimal_price=Decimal(price),
        bookmaker_code="BET365",
        observed_at=NOW,
        odds_snapshot_id=912,
        issued_before_quote=False,
    )


def _bet(match_id=11, side=1):
    return MarketDecisionView(
        match_id=match_id,
        decision_status="BET",
        reason_code="NONE",
        selected_side=side,
        market_decimal_price=Decimal("1.65"),
        sharp_reference_price=Decimal("1.55"),
        price_bookmaker_code="UNIBET",
        edge_pct=Decimal("0.05"),
        min_acceptable_odds=Decimal("1.59"),
    )


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return self

    def fetchall(self):
        return self.rows


def test_guide_lookup_uses_same_issued_picks_and_refuses_mismatched_market_evidence():
    conn = _Connection([
        (11, 1, Decimal("1.50"), "BET365", NOW, 912, NOW, False),
        (12, 2, Decimal("2.75"), "UNIBET", NOW, 913, NOW, True),
        (13, 1, None, None, None, None, None, None),
        (14, 1, Decimal("0.90"), "BET365", NOW, 915, NOW, False),
    ])
    result = get_issued_guide_odds(
        conn, decision_date=date(2026, 10, 19), timezone_name="Asia/Manila"
    )

    assert list(result) == [11, 12]
    assert result[11].decimal_price == Decimal("1.50")
    assert result[12].issued_before_quote is True
    assert conn.params == ("Asia/Manila", "Asia/Manila", date(2026, 10, 19))
    sql = conn.sql
    for invariant in (
        "aligned_model_picks AS",
        "o.match_id = p.match_id",
        "o.source_system = 'OFFERED'",
        "o.odds_origin = 'LIVE_PROVIDER'",
        "o.market_type = 'MATCH_WINNER'",
        "o.market_status = 'OPEN'",
        "o.selection_type::text",
        "mv.player_1_id = latest_mv.player_1_id",
        "mv.player_2_id = latest_mv.player_2_id",
        "GREATEST(o.as_of, o.ingested_at, o.source_observed_at)",
        "o.bookmaker_code IN ('BET365', 'UNIBET', '1XBET')",
        "mv.player_1_id = latest_mv.player_1_id",
        "p.picked_at < mv.match_start_at",
        "JOIN LATERAL",
        "mvq.match_id = p.match_id",
        "mvq.as_of <= GREATEST(",
        "mvq.ingested_at <= GREATEST(",
        "<= quote_mv.match_start_at",
    ):
        assert invariant in sql
    assert "p.picked_at + INTERVAL '24 hours'" not in sql
    assert "<= mv.match_start_at" not in sql
    assert "o.source_observed_at >= p.picked_at - INTERVAL '12 hours'" in sql
    assert "o.as_of <= CURRENT_TIMESTAMP" in sql
    assert "o.ingested_at <= CURRENT_TIMESTAMP" in sql
    assert "g.ingested_at" in sql




def test_read_only_guide_query_executes_on_postgres_schema(db):
    """Catch schema/SQL contract errors without buying odds or changing rows."""
    guide = get_issued_guide_odds(
        db, decision_date=date(2026, 10, 19), timezone_name="UTC"
    )
    assert isinstance(guide, dict)

def test_model_pick_receives_observed_guide_odds_without_becoming_value_bet():
    (pick,) = build_today_picks(
        [_match()], {}, {11: _quote()}, {11: "WON"}
    ).picks

    assert pick.kind == "MODEL_PICK"
    assert not pick.is_actionable_bet
    assert pick.guide_odds_display_text == "1.50"
    assert pick.guide_bookmaker == "BET365"
    assert pick.guide_odds_source == "OFFERED_LIVE_PROVIDER"
    assert pick.result_display_text == "Won"
    assert pick.stake_text is None


@pytest.mark.parametrize(
    "wrong_quote",
    [_quote(match_id=999), _quote(side=2)],
)
def test_wrong_match_or_other_player_quote_is_unavailable(wrong_quote):
    (pick,) = build_today_picks([_match()], {}, {11: wrong_quote}).picks
    assert pick.guide_odds_price is None
    assert pick.guide_odds_display_text == "Unavailable"


def test_winner_side_two_uses_correct_price_and_provenance():
    (pick,) = build_today_picks([_match(selected_side=2)], {}, {11: _quote(side=2)}).picks
    assert pick.player == "Player B"
    assert pick.guide_odds_display_text == "1.50"


def test_issued_bet_price_fallback_requires_matching_selected_side():
    (matching,) = build_today_picks([_match()], {11: _bet()}).picks
    (mismatch,) = build_today_picks([_match()], {11: _bet(side=2)}).picks
    assert matching.guide_odds_display_text == "1.65"
    assert matching.guide_odds_source == "ISSUED_BET"
    assert mismatch.guide_odds_price is None
    assert mismatch.guide_odds_display_text == "Unavailable"


def test_no_quote_never_converts_80_percent_prediction_to_implied_price():
    (pick,) = build_today_picks([_match()], {}, {}).picks
    assert pick.probability_display_text == "80%"
    assert pick.guide_odds_display_text == "Unavailable"
    assert pick.guide_odds_price is None


def test_current_client_hides_value_nav_but_preserves_read_only_old_routes(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)
    body = client.get("/today").text
    assert 'href="/value"' not in body
    assert 'href="/value-bet-results"' not in body
    assert 'href="/today"' in body
    assert "Guide odds" in body
    assert "Unavailable" in body
    assert "Result" in body
    assert "1.72" in body  # Original price for demo issued bet.
    assert client.get("/value").status_code == 200
    assert client.get("/value-bet-results").status_code == 200

    api = client.get("/api/dashboard/today").json()
    assert all("guide_odds" in pick for pick in api["picks"])
    assert all("result_status" in pick for pick in api["picks"])
    assert all("match_id" not in pick for pick in api["picks"])


def test_real_postgres_quote_has_match_side_source_and_receipt_provenance(db):
    """True PostgreSQL join, not only a mocked response or a zero-row query."""
    from test_model_pick_alignment import _seed_pick_at_start

    start = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    issued = start - timedelta(hours=4)
    received = issued + timedelta(minutes=30)
    observed = issued + timedelta(minutes=20)
    match_id = _seed_pick_at_start(db, label="GuideQuote", start=start)
    ingest_id = db.execute(
        """
        INSERT INTO ingest_runs (
            source_system, source_dataset, started_at, finished_at, status
        ) VALUES ('ODDSPAPI', 'test_guide_quote', %s, %s, 'COMPLETED')
        RETURNING ingest_run_id
        """,
        (observed, received),
    ).fetchone()[0]
    mapping_id = db.execute(
        """
        INSERT INTO provider_bookmaker_mappings (
            provider_code, provider_bookmaker_key, mapping_version,
            bookmaker_code, normalization_class, effective_from
        ) VALUES ('ODDSPAPI', 'bet365', 1, 'BET365', 'OFFERED', %s)
        RETURNING provider_bookmaker_mapping_id
        """,
        (issued - timedelta(days=30),),
    ).fetchone()[0]
    raw_id = db.execute(
        """
        INSERT INTO raw_odds_observations (
            ingest_run_id, provider_code, provider_event_id,
            provider_observation_key, received_at, observation_time,
            observation_time_basis, payload_hash, payload_json,
            adapter_version, normalizer_version
        ) VALUES (%s, 'ODDSPAPI', 'test-event', 'test-observation',
                  %s, %s, 'RECEIVE_TIME', 'test-sha', '{}'::jsonb,
                  'test', 'test')
        RETURNING raw_odds_observation_id
        """,
        (ingest_id, received, received),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type,
            selection_type, decimal_price, market_status,
            source_snapshot_id, source_observed_at, as_of, ingested_at,
            raw_odds_observation_id, provider_bookmaker_mapping_id, odds_origin
        ) VALUES
            (%s, 'OFFERED', 'BET365', 'MATCH_WINNER', 'PLAYER_1', 1.50,
             'OPEN', 'test-price-1', %s, %s, %s, %s, %s, 'LIVE_PROVIDER'),
            (%s, 'OFFERED', 'BET365', 'MATCH_WINNER', 'PLAYER_2', 2.65,
             'OPEN', 'test-price-2', %s, %s, %s, %s, %s, 'LIVE_PROVIDER')
        """,
        (match_id, observed, received, received, raw_id, mapping_id,
         match_id, observed, received, received, raw_id, mapping_id),
    )
    quotes = get_issued_guide_odds(db, decision_date=date(2026, 10, 18))
    assert set(quotes) == {match_id}
    quote = quotes[match_id]
    assert quote.selected_side == 1
    assert quote.decimal_price == Decimal("1.50")
    assert quote.bookmaker_code == "BET365"
    assert quote.issued_before_quote is True
    assert quote.available_at == received
    assert quote.observed_at == observed
    assert quote.odds_snapshot_id > 0

    # The source kept its ORIGINAL selected side even if the provider
    # publishes a later correction swapping player 1 and player 2.
    old = db.execute(
        """
        SELECT player_1_id, player_2_id
        FROM match_versions WHERE match_id = %s
        ORDER BY match_version_id LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    correction = issued + timedelta(minutes=45)
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code,
            best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32',
                3, FALSE, 'SCHEDULED', %s, %s, %s)
        """,
        (match_id, old[1], old[0], start,
         correction, correction, correction),
    )
    # Guard: Do not display a pre-swap PLAYER_1 quote next to a
    # reoriented/ambiguous fixture identity.
    after_swap = get_issued_guide_odds(db, decision_date=date(2026, 10, 18))
    assert match_id not in after_swap


def test_pre_match_quote_more_than_24h_after_issue_remains_available(db):
    """A long-horizon pick keeps a valid quote observed after the old cap."""
    from test_performance_regime_cohorting import (
        _policy_id,
        _seed_match,
        _seed_run,
    )

    from sports_edge_tennis.manager import evaluate_and_persist_manager_consensus

    start = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    issued = start - timedelta(hours=30)
    match_id = _seed_match(db, label="LongHorizon", start=start)
    run_id = _seed_run(db, match_id=match_id, as_of=issued)
    outcome = evaluate_and_persist_manager_consensus(
        db,
        prediction_run_id=run_id,
        manager_policy_version_id=_policy_id(db, "v1"),
    )
    assert outcome.status == "PERSISTED"

    observed = start - timedelta(hours=4)
    received = observed + timedelta(minutes=1)
    ingest_id = db.execute(
        """
        INSERT INTO ingest_runs (
            source_system, source_dataset, started_at, finished_at, status
        ) VALUES ('ODDSPAPI', 'test_guide_quote_long', %s, %s, 'COMPLETED')
        RETURNING ingest_run_id
        """,
        (observed, received),
    ).fetchone()[0]
    mapping_id = db.execute(
        """
        INSERT INTO provider_bookmaker_mappings (
            provider_code, provider_bookmaker_key, mapping_version,
            bookmaker_code, normalization_class, effective_from
        ) VALUES ('ODDSPAPI', 'bet365', 1, 'BET365', 'OFFERED', %s)
        RETURNING provider_bookmaker_mapping_id
        """,
        (issued - timedelta(days=30),),
    ).fetchone()[0]
    raw_id = db.execute(
        """
        INSERT INTO raw_odds_observations (
            ingest_run_id, provider_code, provider_event_id,
            provider_observation_key, received_at, observation_time,
            observation_time_basis, payload_hash, payload_json,
            adapter_version, normalizer_version
        ) VALUES (%s, 'ODDSPAPI', 'test-event-long', 'test-observation-long',
                  %s, %s, 'RECEIVE_TIME', 'test-sha-long', '{}'::jsonb,
                  'test', 'test')
        RETURNING raw_odds_observation_id
        """,
        (ingest_id, received, received),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type,
            selection_type, decimal_price, market_status,
            source_snapshot_id, source_observed_at, as_of, ingested_at,
            raw_odds_observation_id, provider_bookmaker_mapping_id, odds_origin
        ) VALUES
            (%s, 'OFFERED', 'BET365', 'MATCH_WINNER', 'PLAYER_1', 1.80,
             'OPEN', 'test-price-long-1', %s, %s, %s, %s, %s, 'LIVE_PROVIDER'),
            (%s, 'OFFERED', 'BET365', 'MATCH_WINNER', 'PLAYER_2', 2.05,
             'OPEN', 'test-price-long-2', %s, %s, %s, %s, %s, 'LIVE_PROVIDER')
        """,
        (match_id, observed, received, received, raw_id, mapping_id,
         match_id, observed, received, received, raw_id, mapping_id),
    )

    quotes = get_issued_guide_odds(db, decision_date=date(2026, 10, 18))
    assert set(quotes) == {match_id}
    quote = quotes[match_id]
    assert quote.selected_side == 1
    assert quote.decimal_price == Decimal("1.80")
    assert quote.issued_before_quote is True
    assert quote.observed_at == observed


def _reschedule(db, match_id, *, start, seen):
    old = db.execute(
        """
        SELECT player_1_id, player_2_id
        FROM match_versions WHERE match_id = %s
        ORDER BY match_version_id LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code,
            best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32',
                3, FALSE, 'SCHEDULED', %s, %s, %s)
        """,
        (match_id, old[0], old[1], start, seen, seen, seen),
    )


def test_quote_after_original_start_but_before_rescheduled_start_is_available(db):
    """A schedule correction already known when the quote arrived is honoured."""
    from test_model_pick_alignment import _seed_pick_at_start

    original_start = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    revised_start = original_start + timedelta(hours=12)
    match_id = _seed_pick_at_start(
        db, label="Rescheduled", start=original_start
    )
    _reschedule(
        db, match_id, start=revised_start,
        seen=original_start + timedelta(minutes=30),
    )
    observed = original_start + timedelta(hours=1)
    _insert_offer(
        db, match_id=match_id, observed=observed,
        received=observed + timedelta(minutes=1),
        price=Decimal("1.80"), tag="rescheduled-on-time",
    )

    quotes = get_issued_guide_odds(db, decision_date=date(2026, 10, 18))
    assert set(quotes) == {match_id}
    assert quotes[match_id].decimal_price == Decimal("1.80")
    assert quotes[match_id].issued_before_quote is True


def test_late_quote_is_not_rescued_by_a_later_schedule_correction(db):
    """At quote time the old start was still in force, so the quote was late."""
    from test_model_pick_alignment import _seed_pick_at_start

    original_start = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    match_id = _seed_pick_at_start(
        db, label="LateThenRescheduled", start=original_start
    )
    observed = original_start + timedelta(hours=1)
    _insert_offer(
        db, match_id=match_id, observed=observed,
        received=observed + timedelta(minutes=1),
        price=Decimal("1.80"), tag="late-then-rescheduled",
    )
    _reschedule(
        db, match_id, start=original_start + timedelta(hours=12),
        seen=original_start + timedelta(hours=2),
    )

    quotes = get_issued_guide_odds(db, decision_date=date(2026, 10, 18))
    assert match_id not in quotes


@pytest.mark.parametrize(
    ("decimal_odds", "fractional_odds", "market_implied"),
    [
        ("1.01", "1/100", "99.0%"),
        ("1.50", "1/2", "66.7%"),
        ("1.64", "16/25", "61.0%"),
        ("2.50", "3/2", "40.0%"),
        ("4.00", "3/1", "25.0%"),
    ],
)
def test_observed_fractional_and_decimal_guide_odds_are_consistent(
    decimal_odds, fractional_odds, market_implied
):
    (pick,) = build_today_picks(
        [_match()], {}, {11: _quote(price=decimal_odds)}
    ).picks
    assert pick.kind == "MODEL_PICK"
    assert not pick.is_actionable_bet
    assert pick.guide_odds_fractional_text == fractional_odds
    assert pick.guide_odds_display_text == f"{Decimal(decimal_odds):.2f}"
    assert pick.guide_market_implied_probability_text == market_implied


def test_fractional_guide_unavailable_without_verified_bookmaker_price():
    (pick,) = build_today_picks([_match()], {}, {}).picks
    assert pick.probability_display_text == "80%"
    assert pick.guide_odds_fractional_text == "Unavailable"
    assert pick.guide_market_implied_probability_text is None


def test_fractional_odds_in_mobile_html_and_today_api(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)
    html = client.get("/today")
    assert html.status_code == 200
    assert "Guide odds · fractional" in html.text
    assert "not how many people placed bets" in html.text
    assert "Decimal" in html.text
    api = client.get("/api/dashboard/today")
    assert api.status_code == 200
    for pick in api.json()["picks"]:
        assert "guide_odds_fractional" in pick
        assert "guide_market_implied_probability" in pick
        if pick["guide_odds"] is None:
            assert pick["guide_odds_fractional"] is None
            assert pick["guide_market_implied_probability"] is None
