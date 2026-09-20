"""Performance must be attributable to the regime that produced it.

Pooling a Manager v1 bet with a Manager v2 bet answers neither "how is the
current system doing" nor "how did the previous one do". These tests pin the
cohorting so the two questions stay separately answerable, and so historical
truth is never rewritten to make the current number look different.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.dashboard.daily_bets import (
    build_performance_windows,
    load_daily_bet_summaries,
    load_daily_bets,
)
from sports_edge_tennis.manager import (
    ensure_manager_policy_version_v1,
    evaluate_and_persist_manager_consensus,
)
from sports_edge_tennis.market import ensure_market_policy_version_v1
from sports_edge_tennis.performance.regimes import (
    DATA_PROVIDER_UNAVAILABLE,
    LEGACY_REGIME_KEY,
    RegimeCohort,
    current_regime_started_at,
    load_regime_cohorts,
    regime_key_for,
    regime_label_for,
    resolve_current_regime,
)

AGENTS = (
    "AGENT_1_SURFACE_ELO",
    "AGENT_2_HIERARCHICAL_MARKOV",
    "AGENT_3_INTRANSITIVITY",
)
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _val(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _policy_id(conn: psycopg.Connection, version: str) -> int:
    """Resolve a Manager policy version, seeding reference data if truncated.

    The v1 row comes from its production helper and the v2 row mirrors
    migration 0014 exactly, so the fixture cannot drift from the real policy
    semantics the Manager enforces.
    """
    if version == "v1":
        ensure_manager_policy_version_v1(conn)
    else:
        conn.execute(
            """
            INSERT INTO manager_policy_versions (
                policy_key, policy_version, consensus_mode,
                allow_agent3_abstention, three_agent_max_probability_spread,
                agent3_abstention_max_probability_spread, min_active_agent_probability
            )
            VALUES ('tennis_manager_consensus', %s, 'UNANIMOUS_ACTIVE_AGENTS',
                    FALSE, 0.080000, 0.000000, 0.500000)
            ON CONFLICT (policy_key, policy_version) DO NOTHING
            """,
            (version,),
        )
    return _val(
        conn,
        """
        SELECT manager_policy_version_id
        FROM manager_policy_versions
        WHERE policy_key = 'tennis_manager_consensus' AND policy_version = %s
        """,
        (version,),
    )


def _decision_policy_id(conn: psycopg.Connection) -> int:
    """Reference-data row required by the decisions FK. Values are immaterial to
    regime cohorting; only its existence is."""
    existing = conn.execute(
        "SELECT decision_policy_version_id FROM decision_policy_versions LIMIT 1"
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    return _val(
        conn,
        """
        INSERT INTO decision_policy_versions (
            policy_key, policy_version, consensus_mode, allow_agent3_abstention,
            max_probability_spread, min_active_agent_probability,
            requires_data_sufficiency, min_recent_matches_with_serve_stats,
            odds_feed_max_staleness_minutes, match_data_max_staleness_hours,
            ratings_max_staleness_hours, fixture_feed_max_staleness_hours,
            atp_main_all_agents_min_edge_pct, atp_main_agent3_abstain_min_edge_pct,
            challenger_min_edge_pct, default_devig_method,
            sharp_reference_policy_code, veto_policy_code,
            publication_requires_min_acceptable_odds, publish_retractions_required
        )
        VALUES (
            'tennis_decision_policy', 'regime-fixture-v1', 'UNANIMOUS_ACTIVE_AGENTS',
            TRUE, 0.080000, 0.500000, TRUE, 10, 30, 12, 12, 12,
            0.030000, 0.040000, 0.050000, 'POWER',
            'PINNACLE_ONLY', 'VETO_V1', TRUE, FALSE
        )
        RETURNING decision_policy_version_id
        """,
    )


def _seed_match(conn: psycopg.Connection, *, label: str, start: datetime) -> int:
    p1 = _val(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (f"{label} One",),
    )
    p2 = _val(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (f"{label} Two",),
    )
    tid = _val(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250') RETURNING tournament_id
        """,
        (f"{label} Open",),
    )
    eid = _val(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, %s) RETURNING tournament_edition_id
        """,
        (tid, f"{label} Open 2026"),
    )
    mid = _val(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (eid,),
    )
    conn.execute(
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
        (mid, p1, p2, start, BASE, BASE, BASE),
    )
    return int(mid)


def _seed_run(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
    experiment_id: str | None = None,
) -> int:
    finalized = as_of + timedelta(minutes=10)
    with conn.transaction():
        conn.execute(
            "SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED"
        )
        run_id = _val(
            conn,
            """
            INSERT INTO prediction_runs (
                run_status, as_of, started_at, prediction_contract_version, experiment_id
            )
            VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1', %s)
            RETURNING prediction_run_id
            """,
            (as_of, as_of, experiment_id),
        )
        conn.execute(
            "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
            (run_id, match_id),
        )
        for agent in AGENTS:
            cfg = _val(
                conn,
                """
                INSERT INTO agent_config_versions (
                    agent_code, config_version, model_version, feature_version,
                    parameter_schema_version, config_payload
                )
                VALUES (%s, %s, 'regime-fixture', 'regime-feature', 'params-v1',
                        '{"regime_fixture": true}'::jsonb)
                RETURNING agent_config_version_id
                """,
                (agent, f"{agent.lower()}-regime-{run_id}"),
            )
            conn.execute(
                """
                INSERT INTO predictions (
                    match_id, prediction_run_id, agent_config_version_id, agent_code,
                    prediction_status, selection_side, win_probability,
                    model_version, feature_version, as_of, prediction_locked_at,
                    probability_representation, experiment_id
                )
                VALUES (%s, %s, %s, %s, 'PREDICTED', 1, 0.60,
                        'regime-fixture', 'regime-feature', %s, %s, %s, %s)
                """,
                (
                    match_id, run_id, cfg, agent, as_of, finalized,
                    AGENT_PROBABILITY_REPRESENTATIONS[agent], experiment_id,
                ),
            )
        conn.execute(
            """
            UPDATE prediction_runs
            SET run_status = 'COMPLETED', prediction_finalized_at = %s, ended_at = %s
            WHERE prediction_run_id = %s
            """,
            (finalized, finalized + timedelta(minutes=1), run_id),
        )
    return int(run_id)


def _seed_bet(
    conn: psycopg.Connection,
    *,
    label: str,
    policy_version: str,
    match_day: date,
    price: str,
    settlement: str | None,
    published: bool = True,
    experiment_id: str | None = None,
    price_source_system: str | None = "OFFERED",
) -> int:
    """One production Type A BET produced under a named Manager policy."""
    start = datetime(
        match_day.year, match_day.month, match_day.day, 12, 0, tzinfo=timezone.utc
    )
    as_of = start - timedelta(hours=4)
    match_id = _seed_match(conn, label=label, start=start)
    run_id = _seed_run(
        conn, match_id=match_id, as_of=as_of, experiment_id=experiment_id
    )

    outcome = evaluate_and_persist_manager_consensus(
        conn,
        prediction_run_id=run_id,
        manager_policy_version_id=_policy_id(conn, policy_version),
    )
    assert outcome.status == "PERSISTED"
    manager_id = _val(
        conn,
        """
        SELECT manager_consensus_result_id FROM manager_consensus_results
        WHERE prediction_run_id = %s AND match_id = %s
        """,
        (run_id, match_id),
    )
    market_policy = ensure_market_policy_version_v1(conn)
    decision_policy = _decision_policy_id(conn)
    decision_id = _val(
        conn,
        """
        INSERT INTO decisions (
            match_id, prediction_run_id, decision_policy_version_id,
            manager_consensus_result_id, market_policy_version_id,
            snapshot_lineage_hash, signal_type, decision_status, reason_code,
            market_type, selection_type, selection_side,
            consensus_probability, consensus_fair_odds, market_decimal_price,
            sharp_reference_price, price_source_system, price_bookmaker_code,
            devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count,
            model_version, feature_version, experiment_id,
            as_of, prediction_locked_at, published_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            'TYPE_A_MODEL_EDGE', 'BET', 'NONE',
            'MATCH_WINNER', 'PLAYER_1', 1,
            0.60, 1.666667, %s, 1.60, %s, 'BOOKIE',
            'MULTIPLICATIVE', 1.60, 0.05, 1.50,
            3, 0, 'regime-fixture', 'regime-feature', %s,
            %s, %s, %s
        )
        RETURNING decision_id
        """,
        (
            match_id, run_id, decision_policy, manager_id,
            market_policy.market_policy_version_id,
            f"lineage-{label}", price, price_source_system, experiment_id,
            as_of, as_of + timedelta(minutes=10),
            (as_of + timedelta(minutes=20)) if published else None,
        ),
    )
    if settlement is not None:
        conn.execute(
            """
            INSERT INTO decision_settlements (
                decision_id, settlement_status, settled_at
            )
            VALUES (%s, %s, %s)
            """,
            (decision_id, settlement, start + timedelta(hours=3)),
        )
    return int(decision_id)


class _KeepOpen:
    """The dashboard routes close the connection they are handed.

    The test fixture's connection is shared across assertions, so hand the
    routes a proxy whose close() is a no-op instead of monkeypatching the
    route's own lifecycle.
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        return None


