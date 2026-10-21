"""Read-only extraction for the Agent 3 investigation and ensemble shadow.

The extractor never writes to the tennis database. It sets
``default_transaction_read_only = on`` before the first query and emits JSONL
rows plus a manifest containing counts and SHA-256 hashes.

The raw JSONL files are intentionally not committed. They contain production
prediction rows and are reproducible from this script and the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

EXPERIMENT_ID = "tennis_agent3_ensemble_20261021"
DATASET_SCHEMA_VERSION = "tennis_agent3_ensemble_extract_v1"

A3_EVALUATIONS_QUERY = """
WITH pivot AS (
    SELECT
        match_id,
        prediction_run_id,
        max(prediction_status::text) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_status,
        max(selection_side) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_side,
        max(win_probability) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_probability,
        max(prediction_status::text) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_status,
        max(selection_side) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_side,
        max(win_probability) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_probability,
        max(prediction_status::text) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_status,
        max(selection_side) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_side,
        max(win_probability) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_probability
    FROM predictions
    WHERE as_of <= %(cutoff)s
      AND agent_code IN (
          'AGENT_1_SURFACE_ELO',
          'AGENT_2_HIERARCHICAL_MARKOV',
          'AGENT_3_INTRANSITIVITY'
      )
    GROUP BY match_id, prediction_run_id
),
result AS (
    SELECT DISTINCT ON (match_id)
        match_id,
        outcome_status::text AS outcome_status,
        winner_side,
        scoreline_text,
        as_of AS result_as_of,
        source_observed_at AS result_source_observed_at
    FROM match_result_observations
    WHERE as_of <= %(cutoff)s
    ORDER BY match_id, as_of DESC, source_observed_at DESC, match_result_observation_id DESC
)
SELECT
    p.prediction_id,
    p.match_id,
    p.prediction_run_id,
    p.as_of,
    p.prediction_status::text AS a3_status,
    p.selection_side AS a3_side,
    p.win_probability AS a3_probability,
    p.model_version AS a3_model_version,
    p.abstain_reason_code AS a3_abstain_reason,
    pv.a1_status,
    pv.a1_side,
    pv.a1_probability,
    pv.a2_status,
    pv.a2_side,
    pv.a2_probability,
    pv.a3_status AS pivot_a3_status,
    pv.a3_side AS pivot_a3_side,
    pv.a3_probability AS pivot_a3_probability,
    r.outcome_status,
    r.winner_side,
    r.scoreline_text,
    mv.surface::text AS surface,
    mv.competition_tier::text AS competition_tier,
    mv.player_1_id,
    mv.player_2_id,
    te.edition_name AS tournament_name,
    te.season_year
FROM predictions p
JOIN pivot pv
  ON pv.match_id = p.match_id
 AND pv.prediction_run_id = p.prediction_run_id
LEFT JOIN result r ON r.match_id = p.match_id
LEFT JOIN LATERAL (
    SELECT surface, competition_tier, player_1_id, player_2_id
    FROM match_versions mv
    WHERE mv.match_id = p.match_id
      AND mv.as_of <= p.as_of
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
    LIMIT 1
) mv ON TRUE
LEFT JOIN matches m ON m.match_id = p.match_id
LEFT JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
WHERE p.agent_code = 'AGENT_3_INTRANSITIVITY'
  AND p.as_of <= %(cutoff)s
ORDER BY p.match_id, p.as_of, p.prediction_id
"""

A3_DIAGNOSTICS_QUERY = """
SELECT
    d.a3_support_diagnostic_id,
    d.match_id,
    d.evaluated_at,
    d.player_1_model_id,
    d.player_2_model_id,
    d.identity_status,
    d.direct_raw_support,
    d.direct_effective_support,
    d.raw_shared_opponents,
    d.eligible_shared_opponents,
    d.q_count,
    d.support_path,
    d.gate_failed,
    d.final_status,
    d.graph_degree_player1,
    d.graph_degree_player2,
    d.history_count_player1,
    d.history_count_player2,
    mv.surface::text AS surface,
    mv.competition_tier::text AS competition_tier,
    mv.player_1_id AS version_player_1_id,
    mv.player_2_id AS version_player_2_id,
    te.edition_name AS tournament_name,
    te.season_year
