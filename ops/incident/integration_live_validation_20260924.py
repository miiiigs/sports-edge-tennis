from __future__ import annotations

"""Read-only live validation probe for the tennis volume-recovery integration.

Prints sanitized structured diagnostics only. Never prints a DSN, password, or
API key. Writes nothing. Each section is isolated so one failure cannot hide the
others.
"""

import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.data.prediction_persistence import (
    load_finalized_predictive_agent_predictions,
)
from sports_edge_tennis.live.current_slate_bootstrap import snapshot_readiness

PLACEHOLDER = re.compile(r"^(R\d+P\d+|Qf\d+|WQF\d+)$", re.IGNORECASE)
CYCLE_WINDOW_MINUTES = 30
PRODUCTION_SPREAD = 0.06
SHADOW_SPREAD = 0.08
AGENTS = ("AGENT_1_SURFACE_ELO", "AGENT_2_HIERARCHICAL_MARKOV", "AGENT_3_INTRANSITIVITY")


def emit(label: str, payload: dict) -> None:
    print(f"{label} {json.dumps(payload, default=str, sort_keys=True)}", flush=True)


def _dsn() -> str:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("INTEGRATION_VALIDATION_BLOCKED missing_dsn")
    return dsn


def _duplicate_baseline(conn) -> dict:
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, mv.player_1_id, mv.player_2_id, mv.match_start_at,
                mv.match_status::text AS match_status
            FROM match_versions mv
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        ),
        sr AS (SELECT DISTINCT match_id FROM match_source_ids WHERE source_system = 'SPORTRADAR'),
        stats AS (
            SELECT match_id, count(*) AS stat_count
            FROM match_stat_observations WHERE source_system = 'SPORTRADAR' GROUP BY match_id
        ),
        results AS (
            SELECT match_id, count(*) AS result_count
            FROM match_result_observations GROUP BY match_id
        )
        SELECT sr_match.match_id, other.match_id,
               abs(extract(epoch FROM (sr_match.match_start_at - other.match_start_at)))::int,
               COALESCE(stats.stat_count, 0), COALESCE(results.result_count, 0)
        FROM latest sr_match
        JOIN sr ON sr.match_id = sr_match.match_id
        JOIN latest other
          ON other.match_id <> sr_match.match_id
         AND other.match_start_at IS NOT NULL
         AND sr_match.match_start_at IS NOT NULL
         AND abs(extract(epoch FROM (sr_match.match_start_at - other.match_start_at))) <= 10800
         AND (
             (other.player_1_id = sr_match.player_1_id AND other.player_2_id = sr_match.player_2_id)
             OR (other.player_1_id = sr_match.player_2_id AND other.player_2_id = sr_match.player_1_id)
         )
        LEFT JOIN sr other_sr ON other_sr.match_id = other.match_id
        LEFT JOIN stats ON stats.match_id = sr_match.match_id
        LEFT JOIN results ON results.match_id = sr_match.match_id
        WHERE other_sr.match_id IS NULL
        """,
    ).fetchall()
    return {
        "duplicate_link_pairs": len({(int(r[0]), int(r[1])) for r in rows}),
        "sportradar_duplicate_matches": len({int(r[0]) for r in rows}),
        "historical_originals_implicated": len({int(r[1]) for r in rows}),
        "sportradar_stat_rows_on_duplicates": sum(int(r[3]) for r in rows),
        "examples": [
            {"sportradar_match_id": int(r[0]), "historical_match_id": int(r[1]), "start_delta_seconds": int(r[2])}
            for r in rows[:10]
        ],
    }


def _latest_run(conn):
    return conn.execute(
        """
        SELECT prediction_run_id, started_at, as_of
        FROM prediction_runs
        WHERE run_status = 'COMPLETED'
          AND experiment_id IS NULL
          AND prediction_contract_version = 'predictive_agent_result_v1'
          AND prediction_finalized_at IS NOT NULL
        ORDER BY started_at DESC, prediction_run_id DESC
        LIMIT 1
        """,
    ).fetchone()


def _cycle_run_ids(conn, started_at):
    return [
        int(r[0])
        for r in conn.execute(
            """
            SELECT prediction_run_id
            FROM prediction_runs
            WHERE run_status = 'COMPLETED'
              AND experiment_id IS NULL
              AND prediction_contract_version = 'predictive_agent_result_v1'
              AND started_at >= %s::timestamptz - (%s || ' minutes')::interval
            ORDER BY prediction_run_id
            """,
            (started_at, CYCLE_WINDOW_MINUTES),
        ).fetchall()
    ]


def _cycle_match_ids(conn, run_ids):
    if not run_ids:
        return []
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT DISTINCT match_id FROM prediction_run_targets WHERE prediction_run_id = ANY(%s)",
            (run_ids,),
        ).fetchall()
    ]


def _match_identity(conn, match_id, cutoff):
    row = conn.execute(
        """
        SELECT mv.player_1_id, mv.player_2_id, p1.canonical_name, p2.canonical_name, mv.match_start_at
        FROM match_versions mv
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        WHERE mv.match_id = %s AND mv.source_observed_at <= %s AND mv.as_of <= %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id, cutoff, cutoff),
    ).fetchone()
    return row


