"""Read-only historical execution of the three locked Agent 3 research profiles.

This is an empirical PIT-reconstruction harness, not a new model. It reuses the
existing Agent 3 production mathematics, the existing history loader, the
existing support-gate cohort definition and the existing `_soft_variant`
profile code. It never writes to the database, never calls a provider and never
changes a production prediction.

Cohort: first completed original run per canonical match, strict version
visibility (as_of/source_observed_at/ingested_at <= run as_of) and issue at
least 30 minutes before the recorded start. Outcomes are joined only after the
forecast is computed. Reconstruction is labelled AMBER: the stored history was
ingested retrospectively, so this is not a GREEN live-issued record.

Usage:
    set TENNIS_DATABASE_URL=...
    set PYTHONPATH=src
    python ops/validation/a3_profile_historical_execution_20261125.py \
        --start-utc 2026-09-01T00:00:00Z --end-utc 2026-11-25T00:00:00Z \
        --out-json <path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3HistorySnapshot,
    PairHistorySnapshot,
    PlayerHistorySnapshot,
    PredictionRequest,
    predict,
)
from sports_edge_tennis.data.agent3_history import (
    _apply_prepared_update,
    expire_history_state,
    load_agent3_history,
    prepare_historical_match_update,
)
from sports_edge_tennis.data.model_identity import resolve_model_player_ids
from sports_edge_tennis.validation.a3_soft_common_opponent_ess_shadow import (
    PROFILES,
    _soft_variant,
)

VERSION = "A3_PROFILE_HISTORICAL_EXECUTION_20261125_V1"
PREMATCH_MINUTES = 30
PROFILE_ORDER = tuple(PROFILES)


def _utc(value: datetime) -> datetime:
    if value is None or value.tzinfo is None:
        raise ValueError("timezone-aware timestamps required")
    return value.astimezone(UTC)


def _parse(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("UTC ISO-8601 Z timestamp required")
    return datetime.fromisoformat(value)


COHORT_SQL = """
WITH first_run AS (
  SELECT DISTINCT ON (t.match_id)
      t.match_id, pr.prediction_run_id, pr.as_of AS run_as_of,
      pr.prediction_finalized_at AS issued_at
  FROM prediction_run_targets t
  JOIN prediction_runs pr ON pr.prediction_run_id = t.prediction_run_id
  WHERE pr.run_status = 'COMPLETED'
    AND pr.prediction_finalized_at IS NOT NULL
    AND pr.experiment_id IS NULL
    AND pr.prediction_contract_version = 'predictive_agent_result_v1'
  ORDER BY t.match_id, pr.prediction_finalized_at, pr.prediction_run_id
), pit_version AS (
  SELECT fr.*, v.match_version_id, v.match_start_at, v.tour_scope,
         v.competition_tier, v.surface, v.round_code, v.best_of_sets,
         v.qualifying_flag, v.player_1_id, v.player_2_id
  FROM first_run fr
  LEFT JOIN LATERAL (
      SELECT mv.*
      FROM match_versions mv
      WHERE mv.match_id = fr.match_id
        AND mv.as_of <= fr.run_as_of
        AND mv.source_observed_at <= fr.run_as_of
        AND mv.ingested_at <= fr.run_as_of
      ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
      LIMIT 1
  ) v ON TRUE
), result AS (
  SELECT DISTINCT ON (mro.match_id)
      mro.match_id, mro.outcome_status, mro.winner_side,
      mro.source_observed_at AS result_source_observed_at,
      mro.as_of AS result_as_of, mro.ingested_at AS result_ingested_at
  FROM match_result_observations mro
  ORDER BY mro.match_id, mro.as_of DESC, mro.source_observed_at DESC,
           mro.match_result_observation_id DESC
)
SELECT pv.match_id, pv.prediction_run_id, pv.run_as_of, pv.issued_at,
       pv.match_version_id, pv.match_start_at, pv.tour_scope::text,
       pv.competition_tier::text, pv.surface::text, pv.round_code,
       pv.best_of_sets, pv.qualifying_flag, pv.player_1_id, pv.player_2_id,
       r.outcome_status::text, r.winner_side,
       a1.win_probability, a1.selection_side,
       a2.win_probability, a2.selection_side,
       a3.prediction_status::text, a3.win_probability, a3.selection_side,
       a3.abstain_reason_code, mgr.manager_status::text,
       diag.player_1_model_id, diag.player_2_model_id
