from __future__ import annotations

"""Combined read-only retrospective replay for Agent 1 / Agent 2 / Agent 3 v1
and an in-memory Manager v1 consensus reconstruction.

The entire database stage is one read-only transaction that is rolled back and
disconnected before any model work. Nothing is persisted. Output is labelled
RETROSPECTIVE_RESEARCH (in-memory chronology reordering by match start) or
STRICT_POINT_IN_TIME.
"""

import argparse
import json
import os
import time
from collections import Counter
from datetime import date, datetime, timezone

import psycopg

from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.agent2_history import (
    load_agent2_history,
    replay_historical_predictions as replay_agent2,
)
from sports_edge_tennis.data.agent3_history import (
    load_agent3_history,
    replay_historical_predictions as replay_agent3,
)
from sports_edge_tennis.manager.contracts import ManagerPolicyV1
from sports_edge_tennis.validation.agent1_historical_replay import (
    _replay as replay_agent1,
)
from sports_edge_tennis.validation.agent1_historical_replay import _score as score_agent1
from sports_edge_tennis.validation.agent2_historical_replay import (
    _as_retrospective as retro_agent2,
)
from sports_edge_tennis.validation.agent2_historical_replay import _score as score_agent2
from sports_edge_tennis.validation.agent3_historical_replay import (
    _as_retrospective as retro_agent3,
)
from sports_edge_tennis.validation.agent3_historical_replay import _score as score_agent3
from sports_edge_tennis.validation.manager_v1_reconstruction import reconstruct_manager_v1

MODE_STRICT = "strict"
MODE_RETROSPECTIVE = "retrospective"


def _load_all_read_only(dsn, *, cutoff, year_start):
    started = time.monotonic()
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        if read_only != "on":
            raise RuntimeError("transaction_read_only is not on")
        agent1 = load_agent1_history(
            conn, cutoff=cutoff, tour_scopes=("ATP_MAIN", "CHALLENGER"), year_start=year_start
        )
        agent2 = load_agent2_history(
            conn, cutoff=cutoff, tour_scopes=("ATP_MAIN", "CHALLENGER"), year_start=year_start
        )
        agent3 = load_agent3_history(
            conn, cutoff=cutoff, tour_scopes=("ATP_MAIN", "CHALLENGER"), year_start=year_start
        )
        policy_row = conn.execute(
            """
            SELECT manager_policy_version_id, policy_key, policy_version,
                   three_agent_max_probability_spread,
                   agent3_abstention_max_probability_spread,
                   min_active_agent_probability, allow_agent3_abstention
            FROM manager_policy_versions
            WHERE policy_version = 'v1'
            ORDER BY manager_policy_version_id DESC
            LIMIT 1
            """
        ).fetchone()
        conn.rollback()
    policy = None if policy_row is None else ManagerPolicyV1(*policy_row)
    return agent1, agent2, agent3, policy, read_only, time.monotonic() - started


def _window(observation, start, end) -> bool:
    match_start = getattr(observation, "match_start_at", None) or getattr(
        observation, "result_source_observed_at", None
    )
    if match_start is None:
        return False
    match_date = match_start.date()
    return start <= match_date <= end


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only multi-agent retrospective replay")
    parser.add_argument("--mode", choices=[MODE_STRICT, MODE_RETROSPECTIVE], default=MODE_RETROSPECTIVE)
    parser.add_argument("--evaluation-start", type=date.fromisoformat, default=date(2026, 9, 10))
    parser.add_argument("--evaluation-end", type=date.fromisoformat, default=date(2026, 9, 21))
    parser.add_argument("--year-start", type=int, default=2016)
    args = parser.parse_args(argv)

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    cutoff = datetime.now(timezone.utc)
    agent1_obs, agent2_obs, agent3_obs, policy, read_only, load_seconds = _load_all_read_only(
        dsn, cutoff=cutoff, year_start=args.year_start
    )

    retrospective = args.mode == MODE_RETROSPECTIVE
    if retrospective:
        agent2_obs = retro_agent2(agent2_obs)
        agent3_obs = retro_agent3(agent3_obs)

    started = time.monotonic()
    a1_records = replay_agent1(agent1_obs, mode=args.mode)
    a1_seconds = time.monotonic() - started
    started = time.monotonic()
    a2_records = replay_agent2(agent2_obs).records
    a2_seconds = time.monotonic() - started
    started = time.monotonic()
    a3_records = replay_agent3(agent3_obs).records
    a3_seconds = time.monotonic() - started

    a1_by_match = {obs.match_id: result for obs, result in a1_records}
    a2_by_match = {rec.observation.match_id: rec.result for rec in a2_records}
    a3_by_match = {rec.observation.match_id: rec.result for rec in a3_records}
    a2_obs_by_match = {rec.observation.match_id: rec.observation for rec in a2_records}
    a3_obs_by_match = {rec.observation.match_id: rec.observation for rec in a3_records}

    manager_status: Counter[str] = Counter()
    manager_reason: Counter[str] = Counter()
    manager_matches = 0
    participation = Counter()
    if policy is not None:
        for match_id in a2_by_match:
            observation = a2_obs_by_match[match_id]
            if not _window(observation, args.evaluation_start, args.evaluation_end):
                continue
            if observation.outcome_status != "COMPLETED" or observation.winner_side not in (1, 2):
                continue
            if match_id not in a1_by_match or match_id not in a3_by_match:
                continue
            participation["matches_with_all_three"] += 1
            result = reconstruct_manager_v1(
                match_id=match_id,
                prediction_run_id=match_id,
                agent1=a1_by_match[match_id],
                agent2=a2_by_match[match_id],
                agent3=a3_by_match[match_id],
                policy=policy,
                as_of=observation.match_start_at,
            )
            manager_matches += 1
            manager_status[result.manager_status] += 1
            manager_reason[result.reason_code] += 1

    result = {
        "status": "OK",
        "label": "RETROSPECTIVE_RESEARCH" if retrospective else "STRICT_POINT_IN_TIME",
        "mode": args.mode,
        "cutoff": cutoff.isoformat(),
        "transaction_read_only": read_only,
        "database_writes": 0,
        "load_seconds": load_seconds,
        "evaluation_start": args.evaluation_start.isoformat(),
        "evaluation_end": args.evaluation_end.isoformat(),
        "agent1": {"seconds": a1_seconds, "score": score_agent1(a1_records, evaluation_start=args.evaluation_start, evaluation_end=args.evaluation_end)},
        "agent2": {"seconds": a2_seconds, "score": score_agent2(a2_records, evaluation_start=args.evaluation_start, evaluation_end=args.evaluation_end)},
        "agent3": {"seconds": a3_seconds, "score": score_agent3(a3_records, evaluation_start=args.evaluation_start, evaluation_end=args.evaluation_end)},
        "manager_v1": {
            "policy_version": None if policy is None else policy.policy_version,
            "matches_with_all_three": participation["matches_with_all_three"],
            "evaluated_matches": manager_matches,
            "status_counts": dict(sorted(manager_status.items())),
            "reason_counts": dict(sorted(manager_reason.items())),
        },
    }
    print("HISTORICAL_MULTI_AGENT_REPLAY " + json.dumps(result, sort_keys=True, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
