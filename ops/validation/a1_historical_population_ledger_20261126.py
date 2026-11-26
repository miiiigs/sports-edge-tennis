"""Frozen per-target ledger for the three historical Agent 1 experiments.

Read-only. Produces one JSONL row per (experiment, target) with stored,
unbridged-replay and bridged-replay probabilities, identity provenance and the
Agent 1 rating diagnostics needed for mechanism attribution.

Experiment populations are frozen by prediction visibility and result-observation
visibility, not by re-running "now":

* E2 (earlier Model Picks / equal coverage): predictions created <= 2026-11-24T09:00Z,
  results observed <= the same cutoff. Reproduces 3,725 targets / 3,201 settled.
* E3 (latest four-way): predictions created <= 2026-11-25T07:20Z, results observed
  <= the same cutoff. Reproduces 3,776 targets / 3,223 settled.

* E1 (earlier Agent 1): the same target population as E2 but the 2026-11-24T08:10Z
  settlement snapshot, before the 08:14Z 52-result batch settled 49 more matches.
  Reproduces 3,152 settled.

The ledger records the two primary variants; the analysis script applies the frozen
settlement cutoffs and the historical metric conventions.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sports_edge_tennis.agents.agent1_surface_elo import (  # noqa: E402
    DEFAULT_CONFIG,
    PredictionRequest,
    advance_replay_history,
    effective_rating,
    predict_from_replay,
    replay_history,
    resolved_player_states,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history  # noqa: E402
from sports_edge_tennis.data.model_identity import (  # noqa: E402
    resolve_model_player_ids_with_provenance,
)

SINCE = datetime(2026, 9, 10, tzinfo=UTC)
E2_CUTOFF = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
E3_CUTOFF = datetime(2026, 11, 25, 7, 20, tzinfo=UTC)
SURFACE_PROBE = ROOT / "docs" / "research" / "evidence" / "a1_surface_production_style_20261125.json"

E2_TARGET_SQL = """
WITH lv AS (
  SELECT DISTINCT ON (match_id) match_id, match_start_at
  FROM match_versions
  ORDER BY match_id, as_of DESC, source_observed_at DESC, match_version_id DESC
), a1 AS (
  SELECT DISTINCT ON (p.match_id)
         p.prediction_id, p.match_id, p.prediction_run_id, p.as_of, p.created_at,
         p.selection_side, p.win_probability, p.model_version, p.feature_version,
         lv.match_start_at
  FROM predictions p
  JOIN prediction_runs pr USING (prediction_run_id)
  JOIN lv USING (match_id)
  WHERE pr.experiment_id IS NULL AND pr.run_status='COMPLETED'
    AND p.agent_code='AGENT_1_SURFACE_ELO' AND p.prediction_status='PREDICTED'
    AND p.created_at < lv.match_start_at AND p.as_of >= %(since)s
    AND p.created_at <= %(cutoff)s
  ORDER BY p.match_id, p.created_at DESC
)
SELECT a1.prediction_id, a1.match_id, a1.prediction_run_id, a1.as_of, a1.created_at,
       a1.selection_side, a1.win_probability, a1.model_version, a1.feature_version,
       a1.match_start_at,
       mv.match_version_id, mv.player_1_id, mv.player_2_id, mv.surface::text,
       mv.tour_scope::text, mv.competition_tier::text, mv.best_of_sets,
       mv.qualifying_flag,
       a2.prediction_status::text, a2.selection_side, a2.win_probability,
       a3.prediction_status::text,
       r.outcome_status::text, r.winner_side, r.created_at,
       (a1.prediction_id = e.prediction_id) AS is_earliest
FROM a1
LEFT JOIN LATERAL (
  SELECT mv0.* FROM match_versions mv0
  WHERE mv0.match_id=a1.match_id
    AND mv0.as_of <= a1.as_of AND mv0.source_observed_at <= a1.as_of
  ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC
  LIMIT 1
) mv ON TRUE
LEFT JOIN LATERAL (
  SELECT p0.prediction_id FROM predictions p0 JOIN prediction_runs pr0 USING (prediction_run_id)
  WHERE p0.match_id=a1.match_id AND p0.agent_code='AGENT_1_SURFACE_ELO'
    AND p0.prediction_status='PREDICTED' AND p0.experiment_id IS NULL
    AND pr0.experiment_id IS NULL AND pr0.run_status='COMPLETED'
  ORDER BY pr0.prediction_finalized_at, p0.as_of, p0.prediction_id LIMIT 1
) e ON TRUE
LEFT JOIN predictions a2
  ON a2.prediction_run_id=a1.prediction_run_id AND a2.match_id=a1.match_id
 AND a2.agent_code='AGENT_2_HIERARCHICAL_MARKOV'
