"""Read-only extraction of genuine forward-issued Tennis model picks.

The production database stores one Manager row per hourly evaluation, not one
row per match.  This extractor therefore freezes the *first* production
evaluation per match, which is the population the dashboard scores, and never
substitutes a later rerun's probability.

Run with a read-only session::

    $env:TENNIS_DATABASE_URL = "<production DSN>"
    python research/tennis_model_error_forensics_20261021/extract_live_picks.py `
        > tmp/tennis_forensics_20261021.jsonl

Output is JSON Lines: one row per unique match and population, then a final
``_manifest`` line.  No credentials or raw provider payloads are written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

DATASET_SCHEMA_VERSION = "tennis_model_error_forensics_v1"
EXTRACT_CUTOFF = "2026-10-21T03:30:00+00:00"

PRODUCTION_RUNS = """
    pr.prediction_contract_version = 'predictive_agent_result_v1'
    AND pr.run_status = 'COMPLETED'
    AND pr.prediction_finalized_at IS NOT NULL
    AND pr.experiment_id IS NULL
"""

PIT_VERSION = """
    LEFT JOIN LATERAL (
        SELECT mv0.*
        FROM match_versions mv0
        WHERE mv0.match_id = {match_id}
          AND mv0.as_of <= {as_of}
          AND mv0.source_observed_at <= {as_of}
        ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC
        LIMIT 1
    ) mv ON TRUE
"""

LATEST_RESULT = """
    LEFT JOIN LATERAL (
        SELECT mro.match_result_observation_id, mro.outcome_status::text AS outcome_status,
               mro.winner_side, mro.scoreline_text, mro.source_observed_at, mro.as_of,
               ir.source_system AS result_source_system
        FROM match_result_observations mro
        LEFT JOIN ingest_runs ir ON ir.ingest_run_id = mro.ingest_run_id
        WHERE mro.match_id = {match_id}
          AND mro.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
        ORDER BY mro.as_of DESC, mro.source_observed_at DESC, mro.match_result_observation_id DESC
        LIMIT 1
    ) r ON TRUE
