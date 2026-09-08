from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.runner.targets import _resolve_model_player_id


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _player(conn, name: str) -> int:
    return _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (name,),
    )


def _alias(conn, player_id: int, name: str) -> None:
    normalized = " ".join(name.lower().split())
    conn.execute(
        """
        INSERT INTO player_aliases (
            player_id, source_system, alias, normalized_alias,
            first_observed_at, last_observed_at
        ) VALUES (%s, 'TENNIS_DATA', %s, %s, %s, %s)
        """,
        (
            player_id,
            name,
            normalized,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )


def _historical_result(conn, player_id: int, *, observed_at: datetime) -> None:
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
        (
            match_id,
            player_id,
            opponent_id,
            observed_at,
            observed_at,
            observed_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        ) VALUES (%s, 'COMPLETED', 1, %s, %s)
        """,
        (match_id, observed_at, observed_at),
    )


def test_live_only_player_maps_to_one_exact_historical_identity(db: psycopg.Connection):
    historical = _player(db, "Jannik Sinner")
    _alias(db, historical, "Jannik Sinner")
    _historical_result(
        db,
        historical,
        observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    live_duplicate = _player(db, "Jannik Sinner")

    resolved = _resolve_model_player_id(
        db,
        player_id=live_duplicate,
        as_of=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    assert resolved == historical


def test_existing_historical_player_identity_is_preserved(db: psycopg.Connection):
    historical = _player(db, "Carlos Alcaraz")
    _alias(db, historical, "Carlos Alcaraz")
    _historical_result(
        db,
        historical,
        observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    resolved = _resolve_model_player_id(
        db,
        player_id=historical,
        as_of=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    assert resolved == historical


def test_ambiguous_historical_identity_fails_closed(db: psycopg.Connection):
    first = _player(db, "Alex Smith")
    second = _player(db, "Alex Smith")
    for player_id in (first, second):
        _alias(db, player_id, "Alex Smith")
        _historical_result(
            db,
            player_id,
            observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    live_duplicate = _player(db, "Alex Smith")

    resolved = _resolve_model_player_id(
        db,
        player_id=live_duplicate,
        as_of=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    assert resolved == live_duplicate


def test_future_historical_evidence_is_not_visible(db: psycopg.Connection):
    historical = _player(db, "Future Evidence")
    _alias(db, historical, "Future Evidence")
    _historical_result(
        db,
        historical,
        observed_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )
    live_duplicate = _player(db, "Future Evidence")

    resolved = _resolve_model_player_id(
        db,
        player_id=live_duplicate,
        as_of=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    assert resolved == live_duplicate