def _cohort(cohorts: tuple[RegimeCohort, ...], key: str) -> RegimeCohort:
    found = [c for c in cohorts if c.regime_key == key]
    assert found, f"no cohort {key} in {[c.regime_key for c in cohorts]}"
    return found[0]


# ----------------------------------------------------------- pure helpers


def test_regime_keys_and_labels_never_expose_database_ids() -> None:
    assert regime_key_for("v1") == "MANAGER_V1"
    assert regime_key_for("v2") == "MANAGER_V2"
    assert regime_key_for(None) == LEGACY_REGIME_KEY
    assert regime_label_for("v1") == "Manager v1"
    assert regime_label_for(None) == "Legacy production regime"
    for label in (regime_label_for("v1"), regime_label_for(None)):
        assert not any(ch.isdigit() for ch in label.replace("v1", ""))


def test_unproven_provider_is_reported_unavailable_not_guessed() -> None:
    cohort = RegimeCohort(
        regime_key="MANAGER_V1", manager_policy_version="v1", bets=1, settled=1,
        pending=0, wins=1, losses=0, voids=0, cancelled=0,
        one_unit_profit=Decimal("0.8"), first_bet_date=None, last_bet_date=None,
        odds_sources=(),
    )
    assert cohort.odds_provider_label == DATA_PROVIDER_UNAVAILABLE
    for banned in ("Sportradar", "Goalserve", "API-Tennis", "Pinnacle"):
        assert banned not in cohort.odds_provider_label