"""


def _git_sha() -> str:
    override = (os.environ.get("TENNIS_RESEARCH_GIT_SHA") or "").strip()
    if override:
        return override
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def _dsn() -> str:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("TENNIS_DATABASE_URL or DATABASE_URL is required")
    return dsn


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=_json_default)


def _rows(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    return conn.execute(sql, params).fetchall()


def load_manager_picks(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return _rows(
        conn,
        f"""
        WITH ranked AS (
            SELECT
                mcr.manager_consensus_result_id,
                mcr.prediction_run_id,
                mcr.match_id,
                mcr.manager_policy_version_id,
                mcr.manager_status::text AS manager_status,
                mcr.selected_side,
                mcr.consensus_probability,
                mcr.active_agent_count,
                mcr.abstaining_agent_count,
                mcr.probability_spread,
                mcr.reason_code::text AS manager_reason_code,
                mcr.as_of AS picked_at,
                pr.prediction_finalized_at,
                row_number() OVER (
                    PARTITION BY mcr.match_id
                    ORDER BY mcr.as_of, mcr.manager_consensus_result_id
                ) AS rn
            FROM manager_consensus_results mcr
            JOIN prediction_runs pr ON pr.prediction_run_id = mcr.prediction_run_id
            WHERE mcr.manager_status = 'CONSENSUS'
              AND mcr.selected_side IN (1, 2)
              AND {PRODUCTION_RUNS}
        )
        SELECT
            'manager_consensus'::text AS population,
            ranked.*,
            mpv.policy_version AS manager_policy_version,
            mv.match_version_id AS pit_match_version_id,
            mv.player_1_id,
            mv.player_2_id,
            mv.match_start_at,
            mv.surface::text AS surface,
            mv.competition_tier::text AS tier,
            mv.tour_scope::text AS tour_scope,
            mv.best_of_sets,
            mv.round_code,
            mv.qualifying_flag,
            mv.as_of AS pit_version_as_of,
            te.season_year,
            te.start_date AS edition_start_date,
            te.end_date AS edition_end_date,
            te.surface::text AS edition_surface,
            t.tournament_id,
            t.canonical_name AS tournament,
            p1.canonical_name AS player_1_name,
            p2.canonical_name AS player_2_name,
            r.match_result_observation_id,
            r.outcome_status,
            r.winner_side,
            r.scoreline_text,
            r.source_observed_at AS result_source_observed_at,
            r.as_of AS result_as_of,
            r.result_source_system,
            (
                SELECT count(*) FROM match_result_observations x
                WHERE x.match_id = ranked.match_id
                  AND x.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
            ) AS result_observation_count,
            (
                SELECT count(DISTINCT (x.outcome_status, x.winner_side))
                FROM match_result_observations x
                WHERE x.match_id = ranked.match_id
                  AND x.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
            ) AS distinct_result_states,
            (
                SELECT count(*) FROM match_versions x WHERE x.match_id = ranked.match_id
            ) AS match_version_count
        FROM ranked
        JOIN manager_policy_versions mpv
          ON mpv.manager_policy_version_id = ranked.manager_policy_version_id
        {PIT_VERSION.format(match_id="ranked.match_id", as_of="ranked.picked_at")}
        JOIN matches m ON m.match_id = ranked.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        {LATEST_RESULT.format(match_id="ranked.match_id")}
        WHERE ranked.rn = 1
        ORDER BY ranked.match_id
        """,
    )


def load_wide_spread_tracking(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return _rows(
        conn,
        f"""
        WITH candidates AS (
            SELECT
                mcr.manager_consensus_result_id,
                mcr.prediction_run_id,
                mcr.match_id,
                mcr.manager_policy_version_id,
                mcr.manager_status::text AS manager_status,
                a1.selection_side AS selected_side,
                (a1.win_probability + a2.win_probability) / 2.0 AS consensus_probability,
                a3.win_probability AS a3_probability,
                a1.win_probability AS a1_probability,
                a2.win_probability AS a2_probability,
                2 AS active_agent_count,
                1 AS abstaining_agent_count,
                abs(a1.win_probability - a2.win_probability) AS probability_spread,
                mcr.reason_code::text AS manager_reason_code,
                mcr.as_of AS picked_at,
                pr.prediction_finalized_at,
                row_number() OVER (
                    PARTITION BY mcr.match_id
                    ORDER BY mcr.as_of, mcr.manager_consensus_result_id
                ) AS rn
            FROM manager_consensus_results mcr
            JOIN prediction_runs pr ON pr.prediction_run_id = mcr.prediction_run_id
            JOIN predictions a1
              ON a1.prediction_run_id = mcr.prediction_run_id
             AND a1.match_id = mcr.match_id
             AND a1.agent_code = 'AGENT_1_SURFACE_ELO'
            JOIN predictions a2
              ON a2.prediction_run_id = mcr.prediction_run_id
             AND a2.match_id = mcr.match_id
             AND a2.agent_code = 'AGENT_2_HIERARCHICAL_MARKOV'
            JOIN predictions a3
              ON a3.prediction_run_id = mcr.prediction_run_id
             AND a3.match_id = mcr.match_id
             AND a3.agent_code = 'AGENT_3_INTRANSITIVITY'
            WHERE mcr.manager_status = 'NO_BET'
              AND mcr.reason_code = 'AGENT3_ABSTENTION_SPREAD_EXCEEDED'
              AND a1.prediction_status = 'PREDICTED'
              AND a2.prediction_status = 'PREDICTED'
              AND a3.prediction_status = 'ABSTAINED'
              AND a1.selection_side IN (1, 2)
              AND a2.selection_side = a1.selection_side
              AND a1.win_probability IS NOT NULL
              AND a2.win_probability IS NOT NULL
              AND ((a1.win_probability + a2.win_probability) / 2.0) >= 0.60
              AND {PRODUCTION_RUNS}
        )
        SELECT
            'wide_spread_tracking'::text AS population,
            candidates.*,
            mpv.policy_version AS manager_policy_version,
            mv.match_version_id AS pit_match_version_id,
            mv.player_1_id,
            mv.player_2_id,
            mv.match_start_at,
            mv.surface::text AS surface,
            mv.competition_tier::text AS tier,
            mv.tour_scope::text AS tour_scope,
            mv.best_of_sets,
            mv.round_code,
            mv.qualifying_flag,
            mv.as_of AS pit_version_as_of,
            te.season_year,
            te.start_date AS edition_start_date,
            te.end_date AS edition_end_date,
            te.surface::text AS edition_surface,
            t.tournament_id,
            t.canonical_name AS tournament,
            p1.canonical_name AS player_1_name,
            p2.canonical_name AS player_2_name,
            r.match_result_observation_id,
            r.outcome_status,
            r.winner_side,
            r.scoreline_text,
            r.source_observed_at AS result_source_observed_at,
            r.as_of AS result_as_of,
            r.result_source_system,
            (
                SELECT count(*) FROM match_result_observations x
                WHERE x.match_id = candidates.match_id
                  AND x.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
            ) AS result_observation_count,
            (
                SELECT count(DISTINCT (x.outcome_status, x.winner_side))
                FROM match_result_observations x
                WHERE x.match_id = candidates.match_id
                  AND x.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
            ) AS distinct_result_states,
            (
                SELECT count(*) FROM match_versions x WHERE x.match_id = candidates.match_id
            ) AS match_version_count
        FROM candidates
        JOIN manager_policy_versions mpv
          ON mpv.manager_policy_version_id = candidates.manager_policy_version_id
        {PIT_VERSION.format(match_id="candidates.match_id", as_of="candidates.picked_at")}
        JOIN matches m ON m.match_id = candidates.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        {LATEST_RESULT.format(match_id="candidates.match_id")}
        WHERE candidates.rn = 1
        ORDER BY candidates.match_id
        """,
    )


def load_agents(conn: psycopg.Connection, picks: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    if not picks:
        return {}
    run_ids = sorted({int(p["prediction_run_id"]) for p in picks})
    match_ids = sorted({int(p["match_id"]) for p in picks})
    keys = {(int(p["prediction_run_id"]), int(p["match_id"])) for p in picks}
    rows = _rows(
        conn,
        """
        SELECT p.prediction_run_id, p.match_id, p.agent_code::text AS agent_code,
               p.prediction_status::text AS prediction_status, p.selection_side,
               p.win_probability, p.probability_representation,
               p.abstain_reason_code, p.model_version, a.config_version
        FROM predictions p
        JOIN agent_config_versions a
          ON a.agent_config_version_id = p.agent_config_version_id
        WHERE p.prediction_run_id = ANY(%s)
          AND p.match_id = ANY(%s)
        """,
        (run_ids, match_ids),
    )
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["prediction_run_id"]), int(row["match_id"]))
        if key not in keys:
            continue
        out.setdefault(key, {})[
            str(row["agent_code"])
        ] = {
            "status": row["prediction_status"],
            "selection_side": row["selection_side"],
            "win_probability": float(row["win_probability"]) if row["win_probability"] is not None else None,
            "probability_representation": row["probability_representation"],
            "abstain_reason_code": row["abstain_reason_code"],
            "model_version": row["model_version"],
            "config_version": row["config_version"],
        }
    return out


def load_history(conn: psycopg.Connection, player_ids: list[int]) -> list[dict[str, Any]]:
    if not player_ids:
        return []
    return _rows(
        conn,
        """
        WITH target_players AS (
            SELECT unnest(%s::bigint[]) AS player_id
        ),
        latest_versions AS (
            SELECT DISTINCT ON (match_id)
                match_id, player_1_id, player_2_id, surface, competition_tier,
                match_start_at, as_of, source_observed_at
            FROM match_versions
            ORDER BY match_id, as_of DESC, source_observed_at DESC, match_version_id DESC
        ),
        latest_results AS (
            SELECT DISTINCT ON (match_id)
                match_id, outcome_status, winner_side, source_observed_at, as_of
            FROM match_result_observations
            WHERE outcome_status = 'COMPLETED' AND winner_side IN (1, 2)
            ORDER BY match_id, as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        )
        SELECT
            mv.match_id,
            tp.player_id,
            CASE WHEN mv.player_1_id = tp.player_id THEN 1 ELSE 2 END AS side,
            CASE WHEN mv.player_1_id = tp.player_id THEN mv.player_2_id ELSE mv.player_1_id END AS opponent_id,
            COALESCE(mv.match_start_at, te.start_date::timestamptz) AS match_date,
            mv.surface::text AS surface,
            mv.competition_tier::text AS tier,
            (lr.winner_side = CASE WHEN mv.player_1_id = tp.player_id THEN 1 ELSE 2 END) AS won,
            lr.as_of AS result_as_of,
            lr.source_observed_at AS result_source_observed_at
        FROM target_players tp
        JOIN latest_versions mv
          ON tp.player_id IN (mv.player_1_id, mv.player_2_id)
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN latest_results lr ON lr.match_id = mv.match_id
        WHERE mv.match_start_at IS NOT NULL OR te.start_date IS NOT NULL
        """,
        (player_ids,),
    )


def load_stat_coverage(conn: psycopg.Connection, match_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not match_ids:
        return {}
    rows = _rows(
        conn,
        """
        SELECT match_id, count(*) AS stat_rows,
               count(DISTINCT player_id) AS stat_players,
               min(as_of) AS stat_first_as_of,
               max(as_of) AS stat_last_as_of
        FROM match_stat_observations
        WHERE observation_kind = 'RAW_SOURCE'
          AND match_id = ANY(%s)
        GROUP BY match_id
        """,
        (match_ids,),
    )
    return {int(r["match_id"]): r for r in rows}


def load_market(
    conn: psycopg.Connection, picks: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    if not picks:
        return {}
    match_ids = sorted({int(p["match_id"]) for p in picks})
    # Latest pre-pick Pinnacle pair and best pre-pick offered price.
    rows = _rows(
        conn,
        """
        WITH sharp AS (
            SELECT DISTINCT ON (os.match_id, os.selection_type)
                os.match_id, os.selection_type::text AS selection_type,
                os.decimal_price, os.as_of
            FROM odds_snapshots os
            WHERE os.match_id = ANY(%s)
              AND os.market_type = 'MATCH_WINNER'
              AND os.source_system = 'SHARP'
              AND os.bookmaker_code = 'PINNACLE'
              AND os.decimal_price > 1
            ORDER BY os.match_id, os.selection_type, os.as_of DESC
        ),
        offered AS (
            SELECT os.match_id, os.selection_type::text AS selection_type,
                   max(os.decimal_price) AS best_price,
                   count(DISTINCT os.bookmaker_code) AS bookmakers,
                   max(os.as_of) AS latest_as_of
            FROM odds_snapshots os
            WHERE os.match_id = ANY(%s)
              AND os.market_type = 'MATCH_WINNER'
              AND os.source_system = 'OFFERED'
              AND os.decimal_price > 1
            GROUP BY os.match_id, os.selection_type
        )
        SELECT s.match_id, s.selection_type, s.decimal_price AS sharp_price, s.as_of AS sharp_as_of,
               o.best_price AS offered_best_price, o.bookmakers, o.latest_as_of AS offered_latest_as_of
        FROM sharp s LEFT JOIN offered o USING (match_id, selection_type)
        """,
        (match_ids, match_ids),
    )
    by_match: dict[int, dict[str, Any]] = {}
    for row in rows:
        by_match.setdefault(int(row["match_id"]), {})[str(row["selection_type"])] = row
    return by_match


def load_decision_overlay(conn: psycopg.Connection, picks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    if not picks:
        return {}
    match_ids = sorted({int(p["match_id"]) for p in picks})
    rows = _rows(
        conn,
        f"""
        WITH candidates AS (
            SELECT
                d.match_id, d.decision_id, d.decision_status::text AS decision_status,
                d.reason_code::text AS decision_reason_code,
                d.consensus_probability, d.market_decimal_price,
                d.published_at, d.as_of, d.edge_pct,
                row_number() OVER (
                    PARTITION BY d.match_id
                    ORDER BY COALESCE(d.published_at, d.as_of), d.decision_id
                ) AS rn
            FROM decisions d
            JOIN prediction_runs pr ON pr.prediction_run_id = d.prediction_run_id
            WHERE d.match_id = ANY(%s)
              AND d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.decision_status IN ('BET', 'VETOED')
              AND d.market_type = 'MATCH_WINNER'
              AND {PRODUCTION_RUNS}
        ),
        bets AS (
            SELECT * FROM candidates WHERE decision_status = 'BET' AND rn = 1
        ),
        vetoed AS (
            SELECT * FROM candidates WHERE decision_status = 'VETOED' AND rn = 1
        ),
        matches AS (
            SELECT DISTINCT match_id FROM candidates
        )
        SELECT m.match_id, b.decision_id AS value_bet_decision_id,
               b.consensus_probability AS value_bet_probability,
               b.market_decimal_price AS value_bet_price,
               b.published_at AS value_bet_published_at,
               b.as_of AS value_bet_as_of, b.edge_pct AS value_bet_edge_pct,
               v.decision_id AS veto_decision_id, v.as_of AS veto_as_of,
               dva.veto_policy_version, dva.triggered_codes
        FROM matches m
        LEFT JOIN bets b USING (match_id)
        LEFT JOIN vetoed v USING (match_id)
        LEFT JOIN decision_veto_audits dva ON dva.decision_id = v.decision_id
        """,
        (match_ids,),
    )
    return {int(r["match_id"]): r for r in rows}


def load_reruns(conn: psycopg.Connection, picks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    if not picks:
        return {}
    match_ids = sorted({int(p["match_id"]) for p in picks})
    rows = _rows(
        conn,
        f"""
        SELECT mcr.match_id,
               count(*) AS consensus_rows,
               min(mcr.as_of) AS first_at,
               max(mcr.as_of) AS last_at,
               min(mcr.consensus_probability) AS prob_min,
               max(mcr.consensus_probability) AS prob_max,
               count(DISTINCT mcr.selected_side) AS side_count
        FROM manager_consensus_results mcr
        JOIN prediction_runs pr ON pr.prediction_run_id = mcr.prediction_run_id
        WHERE mcr.match_id = ANY(%s)
          AND mcr.manager_status = 'CONSENSUS'
          AND mcr.selected_side IN (1, 2)
          AND {PRODUCTION_RUNS}
        GROUP BY mcr.match_id
        """,
        (match_ids,),
    )
    return {int(r["match_id"]): r for r in rows}


def load_identity(conn: psycopg.Connection, player_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not player_ids:
        return {}
    rows = _rows(
        conn,
        """
        WITH aliases AS (
            SELECT pa.player_id, pa.source_system, pa.normalized_alias,
                   count(*) OVER (PARTITION BY pa.source_system, pa.normalized_alias) AS alias_peers
            FROM player_aliases pa
            WHERE pa.player_id = ANY(%s)
        ),
        names AS (
            SELECT player_id, canonical_name,
                   count(*) OVER (PARTITION BY canonical_name) AS name_peers
            FROM players
            WHERE player_id = ANY(%s)
        ),
        events AS (
            SELECT player_id, count(*) AS bridge_events
            FROM (
                SELECT live_player_id AS player_id FROM model_identity_bridge_events
                WHERE live_player_id = ANY(%s)
                UNION ALL
                SELECT historical_player_id FROM model_identity_bridge_events
                WHERE historical_player_id = ANY(%s)
            ) x GROUP BY player_id
        )
        SELECT p.player_id,
               max(coalesce(n.name_peers, 1)) AS canonical_name_peers,
               max(coalesce(a.alias_peers, 1)) AS max_alias_peers,
               max(coalesce(e.bridge_events, 0)) AS bridge_events
        FROM players p
        LEFT JOIN aliases a USING (player_id)
        LEFT JOIN names n USING (player_id)
        LEFT JOIN events e USING (player_id)
        WHERE p.player_id = ANY(%s)
        GROUP BY p.player_id
        """,
        (player_ids, player_ids, player_ids, player_ids, player_ids),
    )
    return {int(r["player_id"]): r for r in rows}


def _history_features(
    history: list[dict[str, Any]],
    *,
    player_id: int,
    opponent_id: int,
    picked_at: datetime,
    surface: str | None,
    stat_coverage: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    rows = [
        h
        for h in history
        if int(h["player_id"]) == player_id
        and h["result_as_of"] is not None
        and h["result_as_of"] <= picked_at
        and h["match_date"] is not None
        and h["match_date"] < picked_at
    ]
    rows.sort(key=lambda h: h["match_date"], reverse=True)
    completed = len(rows)
    wins = sum(1 for h in rows if h["won"])
    surface_rows = [h for h in rows if h["surface"] == surface]
    stat_rows = [h for h in rows if int(h["match_id"]) in stat_coverage]
    surface_stat_rows = [h for h in surface_rows if int(h["match_id"]) in stat_coverage]
    last = rows[0]["match_date"] if rows else None
    days_since = (
        (picked_at.date() - last.date()).days if last is not None else None
    )
    h2h = [h for h in rows if int(h["opponent_id"]) == opponent_id]
    return {
        "prior_matches": completed,
        "prior_wins": wins,
        "prior_win_rate": (wins / completed) if completed else None,
        "prior_surface_matches": len(surface_rows),
        "prior_surface_wins": sum(1 for h in surface_rows if h["won"]),
        "prior_surface_win_rate": (
            sum(1 for h in surface_rows if h["won"]) / len(surface_rows)
            if surface_rows
            else None
        ),
        "prior_stat_matches": len(stat_rows),
        "prior_surface_stat_matches": len(surface_stat_rows),
        "last_match_date": last,
        "days_since_last_match": days_since,
        "prior_10_wins": sum(1 for h in rows[:10] if h["won"]),
        "h2h_prior_matches": len(h2h),
        "h2h_prior_wins": sum(1 for h in h2h if h["won"]),
    }


def _market_features(row: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    side = int(row["selected_side"])
    p1 = market.get("PLAYER_1") or {}
    p2 = market.get("PLAYER_2") or {}
    sharp1 = p1.get("sharp_price")
    sharp2 = p2.get("sharp_price")
    out: dict[str, Any] = {
        "sharp_price_player_1": float(sharp1) if sharp1 else None,
        "sharp_price_player_2": float(sharp2) if sharp2 else None,
        "sharp_as_of": p1.get("sharp_as_of") or p2.get("sharp_as_of"),
        "sharp_picked_probability": None,
        "sharp_opponent_probability": None,
        "sharp_favourite_side": None,
        "model_minus_sharp": None,
        "offered_best_price": None,
        "offered_bookmakers": None,
    }
    if sharp1 and sharp2 and sharp1 > 1 and sharp2 > 1:
        inv1, inv2 = 1.0 / float(sharp1), 1.0 / float(sharp2)
        prob1, prob2 = inv1 / (inv1 + inv2), inv2 / (inv1 + inv2)
        picked = prob1 if side == 1 else prob2
        opponent = prob2 if side == 1 else prob1
        out.update(
            sharp_picked_probability=picked,
            sharp_opponent_probability=opponent,
            sharp_favourite_side=1 if prob1 > prob2 else 2,
            model_minus_sharp=float(row["consensus_probability"]) - picked,
        )
    offered = market.get("PLAYER_1" if side == 1 else "PLAYER_2") or {}
    out["offered_best_price"] = (
        float(offered["offered_best_price"]) if offered.get("offered_best_price") else None
    )
    out["offered_bookmakers"] = (
        int(offered["bookmakers"]) if offered.get("bookmakers") is not None else None
    )
    return out


def load_other_populations(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Value Bets and Veto-blocked selections, deduped to the first per match.

    These are reported separately from Manager Model Picks.  A Value Bet can use
    a later Manager rerun than the first model pick, so it is not a strict subset
    of the >=60% first-pick population and must not be silently merged into it.
    """
    rows = _rows(
        conn,
        f"""
        WITH candidates AS (
            SELECT
                d.match_id, d.decision_id, d.decision_status::text AS decision_status,
                d.selection_side, d.consensus_probability, d.market_decimal_price,
                d.published_at, d.as_of, d.edge_pct,
                d.reason_code::text AS reason_code,
                row_number() OVER (
                    PARTITION BY d.match_id, d.decision_status
                    ORDER BY COALESCE(d.published_at, d.as_of), d.decision_id
                ) AS rn
            FROM decisions d
            JOIN prediction_runs pr ON pr.prediction_run_id = d.prediction_run_id
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.decision_status IN ('BET', 'VETOED')
              AND d.market_type = 'MATCH_WINNER'
              AND d.selection_side IN (1, 2)
              AND {PRODUCTION_RUNS}
        )
        SELECT
            CASE WHEN c.decision_status = 'BET' THEN 'value_bet' ELSE 'vetoed' END AS population,
            c.match_id, c.decision_id, c.decision_status, c.selection_side AS selected_side,
            c.consensus_probability, c.market_decimal_price, c.published_at, c.as_of,
            c.edge_pct, c.reason_code,
            mv.player_1_id, mv.player_2_id,
            mv.match_start_at, mv.surface::text AS surface,
            mv.competition_tier::text AS tier, mv.best_of_sets, mv.round_code,
            mv.qualifying_flag, te.season_year, t.canonical_name AS tournament,
            p1.canonical_name AS player_1_name, p2.canonical_name AS player_2_name,
            r.outcome_status, r.winner_side, r.scoreline_text, r.as_of AS result_as_of,
            r.result_source_system,
            ds.settlement_status::text AS settlement_status,
            dva.veto_policy_version, dva.triggered_codes
        FROM candidates c
        LEFT JOIN LATERAL (
            SELECT mv0.* FROM match_versions mv0
            WHERE mv0.match_id = c.match_id
              AND mv0.as_of <= c.as_of
              AND mv0.source_observed_at <= c.as_of
            ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN matches m ON m.match_id = c.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        {LATEST_RESULT.format(match_id="c.match_id")}
        LEFT JOIN decision_settlements ds ON ds.decision_id = c.decision_id
        LEFT JOIN decision_veto_audits dva ON dva.decision_id = c.decision_id
        WHERE c.rn = 1
        ORDER BY c.decision_status, c.match_id
        """,
    )
    for row in rows:
        side = int(row["selected_side"])
        row["picked_player_id"] = int(row["player_1_id"] if side == 1 else row["player_2_id"])
        row["opponent_player_id"] = int(row["player_2_id"] if side == 1 else row["player_1_id"])
        row["picked_player_name"] = row["player_1_name"] if side == 1 else row["player_2_name"]
        row["opponent_player_name"] = row["player_2_name"] if side == 1 else row["player_1_name"]
        outcome = row.get("outcome_status")
        winner_side = row.get("winner_side")
        row["scored"] = outcome == "COMPLETED" and winner_side in (1, 2)
        row["won"] = row["scored"] and int(winner_side) == side
        row["void"] = outcome in ("RETIRED", "WALKOVER", "CANCELLED")
        row["dataset_schema_version"] = DATASET_SCHEMA_VERSION
    return rows


