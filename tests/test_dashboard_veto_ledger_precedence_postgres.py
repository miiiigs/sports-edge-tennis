"""Real PostgreSQL regression for issued BET versus VETOED chronology.

These tests exercise distinct finalized runs for the SAME match. A veto is
never allowed to erase the actual historical first-issued Value Bet. A
veto-only match remains visible as a non-actionable blocked card, covered by
test_dashboard_veto_read_path_postgres.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from test_dashboard_issued_picks_postgres import (
    SHARP,
    TEST_DATE,
    _odds,
    _seed_configs,
    _seed_manager,
    _seed_match,
    _seed_run,
)
from sports_edge_tennis.dashboard.picks import VALUE_BET, build_today_picks
from sports_edge_tennis.dashboard.read_model import (
    get_issued_bet_decisions,
    get_issued_day_state,
    get_issued_market_decisions,
)
from sports_edge_tennis.market import ensure_market_policy_version_v1
from sports_edge_tennis.veto.integration import (
    evaluate_and_persist_market_slate_with_veto,
)


def _append_match_status(db, match_id: int, *, status: str, when: datetime) -> None:
    """Add a new observable version, never rewrite the earlier version."""
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code,
            best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of, ingested_at
        )
        SELECT match_id, player_1_id, player_2_id, match_start_at,
               tour_scope, competition_tier, surface, round_code,
               best_of_sets, qualifying_flag, %s, %s, %s, %s
        FROM match_versions WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
        LIMIT 1
        """,
        (status, when, when, when, match_id),
    )


@pytest.mark.parametrize("first_status", ["BET", "VETOED"])
def test_historical_bet_survives_either_veto_order(
    db: psycopg.Connection, first_status: str
) -> None:
    configs = _seed_configs(db)
    t0 = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    t1 = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)
    initial_match_status = "SCHEDULED" if first_status == "BET" else "WALKOVER"
    final_match_status = "WALKOVER" if first_status == "BET" else "SCHEDULED"
    match_id = _seed_match(
        db,
        "Historical " + first_status,
        datetime(2026, 9, 25, 15, 0, tzinfo=UTC),
        status=initial_match_status,
        match_version_seen=t0 - timedelta(hours=1),
    )

    for side in (1, 2):
        _odds(db, match_id, f"PLAYER_{side}", "2.00", "sharp-early", "SHARP", "PINNACLE", t0)
        _odds(
            db, match_id, f"PLAYER_{side}",
            "2.05" if side == 1 else "1.75",
            "offer-early", "SOFT", "UNIBET", t0,
        )

    policy_id = ensure_market_policy_version_v1(db).market_policy_version_id
    statuses = []
    for idx, (as_of, current_match_status) in enumerate(
        ((t0, initial_match_status), (t1, final_match_status))
    ):
        if idx:
            _append_match_status(db, match_id, status=current_match_status, when=t1)
        run_id = _seed_run(
            db, as_of, [(match_id, 1, "0.620", 1, "0.600")], configs
        )
        _seed_manager(
            db, run_id, match_id, as_of, status="CONSENSUS", side=1, prob="0.62"
        )
        evaluate_and_persist_market_slate_with_veto(
            db,
            prediction_run_id=run_id,
            market_policy_version_id=policy_id,
            decision_as_of=as_of,
            market_execution_now=as_of + timedelta(minutes=45),
            sharp_reference=SHARP,
        )
        status = db.execute(
            "SELECT decision_status::text FROM decisions "
            "WHERE prediction_run_id=%s AND match_id=%s "
            "AND manager_consensus_result_id IS NOT NULL",
            (run_id, match_id),
        ).fetchone()[0]
        statuses.append(status)
    assert statuses == [first_status, "VETOED" if first_status == "BET" else "BET"]

    # Veto and BET are separate ledgers regardless of timestamp sorting.
    issued_bets = get_issued_bet_decisions(db, TEST_DATE)
    overlay = get_issued_market_decisions(db, TEST_DATE)
    assert issued_bets[match_id].decision_status == "BET"
    assert overlay[match_id].decision_status == "BET"
    assert overlay[match_id].price_bookmaker_code == "UNIBET"
    assert overlay[match_id].market_decimal_price is not None

    state = get_issued_day_state(db, TEST_DATE)
    cards = build_today_picks(state.matches, overlay)
    match_card = next(p for p in cards.picks if p.match_id == match_id)
    assert match_card.kind == VALUE_BET
    assert match_card.is_actionable_bet
    assert cards.blocked_count == 0
    assert cards.value_bet_count == 1
