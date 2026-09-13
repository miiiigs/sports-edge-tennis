from __future__ import annotations

from datetime import datetime, timedelta
from time import perf_counter

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    PredictionRequest as Agent1PredictionRequest,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as AGENT2_DEFAULT_CONFIG,
    PlayerServeReturnState,
    PopulationPriorState,
    PredictionRequest as Agent2PredictionRequest,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG as AGENT3_DEFAULT_CONFIG,
    PredictionRequest as Agent3PredictionRequest,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.agent2_history import (
    build_history_snapshot as build_agent2_snapshot,
    load_agent2_history,
    resolve_scoring_rule,
)
from sports_edge_tennis.data.agent3_history import load_agent3_history
from sports_edge_tennis.runner.agent2_live_snapshot import advance_agent2_history_snapshot
from sports_edge_tennis.runner.agent3_live_snapshot import (
    advance_live_agent3_history_snapshot,
    build_live_agent3_history_snapshot,
)
from sports_edge_tennis.runner.contracts import ResolvedTargetV1
from sports_edge_tennis.runner.live_model_cache import (
    AGENT2_CACHE_KEY,
    AGENT3_CACHE_KEY,
    checkpoint_requires_rebuild,
    decode_agent2_snapshot,
    decode_agent3_snapshot,
    encode_agent2_snapshot,
    encode_agent3_snapshot,
    load_checkpoint,
    save_checkpoint,
)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _has_visible_result_delta(
    conn: psycopg.Connection,
    *,
    previous_cutoff: datetime,
    cutoff: datetime,
) -> bool:
    """Return whether a result can enter an incremental model window.

    Agent 2 and Agent 3 both define result visibility as the later of
    ``source_observed_at`` and ``as_of``. Their existing incremental loaders then
    discard every row whose visibility cohort predates the previous checkpoint.
    Checking that condition first lets an empty hour avoid expensive history joins
    while preserving the exact loader/advance path whenever any row could qualify.
    """
    if previous_cutoff >= cutoff:
        return False
    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM match_result_observations mro
            WHERE mro.source_observed_at <= %s
              AND mro.as_of <= %s
              AND (
                    mro.source_observed_at >= %s
                 OR mro.as_of >= %s
              )
        )
        """,
        (cutoff, cutoff, previous_cutoff, previous_cutoff),
    ).fetchone()
    return bool(row and row[0])


def build_agent1_request(target: ResolvedTargetV1, as_of: datetime) -> Agent1PredictionRequest:
    return Agent1PredictionRequest(
        player_1_id=target.player_1_id,
        player_2_id=target.player_2_id,
        surface=target.surface,
        tour_scope=target.tour_scope,
        competition_tier=target.competition_tier,
        best_of_sets=target.best_of_sets,
        qualifying_flag=target.qualifying_flag,
        as_of=as_of,
    )


def load_agent1_history_observations(conn: psycopg.Connection, cutoff: datetime):
    started = perf_counter()
    print("AGENT1_HISTORY_LOAD_START", flush=True)
    observations = load_agent1_history(conn, cutoff=cutoff)
    print(
        f"AGENT1_HISTORY_LOAD_READY observations={len(observations)} elapsed_ms={_elapsed_ms(started)}",
        flush=True,
    )
    return observations


def _agent2_full_snapshot(conn: psycopg.Connection, cutoff: datetime):
    load_started = perf_counter()
    print("AGENT2_HISTORY_LOAD_START mode=FULL", flush=True)
    observations = load_agent2_history(conn, cutoff=cutoff)
    print(
        "AGENT2_HISTORY_LOAD_READY "
        f"mode=FULL observations={len(observations)} elapsed_ms={_elapsed_ms(load_started)}",
        flush=True,
    )
    snapshot_started = perf_counter()
    snapshot = build_agent2_snapshot(observations, cutoff=cutoff)
    print(
        f"AGENT2_SNAPSHOT_READY mode=FULL players={len(snapshot.players)} elapsed_ms={_elapsed_ms(snapshot_started)}",
        flush=True,
    )
    return snapshot, len(observations)


def build_agent2_history_snapshot(conn: psycopg.Connection, cutoff: datetime):
    checkpoint = load_checkpoint(
        conn,
        checkpoint_key=AGENT2_CACHE_KEY,
        model_version=AGENT2_DEFAULT_CONFIG.model_version,
        feature_version=AGENT2_DEFAULT_CONFIG.feature_version,
    )
    snapshot = None
    observation_count = 0
    if checkpoint is not None and checkpoint.checkpoint_cutoff < cutoff:
        rebuild_reason = checkpoint_requires_rebuild(
            conn,
            checkpoint=checkpoint,
            current_cutoff=cutoff,
            include_match_stats=True,
        )
        if rebuild_reason is None:
            try:
                snapshot = decode_agent2_snapshot(checkpoint.payload)
                delta_started = perf_counter()
                if _has_visible_result_delta(
                    conn,
                    previous_cutoff=checkpoint.checkpoint_cutoff,
                    cutoff=cutoff,
                ):
                    delta_start = (checkpoint.checkpoint_cutoff - timedelta(days=1)).date()
                    observations = load_agent2_history(
                        conn,
                        cutoff=cutoff,
                        date_start=delta_start,
                    )
                    delta = [
                        item
                        for item in observations
                        if item.visibility_cohort >= checkpoint.checkpoint_cutoff
                    ]
                    delta_source = "QUERY"
                else:
                    delta = []
                    delta_source = "NO_VISIBLE_DELTA"
                print(
                    "AGENT2_HISTORY_LOAD_READY "
                    f"mode=INCREMENTAL observations={len(delta)} elapsed_ms={_elapsed_ms(delta_started)} "
                    f"source={delta_source}",
                    flush=True,
                )
                snapshot = advance_agent2_history_snapshot(snapshot, delta, cutoff=cutoff)
                observation_count = checkpoint.observation_count + len(delta)
                print(
                    "MODEL_CACHE_HIT "
                    f"key={AGENT2_CACHE_KEY} previous_cutoff={checkpoint.checkpoint_cutoff.isoformat()} "
                    f"delta={len(delta)}",
                    flush=True,
                )
            except (ValueError, TypeError, KeyError) as exc:
                print(
                    f"MODEL_CACHE_INVALID key={AGENT2_CACHE_KEY} reason=DECODE_OR_ADVANCE:{type(exc).__name__}",
                    flush=True,
                )
                snapshot = None
        else:
            print(
                f"MODEL_CACHE_INVALID key={AGENT2_CACHE_KEY} reason={rebuild_reason}",
                flush=True,
            )
    elif checkpoint is None:
        print(f"MODEL_CACHE_MISS key={AGENT2_CACHE_KEY}", flush=True)

    if snapshot is None:
        snapshot, observation_count = _agent2_full_snapshot(conn, cutoff)

    save_checkpoint(
        conn,
        checkpoint_key=AGENT2_CACHE_KEY,
        model_version=AGENT2_DEFAULT_CONFIG.model_version,
        feature_version=AGENT2_DEFAULT_CONFIG.feature_version,
        checkpoint_cutoff=cutoff,
        observation_count=observation_count,
        payload=encode_agent2_snapshot(snapshot),
    )
    return snapshot


def build_agent2_request(
    target: ResolvedTargetV1,
    snapshot,
    as_of: datetime,
) -> Agent2PredictionRequest:
    resolver = resolve_scoring_rule(
        canonical_tournament_name=target.canonical_tournament_name,
        normalized_tournament_alias=target.normalized_tournament_alias,
        season_year=target.season_year,
        tour_scope=target.tour_scope,
        competition_tier=target.competition_tier,
        round_code=target.round_code,
        best_of_sets=target.best_of_sets,
        qualifying_flag=target.qualifying_flag,
    )
    return Agent2PredictionRequest(
        player_1_id=target.player_1_id,
        player_2_id=target.player_2_id,
        surface=target.surface,
        tour_scope=target.tour_scope,
        competition_tier=target.competition_tier,
        best_of_sets=target.best_of_sets,
        scoring_rule_version=resolver.scoring_rule_version,
        player_1_state=_player_state(snapshot, target.player_1_id, target.surface),
        player_2_state=_player_state(snapshot, target.player_2_id, target.surface),
        population_prior_state=_population_prior(snapshot, target.surface, target.tour_scope),
        as_of=as_of,
        resolver_abstain_reason=resolver.abstain_reason,
    )


def _agent3_bootstrap_snapshot(conn: psycopg.Connection, cutoff: datetime):
    # A final Agent 3 graph retains 1,460 days. Residuals for the oldest retained
    # match can depend on at most another 1,460 days of prior history, so a bounded
    # two-lookback bootstrap is mathematically sufficient and avoids replaying the
    # full historical warehouse.
    bootstrap_days = (AGENT3_DEFAULT_CONFIG.lookback_days * 2) + 2
    bootstrap_start = (cutoff - timedelta(days=bootstrap_days)).date()
    load_started = perf_counter()
    print(
        f"AGENT3_HISTORY_LOAD_START mode=BOOTSTRAP date_start={bootstrap_start.isoformat()}",
        flush=True,
    )
    observations = load_agent3_history(
        conn,
        cutoff=cutoff,
        date_start=bootstrap_start,
    )
    print(
        "AGENT3_HISTORY_LOAD_READY "
        f"mode=BOOTSTRAP observations={len(observations)} elapsed_ms={_elapsed_ms(load_started)}",
        flush=True,
    )
    return build_live_agent3_history_snapshot(observations, cutoff=cutoff), len(observations)


def build_agent3_history_snapshot(conn: psycopg.Connection, cutoff: datetime):
    checkpoint = load_checkpoint(
        conn,
        checkpoint_key=AGENT3_CACHE_KEY,
        model_version=AGENT3_DEFAULT_CONFIG.model_version,
        feature_version=AGENT3_DEFAULT_CONFIG.feature_version,
    )
    snapshot = None
    observation_count = 0
    if checkpoint is not None and checkpoint.checkpoint_cutoff < cutoff:
        rebuild_reason = checkpoint_requires_rebuild(
            conn,
            checkpoint=checkpoint,
            current_cutoff=cutoff,
            include_match_stats=False,
        )
        if rebuild_reason is None:
            try:
                snapshot = decode_agent3_snapshot(checkpoint.payload)
                delta_started = perf_counter()
                if _has_visible_result_delta(
                    conn,
                    previous_cutoff=checkpoint.checkpoint_cutoff,
                    cutoff=cutoff,
                ):
                    delta_start = (checkpoint.checkpoint_cutoff - timedelta(days=1)).date()
                    observations = load_agent3_history(
                        conn,
                        cutoff=cutoff,
                        date_start=delta_start,
                    )
                    delta = [
                        item
                        for item in observations
                        if item.visibility_cohort >= checkpoint.checkpoint_cutoff
                    ]
                    delta_source = "QUERY"
                else:
                    delta = []
                    delta_source = "NO_VISIBLE_DELTA"
                print(
                    "AGENT3_HISTORY_LOAD_READY "
                    f"mode=INCREMENTAL observations={len(delta)} elapsed_ms={_elapsed_ms(delta_started)} "
                    f"source={delta_source}",
                    flush=True,
                )
                snapshot = advance_live_agent3_history_snapshot(
                    snapshot,
                    delta,
                    previous_cutoff=checkpoint.checkpoint_cutoff,
                    cutoff=cutoff,
                )
                observation_count = checkpoint.observation_count + len(delta)
                print(
                    "MODEL_CACHE_HIT "
                    f"key={AGENT3_CACHE_KEY} previous_cutoff={checkpoint.checkpoint_cutoff.isoformat()} "
                    f"delta={len(delta)}",
                    flush=True,
                )
            except (ValueError, TypeError, KeyError) as exc:
                print(
                    f"MODEL_CACHE_INVALID key={AGENT3_CACHE_KEY} reason=DECODE_OR_ADVANCE:{type(exc).__name__}",
                    flush=True,
                )
                snapshot = None
        else:
            print(
                f"MODEL_CACHE_INVALID key={AGENT3_CACHE_KEY} reason={rebuild_reason}",
                flush=True,
            )
    elif checkpoint is None:
        print(f"MODEL_CACHE_MISS key={AGENT3_CACHE_KEY}", flush=True)

    if snapshot is None:
        snapshot, observation_count = _agent3_bootstrap_snapshot(conn, cutoff)

    save_checkpoint(
        conn,
        checkpoint_key=AGENT3_CACHE_KEY,
        model_version=AGENT3_DEFAULT_CONFIG.model_version,
        feature_version=AGENT3_DEFAULT_CONFIG.feature_version,
        checkpoint_cutoff=cutoff,
        observation_count=observation_count,
        payload=encode_agent3_snapshot(snapshot),
    )
    return snapshot


def build_agent3_request(
    target: ResolvedTargetV1,
    snapshot,
    as_of: datetime,
) -> Agent3PredictionRequest:
    return Agent3PredictionRequest(
        player_1_id=target.player_1_id,
        player_2_id=target.player_2_id,
        surface=target.surface,
        history_snapshot=snapshot,
        as_of=as_of,
    )


def _player_state(snapshot, player_id: int, surface: str | None) -> PlayerServeReturnState:
    player = snapshot.players.get(player_id)
    if player is None:
        return PlayerServeReturnState(
            player_id=player_id,
            prior_usable_matches=0,
            overall_serve_points_won=0,
            overall_serve_points_played=0,
            overall_return_points_won=0,
            overall_return_points_played=0,
            surface_serve_points_won=0,
            surface_serve_points_played=0,
            surface_return_points_won=0,
            surface_return_points_played=0,
        )
    surface_state = None if surface is None else player.surface_points.get(surface)
    if surface_state is None:
        surface_serve_points_won = 0
        surface_serve_points_played = 0
        surface_return_points_won = 0
        surface_return_points_played = 0
    else:
        surface_serve_points_won = surface_state.serve_points_won
        surface_serve_points_played = surface_state.serve_points_played
        surface_return_points_won = surface_state.return_points_won
        surface_return_points_played = surface_state.return_points_played
    return PlayerServeReturnState(
        player_id=player_id,
        prior_usable_matches=player.prior_usable_matches,
        overall_serve_points_won=player.overall_serve_points_won,
        overall_serve_points_played=player.overall_serve_points_played,
        overall_return_points_won=player.overall_return_points_won,
        overall_return_points_played=player.overall_return_points_played,
        surface_serve_points_won=surface_serve_points_won,
        surface_serve_points_played=surface_serve_points_played,
        surface_return_points_won=surface_return_points_won,
        surface_return_points_played=surface_return_points_played,
    )


def _population_prior(snapshot, surface: str | None, tour_scope: str) -> PopulationPriorState:
    population = snapshot.population
    if surface is None:
        return PopulationPriorState(
            surface_tour_service_points_won=0,
            surface_tour_service_points_played=0,
            surface_service_points_won=0,
            surface_service_points_played=0,
            global_service_points_won=population.global_service_points_won,
            global_service_points_played=population.global_service_points_played,
        )
    surface_tour = population.surface_tour_service_points.get((surface, tour_scope), (0, 0))
    surface_total = population.surface_service_points.get(surface, (0, 0))
    return PopulationPriorState(
        surface_tour_service_points_won=surface_tour[0],
        surface_tour_service_points_played=surface_tour[1],
        surface_service_points_won=surface_total[0],
        surface_service_points_played=surface_total[1],
        global_service_points_won=population.global_service_points_won,
        global_service_points_played=population.global_service_points_played,
    )