def _slate(conn, run) -> dict:
    run_id, started_at, as_of = int(run[0]), run[1], run[2]
    run_ids = _cycle_run_ids(conn, started_at)
    match_ids = _cycle_match_ids(conn, run_ids)
    concrete, placeholders, players = 0, 0, set()
    identities = {}
    for match_id in match_ids:
        row = _match_identity(conn, match_id, as_of)
        if row is None:
            continue
        p1, p2, n1, n2, _start = row
        is_ph = bool(PLACEHOLDER.match((n1 or "").strip())) or bool(PLACEHOLDER.match((n2 or "").strip()))
        if is_ph:
            placeholders += 1
            continue
        concrete += 1
        players.update((int(p1), int(p2)))
        identities[match_id] = (int(p1), int(p2), str(n1), str(n2))
    return {
        "run_id": run_id,
        "run_ids": run_ids,
        "total_targets": len(match_ids),
        "placeholders": placeholders,
        "concrete_matches": concrete,
        "unique_real_players": len(players),
        "identities": identities,
        "as_of": as_of,
    }


def _agent_counts(conn, run_ids, concrete_ids) -> tuple[dict, dict]:
    counts = {agent: {"predicted": 0, "abstained": 0} for agent in AGENTS}
    by_match: dict[int, dict] = {}
    for run_id in run_ids:
        for prediction in load_finalized_predictive_agent_predictions(conn, prediction_run_id=run_id):
            if prediction.match_id not in concrete_ids:
                continue
            agent = str(prediction.agent_code)
            by_match.setdefault(prediction.match_id, {})[agent] = prediction
            if agent in counts:
                if prediction.prediction_status == "PREDICTED":
                    counts[agent]["predicted"] += 1
                else:
                    counts[agent]["abstained"] += 1
    return counts, by_match


