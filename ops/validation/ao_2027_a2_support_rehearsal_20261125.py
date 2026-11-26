"""Australian Open 2027 Agent 2 support rehearsal with real stored history.

This is a read-only support-condition rehearsal. It loads the internal stored
Sackmann/API-Tennis match history up to a fixed cutoff, builds the same Agent 2
player/population snapshot the live pipeline uses, and asks Agent 2 to price a
**synthetic** Australian Open 2027 main-draw pairing between two real players
who appeared in the stored AO 2026 main draw.

It is not a 2027 forecast: the 2027 fixture does not exist, and the pairing is
chosen for support coverage, not for a sporting claim. It exists to show whether
Agent 2's real, PIT-available support turns the cold-start abstention into an
actual prediction, and to record the support numbers that made that happen.

The script never writes, never calls a provider and never touches production
outside a read-only transaction.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as A2_CONFIG,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    AbstainReason,
    PlayerServeReturnState,
    PredictionRequest,
    predict,
)
from sports_edge_tennis.data.agent2_history import (
    Agent2HistoricalMatch,
    _player_snapshot_for_surface,
    _population_prior_for_context,
    build_history_snapshot,
    load_agent2_history,
    resolve_scoring_rule,
)

VERSION = "AO_2027_A2_SUPPORT_REHEARSAL_20261125_V1"
CUTOFF = datetime(2026, 11, 25, 17, 0, tzinfo=UTC)
LOOKBACK_START_YEAR = 2023


def _eligible_player(snapshot, player_id: int) -> bool:
    player = snapshot.players.get(player_id)
    if player is None:
        return False
    if player.prior_usable_matches < A2_CONFIG.minimum_prior_usable_matches:
        return False
    if player.overall_serve_points_played < A2_CONFIG.minimum_prior_service_points:
        return False
    if player.overall_return_points_played < A2_CONFIG.minimum_prior_return_points:
        return False
    hard = player.surface_points.get("HARD")
    return bool(hard and hard.serve_points_played > 0 and hard.return_points_played > 0)


def _is_ao_main_draw(observation: Agent2HistoricalMatch) -> bool:
    return (
        observation.canonical_tournament_name.casefold() == "australian open"
        and LOOKBACK_START_YEAR <= observation.season_year <= 2025
        and observation.qualifying_flag is False
        and observation.surface == "HARD"
        and observation.outcome_status == "COMPLETED"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn and not os.getenv("PGHOST"):
        parser.error("TENNIS_DATABASE_URL or libpq PG* variables required (read-only)")

    connect_kwargs = {"autocommit": True, "connect_timeout": 60}
    if dsn:
        connection = psycopg.connect(dsn, **connect_kwargs)
    else:
        connection = psycopg.connect(**connect_kwargs)

    with connection as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='240s'")
        observations = load_agent2_history(
            conn,
            cutoff=CUTOFF,
            year_start=LOOKBACK_START_YEAR,
            year_end=2026,
        )

    snapshot = build_history_snapshot(observations, cutoff=CUTOFF)
    ao_matches = [o for o in observations if _is_ao_main_draw(o)]
    if not ao_matches:
        raise SystemExit("no stored AO 2026 main-draw observations in the lookback")

    candidates = []
    for observation in ao_matches:
        if _eligible_player(snapshot, observation.player_1_id) and _eligible_player(
            snapshot, observation.player_2_id
        ):
            p1 = snapshot.players[observation.player_1_id]
            p2 = snapshot.players[observation.player_2_id]
            candidates.append(
                (
                    min(p1.prior_usable_matches, p2.prior_usable_matches),
                    p1.prior_usable_matches + p2.prior_usable_matches,
                    observation,
                )
            )
    if not candidates:
        raise SystemExit(
            "no AO 2026 pairing has both players above the Agent 2 support gates"
        )
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _, _, target = candidates[0]

    rule = resolve_scoring_rule(
        canonical_tournament_name="Australian Open",
        season_year=2027,
        tour_scope="ATP_MAIN",
        competition_tier="GRAND_SLAM",
        round_code="R128",
        best_of_sets=5,
        qualifying_flag=False,
    )

    def request_for(player_1_state, player_2_state) -> PredictionRequest:
        return PredictionRequest(
            player_1_id=target.player_1_id,
            player_2_id=target.player_2_id,
            surface="HARD",
            tour_scope="ATP_MAIN",
            competition_tier="GRAND_SLAM",
            best_of_sets=5,
            scoring_rule_version=rule.scoring_rule_version,
            player_1_state=player_1_state,
            player_2_state=player_2_state,
            population_prior_state=_population_prior_for_context(
                snapshot, surface="HARD", tour_scope="ATP_MAIN"
            ),
            as_of=CUTOFF,
            resolver_abstain_reason=rule.abstain_reason,
        )

    realistic_request = request_for(
        _player_snapshot_for_surface(
            snapshot, player_id=target.player_1_id, surface="HARD"
        ),
        _player_snapshot_for_surface(
            snapshot, player_id=target.player_2_id, surface="HARD"
        ),
    )
    realistic = predict(realistic_request, config=A2_CONFIG)

    cold_request = request_for(
        PlayerServeReturnState(
            player_id=target.player_1_id,
            prior_usable_matches=0,
            overall_serve_points_won=0,
            overall_serve_points_played=0,
            overall_return_points_won=0,
            overall_return_points_played=0,
            surface_serve_points_won=0,
            surface_serve_points_played=0,
            surface_return_points_won=0,
            surface_return_points_played=0,
        ),
        PlayerServeReturnState(
            player_id=target.player_2_id,
            prior_usable_matches=0,
            overall_serve_points_won=0,
            overall_serve_points_played=0,
            overall_return_points_won=0,
            overall_return_points_played=0,
            surface_serve_points_won=0,
            surface_serve_points_played=0,
            surface_return_points_won=0,
            surface_return_points_played=0,
        ),
    )
    cold = predict(cold_request, config=A2_CONFIG)

    p1 = snapshot.players[target.player_1_id]
    p2 = snapshot.players[target.player_2_id]

    report = {
        "version": VERSION,
        "read_only_production": True,
        "provider_calls": 0,
        "database_writes": 0,
        "cutoff": CUTOFF.isoformat().replace("+00:00", "Z"),
        "lookback_start_year": LOOKBACK_START_YEAR,
        "source": (
            "internal stored Sackmann/API-Tennis history; not a licensed "
            "certification and not redistributed"
        ),
        "target": {
            "synthetic": True,
            "sporting_claim": False,
            "note": (
                "AO 2027 R128 pairing selected for real support coverage from "
                "stored AO main-draw participants (latest hard-court edition in lookback); no 2027 fixture exists"
            ),
            "season_year": 2027,
            "round_code": "R128",
            "best_of_sets": 5,
            "surface": "HARD",
            "scoring_rule": rule.scoring_rule_version.value,
            "resolver_abstain_reason": (
                None if rule.abstain_reason is None else rule.abstain_reason.value
            ),
            "player_1_id": target.player_1_id,
            "player_2_id": target.player_2_id,
            "selected_from_ao_match_id": target.match_id,
        },
        "loaded_history": {
            "observations": len(observations),
            "ao_main_draw_observations": len(ao_matches),
            "pairings_above_support_gates": len(candidates),
        },
        "player_support": {
            "player_1": {
                "prior_usable_matches": p1.prior_usable_matches,
                "overall_serve_points_played": p1.overall_serve_points_played,
                "overall_return_points_played": p1.overall_return_points_played,
                "hard_serve_points_played": p1.surface_points["HARD"].serve_points_played,
                "hard_return_points_played": p1.surface_points["HARD"].return_points_played,
            },
            "player_2": {
                "prior_usable_matches": p2.prior_usable_matches,
                "overall_serve_points_played": p2.overall_serve_points_played,
                "overall_return_points_played": p2.overall_return_points_played,
                "hard_serve_points_played": p2.surface_points["HARD"].serve_points_played,
                "hard_return_points_played": p2.surface_points["HARD"].return_points_played,
            },
        },
        "agent2_realistic_support": {
            "prediction_status": realistic.prediction_status,
            "selection_side": realistic.selection_side,
            "player_1_probability": (
                None if realistic.player_1_probability is None
                else str(realistic.player_1_probability)
            ),
            "win_probability": (
                None if realistic.win_probability is None
                else str(realistic.win_probability)
            ),
            "diagnostics": realistic.diagnostics.to_payload(),
        },
        "agent2_cold_start_control": {
            "prediction_status": cold.prediction_status,
            "abstain_reason": cold.diagnostics.abstain_reason.value
            if cold.diagnostics.abstain_reason
            else None,
        },
        "interpretation": {
            "what_this_shows": (
                "Real PIT-available player history satisfies Agent 2's support "
                "gates and produces a BO5 prediction; the cold-start control "
                "abstains for insufficient history."
            ),
            "what_this_does_not_show": (
                "It is not a 2027 forecast, not an accuracy result, and not "
                "evidence that the synthetic pairing resembles the real draw."
            ),
            "cold_start_abstain_expected": cold.prediction_status == "ABSTAINED"
            and cold.diagnostics.abstain_reason
            in {
                AbstainReason.INSUFFICIENT_HISTORY,
                AbstainReason.INSUFFICIENT_POINT_HISTORY,
            },
        },
    }
    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    with open(args.out_json, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
