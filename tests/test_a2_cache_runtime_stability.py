"""P0 A2 cache/runtime stability regression tests.

Covers:
* live odds providers (ODDSPAPI/PINNAPI) must not block the live->historical bridge;
* checkpoint cutoff can never regress (compare-and-swap guard);
* a new historical source identity forces a rebuild;
* zero-delta incremental advancement preserves all player state exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.data.agent2_history import (
    Agent2HistorySnapshot,
    PlayerHistorySnapshot,
    PopulationHistorySnapshot,
    SurfacePointSnapshot,
)
from sports_edge_tennis.data.model_identity import (
    has_historical_model_identity,
    resolve_model_player_id,
)
from sports_edge_tennis.data.model_identity_bridge import LIVE_FIXTURE_SOURCE_SYSTEMS
from sports_edge_tennis.data.normalize import normalize_person_lookup
from sports_edge_tennis.runner.agent2_live_snapshot import (
    advance_agent2_history_snapshot,
)
from sports_edge_tennis.runner.live_model_cache import (
    AGENT2_CACHE_KEY,
    checkpoint_requires_rebuild,
    decode_agent2_snapshot,
    encode_agent2_snapshot,
    load_checkpoint,
    save_checkpoint,
)

MODEL_VERSION = "agent2_hierarchical_markov_v1"
FEATURE_VERSION = "agent2_serve_return_inputs_v1"


def _player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _source(conn: psycopg.Connection, player_id: int, system: str, source_id: str) -> None:
    conn.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id) VALUES (%s, %s, %s)",
        (player_id, system, source_id),
    )


def _alias(conn: psycopg.Connection, player_id: int, alias: str, system: str = "TENNIS_DATA") -> None:
    conn.execute(
        """
        INSERT INTO player_aliases (player_id, source_system, alias, normalized_alias)
        VALUES (%s, %s, %s, %s)
        """,
        (player_id, system, alias, normalize_person_lookup(alias)),
    )


def _completed_history(conn: psycopg.Connection, player_id: int, when: datetime) -> None:
    tournament_id = conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('Cache Test Open','ATP_MAIN','ATP_250') RETURNING tournament_id"
    ).fetchone()[0]
    edition_id = conn.execute(
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2025, 'Cache Test Open') RETURNING tournament_edition_id",
        (tournament_id,),
    ).fetchone()[0]
    match_id = conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (edition_id,)
    ).fetchone()[0]
    opponent = _player(conn, "Cache Opponent")
    _source(conn, opponent, "SACKMANN", f"opp-{player_id}")
    conn.execute(
        """
        INSERT INTO match_versions (match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of)
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'COMPLETED', %s, %s)
        """,
        (match_id, player_id, opponent, when, when, when),
    )
    conn.execute(
        "INSERT INTO match_result_observations (match_id, outcome_status, winner_side, source_observed_at, as_of) VALUES (%s, 'COMPLETED', 1, %s, %s)",
        (match_id, when, when),
    )


def test_live_odds_providers_do_not_block_live_to_historical_bridge(db: psycopg.Connection):
    assert "ODDSPAPI" in LIVE_FIXTURE_SOURCE_SYSTEMS
    assert "PINNAPI" in LIVE_FIXTURE_SOURCE_SYSTEMS

    historical = _player(db, "Bridge Cache Player")
    _source(db, historical, "SACKMANN", "hist-cache-1")
    _alias(db, historical, "Bridge Cache Player")
    _completed_history(db, historical, datetime(2025, 1, 1, 12, tzinfo=UTC))

    live = _player(db, "Bridge Cache Player")
    _source(db, live, "API_TENNIS", "api-cache-1")
    _source(db, live, "SPORTRADAR", "sr:competitor:1")
    _source(db, live, "ODDSPAPI", "1")
    _source(db, live, "PINNAPI", "1")

    assert has_historical_model_identity(db, live) is False
    assert resolve_model_player_id(db, player_id=live, as_of=datetime(2025, 6, 1, tzinfo=UTC)) == historical


def test_checkpoint_cutoff_cannot_regress(db: psycopg.Connection):
    newer = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)
    older = newer - timedelta(hours=2)
    save_checkpoint(
        db,
        checkpoint_key=AGENT2_CACHE_KEY,
        model_version=MODEL_VERSION,
        feature_version=FEATURE_VERSION,
        checkpoint_cutoff=newer,
        observation_count=10,
        payload=b"newer-payload",
    )
    save_checkpoint(
        db,
        checkpoint_key=AGENT2_CACHE_KEY,
        model_version=MODEL_VERSION,
        feature_version=FEATURE_VERSION,
        checkpoint_cutoff=older,
        observation_count=5,
        payload=b"older-payload",
    )
    stored = load_checkpoint(
        db, checkpoint_key=AGENT2_CACHE_KEY, model_version=MODEL_VERSION, feature_version=FEATURE_VERSION
    )
    assert stored is not None
    assert stored.checkpoint_cutoff == newer
    assert stored.payload == b"newer-payload"


def test_new_historical_source_identity_forces_rebuild(db: psycopg.Connection):
    cutoff = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)
    save_checkpoint(
        db,
        checkpoint_key=AGENT2_CACHE_KEY,
        model_version=MODEL_VERSION,
        feature_version=FEATURE_VERSION,
        checkpoint_cutoff=cutoff,
        observation_count=1,
        payload=b"x",
    )
    checkpoint = load_checkpoint(
        db, checkpoint_key=AGENT2_CACHE_KEY, model_version=MODEL_VERSION, feature_version=FEATURE_VERSION
    )
    assert checkpoint is not None

    # A live odds provider id must NOT invalidate the cache.
    live_player = _player(db, "Odds Only Player")
    _source(db, live_player, "ODDSPAPI", "999999")
    assert checkpoint_requires_rebuild(
        db, checkpoint=checkpoint, current_cutoff=cutoff, include_match_stats=True
    ) is None

    # A new historical (SACKMANN) source id must invalidate the cache.
    hist_player = _player(db, "Historical Player")
    _source(db, hist_player, "SACKMANN", "hist-late")
    assert (
        checkpoint_requires_rebuild(
            db, checkpoint=checkpoint, current_cutoff=cutoff, include_match_stats=True
        )
        == "IDENTITY_SOURCE_CHANGE"
    )


def test_zero_delta_advance_preserves_state():
    snapshot = Agent2HistorySnapshot(
        players={
            7: PlayerHistorySnapshot(
                player_id=7,
                prior_usable_matches=3,
                overall_serve_points_won=11,
                overall_serve_points_played=20,
                overall_return_points_won=9,
                overall_return_points_played=21,
                surface_points={"HARD": SurfacePointSnapshot(11, 20, 9, 21)},
            )
        },
        population=PopulationHistorySnapshot(
            surface_tour_service_points={("HARD", "ATP_MAIN"): (11, 20)},
            surface_service_points={"HARD": (11, 20)},
            global_service_points_won=11,
            global_service_points_played=20,
        ),
    )
    decoded = decode_agent2_snapshot(encode_agent2_snapshot(snapshot))
    advanced = advance_agent2_history_snapshot(decoded, [], cutoff=datetime(2026, 9, 25, tzinfo=UTC))
    assert advanced.players == snapshot.players
    assert advanced.population == snapshot.population
