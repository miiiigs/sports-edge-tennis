"""A3 loader player-resolution equivalence: set-based bulk == per-player N+1.

The optimized loader delegates to ``resolve_model_player_ids`` (bulk). This test
proves the produced ``Agent3HistoricalMatch`` rows are identical to the previous
per-player resolution path on a fixture that exercises historical identity, a
live-only player, and a TENNIS_DATA alias bridge.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.data import agent3_history
from sports_edge_tennis.data.agent3_history import load_agent3_history
from sports_edge_tennis.data.model_identity import resolve_model_player_id
from sports_edge_tennis.data.normalize import normalize_person_lookup

UTC = timezone.utc
CUTOFF = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
OBSERVED = CUTOFF - timedelta(days=10)


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _player(conn, name):
    return _fetchval(
        conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,)
    )


def _source_id(conn, player_id, source_system, source_id):
    conn.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id) VALUES (%s, %s, %s)",
        (player_id, source_system, source_id),
    )


def _alias(conn, player_id, alias):
    conn.execute(
        """
        INSERT INTO player_aliases (player_id, source_system, alias, normalized_alias, first_observed_at)
        VALUES (%s, 'TENNIS_DATA', %s, %s, %s)
        """,
        (player_id, alias, normalize_person_lookup(alias), OBSERVED - timedelta(days=100)),
    )


def _completed_match(conn, edition_id, p1, p2, *, observed):
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'COMPLETED', %s, %s)
        """,
        (match_id, p1, p2, observed, observed),
    )
    conn.execute(
        """
        INSERT INTO match_result_observations (match_id, outcome_status, winner_side, source_observed_at, as_of)
        VALUES (%s, 'COMPLETED', 1, %s, %s)
        """,
        (match_id, observed, observed),
    )
    return match_id


def _seed(conn):
    historical = _player(conn, "Jane Doe")
    _source_id(conn, historical, "SACKMANN", "sack-jane")
    opponent = _player(conn, "Opp One")
    _source_id(conn, opponent, "SACKMANN", "sack-opp1")
    live_only = _player(conn, "Jane Doe")
    _source_id(conn, live_only, "API_TENNIS", "api-jane")
    other = _player(conn, "Opp Two")
    _source_id(conn, other, "SACKMANN", "sack-opp2")
    _alias(conn, historical, "Jane Doe")

    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('Equiv Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
    )
    edition_id = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2026, 'Equiv 2026') RETURNING tournament_edition_id",
        (tournament_id,),
    )
    _completed_match(conn, edition_id, historical, opponent, observed=OBSERVED)
    _completed_match(conn, edition_id, live_only, other, observed=OBSERVED)
    return {"historical": historical, "live_only": live_only}


def _per_player_resolver(conn, *, player_ids, as_of):
    """Replicates the previous N+1 behaviour for equivalence comparison."""
    return {
        player_id: resolve_model_player_id(conn, player_id=player_id, as_of=as_of)
        for player_id in sorted({int(pid) for pid in player_ids})
    }


def test_bulk_and_per_player_resolution_are_identical(db: psycopg.Connection):
    ids = _seed(db)
    bulk = load_agent3_history(db, cutoff=CUTOFF)

    original = agent3_history.resolve_model_player_ids
    agent3_history.resolve_model_player_ids = _per_player_resolver
    try:
        per_player = load_agent3_history(db, cutoff=CUTOFF)
    finally:
        agent3_history.resolve_model_player_ids = original

    assert bulk == per_player
    # The live-only player was bridged to the historical candidate in both paths.
    assert ids["historical"] in {row.player_1_id for row in bulk} | {row.player_2_id for row in bulk}
    assert ids["live_only"] not in {row.player_1_id for row in bulk} | {row.player_2_id for row in bulk}
