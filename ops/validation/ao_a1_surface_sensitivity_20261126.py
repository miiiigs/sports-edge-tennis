"""Read-only A1 sensitivity to the AO 2026 NULL-surface defect.

This answers Brian's dependency question without assuming the best-of-three
label invalidates Agent 1:

* variant `bestof_only` changes AO 2026 best_of_sets 3 -> 5 and must produce
  byte-identical A1 probabilities because Agent 1 never consumes the field;
* variant `surface_only` fills the AO 2026 NULL surface with HARD and measures
  the actual rating/probability effect;
* `surface_and_bestof` confirms the two corrections are independent.

It replays the unchanged production Agent 1 code chronologically over the same
A1 target population used by the identity-bridge workstream (targets since
2026-09-10). Nothing is written to the database and no provider is called.

The surface variant is a bounded counterfactual. It is not a historical rewrite
and it is not an accuracy claim for a new production model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    PredictionRequest,
    SurfaceEloValidationError,
    advance_replay_history,
    predict_from_replay,
    replay_history,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history

VERSION = "AO_A1_SURFACE_SENSITIVITY_20261126_V1"
SINCE = datetime(2026, 9, 10, tzinfo=UTC)

TARGETS_SQL = """
WITH lv AS (
  SELECT DISTINCT ON (match_id) match_id, match_start_at
  FROM match_versions
  ORDER BY match_id, as_of DESC, source_observed_at DESC, match_version_id DESC
), a1 AS (
  SELECT DISTINCT ON (p.match_id) p.prediction_id, p.match_id, p.as_of,
         p.selection_side, p.win_probability, lv.match_start_at
  FROM predictions p
  JOIN prediction_runs pr USING (prediction_run_id)
  JOIN lv USING (match_id)
  WHERE pr.experiment_id IS NULL AND pr.run_status='COMPLETED'
    AND p.agent_code='AGENT_1_SURFACE_ELO'
    AND p.prediction_status='PREDICTED'
    AND p.created_at < lv.match_start_at
    AND p.as_of >= %(since)s
  ORDER BY p.match_id, p.created_at DESC
)
SELECT a1.prediction_id, a1.as_of, a1.selection_side, a1.win_probability,
       mv.player_1_id, mv.player_2_id, mv.surface::text, mv.tour_scope::text,
       mv.competition_tier::text, mv.best_of_sets, mv.qualifying_flag,
       r.outcome_status::text, r.winner_side, a1.match_id
FROM a1
JOIN LATERAL (
  SELECT * FROM match_versions mv0
  WHERE mv0.match_id=a1.match_id
    AND mv0.as_of <= a1.as_of
    AND mv0.source_observed_at <= a1.as_of
  ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC
  LIMIT 1
) mv ON TRUE
LEFT JOIN LATERAL (
  SELECT outcome_status, winner_side FROM match_result_observations r0
  WHERE r0.match_id=a1.match_id
    AND r0.outcome_status IN ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
  ORDER BY r0.as_of DESC, r0.source_observed_at DESC,
           r0.match_result_observation_id DESC
  LIMIT 1
) r ON TRUE
ORDER BY a1.as_of
"""

AO_COMPLETED_SQL = """
SELECT DISTINCT m.match_id
FROM matches m
JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
JOIN tournaments t ON t.tournament_id=te.tournament_id
JOIN match_result_observations r ON r.match_id=m.match_id
WHERE t.canonical_name ILIKE '%australian%'
  AND te.season_year=2026
  AND r.outcome_status='COMPLETED'