def build_rows(conn: psycopg.Connection) -> list[dict[str, Any]]:
    manager = load_manager_picks(conn)
    tracking = load_wide_spread_tracking(conn)
    picks = manager + tracking

    for row in picks:
        p1, p2 = int(row["player_1_id"]), int(row["player_2_id"])
        row["picked_player_id"] = p1 if int(row["selected_side"]) == 1 else p2
        row["opponent_player_id"] = p2 if int(row["selected_side"]) == 1 else p1
        row["picked_player_name"] = (
            row["player_1_name"] if int(row["selected_side"]) == 1 else row["player_2_name"]
        )
        row["opponent_player_name"] = (
            row["player_2_name"] if int(row["selected_side"]) == 1 else row["player_1_name"]
        )
    players = sorted(
        {int(r["player_1_id"]) for r in picks} | {int(r["player_2_id"]) for r in picks}
    )

    agents = load_agents(conn, picks)
    history = load_history(conn, players)
    history_match_ids = sorted({int(h["match_id"]) for h in history})
    stat_coverage = load_stat_coverage(conn, history_match_ids)
    market = load_market(conn, picks)
    decisions = load_decision_overlay(conn, picks)
    reruns = load_reruns(conn, picks)
    identity = load_identity(conn, players)

    for row in picks:
        match_id = int(row["match_id"])
        picked_at = row["picked_at"]
        surface = row.get("surface")
        row["agents"] = agents.get((int(row["prediction_run_id"]), match_id), {})
        row["history"] = {
            "picked": _history_features(
                history,
                player_id=int(row["picked_player_id"]),
                opponent_id=int(row["opponent_player_id"]),
                picked_at=picked_at,
                surface=surface,
                stat_coverage=stat_coverage,
            ),
            "opponent": _history_features(
                history,
                player_id=int(row["opponent_player_id"]),
                opponent_id=int(row["picked_player_id"]),
                picked_at=picked_at,
                surface=surface,
                stat_coverage=stat_coverage,
            ),
        }
        row["market"] = _market_features(row, market.get(match_id, {}))
        overlay = decisions.get(match_id, {})
        row["value_bet"] = {
            "is_value_bet": overlay.get("value_bet_decision_id") is not None,
            "decision_id": overlay.get("value_bet_decision_id"),
            "probability": (
                float(overlay["value_bet_probability"])
                if overlay.get("value_bet_probability") is not None
                else None
            ),
            "price": (
                float(overlay["value_bet_price"])
                if overlay.get("value_bet_price") is not None
                else None
            ),
            "published_at": overlay.get("value_bet_published_at"),
            "edge_pct": (
                float(overlay["value_bet_edge_pct"])
                if overlay.get("value_bet_edge_pct") is not None
                else None
            ),
            "is_vetoed": overlay.get("veto_decision_id") is not None,
            "veto_policy_version": overlay.get("veto_policy_version"),
            "veto_codes": overlay.get("triggered_codes"),
        }
        row["reruns"] = {
            "consensus_rows": int(reruns.get(match_id, {}).get("consensus_rows", 0)),
            "first_at": reruns.get(match_id, {}).get("first_at"),
            "last_at": reruns.get(match_id, {}).get("last_at"),
            "prob_min": (
                float(reruns[match_id]["prob_min"])
                if reruns.get(match_id, {}).get("prob_min") is not None
                else None
            ),
            "prob_max": (
                float(reruns[match_id]["prob_max"])
                if reruns.get(match_id, {}).get("prob_max") is not None
                else None
            ),
            "selected_side_count": int(reruns.get(match_id, {}).get("side_count", 0)),
        }
        outcome = row.get("outcome_status")
        winner_side = row.get("winner_side")
        scored = outcome == "COMPLETED" and winner_side in (1, 2)
        row["scored"] = scored
        row["won"] = scored and int(winner_side) == int(row["selected_side"])
        row["void"] = outcome in ("RETIRED", "WALKOVER", "CANCELLED")
        row["result_available_after_pick"] = (
            row.get("result_as_of") is not None and row["result_as_of"] > picked_at
        )
        row["data_quality"] = {
            "surface_missing": surface is None,
            "surface_conflicts_edition": (
                surface is not None
                and row.get("edition_surface") is not None
                and surface != row["edition_surface"]
            ),
            "tournament_name_has_qualification": "qualification" in str(row["tournament"]).lower(),
            "qualifying_flag": bool(row.get("qualifying_flag")),
            "name_flag_mismatch": (
                ("qualification" in str(row["tournament"]).lower())
                != bool(row.get("qualifying_flag"))
            ),
            "result_observation_count": int(row.get("result_observation_count") or 0),
            "result_states_disagree": int(row.get("distinct_result_states") or 0) > 1,
            "match_version_count": int(row.get("match_version_count") or 0),
            "participants_visible_at_pick": True,
            "identity": identity.get(int(row["player_1_id"]), {}),
            "identity_opponent": identity.get(int(row["player_2_id"]), {}),
            "pit_version_is_latest": True,
        }
        # Remove bulky/redundant fields that are already nested.
        for key in ("result_observation_count", "distinct_result_states", "match_version_count"):
            row.pop(key, None)
        row.pop("rn", None)
        row["dataset_schema_version"] = DATASET_SCHEMA_VERSION

    picks.extend(load_other_populations(conn))
    return picks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", default=EXTRACT_CUTOFF)
    parser.add_argument("--git-sha", default=None)
    args = parser.parse_args()
    cutoff = datetime.fromisoformat(args.cutoff)
    if args.git_sha:
        os.environ["TENNIS_RESEARCH_GIT_SHA"] = args.git_sha

    dsn = _dsn()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = '600s'")
        rows = build_rows(conn)

    for row in rows:
        print(json.dumps(row, sort_keys=True, default=_json_default))

    manifest = {
        "_manifest": True,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "extracted_at": datetime.now(UTC).isoformat(),
        "extract_cutoff": cutoff.isoformat(),
        "git_sha": _git_sha(),
        "row_count": len(rows),
        "population_counts": {
            population: sum(1 for r in rows if r["population"] == population)
            for population in sorted({r["population"] for r in rows})
        },
        "row_sha256": hashlib.sha256(
            "\n".join(_canonical(r) for r in rows).encode("utf-8")
        ).hexdigest(),
    }
    print(json.dumps(manifest, sort_keys=True, default=_json_default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
