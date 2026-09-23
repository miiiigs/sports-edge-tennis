from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import psycopg

from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3Config,
    Agent3HistorySnapshot,
    DEFAULT_CONFIG,
    OutcomeObservation,
    PairHistorySnapshot,
    PlayerHistorySnapshot,
    PredictionRequest,
    PredictionResult,
    ResidualObservation,
    Surface,
    predict,
    residual_for_match,
    summarize_player_bucket,
)
from sports_edge_tennis.data.model_identity import resolve_model_player_id


@dataclass(frozen=True)
class Agent3HistoricalMatch:
    match_id: int
    player_1_id: int
    player_2_id: int
    match_start_at: datetime | None
    surface: Surface | None
    tour_scope: str
    competition_tier: str
    outcome_status: str
    winner_side: int | None
    result_source_observed_at: datetime
    result_as_of: datetime
    version_source_observed_at: datetime
    version_as_of: datetime

    @property
    def visibility_cohort(self) -> datetime:
        return max(self.result_source_observed_at, self.result_as_of)

    @property
    def recency_anchor(self) -> datetime:
        """Return match occurrence time for decay without weakening PIT visibility.

        Backfilled results become usable only at ``visibility_cohort``. Once visible,
        however, a January match must decay from January rather than from the September
        date on which it was imported. Missing, naive, or impossible future start times
        fail closed to the visibility timestamp.
        """
        start = self.match_start_at
        visibility = self.visibility_cohort
        if start is None or start.tzinfo is None or start > visibility:
            return visibility
        return start


@dataclass(frozen=True)
class HistoricalPredictionRecord:
    observation: Agent3HistoricalMatch
    request: PredictionRequest
    result: PredictionResult


@dataclass(frozen=True)
class HistoricalReplayResult:
    records: tuple[HistoricalPredictionRecord, ...]
    final_snapshot: Agent3HistorySnapshot


@dataclass
class _MutablePlayerAccumulator:
    player_id: int
    all_observations: deque[OutcomeObservation] = field(default_factory=deque)
    surface_observations: dict[Surface, deque[OutcomeObservation]] = field(default_factory=dict)


@dataclass
class _MutablePairAccumulator:
    player_low_id: int
    player_high_id: int
    all_observations: deque[ResidualObservation] = field(default_factory=deque)
    surface_observations: dict[Surface, deque[ResidualObservation]] = field(default_factory=dict)


@dataclass(frozen=True)
class _PreparedMatchUpdate:
    player_1_id: int
    player_2_id: int
    observed_at: datetime
    player_1_outcome: OutcomeObservation
    player_2_outcome: OutcomeObservation
    surface_player_1_outcome: tuple[Surface, OutcomeObservation] | None
    surface_player_2_outcome: tuple[Surface, OutcomeObservation] | None
    canonical_pair: tuple[int, int]
    all_residual: ResidualObservation
    surface_residual: tuple[Surface, ResidualObservation] | None


def _history_sort_key(
    observation: Agent3HistoricalMatch,
) -> tuple[datetime, datetime, int, int, int]:
    return (
        observation.visibility_cohort,
        observation.result_source_observed_at,
        observation.match_id,
        observation.player_1_id,
        observation.player_2_id,
    )


def _cohort_sort_key(
    observation: Agent3HistoricalMatch,
) -> tuple[datetime, datetime, datetime, int, int, int]:
    return (
        observation.recency_anchor,
        observation.result_as_of,
        observation.result_source_observed_at,
        observation.match_id,
        observation.player_1_id,
        observation.player_2_id,
    )