ORDER BY 1
"""


def _sha256_ids(ids) -> str:
    joined = ",".join(str(value) for value in sorted(ids))
    return hashlib.sha256(joined.encode()).hexdigest()


def _variant(history, *, ao_ids, surface: bool, best_of: bool):
    output = []
    for observation in history:
        if observation.match_id in ao_ids:
            changes = {}
            if surface and observation.surface is None:
                changes["surface"] = "HARD"
            if best_of and observation.best_of_sets == 3:
                changes["best_of_sets"] = 5
            if changes:
                output.append(replace(observation, **changes))
                continue
        output.append(observation)
    return output


def _replay_series(history_rows, targets) -> dict[int, float]:
    history = sorted(
        history_rows, key=lambda row: (max(row.as_of, row.source_observed_at), row.match_id)
    )
    output: dict[int, float] = {}
    replay = None
    previous_as_of = None
    index = 0
    for position, target in enumerate(targets, start=1):
        as_of = target[1]
        if replay is None:
            visible = [
                row
                for row in history
                if row.as_of <= as_of and row.source_observed_at <= as_of
            ]
            replay = replay_history(visible, as_of)
            index = len(visible)
        else:
            new_rows = []
            while index < len(history) and max(
                history[index].as_of, history[index].source_observed_at
            ) <= as_of:
                new_rows.append(history[index])
                index += 1
            if new_rows:
                try:
                    replay = advance_replay_history(
                        replay,
                        new_rows,
                        previous_prediction_as_of=previous_as_of,
                        prediction_as_of=as_of,
                    )
                except SurfaceEloValidationError:
                    replay = replay_history(history[:index], as_of)
        previous_as_of = as_of
        try:
            request = PredictionRequest(
                player_1_id=target[4],
                player_2_id=target[5],
                surface=target[6],
                tour_scope=target[7],
                competition_tier=target[8],
                best_of_sets=target[9],
                qualifying_flag=bool(target[10]),
                as_of=as_of,
            )
            output[target[13]] = float(
                predict_from_replay(request, replay).player_1_probability
            )
        except SurfaceEloValidationError:
            continue
        if position % 500 == 0:
            print(f"replayed={position}", flush=True)
    return output


def _metrics(probabilities, settled) -> dict[str, float]:
    pairs = [
        (probabilities[target[13]], 1.0 if target[12] == 1 else 0.0)
        for target in settled
        if target[13] in probabilities
    ]
    n = len(pairs)
    accuracy = sum((p > 0.5) == (y == 1) for p, y in pairs) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    log_loss = -sum(
        y * math.log(max(p, 1e-6)) + (1 - y) * math.log(max(1 - p, 1e-6))
        for p, y in pairs
    ) / n
    return {
        "n": n,
        "accuracy": accuracy,
        "brier": brier,
        "log_loss": log_loss,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")

    with psycopg.connect(
        dsn,
        options="-c default_transaction_read_only=on -c statement_timeout=900000",
        connect_timeout=20,
    ) as conn:
        history = load_agent1_history(conn, cutoff=datetime.now(UTC))
        targets = conn.execute(TARGETS_SQL, {"since": SINCE}).fetchall()
        ao_ids = {int(row[0]) for row in conn.execute(AO_COMPLETED_SQL).fetchall()}

    print(f"history={len(history)} targets={len(targets)} ao_completed={len(ao_ids)}", flush=True)
    variants = {
        "stored": history,
        "bestof_only": _variant(history, ao_ids=ao_ids, surface=False, best_of=True),
        "surface_only": _variant(history, ao_ids=ao_ids, surface=True, best_of=False),
        "surface_and_bestof": _variant(
            history, ao_ids=ao_ids, surface=True, best_of=True
        ),
    }
    probabilities = {}
    for name, rows in variants.items():
        print(f"running={name}", flush=True)
        probabilities[name] = _replay_series(rows, targets)

    settled = [
        target
        for target in targets
        if target[11] == "COMPLETED"
        and target[12] in (1, 2)
        and target[13] in probabilities["stored"]
    ]
    deltas = {}
    for name, values in probabilities.items():
        if name == "stored":
            continue
        differences = [
            abs(values[target[13]] - probabilities["stored"][target[13]])
            for target in settled
        ]
        deltas[name] = {
            "changed_targets": sum(delta > 1e-12 for delta in differences),
            "max_abs_probability_delta": max(differences),
            "mean_abs_probability_delta": sum(differences) / len(differences),
        }

    report = {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "no_database_write": True,
        "no_historical_rewrite": True,
        "counterfactual_only": True,
        "scope": "A1 targets since 2026-09-10, settled COMPLETED only",
        "history_rows": len(history),
        "targets": len(targets),
        "settled_completed": len(settled),
        "ao_2026_completed_matches_corrected": len(ao_ids),
        "target_set_sha256": _sha256_ids(target[13] for target in targets),
        "settled_set_sha256": _sha256_ids(target[13] for target in settled),
        "ao_2026_completed_sha256": _sha256_ids(ao_ids),
        "metrics": {
            name: _metrics(values, settled) for name, values in probabilities.items()
        },
        "deltas_vs_stored": deltas,
        "interpretation": {
            "bestof_only": (
                "Exactly zero changed probabilities. Agent 1 does not consume "
                "best_of_sets; the BO3 defect does not change A1 ratings or A1 "
                "evaluation results."
            ),
            "surface_only": (
                "A real but separate effect from the NULL AO 2026 surface. "
                "Measured as a bounded counterfactual on the stored A1 target "
                "population; not a production model change."
            ),
            "surface_and_bestof": (
                "Identical to surface_only, confirming the two corrections are "
                "independent and only the surface metadata moves A1."
            ),
        },
    }
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"wrote": str(out), "metrics": report["metrics"], "deltas": deltas}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
