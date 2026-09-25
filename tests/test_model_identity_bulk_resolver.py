"""Semantic parity and query-scaling tests for the bulk model identity resolver.

The bulk resolver must be exactly equivalent to calling
``resolve_model_player_id`` once per player, while issuing a bounded number of SQL
statements instead of growing with the player count (no N+1).
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from sports_edge_tennis.data.model_identity import (
    resolve_model_player_id,
    resolve_model_player_ids,
)
from sports_edge_tennis.data.normalize import normalize_person_lookup


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _source(conn: psycopg.Connection, *, player_id: int, system: str, source_id: str) -> None:
    conn.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id) VALUES (%s, %s, %s)",
        (player_id, system, source_id),
    )


def _alias(conn: psycopg.Connection, *, player_id: int, alias: str, system: str = "TENNIS_DATA") -> None:
    conn.execute(
        """
        INSERT INTO player_aliases (player_id, source_system, alias, normalized_alias)
        VALUES (%s, %s, %s, %s)
        """,
        (player_id, system, alias, normalize_person_lookup(alias)),
    )


def _dummy_match(conn: psycopg.Connection, name: str) -> int:
    tournament_id = conn.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
        (name,),
    ).fetchone()[0]
    edition_id = conn.execute(
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2025, %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, name),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]


def _seed_candidate(
    conn: psycopg.Connection,
    *,
    name: str,
    alias: str,
    observed_at: datetime,
    with_completed_history: bool = True,
    live_only: bool = False,
) -> int:
    """Seed a historical candidate with an alias, optionally PIT-visible results."""
    player_id = _player(conn, name)
    if live_only:
        _source(conn, player_id=player_id, system="API_TENNIS", source_id=f"live-cand-{player_id}")
    else:
        _source(conn, player_id=player_id, system="SACKMANN", source_id=f"hist-cand-{player_id}")
    _alias(conn, player_id=player_id, alias=alias)
    if not with_completed_history:
        return player_id

    opponent = _player(conn, f"{name} Opponent")
    _source(conn, player_id=opponent, system="SACKMANN", source_id=f"hist-opp-{player_id}")
    match_id = _dummy_match(conn, f"{name} Open")
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE,
                'COMPLETED', %s, %s)
        """,
        (match_id, player_id, opponent, observed_at, observed_at, observed_at),
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
    return player_id


def _seed_bridge(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    historical_player_id: int,
    activated_at: datetime,
) -> None:
    match_id = _dummy_match(conn, f"Bridge Evidence {live_player_id}")
    conn.execute(
        """
        INSERT INTO model_identity_bridge_events (
            live_player_id, action, historical_player_id, evidence_source_system,
            evidence_match_id, evidence_provider_event_id, evidence_provider_player_id,
            evidence_provider_name, evidence_status, evidence_as_of, activated_at,
            actor, reason
        )
        VALUES (%s, 'BRIDGE', %s, 'SPORTRADAR', %s, 'evt-1', 'prov-1', 'Prov Name',
                'UNIQUE_PIT_CANDIDATE', %s, %s, 'test', 'parity')
        """,
        (live_player_id, historical_player_id, match_id, activated_at, activated_at),
    )