def _resolve_history_player_ids(
    conn: psycopg.Connection,
    rows: list[tuple],
    *,
    cutoff: datetime,
) -> dict[int, int]:
    """Bridge only API-only rows, caching each player resolution once per load."""
    player_ids = sorted({int(row[1]) for row in rows} | {int(row[2]) for row in rows})
    if not player_ids:
        return {}
    historical_rows = conn.execute(
        """
        SELECT DISTINCT player_id
        FROM player_source_ids
        WHERE player_id = ANY(%s)
          AND source_system <> 'API_TENNIS'
        """,
        (player_ids,),
    ).fetchall()
    historical_ids = {int(row[0]) for row in historical_rows}
    mapping = {player_id: player_id for player_id in historical_ids}
    for player_id in player_ids:
        if player_id in mapping:
            continue
        mapping[player_id] = resolve_model_player_id(
            conn,
            player_id=player_id,
            as_of=cutoff,
        )
    return mapping


def load_agent3_history(
    conn: psycopg.Connection,
    *,
    cutoff: datetime,
    tour_scopes: tuple[str, ...] | list[str] | None = None,
    competition_tiers: tuple[str, ...] | list[str] | None = None,
    year_start: int | None = None,
    year_end: int | None = None,
    date_start: date | None = None,
    date_end: date | None = None,
) -> list[Agent3HistoricalMatch]:
    result_filters = [
        "mro.source_observed_at <= %(cutoff)s",
        "mro.as_of <= %(cutoff)s",
    ]
    version_filters = [
        "mv.source_observed_at <= selected_results.source_observed_at",
        "mv.as_of <= selected_results.as_of",
    ]
    params: dict[str, object] = {"cutoff": cutoff}

    if tour_scopes:
        version_filters.append("mv.tour_scope::text = ANY(%(tour_scopes)s)")
        params["tour_scopes"] = list(tour_scopes)
    if competition_tiers:
        version_filters.append("mv.competition_tier::text = ANY(%(competition_tiers)s)")
        params["competition_tiers"] = list(competition_tiers)
    if year_start is not None:
        version_filters.append("te.season_year >= %(year_start)s")
        params["year_start"] = year_start
    if year_end is not None:
        version_filters.append("te.season_year <= %(year_end)s")
        params["year_end"] = year_end
    if date_start is not None:
        result_filters.append("mro.source_observed_at::date >= %(date_start)s")
        params["date_start"] = date_start
    if date_end is not None:
        result_filters.append("mro.source_observed_at::date <= %(date_end)s")
        params["date_end"] = date_end

    query = f"""
        WITH selected_results AS (
            SELECT
                mro.match_result_observation_id,
                mro.match_id,
                mro.outcome_status::text AS outcome_status,
                mro.winner_side,
                mro.source_observed_at,
                mro.as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY mro.match_id
                    ORDER BY mro.as_of DESC, mro.source_observed_at DESC, mro.match_result_observation_id DESC
                ) AS result_rank
            FROM match_result_observations mro
            WHERE {" AND ".join(result_filters)}
        ),
        selected_versions AS (
            SELECT
                selected_results.match_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at,
                mv.surface::text AS surface,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                selected_results.outcome_status,
                selected_results.winner_side,
                selected_results.source_observed_at AS result_source_observed_at,
                selected_results.as_of AS result_as_of,
                mv.source_observed_at AS version_source_observed_at,
                mv.as_of AS version_as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY selected_results.match_id
                    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
                ) AS version_rank
            FROM selected_results
            JOIN matches m
                ON m.match_id = selected_results.match_id
            JOIN tournament_editions te
                ON te.tournament_edition_id = m.tournament_edition_id
            JOIN match_versions mv
                ON mv.match_id = selected_results.match_id
            WHERE selected_results.result_rank = 1
              AND {" AND ".join(version_filters)}
        )
        SELECT
            match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            surface,
            tour_scope,
            competition_tier,
            outcome_status,
            winner_side,
            result_source_observed_at,
            result_as_of,
            version_source_observed_at,
            version_as_of
        FROM selected_versions
        WHERE version_rank = 1
        ORDER BY
            result_as_of,
            result_source_observed_at,
            match_id,
            player_1_id,
            player_2_id
    """

    rows = conn.execute(query, params).fetchall()
    model_player_ids = _resolve_history_player_ids(conn, rows, cutoff=cutoff)
    return [
        Agent3HistoricalMatch(
            match_id=int(row[0]),
            player_1_id=model_player_ids.get(int(row[1]), int(row[1])),
            player_2_id=model_player_ids.get(int(row[2]), int(row[2])),
            match_start_at=row[3],
            surface=row[4],
            tour_scope=row[5],
            competition_tier=row[6],
            outcome_status=row[7],
            winner_side=row[8],
            result_source_observed_at=row[9],
            result_as_of=row[10],
            version_source_observed_at=row[11],
            version_as_of=row[12],
        )
        for row in rows
    ]


