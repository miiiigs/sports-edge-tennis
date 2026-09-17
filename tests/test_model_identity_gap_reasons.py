from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.runner.targets import (
    _log_model_identity_gap,
    _model_identity_gap_diagnostic,
)


AS_OF = datetime(2026, 9, 17, 15, 0, tzinfo=UTC)
HISTORY_AT = datetime(2026, 1, 2, tzinfo=UTC)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _player(conn: psycopg.Connection, name: str) -> int:
    return _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (name,),
    )


def _historical_identity(conn: psycopg.Connection, name: str, source_id: str) -> int:
    player_id = _player(conn, name)
    conn.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, 'SACKMANN', %s)
        """,
        (player_id, source_id),
    )
    normalized = " ".join(name.casefold().split())
    conn.execute(
        """
        INSERT INTO player_aliases (
            player_id, source_system, alias, normalized_alias,
            first_observed_at, last_observed_at
        ) VALUES (%s, 'TENNIS_DATA', %s, %s, %s, %s)
        """,
        (player_id, name, normalized, HISTORY_AT, HISTORY_AT),
    )
    return player_id


def _historical_result(
    conn: psycopg.Connection,
    player_id: int,
    *,
    observed_at: datetime = HISTORY_AT,
) -> None:
    opponent_id = _player(conn, f"Opponent {player_id}")
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250') RETURNING tournament_id
        """,
        (f"History Event {player_id}",),
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, start_date
        ) VALUES (%s, 2026, %s, '2026-01-01') RETURNING tournament_edition_id
        """,
        (tournament_id, f"History Event {player_id} 2026"),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets,
            qualifying_flag, match_status, source_observed_at, as_of
        ) VALUES (
            %s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3,
            FALSE, 'COMPLETED', %s, %s
        )
        """,
        (match_id, player_id, opponent_id, observed_at, observed_at, observed_at),
    )
    conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        ) VALUES (%s, 'COMPLETED', 1, %s, %s)
        """,
        (match_id, observed_at, observed_at),
    )


def test_gap_reason_no_historical_alias(db: psycopg.Connection):
    live_player = _player(db, "New Prospect")

    diagnostic = _model_identity_gap_diagnostic(
        db,
        player_id=live_player,
        as_of=AS_OF,
    )

    assert diagnostic == ("NO_HISTORICAL_ALIAS", 0, 0, ())


def test_gap_reason_alias_without_pit_history(db: psycopg.Connection):
    _historical_identity(db, "Young Player", "hist-young")
    live_player = _player(db, "Young Player")

    reason, alias_count, pit_count, pit_ids = _model_identity_gap_diagnostic(
        db,
        player_id=live_player,
        as_of=AS_OF,
    )

    assert reason == "ALIAS_WITHOUT_PIT_HISTORY"
    assert alias_count == 1
    assert pit_count == 0
    assert pit_ids == ()


def test_gap_reason_ambiguous_pit_candidates(db: psycopg.Connection):
    first = _historical_identity(db, "Alex Smith", "hist-alex-1")
    second = _historical_identity(db, "Alex Smith", "hist-alex-2")
    _historical_result(db, first)
    _historical_result(db, second)
    live_player = _player(db, "Alex Smith")

    reason, alias_count, pit_count, pit_ids = _model_identity_gap_diagnostic(
        db,
        player_id=live_player,
        as_of=AS_OF,
    )

    assert reason == "AMBIGUOUS_PIT_CANDIDATES"
    assert alias_count == 2
    assert pit_count == 2
    assert pit_ids == tuple(sorted((first, second)))


def test_future_history_is_reported_as_not_pit_visible(db: psycopg.Connection):
    historical = _historical_identity(db, "Future Evidence", "hist-future")
    _historical_result(
        db,
        historical,
        observed_at=datetime(2026, 9, 17, 15, 30, tzinfo=UTC),
    )
    live_player = _player(db, "Future Evidence")

    reason, alias_count, pit_count, pit_ids = _model_identity_gap_diagnostic(
        db,
        player_id=live_player,
        as_of=AS_OF,
    )

    assert reason == "ALIAS_WITHOUT_PIT_HISTORY"
    assert alias_count == 1
    assert pit_count == 0
    assert pit_ids == ()


def test_gap_log_is_explicitly_read_only(db: psycopg.Connection, capsys):
    live_player = _player(db, "No History Player")

    _log_model_identity_gap(
        db,
        match_id=130399,
        slot="P2",
        player_id=live_player,
        as_of=AS_OF,
    )

    output = capsys.readouterr().out
    assert "MODEL_IDENTITY_GAP" in output
    assert "match_id=130399" in output
    assert "slot=P2" in output
    assert "reason=NO_HISTORICAL_ALIAS" in output
    assert "mapping_changed=0" in output
