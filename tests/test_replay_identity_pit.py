"""Point-in-time identity availability regression tests (Stage 3B).

A replay at timestamp T may use an identity only if the underlying evidence was
available by T. Later canonical reconciliation must never leak backwards.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.data.normalize import normalize_person_lookup
from sports_edge_tennis.replay.identity_pit import (
    STATUS_BRIDGED,
    STATUS_HISTORICAL,
    STATUS_LIVE_UNRESOLVED,
    resolve_model_player_id_pit,
)

UTC = timezone.utc
CUTOFF = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
BEFORE = CUTOFF - timedelta(days=30)
AFTER = CUTOFF + timedelta(days=10)


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _player(conn, name: str) -> int:
    return _fetchval(
        conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,)
    )


def _source_id(conn, player_id: int, source_system: str, source_id: str, created_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id, created_at)
        VALUES (%s, %s, %s, %s)
        """,
        (player_id, source_system, source_id, created_at),
    )


def _alias(
    conn,
    player_id: int,
    alias: str,
    *,
    first_observed_at: datetime,
    created_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO player_aliases (
            player_id, source_system, alias, normalized_alias,
            first_observed_at, last_observed_at, created_at, updated_at
        )
        VALUES (%s, 'TENNIS_DATA', %s, %s, %s, %s, %s, %s)
        """,
        (
            player_id,
            alias,
            normalize_person_lookup(alias),
            first_observed_at,
            first_observed_at,
            created_at,
            created_at,
        ),
    )


def _completed_match(conn, p1: int, p2: int, observed_at: datetime) -> int:
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Identity PIT Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id
        """,
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, 'Identity PIT Open 2026') RETURNING tournament_edition_id
        """,
        (tournament_id,),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'COMPLETED', %s, %s)
        """,
        (match_id, p1, p2, observed_at - timedelta(days=1), observed_at, observed_at),
    )
    conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        )
        VALUES (%s, 'COMPLETED', 1, %s, %s)
        """,
        (match_id, observed_at, observed_at),
    )
    return match_id


def test_historical_identity_before_cutoff_is_available(db: psycopg.Connection):
    player = _player(db, "Alice Alpha")
    _source_id(db, player, "SACKMANN", "sack-alice", BEFORE)

    resolution = resolve_model_player_id_pit(db, live_player_id=player, as_of=CUTOFF)
    assert resolution.status == STATUS_HISTORICAL
    assert resolution.available is True
    assert resolution.model_player_id == player


def test_source_mapping_created_after_cutoff_is_not_available(db: psycopg.Connection):
    player = _player(db, "Bob Beta")
    # A later reconciliation must not make identity available earlier.
    _source_id(db, player, "SACKMANN", "sack-bob", AFTER)

    resolution = resolve_model_player_id_pit(db, live_player_id=player, as_of=CUTOFF)
    assert resolution.status == STATUS_LIVE_UNRESOLVED
    assert resolution.available is False


def test_live_fixture_source_id_does_not_count_as_historical(db: psycopg.Connection):
    player = _player(db, "Cara Gamma")
    _source_id(db, player, "SPORTRADAR", "sdr-cara", BEFORE)

    resolution = resolve_model_player_id_pit(db, live_player_id=player, as_of=CUTOFF)
    assert resolution.status == STATUS_LIVE_UNRESOLVED


def test_alias_late_mapping_with_earlier_provider_evidence_is_allowed(db: psycopg.Connection):
    live = _player(db, "Jane Doe")
    _source_id(db, live, "API_TENNIS", "api-jane", BEFORE)

    historical = _player(db, "Jane Doe")
    _source_id(db, historical, "SACKMANN", "sack-jane", BEFORE)
    opponent = _player(db, "Opponent One")
    _source_id(db, opponent, "SACKMANN", "sack-opp", BEFORE)
    _completed_match(db, historical, opponent, BEFORE)

    # Provider evidence predates the cutoff; the linkage row is created later.
    _alias(
        db,
        historical,
        "Jane Doe",
        first_observed_at=BEFORE,
        created_at=AFTER,
    )

    resolution = resolve_model_player_id_pit(db, live_player_id=live, as_of=CUTOFF)
    assert resolution.status == STATUS_BRIDGED
    assert resolution.available is True
    assert resolution.model_player_id == historical


def test_alias_first_observed_after_cutoff_is_blocked(db: psycopg.Connection):
    live = _player(db, "Kate Kite")
    _source_id(db, live, "API_TENNIS", "api-kate", BEFORE)

    historical = _player(db, "Kate Kite")
    _source_id(db, historical, "SACKMANN", "sack-kate", AFTER)
    opponent = _player(db, "Opponent Two")
    _source_id(db, opponent, "SACKMANN", "sack-opp2", BEFORE)
    _completed_match(db, historical, opponent, BEFORE)

    _alias(db, historical, "Kate Kite", first_observed_at=AFTER, created_at=AFTER)

    resolution = resolve_model_player_id_pit(db, live_player_id=live, as_of=CUTOFF)
    assert resolution.status == STATUS_LIVE_UNRESOLVED


def test_ambiguous_identity_at_cutoff_is_blocked(db: psycopg.Connection):
    live = _player(db, "Lee Lake")
    _source_id(db, live, "API_TENNIS", "api-lee", BEFORE)

    first = _player(db, "Lee Lake")
    _source_id(db, first, "SACKMANN", "sack-lee-1", BEFORE)
    second = _player(db, "Lee Lake")
    _source_id(db, second, "SACKMANN", "sack-lee-2", BEFORE)
    opponent = _player(db, "Opponent Three")
    _source_id(db, opponent, "SACKMANN", "sack-opp3", BEFORE)
    _completed_match(db, first, opponent, BEFORE)
    _completed_match(db, second, opponent, BEFORE)

    _alias(db, first, "Lee Lake", first_observed_at=BEFORE, created_at=BEFORE)
    _alias(db, second, "Lee Lake", first_observed_at=BEFORE, created_at=BEFORE)

    resolution = resolve_model_player_id_pit(db, live_player_id=live, as_of=CUTOFF)
    assert resolution.status == STATUS_LIVE_UNRESOLVED