def historical_match_is_eligible_for_update(observation: Agent3HistoricalMatch) -> bool:
    return (
        observation.outcome_status == "COMPLETED"
        and observation.winner_side in (1, 2)
        and observation.player_1_id > 0
        and observation.player_2_id > 0
        and observation.player_1_id != observation.player_2_id
    )


def _mutable_player(
    players: dict[int, _MutablePlayerAccumulator],
    player_id: int,
) -> _MutablePlayerAccumulator:
    accumulator = players.get(player_id)
    if accumulator is None:
        accumulator = _MutablePlayerAccumulator(player_id=player_id)
        players[player_id] = accumulator
    return accumulator


def _mutable_pair(
    pairs: dict[tuple[int, int], _MutablePairAccumulator],
    canonical_pair: tuple[int, int],
) -> _MutablePairAccumulator:
    accumulator = pairs.get(canonical_pair)
    if accumulator is None:
        accumulator = _MutablePairAccumulator(
            player_low_id=canonical_pair[0],
            player_high_id=canonical_pair[1],
        )
        pairs[canonical_pair] = accumulator
    return accumulator


def _snapshot_players(
    players: dict[int, _MutablePlayerAccumulator],
) -> dict[int, PlayerHistorySnapshot]:
    return {
        player_id: PlayerHistorySnapshot(
            player_id=player.player_id,
            all_observations=tuple(player.all_observations),
            surface_observations={
                surface: tuple(observations)
                for surface, observations in player.surface_observations.items()
            },
        )
        for player_id, player in players.items()
    }


def _snapshot_pairs(
    pairs: dict[tuple[int, int], _MutablePairAccumulator],
) -> dict[tuple[int, int], PairHistorySnapshot]:
    return {
        pair_key: PairHistorySnapshot(
            player_low_id=pair.player_low_id,
            player_high_id=pair.player_high_id,
            all_observations=tuple(pair.all_observations),
            surface_observations={
                surface: tuple(observations)
                for surface, observations in pair.surface_observations.items()
            },
        )
        for pair_key, pair in pairs.items()
    }


def snapshot_history_state(
    players: dict[int, _MutablePlayerAccumulator],
    pairs: dict[tuple[int, int], _MutablePairAccumulator],
    adjacency: dict[int, set[int]],
) -> Agent3HistorySnapshot:
    return Agent3HistorySnapshot(
        players=_snapshot_players(players),
        pairs=_snapshot_pairs(pairs),
        adjacency={player_id: tuple(sorted(neighbors)) for player_id, neighbors in adjacency.items()},
    )


def _outcome_for_player(observation: Agent3HistoricalMatch, player_id: int) -> int:
    if observation.winner_side not in (1, 2):
        raise ValueError("COMPLETED Agent 3 history rows require winner_side 1 or 2")
    winner_id = observation.player_1_id if observation.winner_side == 1 else observation.player_2_id
    return 1 if player_id == winner_id else -1