FROM pit_version pv
LEFT JOIN result r ON r.match_id = pv.match_id
LEFT JOIN predictions a1 ON a1.prediction_run_id = pv.prediction_run_id
  AND a1.match_id = pv.match_id AND a1.agent_code = 'AGENT_1_SURFACE_ELO'
  AND a1.experiment_id IS NULL
LEFT JOIN predictions a2 ON a2.prediction_run_id = pv.prediction_run_id
  AND a2.match_id = pv.match_id AND a2.agent_code = 'AGENT_2_HIERARCHICAL_MARKOV'
  AND a2.experiment_id IS NULL
LEFT JOIN predictions a3 ON a3.prediction_run_id = pv.prediction_run_id
  AND a3.match_id = pv.match_id AND a3.agent_code = 'AGENT_3_INTRANSITIVITY'
  AND a3.experiment_id IS NULL
LEFT JOIN manager_consensus_results mgr
  ON mgr.prediction_run_id = pv.prediction_run_id AND mgr.match_id = pv.match_id
LEFT JOIN LATERAL (
  SELECT d.player_1_model_id, d.player_2_model_id
  FROM a3_support_diagnostics d
  WHERE d.match_id = pv.match_id
    AND d.evaluated_at >= pv.run_as_of
    AND d.evaluated_at <= pv.issued_at
  ORDER BY d.evaluated_at DESC, d.a3_support_diagnostic_id DESC
  LIMIT 1
) diag ON TRUE
WHERE pv.match_start_at >= %(start)s AND pv.match_start_at < %(end)s
  AND pv.match_start_at IS NOT NULL
  AND pv.run_as_of IS NOT NULL
  AND pv.issued_at IS NOT NULL