def _manager_counts(conn, run_ids) -> dict:
    rows = conn.execute(
        """
        SELECT manager_status::text, reason_code::text, count(*)
        FROM manager_consensus_results
        WHERE prediction_run_id = ANY(%s)
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        (run_ids,),
    ).fetchall()
    return {f"{r[0]}:{r[1]}": int(r[2]) for r in rows}


def _market_reasons(conn, run_ids) -> dict:
    rows = conn.execute(
        """
        SELECT signal_type::text, decision_status::text, reason_code::text, count(*)
        FROM decisions
        WHERE prediction_run_id = ANY(%s) AND market_policy_version_id IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """,
        (run_ids,),
    ).fetchall()
    return {f"{r[0]}:{r[1]}:{r[2]}": int(r[3]) for r in rows}


def _spread(conn, by_match, identities) -> dict:
    candidates = []
    prod = shadow = 0
    for match_id, agents in by_match.items():
        a1 = agents.get("AGENT_1_SURFACE_ELO")
        a2 = agents.get("AGENT_2_HIERARCHICAL_MARKOV")
        if a1 is None or a2 is None:
            continue
        if a1.prediction_status != "PREDICTED" or a2.prediction_status != "PREDICTED":
            continue
        if a1.selection_side != a2.selection_side or a1.selection_side not in (1, 2):
            continue
        p1 = float(a1.win_probability)
        p2 = float(a2.win_probability)
        if p1 < 0.5 or p2 < 0.5:
            continue
        spread = abs(p1 - p2)
        prod_pass = spread <= PRODUCTION_SPREAD
        shadow_pass = spread <= SHADOW_SPREAD
        prod += int(prod_pass)
        shadow += int(shadow_pass)
        names = identities.get(match_id, (None, None, "?", "?"))
        candidates.append(
            {
                "match_id": match_id,
                "players": [names[2], names[3]],
                "a1_probability": p1,
                "a2_probability": p2,
                "side": int(a1.selection_side),
                "spread": round(spread, 6),
                "production_0.06_pass": prod_pass,
                "shadow_0.08_pass": shadow_pass,
            }
        )
    candidates.sort(key=lambda c: c["spread"])
    for c in candidates:
        emit("INTEGRATION_SPREAD_CANDIDATE", c)
    return {
        "same_direction_candidates": len(candidates),
        "production_0.06_pass": prod,
        "shadow_0.08_pass": shadow,
        "incremental_shadow_candidates": shadow - prod,
    }


def _a2_readiness(conn, concrete_ids, as_of) -> dict:
    targets = []
    for match_id in concrete_ids:
        row = _match_identity(conn, match_id, as_of)
        if row is not None:
            targets.append((match_id, int(row[0]), int(row[1])))
    player_ids = sorted({pid for _m, p1, p2 in targets for pid in (p1, p2)})
    readiness = snapshot_readiness(conn, player_ids=player_ids, cutoff=as_of)
    ready = [pid for pid in player_ids if readiness[pid].ready]
    not_ready = [pid for pid in player_ids if not readiness[pid].ready]
    deficits = []
    for pid in not_ready:
        r = readiness[pid]
        emit(
            "INTEGRATION_A2_PLAYER_NOT_READY",
            {
                "player_id": pid,
                "usable_matches": r.usable_matches,
                "serve_points": r.serve_points,
                "return_points": r.return_points,
                "missing_matches": r.missing_matches,
            },
        )
    return {
        "players": len(player_ids),
        "ready_players": len(ready),
        "not_ready_players": len(not_ready),
        "not_ready_ids": not_ready,
        "not_ready_exact_deficits": deficits,
    }


def _bootstrap_effect(conn, not_ready_ids: list[int]) -> dict:
    inserted = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM match_result_observations WHERE ingested_at >= now() - interval '3 hours'),
            (SELECT count(DISTINCT match_id) FROM match_result_observations WHERE ingested_at >= now() - interval '3 hours'),
            (SELECT count(*) FROM match_stat_observations WHERE ingested_at >= now() - interval '3 hours'),
            (SELECT count(DISTINCT match_id) FROM match_stat_observations WHERE ingested_at >= now() - interval '3 hours'),
            (SELECT count(DISTINCT player_id) FROM match_stat_observations WHERE ingested_at >= now() - interval '3 hours')
        """,
    ).fetchone()
    targeted = conn.execute(
        """
        SELECT player_id, count(DISTINCT match_id)
        FROM match_stat_observations
        WHERE ingested_at >= now() - interval '3 hours' AND player_id = ANY(%s)
        GROUP BY player_id ORDER BY player_id
        """,
        (not_ready_ids or [0],),
    ).fetchall()
    per_player = []
    for pid in not_ready_ids:
        row = conn.execute(
            """
            WITH pv AS (
                SELECT DISTINCT mv.match_id
                FROM match_versions mv
                WHERE (mv.player_1_id = %s OR mv.player_2_id = %s)
                  AND mv.match_start_at IS NOT NULL
                  AND mv.source_observed_at <= now()
                  AND mv.as_of <= now()
            )
            SELECT
                count(*) FILTER (
                    WHERE EXISTS (
                        SELECT 1 FROM match_result_observations r
                        WHERE r.match_id = pv.match_id AND r.outcome_status = 'COMPLETED'
                    )
                ) AS completed_matches,
                count(*) FILTER (
                    WHERE EXISTS (
                        SELECT 1 FROM match_stat_observations s WHERE s.match_id = pv.match_id
                    )
                ) AS statted_matches
            FROM pv
            """,
            (pid, pid),
        ).fetchone()
        per_player.append(
            {"player_id": pid, "completed_matches": int(row[0]), "statted_matches": int(row[1])}
        )
    return {
        "inserted_results": int(inserted[0]),
        "inserted_result_matches": int(inserted[1]),
        "inserted_stats": int(inserted[2]),
        "inserted_stat_matches": int(inserted[3]),
        "inserted_stat_players": int(inserted[4]),
        "inserted_stats_for_not_ready_players": [
            {"player_id": int(r[0]), "matches": int(r[1])} for r in targeted
        ],
        "not_ready_player_coverage": per_player,
    }


def _match_130870(conn) -> dict:
    rows = conn.execute(
        """
        SELECT decision_id, decision_status::text, reason_code::text,
               consensus_probability, market_decimal_price, sharp_reference_price, as_of
        FROM decisions WHERE match_id = 130870
        ORDER BY decision_id DESC LIMIT 5
        """,
    ).fetchall()
    return {
        "decisions": [
            {
                "decision_id": int(r[0]),
                "status": r[1],
                "reason_code": r[2],
                "consensus_probability": r[3],
                "market_decimal_price": r[4],
                "sharp_reference_price": r[5],
                "as_of": r[6],
            }
            for r in rows
        ]
    }


