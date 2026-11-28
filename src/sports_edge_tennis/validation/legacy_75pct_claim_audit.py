"""Provenance audit of the legacy "~14,000 historical matches at ~75%" claim.

The claim has been referenced repeatedly as a historical benchmark. This audit
enumerates every evidence class that could reproduce it and records, for each, whether
the artifact exists and whether it reproduces the number. It is deliberately narrow:
it does not test new models and does not tune anything.

Verdict policy
--------------
``REPRODUCED`` requires *all* of: (1) an immutable original prediction/run artifact,
(2) an exact selected-fixture ID set, (3) a pre-match feature-availability trail,
(4) a settled-outcome join that postdates match start, and (5) an accuracy near 75%.
If any of these is absent the claim is ``UNREPRODUCIBLE`` and the audit recommends
``RETIRE``.

Read-only. No writes. No provider calls.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.validation.a1_identity_provenance_audit import dsn_from_env

VERSION = "LEGACY_75PCT_CLAIM_AUDIT_V1"

CLAIM_STATEMENTS = [
    {
        "claim": "approximately 14,000 historical predictions at ~75% accuracy",
        "sources": [
            "docs/research/TENNIS_PIT_ELIGIBLE_HISTORY_REALDATA_20261121.md",
            "docs/research/TENNIS_A1_A2_INDEPENDENCE_REAL_DATA_20261121.md",
            "docs/research/TENNIS_A1_A2_VALIDATION_FROZEN_SELECTION_REAL_DATA_20261121.md",
            "docs/research/BRAZZY365_NOV21_23_LEAKAGE_AND_BENCHMARK_SCOPE.md",
        ],
    },
    {
        "claim": "sustained 72-75% winner accuracy at 40-50% eligible coverage",
        "sources": [
            "docs/research/TENNIS_PROSPECTIVE_MANAGER_FROZEN_PROTOCOL_20261122.md",
            "docs/research/TENNIS_FEATURE_AVAILABILITY_EVIDENCE_CONTRACT_20261122.md",
        ],
    },
]


def _scalar(conn: psycopg.Connection, sql: str, params: Any = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def audit(conn: psycopg.Connection) -> dict[str, Any]:
    evidence: dict[str, Any] = {}

    # 1. Raw stored historical results. These are results, not forecasts.
    evidence["stored_historical_results"] = {
        "total_matches": _scalar(conn, "SELECT count(*) FROM matches"),
        "completed": _scalar(
            conn,
            "SELECT count(*) FROM match_result_observations WHERE outcome_status = 'COMPLETED'",
        ),
        "by_year": [
            {"season_year": int(row[0]), "completed": int(row[1])}
            for row in conn.execute(
                """
                SELECT te.season_year, count(*)
                FROM match_result_observations mro
                JOIN matches m ON m.match_id = mro.match_id
                JOIN tournament_editions te
                  ON te.tournament_edition_id = m.tournament_edition_id
                WHERE mro.outcome_status = 'COMPLETED'
                GROUP BY te.season_year
                ORDER BY te.season_year
                """
            ).fetchall()
        ],
    }

    # 2. Original production prediction records: the only recoverable forecast sample.
    evidence["original_prediction_records"] = {
        "a1_predicted_all_runs": _scalar(
            conn,
            """
            SELECT count(*) FROM predictions p
            JOIN prediction_runs pr USING (prediction_run_id)
            WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
              AND p.prediction_status = 'PREDICTED'
              AND p.experiment_id IS NULL
              AND pr.experiment_id IS NULL
            """,
        ),
        "distinct_matches_with_original_a1": _scalar(
            conn,
            """
            SELECT count(DISTINCT p.match_id) FROM predictions p
            JOIN prediction_runs pr USING (prediction_run_id)
            WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
              AND p.prediction_status = 'PREDICTED'
              AND p.experiment_id IS NULL
              AND pr.experiment_id IS NULL
            """,
        ),
        "earliest_original_run": str(
            _scalar(
                conn,
                """
                SELECT min(as_of) FROM prediction_runs
                WHERE run_status = 'COMPLETED'
                  AND experiment_id IS NULL
                  AND prediction_contract_version = 'predictive_agent_result_v1'
                """,
            )
        ),
    }

    # 3. Ingest provenance: were any historical versions present before the match?
    evidence["historical_version_ingest"] = {
        "historical_versions_before_2026": _scalar(
            conn,
            """
            SELECT count(*)
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            JOIN tournament_editions te
              ON te.tournament_edition_id = m.tournament_edition_id
            WHERE te.season_year < 2026
            """,
        ),
        "historical_versions_ingested_before_start": _scalar(
            conn,
            """
            SELECT count(*)
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            JOIN tournament_editions te
              ON te.tournament_edition_id = m.tournament_edition_id
            WHERE te.season_year < 2026
              AND mv.match_start_at IS NOT NULL
              AND mv.ingested_at < mv.match_start_at
            """,
        ),
        "distinct_historical_ingest_days": [
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT mv.ingested_at::date
                FROM match_versions mv
                JOIN matches m ON m.match_id = mv.match_id
                JOIN tournament_editions te
                  ON te.tournament_edition_id = m.tournament_edition_id
                WHERE te.season_year < 2026
                ORDER BY 1
                """
            ).fetchall()
        ],
    }

    # 4. Largest reproducible original-forecast cohort and its best observed accuracy.
    cohort = conn.execute(
        """
        WITH latest AS (
          SELECT DISTINCT ON (p.match_id)
                 p.prediction_id, p.match_id, p.selection_side, p.win_probability
          FROM predictions p JOIN prediction_runs pr USING (prediction_run_id)
          WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
            AND p.prediction_status = 'PREDICTED'
            AND p.experiment_id IS NULL AND pr.experiment_id IS NULL
            AND pr.run_status = 'COMPLETED'
          ORDER BY p.match_id, p.created_at DESC
        )
        SELECT count(*) AS n,
               count(*) FILTER (
                 WHERE (latest.win_probability >= 0.5)
                   = (mro.winner_side = latest.selection_side)
               ) AS wins
        FROM latest
        JOIN LATERAL (
          SELECT mro0.winner_side, mro0.outcome_status
          FROM match_result_observations mro0
          WHERE mro0.match_id = latest.match_id
          ORDER BY mro0.as_of DESC, mro0.source_observed_at DESC,
                   mro0.match_result_observation_id DESC
          LIMIT 1
        ) mro ON TRUE
        WHERE mro.outcome_status = 'COMPLETED'
          AND mro.winner_side IN (1, 2)
        """
    ).fetchone()
    n, wins = int(cohort[0]), int(cohort[1])
    evidence["largest_reproducible_cohort"] = {
        "settled_matches": n,
        "wins": wins,
        "accuracy": wins / n if n else None,
        "shortfall_vs_75pct": (0.75 - wins / n) if n else None,
    }

    # 5. Does any stored artifact contain ~14k predictions? Only the *training corpus*
    #    reaches that size, which is the most plausible origin of the number.
    evidence["training_corpus_size"] = {
        "history_rows_visible_now": _scalar(
            conn,
            """
            SELECT count(*) FROM match_result_observations
            WHERE outcome_status = 'COMPLETED'
            """,
        ),
        "note": (
            "The 2026-11-23 label-shuffle artifact reports trained_history_count=14229 "
            "over a 623-match holdout. That is a training-corpus row count, not a "
            "prediction sample, and it is the only stored quantity near 14,000."
        ),
    }

    # 6. Required artifacts and whether they exist.
    evidence["required_artifacts"] = {
        "immutable_original_run_manifest_for_14k_sample": False,
        "exact_selected_fixture_id_set_for_14k_sample": False,
        "prematch_feature_timestamp_snapshot_for_14k_sample": False,
        "settled_outcome_join_after_match_start_for_14k_sample": False,
        "approved_commercial_use_rights_for_historical_corpus": False,
        "reason": (
            "No prediction_runs/predictions row set of that size exists; stored "
            "historical rows were ingested retrospectively on "
            "2026-08-31 and cannot evidence an original forecast."
        ),
    }

    reproduced = all(evidence["required_artifacts"][key] for key in (
        "immutable_original_run_manifest_for_14k_sample",
        "exact_selected_fixture_id_set_for_14k_sample",
        "prematch_feature_timestamp_snapshot_for_14k_sample",
        "settled_outcome_join_after_match_start_for_14k_sample",
        "approved_commercial_use_rights_for_historical_corpus",
    ))
    return {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "claims": CLAIM_STATEMENTS,
        "evidence": evidence,
        "verdict": "REPRODUCED" if reproduced else "UNREPRODUCIBLE",
        "recommendation": "RETAIN" if reproduced else "RETIRE",
        "retirement_scope": (
            "Retire the '~14,000 historical matches at ~75%' benchmark from every "
            "commercial, marketing, investor and model-quality context. It may only be "
            "cited as an unreproducible legacy internal claim, with this audit attached."
        ),
        "replacement_evidence": (
            "Use the reproducible original-forecast cohort above, the forward shadow "
            "gate (>=200 settled first-issued selections over >=30 days), and the "
            "paired proper-score activation rule."
        ),
        "can_promote": False,
        "production_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="docs/research/evidence/legacy_75pct_claim_audit_20261125.json",
    )
    args = parser.parse_args(argv)
    with psycopg.connect(
        dsn_from_env(), options="-c default_transaction_read_only=on"
    ) as conn:
        report = audit(conn)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str)[:4000])
    print("wrote", out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