def _seed_disable(conn: psycopg.Connection, *, live_player_id: int, activated_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO model_identity_bridge_events (
            live_player_id, action, historical_player_id, evidence_status,
            evidence_as_of, activated_at, actor, reason
        )
        VALUES (%s, 'DISABLE', NULL, 'DISABLE', %s, %s, 'test', 'parity')
        """,
        (live_player_id, activated_at, activated_at),
    )


def _scalar_map(conn: psycopg.Connection, player_ids, as_of: datetime) -> dict[int, int]:
    return {
        player_id: resolve_model_player_id(conn, player_id=player_id, as_of=as_of)
        for player_id in player_ids
    }


def test_bulk_resolver_matches_scalar_for_representative_cases(db: psycopg.Connection):
    cutoff = _utc(2025, 6, 16)
    observed = _utc(2025, 1, 1, 12)

    historical = _player(db, "Bulk Historical")
    _source(db, player_id=historical, system="SACKMANN", source_id="hist-bulk")

    bridge_live = _player(db, "Bulk Bridge Live")
    _source(db, player_id=bridge_live, system="API_TENNIS", source_id="api-bridge-live")
    bridge_target = _seed_candidate(db, name="Bulk Bridge Target", alias="Bulk Bridge Target", observed_at=observed)
    _seed_bridge(
        db,
        live_player_id=bridge_live,
        historical_player_id=bridge_target,
        activated_at=_utc(2025, 5, 1),
    )

    alias_live = _player(db, "Bulk Alias")
    _source(db, player_id=alias_live, system="SPORTRADAR", source_id="sr:bulk-alias")
    alias_target = _seed_candidate(db, name="Bulk Alias", alias="Bulk Alias", observed_at=observed)

    no_candidate = _player(db, "Bulk Nobody")
    _source(db, player_id=no_candidate, system="API_TENNIS", source_id="api-nobody")

    ambiguous_live = _player(db, "Bulk Ambiguous")
    _source(db, player_id=ambiguous_live, system="API_TENNIS", source_id="api-ambiguous")
    _seed_candidate(db, name="Bulk Ambiguous One", alias="Bulk Ambiguous", observed_at=observed)
    _seed_candidate(db, name="Bulk Ambiguous Two", alias="Bulk Ambiguous", observed_at=observed)

    no_history_live = _player(db, "Bulk No History")
    _source(db, player_id=no_history_live, system="API_TENNIS", source_id="api-no-history")
    _seed_candidate(
        db,
        name="Bulk No History",
        alias="Bulk No History",
        observed_at=observed,
        with_completed_history=False,
    )

    future_bridge_live = _player(db, "Bulk Future Bridge")
    _source(db, player_id=future_bridge_live, system="API_TENNIS", source_id="api-future-bridge")
    future_target = _seed_candidate(db, name="Bulk Future Target", alias="Bulk Future Target", observed_at=observed)
    _seed_bridge(
        db,
        live_player_id=future_bridge_live,
        historical_player_id=future_target,
        activated_at=_utc(2025, 7, 1),
    )

    live_only_candidate_live = _player(db, "Bulk Live Candidate")
    _source(db, player_id=live_only_candidate_live, system="API_TENNIS", source_id="api-live-candidate")
    _seed_candidate(
        db,
        name="Bulk Live Candidate Target",
        alias="Bulk Live Candidate",
        observed_at=observed,
        live_only=True,
    )

    disabled_live = _player(db, "Bulk Disabled")
    _source(db, player_id=disabled_live, system="API_TENNIS", source_id="api-disabled")
    disabled_target = _seed_candidate(db, name="Bulk Disabled Target", alias="Bulk Disabled Target", observed_at=observed)
    _seed_bridge(
        db,
        live_player_id=disabled_live,
        historical_player_id=disabled_target,
        activated_at=_utc(2025, 4, 1),
    )
    _seed_disable(db, live_player_id=disabled_live, activated_at=_utc(2025, 5, 1))

    player_ids = [
        historical,
        bridge_live,
        alias_live,
        no_candidate,
        ambiguous_live,
        no_history_live,
        future_bridge_live,
        live_only_candidate_live,
        disabled_live,
    ]

    bulk = resolve_model_player_ids(db, player_ids=player_ids, as_of=cutoff)
    scalar = _scalar_map(db, player_ids, cutoff)

    assert bulk == scalar
    assert bulk[historical] == historical
    assert bulk[bridge_live] == bridge_target
    assert bulk[alias_live] == alias_target
    assert bulk[no_candidate] == no_candidate
    assert bulk[ambiguous_live] == ambiguous_live
    assert bulk[no_history_live] == no_history_live
    assert bulk[future_bridge_live] == future_bridge_live
    assert bulk[live_only_candidate_live] == live_only_candidate_live
    assert bulk[disabled_live] == disabled_live


def test_bulk_resolver_returns_entry_for_every_input_and_dedupes(db: psycopg.Connection):
    cutoff = _utc(2025, 6, 16)
    historical = _player(db, "Bulk Dedupe Historical")
    _source(db, player_id=historical, system="SACKMANN", source_id="hist-dedupe")
    live = _player(db, "Bulk Dedupe Live")
    _source(db, player_id=live, system="API_TENNIS", source_id="api-dedupe")

    bulk = resolve_model_player_ids(
        db,
        player_ids=[historical, live, historical, live, historical],
        as_of=cutoff,
    )

    assert bulk == {historical: historical, live: live}
    assert resolve_model_player_ids(db, player_ids=[], as_of=cutoff) == {}


class _CountingConnection:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self.queries = 0

    def execute(self, *args, **kwargs):
        self.queries += 1
        return self._conn.execute(*args, **kwargs)


def test_bulk_resolver_query_count_is_not_proportional_to_player_count(db: psycopg.Connection):
    cutoff = _utc(2025, 6, 16)
    player_ids: list[int] = []
    for index in range(150):
        player_id = _player(db, f"Bulk Scaling Player {index:03d}")
        _source(db, player_id=player_id, system="API_TENNIS", source_id=f"api-scaling-{index:03d}")
        player_ids.append(player_id)

    scalar_conn = _CountingConnection(db)
    scalar = _scalar_map(scalar_conn, player_ids, cutoff)

    bulk_conn = _CountingConnection(db)
    bulk = resolve_model_player_ids(bulk_conn, player_ids=player_ids, as_of=cutoff)

    assert bulk == scalar
    assert all(value == player_id for player_id, value in bulk.items())

    # Bulk resolution stays bounded; scalar resolution grows with the player count.
    assert bulk_conn.queries <= 6
    assert scalar_conn.queries >= 10 * bulk_conn.queries
    assert scalar_conn.queries >= len(player_ids)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
