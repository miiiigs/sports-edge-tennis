"""PostgreSQL regression: issued daily ledger preserves run-A picks across run B.

Reuses the repository's valid persistence patterns (tests/test_dashboard.py and
tests/test_final_bet_publication.py): a RUNNING predictive_agent_result_v1 run
with the canonical three-agent prediction set, then legitimate finalization,
manager consensus, real persisted BET decisions, and a later degraded run.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.dashboard.picks import MODEL_PICK, VALUE_BET, build_today_picks
from sports_edge_tennis.dashboard.read_model import (
    completed_day_run_rows,
    get_issued_day_state,
    get_issued_market_decisions,
)
from sports_edge_tennis.live.orchestrator import ensure_agent_config_versions_v1
from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1
from sports_edge_tennis.market import (
    SharpReferenceSpec,
    ensure_market_policy_version_v1,
    evaluate_and_persist_market_slate,
)

TEST_DATE = date(2026, 9, 25)
A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"
SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


def _fv(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_match(conn, name, start):
    p1 = _fv(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (f"{name} A",))
    p2 = _fv(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (f"{name} B",))
    tid = _fv(conn, "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES (%s,'ATP_MAIN','ATP_250') RETURNING tournament_id", (f"Test Open {name}",))
    eid = _fv(conn, "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date) VALUES (%s, 2026, %s, %s) RETURNING tournament_edition_id", (tid, f"Edition {name}", TEST_DATE.isoformat()))
    mid = _fv(conn, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (eid,))
    observed = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    conn.execute(
        """
        INSERT INTO match_versions (match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of)
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'SCHEDULED', %s, %s)
        """,
        (mid, p1, p2, start, observed, observed),
    )
    return mid


def _seed_configs(conn):
    ensure_agent_config_versions_v1(conn)
    ensure_manager_policy_version_v1(conn)
    ensure_market_policy_version_v1(conn)
    rows = conn.execute(
        "SELECT agent_code::text, agent_config_version_id, model_version, feature_version FROM agent_config_versions"
    ).fetchall()
    return {r[0]: {"id": r[1], "model": r[2], "feature": r[3]} for r in rows}


def _seed_run(conn, as_of, targets, configs):
    """targets: list of (match_id, a1_side, a1_prob, a2_side, a2_prob); A3 abstains."""
    run_id = _fv(conn, "INSERT INTO prediction_runs (run_status, as_of, started_at, prediction_contract_version) VALUES ('RUNNING',%s,%s,'predictive_agent_result_v1') RETURNING prediction_run_id", (as_of, as_of))
    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        for mid, a1s, a1p, a2s, a2p in targets:
            conn.execute("INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s,%s)", (run_id, mid))
            for code, side, prob in ((A1, a1s, a1p), (A2, a2s, a2p)):
                cfg = configs[code]
                conn.execute(
                    """
                    INSERT INTO predictions (match_id, prediction_run_id, agent_config_version_id, agent_code,
                        prediction_status, selection_side, win_probability, probability_representation,
                        model_version, feature_version, as_of, prediction_locked_at)
                    VALUES (%s,%s,%s,%s,'PREDICTED',%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (mid, run_id, cfg["id"], code, side, Decimal(prob), AGENT_PROBABILITY_REPRESENTATIONS[code], cfg["model"], cfg["feature"], as_of, as_of),
                )
            cfg = configs[A3]
            conn.execute(
                """
                INSERT INTO predictions (match_id, prediction_run_id, agent_config_version_id, agent_code,
                    prediction_status, selection_side, win_probability, probability_representation,
                    model_version, feature_version, as_of, prediction_locked_at)
                VALUES (%s,%s,%s,%s,'ABSTAINED',NULL,NULL,NULL,%s,%s,%s,%s)
                """,
                (mid, run_id, cfg["id"], A3, cfg["model"], cfg["feature"], as_of, as_of),
            )
        conn.execute("UPDATE prediction_runs SET run_status='COMPLETED', prediction_finalized_at=%s, ended_at=%s WHERE prediction_run_id=%s", (as_of, as_of, run_id))
    return run_id


def _seed_manager(conn, run_id, match_id, as_of, *, status, side=None, prob=None, reason="NONE", spread="0.02"):
    policy_id = _fv(conn, "SELECT manager_policy_version_id FROM manager_policy_versions ORDER BY manager_policy_version_id LIMIT 1")
    conn.execute(
        """
        INSERT INTO manager_consensus_results (prediction_run_id, match_id, manager_policy_version_id,
            manager_status, selected_side, consensus_probability, active_agent_count,
            abstaining_agent_count, probability_spread, reason_code, as_of)
        VALUES (%s,%s,%s,%s,%s,%s,2,1,%s,%s,%s)
        """,
        (run_id, match_id, policy_id, status, side, Decimal(prob) if prob else None, Decimal(spread), reason, as_of),
    )


def _odds(conn, match_id, selection, price, snapshot, source, book, when):
    conn.execute(
        """
        INSERT INTO odds_snapshots (match_id, source_system, bookmaker_code, market_type, selection_type,
            decimal_price, market_status, source_snapshot_id, source_record_hash, source_observed_at, as_of, odds_origin)
        VALUES (%s,%s,%s,'MATCH_WINNER',%s,%s,'OPEN',%s,%s,%s,%s,'HISTORICAL')
        """,
        (match_id, source, book, selection, Decimal(price), snapshot, f"h-{snapshot}-{selection}", when, when),
    )