LEFT JOIN predictions a3
  ON a3.prediction_run_id=a1.prediction_run_id AND a3.match_id=a1.match_id
 AND a3.agent_code='AGENT_3_INTRANSITIVITY'
LEFT JOIN LATERAL (
  SELECT mro.outcome_status, mro.winner_side, mro.created_at
  FROM match_result_observations mro
  WHERE mro.match_id=a1.match_id AND mro.created_at <= %(cutoff)s
  ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
           mro.match_result_observation_id DESC LIMIT 1
) r ON TRUE
ORDER BY a1.as_of, a1.match_id
"""

E3_TARGET_SQL = """
WITH a1 AS (
  SELECT DISTINCT ON (p.match_id)
         p.prediction_id, p.match_id, p.prediction_run_id, p.as_of, p.created_at,
         p.selection_side, p.win_probability, p.model_version, p.feature_version,
         pr.prediction_finalized_at
  FROM predictions p JOIN prediction_runs pr USING (prediction_run_id)
  WHERE p.agent_code='AGENT_1_SURFACE_ELO' AND p.prediction_status='PREDICTED'
    AND p.experiment_id IS NULL AND pr.experiment_id IS NULL AND pr.run_status='COMPLETED'
    AND p.as_of >= %(since)s AND p.created_at <= %(cutoff)s
  ORDER BY p.match_id, p.created_at DESC
), earliest AS (
  SELECT DISTINCT ON (p.match_id) p.match_id, p.prediction_id
  FROM predictions p JOIN prediction_runs pr USING (prediction_run_id)
  WHERE p.agent_code='AGENT_1_SURFACE_ELO' AND p.prediction_status='PREDICTED'
    AND p.experiment_id IS NULL AND pr.experiment_id IS NULL AND pr.run_status='COMPLETED'
  ORDER BY p.match_id, pr.prediction_finalized_at, p.as_of, p.prediction_id
)
SELECT a1.prediction_id, a1.match_id, a1.prediction_run_id, a1.as_of,
       a1.prediction_finalized_at, a1.selection_side, a1.win_probability,
       a1.created_at, a1.model_version, a1.feature_version,
       mv.match_version_id, mv.player_1_id, mv.player_2_id,
       mv.surface::text AS live_surface, te.surface::text AS edition_surface,
       t.tournament_id, t.canonical_name, mv.tour_scope::text,
       mv.competition_tier::text, te.season_year, mv.best_of_sets,
       mv.qualifying_flag, mv.match_start_at,
       (a1.prediction_id = e.prediction_id) AS is_earliest,
       a2.prediction_status::text AS a2_status, a2.selection_side AS a2_side,
       a2.win_probability AS a2_probability, a3.prediction_status::text AS a3_status,
       r.outcome_status::text AS outcome_status, r.winner_side, r.created_at AS result_created_at
FROM a1
JOIN prediction_run_targets prt
  ON prt.prediction_run_id=a1.prediction_run_id AND prt.match_id=a1.match_id
JOIN earliest e ON e.match_id = a1.match_id
JOIN LATERAL (
  SELECT mv0.* FROM match_versions mv0
  WHERE mv0.match_id=a1.match_id
    AND mv0.source_observed_at <= a1.as_of AND mv0.as_of <= a1.as_of
    AND mv0.ingested_at <= a1.as_of
  ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
           mv0.ingested_at DESC, mv0.match_version_id DESC LIMIT 1
) mv ON TRUE
JOIN matches m ON m.match_id=a1.match_id
JOIN tournament_editions te USING (tournament_edition_id)
JOIN tournaments t USING (tournament_id)
LEFT JOIN predictions a2
  ON a2.prediction_run_id=a1.prediction_run_id AND a2.match_id=a1.match_id
 AND a2.agent_code='AGENT_2_HIERARCHICAL_MARKOV'
