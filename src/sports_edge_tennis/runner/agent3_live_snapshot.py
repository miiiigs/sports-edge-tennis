from __future__ import annotations

from collections import deque
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
    """Freeze only players needed to calculate this cohort's pre-match baselines."""
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


def _mutable_state_from_snapshot(
    snapshot: Agent3HistorySnapshot,
) -> tuple[
    dict[int, _MutablePlayerAccumulator],
    dict[tuple[int, int], _MutablePairAccumulator],
    dict[int, set[int]],
]:
    players: dict[int, _MutablePlayerAccumulator] = {}
    for player_id, player in snapshot.players.items():
        players[player_id] = _MutablePlayerAccumulator(
            player_id=player.player_id,
            all_observations=deque(player.all_observations),
            surface_observations={
                surface: deque(observations)
                for surface, observations in player.surface_observations.items()
            },
        )
    pairs: dict[tuple[int, int], _MutablePairAccumulator] = {}
    for pair_key, pair in snapshot.pairs.items():
        pairs[pair_key] = _MutablePairAccumulator(
            player_low_id=pair.player_low_id,
            player_high_id=pair.player_high_id,
            all_observations=deque(pair.all_observations),
            surface_observations={
                surface: deque(observations)
                for surface, observations in pair.surface_observations.items()
            },
        )
    adjacency = {
        player_id: set(neighbors)
        for player_id, neighbors in snapshot.adjacency.items()
    }
    return players, pairs, adjacency


def _apply_grouped_history(
    players: dict[int, _MutablePlayerAccumulator],
    pairs: dict[tuple[int, int], _MutablePairAccumulator],
    adjacency: dict[int, set[int]],
    observations: list[Agent3HistoricalMatch] | tuple[Agent3HistoricalMatch, ...],
    *,
    cutoff,
    minimum_visibility=None,
    config: Agent3Config = DEFAULT_CONFIG,
) -> tuple[int, int]:
    grouped: dict[object, list[Agent3HistoricalMatch]] = {}
    for observation in sorted(observations, key=_history_sort_key):
        if minimum_visibility is not None and observation.visibility_cohort < minimum_visibility:
            continue
        if observation.visibility_cohort >= cutoff:
            continue
        grouped.setdefault(observation.visibility_cohort, []).append(observation)

    applied_updates = 0
    processed_cohorts = 0
    for visibility_cohort in sorted(grouped):
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
    return processed_cohorts, applied_updates


def build_live_agent3_history_snapshot(
    observations: list[Agent3HistoricalMatch] | tuple[Agent3HistoricalMatch, ...],
    *,
    cutoff,
    config: Agent3Config = DEFAULT_CONFIG,
) -> Agent3HistorySnapshot:
    """Build a fresh live Agent 3 graph from a bounded historical bootstrap window."""
    started = perf_counter()
    print(f"AGENT3_SNAPSHOT_START observations={len(observations)}", flush=True)
    players: dict[int, _MutablePlayerAccumulator] = {}
    pairs: dict[tuple[int, int], _MutablePairAccumulator] = {}
    adjacency: dict[int, set[int]] = {}
    processed_cohorts, applied_updates = _apply_grouped_history(
        players,
        pairs,
        adjacency,
        observations,
        cutoff=cutoff,
        config=config,
    )
    expire_history_state(players, pairs, adjacency, cutoff=cutoff, config=config)
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


def advance_live_agent3_history_snapshot(
    snapshot: Agent3HistorySnapshot,
    observations: list[Agent3HistoricalMatch] | tuple[Agent3HistoricalMatch, ...],
    *,
    previous_cutoff,
    cutoff,
    config: Agent3Config = DEFAULT_CONFIG,
) -> Agent3HistorySnapshot:
    """Advance a persisted Agent 3 graph using only history newly visible since the prior cutoff."""
    started = perf_counter()
    players, pairs, adjacency = _mutable_state_from_snapshot(snapshot)
    processed_cohorts, applied_updates = _apply_grouped_history(
        players,
        pairs,
        adjacency,
        observations,
        cutoff=cutoff,
        minimum_visibility=previous_cutoff,
        config=config,
    )
    expire_history_state(players, pairs, adjacency, cutoff=cutoff, config=config)
    advanced = snapshot_history_state(players, pairs, adjacency)
    elapsed_ms = int((perf_counter() - started) * 1000)
    print(
        "AGENT3_SNAPSHOT_INCREMENTAL_READY "
        f"delta_observations={len(observations)} cohorts={processed_cohorts} "
        f"updates={applied_updates} players={len(advanced.players)} "
        f"pairs={len(advanced.pairs)} elapsed_ms={elapsed_ms}",
        flush=True,
    )
    return advanced