def test_issued_daily_ledger_preserves_run_a_across_degraded_run_b(db: psycopg.Connection):
    configs = _seed_configs(db)
    t0 = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    t1 = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)

    m1 = _seed_match(db, "Match One", datetime(2026, 9, 25, 1, 0, tzinfo=UTC))
    m2 = _seed_match(db, "Match Two", datetime(2026, 9, 25, 1, 0, tzinfo=UTC))
    m3 = _seed_match(db, "Match Three", datetime(2026, 9, 25, 1, 0, tzinfo=UTC))
    m4 = _seed_match(db, "Match Four", datetime(2026, 9, 25, 1, 0, tzinfo=UTC))
    m5 = _seed_match(db, "Match Five", datetime(2026, 9, 25, 1, 0, tzinfo=UTC))

    # RUN A: m1..m4 Manager CONSENSUS; m1,m3 also a real Value Bet.
    run_a = _seed_run(
        db,
        t0,
        [(m1, 1, "0.620", 1, "0.600"), (m2, 2, "0.590", 2, "0.570"), (m3, 1, "0.620", 1, "0.600"), (m4, 2, "0.590", 2, "0.570")],
        configs,
    )
    for mid, side in ((m1, 1), (m2, 2), (m3, 1), (m4, 2)):
        # Qualifying (>= 60%) so these are aligned Model Picks under the current
        # rule; the test still proves run B cannot erase run A's issued picks.
        _seed_manager(db, run_a, mid, t0, status="CONSENSUS", side=side, prob="0.62")
    for mid, offer_p1, offer_p2 in ((m1, "1.98", "2.05"), (m3, "2.15", "1.90")):
        _odds(db, mid, "PLAYER_1", "2.00", f"sharp-{mid}", "SHARP", "PINNACLE", t0)
        _odds(db, mid, "PLAYER_2", "2.00", f"sharp-{mid}", "SHARP", "PINNACLE", t0)
        _odds(db, mid, "PLAYER_1", offer_p1, f"offer-{mid}", "SOFT", "UNIBET", t0)
        _odds(db, mid, "PLAYER_2", offer_p2, f"offer-{mid}", "SOFT", "UNIBET", t0)
    market_policy = ensure_market_policy_version_v1(db).market_policy_version_id
    evaluate_and_persist_market_slate(
        db,
        prediction_run_id=run_a,
        market_policy_version_id=market_policy,
        decision_as_of=t0,
        market_execution_now=datetime(2026, 9, 25, 9, 45, tzinfo=UTC),
        sharp_reference=SHARP,
    )

    # RUN B: m1..m4 NO_BET; m5 Wide-Spread tracking (A1=A2 side 1, spread > 0.06).
    run_b = _seed_run(
        db,
        t1,
        [(m1, 2, "0.550", 2, "0.500"), (m2, 2, "0.550", 2, "0.500"), (m3, 2, "0.550", 2, "0.500"), (m4, 2, "0.550", 2, "0.500"), (m5, 1, "0.700", 1, "0.550")],
        configs,
    )
    for mid in (m1, m2, m3, m4):
        _seed_manager(db, run_b, mid, t1, status="NO_BET", reason="DIRECTIONAL_DISAGREEMENT")
    _seed_manager(db, run_b, m5, t1, status="NO_BET", reason="AGENT3_ABSTENTION_SPREAD_EXCEEDED", spread="0.15")

    # Both runs are found for the sporting day.
    assert [r[0] for r in completed_day_run_rows(db, TEST_DATE)] == [run_a, run_b]

    # Issued ledger: m1..m4 keep RUN-A consensus; m5 is the additive Wide-Spread pick.
    state = get_issued_day_state(db, TEST_DATE)
    assert sorted(m.match_id for m in state.matches) == sorted([m1, m2, m3, m4, m5])
    m1v = next(m for m in state.matches if m.match_id == m1)
    assert m1v.manager_status == "CONSENSUS"
    assert m1v.selected_side == 1
    assert state.prediction_run_id == run_b

    # Issued Value Bets are the RUN-A decisions (never replaced/erased by run B).
    market = get_issued_market_decisions(db, TEST_DATE)
    assert set(market) == {m1, m3}
    assert market[m1].decision_status == "BET"
    assert market[m1].price_bookmaker_code == "UNIBET"
    assert market[m1].market_decimal_price == Decimal("1.98")
    assert market[m3].market_decimal_price == Decimal("2.15")
    assert market[m1].published_at is not None

    picks = build_today_picks(state.matches, market)
    assert picks.qualified_model_pick_count == 4
    assert picks.wide_spread_model_pick_count == 1
    assert picks.total_model_pick_count == 5
    assert picks.value_bet_count == 2
    assert len({p.match_id for p in picks.picks}) == 5

    value_ids = {p.match_id for p in picks.picks if p.kind == VALUE_BET}
    qualified_ids = {p.match_id for p in picks.picks if p.kind in (MODEL_PICK, VALUE_BET) and not p.is_wide_spread}
    assert value_ids == {m1, m3}
    assert value_ids <= qualified_ids
    assert [p.match_id for p in picks.picks].count(m1) == 1  # never shown twice
