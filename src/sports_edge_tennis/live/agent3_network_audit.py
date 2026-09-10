"""Read-only production audit for Agent 3 matchup-network coverage.

Uses the persisted live Agent 3 checkpoint from the same prediction cutoff and
reports raw graph connectivity separately from threshold-qualified network
support. It performs SELECTs only and never changes model policy or canonical
data.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG,
    summarize_network_bucket,
    summarize_pair_residual_bucket,
)
from sports_edge_tennis.runner.live_model_cache import decode_agent3_snapshot
from sports_edge_tennis.runner.targets import resolve_target


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _log(message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Agent 3 production network audit")
    parser.add_argument("--dsn", default=_dsn())
    parser.add_argument("--prediction-run-id", type=int)
    args = parser.parse_args(argv)

    if not args.dsn:
        _log("A3_AUDIT_BLOCKED no_database_dsn")
        return 2

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        if args.prediction_run_id is None:
            run = conn.execute(
                """
                SELECT pr.prediction_run_id, pr.as_of
                FROM prediction_runs pr
                WHERE pr.run_status = 'COMPLETED'
                  AND EXISTS (
                      SELECT 1 FROM prediction_run_targets prt
                      WHERE prt.prediction_run_id = pr.prediction_run_id
                  )
                ORDER BY pr.prediction_run_id DESC
                LIMIT 1
                """
            ).fetchone()
        else:
            run = conn.execute(
                """
                SELECT prediction_run_id, as_of
                FROM prediction_runs
                WHERE prediction_run_id = %s AND run_status = 'COMPLETED'
                """,
                (args.prediction_run_id,),
            ).fetchone()
        if run is None:
            _log("A3_AUDIT_BLOCKED no_completed_prediction_run")
            return 2

        prediction_run_id, as_of = int(run[0]), run[1]
        checkpoint = conn.execute(
            """
            SELECT checkpoint_cutoff, payload, observation_count
            FROM live_model_state_checkpoints
            WHERE checkpoint_key = 'AGENT_3_INTRANSITIVITY'
              AND state_schema_version = 'live_model_state_v1'
              AND model_version = %s
              AND feature_version = %s
            """,
            (DEFAULT_CONFIG.model_version, DEFAULT_CONFIG.feature_version),
        ).fetchone()
        if checkpoint is None:
            _log("A3_AUDIT_BLOCKED no_agent3_checkpoint")
            return 2

        checkpoint_cutoff, payload, observation_count = checkpoint
        if checkpoint_cutoff != as_of:
            _log(
                "A3_AUDIT_BLOCKED checkpoint_cutoff_mismatch "
                f"prediction_run_id={prediction_run_id} run_as_of={as_of.isoformat()} "
                f"checkpoint_cutoff={checkpoint_cutoff.isoformat()}"
            )
            return 2

        snapshot = decode_agent3_snapshot(bytes(payload))
        match_ids = tuple(
            int(row[0])
            for row in conn.execute(
                """
                SELECT match_id
                FROM prediction_run_targets
                WHERE prediction_run_id = %s
                ORDER BY match_id
                """,
                (prediction_run_id,),
            ).fetchall()
        )

        _log(
            "A3_AUDIT_START "
            f"prediction_run_id={prediction_run_id} targets={len(match_ids)} "
            f"checkpoint_cutoff={checkpoint_cutoff.isoformat()} "
            f"checkpoint_observations={int(observation_count)} players={len(snapshot.players)} "
            f"pairs={len(snapshot.pairs)}"
        )

        raw_shared_total = 0
        eligible_shared_total = 0
        zero_raw_shared = 0
        raw_but_filtered = 0
        no_player_history = 0

        for match_id in match_ids:
            target = resolve_target(conn, match_id=match_id, as_of=as_of)
            p1, p2 = target.player_1_id, target.player_2_id
            n1 = set(snapshot.adjacency.get(p1, ())) - {p2}
            n2 = set(snapshot.adjacency.get(p2, ())) - {p1}
            raw_shared = len(n1 & n2)
            p1_hist = len(snapshot.players.get(p1).all_observations) if p1 in snapshot.players else 0
            p2_hist = len(snapshot.players.get(p2).all_observations) if p2 in snapshot.players else 0
            direct = summarize_pair_residual_bucket(
                snapshot, p1, p2, surface=None, as_of=as_of, config=DEFAULT_CONFIG
            )
            network = summarize_network_bucket(
                snapshot, p1, p2, surface=None, as_of=as_of, config=DEFAULT_CONFIG
            )
            surface_network = summarize_network_bucket(
                snapshot, p1, p2, surface=target.surface, as_of=as_of, config=DEFAULT_CONFIG
            )

            raw_shared_total += raw_shared
            eligible_shared_total += network.shared_opponent_count
            zero_raw_shared += int(raw_shared == 0)
            raw_but_filtered += int(raw_shared > 0 and network.shared_opponent_count == 0)
            no_player_history += int(p1_hist == 0 or p2_hist == 0)

            _log(
                "A3_NETWORK_AUDIT "
                f"match_id={match_id} p1={p1} p2={p2} "
                f"history={p1_hist}/{p2_hist} degree={len(n1)}/{len(n2)} "
                f"raw_shared={raw_shared} eligible_shared={network.shared_opponent_count} "
                f"q_all={network.q_total} surface_eligible={surface_network.shared_opponent_count} "
                f"q_surface={surface_network.q_total} direct_raw={direct.raw_count} "
                f"direct_effective={direct.effective_count}"
            )

        _log(
            "A3_AUDIT_SUMMARY "
            f"targets={len(match_ids)} zero_raw_shared={zero_raw_shared} "
            f"raw_but_all_filtered={raw_but_filtered} any_player_without_history={no_player_history} "
            f"raw_shared_total={raw_shared_total} eligible_shared_total={eligible_shared_total}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
