from __future__ import annotations

from collections import OrderedDict, deque
from decimal import Decimal
from time import perf_counter

from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG,
    Agent3Config,
    Agent3HistorySnapshot,
    OutcomeObservation,
    ResidualObservation,
    residual_for_match,
    shrink,
    temporal_weight,
    weighted_mean,
)
from sports_edge_tennis.data.agent3_history import (
    Agent3HistoricalMatch,
    _MutablePairAccumulator,
    _MutablePlayerAccumulator,
    _PreparedMatchUpdate,
    _apply_prepared_update,
    _outcome_for_player,
    expire_history_state,
    historical_match_is_eligible_for_update,
    snapshot_history_state,
)


class _TemporalWeightCache:
    """Bounded exact memoization for Agent 3's expensive Decimal decay weights.

    ``temporal_weight`` depends only on the age delta for a fixed locked config.
    Historical Tennis visibility timestamps reuse the same age deltas heavily, so
    caching the exact returned Decimal removes repeated ``ln``/``exp`` work while
    leaving the model formula and returned values unchanged.
    """

    def __init__(self, config: Agent3Config, *, maxsize: int = 16384) -> None:
        self.config = config
        self.maxsize = maxsize
        self._cache: OrderedDict[tuple[int, int, int], Decimal] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def __call__(self, observed_at, target_as_of) -> Decimal:
        delta = target_as_of - observed_at
        key = (delta.days, delta.seconds, delta.microseconds)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            self._cache.move_to_end(key)
            return cached
        value = temporal_weight(observed_at, target_as_of, self.config)
        self.misses += 1
        self._cache[key] = value
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)
        return value

    @property
    def size(self) -> int:
        return len(self._cache)


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
) -> tuple[object, object, object, int, int, int]:
    return (
        observation.recency_anchor,
        observation.result_as_of,
        observation.result_source_observed_at,
        observation.match_id,
        observation.player_1_id,
        observation.player_2_id,
    )


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


def _player_baseline(
    players: dict[int, _MutablePlayerAccumulator],
    player_id: int,
    *,
    surface,
    as_of,
    config: Agent3Config,
    weight_cache: _TemporalWeightCache,
) -> Decimal:
    player = players.get(player_id)
    if player is None:
        return Decimal("0")
    observations = (
        player.all_observations
        if surface is None
        else player.surface_observations.get(surface, ())
    )
    weighted_values: list[tuple[Decimal, Decimal]] = []
    for observation in observations:
        weight = weight_cache(observation.observed_at, as_of)
        if weight == Decimal("0"):
            continue
        weighted_values.append((Decimal(observation.value), weight))
    effective_count = sum((weight for _, weight in weighted_values), Decimal("0"))
    mean_value = weighted_mean(weighted_values)
    return (
        shrink(effective_count, config.k_g) * mean_value
        if effective_count > 0
        else Decimal("0")
    )


def _prepare_live_historical_match_update(
    observation: Agent3HistoricalMatch,
    players: dict[int, _MutablePlayerAccumulator],
    *,
    config: Agent3Config,
    weight_cache: _TemporalWeightCache,
) -> _PreparedMatchUpdate | None:
    """Prepare one update against the frozen pre-cohort mutable player state.

    No player state is mutated until every match in the visibility cohort has
    been prepared, preserving the same PIT cohort-freeze semantics as the
    reference replay without copying player histories into temporary tuples.
    """
    if not historical_match_is_eligible_for_update(observation):
        return None
    observed_at = observation.recency_anchor
    baseline_as_of = observation.visibility_cohort
    canonical_pair = (
        (observation.player_1_id, observation.player_2_id)
        if observation.player_1_id < observation.player_2_id
        else (observation.player_2_id, observation.player_1_id)
    )
    low_outcome = _outcome_for_player(observation, canonical_pair[0])
    player_1_outcome = OutcomeObservation(
        observed_at=observed_at,
        value=_outcome_for_player(observation, observation.player_1_id),
    )
    player_2_outcome = OutcomeObservation(
        observed_at=observed_at,
        value=_outcome_for_player(observation, observation.player_2_id),
    )
    baseline_low_all = _player_baseline(
        players,
        canonical_pair[0],
        surface=None,
        as_of=baseline_as_of,
        config=config,
        weight_cache=weight_cache,
    )
    baseline_high_all = _player_baseline(
        players,
        canonical_pair[1],
        surface=None,
        as_of=baseline_as_of,
        config=config,
        weight_cache=weight_cache,
    )
    all_residual = ResidualObservation(
        observed_at=observed_at,
        residual=residual_for_match(
            baseline_low_all,
            baseline_high_all,
            outcome=low_outcome,
            config=config,
        ),
    )

    surface_player_1_outcome = None
    surface_player_2_outcome = None
    surface_residual = None
    if observation.surface is not None:
        baseline_low_surface = _player_baseline(
            players,
            canonical_pair[0],
            surface=observation.surface,
            as_of=baseline_as_of,
            config=config,
            weight_cache=weight_cache,
        )
        baseline_high_surface = _player_baseline(
            players,
            canonical_pair[1],
            surface=observation.surface,
            as_of=baseline_as_of,
            config=config,
            weight_cache=weight_cache,
        )
        surface_player_1_outcome = (observation.surface, player_1_outcome)
        surface_player_2_outcome = (observation.surface, player_2_outcome)
        surface_residual = (
            observation.surface,
            ResidualObservation(
                observed_at=observed_at,
                residual=residual_for_match(
                    baseline_low_surface,
                    baseline_high_surface,
                    outcome=low_outcome,
                    config=config,
                ),
            ),
        )
    return _PreparedMatchUpdate(
        player_1_id=observation.player_1_id,
        player_2_id=observation.player_2_id,
        observed_at=observed_at,
        player_1_outcome=player_1_outcome,
        player_2_outcome=player_2_outcome,
        surface_player_1_outcome=surface_player_1_outcome,
        surface_player_2_outcome=surface_player_2_outcome,
        canonical_pair=canonical_pair,
        all_residual=all_residual,
        surface_residual=surface_residual,
    )


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

    weight_cache = _TemporalWeightCache(config)
    applied_updates = 0
    processed_cohorts = 0
    for visibility_cohort in sorted(grouped):
        cohort = sorted(grouped[visibility_cohort], key=_cohort_sort_key)
        processed_cohorts += 1
        prepared_updates = [
            update
            for update in (
                _prepare_live_historical_match_update(
                    observation,
                    players,
                    config=config,
                    weight_cache=weight_cache,
                )
                for observation in cohort
            )
            if update is not None
        ]
        for update in prepared_updates:
            _apply_prepared_update(players, pairs, adjacency, update)
        applied_updates += len(prepared_updates)
    print(
        "AGENT3_WEIGHT_CACHE "
        f"hits={weight_cache.hits} misses={weight_cache.misses} size={weight_cache.size}",
        flush=True,
    )
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