# ------------------------------------------------------- cohorting truth


def test_v1_and_v2_bets_form_separate_cohorts(db: psycopg.Connection) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    cohorts = load_regime_cohorts(db)

    assert {c.regime_key for c in cohorts} == {"MANAGER_V1", "MANAGER_V2"}
    v1, v2 = _cohort(cohorts, "MANAGER_V1"), _cohort(cohorts, "MANAGER_V2")
    assert (v1.bets, v1.wins, v1.losses) == (1, 1, 0)
    assert (v2.bets, v2.wins, v2.losses) == (0 + 1, 0, 1)
    # Original persisted price, not a recomputed one: 2.00 -> +1.00 on a win.
    assert v1.one_unit_profit == Decimal("1.00")
    assert v2.one_unit_profit == Decimal("-1")
    assert v1.label == "Manager v1" and v2.label == "Manager v2"


def test_current_system_excludes_previous_regime_bets(db: psycopg.Connection) -> None:
    """The central claim: Current System contains only the active regime."""
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    current = resolve_current_regime(db)
    assert current is not None and current.regime_key == "MANAGER_V2"

    current_only = load_daily_bet_summaries(db, regime_key=current.regime_key)
    all_history = load_daily_bet_summaries(db)

    assert sum(r.bets for r in current_only) == 1
    assert sum(r.wins for r in current_only) == 0
    assert sum(r.losses for r in current_only) == 1
    # The v1 win is absent from Current System but present in history.
    assert sum(r.bets for r in all_history) == 2
    assert sum(r.wins for r in all_history) == 1