FROM a3_support_diagnostics d
LEFT JOIN LATERAL (
    SELECT surface, competition_tier, player_1_id, player_2_id
    FROM match_versions mv
    WHERE mv.match_id = d.match_id
      AND mv.as_of <= d.evaluated_at
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
    LIMIT 1
) mv ON TRUE
LEFT JOIN matches m ON m.match_id = d.match_id
LEFT JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
WHERE d.evaluated_at <= %(cutoff)s
ORDER BY d.evaluated_at, d.match_id, d.a3_support_diagnostic_id
"""

ENSEMBLE_ROWS_QUERY = """
WITH first_manager AS (
    SELECT DISTINCT ON (match_id)
        manager_consensus_result_id,
        prediction_run_id,
        match_id,
        manager_policy_version_id,
        manager_status::text AS manager_status,
        selected_side AS manager_selected_side,
        consensus_probability AS manager_consensus_probability,
        active_agent_count,
        abstaining_agent_count,
        probability_spread AS manager_probability_spread,
        reason_code::text AS manager_reason_code,
        as_of AS manager_as_of
    FROM manager_consensus_results
    WHERE as_of <= %(cutoff)s
    ORDER BY match_id, as_of, manager_consensus_result_id
),
pivot AS (
    SELECT
        match_id,
        prediction_run_id,
        max(prediction_status::text) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_status,
        max(selection_side) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_side,
        max(win_probability) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_probability,
        max(abstain_reason_code) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_abstain_reason,
        max(prediction_status::text) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_status,
        max(selection_side) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_side,
        max(win_probability) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_probability,
        max(abstain_reason_code) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_abstain_reason,
        max(prediction_status::text) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_status,
        max(selection_side) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_side,
        max(win_probability) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_probability,
        max(abstain_reason_code) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_abstain_reason
    FROM predictions
    WHERE as_of <= %(cutoff)s
      AND agent_code IN (
          'AGENT_1_SURFACE_ELO',
          'AGENT_2_HIERARCHICAL_MARKOV',
          'AGENT_3_INTRANSITIVITY'
      )
    GROUP BY match_id, prediction_run_id
),
result AS (
    SELECT DISTINCT ON (match_id)
        match_id,
        outcome_status::text AS outcome_status,
        winner_side,
        scoreline_text,
        as_of AS result_as_of,
        source_observed_at AS result_source_observed_at
    FROM match_result_observations
    WHERE as_of <= %(cutoff)s
    ORDER BY match_id, as_of DESC, source_observed_at DESC, match_result_observation_id DESC
),
version AS (
    SELECT DISTINCT ON (fm.match_id)
        fm.match_id,
        mv.surface::text AS surface,
        mv.competition_tier::text AS competition_tier,
        mv.player_1_id,
        mv.player_2_id,
        mv.match_start_at,
        mv.qualifying_flag,
        te.edition_name AS tournament_name,
        te.season_year
    FROM first_manager fm
    JOIN match_versions mv
      ON mv.match_id = fm.match_id
     AND mv.as_of <= fm.manager_as_of
    LEFT JOIN matches m ON m.match_id = fm.match_id
    LEFT JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
    ORDER BY fm.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
)
SELECT
    fm.*,
    pv.a1_status,
    pv.a1_side,
    pv.a1_probability,
    pv.a1_abstain_reason,
    pv.a2_status,
    pv.a2_side,
    pv.a2_probability,
    pv.a2_abstain_reason,
    pv.a3_status,
    pv.a3_side,
    pv.a3_probability,
    pv.a3_abstain_reason,
    r.outcome_status,
    r.winner_side,
    r.scoreline_text,
    r.result_as_of,
    r.result_source_observed_at,
    v.surface,
    v.competition_tier,
    v.player_1_id,
    v.player_2_id,
    v.match_start_at,
    v.qualifying_flag,
    v.tournament_name,
    v.season_year,
    mpv.policy_version,
    mpv.allow_agent3_abstention,
    mpv.three_agent_max_probability_spread,
    mpv.agent3_abstention_max_probability_spread,
    mpv.min_active_agent_probability
FROM first_manager fm
JOIN pivot pv
  ON pv.match_id = fm.match_id
 AND pv.prediction_run_id = fm.prediction_run_id
JOIN manager_policy_versions mpv
  ON mpv.manager_policy_version_id = fm.manager_policy_version_id
LEFT JOIN result r ON r.match_id = fm.match_id
LEFT JOIN version v ON v.match_id = fm.match_id
ORDER BY fm.match_id
"""


def _connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn)
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET statement_timeout = '600s'")
    return conn


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            line = json.dumps(row, default=_json_default, sort_keys=True, ensure_ascii=True)
            handle.write(line)
            handle.write("\n")
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _rows_as_dicts(cursor: psycopg.Cursor) -> list[dict[str, Any]]:
    columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def extract(output_dir: Path, *, cutoff: datetime) -> dict[str, Any]:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("TENNIS_DATABASE_URL or DATABASE_URL is required")

    output_dir.mkdir(parents=True, exist_ok=True)
    params = {"cutoff": cutoff}
    with _connect(dsn) as conn:
        a3_evaluations = _rows_as_dicts(conn.execute(A3_EVALUATIONS_QUERY, params))
        a3_diagnostics = _rows_as_dicts(conn.execute(A3_DIAGNOSTICS_QUERY, params))
        ensemble_rows = _rows_as_dicts(conn.execute(ENSEMBLE_ROWS_QUERY, params))

    files = {
        "a3_evaluations.jsonl": _write_jsonl(output_dir / "a3_evaluations.jsonl", a3_evaluations),
        "a3_diagnostics.jsonl": _write_jsonl(output_dir / "a3_diagnostics.jsonl", a3_diagnostics),
        "ensemble_rows.jsonl": _write_jsonl(output_dir / "ensemble_rows.jsonl", ensemble_rows),
    }
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "cutoff": cutoff.isoformat(),
        "extraction_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "source_database": "railway",
        "counts": {
            "a3_evaluations": len(a3_evaluations),
            "a3_diagnostics": len(a3_diagnostics),
            "ensemble_rows": len(ensemble_rows),
        },
        "files": files,
        "queries_sha256": {
            "a3_evaluations": hashlib.sha256(A3_EVALUATIONS_QUERY.encode()).hexdigest(),
            "a3_diagnostics": hashlib.sha256(A3_DIAGNOSTICS_QUERY.encode()).hexdigest(),
            "ensemble_rows": hashlib.sha256(ENSEMBLE_ROWS_QUERY.encode()).hexdigest(),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--cutoff",
        type=datetime.fromisoformat,
        default=datetime(2026, 10, 21, 23, 59, 59, tzinfo=timezone.utc),
    )
    args = parser.parse_args()
    manifest = extract(args.output_dir, cutoff=args.cutoff)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
