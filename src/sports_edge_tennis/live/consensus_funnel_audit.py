"""Read-only audit for Brian's proposed Tennis winner-selection funnel.

This module never changes production Manager policy, decisions, paper executions,
or settlement truth. It measures the existing three-agent prediction stream under
an odds-independent interpretation:

- 3/3 same side -> premium selection candidate
- 2/3 majority -> shadow comparison
- one active agent -> shadow comparison
- no directional majority / no active agents -> no selection

Repeated prediction runs are retained as raw audit snapshots, while performance
is scored at one independent position per match and bucket using the first
qualifying snapshot. This prevents hourly reruns from inflating the sample.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from sports_edge_tennis.data.prediction_persistence import (
    PersistedPredictiveAgentPrediction,
    load_finalized_predictive_agent_predictions,
)
from sports_edge_tennis.manager.strict_consensus import (
    NO_ACTIVE_AGENTS,
    NO_DIRECTIONAL_MAJORITY,
    SHADOW_ONE_ACTIVE,
    SHADOW_TWO_OF_THREE,
    STRICT_THREE_OF_THREE,
    StrictConsensusClassification,
    classify_strict_consensus,
)

BUCKET_ORDER = (
    STRICT_THREE_OF_THREE,
    SHADOW_TWO_OF_THREE,
    SHADOW_ONE_ACTIVE,
    NO_DIRECTIONAL_MAJORITY,
    NO_ACTIVE_AGENTS,
)


@dataclass(frozen=True)
class SnapshotClassification:
    prediction_run_id: int
    run_as_of: datetime
    match_id: int
    classification: StrictConsensusClassification


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _log(message: str) -> None:
    print(message, flush=True)


def _group_by_match(
    predictions: list[PersistedPredictiveAgentPrediction],
) -> dict[int, list[PersistedPredictiveAgentPrediction]]:
    grouped: dict[int, list[PersistedPredictiveAgentPrediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[prediction.match_id].append(prediction)
    return grouped


def _latest_results(conn: psycopg.Connection, match_ids: list[int]) -> dict[int, tuple[str, int | None]]:
    if not match_ids:
        return {}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (match_id)
            match_id,
            outcome_status::text,
            winner_side
        FROM match_result_observations
        WHERE match_id = ANY(%s)
        ORDER BY match_id, as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        """,
        (match_ids,),
    ).fetchall()
    return {int(match_id): (str(status), winner_side) for match_id, status, winner_side in rows}