def _odds_state(conn, since) -> dict:
    ledger = conn.execute(
        """
        SELECT endpoint, COALESCE(bookmaker, '-'), attempt_status::text, count(*)
        FROM oddspapi_request_ledger
        WHERE requested_at >= %s
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """,
        (since,),
    ).fetchall()
    raw = conn.execute(
        """
        SELECT count(*), count(DISTINCT provider_event_id),
               min(observation_time), max(observation_time)
        FROM raw_odds_observations
        WHERE provider_code = 'ODDSPAPI' AND received_at >= %s
        """,
        (since,),
    ).fetchone()
    snaps = conn.execute(
        """
        SELECT source_system, bookmaker_code, count(*),
               min(source_observed_at), max(source_observed_at), max(ingested_at)
        FROM odds_snapshots
        WHERE ingested_at >= %s AND source_system IN ('SHARP', 'OFFERED')
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        (since,),
    ).fetchall()
    cache = conn.execute(
        "SELECT count(*), max(last_verified_at) FROM oddspapi_event_identity_cache"
    ).fetchone()
    return {
        "ledger": [{"endpoint": r[0], "bookmaker": r[1], "status": r[2], "count": int(r[3])} for r in ledger],
        "raw_observations": {
            "rows": int(raw[0]) if raw else 0,
            "events": int(raw[1]) if raw else 0,
            "min_observation_time": raw[2] if raw else None,
            "max_observation_time": raw[3] if raw else None,
        },
        "snapshots": [
            {
                "source_system": r[0],
                "bookmaker_code": r[1],
                "rows": int(r[2]),
                "min_source_observed_at": r[3],
                "max_source_observed_at": r[4],
                "max_ingested_at": r[5],
            }
            for r in snaps
        ],
        "identity_cache_rows": int(cache[0]) if cache else 0,
        "identity_cache_max_verified_at": cache[1] if cache else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-hours", type=int, default=3)
    args = parser.parse_args()

    with psycopg.connect(_dsn(), autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        now = datetime.now(UTC)
        since = now - timedelta(hours=args.since_hours)

        try:
            emit("INTEGRATION_DUPLICATE_BASELINE", _duplicate_baseline(conn))
        except Exception as exc:  # noqa: BLE001
            emit("INTEGRATION_SECTION_ERROR", {"section": "duplicate", "error": type(exc).__name__})

        run = _latest_run(conn)
        if run is None:
            emit("INTEGRATION_SECTION_ERROR", {"section": "slate", "error": "no_completed_production_run"})
            return 0
        emit("INTEGRATION_RUN", {"run_id": int(run[0]), "started_at": run[1], "as_of": run[2]})

        slate = _slate(conn, run)
        emit(
            "INTEGRATION_SLATE",
            {k: slate[k] for k in ("run_id", "run_ids", "total_targets", "placeholders", "concrete_matches", "unique_real_players")},
        )
        concrete_ids = set(slate["identities"])

        try:
            counts, by_match = _agent_counts(conn, slate["run_ids"], concrete_ids)
            emit("INTEGRATION_AGENT_COUNTS", counts)
        except Exception as exc:  # noqa: BLE001
            emit("INTEGRATION_SECTION_ERROR", {"section": "agents", "error": type(exc).__name__})
            counts, by_match = {}, {}

        try:
            emit("INTEGRATION_MANAGER", _manager_counts(conn, slate["run_ids"]))
        except Exception as exc:  # noqa: BLE001
            emit("INTEGRATION_SECTION_ERROR", {"section": "manager", "error": type(exc).__name__})

        try:
            emit("INTEGRATION_MARKET_REASONS", _market_reasons(conn, slate["run_ids"]))
        except Exception as exc:  # noqa: BLE001
            emit("INTEGRATION_SECTION_ERROR", {"section": "market", "error": type(exc).__name__})

        try:
            readiness = _a2_readiness(conn, sorted(concrete_ids), slate["as_of"])
            not_ready_ids = readiness.pop("not_ready_ids", [])
            emit("INTEGRATION_A2_READINESS", readiness)
            emit("INTEGRATION_BOOTSTRAP_EFFECT", _bootstrap_effect(conn, not_ready_ids))
        except Exception as exc:  # noqa: BLE001
            emit("INTEGRATION_SECTION_ERROR", {"section": "a2_readiness", "error": type(exc).__name__})

        try:
            emit("INTEGRATION_SPREAD_SUMMARY", _spread(conn, by_match, slate["identities"]))
        except Exception as exc:  # noqa: BLE001
            emit("INTEGRATION_SECTION_ERROR", {"section": "spread", "error": type(exc).__name__})

        try:
            emit("INTEGRATION_130870", _match_130870(conn))
        except Exception as exc:  # noqa: BLE001
            emit("INTEGRATION_SECTION_ERROR", {"section": "130870", "error": type(exc).__name__})

        try:
            emit("INTEGRATION_ODDS_STATE", _odds_state(conn, since))
        except Exception as exc:  # noqa: BLE001
            emit("INTEGRATION_SECTION_ERROR", {"section": "odds", "error": type(exc).__name__})

    emit("INTEGRATION_VALIDATION_DONE", {"writes": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
