"""Analyse the frozen per-target ledger; emit reconciliation evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "research" / "evidence"
BOOT_REPS = 1000
BOOT_SEED = 20261125
E1_CUTOFF = "2026-11-24T08:10:00Z"


def _dsn() -> str:
    direct = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if direct:
        return direct
    if os.environ.get("PGHOST") and os.environ.get("PGPASSWORD"):
        return psycopg.conninfo.make_conninfo(
            host=os.environ["PGHOST"],
            port=os.environ.get("PGPORT", "5432"),
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
            dbname=os.environ["PGDATABASE"],
            sslmode=os.environ.get("PGSSLMODE", "require"),
        )
    raise SystemExit("TENNIS_DATABASE_URL, DATABASE_URL or PG* variables are required")


def _rows(sql: str, params=()):
    with psycopg.connect(
        _dsn(), autocommit=True, options="-c default_transaction_read_only=on"
    ) as conn:
        return conn.execute(sql, params).fetchall()


def p1_side(p):
    return 1 if p >= 0.5 else 2


def conf(p):
    return max(p, 1 - p)


def won(p, y):
    return int(p1_side(p) == (1 if y else 2))


def metrics(rows, key):
    n = len(rows)
    if not n:
        return {"n": 0}
    wins = sum(won(r[key], r["y"]) for r in rows)
    brier = sum((r[key] - r["y"]) ** 2 for r in rows) / n
    ll = -sum(
        r["y"] * math.log(min(max(r[key], 1e-12), 1 - 1e-12))
        + (1 - r["y"]) * math.log(min(max(1 - r[key], 1e-12), 1 - 1e-12))
        for r in rows
    ) / n
    return {"n": n, "wins": wins, "losses": n - wins, "accuracy": wins / n,
            "brier": brier, "log_loss": ll}


def metrics_strict(rows, key):
    """Historical E1 accuracy convention: p > 0.5, so a 0.5 forecast is side 2."""
    n = len(rows)
    if not n:
        return {"n": 0}
    wins = sum(int((r[key] > 0.5) == (r["y"] == 1)) for r in rows)
    return {"n": n, "wins": wins, "losses": n - wins, "accuracy": wins / n}


def top_n(rows, key, n):
    return sorted(rows, key=lambda r: conf(r[key]), reverse=True)[:n]


def set_key(r):
    return (int(r["prediction_id"]), int(r["match_id"]))


def wilson(k, n, z=1.96):
    if not n:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return {"discordant": 0, "p_value": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return {"discordant": n, "p_value": min(1.0, 2 * tail)}


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs, ys):
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def paired_day_bootstrap(rows, key_a, key_b, reps=BOOT_REPS, seed=BOOT_SEED):
    by_day = defaultdict(list)
    for r in rows:
        day = (r.get("match_start_at") or r["as_of"])[:10]
        by_day[day].append(r)
    days = sorted(by_day)
    rng = random.Random(seed)
    db, dl, da = [], [], []
    for _ in range(reps):
        sample = [x for _ in days for x in by_day[rng.choice(days)]]
        ma, mb = metrics(sample, key_a), metrics(sample, key_b)
        db.append(mb["brier"] - ma["brier"])
        dl.append(mb["log_loss"] - ma["log_loss"])
        da.append(mb["accuracy"] - ma["accuracy"])
    for arr in (db, dl, da):
        arr.sort()
    lo, hi = int(0.025 * (reps - 1)), int(0.975 * (reps - 1))
    return {
        "brier_delta_B_minus_A": [db[lo], db[hi]],
        "log_loss_delta_B_minus_A": [dl[lo], dl[hi]],
        "accuracy_delta_B_minus_A": [da[lo], da[hi]],
        "reps": reps, "seed": seed,
    }


def model_pick(a1_p1, r, a2):
    if a2 is None or a2.get("status") != "PREDICTED" or a2.get("probability") is None:
        return None
    if r.get("a3_status") != "ABSTAINED":
        return None
    side = p1_side(a1_p1)
    a1_sel = a1_p1 if side == 1 else 1 - a1_p1
    if a2.get("side") != side or a2["probability"] < 0.5:
        return None
    mean = (a1_sel + a2["probability"]) / 2
    return (side, mean) if mean >= 0.6 else None


def load_agent_map(rows):
    keys = sorted({(int(r["prediction_run_id"]), int(r["match_id"])) for r in rows})
    result = _rows(
        """
        SELECT prediction_run_id, match_id, agent_code::text, prediction_status::text,
               selection_side, win_probability
        FROM predictions
        WHERE (prediction_run_id, match_id) IN (
            SELECT * FROM unnest(%s::bigint[], %s::bigint[])
        )
        """,
        ([k[0] for k in keys], [k[1] for k in keys]),
    )
    out = defaultdict(dict)
    for run_id, match_id, code, status, side, prob in result:
        out[(int(run_id), int(match_id))][code] = {
            "status": status,
            "side": side,
            "probability": float(prob) if prob is not None else None,
        }
    return out


def attach_agents(rows):
    amap = load_agent_map(rows)
    for r in rows:
        key = (int(r["prediction_run_id"]), int(r["match_id"]))
        r["a2"] = amap.get(key, {}).get("AGENT_2_HIERARCHICAL_MARKOV")
        r["a3_status"] = (amap.get(key, {}).get("AGENT_3_INTRANSITIVITY") or {}).get("status")


def attach_result_created(rows, cutoff):
    ids = sorted({r["match_id"] for r in rows})
    result = _rows(
        """
        SELECT DISTINCT ON (match_id) match_id, created_at
        FROM match_result_observations
        WHERE match_id = ANY(%s) AND created_at <= %s
        ORDER BY match_id, as_of DESC, source_observed_at DESC,
                 match_result_observation_id DESC
        """,
        (ids, cutoff),
    )
    by_match = {int(mid): created for mid, created in result}
    for r in rows:
        r["result_created_at"] = by_match.get(r["match_id"]).isoformat() if by_match.get(r["match_id"]) else None


def db_metadata():
    return {
        "model_and_feature_versions": _rows(
            "SELECT DISTINCT model_version, feature_version FROM predictions "
            "WHERE agent_code='AGENT_1_SURFACE_ELO'"
        ),
        "agent_configs": _rows(
            "SELECT config_version, model_version, feature_version, calibration_version, created_at "
            "FROM agent_config_versions WHERE agent_code='AGENT_1_SURFACE_ELO'"
        ),
        "bridge_events": _rows(
            "SELECT count(*), min(created_at), max(created_at) FROM model_identity_bridge_events"
        ),
        "aliases": _rows(
            "SELECT source_system::text, count(*), min(created_at), max(created_at) "
            "FROM player_aliases GROUP BY 1 ORDER BY 2 DESC"
        ),
    }


def pick_metrics(picks):
    n = len(picks)
    if not n:
        return {"n": 0, "wins": 0, "losses": 0, "accuracy": None, "wilson95": [None, None]}
    wins = sum(int(side == (1 if r["y"] else 2)) for r, side, _ in picks)
    return {"n": n, "wins": wins, "losses": n - wins, "accuracy": wins / n,
            "wilson95": wilson(wins, n)}


def pick_top(picks, n):
    chosen = sorted(picks, key=lambda x: x[2], reverse=True)[:n]
    return pick_metrics(chosen), chosen


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default=str(ROOT / "tmp" / "ledger_full.jsonl"))
    parser.add_argument("--out", default=str(OUT_DIR / "a1_historical_reconciliation_20261126.json"))
    parser.add_argument("--sets-out", default=str(OUT_DIR / "a1_top800_sets_20261126.json"))
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in Path(args.ledger).read_text(encoding="utf-8").splitlines() if line.strip()]
    e2_all = [r for r in rows if r["experiment"] == "E2"]
    e3_all = [r for r in rows if r["experiment"] == "E3"]

    for r in e2_all:
        r["pStored"] = float(r["stored_p1"])
        sc, sb = r.get("shadow_current", {}), r.get("shadow_bridged", {})
        r["pShadow"] = float(sc["p1"]) if "p1" in sc else None
        r["pBridge"] = float(sb["p1"]) if "p1" in sb else None
    for r in e3_all:
        r["pA"] = float(r["A"]["p1"]) if "p1" in r.get("A", {}) else None
        r["pB"] = float(r["B"]["p1"]) if "p1" in r.get("B", {}) else None

    e2 = [r for r in e2_all if r.get("y") is not None and r.get("outcome_status") == "COMPLETED"]
    e3 = [r for r in e3_all if r.get("y") is not None and r.get("outcome_status") == "COMPLETED"]
    e2_ok = [r for r in e2 if r["pShadow"] is not None and r["pBridge"] is not None]
    e3_ok = [r for r in e3 if r["pA"] is not None and r["pB"] is not None]
    attach_result_created(e2, "2026-11-24T09:00:00Z")
    # E1 is the 2026-11-24T08:10Z settlement snapshot: the 52-result batch observed
    # at 08:14Z had not yet made those matches settled.
    e1 = [r for r in e2_ok if r["result_created_at"] and r["result_created_at"] <= E1_CUTOFF]

    attach_agents(e2)
    attach_agents(e3)

    e2_orig_picks = []
    e2_bridge_picks = []
    for r in e2:
        po = model_pick(r["pStored"], r, r["a2"])
        pb = model_pick(r["pBridge"], r, r["a2"]) if r["pBridge"] is not None else None
        if po:
            e2_orig_picks.append((r, po[0], po[1]))
        if pb:
            e2_bridge_picks.append((r, pb[0], pb[1]))

    e2_top = {}
    for label, picks in (("original", e2_orig_picks), ("bridged", e2_bridge_picks)):
        m, chosen = pick_top(picks, 800)
        boundary = chosen[-1][2] if chosen else None
        e2_top[label] = {
            "metrics": m,
            "prediction_ids": [r["prediction_id"] for r, _, _ in chosen],
            "tie_handling": {
                "boundary_confidence": boundary,
                "selected_at_boundary": sum(1 for _, _, c in chosen if c == boundary) if chosen else 0,
                "eligible_at_boundary": sum(1 for _, _, c in picks if c == boundary) if chosen else 0,
            },
        }

    e3_a_top = top_n(e3_ok, "pA", 800)
    e3_b_top = top_n(e3_ok, "pB", 800)

    both_correct = sum(won(r["pA"], r["y"]) and won(r["pB"], r["y"]) for r in e3_ok)
    both_wrong = sum(not won(r["pA"], r["y"]) and not won(r["pB"], r["y"]) for r in e3_ok)
    a_only = sum(won(r["pA"], r["y"]) and not won(r["pB"], r["y"]) for r in e3_ok)
    b_only = sum(not won(r["pA"], r["y"]) and won(r["pB"], r["y"]) for r in e3_ok)
    mcnemar = mcnemar_exact(a_only, b_only)
    ma, mb = metrics(e3_ok, "pA"), metrics(e3_ok, "pB")

    tour = {}
    for label, keep in (("ATP", lambda r: r["tour"] == "ATP_MAIN"),
                        ("CHALLENGER", lambda r: r["tour"] == "CHALLENGER")):
        subset = [r for r in e3_ok if keep(r)]
        tour[label] = {"n": len(subset), "A": metrics(subset, "pA"), "B": metrics(subset, "pB"),
                       "discordant_A_only": sum(won(r["pA"], r["y"]) and not won(r["pB"], r["y"]) for r in subset),
                       "discordant_B_only": sum(not won(r["pA"], r["y"]) and won(r["pB"], r["y"]) for r in subset)}

    def tie_info(selected_rows, all_rows, key):
        if not selected_rows:
            return {"n": 0}
        boundary = conf(selected_rows[-1][key])
        return {
            "boundary_confidence": boundary,
            "selected_at_boundary": sum(1 for r in selected_rows if conf(r[key]) == boundary),
            "eligible_at_boundary": sum(1 for r in all_rows if conf(r[key]) == boundary),
            "excluded_at_boundary": sum(1 for r in all_rows if conf(r[key]) == boundary)
            - sum(1 for r in selected_rows if conf(r[key]) == boundary),
            "rule": "Descending confidence; Python stable sort preserves the SQL chronological/match_id order.",
        }

    A_by = {set_key(r): r for r in e3_a_top}
    B_by = {set_key(r): r for r in e3_b_top}
    shared = set(A_by) & set(B_by)
    a_only_keys = set(A_by) - set(B_by)
    b_only_keys = set(B_by) - set(A_by)
    shared_same = [k for k in shared if p1_side(A_by[k]["pA"]) == p1_side(B_by[k]["pB"])]
    shared_changed = [k for k in shared if p1_side(A_by[k]["pA"]) != p1_side(B_by[k]["pB"])]
    shared_wins_A = sum(won(A_by[k]["pA"], A_by[k]["y"]) for k in shared)
    shared_wins_B = sum(won(B_by[k]["pB"], B_by[k]["y"]) for k in shared)
    a_only_wins = sum(won(A_by[k]["pA"], A_by[k]["y"]) for k in a_only_keys)
    b_only_wins = sum(won(B_by[k]["pB"], B_by[k]["y"]) for k in b_only_keys)
    wins_A = shared_wins_A + a_only_wins
    wins_B = shared_wins_B + b_only_wins

    union = sorted(set(A_by) | set(B_by))
    ranked_a = sorted(e3_ok, key=lambda r: conf(r["pA"]), reverse=True)
    ranked_b = sorted(e3_ok, key=lambda r: conf(r["pB"]), reverse=True)
    rank_a_all = {set_key(r): i + 1 for i, r in enumerate(ranked_a)}
    rank_b_all = {set_key(r): i + 1 for i, r in enumerate(ranked_b)}
    displacements = [abs(rank_a_all[k] - rank_b_all[k]) for k in union]
    spearman_all = spearman([conf(r["pA"]) for r in e3_ok], [conf(r["pB"]) for r in e3_ok])

    def group_stats(table, keys):
        group = [table[k] for k in keys]
        if not group:
            return {"n": 0}
        return {
            "n": len(group),
            "wins_A": sum(won(r["pA"], r["y"]) for r in group),
            "wins_B": sum(won(r["pB"], r["y"]) for r in group),
            "mean_confidence_A": statistics.fmean(conf(r["pA"]) for r in group),
            "mean_confidence_B": statistics.fmean(conf(r["pB"]) for r in group),
            "mean_confidence_delta": statistics.fmean(conf(r["pB"]) - conf(r["pA"]) for r in group),
            "mean_abs_probability_delta": statistics.fmean(abs(r["pB"] - r["pA"]) for r in group),
            "mean_player_overall_matches_A": statistics.fmean(
                (r["A"]["player_1_overall_matches"] + r["A"]["player_2_overall_matches"]) / 2 for r in group
            ),
            "mean_player_overall_matches_B": statistics.fmean(
                (r["B"]["player_1_overall_matches"] + r["B"]["player_2_overall_matches"]) / 2 for r in group
            ),
            "targets_with_any_bridged_player": sum(
                bool((r["p1_resolution"] or {}).get("bridged") or (r["p2_resolution"] or {}).get("bridged"))
                for r in group
            ),
            "tours": dict(Counter(r["tour"] for r in group)),
        }

    affected = [r for r in e3_ok if abs(r["pB"] - r["pA"]) > 1e-9 or p1_side(r["pA"]) != p1_side(r["pB"])]
    side_flips = [r for r in affected if p1_side(r["pA"]) != p1_side(r["pB"])]
    direct, indirect = [], []
    for r in affected:
        changed = (r["p1_resolution"] or {}).get("bridged") or (r["p2_resolution"] or {}).get("bridged")
        recovered = (
            r["B"]["player_1_overall_matches"] - r["A"]["player_1_overall_matches"]
            + r["B"]["player_2_overall_matches"] - r["A"]["player_2_overall_matches"]
        )
        (direct if changed and recovered > 0 else indirect).append(r)

    def summarize(group):
        if not group:
            return {"n": 0}
        return {
            "n": len(group),
            "probability_delta_abs_mean": statistics.fmean(abs(r["pB"] - r["pA"]) for r in group),
            "probability_delta_signed_mean": statistics.fmean(r["pB"] - r["pA"] for r in group),
            "side_flips": sum(p1_side(r["pA"]) != p1_side(r["pB"]) for r in group),
            "overall_elo_delta_abs_mean": statistics.fmean(
                (abs(r["B"]["player_1_overall_rating"] - r["A"]["player_1_overall_rating"])
                 + abs(r["B"]["player_2_overall_rating"] - r["A"]["player_2_overall_rating"])) / 2
                for r in group
            ),
            "surface_elo_delta_abs_mean": statistics.fmean(
                (abs((r["B"]["player_1_surface_rating"] or 0) - (r["A"]["player_1_surface_rating"] or 0))
                 + abs((r["B"]["player_2_surface_rating"] or 0) - (r["A"]["player_2_surface_rating"] or 0))) / 2
                for r in group
            ),
            "recovered_matches_total": sum(
                max(0, r["B"]["player_1_overall_matches"] - r["A"]["player_1_overall_matches"])
                + max(0, r["B"]["player_2_overall_matches"] - r["A"]["player_2_overall_matches"])
                for r in group
            ),
        }

    def keyset(rs):
        return {set_key(r) for r in rs}

    e1_keys, e2_keys, e3_keys = keyset(e1), keyset(e2), keyset(e3)
    e2_all_keys, e3_all_keys = keyset(e2_all), keyset(e3_all)
    def matchset(rs):
        return {int(r["match_id"]) for r in rs}
    e1_matches, e2_matches, e3_matches = matchset(e1), matchset(e2), matchset(e3)
    e2_target_matches, e3_target_matches = matchset(e2_all), matchset(e3_all)

    e3_a_picks, e3_b_picks = [], []
    for r in e3:
        pa = model_pick(r["pA"], r, r["a2"])
        pb = model_pick(r["pB"], r, r["a2"])
        if pa:
            e3_a_picks.append((r, pa[0], pa[1]))
        if pb:
            e3_b_picks.append((r, pb[0], pb[1]))
    e3_a_pick_top, _ = pick_top(e3_a_picks, 800)
    e3_b_pick_top, _ = pick_top(e3_b_picks, 800)

    e2_a1_top = {
        "stored": metrics(top_n(e2, "pStored", 800), "pStored"),
        "bridged": metrics(top_n([r for r in e2 if r["pBridge"] is not None], "pBridge", 800), "pBridge"),
    }
    e3_on_e2 = [r for r in e3 if set_key(r) in e2_keys]
    e3_a_e2_top = metrics(top_n(e3_on_e2, "pA", 800), "pA")
    e3_b_e2_top = metrics(top_n(e3_on_e2, "pB", 800), "pB")

    e2_orig_top_pids = set(e2_top["original"]["prediction_ids"])
    e2_bridge_top_pids = set(e2_top["bridged"]["prediction_ids"])
    e3_a_top_pids = {A_by[k]["prediction_id"] for k in A_by}
    e3_b_top_pids = {B_by[k]["prediction_id"] for k in B_by}
    p5 = {
        "E2_original": e2_top["original"]["metrics"],
        "E2_bridged": e2_top["bridged"]["metrics"],
        "E2_tie_handling": {
            "original": e2_top["original"]["tie_handling"],
            "bridged": e2_top["bridged"]["tie_handling"],
        },
        "E3_A": metrics(e3_a_top, "pA"),
        "E3_B": metrics(e3_b_top, "pB"),
        "E2_original_vs_E3_A_intersection": len(e2_orig_top_pids & e3_a_top_pids),
        "E2_bridged_vs_E3_B_intersection": len(e2_bridge_top_pids & e3_b_top_pids),
        "E2_original_vs_E3_A_match_intersection": len(
            {int(r["match_id"]) for r in e2 if r["prediction_id"] in e2_orig_top_pids}
            & {int(A_by[k]["match_id"]) for k in A_by}
        ),
        "E2_bridged_vs_E3_B_match_intersection": len(
            {int(r["match_id"]) for r in e2 if r["prediction_id"] in e2_bridge_top_pids}
            & {int(B_by[k]["match_id"]) for k in B_by}
        ),
        "ranking_universe": {
            "E2_all_targets_top800_stored": e2_a1_top["stored"],
            "E2_all_targets_top800_bridged": e2_a1_top["bridged"],
            "E3_model_pick_top800_A": e3_a_pick_top,
            "E3_model_pick_top800_B": e3_b_pick_top,
            "E3_on_E2_settled_top800_A": e3_a_e2_top,
            "E3_on_E2_settled_top800_B": e3_b_e2_top,
        },
    }

    report = {
        "version": "A1_HISTORICAL_POPULATION_RECONCILIATION_V1",
        "generated_at": datetime.now(UTC).isoformat(),
        "classification": "HISTORICAL_DEVELOPMENT_REUSE_NOT_UNTOUCHED_VALIDATION",
        "can_promote": False,
        "production_changed": False,
        "freeze": {
            "E1_cutoff": E1_CUTOFF,
            "E2_cutoff": "2026-11-24T09:00:00Z",
            "E3_cutoff": "2026-11-25T07:20:00Z",
            "E2_targets": len(e2_all), "E2_settled": len(e2),
            "E3_targets": len(e3_all), "E3_settled": len(e3),
            "E1_reconstructed": len(e1),
        },
        "phase1_populations": {
            "E1_earlier_agent1": {
                "settled": len(e1),
                "stored": metrics(e1, "pStored"),
                "bridged": metrics(e1, "pBridge"),
                "stored_historical_strict_gt": metrics_strict(e1, "pStored"),
                "bridged_historical_strict_gt": metrics_strict(e1, "pBridge"),
                "tie_handling": {
                    "p1_exactly_0_5": sum(1 for r in e1 if r["pStored"] == 0.5),
                    "match_ids": sorted(r["match_id"] for r in e1 if r["pStored"] == 0.5),
                    "note": "The quoted E1 63.17%/65.74% uses p>0.5; the current metric uses p>=0.5. A single 0.5 tie (match 134226, winner side 2) accounts for the one-win difference at both variants.",
                },
                "note": "E1 is the 2026-11-24T08:10Z settlement snapshot; the 52-result batch observed at 08:14Z had not yet settled 49 of those matches.",
            },
            "E2_earlier_model_picks": {
                "settled": len(e2),
                "picks_original": len(e2_orig_picks),
                "picks_bridged": len(e2_bridge_picks),
                "picks_original_metrics": pick_metrics(e2_orig_picks),
                "picks_bridged_metrics": pick_metrics(e2_bridge_picks),
                "top800": e2_top,
            },
            "E3_latest_agent1_confidence": {
                "settled": len(e3),
                "A": ma, "B": mb,
                "top800_A": metrics(e3_a_top, "pA"),
                "top800_B": metrics(e3_b_top, "pB"),
            },
            "set_reconciliation": {
                "E2_targets": len(e2_all_keys), "E3_targets": len(e3_all_keys),
                "target_intersection": len(e2_all_keys & e3_all_keys),
                "target_added_E3": len(e3_all_keys - e2_all_keys),
                "target_removed_E3": len(e2_all_keys - e3_all_keys),
                "settled_intersection": len(e2_keys & e3_keys),
                "settled_added_E3": len(e3_keys - e2_keys),
                "settled_removed_E3": len(e2_keys - e3_keys),
                "E1_to_E2_added_by_settlement": len(e2_keys - e1_keys),
                "net_E1_to_E3": len(e3_keys) - len(e1_keys),
                "E1_to_E2_added_prediction_ids": sorted(e2_keys - e1_keys),
                "E2_to_E3_added_prediction_ids": sorted(e3_keys - e2_keys),
                "E2_to_E3_removed_prediction_ids": sorted(e2_keys - e3_keys),
                "targets_added_E3_prediction_ids": sorted(e3_all_keys - e2_all_keys),
                "targets_removed_E3_prediction_ids": sorted(e2_all_keys - e3_all_keys),
                "match_level": {
                    "settled_matches_E1": len(e1_matches),
                    "settled_matches_E2": len(e2_matches),
                    "settled_matches_E3": len(e3_matches),
                    "settled_matches_added_E1_E2": len(e2_matches - e1_matches),
                    "settled_matches_removed_E1_E2": len(e1_matches - e2_matches),
                    "settled_matches_added_E2_E3": len(e3_matches - e2_matches),
                    "settled_matches_removed_E2_E3": len(e2_matches - e3_matches),
                    "settled_matches_added_E1_E3": len(e3_matches - e1_matches),
                    "settled_matches_removed_E1_E3": len(e1_matches - e3_matches),
                    "target_matches_E2": len(e2_target_matches),
                    "target_matches_E3": len(e3_target_matches),
                    "target_matches_added_E2_E3": len(e3_target_matches - e2_target_matches),
                    "target_matches_removed_E2_E3": len(e2_target_matches - e3_target_matches),
                    "settled_matches_added_E1_E3_ids": sorted(e3_matches - e1_matches),
                },
                "prediction_target_level": {
                    "added_E2_E3": len(e3_keys - e2_keys),
                    "removed_E2_E3": len(e2_keys - e3_keys),
                    "note": "A re-issued match moves its selected prediction_id/run_id, so the prediction-target set is not monotone even when the match set is.",
                },
            },
        },
        "phase2_top800_decomposition": {
            "wins_A": wins_A, "wins_B": wins_B, "difference": wins_B - wins_A,
            "shared": len(shared), "original_only": len(a_only_keys), "bridge_only": len(b_only_keys),
            "shared_same_side": len(shared_same), "shared_changed_side": len(shared_changed),
            "shared_probability_only_changes": sum(abs(A_by[k]["pA"] - B_by[k]["pB"]) > 1e-9 for k in shared_same),
            "shared_wins_A": shared_wins_A, "shared_wins_B": shared_wins_B,
            "original_only_wins": a_only_wins, "original_only_losses": len(a_only_keys) - a_only_wins,
            "bridge_only_wins": b_only_wins, "bridge_only_losses": len(b_only_keys) - b_only_wins,
            "identity": f"{wins_B} = {shared_wins_B} + {b_only_wins}; {wins_A} = {shared_wins_A} + {a_only_wins}; difference = {(shared_wins_B - shared_wins_A) + b_only_wins - a_only_wins}",
            "group_stats": {
                "shared": group_stats(B_by, shared),
                "original_only": group_stats(A_by, a_only_keys),
                "bridge_only": group_stats(B_by, b_only_keys),
            },
            "rank": {
                "spearman_all_settled": spearman_all,
                "median_abs_displacement_union_top": statistics.median(displacements) if displacements else None,
                "mean_abs_displacement_union_top": statistics.fmean(displacements) if displacements else None,
                "max_abs_displacement_union_top": max(displacements) if displacements else None,
            },
            "tie_handling": {
                "A": tie_info(e3_a_top, e3_ok, "pA"),
                "B": tie_info(e3_b_top, e3_ok, "pB"),
            },
            "sets": {
                "A_top800_prediction_ids": sorted(A_by),
                "B_top800_prediction_ids": sorted(B_by),
                "shared_prediction_ids": sorted(shared),
                "original_only_prediction_ids": sorted(a_only_keys),
                "bridge_only_prediction_ids": sorted(b_only_keys),
                "shared_changed_side_prediction_ids": sorted(shared_changed),
            },
        },
        "phase3_paired": {
            "n": len(e3_ok),
            "both_correct": both_correct, "both_incorrect": both_wrong,
            "A_correct_B_incorrect": a_only, "A_incorrect_B_correct": b_only,
            "mcnemar_exact": mcnemar,
            "A": ma, "B": mb,
            "brier_improvement_absolute": ma["brier"] - mb["brier"],
            "brier_improvement_relative": 1 - mb["brier"] / ma["brier"],
            "log_loss_improvement_absolute": ma["log_loss"] - mb["log_loss"],
            "log_loss_improvement_relative": 1 - mb["log_loss"] / ma["log_loss"],
            "paired_day_block": paired_day_bootstrap(e3_ok, "pA", "pB"),
            "by_tour": tour,
        },
        "phase4_mechanism": {
            "affected": len(affected), "side_flips": len(side_flips),
            "target_slots": 2 * len(e3),
            "bridged_target_slots": sum(
                bool((r["p1_resolution"] or {}).get("bridged")) + bool((r["p2_resolution"] or {}).get("bridged"))
                for r in e3
            ),
            "distinct_original_players": len({r["p1_live"] for r in e3} | {r["p2_live"] for r in e3}),
            "distinct_model_players": len({
                (r["p1_resolution"] or {}).get("resolved_player_id") for r in e3
            } | {
                (r["p2_resolution"] or {}).get("resolved_player_id") for r in e3
            }),
            "affected_target_id_changed": sum(
                bool((r["p1_resolution"] or {}).get("bridged") or (r["p2_resolution"] or {}).get("bridged"))
                for r in affected
            ),
            "affected_target_id_unchanged": sum(
                not bool((r["p1_resolution"] or {}).get("bridged") or (r["p2_resolution"] or {}).get("bridged"))
                for r in affected
            ),
            "recovered_matches_all_settled": sum(
                max(0, r["B"]["player_1_overall_matches"] - r["A"]["player_1_overall_matches"])
                + max(0, r["B"]["player_2_overall_matches"] - r["A"]["player_2_overall_matches"])
                for r in e3
            ),
            "direct_recovery": summarize(direct),
            "indirect_only": summarize(indirect),
            "identity_resolution_methods": dict(Counter(
                (r["p1_resolution"] or {}).get("method") for r in e3
            )),
            "itf_transition": "UNRESOLVED: no ITF results are ingested; ITF transition and genuine debut are a union, not separable.",
        },
        "phase5_cross_experiment_top800": p5,
        "validation": {
            "E1_settled_is_3152": len(e1) == 3152,
            "E2_settled_is_3201": len(e2) == 3201,
            "E3_settled_is_3223": len(e3) == 3223,
            "E2_top800_original_is_575": e2_top["original"]["metrics"]["wins"] == 575,
            "E2_top800_bridged_is_594": e2_top["bridged"]["metrics"]["wins"] == 594,
            "E3_top800_A_is_596": wins_A == 596,
            "E3_top800_B_is_655": wins_B == 655,
            "top800_identity_holds": (wins_B - wins_A) == (shared_wins_B - shared_wins_A) + b_only_wins - a_only_wins,
        },
        "provenance": {
            "stored_baseline_probabilities": "E1/E2 original = predictions.win_probability (stored forecasts); E3 A = reconstructed unbridged replay, NOT stored.",
            "research_code_versions": {
                "E1_E2_evidence_commit": "853a3a3",
                "E3_evidence_commit": "9f68cea",
                "PR388_head": "2a6053a5",
                "PR392_reconciled": "af6e09be",
                "merge_commit": "2a44627",
            },
            "reference_policy": {
                "E1_E2_E3_historical": "pre-v2 permissive-equivalent resolver semantics",
                "current_branch": "identity_reference_pit_v3 (strict)",
                "v3_remeasurement": "NOT_RUN_ON_PRODUCTION_SCHEMA; 0025 not applied",
            },
            "database_metadata": db_metadata(),
        },
        "blockers": [
            "Reference-policy v3 (0025) not applied to production and therefore not re-measured.",
            "No independent wall-clock forward receipt; 52 re-issued targets remain quarantined and 17 pre-outcome records are not independent receipts.",
            "The historical population is development-reused, not untouched validation.",
        ],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    sanitised = {
        "version": "A1_TOP800_SETS_20261126",
        "purpose": "Sanitised exact top-800 fixture sets for independent bookmaker audit.",
        "contains_pii": False,
        "freeze": report["freeze"],
        "sets": {
            "E3_A_top800": [
                {"prediction_id": r["prediction_id"], "match_id": r["match_id"],
                 "prediction_run_id": r["prediction_run_id"], "match_version_id": r["match_version_id"],
                 "match_start_at": r["match_start_at"], "tour": r["tour"],
                 "selection_side": p1_side(r["pA"]), "p1": r["pA"], "confidence": conf(r["pA"]),
                 "winner_side": r["winner_side"], "won": bool(won(r["pA"], r["y"])),
                 "decomposition_group": "shared" if set_key(r) in shared else "original_only"}
                for r in sorted(e3_a_top, key=lambda r: conf(r["pA"]), reverse=True)
            ],
            "E3_B_top800": [
                {"prediction_id": r["prediction_id"], "match_id": r["match_id"],
                 "prediction_run_id": r["prediction_run_id"], "match_version_id": r["match_version_id"],
                 "match_start_at": r["match_start_at"], "tour": r["tour"],
                 "selection_side": p1_side(r["pB"]), "p1": r["pB"], "confidence": conf(r["pB"]),
                 "winner_side": r["winner_side"], "won": bool(won(r["pB"], r["y"])),
                 "decomposition_group": "shared" if set_key(r) in shared else "bridge_only"}
                for r in sorted(e3_b_top, key=lambda r: conf(r["pB"]), reverse=True)
            ],
        },
        "decomposition": {
            "shared": len(shared), "original_only": len(a_only_keys), "bridge_only": len(b_only_keys),
            "shared_wins_A": shared_wins_A, "shared_wins_B": shared_wins_B,
            "original_only_wins": a_only_wins, "original_only_losses": len(a_only_keys) - a_only_wins,
            "bridge_only_wins": b_only_wins, "bridge_only_losses": len(b_only_keys) - b_only_wins,
            "identity": report["phase2_top800_decomposition"]["identity"],
        },
    }
    Path(args.sets_out).write_text(json.dumps(sanitised, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "phase2_top800_decomposition"}, indent=2, default=str)[:7000])
    print("wrote", out, "and", args.sets_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