def _score_bucket(
    positions: list[SnapshotClassification],
    results: dict[int, tuple[str, int | None]],
) -> dict[str, int | Decimal | None]:
    wins = 0
    losses = 0
    pending = 0
    nonstandard = 0
    for position in positions:
        result = results.get(position.match_id)
        if result is None:
            pending += 1
            continue
        status, winner_side = result
        if status != "COMPLETED" or winner_side not in (1, 2):
            nonstandard += 1
            continue
        if position.classification.selected_side == winner_side:
            wins += 1
        else:
            losses += 1
    settled = wins + losses
    win_rate = None if settled == 0 else (Decimal(wins) / Decimal(settled))
    return {
        "positions": len(positions),
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "nonstandard": nonstandard,
        "win_rate": win_rate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Tennis strict-consensus funnel audit")
    parser.add_argument("--dsn", default=_dsn())
    parser.add_argument("--lookback-days", type=int, default=30)
    args = parser.parse_args(argv)

    if not args.dsn:
        _log("STRICT_FUNNEL_AUDIT_BLOCKED no_database_dsn")
        return 2
    if args.lookback_days < 1:
        _log("STRICT_FUNNEL_AUDIT_BLOCKED invalid_lookback_days")
        return 2

    cutoff = datetime.now(tz=UTC) - timedelta(days=args.lookback_days)

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        runs = conn.execute(
            """
            SELECT prediction_run_id, as_of
            FROM prediction_runs
            WHERE run_status = 'COMPLETED'
              AND prediction_finalized_at IS NOT NULL
              AND prediction_contract_version = 'predictive_agent_result_v1'
              AND as_of >= %s
            ORDER BY as_of, prediction_run_id
            """,
            (cutoff,),
        ).fetchall()

        if not runs:
            _log("STRICT_FUNNEL_AUDIT_BLOCKED no_completed_runs_in_window")
            return 2

        snapshots: list[SnapshotClassification] = []
        active_by_agent: Counter[str] = Counter()
        abstain_by_agent: Counter[str] = Counter()
        current_manager_consensus = 0
        total_targets = 0

        for prediction_run_id, run_as_of in runs:
            prediction_run_id = int(prediction_run_id)
            predictions = load_finalized_predictive_agent_predictions(
                conn,
                prediction_run_id=prediction_run_id,
            )
            grouped = _group_by_match(predictions)
            total_targets += len(grouped)

            for prediction in predictions:
                if prediction.prediction_status == "PREDICTED":
                    active_by_agent[prediction.agent_code] += 1
                else:
                    abstain_by_agent[prediction.agent_code] += 1

            for match_id, match_predictions in grouped.items():
                if len(match_predictions) != 3:
                    _log(
                        "STRICT_FUNNEL_AUDIT_WARNING incomplete_agent_matrix "
                        f"prediction_run_id={prediction_run_id} match_id={match_id} "
                        f"rows={len(match_predictions)}"
                    )
                    continue
                classification = classify_strict_consensus(match_predictions)
                snapshots.append(
                    SnapshotClassification(
                        prediction_run_id=prediction_run_id,
                        run_as_of=run_as_of,
                        match_id=match_id,
                        classification=classification,
                    )
                )

            manager_row = conn.execute(
                """
                SELECT count(*)
                FROM manager_consensus_results
                WHERE prediction_run_id = %s
                  AND manager_status = 'CONSENSUS'
                """,
                (prediction_run_id,),
            ).fetchone()
            current_manager_consensus += int(manager_row[0]) if manager_row else 0

        raw_bucket_counts = Counter(snapshot.classification.bucket for snapshot in snapshots)

        first_by_bucket_match: dict[tuple[str, int], SnapshotClassification] = {}
        for snapshot in snapshots:
            bucket = snapshot.classification.bucket
            if bucket not in {STRICT_THREE_OF_THREE, SHADOW_TWO_OF_THREE, SHADOW_ONE_ACTIVE}:
                continue
            key = (bucket, snapshot.match_id)
            first_by_bucket_match.setdefault(key, snapshot)

        unique_positions: dict[str, list[SnapshotClassification]] = {
            bucket: [] for bucket in (STRICT_THREE_OF_THREE, SHADOW_TWO_OF_THREE, SHADOW_ONE_ACTIVE)
        }
        for (bucket, _match_id), snapshot in first_by_bucket_match.items():
            unique_positions[bucket].append(snapshot)
        for positions in unique_positions.values():
            positions.sort(key=lambda item: (item.run_as_of, item.match_id))

        all_match_ids = sorted({snapshot.match_id for snapshot in snapshots})
        results = _latest_results(conn, all_match_ids)

        _log(
            "STRICT_FUNNEL_AUDIT_START "
            f"lookback_days={args.lookback_days} runs={len(runs)} "
            f"target_snapshots={total_targets} classified_snapshots={len(snapshots)} "
            f"unique_matches={len(all_match_ids)}"
        )

        for agent_code in sorted(set(active_by_agent) | set(abstain_by_agent)):
            active = active_by_agent[agent_code]
            abstain = abstain_by_agent[agent_code]
            total = active + abstain
            active_rate = Decimal(active) / Decimal(total) if total else Decimal("0")
            _log(
                "STRICT_FUNNEL_AGENT "
                f"agent={agent_code} active={active} abstained={abstain} "
                f"active_rate={active_rate:.4f}"
            )

        for bucket in BUCKET_ORDER:
            _log(f"STRICT_FUNNEL_RAW bucket={bucket} snapshots={raw_bucket_counts[bucket]}")

        for bucket in (STRICT_THREE_OF_THREE, SHADOW_TWO_OF_THREE, SHADOW_ONE_ACTIVE):
            score = _score_bucket(unique_positions[bucket], results)
            win_rate = score["win_rate"]
            rendered_rate = "NA" if win_rate is None else f"{win_rate:.4f}"
            _log(
                "STRICT_FUNNEL_POSITION_PERFORMANCE "
                f"bucket={bucket} positions={score['positions']} settled={score['settled']} "
                f"wins={score['wins']} losses={score['losses']} pending={score['pending']} "
                f"nonstandard={score['nonstandard']} win_rate={rendered_rate}"
            )

        strict_raw = raw_bucket_counts[STRICT_THREE_OF_THREE]
        _log(
            "STRICT_FUNNEL_MANAGER_COMPARISON "
            f"current_manager_consensus_snapshots={current_manager_consensus} "
            f"strict_3of3_snapshots={strict_raw} "
            f"difference={current_manager_consensus - strict_raw}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
