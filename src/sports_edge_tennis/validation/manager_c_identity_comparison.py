"""Brian's fixed Manager C identity comparison (A versus B).

A: stored original Agent 1 + stored Agent 2/3 + the existing Manager C rule.
B: identity-corrected Agent 1 + the identical stored Agent 2/3 + the identical
   Manager C rule.

Both arms use the same eligible fixtures, original forecast cutoffs, frozen
20%/40% thresholds and the registered uncalibrated mean policy. Nothing is
retrained, refrozen, rescored prospectively or written. Agent 1 is replayed with
the production code; the replay's reproduction of the stored A1 forecast is
reported so harness error is not presented as an identity effect.

The requested experiment needs a model execution for B. It is therefore also
reported as A' (unbridged replay) versus B (bridged replay), which isolates the
identity change from harness error.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    PredictionRequest,
    SurfaceEloValidationError,
    advance_replay_history,
    predict_from_replay,
    replay_history,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.model_identity import resolve_model_player_ids
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES,
    _available_agents,
    _policy,
    _utc,
    _wilson,
)
from sports_edge_tennis.validation.manager_c_challenger_history import (
    _sha,
    read_source,
)
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY
from sports_edge_tennis.validation.manager_c_original_source import (
    HISTORICAL_AS_OF,
    TRAIN_START,
    VALIDATION_END,
    select_cohorts,
)

VERSION = "MANAGER_C_IDENTITY_COMPARISON_V1"
ITERATIONS = 4000
SEED = 20261126
A1 = CODES[0]


def _p1(side: int | None, probability: float | None) -> float | None:
    if side not in (1, 2) or probability is None:
        return None
    return float(probability) if side == 1 else 1 - float(probability)


def _agent_evidence(raw_rows: list[tuple]) -> dict[int, dict[str, tuple[int, float]]]:
    grouped: dict[int, dict[str, tuple[str, int | None, float | None]]] = defaultdict(dict)
    for row in raw_rows:
        mid, code = int(row[0]), row[6]
        if code is None:
            continue
        if code not in CODES or code in grouped[mid]:
            raise ValueError("duplicate or unexpected original agent")
        grouped[mid][code] = (
            str(row[7]),
            int(row[8]) if row[8] is not None else None,
            float(row[9]) if row[9] is not None else None,
        )
    return {mid: _available_agents(preds) for mid, preds in grouped.items()}


def _target_metadata(conn: psycopg.Connection, targets: list[dict]) -> dict[int, dict]:
    ids = [t["match_id"] for t in targets]
    as_ofs = [t["a1_as_of"] for t in targets]
    rows = conn.execute(
        """
        WITH t(match_id, as_of) AS (
            SELECT * FROM unnest(%s::bigint[], %s::timestamptz[])
        )
        SELECT t.match_id, v.match_version_id, v.player_1_id, v.player_2_id,
               v.surface::text, v.tour_scope::text, v.competition_tier::text,
               v.best_of_sets, v.qualifying_flag, v.match_start_at
        FROM t
        JOIN LATERAL (
            SELECT * FROM match_versions mv
            WHERE mv.match_id = t.match_id
              AND mv.as_of <= t.as_of
              AND mv.source_observed_at <= t.as_of
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
            LIMIT 1
        ) v ON TRUE
        """,
        (ids, as_ofs),
    ).fetchall()
    return {
        int(r[0]): {
            "match_version_id": int(r[1]), "player_1_id": int(r[2]),
            "player_2_id": int(r[3]), "surface": r[4], "tour_scope": r[5],
            "competition_tier": r[6], "best_of_sets": int(r[7]),
            "qualifying_flag": bool(r[8]), "match_start_at": r[9],
        } for r in rows
    }


def _replay_series(history, targets: list[dict],
                   mapping: dict[int, int] | None) -> tuple[dict[int, float], dict]:
    """Replay production A1 chronologically at each original target cutoff."""
    hist = sorted(history, key=lambda o: (max(o.as_of, o.source_observed_at), o.match_id))
    out: dict[int, float] = {}
    failures: Counter[str] = Counter()
    replay, previous, index = None, None, 0
    full_replays = 0
    for target in sorted(targets, key=lambda t: (t["a1_as_of"], t["match_id"])):
        as_of = target["a1_as_of"]
        if replay is None:
            visible = [o for o in hist
                       if o.as_of <= as_of and o.source_observed_at <= as_of]
            replay, index = replay_history(visible, as_of), len(visible)
            full_replays += 1
        else:
            new = []
            while (index < len(hist)
                   and max(hist[index].as_of, hist[index].source_observed_at) <= as_of):
                new.append(hist[index])
                index += 1
            if new:
                try:
                    replay = advance_replay_history(
                        replay, new, previous_prediction_as_of=previous,
                        prediction_as_of=as_of)
                except SurfaceEloValidationError:
                    replay = replay_history(hist[:index], as_of)
                    full_replays += 1
        previous = as_of
        p1 = int(target["player_1_id"])
        p2 = int(target["player_2_id"])
        if mapping:
            m1, m2 = mapping.get(p1, p1), mapping.get(p2, p2)
            if m1 != m2:
                p1, p2 = m1, m2
        try:
            request = PredictionRequest(
                player_1_id=p1, player_2_id=p2,
                surface=target["surface"], tour_scope=target["tour_scope"],
                competition_tier=target["competition_tier"],
                best_of_sets=target["best_of_sets"],
                qualifying_flag=target["qualifying_flag"], as_of=as_of)
            out[target["match_id"]] = float(
                predict_from_replay(request, replay).player_1_probability)
        except SurfaceEloValidationError:
            failures["SURFACE_ELO_VALIDATION_ERROR"] += 1
    return out, {"failures": dict(failures), "full_replays": full_replays,
                 "targets": len(targets), "replayed": len(out)}


def _candidate(active: dict[str, tuple[int, float]],
               replacement: float | None) -> tuple[int, float] | None:
    values = dict(active)
    if A1 in values and replacement is not None:
        values[A1] = (1 if replacement >= 0.5 else 2, replacement)
    return _policy(values)[POLICY]


def _group_metrics(rows: list[dict], arm: str) -> dict[str, Any]:
    settled = [r for r in rows if r["winner"] in (1, 2)
               and r["candidates"][arm] is not None]
    if not settled:
        return {"selected": len(rows), "settled": 0}
    wins = sum(r["candidates"][arm][0] == r["winner"] for r in settled)
    expected = sum(max(r["candidates"][arm][1], 1 - r["candidates"][arm][1])
                   for r in settled)
    p1_pairs = [(r["candidates"][arm][1], int(r["winner"] == 1)) for r in settled]
    brier = sum((p - y) ** 2 for p, y in p1_pairs) / len(p1_pairs)
    logloss = -sum(y * math.log(max(p, 1e-12))
                   + (1 - y) * math.log(max(1 - p, 1e-12))
                   for p, y in p1_pairs) / len(p1_pairs)
    confidence = [max(r["candidates"][arm][1], 1 - r["candidates"][arm][1])
                  for r in settled]
    return {
        "selected": len(rows), "settled": len(settled),
        "wins": wins, "losses": len(settled) - wins,
        "accuracy": wins / len(settled),
        "accuracy_wilson95": _wilson(wins, len(settled)),
        "expected_wins": expected,
        "residual": wins - expected,
        "mean_selected_probability": sum(confidence) / len(confidence),
        "brier": brier, "log_loss": logloss,
        "ids_sha256": _sha(sorted(r["match_id"] for r in rows)),
    }


def _select(rows: list[dict], arm: str, cutoff: float) -> list[dict]:
    return [r for r in rows
            if r["candidates"][arm] is not None
            and max(r["candidates"][arm][1], 1 - r["candidates"][arm][1]) + 1e-12 >= cutoff]


def _set_report(a: list[dict], b: list[dict], *, arm_a: str = POLICY,
                arm_b: str = "B") -> dict[str, Any]:
    ids_a = {r["match_id"] for r in a}
    ids_b = {r["match_id"] for r in b}
    shared = ids_a & ids_b
    removed = ids_a - ids_b
    added = ids_b - ids_a
    by_id_b = {r["match_id"]: r for r in b}
    corrected, newly_incorrect = [], []
    for row in a:
        if row["match_id"] not in shared or row["winner"] not in (1, 2):
            continue
        other = by_id_b[row["match_id"]]
        wa = row["candidates"][arm_a][0] == row["winner"]
        wb = other["candidates"][arm_b][0] == row["winner"]
        if not wa and wb:
            corrected.append(row["match_id"])
        elif wa and not wb:
            newly_incorrect.append(row["match_id"])
    return {
        "a_selected": len(ids_a), "b_selected": len(ids_b),
        "shared": len(shared), "removed": len(removed), "added": len(added),
        "removed_ids_sha256": _sha(sorted(removed)),
        "added_ids_sha256": _sha(sorted(added)),
        "corrected_winner_predictions": len(corrected),
        "newly_incorrect_winner_predictions": len(newly_incorrect),
        "corrected_ids_sha256": _sha(sorted(corrected)),
        "newly_incorrect_ids_sha256": _sha(sorted(newly_incorrect)),
        "shared_side_flips": sum(
            by_id_b[m]["candidates"][arm_b][0] != next(
                r for r in a if r["match_id"] == m)["candidates"][arm_a][0]
            for m in shared),
    }


def _paired_bootstrap(rows: list[dict], arm_a: str, arm_b: str,
                      *, cluster_key: str = "match_id") -> dict[str, Any]:
    shared = [r for r in rows if r["winner"] in (1, 2)
              and r["candidates"][arm_a] is not None
              and r["candidates"][arm_b] is not None]
    clusters: dict[Any, list[dict]] = defaultdict(list)
    for row in shared:
        clusters[row[cluster_key]].append(row)
    keys = sorted(clusters)
    if len(keys) < 2 or not shared:
        return {"available": False, "clusters": len(keys)}

    def deltas(group):
        brier = accuracy = residual = 0.0
        for r in group:
            ya = int(r["winner"] == r["candidates"][arm_a][0])
            yb = int(r["winner"] == r["candidates"][arm_b][0])
            brier += ((r["candidates"][arm_a][1] - int(r["winner"] == 1)) ** 2
                      - (r["candidates"][arm_b][1] - int(r["winner"] == 1)) ** 2)
            accuracy += ya - yb
            residual += ((ya - max(r["candidates"][arm_a][1], 1 - r["candidates"][arm_a][1]))
                         - (yb - max(r["candidates"][arm_b][1], 1 - r["candidates"][arm_b][1])))
        n = len(group)
        return brier / n, accuracy / n, residual / n

    rng = random.Random(SEED + 7)
    samples = []
    for _ in range(ITERATIONS):
        picked = [row for _ in keys for row in clusters[keys[rng.randrange(len(keys))]]]
        if picked:
            samples.append(deltas(picked))

    def interval(index):
        values = sorted(s[index] for s in samples)
        return [values[int(.025 * len(values))],
                values[min(len(values) - 1, int(.975 * len(values)))]]

    return {
        "available": True, "clusters": len(keys), "iterations": ITERATIONS,
        "seed": SEED + 7, "cluster_key": cluster_key, "paired_settled": len(shared),
        "brier_improvement_a_minus_b_95": interval(0),
        "accuracy_improvement_a_minus_b_95": interval(1),
        "residual_improvement_a_minus_b_95": interval(2),
        "interpretation": (
            "Positive Brier improvement means A scored worse than B. Accuracy "
            "improvement is A minus B. The cluster resamples matches; selections "
            "sharing a favourite are not independent and are also reported "
            "clustered by favourite."),
    }


def _equal_count(rows: list[dict], arm: str, k: int) -> list[dict]:
    available = [r for r in rows if r["candidates"][arm] is not None]
    available.sort(key=lambda r: (
        -max(r["candidates"][arm][1], 1 - r["candidates"][arm][1]),
        r["match_id"]))
    return available[:k]


def _bookmaker(conn: psycopg.Connection, rows: list[dict]) -> dict[str, Any]:
    """Latest pre-issue two-sided MATCH_WINNER quote per book, if it qualifies."""
    ids = [r["match_id"] for r in rows]
    if not ids:
        return {"fixtures": 0, "eligible": 0}
    quotes = conn.execute(
        """
        SELECT o.match_id, o.source_system::text, o.bookmaker_code,
               o.selection_type::text, o.decimal_price,
               o.source_observed_at, o.as_of, o.ingested_at, lock.locked_at
        FROM odds_snapshots o
        JOIN LATERAL (
            SELECT p.prediction_locked_at AS locked_at
            FROM predictions p
            WHERE p.match_id = o.match_id
              AND p.agent_code = 'AGENT_1_SURFACE_ELO'
              AND p.prediction_status = 'PREDICTED'
            ORDER BY p.as_of LIMIT 1
        ) lock ON TRUE
        WHERE o.match_id = ANY(%s)
          AND o.market_type = 'MATCH_WINNER'
          AND o.selection_type IN ('PLAYER_1','PLAYER_2')
          AND o.market_status = 'OPEN'
          AND o.source_observed_at <= lock.locked_at
          AND o.as_of <= lock.locked_at
          AND o.ingested_at <= lock.locked_at
          AND o.source_observed_at >= lock.locked_at - interval '6 hours'
        ORDER BY o.match_id, o.bookmaker_code, o.selection_type,
                 o.source_observed_at DESC, o.odds_snapshot_id DESC
        """,
        (ids,),
    ).fetchall()
    latest: dict[tuple, dict] = {}
    for row in quotes:
        mid, source, book, side, price, observed, as_of, ingested, _ = row
        latest[(int(mid), source, book, side)] = {
            "price": float(price), "observed": observed, "as_of": as_of,
            "ingested": ingested,
        }
    by_book: dict[tuple, dict] = defaultdict(dict)
    for (mid, source, book, side), quote in latest.items():
        by_book[(mid, source, book)][side] = quote
    eligible: dict[int, list[dict]] = defaultdict(list)
    for (mid, source, book), sides in by_book.items():
        if set(sides) != {"PLAYER_1", "PLAYER_2"}:
            continue
        q1, q2 = sides["PLAYER_1"], sides["PLAYER_2"]
        if abs(q1["observed"] - q2["observed"]).total_seconds() > 300:
            continue
        if not (1 < q1["price"] < 100 and 1 < q2["price"] < 100):
            continue
        implied = [1 / q1["price"], 1 / q2["price"]]
        total = sum(implied)
        if not .95 <= total <= 1.25:
            continue
        eligible[mid].append({"source": source, "bookmaker": book,
                              "p1": implied[0] / total,
                              "observed": min(q1["observed"], q2["observed"])})
    result: dict[str, Any] = {
        "fixtures": len(rows), "eligible_fixtures": len(eligible),
        "by_bookmaker": dict(sorted(Counter(
            item["bookmaker"] for items in eligible.values() for item in items).items())),
        "coverage": len(eligible) / len(rows) if rows else None,
    }
    for arm in (POLICY, "A_replay", "B"):
        pairs = []
        market_expected = model_expected = actual = 0
        for row in rows:
            if row["match_id"] not in eligible or row["winner"] not in (1, 2):
                continue
            pick = row["candidates"].get(arm)
            if pick is None:
                continue
            quote = min(eligible[row["match_id"]], key=lambda x: x["observed"])
            market_p1 = quote["p1"]
            model_p1 = pick[1]
            y = int(row["winner"] == 1)
            market_expected += market_p1 if pick[0] == 1 else 1 - market_p1
            model_expected += max(model_p1, 1 - model_p1)
            actual += int(pick[0] == row["winner"])
            pairs.append((market_p1, model_p1, y, pick[0]))
        if pairs:
            market_brier = sum((m - y) ** 2 for m, _, y, _ in pairs) / len(pairs)
            model_brier = sum((mod - y) ** 2 for _, mod, y, _ in pairs) / len(pairs)
            result[arm] = {
                "settled_priced": len(pairs), "actual_wins": actual,
                "market_expected_wins": market_expected,
                "model_expected_wins": model_expected,
                "market_brier": market_brier, "model_brier": model_brier,
            }
        else:
            result[arm] = {"settled_priced": 0,
                           "blocker": "NO_ORIGINAL_TIME_TWO_SIDED_QUOTE"}
    return result


def run(conn: psycopg.Connection, *, start=TRAIN_START, end=VALIDATION_END,
        as_of=HISTORICAL_AS_OF) -> dict[str, Any]:
    data = read_source(conn, start=start, end=end, as_of=as_of)
    cohorts = select_cohorts(data)
    evaluation = cohorts["later"]
    by_id = {r["match_id"]: r for r in evaluation}
    raw_by_match: dict[int, dict[str, tuple[int, float, datetime]]] = defaultdict(dict)
    for row in data["raw"]:
        if row[6] is None or int(row[0]) not in by_id:
            continue
        if row[8] is None or row[9] is None:
            continue
        raw_by_match[int(row[0])][str(row[6])] = (
            int(row[8]), float(row[9]), _utc(row[30]))
    targets = []
    for mid in sorted(by_id):
        agents = raw_by_match.get(mid, {})
        if A1 not in agents:
            continue
        side, probability, a1_as_of = agents[A1]
        targets.append({"match_id": mid, "a1_as_of": a1_as_of,
                        "stored_a1_p1": _p1(side, probability),
                        "stored_side": side, "stored_probability": probability})
    metadata = _target_metadata(conn, targets)
    for target in targets:
        m = metadata[target["match_id"]]
        target.update({
            "player_1_id": m["player_1_id"], "player_2_id": m["player_2_id"],
            "surface": m["surface"], "tour_scope": m["tour_scope"],
            "competition_tier": m["competition_tier"],
            "best_of_sets": m["best_of_sets"],
            "qualifying_flag": m["qualifying_flag"],
        })
    cutoff_values = sorted({t["a1_as_of"] for t in targets})
    target_players = {t["player_1_id"] for t in targets} | {
        t["player_2_id"] for t in targets}
    sampled = [cutoff_values[0], cutoff_values[len(cutoff_values) // 2], cutoff_values[-1]]
    mappings = [resolve_model_player_ids(conn, player_ids=target_players, as_of=c)
                for c in sampled]
    mapping_invariant = all(m == mappings[0] for m in mappings[1:])
    history = load_agent1_history(conn, cutoff=_utc(as_of))
    bridged_history = load_agent1_history(
        conn, cutoff=_utc(as_of), bridge_model_identities=True)
    mapping = mappings[-1]
    # Production resolves live target IDs into the model namespace before Agent 1
    # sees them (runner/targets.py). The CURRENT arm therefore uses the same
    # target mapping; only the history namespace differs in the BRIDGED arm.
    current, current_diag = _replay_series(history, targets, mapping)
    bridged, bridged_diag = _replay_series(bridged_history, targets, mapping)
    stored = {t["match_id"]: t["stored_a1_p1"] for t in targets}
    reproduction = [
        abs(current[m] - stored[m]) for m in stored if m in current
    ]
    agents = _agent_evidence(data["raw"])
    rows = []
    policy_mismatch = 0
    for target in targets:
        mid = target["match_id"]
        record = dict(by_id[mid])
        active = dict(agents.get(mid, {}))
        stored_candidate = _candidate(active, None)
        original = record["candidates"][POLICY]
        if (original is None) != (stored_candidate is None) or (
                original is not None and stored_candidate is not None
                and (original[0] != stored_candidate[0]
                     or abs(original[1] - stored_candidate[1]) > 1e-12)):
            policy_mismatch += 1
        replay_active = dict(active)
        if A1 in replay_active and mid in current:
            replay_active[A1] = (1 if current[mid] >= 0.5 else 2, current[mid])
        bridged_active = dict(active)
        if A1 in bridged_active and mid in bridged:
            bridged_active[A1] = (1 if bridged[mid] >= 0.5 else 2, bridged[mid])
        # The requested A arm uses the stored A1 probabilities; the policy is
        # re-evaluated from those stored values so A and B are identical code.
        candidates = {
            POLICY: stored_candidate,
            "A_replay": _policy(replay_active)[POLICY],
            "B": _policy(bridged_active)[POLICY],
        }
        rows.append({
            **record,
            "a1_stored_p1": target["stored_a1_p1"],
            "a1_current_replay_p1": current.get(mid),
            "a1_bridged_replay_p1": bridged.get(mid),
            "candidates": candidates,
            "favourite_player_id": (
                target["player_1_id"]
                if (candidates[POLICY] or (1, .5))[0] == 1
                else target["player_2_id"]),
            "cluster": (
                target["player_1_id"]
                if (candidates[POLICY] or (1, .5))[0] == 1
                else target["player_2_id"]),
        })
    narrow_cutoff = float(cohorts["thresholds"]["narrow"]["threshold"])
    broad_cutoff = float(cohorts["thresholds"]["broad"]["threshold"])

    def cohort_report(rows_in: list[dict]) -> dict[str, Any]:
        out = {"fixtures": len(rows_in), "settled": sum(
            r["winner"] in (1, 2) for r in rows_in)}
        for arm in (POLICY, "A_replay", "B"):
            selected = _select(rows_in, arm, broad_cutoff)
            narrow = _select(rows_in, arm, narrow_cutoff)
            narrow_ids = {r["match_id"] for r in narrow}
            marginal = [r for r in selected if r["match_id"] not in narrow_ids]
            broad_metrics = _group_metrics(selected, arm)
            narrow_metrics = _group_metrics(narrow, arm)
            marginal_metrics = _group_metrics(marginal, arm)
            broad_metrics["coverage_of_eligible"] = (
                len(selected) / len(rows_in) if rows_in else None)
            narrow_metrics["coverage_of_eligible"] = (
                len(narrow) / len(rows_in) if rows_in else None)
            marginal_metrics["coverage_of_eligible"] = (
                len(marginal) / len(rows_in) if rows_in else None)
            out[arm] = {
                "broad": broad_metrics,
                "narrow": narrow_metrics,
                "marginal": marginal_metrics,
            }
        narrow_ids_a = {r["match_id"] for r in _select(rows_in, POLICY, narrow_cutoff)}
        narrow_ids_b = {r["match_id"] for r in _select(rows_in, "B", narrow_cutoff)}
        marginal_a = [r for r in _select(rows_in, POLICY, broad_cutoff)
                      if r["match_id"] not in narrow_ids_a]
        marginal_b = [r for r in _select(rows_in, "B", broad_cutoff)
                      if r["match_id"] not in narrow_ids_b]
        out["sets"] = {
            "broad": _set_report(_select(rows_in, POLICY, broad_cutoff),
                                 _select(rows_in, "B", broad_cutoff),
                                 arm_a=POLICY, arm_b="B"),
            "narrow": _set_report(_select(rows_in, POLICY, narrow_cutoff),
                                  _select(rows_in, "B", narrow_cutoff),
                                  arm_a=POLICY, arm_b="B"),
            "marginal": _set_report(marginal_a, marginal_b,
                                    arm_a=POLICY, arm_b="B"),
        }
        out["paired"] = {
            "broad_brier_accuracy_residual": _paired_bootstrap(
                _select(rows_in, POLICY, broad_cutoff), POLICY, "B"),
            "broad_brier_accuracy_residual_favourite_cluster": _paired_bootstrap(
                _select(rows_in, POLICY, broad_cutoff), POLICY, "B",
                cluster_key="favourite_player_id"),
            "marginal_brier_accuracy_residual": _paired_bootstrap(
                marginal_a, POLICY, "B"),
            "marginal_brier_accuracy_residual_favourite_cluster": _paired_bootstrap(
                marginal_a, POLICY, "B", cluster_key="favourite_player_id"),
            "replay_only_brier_accuracy_residual": _paired_bootstrap(
                _select(rows_in, "A_replay", broad_cutoff), "A_replay", "B"),
        }
        for target in (.20, .40):
            k = math.ceil(len(rows_in) * target)
            a_block = _group_metrics(_equal_count(rows_in, POLICY, k), POLICY)
            b_block = _group_metrics(_equal_count(rows_in, "B", k), "B")
            a_block["coverage_of_eligible"] = (
                a_block["selected"] / len(rows_in) if rows_in else None)
            b_block["coverage_of_eligible"] = (
                b_block["selected"] / len(rows_in) if rows_in else None)
            out[f"equal_count_{int(target*100)}pct"] = {
                "k": k, "A": a_block, "B": b_block,
            }
        return out

    report: dict[str, Any] = {
        "version": VERSION,
        "status": "DESCRIPTIVE_HISTORICAL_RESEARCH_ONLY",
        "independent_forward_validation": False,
        "historical_evaluation_previously_inspected": True,
        "as_of": _utc(as_of).isoformat(),
        "policy": POLICY,
        "cohort_thresholds": cohorts["thresholds"],
        "eligible_fixtures": len(rows),
        "a1_replay": {
            "targets": len(targets),
            "stored_a1_available": len(stored),
            "a_arm_policy_reproduction": {
                "compared": len(rows),
                "mismatches": policy_mismatch,
                "exact": policy_mismatch == 0,
                "note": (
                    "The A arm re-evaluates the frozen policy from the stored "
                    "A1/A2/A3 probabilities. Zero mismatches means it is the "
                    "original Manager C candidate, not a new rule."),
            },
            "current_replayed": current_diag,
            "bridged_replayed": bridged_diag,
            "reproduction_vs_stored": {
                "compared": len(reproduction),
                "exact_1e-6": sum(d <= 1e-6 for d in reproduction),
                "exact_fraction": (sum(d <= 1e-6 for d in reproduction)
                                   / len(reproduction)) if reproduction else None,
                "within_0.005": sum(d <= .005 for d in reproduction),
                "within_0.005_fraction": (sum(d <= .005 for d in reproduction)
                                          / len(reproduction)) if reproduction else None,
                "median_abs_difference": (
                    sorted(reproduction)[len(reproduction) // 2]
                    if reproduction else None),
            },
            "identity_mapping": {
                "resolved_players": sum(mapping.get(p, p) != p for p in target_players),
                "target_players": len(target_players),
                "invariant_across_sampled_cutoffs": mapping_invariant,
                "cutoffs_sampled": [c.isoformat() for c in sampled],
                "activated_bridge_note": (
                    "The bridge is applied to Agent 1 history and to target IDs. "
                    "It fails closed on non-unique aliases."),
            },
        },
        "all": cohort_report(rows),
        "challenger": cohort_report(
            [r for r in rows if r["tour"] == "CHALLENGER"]),
        "atp_main": cohort_report([r for r in rows if r["tour"] == "ATP_MAIN"]),
        "bookmaker": _bookmaker(conn, rows),
        "limitations": [
            "The November window was previously inspected; not independent forward validation.",
            "B requires a local A1 replay; reproduction against stored A1 is reported, and A' versus B isolates the identity change.",
            "Only A1 changes between A and B. A2/A3 stored inputs, Manager rule, cutoffs, thresholds, calibration and settlement are identical.",
            "Identity resolution is invariant across the sampled cutoffs but is an alias-uniqueness bridge, not a verified provider census.",
            "Bookmaker comparisons are reported only for strict original-time two-sided quotes; no quote is fabricated.",
        ],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("authorized TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=15,
                         options="-c default_transaction_read_only=on") as conn:
        report = run(conn)
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
