from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg

from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3HistorySnapshot,
    OutcomeObservation,
    PairHistorySnapshot,
    ResidualObservation,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    PlayerHistorySnapshot as Agent3PlayerHistorySnapshot,
)
from sports_edge_tennis.data.agent2_history import (
    Agent2HistorySnapshot,
    PopulationHistorySnapshot,
    SurfacePointSnapshot,
)
from sports_edge_tennis.data.agent2_history import (
    PlayerHistorySnapshot as Agent2PlayerHistorySnapshot,
)
from sports_edge_tennis.data.model_identity_bridge import LIVE_FIXTURE_SOURCE_SYSTEMS

CACHE_SCHEMA_VERSION = "live_model_state_v1"
AGENT2_CACHE_KEY = "AGENT_2_HIERARCHICAL_MARKOV"
AGENT3_CACHE_KEY = "AGENT_3_INTRANSITIVITY"

_CHECKPOINT_DDL = """
CREATE TABLE IF NOT EXISTS live_model_state_checkpoints (
    checkpoint_key TEXT PRIMARY KEY,
    state_schema_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    checkpoint_cutoff TIMESTAMPTZ NOT NULL,
    checkpoint_created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    observation_count BIGINT NOT NULL DEFAULT 0 CHECK (observation_count >= 0),
    payload BYTEA NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    payload_bytes BIGINT NOT NULL CHECK (payload_bytes >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@dataclass(frozen=True)
class LiveModelCheckpoint:
    checkpoint_key: str
    state_schema_version: str
    model_version: str
    feature_version: str
    checkpoint_cutoff: datetime
    checkpoint_created_at: datetime
    observation_count: int
    payload: bytes
    payload_sha256: str


def ensure_live_model_checkpoint_table(conn: psycopg.Connection) -> None:
    """Create the derived operational cache table if migration 0010 is not applied yet.

    The table contains only rebuildable model state. Canonical historical truth stays
    in the append-only source tables.
    """
    conn.execute(_CHECKPOINT_DDL)


def load_checkpoint(
    conn: psycopg.Connection,
    *,
    checkpoint_key: str,
    model_version: str,
    feature_version: str,
) -> LiveModelCheckpoint | None:
    ensure_live_model_checkpoint_table(conn)
    row = conn.execute(
        """
        SELECT
            checkpoint_key,
            state_schema_version,
            model_version,
            feature_version,
            checkpoint_cutoff,
            checkpoint_created_at,
            observation_count,
            payload,
            payload_sha256
        FROM live_model_state_checkpoints
        WHERE checkpoint_key = %s
        """,
        (checkpoint_key,),
    ).fetchone()
    if row is None:
        return None
    if row[1] != CACHE_SCHEMA_VERSION or row[2] != model_version or row[3] != feature_version:
        return None
    payload = bytes(row[7])
    digest = hashlib.sha256(payload).hexdigest()
    if digest != row[8]:
        print(
            f"MODEL_CACHE_INVALID key={checkpoint_key} reason=CHECKSUM_MISMATCH",
            flush=True,
        )
        return None
    return LiveModelCheckpoint(
        checkpoint_key=row[0],
        state_schema_version=row[1],
        model_version=row[2],
        feature_version=row[3],
        checkpoint_cutoff=row[4],
        checkpoint_created_at=row[5],
        observation_count=int(row[6]),
        payload=payload,
        payload_sha256=row[8],
    )


def save_checkpoint(
    conn: psycopg.Connection,
    *,
    checkpoint_key: str,
    model_version: str,
    feature_version: str,
    checkpoint_cutoff: datetime,
    observation_count: int,
    payload: bytes,
) -> None:
    ensure_live_model_checkpoint_table(conn)
    digest = hashlib.sha256(payload).hexdigest()
    # Monotonic guard: a checkpoint write may never regress the persisted cutoff.
    # This makes every writer compare-and-swap on cutoff, so a stale or slower
    # concurrent batch (or an older-cutoff validation run) cannot overwrite a
    # newer valid checkpoint with an older state.
    cursor = conn.execute(
        """
        INSERT INTO live_model_state_checkpoints (
            checkpoint_key,
            state_schema_version,
            model_version,
            feature_version,
            checkpoint_cutoff,
            checkpoint_created_at,
            observation_count,
            payload,
            payload_sha256,
            payload_bytes,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (checkpoint_key) DO UPDATE SET
            state_schema_version = EXCLUDED.state_schema_version,
            model_version = EXCLUDED.model_version,
            feature_version = EXCLUDED.feature_version,
            checkpoint_cutoff = EXCLUDED.checkpoint_cutoff,
            checkpoint_created_at = CURRENT_TIMESTAMP,
            observation_count = EXCLUDED.observation_count,
            payload = EXCLUDED.payload,
            payload_sha256 = EXCLUDED.payload_sha256,
            payload_bytes = EXCLUDED.payload_bytes,
            updated_at = CURRENT_TIMESTAMP
        WHERE EXCLUDED.checkpoint_cutoff >= live_model_state_checkpoints.checkpoint_cutoff
        """,
        (
            checkpoint_key,
            CACHE_SCHEMA_VERSION,
            model_version,
            feature_version,
            checkpoint_cutoff,
            observation_count,
            payload,
            digest,
            len(payload),
        ),
    )
    if cursor.rowcount == 0:
        print(
            "MODEL_CACHE_SAVE_REJECTED "
            f"key={checkpoint_key} reason=CUTOFF_REGRESSION "
            f"attempted_cutoff={checkpoint_cutoff.isoformat()}",
            flush=True,
        )
        return
    print(
        "MODEL_CACHE_SAVED "
        f"key={checkpoint_key} cutoff={checkpoint_cutoff.isoformat()} "
        f"observations={observation_count} bytes={len(payload)}",
        flush=True,
    )


def checkpoint_requires_rebuild(
    conn: psycopg.Connection,
    *,
    checkpoint: LiveModelCheckpoint,
    current_cutoff: datetime,
    include_match_stats: bool,
) -> str | None:
    """Detect cache-invalidating late backfills/corrections without changing PIT truth."""
    # A newly attached non-live (historical) source identity can change
    # live->historical player normalization, which changes the player-id key
    # space of the cached snapshot. Live/odds provider ids cannot, so they do
    # not force a rebuild.
    identity_change = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM player_source_ids psi
            WHERE psi.created_at > %s
              AND psi.source_system <> ALL(%s)
        )
        """,
        (checkpoint.checkpoint_created_at, list(LIVE_FIXTURE_SOURCE_SYSTEMS)),
    ).fetchone()[0]
    if identity_change:
        return "IDENTITY_SOURCE_CHANGE"

    late_result = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM match_result_observations mro
            WHERE mro.ingested_at > %s
              AND mro.outcome_status = 'COMPLETED'
              AND mro.source_observed_at <= %s
              AND mro.as_of <= %s
        )
        """,
        (
            checkpoint.checkpoint_created_at,
            checkpoint.checkpoint_cutoff,
            checkpoint.checkpoint_cutoff,
        ),
    ).fetchone()[0]
    if late_result:
        return "LATE_RESULT_BACKFILL"

    corrected_completed_match = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM match_result_observations newer
            WHERE newer.outcome_status = 'COMPLETED'
              AND GREATEST(newer.source_observed_at, newer.as_of) >= %s
              AND newer.source_observed_at <= %s
              AND newer.as_of <= %s
              AND EXISTS (
                  SELECT 1
                  FROM match_result_observations older
                  WHERE older.match_id = newer.match_id
                    AND older.outcome_status = 'COMPLETED'
                    AND older.source_observed_at < %s
                    AND older.as_of < %s
              )
        )
        """,
        (
            checkpoint.checkpoint_cutoff,
            current_cutoff,
            current_cutoff,
            checkpoint.checkpoint_cutoff,
            checkpoint.checkpoint_cutoff,
        ),
    ).fetchone()[0]
    if corrected_completed_match:
        return "COMPLETED_MATCH_CORRECTION"

    if include_match_stats:
        late_stats = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM match_stat_observations mso
                WHERE mso.ingested_at > %s
                  AND mso.source_observed_at <= %s
                  AND mso.as_of <= %s
            )
            """,
            (
                checkpoint.checkpoint_created_at,
                checkpoint.checkpoint_cutoff,
                checkpoint.checkpoint_cutoff,
            ),
        ).fetchone()[0]
        if late_stats:
            return "LATE_MATCH_STAT_BACKFILL"

    return None


def _compress_json(payload: dict[str, object]) -> bytes:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _decompress_json(payload: bytes) -> dict[str, object]:
    decoded = json.loads(gzip.decompress(payload).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("cached payload root must be an object")
    return decoded


def _dt(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("cached timestamp must be timezone-aware")
    return result


def encode_agent2_snapshot(snapshot: Agent2HistorySnapshot) -> bytes:
    players = []
    for player_id in sorted(snapshot.players):
        player = snapshot.players[player_id]
        players.append(
            [
                player.player_id,
                player.prior_usable_matches,
                player.overall_serve_points_won,
                player.overall_serve_points_played,
                player.overall_return_points_won,
                player.overall_return_points_played,
                [
                    [
                        surface,
                        state.serve_points_won,
                        state.serve_points_played,
                        state.return_points_won,
                        state.return_points_played,
                    ]
                    for surface, state in sorted(player.surface_points.items())
                ],
            ]
        )
    population = snapshot.population
    payload = {
        "kind": "agent2",
        "players": players,
        "population": {
            "surface_tour": [
                [surface, tour_scope, won, played]
                for (surface, tour_scope), (won, played) in sorted(
                    population.surface_tour_service_points.items()
                )
            ],
            "surface": [
                [surface, won, played]
                for surface, (won, played) in sorted(population.surface_service_points.items())
            ],
            "global": [
                population.global_service_points_won,
                population.global_service_points_played,
            ],
        },
    }
    return _compress_json(payload)


def decode_agent2_snapshot(payload: bytes) -> Agent2HistorySnapshot:
    data = _decompress_json(payload)
    if data.get("kind") != "agent2":
        raise ValueError("cached payload is not Agent 2 state")
    players: dict[int, Agent2PlayerHistorySnapshot] = {}
    for row in data["players"]:
        player_id = int(row[0])
        surfaces = {
            item[0]: SurfacePointSnapshot(
                serve_points_won=int(item[1]),
                serve_points_played=int(item[2]),
                return_points_won=int(item[3]),
                return_points_played=int(item[4]),
            )
            for item in row[6]
        }
        players[player_id] = Agent2PlayerHistorySnapshot(
            player_id=player_id,
            prior_usable_matches=int(row[1]),
            overall_serve_points_won=int(row[2]),
            overall_serve_points_played=int(row[3]),
            overall_return_points_won=int(row[4]),
            overall_return_points_played=int(row[5]),
            surface_points=surfaces,
        )
    population_data = data["population"]
    population = PopulationHistorySnapshot(
        surface_tour_service_points={
            (item[0], item[1]): (int(item[2]), int(item[3]))
            for item in population_data["surface_tour"]
        },
        surface_service_points={
            item[0]: (int(item[1]), int(item[2]))
            for item in population_data["surface"]
        },
        global_service_points_won=int(population_data["global"][0]),
        global_service_points_played=int(population_data["global"][1]),
    )
    return Agent2HistorySnapshot(players=players, population=population)


def encode_agent3_snapshot(snapshot: Agent3HistorySnapshot) -> bytes:
    players = []
    for player_id in sorted(snapshot.players):
        player = snapshot.players[player_id]
        players.append(
            [
                player.player_id,
                [[obs.observed_at.isoformat(), obs.value] for obs in player.all_observations],
                [
                    [
                        surface,
                        [[obs.observed_at.isoformat(), obs.value] for obs in observations],
                    ]
                    for surface, observations in sorted(player.surface_observations.items())
                ],
            ]
        )
    pairs = []
    for pair_key in sorted(snapshot.pairs):
        pair = snapshot.pairs[pair_key]
        pairs.append(
            [
                pair.player_low_id,
                pair.player_high_id,
                [
                    [obs.observed_at.isoformat(), str(obs.residual)]
                    for obs in pair.all_observations
                ],
                [
                    [
                        surface,
                        [
                            [obs.observed_at.isoformat(), str(obs.residual)]
                            for obs in observations
                        ],
                    ]
                    for surface, observations in sorted(pair.surface_observations.items())
                ],
            ]
        )
    return _compress_json({"kind": "agent3", "players": players, "pairs": pairs})


def decode_agent3_snapshot(payload: bytes) -> Agent3HistorySnapshot:
    data = _decompress_json(payload)
    if data.get("kind") != "agent3":
        raise ValueError("cached payload is not Agent 3 state")
    players: dict[int, Agent3PlayerHistorySnapshot] = {}
    for row in data["players"]:
        player_id = int(row[0])
        players[player_id] = Agent3PlayerHistorySnapshot(
            player_id=player_id,
            all_observations=tuple(
                OutcomeObservation(observed_at=_dt(item[0]), value=int(item[1]))
                for item in row[1]
            ),
            surface_observations={
                surface_row[0]: tuple(
                    OutcomeObservation(observed_at=_dt(item[0]), value=int(item[1]))
                    for item in surface_row[1]
                )
                for surface_row in row[2]
            },
        )
    pairs: dict[tuple[int, int], PairHistorySnapshot] = {}
    adjacency: dict[int, set[int]] = {}
    for row in data["pairs"]:
        low_id = int(row[0])
        high_id = int(row[1])
        pair = PairHistorySnapshot(
            player_low_id=low_id,
            player_high_id=high_id,
            all_observations=tuple(
                ResidualObservation(observed_at=_dt(item[0]), residual=Decimal(item[1]))
                for item in row[2]
            ),
            surface_observations={
                surface_row[0]: tuple(
                    ResidualObservation(
                        observed_at=_dt(item[0]), residual=Decimal(item[1])
                    )
                    for item in surface_row[1]
                )
                for surface_row in row[3]
            },
        )
        pairs[(low_id, high_id)] = pair
        adjacency.setdefault(low_id, set()).add(high_id)
        adjacency.setdefault(high_id, set()).add(low_id)
    return Agent3HistorySnapshot(
        players=players,
        pairs=pairs,
        adjacency={player_id: tuple(sorted(neighbors)) for player_id, neighbors in adjacency.items()},
    )