LEFT JOIN predictions a3
  ON a3.prediction_run_id=a1.prediction_run_id AND a3.match_id=a1.match_id
 AND a3.agent_code='AGENT_3_INTRANSITIVITY'
LEFT JOIN LATERAL (
  SELECT mro.outcome_status, mro.winner_side, mro.created_at
  FROM match_result_observations mro
  WHERE mro.match_id=a1.match_id AND mro.created_at <= %(cutoff)s
  ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
           mro.ingested_at DESC, mro.match_result_observation_id DESC LIMIT 1
) r ON TRUE
ORDER BY a1.as_of, a1.match_id
"""


def _production_surface_map() -> dict[int, tuple[str | None, str]]:
    probe = json.loads(SURFACE_PROBE.read_text(encoding="utf-8"))
    return {
        int(r["prediction_id"]): (r.get("production_surface"), r.get("production_status") or "UNKNOWN")
        for r in probe
    }


def _resolution_map(conn, targets, cutoff, *, use_asof: bool):
    by_key: dict[Any, list] = defaultdict(list)
    for t in targets:
        key = t["as_of"] if use_asof else cutoff
        by_key[key].append(t)
    resolved: dict[Any, dict[int, Any]] = {}
    for key, group in by_key.items():
        ids = {t["p1_live"] for t in group} | {t["p2_live"] for t in group}
        result = resolve_model_player_ids_with_provenance(
            conn, player_ids=ids, as_of=key, reference_policy="permissive"
        )
        resolved[key] = result
    return resolved


def _replay_pass(history, targets, resolved, surface_map, *, id_policy: str, surface_key: str):
    hist = sorted(history, key=lambda o: (max(o.as_of, o.source_observed_at), o.match_id))
    out: dict[int, dict[str, Any]] = {}
    replay = None
    index = 0
    previous = None
    for t in sorted(targets, key=lambda x: (x["as_of"], x["match_id"])):
        as_of = t["as_of"]
        if replay is None:
            visible = [o for o in hist if o.as_of <= as_of and o.source_observed_at <= as_of]
            replay = replay_history(visible, as_of)
            index = len(visible)
        else:
            new = []
            while index < len(hist) and max(hist[index].as_of, hist[index].source_observed_at) <= as_of:
                new.append(hist[index])
                index += 1
            if new:
                try:
                    replay = advance_replay_history(
                        replay, new, previous_prediction_as_of=previous, prediction_as_of=as_of
                    )
                except Exception:
                    replay = replay_history(hist[:index], as_of)
        previous = as_of
        if id_policy == "pit":
            ids = resolved.get(as_of, {})
        else:
            ids = resolved
        p1_id = ids.get(t["p1_live"])
        p1_id = int(p1_id.resolved_player_id) if hasattr(p1_id, "resolved_player_id") else int(p1_id or t["p1_live"])
        p2_id = ids.get(t["p2_live"])
        p2_id = int(p2_id.resolved_player_id) if hasattr(p2_id, "resolved_player_id") else int(p2_id or t["p2_live"])
        if p1_id == p2_id:
            p1_id, p2_id = t["p1_live"], t["p2_live"]
        if surface_key == "production":
            # The probe file contains only the 579 targets whose live version has no
            # surface. Every other target's production surface is the live surface.
            surface = surface_map.get(t["prediction_id"], (t.get("raw_surface"), "LIVE"))[0]
        else:
            surface = t.get("raw_surface")
        try:
            request = PredictionRequest(
                player_1_id=p1_id, player_2_id=p2_id, surface=surface,
                tour_scope=t["tour_scope"], competition_tier=t["competition_tier"],
                best_of_sets=int(t["best_of_sets"]), qualifying_flag=bool(t["qualifying_flag"]),
                as_of=as_of,
            )
            prediction = predict_from_replay(request, replay, DEFAULT_CONFIG)
            state1, state2 = resolved_player_states(request, replay, DEFAULT_CONFIG)
            out[t["prediction_id"]] = {
                "p1": float(prediction.player_1_probability),
                "side": int(prediction.selection_side),
                "p1_id": p1_id,
                "p2_id": p2_id,
                "player_1_overall_matches": prediction.diagnostics.player_1_overall_matches,
                "player_2_overall_matches": prediction.diagnostics.player_2_overall_matches,
                "player_1_surface_matches": prediction.diagnostics.player_1_surface_matches,
                "player_2_surface_matches": prediction.diagnostics.player_2_surface_matches,
                "player_1_cold_start": prediction.diagnostics.player_1_cold_start,
                "player_2_cold_start": prediction.diagnostics.player_2_cold_start,
                "player_1_surface_cold_start": prediction.diagnostics.player_1_surface_cold_start,
                "player_2_surface_cold_start": prediction.diagnostics.player_2_surface_cold_start,
                "eligible_history_match_count": prediction.diagnostics.eligible_history_match_count,
                "player_1_overall_rating": float(state1.overall_rating),
                "player_2_overall_rating": float(state2.overall_rating),
                "player_1_surface_rating": float(state1.surface_rating(surface, DEFAULT_CONFIG)) if surface else None,
                "player_2_surface_rating": float(state2.surface_rating(surface, DEFAULT_CONFIG)) if surface else None,
                "player_1_effective_rating": float(effective_rating(state1, surface, DEFAULT_CONFIG)),
                "player_2_effective_rating": float(effective_rating(state2, surface, DEFAULT_CONFIG)),
                "surface": surface,
            }
        except Exception as exc:  # one fixture must not abort the ledger
            out[t["prediction_id"]] = {"error": type(exc).__name__}
    return out


def _row_base(t, experiment):
    return {
        "experiment": experiment,
        "prediction_id": int(t["prediction_id"]),
        "match_id": int(t["match_id"]),
        "prediction_run_id": int(t["prediction_run_id"]),
        "match_version_id": int(t["match_version_id"]) if t.get("match_version_id") else None,
        "as_of": t["as_of"].isoformat(),
        "prediction_created_at": t["created_at"].isoformat() if t.get("created_at") else None,
        "match_start_at": t["match_start_at"].isoformat() if t.get("match_start_at") else None,
        "season_year": t.get("season_year"),
        "tour": t["tour_scope"],
        "competition_tier": t["competition_tier"],
        "best_of_sets": int(t["best_of_sets"]),
        "qualifying_flag": bool(t["qualifying_flag"]),
        "is_earliest": bool(t.get("is_earliest")),
        "p1_live": int(t["p1_live"]),
        "p2_live": int(t["p2_live"]),
        "stored_side": int(t["stored_side"]),
        "stored_p1": float(t["stored_p1"]),
        "outcome_status": t.get("outcome_status"),
        "winner_side": t.get("winner_side"),
        "y": (1 if t.get("winner_side") == 1 else 0) if t.get("winner_side") in (1, 2) else None,
        "raw_surface": t.get("raw_surface"),
    }


def _normalise_e2(rows):
    out = []
    for r in rows:
        (
            prediction_id, match_id, prediction_run_id, as_of, created_at, side, prob,
            model_version, feature_version, match_start_at, match_version_id, p1, p2,
            surface, tour_scope, competition_tier, best_of_sets, qualifying_flag,
            a2_status, a2_side, a2_prob, a3_status, outcome_status, winner_side,
            result_created_at, is_earliest,
        ) = r
        out.append({
            "prediction_id": prediction_id, "match_id": match_id,
            "prediction_run_id": prediction_run_id, "as_of": as_of, "created_at": created_at,
            "stored_side": side, "stored_p1": float(prob) if side == 1 else 1 - float(prob),
            "model_version": model_version, "feature_version": feature_version,
            "match_start_at": match_start_at, "match_version_id": match_version_id,
            "p1_live": p1, "p2_live": p2, "raw_surface": surface,
            "tour_scope": tour_scope, "competition_tier": competition_tier,
            "best_of_sets": best_of_sets, "qualifying_flag": qualifying_flag,
            "a2_status": a2_status, "a2_side": a2_side, "a2_prob": float(a2_prob) if a2_prob is not None else None,
            "a3_status": a3_status, "outcome_status": outcome_status,
            "winner_side": winner_side, "result_created_at": result_created_at,
            "is_earliest": is_earliest, "season_year": None,
        })
    return out


def _normalise_e3(rows):
    out = []
    for r in rows:
        (
            prediction_id, match_id, prediction_run_id, as_of, finalized_at, side, prob,
            created_at, model_version, feature_version, match_version_id, p1, p2,
            live_surface, edition_surface, tournament_id, canonical_name, tour_scope,
            competition_tier, season_year, best_of_sets, qualifying_flag, match_start_at,
            is_earliest, a2_status, a2_side, a2_prob, a3_status, outcome_status,
            winner_side, result_created_at,
        ) = r
        out.append({
            "prediction_id": prediction_id, "match_id": match_id,
            "prediction_run_id": prediction_run_id, "as_of": as_of, "created_at": created_at,
            "stored_side": side, "stored_p1": float(prob) if side == 1 else 1 - float(prob),
            "model_version": model_version, "feature_version": feature_version,
            "match_start_at": match_start_at, "match_version_id": match_version_id,
            "p1_live": p1, "p2_live": p2, "raw_surface": live_surface,
            "tour_scope": tour_scope, "competition_tier": competition_tier,
            "best_of_sets": best_of_sets, "qualifying_flag": qualifying_flag,
            "a2_status": a2_status, "a2_side": a2_side, "a2_prob": float(a2_prob) if a2_prob is not None else None,
            "a3_status": a3_status, "outcome_status": outcome_status,
            "winner_side": winner_side, "result_created_at": result_created_at,
            "is_earliest": is_earliest, "season_year": season_year,
        })
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        dsn = Path(r"C:\Users\Desktop 2\AppData\Local\Temp\opencode\tennis_dsn.txt").read_text(encoding="utf-8").strip()
    with psycopg.connect(dsn, autocommit=True, options="-c default_transaction_read_only=on -c statement_timeout=1800000") as conn:
        e2 = _normalise_e2(conn.execute(E2_TARGET_SQL, {"since": SINCE, "cutoff": E2_CUTOFF}).fetchall())
        e3 = _normalise_e3(conn.execute(E3_TARGET_SQL, {"since": SINCE, "cutoff": E3_CUTOFF}).fetchall())
        if args.limit:
            e2 = e2[: args.limit]
            e3 = e3[: args.limit]
        print(f"targets E2={len(e2)} E3={len(e3)}", flush=True)
        surface_map = _production_surface_map()
        # Resolve IDs once per population.
        e2_res = _resolution_map(conn, e2, E2_CUTOFF, use_asof=False)
        e2_res = e2_res[E2_CUTOFF]
        e3_res = _resolution_map(conn, e3, E3_CUTOFF, use_asof=True)
        print("resolved", flush=True)
        e2_out = {}
        for name, bridged in (("shadow_current", False), ("shadow_bridged", True)):
            hist = load_agent1_history(conn, cutoff=E2_CUTOFF, bridge_model_identities=bridged, ingested_by=E2_CUTOFF)
            probs = _replay_pass(hist, e2, e2_res, surface_map, id_policy="now", surface_key="raw")
            e2_out[name] = probs
            print(name, "n=", len(probs), flush=True)
        e3_out = {}
        for name, bridged in (("A", False), ("B", True)):
            hist = load_agent1_history(conn, cutoff=E3_CUTOFF, bridge_model_identities=bridged, ingested_by=E3_CUTOFF)
            probs = _replay_pass(hist, e3, e3_res, surface_map, id_policy="pit", surface_key="production")
            e3_out[name] = probs
            print(name, "n=", len(probs), flush=True)

    rows = []
    for experiment, targets, variants, res in (("E2", e2, e2_out, e2_res), ("E3", e3, e3_out, e3_res)):
        for t in targets:
            row = _row_base(t, experiment)
            for name, probs in variants.items():
                p = probs.get(t["prediction_id"], {})
                row[name] = p
            # identity provenance for target players
            for slot, live in (("p1", t["p1_live"]), ("p2", t["p2_live"])):
                r = res.get(live) if experiment == "E2" else res.get(t["as_of"], {}).get(live)
                row[f"{slot}_resolution"] = (
                    {
                        "resolved_player_id": int(r.resolved_player_id),
                        "method": r.method,
                        "evidence": r.evidence,
                        "name_source": r.name_source,
                        "candidate_count": r.candidate_count,
                        "matched_alias": r.matched_alias,
                        "bridge_event_id": r.bridge_event_id,
                        "bridged": bool(r.bridged),
                    }
                    if r is not None else None
                )
            rows.append(row)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    print("wrote", out, "rows", len(rows), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