ORDER BY pv.run_as_of, pv.match_id
"""


def load_cohort(conn: psycopg.Connection, *, start: datetime, end: datetime) -> list[dict]:
    rows = conn.execute(COHORT_SQL, {"start": start, "end": end}).fetchall()
    keys = (
        "match_id", "prediction_run_id", "run_as_of", "issued_at", "match_version_id",
        "match_start_at", "tour", "tier", "surface", "round_code", "best_of_sets",
        "qualifying", "player_1_id", "player_2_id", "outcome", "winner_side",
        "a1_p", "a1_side", "a2_p", "a2_side", "a3_status", "a3_p", "a3_side",
        "a3_reason", "manager_status", "diag_p1_model", "diag_p2_model",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def _ingestion_maps(conn: psycopg.Connection) -> tuple[dict[int, datetime], dict[int, datetime]]:
    result_map = dict(
        conn.execute(
            "SELECT match_id, max(ingested_at) FROM match_result_observations GROUP BY match_id"
        ).fetchall()
    )
    version_map = dict(
        conn.execute(
            "SELECT match_id, max(ingested_at) FROM match_versions GROUP BY match_id"
        ).fetchall()
    )
    return result_map, version_map


def _player_snapshot(acc) -> PlayerHistorySnapshot:
    return PlayerHistorySnapshot(
        player_id=acc.player_id,
        all_observations=tuple(acc.all_observations),
        surface_observations={
            surface: tuple(values) for surface, values in acc.surface_observations.items()
        },
    )


def _restricted_snapshot(players, pairs, adjacency, target: dict) -> Agent3HistorySnapshot:
    p1, p2 = int(target["model_1_id"]), int(target["model_2_id"])
    adj1 = set(adjacency.get(p1, ()))
    adj2 = set(adjacency.get(p2, ()))
    shared = sorted((adj1 & adj2) - {p1, p2})
    pair_keys = {(min(p1, p2), max(p1, p2))}
    pair_keys.update((min(p1, o), max(p1, o)) for o in shared)
    pair_keys.update((min(p2, o), max(p2, o)) for o in shared)
    restricted_pairs = {}
    for key in pair_keys:
        acc = pairs.get(key)
        if acc is None:
            continue
        restricted_pairs[key] = PairHistorySnapshot(
            player_low_id=acc.player_low_id,
            player_high_id=acc.player_high_id,
            all_observations=tuple(acc.all_observations),
            surface_observations={
                surface: tuple(values)
                for surface, values in acc.surface_observations.items()
            },
        )
    restricted_players = {}
    for pid in (p1, p2):
        acc = players.get(pid)
        if acc is not None:
            restricted_players[pid] = _player_snapshot(acc)
    return Agent3HistorySnapshot(
        players=restricted_players,
        pairs=restricted_pairs,
        adjacency={p: tuple(sorted(neighbors)) for p, neighbors in ((p1, adj1), (p2, adj2))},
    )


def _p1_from_side(probability, side) -> float | None:
    if probability is None or side not in (1, 2):
        return None
    p = float(probability)
    return p if side == 1 else 1.0 - p


def _metrics(pairs: list[tuple[float, int]]) -> dict:
    n = len(pairs)
    if not n:
        return {"n": 0}
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    logloss = -sum(
        y * math.log(min(max(p, 1e-9), 1 - 1e-9))
        + (1 - y) * math.log(min(max(1 - p, 1e-9), 1 - 1e-9))
        for p, y in pairs
    ) / n
    accuracy = sum((p > 0.5) == (y == 1) for p, y in pairs) / n
    return {
        "n": n,
        "accuracy": round(accuracy, 8),
        "brier": round(brier, 8),
        "log_loss": round(logloss, 8),
    }


def _day_block_bootstrap(
    rows: list[dict], *, metric: str, samples: int = 1000, seed: int = 20261125
) -> dict:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[row["day_utc"]].append(row)
    days = sorted(by_day)
    if len(days) < 2:
        return {"status": "INSUFFICIENT_DAYS", "days": len(days)}
    rng = random.Random(seed)
    values = []
    for _ in range(samples):
        picked = [by_day[rng.choice(days)] for _ in days]
        flat = [r for group in picked for r in group]
        pairs = [(r["p1"], r["y"]) for r in flat if r["p1"] is not None]
        value = _metrics(pairs).get(metric)
        if value is not None:
            values.append(value)
    values.sort()
    point = _metrics([(r["p1"], r["y"]) for r in rows if r["p1"] is not None]).get(metric)
    return {
        "status": "OK",
        "days": len(days),
        "point": point,
        "p025": values[int(0.025 * (len(values) - 1))],
        "p975": values[int(0.975 * (len(values) - 1))],
    }


def execute(
    conn: psycopg.Connection, *, start: datetime, end: datetime,
    history_year_start: int = 2022, max_targets: int | None = None,
) -> dict:
    cohort = load_cohort(conn, start=start, end=end)
    result_ingested, version_ingested = _ingestion_maps(conn)

    eligible = []
    exclusions = Counter()
    for row in cohort:
        if row["match_start_at"] is None:
            exclusions["NO_PIT_VERSION"] += 1
        elif row["tour"] not in ("ATP_MAIN", "CHALLENGER"):
            exclusions["OUT_OF_SCOPE_TOUR"] += 1
        elif row["surface"] is None:
            exclusions["UNKNOWN_SURFACE"] += 1
        elif row["player_1_id"] is None or row["player_2_id"] is None or row["player_1_id"] == row["player_2_id"]:
            exclusions["BAD_PLAYER_IDENTITY"] += 1
        elif _utc(row["issued_at"]) > _utc(row["match_start_at"]) - timedelta(minutes=PREMATCH_MINUTES):
            exclusions["LATE_OR_UNSAFE_ISSUE"] += 1
        elif row["outcome"] != "COMPLETED" or row["winner_side"] not in (1, 2):
            exclusions["NOT_SETTLED_COMPLETED"] += 1
        else:
            row["run_as_of"] = _utc(row["run_as_of"])
            row["issued_at"] = _utc(row["issued_at"])
            row["match_start_at"] = _utc(row["match_start_at"])
            row["y"] = 1 if row["winner_side"] == 1 else 0
            row["day_utc"] = row["match_start_at"].date().isoformat()
            eligible.append(row)

    if max_targets is not None:
        eligible = eligible[:max_targets]

    max_cutoff = max(row["run_as_of"] for row in eligible)
    min_cutoff = min(row["run_as_of"] for row in eligible)
    target_player_ids = {
        int(pid)
        for row in eligible
        for pid in (row["player_1_id"], row["player_2_id"])
    }
    model_map = resolve_model_player_ids(
        conn, player_ids=target_player_ids, as_of=max_cutoff
    )
    identity_sources = Counter()
    resolved_eligible = []
    for row in eligible:
        p1, p2 = int(row["player_1_id"]), int(row["player_2_id"])
        m1 = int(model_map.get(p1, p1))
        m2 = int(model_map.get(p2, p2))
        diag1, diag2 = row.get("diag_p1_model"), row.get("diag_p2_model")
        if diag1 is not None and diag2 is not None and {int(diag1), int(diag2)} == {m1, m2}:
            identity_sources["DIAGNOSTIC_MATCH"] += 1
        else:
            identity_sources["RESOLVER_ONLY"] += 1
        if m1 <= 0 or m2 <= 0 or m1 == m2:
            exclusions["UNRESOLVED_MODEL_IDENTITY"] += 1
            continue
        row["model_1_id"] = m1
        row["model_2_id"] = m2
        resolved_eligible.append(row)
    eligible = resolved_eligible
    print(
        f"COHORT eligible={len(eligible)} identity={dict(identity_sources)}",
        file=sys.stderr,
        flush=True,
    )
    history = load_agent3_history(
        conn,
        cutoff=max_cutoff,
        tour_scopes=("ATP_MAIN", "CHALLENGER"),
        year_start=history_year_start,
    )
    # Keep the production loader's match-date visibility semantics. The stored
    # history is retrospective backfill; changing visibility to ingestion time
    # would compute an old match's residual from future form. Ingestion lateness
    # is measured below and reported as an AMBER limitation instead.
    ingestion = {
        "history_observations": len(history),
        "result_ingested_after_first_run": sum(
            1
            for o in history
            if result_ingested.get(o.match_id)
            and _utc(result_ingested[o.match_id]) > min_cutoff
        ),
        "version_ingested_after_first_run": sum(
            1
            for o in history
            if version_ingested.get(o.match_id)
            and _utc(version_ingested[o.match_id]) > min_cutoff
        ),
        "result_ingested_after_last_run": sum(
            1
            for o in history
            if result_ingested.get(o.match_id)
            and _utc(result_ingested[o.match_id]) > max_cutoff
        ),
        "first_run_cutoff_utc": min_cutoff.isoformat(),
        "last_run_cutoff_utc": max_cutoff.isoformat(),
    }
    print(f"HISTORY loaded={len(history)}", file=sys.stderr, flush=True)
    history.sort(key=lambda o: (o.visibility_cohort, o.match_id))

    runs: dict[tuple[datetime, int], list[dict]] = defaultdict(list)
    for row in eligible:
        runs[(row["run_as_of"], row["prediction_run_id"])].append(row)

    players: dict = {}
    pairs: dict = {}
    adjacency: dict = {}
    index = 0
    last_progress = 0
    profile_rows: dict[str, list[dict]] = {name: [] for name in PROFILE_ORDER}
    original_rows: list[dict] = []
    latencies: list[float] = []
    profile_latencies: dict[str, list[float]] = {name: [] for name in PROFILE_ORDER}

    for (run_as_of, run_id), targets in sorted(runs.items()):
        while index < len(history) and history[index].visibility_cohort < run_as_of:
            group_time = history[index].visibility_cohort
            group = []
            while index < len(history) and history[index].visibility_cohort == group_time:
                group.append(history[index])
                index += 1
            for observation in group:
                tiny = Agent3HistorySnapshot(
                    players={
                        pid: _player_snapshot(players[pid])
                        for pid in (observation.player_1_id, observation.player_2_id)
                        if pid in players
                    }
                )
                update = prepare_historical_match_update(observation, tiny)
                if update is not None:
                    _apply_prepared_update(players, pairs, adjacency, update)
            if index - last_progress >= 5000:
                last_progress = index
                print(f"APPLIED history={index}", file=sys.stderr, flush=True)
        expire_history_state(players, pairs, adjacency, cutoff=run_as_of)
        for target in targets:
            snapshot = _restricted_snapshot(players, pairs, adjacency, target)
            request = PredictionRequest(
                player_1_id=int(target["model_1_id"]),
                player_2_id=int(target["model_2_id"]),
                surface=target["surface"],
                history_snapshot=snapshot,
                as_of=run_as_of,
            )
            started = time.perf_counter()
            original = predict(request)
            latencies.append((time.perf_counter() - started) * 1000)
            original_rows.append(
                {
                    "match_id": target["match_id"],
                    "run_id": run_id,
                    "day_utc": target["day_utc"],
                    "tour": target["tour"],
                    "surface": target["surface"],
                    "qualifying": bool(target["qualifying"]),
                    "status": original.prediction_status,
                    "reason": original.abstain_reason.value if original.abstain_reason else None,
                    "p1": float(original.player_1_probability) if original.player_1_probability is not None else None,
                    "stored_status": target["a3_status"],
                    "stored_p1": _p1_from_side(target["a3_p"], target["a3_side"]),
                    "stored_reason": target["a3_reason"],
                    "y": target["y"],
                }
            )
            for name, (edge_min, cross_weight) in PROFILES.items():
                variant_started = time.perf_counter()
                result = _soft_variant(
                    request, edge_min=edge_min, cross_surface_weight=cross_weight
                )
                profile_latencies[name].append((time.perf_counter() - variant_started) * 1000)
                profile_rows[name].append(
                    {
                        "match_id": target["match_id"],
                        "run_id": run_id,
                        "day_utc": target["day_utc"],
                        "tour": target["tour"],
                        "surface": target["surface"],
                        "qualifying": bool(target["qualifying"]),
                        "status": result["status"],
                        "reason": result["reason"],
                        "p1": result.get("p1"),
                        "selected_side": result.get("selected_side"),
                        "raw_shared_opponents": result.get("raw_shared_opponents"),
                        "eligible_shared_opponents": result.get("eligible_shared_opponents"),
                        "kish_ess": result.get("kish_ess"),
                        "y": target["y"],
                        "a1_p": _p1_from_side(target["a1_p"], target["a1_side"]),
                        "a2_p": _p1_from_side(target["a2_p"], target["a2_side"]),
                        "manager_status": target["manager_status"],
                    }
                )

    def summarize_profile(rows: list[dict]) -> dict:
        predicted = [r for r in rows if r["status"] == "EXPERIMENTAL_PREDICTION" and r["p1"] is not None]
        abstained = [r for r in rows if r["status"] == "ABSTAINED"]
        reasons = Counter(r["reason"] or "NONE" for r in abstained)
        by_tour = {}
        for tour in ("ATP_MAIN", "CHALLENGER"):
            subset = [r for r in predicted if r["tour"] == tour]
            by_tour[tour] = _metrics([(r["p1"], r["y"]) for r in subset])
        by_surface = {}
        for surface in sorted({r["surface"] for r in predicted}):
            subset = [r for r in predicted if r["surface"] == surface]
            by_surface[surface] = _metrics([(r["p1"], r["y"]) for r in subset])
        by_format = {}
        for flag, label in ((False, "MAIN"), (True, "QUALIFYING")):
            subset = [r for r in predicted if r["qualifying"] is flag]
            by_format[label] = _metrics([(r["p1"], r["y"]) for r in subset])
        intersection = [
            r for r in predicted if r["a1_p"] is not None and r["a2_p"] is not None
        ]
        incremental = {
            "intersection_n": len(intersection),
            "profile": _metrics([(r["p1"], r["y"]) for r in intersection]),
            "agent1": _metrics([(r["a1_p"], r["y"]) for r in intersection]),
            "agent2": _metrics([(r["a2_p"], r["y"]) for r in intersection]),
        }
        if intersection:
            incremental["profile_minus_agent1_brier"] = round(
                incremental["profile"]["brier"] - incremental["agent1"]["brier"], 8
            )
            incremental["profile_minus_agent2_brier"] = round(
                incremental["profile"]["brier"] - incremental["agent2"]["brier"], 8
            )
        return {
            "settled_targets": len(rows),
            "predicted": len(predicted),
            "abstained": len(abstained),
            "coverage": round(len(predicted) / len(rows), 8) if rows else None,
            "abstain_reasons": dict(sorted(reasons.items())),
            "metrics": _metrics([(r["p1"], r["y"]) for r in predicted]),
            "by_tour": by_tour,
            "by_surface": by_surface,
            "by_draw": by_format,
            "incremental_vs_a1_a2": incremental,
            "day_block_bootstrap_brier": _day_block_bootstrap(predicted, metric="brier"),
        }

    original_predicted = [r for r in original_rows if r["status"] == "PREDICTED" and r["p1"] is not None]
    fidelity = None
    if original_predicted:
        matches = [r for r in original_predicted if r["stored_status"] == "PREDICTED" and r["stored_p1"] is not None]
        fidelity = {
            "reconstructed_predicted": len(original_predicted),
            "stored_predicted": sum(1 for r in original_rows if r["stored_status"] == "PREDICTED"),
            "both_predicted": len(matches),
            "max_abs_p1_delta_where_both": round(
                max(abs(r["p1"] - r["stored_p1"]) for r in matches), 8
            ) if matches else None,
            "status_agreement": round(
                sum(r["status"] == (r["stored_status"] or "MISSING") for r in original_rows)
                / len(original_rows),
                8,
            ) if original_rows else None,
        }

    def _blend_comparison(rows: list[dict]) -> dict:
        """One bounded equal-weight A1/A2 vs A1/A2/A3 comparison.

        Main draw only, identical intersection, identical coverage and no fitted
        weights. A3 contributes one third of the arithmetic mean. This is a
        comparison, not a new Agent 3 architecture.
        """
        main_draw = [
            r for r in rows
            if not r["qualifying"]
            and r["a1_p"] is not None
            and r["a2_p"] is not None
            and r["p1"] is not None
        ]
        a12 = [((r["a1_p"] + r["a2_p"]) / 2.0, r["y"]) for r in main_draw]
        a123 = [((r["a1_p"] + r["a2_p"] + r["p1"]) / 3.0, r["y"]) for r in main_draw]
        baseline = _metrics(a12)
        with_a3 = _metrics(a123)
        delta = (
            round(with_a3["brier"] - baseline["brier"], 8)
            if baseline.get("n") and with_a3.get("n") else None
        )
        by_day: dict[str, list[dict]] = defaultdict(list)
        for row in main_draw:
            by_day[row["day_utc"]].append(row)
        days = sorted(by_day)
        bootstrap = {"status": "INSUFFICIENT_DAYS", "days": len(days)}
        if len(days) >= 2:
            rng = random.Random(20261125)
            deltas = []
            for _ in range(1000):
                picked = [by_day[rng.choice(days)] for _ in days]
                flat = [r for group in picked for r in group]
                b12 = _metrics([
                    ((r["a1_p"] + r["a2_p"]) / 2.0, r["y"]) for r in flat
                ])
                b123 = _metrics([
                    ((r["a1_p"] + r["a2_p"] + r["p1"]) / 3.0, r["y"]) for r in flat
                ])
                if b12.get("n") and b123.get("n"):
                    deltas.append(b123["brier"] - b12["brier"])
            deltas.sort()
            if deltas:
                bootstrap = {
                    "status": "OK",
                    "days": len(days),
                    "point": delta,
                    "p025": round(deltas[int(0.025 * (len(deltas) - 1))], 8),
                    "p975": round(deltas[int(0.975 * (len(deltas) - 1))], 8),
                }
        return {
            "scope": "MAIN_DRAW_EQUAL_WEIGHT_INTERSECTION",
            "main_draw_settled_targets": sum(
                1 for r in rows if not r["qualifying"]
            ),
            "a1_a2": baseline,
            "a1_a2_a3": with_a3,
            "a1_a2_a3_minus_a1_a2_brier": delta,
            "a1_a2_a3_minus_a1_a2_log_loss": (
                round(with_a3["log_loss"] - baseline["log_loss"], 8)
                if baseline.get("n") and with_a3.get("n") else None
            ),
            "paired_day_block_bootstrap_brier_delta": bootstrap,
            "weights_fitted": False,
            "a3_weight": 1 / 3,
        }

    report = {
        "version": VERSION,
        "main_draw_blend_comparison": _blend_comparison(
            profile_rows["original_edge_threshold_diagnostic"]
        ),
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "cohort_rows": len(cohort),
        "settled_eligible_targets": len(eligible),
        "unique_matches": len({r["match_id"] for r in eligible}),
        "exclusions": dict(sorted(exclusions.items())),
        "model_identity_sources": dict(sorted(identity_sources.items())),
        "run_cutoffs": len(runs),
        "history_observations_visible": len(history),
        "history_ingestion_coverage": ingestion,
        "history_visibility_max_utc": max_cutoff.isoformat(),
        "profiles": {name: summarize_profile(profile_rows[name]) for name in PROFILE_ORDER},
        "original_agent3_control": {
            "predicted": len(original_predicted),
            "metrics": _metrics([(r["p1"], r["y"]) for r in original_predicted]),
            "stored_fidelity": fidelity,
        },
        "latency_ms": {
            "original_agent3": {
                "n": len(latencies),
                "p50": round(statistics.median(latencies), 3),
                "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 3),
                "max": round(max(latencies), 3),
            },
            **{
                name: {
                    "n": len(values),
                    "p50": round(statistics.median(values), 3),
                    "p95": round(sorted(values)[int(0.95 * (len(values) - 1))], 3),
                    "max": round(max(values), 3),
                }
                for name, values in profile_latencies.items()
            },
        },
        "cohort_sha256": hashlib.sha256(
            "\n".join(
                f"{r['match_id']}|{r['prediction_run_id']}|{r['run_as_of'].isoformat()}"
                for r in sorted(eligible, key=lambda x: (x["match_id"], x["prediction_run_id"]))
            ).encode()
        ).hexdigest(),
        "status": "AMBER_RECONSTRUCTED_PIT_HISTORICAL_EXECUTION",
        "not_green_live_issued": True,
        "not_commercially_verified_dataset": True,
        "no_data_writes": True,
        "no_provider_calls": True,
        "production_observer_disabled": True,
        "no_production_manager_change": True,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--out-json")
    parser.add_argument("--history-year-start", type=int, default=2022)
    parser.add_argument("--max-targets", type=int)
    args = parser.parse_args(argv)
    start, end = _parse(args.start_utc), _parse(args.end_utc)
    if not start < end or (end - start).days > 120:
        parser.error("bounded window required")
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='600s'")
        report = execute(
            conn, start=start, end=end,
            history_year_start=args.history_year_start,
            max_targets=args.max_targets,
        )
    text = json.dumps(report, sort_keys=True, indent=2, default=str)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(
        "TENNIS_A3_PROFILE_HISTORICAL_EXECUTION "
        + json.dumps(
            {
                "out_json": args.out_json,
                "settled_eligible_targets": report["settled_eligible_targets"],
                "profiles": {
                    name: {
                        "predicted": value["predicted"],
                        "abstained": value["abstained"],
                        "coverage": value["coverage"],
                        "metrics": value["metrics"],
                    }
                    for name, value in report["profiles"].items()
                },
                "original_agent3_predicted": report["original_agent3_control"]["predicted"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