def prepare_historical_match_update(
    observation: Agent3HistoricalMatch,
    snapshot: Agent3HistorySnapshot,
    *,
    config: Agent3Config = DEFAULT_CONFIG,
) -> _PreparedMatchUpdate | None:
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
    baseline_low_all = summarize_player_bucket(
        snapshot,
        canonical_pair[0],
        surface=None,
        as_of=baseline_as_of,
        config=config,
    ).baseline
    baseline_high_all = summarize_player_bucket(
        snapshot,
        canonical_pair[1],
        surface=None,
        as_of=baseline_as_of,
        config=config,
    ).baseline
    all_residual = ResidualObservation(
        observed_at=observed_at,
        residual=residual_for_match(
            baseline_low_all,
            baseline_high_all,
            outcome=low_outcome,
            config=config,
        ),
    )

    surface_player_1_outcome: tuple[Surface, OutcomeObservation] | None = None
    surface_player_2_outcome: tuple[Surface, OutcomeObservation] | None = None
    surface_residual: tuple[Surface, ResidualObservation] | None = None
    if observation.surface is not None:
        baseline_low_surface = summarize_player_bucket(
            snapshot,
            canonical_pair[0],
            surface=observation.surface,
            as_of=baseline_as_of,
            config=config,
        ).baseline
        baseline_high_surface = summarize_player_bucket(
            snapshot,
            canonical_pair[1],
            surface=observation.surface,
            as_of=baseline_as_of,
            config=config,
        ).baseline
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


def _apply_prepared_update(
    players: dict[int, _MutablePlayerAccumulator],
    pairs: dict[tuple[int, int], _MutablePairAccumulator],
    adjacency: dict[int, set[int]],
    update: _PreparedMatchUpdate,
) -> None:
    player_1 = _mutable_player(players, update.player_1_id)
    player_2 = _mutable_player(players, update.player_2_id)
    player_1.all_observations.append(update.player_1_outcome)
    player_2.all_observations.append(update.player_2_outcome)
    if update.surface_player_1_outcome is not None:
        surface, outcome = update.surface_player_1_outcome
        player_1.surface_observations.setdefault(surface, deque()).append(outcome)
    if update.surface_player_2_outcome is not None:
        surface, outcome = update.surface_player_2_outcome
        player_2.surface_observations.setdefault(surface, deque()).append(outcome)

    pair = _mutable_pair(pairs, update.canonical_pair)
    pair.all_observations.append(update.all_residual)
    if update.surface_residual is not None:
        surface, residual = update.surface_residual
        pair.surface_observations.setdefault(surface, deque()).append(residual)
    adjacency.setdefault(update.canonical_pair[0], set()).add(update.canonical_pair[1])
    adjacency.setdefault(update.canonical_pair[1], set()).add(update.canonical_pair[0])


def _expiration_threshold(cutoff: datetime, config: Agent3Config) -> datetime:
    return cutoff - timedelta(days=config.lookback_days)


def _expire_deque(observations: deque[OutcomeObservation] | deque[ResidualObservation], threshold: datetime) -> None:
    while observations and observations[0].observed_at < threshold:
        observations.popleft()


def expire_history_state(
    players: dict[int, _MutablePlayerAccumulator],
    pairs: dict[tuple[int, int], _MutablePairAccumulator],
    adjacency: dict[int, set[int]],
    *,
    cutoff: datetime,
    config: Agent3Config = DEFAULT_CONFIG,
) -> None:
    threshold = _expiration_threshold(cutoff, config)

    player_ids = sorted(players)
    for player_id in player_ids:
        player = players[player_id]
        _expire_deque(player.all_observations, threshold)
        empty_surfaces = []
        for surface, observations in player.surface_observations.items():
            _expire_deque(observations, threshold)
            if not observations:
                empty_surfaces.append(surface)
        for surface in empty_surfaces:
            del player.surface_observations[surface]
        if not player.all_observations and not player.surface_observations:
            del players[player_id]

    pair_keys = sorted(pairs)
    for pair_key in pair_keys:
        pair = pairs[pair_key]
        _expire_deque(pair.all_observations, threshold)
        empty_surfaces = []
        for surface, observations in pair.surface_observations.items():
            _expire_deque(observations, threshold)
            if not observations:
                empty_surfaces.append(surface)
        for surface in empty_surfaces:
            del pair.surface_observations[surface]
        if pair.all_observations or pair.surface_observations:
            continue
        del pairs[pair_key]
        low_id, high_id = pair_key
        if low_id in adjacency:
            adjacency[low_id].discard(high_id)
            if not adjacency[low_id]:
                del adjacency[low_id]
        if high_id in adjacency:
            adjacency[high_id].discard(low_id)
            if not adjacency[high_id]:
                del adjacency[high_id]