def test_previous_regime_is_preserved_after_a_new_regime_appears(
    db: psycopg.Connection,
) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    before = load_regime_cohorts(db)
    v1_before = _cohort(before, "MANAGER_V1")

    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")
    after = load_regime_cohorts(db)
    v1_after = _cohort(after, "MANAGER_V1")

    # Adding a newer regime must not touch the older record in any way.
    assert v1_after == v1_before


def test_current_regime_resolves_from_production_lineage_not_environment(
    db: psycopg.Connection, monkeypatch
) -> None:
    _seed_bet(db, label="Only", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    # Environment claiming the target stack must not relabel persisted history.
    monkeypatch.setenv("TENNIS_MANAGER_POLICY_VERSION", "v2")
    monkeypatch.setenv("TENNIS_DATA_PROVIDER_MODE", "SPORTRADAR_V3")

    current = resolve_current_regime(db)

    assert current is not None
    assert current.regime_key == "MANAGER_V1"
    assert current.label == "Manager v1"


def test_no_production_history_yields_no_current_regime(db: psycopg.Connection) -> None:
    assert resolve_current_regime(db) is None
    assert load_regime_cohorts(db) == ()


# ------------------------------------------------ contamination boundaries


def test_experiment_bets_never_contaminate_production_performance(
    db: psycopg.Connection,
) -> None:
    _seed_bet(db, label="Real", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="Lab", policy_version="v1", match_day=date(2026, 9, 3),
              price="5.00", settlement="WON", experiment_id="BACKTEST_A")

    cohorts = load_regime_cohorts(db)
    summaries = load_daily_bet_summaries(db)

    assert _cohort(cohorts, "MANAGER_V1").bets == 1
    assert sum(r.bets for r in summaries) == 1
    assert sum(r.one_unit_profit for r in summaries) == Decimal("1.00")


def test_unpublished_modern_bet_rows_remain_excluded(db: psycopg.Connection) -> None:
    _seed_bet(db, label="Published", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="Unpublished", policy_version="v1", match_day=date(2026, 9, 4),
              price="3.00", settlement="WON", published=False)

    summaries = load_daily_bet_summaries(db)

    assert sum(r.bets for r in summaries) == 1
    assert _cohort(load_regime_cohorts(db), "MANAGER_V1").bets == 1


def test_repeat_hourly_bets_on_one_match_count_once(db: psycopg.Connection) -> None:
    """Cohorting must not let a later duplicate become a match's first bet."""
    _seed_bet(db, label="First", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    match_id = _val(db, "SELECT max(match_id) FROM matches")
    run_id = _val(db, "SELECT max(prediction_run_id) FROM prediction_runs")
    original = _val(
        db,
        "SELECT decision_id FROM decisions WHERE match_id = %s",
        (match_id,),
    )
    # A second, later BET decision for the very same sporting match.
    db.execute(
        """
        INSERT INTO decisions (
            match_id, prediction_run_id, decision_policy_version_id,
            manager_consensus_result_id, market_policy_version_id,
            snapshot_lineage_hash, signal_type, decision_status, reason_code,
            market_type, selection_type, selection_side,
            consensus_probability, consensus_fair_odds, market_decimal_price,
            sharp_reference_price, price_source_system, price_bookmaker_code,
            devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count,
            model_version, feature_version, as_of, prediction_locked_at, published_at
        )
        SELECT
            match_id, prediction_run_id, decision_policy_version_id,
            manager_consensus_result_id, market_policy_version_id,
            'lineage-dup', signal_type, decision_status, reason_code,
            market_type, selection_type, selection_side,
            consensus_probability, consensus_fair_odds, 9.00,
            sharp_reference_price, price_source_system, price_bookmaker_code,
            devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count,
            model_version, feature_version,
            as_of + interval '1 hour',
            prediction_locked_at + interval '1 hour',
            published_at + interval '1 hour'
        FROM decisions WHERE decision_id = %s
        """,
        (original,),
    )

    summaries = load_daily_bet_summaries(db)
    cohort = _cohort(load_regime_cohorts(db), "MANAGER_V1")

    assert sum(r.bets for r in summaries) == 1
    assert cohort.bets == 1
    # The original 2.00 price is used, never the later 9.00 duplicate.
    assert cohort.one_unit_profit == Decimal("1.00")

    cohort_filtered = load_daily_bet_summaries(db, regime_key="MANAGER_V1")
    assert sum(r.bets for r in cohort_filtered) == 1


# --------------------------------------------------- original bet identity


def test_historical_rows_carry_their_own_regime_and_original_terms(
    db: psycopg.Connection,
) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")

    (row,) = load_daily_bets(db, decision_date=date(2026, 9, 2))

    assert row.manager_policy_version == "v1"
    assert row.regime_key == "MANAGER_V1"
    assert row.market_decimal_price == Decimal("2.000000")
    assert row.selection == "Old One"
    assert row.settlement_status == "WON"
    assert row.profit_units == Decimal("1.000000")


def test_regime_windows_use_the_same_first_bet_ledger(db: psycopg.Connection) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    current = resolve_current_regime(db)
    windows = build_performance_windows(
        load_daily_bet_summaries(db, regime_key=current.regime_key),
        as_of_date=date(2026, 9, 10),
    )

    assert windows["all_time"].bets == 1
    assert windows["all_time"].losses == 1
    assert windows["all_time"].wins == 0


def test_regime_start_is_the_first_production_bet_of_that_regime(
    db: psycopg.Connection,
) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    started = current_regime_started_at(db, "MANAGER_V2")

    assert started is not None
    assert started.date() == date(2026, 9, 10)
    assert current_regime_started_at(db, "MANAGER_V1").date() == date(2026, 9, 2)


# -------------------------------------------------- client surface contract


def test_results_api_separates_current_regime_from_all_history(
    db: psycopg.Connection, monkeypatch
) -> None:
    """The API must expose the two questions as two different numbers."""
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.app as dashboard_app
    import sports_edge_tennis.dashboard.daily_history as daily_history
    from sports_edge_tennis.dashboard.production_app import app

    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "_connect", lambda: _KeepOpen(db))
    monkeypatch.setattr(daily_history, "_latest_activity", lambda: None)

    body = TestClient(app).get("/api/dashboard/bet-history").json()

    assert body["current_regime"]["regime_key"] == "MANAGER_V2"
    assert body["current_regime"]["label"] == "Manager v2"
    # Current regime: the single v2 loss only.
    assert body["current_regime_performance"]["all_time"]["bets"] == 1
    assert body["current_regime_performance"]["all_time"]["losses"] == 1
    assert body["current_regime_performance"]["all_time"]["wins"] == 0
    # All history: both regimes preserved.
    assert body["all_historical_performance"]["all_time"]["bets"] == 2
    assert body["all_historical_performance"]["all_time"]["wins"] == 1
    # Backwards-compatible key keeps its original all-regimes meaning.
    assert body["performance_windows"]["all_time"]["bets"] == 2
    assert {r["regime_key"] for r in body["regimes"]} == {"MANAGER_V1", "MANAGER_V2"}
    assert [r["is_current"] for r in body["regimes"] if r["regime_key"] == "MANAGER_V2"] == [True]


def test_results_page_shows_current_system_before_history(
    db: psycopg.Connection, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.app as dashboard_app
    import sports_edge_tennis.dashboard.daily_history as daily_history
    from sports_edge_tennis.dashboard.production_app import app

    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "_connect", lambda: _KeepOpen(db))
    monkeypatch.setattr(daily_history, "_latest_activity", lambda: None)

    body = TestClient(app).get("/bet-history").text

    assert "Current system" in body
    assert "All historical performance" in body
    assert body.index("Current system") < body.index("All historical performance")
    assert "Manager v2" in body
    # History must say plainly that it combines system versions.
    assert "combining" in body and "more than one system version" in body
    # And must promise no retroactive re-scoring.
    assert "nothing is re-scored against today" in body


def test_performance_page_marks_static_metrics_as_historical_validation(
    monkeypatch,
) -> None:
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.app as dashboard_app
    from sports_edge_tennis.dashboard.production_app import app

    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    body = TestClient(app).get("/performance").text

    assert "Historical model validation" in body
    assert "historical validation figures, not the live betting record" in body
    assert "not recomputed when the production system changes" in body
    # The forbidden framings must not appear anywhere on the page.
    for banned in ("Current accuracy", "current live accuracy", "Current system performance"):
        assert banned not in body


def test_agent_evidence_declares_it_spans_all_regimes() -> None:
    """Agent accuracy is all-production evidence and must say so."""
    import inspect

    from sports_edge_tennis.dashboard import model_evidence_routes
    from sports_edge_tennis.performance import evidence

    source = inspect.getsource(model_evidence_routes)
    assert '"agents_scope": "ALL_PRODUCTION_REGIMES"' in source
    assert "across all " in source
    assert "not current-system evidence" in (evidence.__doc__ or "")


def test_decisions_without_manager_lineage_become_legacy_not_a_guess(
    db: psycopg.Connection,
) -> None:
    """Pre-versioning rows are labelled legacy, never given an invented Manager.

    The schema guarantees a Type A BET records ``price_source_system``
    (decisions_check4), so the offered-price provider is always proven for a
    real bet. What is never recorded anywhere is the *data* provider, which is
    why no data-provider claim is made for any historical row.
    """
    _seed_bet(db, label="Versioned", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")

    # A decision that predates Manager lineage: permitted by the schema
    # (chk_decisions_market_links_coherent allows a NULL manager link).
    match_id = _seed_match(
        db, label="Ancient", start=datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    )
    run_id = _seed_run(
        db, match_id=match_id, as_of=datetime(2026, 9, 3, 8, tzinfo=timezone.utc)
    )
    legacy_id = _val(
        db,
        """
        INSERT INTO decisions (
            match_id, prediction_run_id, decision_policy_version_id,
            signal_type, decision_status, reason_code, market_type,
            selection_type, selection_side, consensus_probability,
            consensus_fair_odds, market_decimal_price, sharp_reference_price,
            price_source_system, price_bookmaker_code, devig_method,
            devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count,
            model_version, feature_version, as_of, prediction_locked_at, published_at
        )
        VALUES (
            %s, %s, %s, 'TYPE_A_MODEL_EDGE', 'BET', 'NONE', 'MATCH_WINNER',
            'PLAYER_1', 1, 0.60, 1.666667, 3.00, 1.60,
            'OFFERED', 'BOOKIE', 'MULTIPLICATIVE', 1.60, 0.05, 1.50, 3, 0,
            'legacy', 'legacy', %s, %s, %s
        )
        RETURNING decision_id
        """,
        (
            match_id, run_id, _decision_policy_id(db),
            datetime(2026, 9, 3, 8, tzinfo=timezone.utc),
            datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc),
            datetime(2026, 9, 3, 8, 20, tzinfo=timezone.utc),
        ),
    )
    db.execute(
        "INSERT INTO decision_settlements (decision_id, settlement_status, settled_at) "
        "VALUES (%s, 'WON', %s)",
        (legacy_id, datetime(2026, 9, 3, 15, tzinfo=timezone.utc)),
    )

    cohorts = load_regime_cohorts(db)
    legacy = _cohort(cohorts, LEGACY_REGIME_KEY)

    assert legacy.bets == 1
    assert legacy.label == "Legacy production regime"
    assert legacy.manager_policy_version is None
    # The offered-price provider is reported from what was actually recorded.
    assert legacy.odds_sources == ("OFFERED",)
    # No unproven provider is ever attributed to a historical row.
    for banned in ("Sportradar", "Goalserve", "API-Tennis", "Manager v"):
        assert banned not in legacy.label
        assert banned not in legacy.odds_provider_label

    # The legacy bet is preserved in history but excluded from Current System.
    current = resolve_current_regime(db)
    assert current.regime_key == "MANAGER_V1"
    assert sum(r.bets for r in load_daily_bet_summaries(db)) == 2
    assert sum(r.bets for r in load_daily_bet_summaries(db, regime_key="MANAGER_V1")) == 1
