"""Read-only fixture-set handshake for the A1 research populations.

The A1 identity-bridge workstream declares three evaluation populations:
3,152 (retrospective comparison), 3,201 (equal-coverage settled targets) and
3,223 (four-way safely completed). This script reproduces the four-way target
query from that workstream's read-only harness, emits the exact match-id sets,
and measures their intersection with the 2026 Australian Open fixture set.

It does not replay A1, write a prediction or assert that the declared counts are
frozen. The declared counts belong to the A1 workstream; this script provides the
current database reproduction and the hashes needed for an exact comparison.

    $env:TENNIS_DATABASE_URL = <authorised read-only DSN>
    python ops/validation/ao_research_population_handshake_20261126.py \
      --out-json docs/research/evidence/ao_research_population_handshake_20261126.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg

VERSION = "AO_RESEARCH_POPULATION_HANDSHAKE_20261126_V1"
SINCE = datetime(2026, 9, 10, tzinfo=UTC)
DECLARED = (3152, 3201, 3223)

# Copied from the A1 workstream's read-only four-way harness. It is deliberately
# the same definition, not a new population.
FOUR_WAY_SQL = """
WITH a1 AS (
  SELECT DISTINCT ON (p.match_id)
         p.prediction_id, p.match_id, p.prediction_run_id, p.as_of,
         p.selection_side, p.win_probability, pr.prediction_finalized_at
  FROM predictions p JOIN prediction_runs pr USING (prediction_run_id)
  WHERE p.agent_code='AGENT_1_SURFACE_ELO'
    AND p.prediction_status='PREDICTED'
    AND p.experiment_id IS NULL AND pr.experiment_id IS NULL
    AND pr.run_status='COMPLETED'
    AND p.as_of >= %(since)s
  ORDER BY p.match_id, p.created_at DESC
),
earliest AS (
  SELECT DISTINCT ON (p.match_id) p.match_id, p.prediction_id
  FROM predictions p JOIN prediction_runs pr USING (prediction_run_id)
  WHERE p.agent_code='AGENT_1_SURFACE_ELO'
    AND p.prediction_status='PREDICTED'
    AND p.experiment_id IS NULL AND pr.experiment_id IS NULL
    AND pr.run_status='COMPLETED'
  ORDER BY p.match_id, pr.prediction_finalized_at, p.as_of, p.prediction_id
)
SELECT a1.prediction_id, a1.match_id, a1.prediction_run_id, a1.as_of,
       mv.match_version_id, mv.player_1_id, mv.player_2_id,
       mv.surface::text, te.surface::text, t.tournament_id, t.canonical_name,
       mv.tour_scope::text, mv.competition_tier::text, te.season_year,
       mv.best_of_sets, mv.qualifying_flag, mv.match_start_at,
       (a1.prediction_id = e.prediction_id) AS is_earliest,
       r.outcome_status::text, r.winner_side
FROM a1
JOIN prediction_run_targets prt
  ON prt.prediction_run_id=a1.prediction_run_id AND prt.match_id=a1.match_id
JOIN earliest e ON e.match_id = a1.match_id
JOIN LATERAL (
  SELECT mv0.* FROM match_versions mv0
  WHERE mv0.match_id=a1.match_id
    AND mv0.source_observed_at <= a1.as_of
    AND mv0.as_of <= a1.as_of
    AND mv0.ingested_at <= a1.as_of
  ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
           mv0.ingested_at DESC, mv0.match_version_id DESC
  LIMIT 1
) mv ON TRUE
JOIN matches m ON m.match_id=a1.match_id
JOIN tournament_editions te USING (tournament_edition_id)
JOIN tournaments t USING (tournament_id)
LEFT JOIN LATERAL (
  SELECT mro.outcome_status, mro.winner_side
  FROM match_result_observations mro
  WHERE mro.match_id=a1.match_id
  ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
           mro.ingested_at DESC, mro.match_result_observation_id DESC
  LIMIT 1
) r ON TRUE
ORDER BY a1.as_of, a1.match_id
"""

AO_2026_SQL = """
SELECT DISTINCT m.match_id
FROM matches m
JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
JOIN tournaments t ON t.tournament_id=te.tournament_id
WHERE te.season_year=2026 AND t.canonical_name ILIKE '%australian%'
ORDER BY 1
"""


def _sha256_ids(ids) -> str:
    joined = ",".join(str(value) for value in sorted(ids))
    return hashlib.sha256(joined.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")

    with psycopg.connect(
        dsn,
        options="-c default_transaction_read_only=on -c statement_timeout=300000",
        connect_timeout=20,
    ) as conn:
        rows = conn.execute(FOUR_WAY_SQL, {"since": SINCE}).fetchall()
        ao_ids = {int(row[0]) for row in conn.execute(AO_2026_SQL).fetchall()}

    target_ids = {int(row[1]) for row in rows}
    settled_ids = {
        int(row[1])
        for row in rows
        if row[18] == "COMPLETED" and row[19] in (1, 2)
    }
    earliest_ids = {int(row[1]) for row in rows if row[17] is True}

    report = {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "declared_populations": list(DECLARED),
        "declared_scope": "A1 targets since 2026-09-10 (A1 identity-bridge workstream).",
        "reproduced": {
            "targets": len(target_ids),
            "settled_completed": len(settled_ids),
            "earliest_issued_targets": len(earliest_ids),
            "target_set_sha256": _sha256_ids(target_ids),
            "settled_set_sha256": _sha256_ids(settled_ids),
        },
        "ao_2026": {
            "fixture_ids_count": len(ao_ids),
            "fixture_set_sha256": _sha256_ids(ao_ids),
            "fixture_ids": sorted(ao_ids),
            "intersection_with_targets": sorted(ao_ids & target_ids),
            "intersection_with_settled": sorted(ao_ids & settled_ids),
        },
        "comparison_status": (
            "PREPARED, NOT CONFIRMED. Exact equality to 3,152/3,201/3,223 "
            "requires the A1 workstream's frozen fixture-id manifest. The "
            "current reproduction and AO intersection are emitted so the sets "
            "can be compared without ambiguity."
        ),
        "interpretation": (
            "The AO 2026 fixtures are January 2026. The declared A1 populations "
            "start 2026-09-10, so AO cannot be an evaluation target in them. If "
            "the frozen manifests differ from this reproduction, reconcile the "
            "difference before citing a population count."
        ),
    }
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "wrote": str(out),
                "targets": len(target_ids),
                "settled": len(settled_ids),
                "ao_fixtures": len(ao_ids),
                "ao_intersection": len(ao_ids & target_ids),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