def build_prediction_request_from_snapshot(
    observation: Agent3HistoricalMatch,
    snapshot: Agent3HistorySnapshot,
    *,
    prediction_as_of: datetime | None = None,
) -> PredictionRequest:
    return PredictionRequest(
        player_1_id=observation.player_1_id,
        player_2_id=observation.player_2_id,
        surface=observation.surface,
        history_snapshot=snapshot,
        as_of=prediction_as_of or observation.visibility_cohort,
    )


def _group_observations(
    observations: list[Agent3HistoricalMatch] | tuple[Agent3HistoricalMatch, ...],
) -> dict[datetime, list[Agent3HistoricalMatch]]:
    grouped: dict[datetime, list[Agent3HistoricalMatch]] = {}
    for observation in sorted(observations, key=_history_sort_key):
        grouped.setdefault(observation.visibility_cohort, []).append(observation)
    return grouped


def _replay_updates(
    observations: list[Agent3HistoricalMatch] | tuple[Agent3HistoricalMatch, ...],
    *,
    cutoff: datetime | None = None,
    include_predictions: bool,
    config: Agent3Config = DEFAULT_CONFIG,
    prediction_filter=None,
) -> HistoricalReplayResult:
    grouped = _group_observations(observations)
    players: dict[int, _MutablePlayerAccumulator] = {}
    pairs: dict[tuple[int, int], _MutablePairAccumulator] = {}
    adjacency: dict[int, set[int]] = {}
    records: list[HistoricalPredictionRecord] = []

    for visibility_cohort in sorted(grouped):
        if cutoff is not None and visibility_cohort >= cutoff:
            continue
        cohort = sorted(grouped[visibility_cohort], key=_cohort_sort_key)
        expire_history_state(players, pairs, adjacency, cutoff=visibility_cohort, config=config)
        frozen_snapshot = snapshot_history_state(players, pairs, adjacency)
        if include_predictions:
            to_predict = (
                cohort
                if prediction_filter is None
                else [item for item in cohort if prediction_filter(item)]
            )
            for observation in to_predict:
                request = build_prediction_request_from_snapshot(
                    observation,
                    frozen_snapshot,
                    prediction_as_of=visibility_cohort,
                )
                records.append(
                    HistoricalPredictionRecord(
                        observation=observation,
                        request=request,
                        result=predict(request, config),
                    )
                )
        prepared_updates = [
            update
            for update in (
                prepare_historical_match_update(observation, frozen_snapshot, config=config)
                for observation in cohort
            )
            if update is not None
        ]
        for update in prepared_updates:
            _apply_prepared_update(players, pairs, adjacency, update)

    if cutoff is not None:
        expire_history_state(players, pairs, adjacency, cutoff=cutoff, config=config)
    return HistoricalReplayResult(
        records=tuple(records),
        final_snapshot=snapshot_history_state(players, pairs, adjacency),
    )


def build_history_snapshot(
    observations: list[Agent3HistoricalMatch] | tuple[Agent3HistoricalMatch, ...],
    *,
    cutoff: datetime,
    config: Agent3Config = DEFAULT_CONFIG,
) -> Agent3HistorySnapshot:
    return _replay_updates(
        observations,
        cutoff=cutoff,
        include_predictions=False,
        config=config,
    ).final_snapshot


def replay_historical_predictions(
    observations: list[Agent3HistoricalMatch] | tuple[Agent3HistoricalMatch, ...],
    *,
    config: Agent3Config = DEFAULT_CONFIG,
    prediction_filter=None,
) -> HistoricalReplayResult:
    return _replay_updates(
        observations,
        include_predictions=True,
        config=config,
        prediction_filter=prediction_filter,
    )
