from __future__ import annotations

from time import perf_counter

from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG,
    Agent3Config,
    Agent3HistorySnapshot,
    PlayerHistorySnapshot,
)
from sports_edge_tennis.data.agent3_history import (
    Agent3HistoricalMatch,
    _MutablePairAccumulator,
    _MutablePlayerAccumulator,
    _apply_prepared_update,
    expire_history_state,
    prepare_historical_match_update,
    snapshot_history_state,
)


def _history_sort_key(
    observation: Agent3HistoricalMatch,
) -> tuple[object, object, int, int, int]:
    return (
        observation.visibility_cohort,
        observation.result_source_observed_at,
        observation.match_id,
        observation.player_1_id,
        observation.player_2_id,
    )


def _cohort_sort_key(
    observation: Agent3HistoricalMatch,
) -> tuple[object, object, int, int, int]:
    return (
        observation.result_as_of,
        observation.result_source_observed_at,
        observation.match_id,
        observation.player_1_id,
        observation.player_2_id,
    )


def _snapshot_cohort_players(
    players: dict[int, _MutablePlayerAccumulator],
    cohort: list[Agent3HistoricalMatch],
) -> Agent3HistorySnapshot:
    """Freeze only players needed to calculate this cohort's pre-match baselines.

    Agent 3 residual construction reads only player history. Pair history and
    adjacency are needed by final live prediction, not while replaying historical
    results into the snapshot. Keeping the cohort snapshot narrow avoids copying
    the entire historical graph for every visibility cohort.
    """
    player_ids = {
        player_id
        for observation in cohort
        for player_id in (observation.player_1_id, observation.player_2_id)
    }
    frozen_players: dict[int, PlayerHistorySnapshot] = {}
    for player_id in sorted(player_ids):
        player = players.get(player_id)
        if player is None:
            continue
        frozen_players[player_id] = PlayerHistorySnapshot(
            player_id=player.player_id,
            all_observations=tuple(player.all_observations),
            surface_observations={
                surface: tuple(observations)
                for surface, observations in player.surface_observations.items()
            },
        )
    return Agent3HistorySnapshot(players=frozen_players, pairs={}, adjacency={})


def build_live_agent3_history_snapshot(
    observations: list[Agent3HistoricalMatch] | tuple[Agent3HistoricalMatch, ...],
    *,
    cutoff,
    config: Agent3Config = DEFAULT_CONFIG,
) -> Agent3HistorySnapshot:
    """Build the live Agent 3 snapshot without per-cohort full-graph copies.

    This preserves the frozen pre-cohort semantics used by the reference replay.
    Historical observations older than the configured lookback may stay in the
    mutable build state temporarily because Agent 3's temporal weighting already
    assigns them zero weight at each cohort. A single authoritative expiration at
    the live cutoff then produces the same final lookback-bounded graph.
    """
    started = perf_counter()
    print(
        f"AGENT3_SNAPSHOT_START observations={len(observations)}",
        flush=True,
    )

    grouped: dict[object, list[Agent3HistoricalMatch]] = {}
    for observation in sorted(observations, key=_history_sort_key):
        grouped.setdefault(observation.visibility_cohort, []).append(observation)

    players: dict[int, _MutablePlayerAccumulator] = {}
    pairs: dict[tuple[int, int], _MutablePairAccumulator] = {}
    adjacency: dict[int, set[int]] = {}
    applied_updates = 0
    processed_cohorts = 0

    for visibility_cohort in sorted(grouped):
        if visibility_cohort >= cutoff:
            continue
        cohort = sorted(grouped[visibility_cohort], key=_cohort_sort_key)
        processed_cohorts += 1
        frozen_snapshot = _snapshot_cohort_players(players, cohort)
        prepared_updates = [
            update
            for update in (
                prepare_historical_match_update(
                    observation,
                    frozen_snapshot,
                    config=config,
                )
                for observation in cohort
            )
            if update is not None
        ]
        for update in prepared_updates:
            _apply_prepared_update(players, pairs, adjacency, update)
        applied_updates += len(prepared_updates)

    expire_history_state(
        players,
        pairs,
        adjacency,
        cutoff=cutoff,
        config=config,
    )
    snapshot = snapshot_history_state(players, pairs, adjacency)
    elapsed_ms = int((perf_counter() - started) * 1000)
    print(
        "AGENT3_SNAPSHOT_READY "
        f"observations={len(observations)} cohorts={processed_cohorts} "
        f"updates={applied_updates} players={len(snapshot.players)} "
        f"pairs={len(snapshot.pairs)} elapsed_ms={elapsed_ms}",
        flush=True,
    )
    return snapshot
