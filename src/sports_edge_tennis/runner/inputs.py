from __future__ import annotations

from datetime import datetime
from time import perf_counter

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    PredictionRequest as Agent1PredictionRequest,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    PlayerServeReturnState,
    PopulationPriorState,
    PredictionRequest as Agent2PredictionRequest,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    PredictionRequest as Agent3PredictionRequest,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.agent2_history import (
    build_history_snapshot as build_agent2_snapshot,
    load_agent2_history,
    resolve_scoring_rule,
)
from sports_edge_tennis.data.agent3_history import load_agent3_history
from sports_edge_tennis.runner.agent3_live_snapshot import build_live_agent3_history_snapshot
from sports_edge_tennis.runner.contracts import ResolvedTargetV1


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


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


def build_agent2_history_snapshot(conn: psycopg.Connection, cutoff: datetime):
    load_started = perf_counter()
    print("AGENT2_HISTORY_LOAD_START", flush=True)
    observations = load_agent2_history(conn, cutoff=cutoff)
    print(
        f"AGENT2_HISTORY_LOAD_READY observations={len(observations)} elapsed_ms={_elapsed_ms(load_started)}",
        flush=True,
    )
    snapshot_started = perf_counter()
    snapshot = build_agent2_snapshot(observations, cutoff=cutoff)
    print(
        f"AGENT2_SNAPSHOT_READY players={len(snapshot.players)} elapsed_ms={_elapsed_ms(snapshot_started)}",
        flush=True,
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


def build_agent3_history_snapshot(conn: psycopg.Connection, cutoff: datetime):
    load_started = perf_counter()
    print("AGENT3_HISTORY_LOAD_START", flush=True)
    observations = load_agent3_history(conn, cutoff=cutoff)
    print(
        f"AGENT3_HISTORY_LOAD_READY observations={len(observations)} elapsed_ms={_elapsed_ms(load_started)}",
        flush=True,
    )
    return build_live_agent3_history_snapshot(observations, cutoff=cutoff)


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
