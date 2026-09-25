"""Postgres-backed point-in-time regression tests for the replay loader.

Run only when ``TEST_POSTGRES_DSN`` or ``DATABASE_URL`` is configured.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg

from sports_edge_tennis.replay.engine import run_replay
from sports_edge_tennis.replay.loader import (
    ReplayLoadRequest,
    ReplayRunSpec,
    load_replay_inputs,
)

from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1
from sports_edge_tennis.market.persistence import ensure_market_policy_version_v1

UTC = timezone.utc
PREDICTION = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
MARKET = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
MATCH_START = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_policies(conn: psycopg.Connection) -> None:
    # Reference policy rows are normally migration-seeded; the test fixture
    # truncates them, so seed them explicitly here. Replay itself never seeds.
    ensure_manager_policy_version_v1(conn)
    ensure_market_policy_version_v1(conn)


def _insert_player(conn, name: str, *, historical: bool = True) -> int:
    player_id = _fetchval(
        conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,)
    )
    if historical:
        # created_at is set before the replay cutoff so the historical mapping is
        # provably PIT-available at PREDICTION.
        conn.execute(
            """
            INSERT INTO player_source_ids (player_id, source_system, source_player_id, created_at)
            VALUES (%s, 'SACKMANN', %s, %s)
            """,
            (
                player_id,
                f"sack-{player_id}",
                PREDICTION - timedelta(days=30),
            ),
        )
    return player_id


def _insert_target(conn, *, historical_identity: bool = True) -> dict:
    p1 = _insert_player(conn, "Replay Player One", historical=historical_identity)
    p2 = _insert_player(conn, "Replay Player Two", historical=historical_identity)
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Replay Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id
        """,
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, 'Replay Open 2026') RETURNING tournament_edition_id
        """,
        (tournament_id,),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    scheduled_version_id = _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (match_id, p1, p2, MATCH_START, PREDICTION - timedelta(hours=1), PREDICTION - timedelta(hours=1)),
    )
    # A later correction that must never be visible at the prediction cutoff.
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'CANCELLED', %s, %s)
        """,
        (
            match_id,
            p1,
            p2,
            MATCH_START,
            MARKET + timedelta(hours=1),
            MARKET + timedelta(hours=1),
        ),
    )
    return {
        "match_id": match_id,
        "match_version_id": scheduled_version_id,
        "player_1_id": p1,
        "player_2_id": p2,
    }


def _insert_odds(
    conn,
    *,
    match_id: int,
    source_system: str,
    bookmaker_code: str,
    source_snapshot_id: str,
    observed_at: datetime,
) -> None:
    for selection_type, price in (("PLAYER_1", "2.10"), ("PLAYER_2", "1.80")):
        conn.execute(
            """
            INSERT INTO odds_snapshots (
                match_id, source_system, bookmaker_code, market_type, selection_type,
                decimal_price, market_status, source_snapshot_id, source_observed_at, as_of
            )
            VALUES (%s, %s, %s, 'MATCH_WINNER', %s, %s, 'OPEN', %s, %s, %s)
            """,
            (
                match_id,
                source_system,
                bookmaker_code,
                selection_type,
                Decimal(price),
                source_snapshot_id,
                observed_at,
                observed_at,
            ),
        )


def _spec(match_id: int) -> ReplayLoadRequest:
    return ReplayLoadRequest(
        run_specs=(
            ReplayRunSpec(
                run_id="replay-test",
                run_sequence=1,
                prediction_as_of=PREDICTION,
                market_as_of=MARKET,
                match_ids=(match_id,),
            ),
        ),
        result_as_of=MATCH_START + timedelta(hours=3),
    )


def test_loader_excludes_future_odds_and_future_versions(db: psycopg.Connection):
    _seed_policies(db)
    target = _insert_target(db)
    _insert_odds(
        db,
        match_id=target["match_id"],
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        source_snapshot_id="sharp-visible",
        observed_at=MARKET - timedelta(minutes=10),
    )
    _insert_odds(
        db,
        match_id=target["match_id"],
        source_system="OFFERED",
        bookmaker_code="B365",
        source_snapshot_id="offered-visible",
        observed_at=MARKET - timedelta(minutes=10),
    )
    _insert_odds(
        db,
        match_id=target["match_id"],
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        source_snapshot_id="sharp-future",
        observed_at=MARKET + timedelta(minutes=30),
    )

    loaded = load_replay_inputs(db, _spec(target["match_id"]))
    match_input = loaded.runs[0].matches[0]

    assert match_input.target.match_version_id == target["match_version_id"]
    # 2 visible SHARP legs + 2 visible OFFERED legs; the future SHARP pair is excluded.
    assert len(match_input.odds_snapshots) == 4
    assert all(snapshot.source_observed_at <= MARKET for snapshot in match_input.odds_snapshots)
    assert match_input.odds_proof.max_source_observed_at <= MARKET
    assert "sharp-future" not in {snapshot.source_snapshot_id for snapshot in match_input.odds_snapshots}

    result = run_replay(loaded.runs)
    assert len(result.model_picks) == 1


def test_loader_marks_identity_blocked_without_historical_identity(db: psycopg.Connection):
    _seed_policies(db)
    target = _insert_target(db, historical_identity=False)
    loaded = load_replay_inputs(db, _spec(target["match_id"]))
    match_input = loaded.runs[0].matches[0]

    assert match_input.identity_status == "LIVE_UNRESOLVED"

    result = run_replay(loaded.runs)
    reasons = {exclusion.reason for exclusion in result.exclusions}
    assert "IDENTITY_NOT_AVAILABLE_PIT" in reasons
    assert result.model_picks == ()


def test_loader_does_not_mutate_production_history(db: psycopg.Connection):
    _seed_policies(db)
    target = _insert_target(db)
    _insert_odds(
        db,
        match_id=target["match_id"],
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        source_snapshot_id="sharp-visible",
        observed_at=MARKET - timedelta(minutes=10),
    )

    tracked = (
        "prediction_runs",
        "predictions",
        "manager_consensus_results",
        "decisions",
        "decision_agents",
        "decision_settlements",
        "match_versions",
        "match_result_observations",
        "odds_snapshots",
    )
    before = {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tracked
    }

    loaded = load_replay_inputs(db, _spec(target["match_id"]))
    run_replay(loaded.runs)

    after = {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tracked
    }
    assert before == after
