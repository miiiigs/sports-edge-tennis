"""Complete calibration reconciliation of the original 143-match marginal cohort.

Research-only, read-only. This module does not change Manager C, any threshold,
weight, settlement rule or forecasting policy. It reproduces the registered
``MANAGER_C_ORIGINAL_SOURCE_V1`` cohort (broad minus narrow, exact match IDs) and
reports the full probability distribution and calibration decomposition that the
audit asked for, rather than only the <20-history subgroup.

Definitions
-----------
* Selected probability is the probability of the side Manager C selected. For
  side 2 it is ``1 - p1``; scoring on ``p1`` is mathematically equivalent for
  Brier/log loss but the expected-win sum must use the selected side.
* Confidence bands use ``max(selected p, 1 - selected p)``.
* Expected wins are ``sum(selected p)``, never a recalibration.
* The independent winner-count variance is ``sum p(1-p)``. Telemetry is not
  independent: selections share favourites, opponents and tournament
  conditions. A player-clustered bootstrap supplies the dependence-aware
  variance and is reported next to the independent figure.
* History depth follows the Agent 1 PIT definition used by the registered
  Challenger history study. The identity bridge is the existing fail-closed
  alias-uniqueness resolver, applied for research history counts only.
"""
from __future__ import annotations

import argparse
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import psycopg

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import _utc, _wilson
from sports_edge_tennis.validation.manager_c_challenger_history import (
    LIVE_SOURCES,
    MIN_HISTORY,
    _bootstrap_ci,
    _classify,
    _has_historical_identity,
    _sha,
    build_index,
    load_history_index,
    read_source,
    resolve_mapping,
    selected_probability,
)
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY
from sports_edge_tennis.validation.manager_c_original_source import (
    HISTORICAL_AS_OF,
    TRAIN_START,
    VALIDATION_END,
    select_cohorts,
)

VERSION = "MANAGER_C_MARGINAL_CALIBRATION_V1"
BOOTSTRAP_ITERATIONS = 4000
BOOTSTRAP_SEED = 20261126
BANDS = (
    ("[0.55,0.60)", 0.55, 0.60),
    ("[0.60,0.65)", 0.60, 0.65),
    ("[0.65,0.70)", 0.65, 0.70),
    ("[0.70,0.75)", 0.70, 0.75),
    ("[0.75,0.80)", 0.75, 0.80),
    ("[0.80,1.00]", 0.80, 1.0000000001),
)
DEPTH_BUCKETS = ((0, 0), (1, 9), (10, 19), (20, 49), (50, 99), (100, 10**9))
SURFACE_BUCKETS = ((0, 0), (1, 4), (5, 9), (10, 19), (20, 10**9))
INACTIVITY_BUCKETS = (
    ("0-30", 0, 30), ("31-90", 31, 90), ("91-180", 91, 180),
    ("181-365", 181, 365), ("366+", 366, 10**9),
)


def confidence_band(confidence: float) -> str:
    for label, low, high in BANDS:
        if low <= confidence < high:
            return label
    return "BELOW_0.55"


def bucket(value: int | None, buckets=((0, 0), (1, 1), (2, 10**9))) -> str:
    if value is None:
        return "UNKNOWN"
    for low, high in buckets:
        if low <= value <= high:
            return str(low) if low == high else f"{low}-{high}" if high < 10**9 else f"{low}+"
    return "UNKNOWN"


def expected_variance(rows: list[dict]) -> dict[str, Any]:
    """Independent binomial variance and a dependence-aware cluster bootstrap."""
    settled = [r for r in rows if r["winner"] in (1, 2)]
    independent = sum(selected_probability(r) * (1 - selected_probability(r))
                      for r in settled)
    clusters: dict[int, list[dict]] = defaultdict(list)
    for row in settled:
        clusters[row["cluster"]].append(row)
    keys = sorted(clusters)
    out = {
        "settled": len(settled),
        "independent_variance": independent,
        "independent_sd": math.sqrt(independent) if independent else None,
        "clusters": len(keys),
        "largest_cluster": max((len(v) for v in clusters.values()), default=0),
        "limitation": (
            "sum p(1-p) is exact only for independent Bernoulli selections. "
            "Selections share favourites, opponents and tournament conditions; "
            "the cluster bootstrap is the dependence-aware estimate and still "
            "cannot capture unobserved shared shocks."
        ),
    }
    if len(keys) < 2 or len(settled) < 2:
        out["cluster_bootstrap"] = {"available": False}
        return out
    rng = random.Random(BOOTSTRAP_SEED + 11)
    residual, wins = [], []
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample = [clusters[keys[rng.randrange(len(keys))]] for _ in keys]
        picked = [row for group in sample for row in group]
        w = sum(r["winner"] == r["side"] for r in picked)
        e = sum(selected_probability(r) for r in picked)
        wins.append(w)
        residual.append(w - e)
    mean_w = sum(wins) / len(wins)
    cluster_var = sum((w - mean_w) ** 2 for w in wins) / (len(wins) - 1)
    out["cluster_bootstrap"] = {
        "available": True,
        "iterations": BOOTSTRAP_ITERATIONS,
        "seed": BOOTSTRAP_SEED + 11,
        "wins_variance": cluster_var,
        "wins_sd": math.sqrt(cluster_var),
        "residual_variance": sum(
            (x - sum(residual) / len(residual)) ** 2 for x in residual
        ) / (len(residual) - 1),
        "design_effect": (cluster_var / independent) if independent else None,
        "interpretation": (
            "A design effect above 1 means the naive independent SD understates "
            "the uncertainty of the winner count."
        ),
    }
    actual = sum(r["winner"] == r["side"] for r in settled)
    expected_total = sum(selected_probability(r) for r in settled)
    residual_sd = math.sqrt(out["cluster_bootstrap"]["residual_variance"])
    out["cluster_bootstrap"]["residual_z"] = (
        (actual - expected_total) / residual_sd if residual_sd else None)
    return out


def band_block(rows: list[dict]) -> dict[str, Any]:
    settled = [r for r in rows if r["winner"] in (1, 2)]
    if not settled:
        return {
            "settled": 0, "wins": 0, "losses": 0, "expected_wins": 0.0,
            "residual": 0.0, "mean_selected_probability": None,
            "accuracy": None, "accuracy_wilson95": None,
            "brier": None, "log_loss": None,
            "independent_variance": 0.0, "residual_z": None,
        }
    wins = sum(r["winner"] == r["side"] for r in settled)
    expected = sum(selected_probability(r) for r in settled)
    variance = sum(selected_probability(r) * (1 - selected_probability(r))
                   for r in settled)
    brier = sum((selected_probability(r) - int(r["winner"] == r["side"])) ** 2
                for r in settled) / len(settled)
    logloss = -sum(
        int(r["winner"] == r["side"]) * math.log(selected_probability(r))
        + int(r["winner"] != r["side"]) * math.log1p(-selected_probability(r))
        for r in settled
    ) / len(settled)
    return {
        "settled": len(settled),
        "wins": wins,
        "losses": len(settled) - wins,
        "expected_wins": expected,
        "residual": wins - expected,
        "residual_z": ((wins - expected) / math.sqrt(variance)) if variance else None,
        "mean_selected_probability": expected / len(settled),
        "accuracy": wins / len(settled),
        "accuracy_wilson95": _wilson(wins, len(settled)),
        "brier": brier,
        "log_loss": logloss,
        "independent_variance": variance,
    }


def grouped(rows: list[dict], key) -> dict[str, Any]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[str(key(row))].append(row)
    return {
        name: {**band_block(group), "clustered_bootstrap": _bootstrap_ci(group)}
        for name, group in sorted(buckets.items())
    }


def _history_depth(row: dict, corrected: bool) -> int:
    return row["fav_bridged_count"] if corrected else row["fav_raw_count"]


def _surface_depth(row: dict, corrected: bool) -> int:
    return (row["fav_bridged_surface_count"] if corrected
            else row["fav_surface_count"])


def _inactivity_bucket(row: dict) -> str:
    days = row["corrected_last_prior_days"]
    if days is None:
        return "UNKNOWN_START_DATE_INCOMPLETE"
    for label, low, high in INACTIVITY_BUCKETS:
        if low <= days <= high:
            return label
    return "UNKNOWN"


def _control_subgroup(row: dict) -> str:
    """Explicit, overlapping research subgroup; never a model input."""
    if row["fav_bridged_count"] < MIN_HISTORY:
        return "FAVOURITE_BRIDGED_UNDER_20"
    if row["fav_bridged_surface_count"] < 5:
        return "SURFACE_EXPERIENCE_UNDER_5"
    return "CORRECTED_HISTORY_HEALTHY"


def enrich_rows(data: dict[str, Any], *, history_rows: list[tuple],
                mapping: dict[int, int], conn: psycopg.Connection) -> list[dict]:
    """Enrich the exact marginal selection rows with PIT history evidence."""
    cohorts = select_cohorts(data)
    meta = data["meta"]
    raw_index = build_index(history_rows)
    bridged_index = build_index(history_rows, mapping)
    identity_cache: dict[int, bool] = {}
    rows: list[dict] = []
    for record in cohorts["groups"]["marginal"]:
        mid = record["match_id"]
        m = meta[mid]
        if m.get("player_1_id") is None or m.get("as_of") is None:
            raise ValueError("missing original orientation or cutoff")
        pick = record["candidates"][POLICY]
        if pick is None:
            raise ValueError("marginal row lacks a policy candidate")
        side, probability = int(pick[0]), float(pick[1])
        p1, p2 = int(m["player_1_id"]), int(m["player_2_id"])
        fav_id, dog_id = (p1, p2) if side == 1 else (p2, p1)
        cutoff = _utc(m["as_of"])
        fav_map = mapping.get(fav_id, fav_id)
        dog_map = mapping.get(dog_id, dog_id)
        raw_hist = raw_index.get(fav_id)
        bridged_hist = bridged_index.get(fav_map)
        dog_raw = raw_index.get(dog_id)
        dog_bridged = bridged_index.get(dog_map)
        raw_count = raw_hist.count(cutoff) if raw_hist else 0
        bridged_count = bridged_hist.count(cutoff) if bridged_hist else 0
        dog_raw_count = dog_raw.count(cutoff) if dog_raw else 0
        dog_bridged_count = dog_bridged.count(cutoff) if dog_bridged else 0
        first_start = (bridged_hist.first_start(cutoff) if bridged_hist else None)
        last_start = (bridged_hist.last_start(cutoff) if bridged_hist else None)
        starts_complete = (bridged_hist.start_dates_complete
                           if bridged_hist else True)
        if fav_id not in identity_cache:
            identity_cache[fav_id] = _has_historical_identity(
                conn, fav_id, LIVE_SOURCES)
        historical_identity = identity_cache[fav_id]
        labels = _classify(
            fav_id, raw=raw_count, bridged=bridged_count, cutoff=cutoff,
            first_start=first_start, last_start=last_start,
            historical_identity=historical_identity,
            resolved=fav_map != fav_id, starts_complete=starts_complete)
        surface = m.get("surface")
        rows.append({
            **record,
            "side": side, "p": probability,
            "selected_probability": selected_probability({"side": side, "p": probability}),
            "confidence": max(probability, 1 - probability),
            "probability_band": confidence_band(max(probability, 1 - probability)),
            "surface": surface or "UNKNOWN",
            "qualifying": bool(m.get("qualifying")),
            "competition_tier": m.get("competition_tier"),
            "cutoff": cutoff.isoformat(),
            "player_1_id": p1, "player_2_id": p2,
            "favourite_player_id": fav_id, "cluster": fav_id,
            "favourite_player_id_sha256": _sha(fav_id),
            "fav_raw_count": raw_count, "fav_bridged_count": bridged_count,
            "dog_raw_count": dog_raw_count, "dog_bridged_count": dog_bridged_count,
            "fav_surface_count": (raw_hist.surface_count(cutoff, surface)
                                  if raw_hist else 0),
            "fav_bridged_surface_count": (
                bridged_hist.surface_count(cutoff, surface)
                if bridged_hist else 0),
            "dog_surface_count": (dog_raw.surface_count(cutoff, surface)
                                  if dog_raw else 0),
            "fav_bridged_first_start": (first_start.isoformat()
                                        if first_start else None),
            "fav_bridged_last_start": (last_start.isoformat()
                                       if last_start else None),
            "identity_mapped": fav_map != fav_id,
            "identity_complete_raw": raw_count > 0 and dog_raw_count > 0,
            "identity_complete_bridged": bridged_count > 0 and dog_bridged_count > 0,
            "corrected_last_prior_days": labels["corrected_last_prior_days"],
            "corrected_career_days": labels["corrected_career_days"],
            **labels,
        })
    return rows


def analyse(rows: list[dict]) -> dict[str, Any]:
    settled = [r for r in rows if r["winner"] in (1, 2)]
    bands = {
        label: {
            **band_block([r for r in settled if r["probability_band"] == label]),
            "clustered_bootstrap": _bootstrap_ci(
                [r for r in settled if r["probability_band"] == label]),
        }
        for label, _, _ in BANDS
    }
    bands["BELOW_0.55"] = {
        **band_block([r for r in settled if r["probability_band"] == "BELOW_0.55"]),
        "clustered_bootstrap": _bootstrap_ci(
            [r for r in settled if r["probability_band"] == "BELOW_0.55"]),
    }
    overall = band_block(settled)
    low_corrected = [r for r in settled if r["fav_bridged_count"] < MIN_HISTORY]

    def identity_block(base: list[dict]) -> dict[str, Any]:
        low = [r for r in base if r["fav_bridged_count"] < MIN_HISTORY]
        return {
            "selections": len(base),
            "complete_raw": sum(r["identity_complete_raw"] for r in base),
            "complete_bridged": sum(r["identity_complete_bridged"] for r in base),
            "favourite_identity_mapped": sum(r["identity_mapped"] for r in base),
            "favourite_historical_identity_present": sum(
                r["historical_identity_present"] for r in base),
            "favourite_under_20_raw": sum(
                r["fav_raw_count"] < MIN_HISTORY for r in base),
            "favourite_under_20_bridged": len(low),
            "recovered_to_at_least_20": sum(
                "IDENTITY_GAP_RECOVERED" in r["flags"] for r in base),
            "unresolved_link": sum(
                "IDENTITY_LINK_UNRESOLVED" in r["flags"] for r in base),
            "mean_raw_count_under_20": (
                sum(r["fav_raw_count"] for r in base
                    if r["fav_raw_count"] < MIN_HISTORY)
                / sum(r["fav_raw_count"] < MIN_HISTORY for r in base)
                if any(r["fav_raw_count"] < MIN_HISTORY for r in base) else None),
            "mean_bridged_count_under_20_raw": (
                sum(r["fav_bridged_count"] for r in base
                    if r["fav_raw_count"] < MIN_HISTORY)
                / sum(r["fav_raw_count"] < MIN_HISTORY for r in base)
                if any(r["fav_raw_count"] < MIN_HISTORY for r in base) else None),
            "note": (
                "The existing bridge resolves live-only IDs through persisted "
                "bridges and unique TENNIS_DATA aliases. A zero count for "
                "non-live source rows is not evidence of missing identity; the "
                "bridge is an alias-uniqueness resolver, not a provider census."),
        }

    def class_block(base: list[dict]) -> dict[str, Any]:
        base_flags: Counter[str] = Counter()
        base_overlaps: Counter[str] = Counter()
        for row in base:
            base_flags.update(row["flags"])
            base_overlaps["+".join(row["flags"])] += 1
        return {
            "selections": len(base),
            "primary_counts": dict(sorted(Counter(
                r["primary"] for r in base).items())),
            "flag_counts": dict(sorted(base_flags.items())),
            "overlap_combinations": dict(sorted(base_overlaps.items())),
            "itf_separation": (
                "NOT_POSSIBLE: no ITF/FUT result is ingested. "
                "SHORT_CAREER_ITF_OR_DEBUT is a union, not a split."
            ),
            "start_date_incomplete": sum(
                "START_DATE_INCOMPLETE" in r["flags"] for r in base),
        }
    return {
        "version": VERSION,
        "policy": POLICY,
        "status": "DESCRIPTIVE_HISTORICAL_RESEARCH_ONLY",
        "historical_evaluation_previously_inspected": True,
        "independent_forward_validation": False,
        "settled": len(settled),
        "selected": len(rows),
        "reconciliation": {
            "actual_wins": overall["wins"],
            "expected_wins": overall["expected_wins"],
            "residual": overall["residual"],
            "mean_selected_probability": overall["mean_selected_probability"],
            "brier": overall["brier"],
            "log_loss": overall["log_loss"],
        },
        "bands": bands,
        "band_notes": {
            "55_60_empty_by_construction": (
                "The registered broad 40% cutoff is 0.6213299999999999, so no "
                "marginal selection can have confidence below 0.62133. The "
                "[0.55,0.60) band is reported as a measured zero, not omitted."
            ),
            "70_plus_sparse_by_construction": (
                "The registered narrow 20% cutoff is 0.7070145 and narrow picks "
                "are removed from marginal, so values above 0.7070145 cannot "
                "appear. The 0.70-0.75 band contains only 0.70-0.7070."
            ),
        },
        "expected_winner_variance": expected_variance(settled),
        "identity": identity_block(settled),
        "selected_identity": identity_block(rows),
        "history_depth_raw": grouped(
            settled, lambda r: bucket(_history_depth(r, False), DEPTH_BUCKETS)),
        "history_depth_bridged": grouped(
            settled, lambda r: bucket(_history_depth(r, True), DEPTH_BUCKETS)),
        "surface_experience_raw": grouped(
            settled, lambda r: bucket(_surface_depth(r, False), SURFACE_BUCKETS)),
        "surface_experience_bridged": grouped(
            settled, lambda r: bucket(_surface_depth(r, True), SURFACE_BUCKETS)),
        "inactivity": grouped(settled, _inactivity_bucket),
        "class_census": class_block(settled),
        "selected_class_census": class_block(rows),
        "control_by_probability_band": {
            label: {
                "all": band_block([r for r in settled if r["probability_band"] == label]),
                "by_subgroup": grouped(
                    [r for r in settled if r["probability_band"] == label],
                    _control_subgroup),
            } for label, _, _ in BANDS
        },
        "control_by_surface": grouped(settled, lambda r: r["surface"]),
        "control_by_qualifying": grouped(
            settled, lambda r: "QUALIFYING" if r["qualifying"] else "MAIN_DRAW"),
        "corrected_low_history_subgroup": {
            "settled": len(low_corrected),
            "wins": sum(r["winner"] == r["side"] for r in low_corrected),
            "expected_wins": sum(selected_probability(r) for r in low_corrected),
            "flags": dict(sorted(Counter(
                flag for r in low_corrected for flag in r["flags"]).items())),
            "ids_sha256": _sha(sorted(r["match_id"] for r in low_corrected)),
        },
        "limitations": [
            "November window previously inspected; not independent validation.",
            "Expected wins and residuals use the original selected-side probability, not a new calibration.",
            "Only ATP main and Challenger results are ingested; ITF transition cannot be separated from debut.",
            "Career and inactivity are not inferred when match_start_at is missing.",
            "History counts are research-derived from PIT-visible rows, not persisted original-run features.",
            "The identity bridge is an alias-uniqueness resolver, not a verified provider census.",
            "Selections are not independent; the cluster bootstrap is partial and does not model shared opponents.",
        ],
    }


def run(conn: psycopg.Connection, *, start=TRAIN_START, end=VALIDATION_END,
        as_of=HISTORICAL_AS_OF) -> dict[str, Any]:
    data = read_source(conn, start=start, end=end, as_of=as_of)
    history_rows = load_history_index(conn, as_of=_utc(as_of))
    cohorts = select_cohorts(data)
    target_players = {
        int(v) for record in cohorts["groups"]["marginal"]
        for v in (data["meta"][record["match_id"]].get("player_1_id"),
                  data["meta"][record["match_id"]].get("player_2_id"))
        if v is not None
    }
    history_players = {int(v) for row in history_rows for v in (row[1], row[2])}
    mapping = resolve_mapping(conn, target_players | history_players, as_of=_utc(as_of))
    rows = enrich_rows(data, history_rows=history_rows, mapping=mapping, conn=conn)
    challenger = [r for r in rows if r["tour"] == "CHALLENGER"]
    report = analyse(challenger)
    report["scope"] = "CHALLENGER"
    report["atp_main_context"] = analyse([r for r in rows if r["tour"] == "ATP_MAIN"])
    report["all_tour_context"] = analyse(rows)
    report["challenger_selected"] = len(challenger)
    report["challenger_settled"] = sum(r["winner"] in (1, 2) for r in challenger)
    report["source_hashes"] = {
        "dataset_sha256": data.get("source_sha256"),
        "full_source_sha256": data.get("full_source_sha256"),
        "registered_baseline_match": (
            data.get("source_sha256")
            == "e7d70fb821d7c69339adfce51bbd7fe35161adda49c595124d1791fef1b56863"),
    }
    report["cohort_thresholds"] = cohorts["thresholds"]
    report["cohort_sizes"] = {
        name: {
            "selected": len(group),
            "challenger": sum(r["tour"] == "CHALLENGER" for r in group),
            "challenger_settled": sum(
                r["tour"] == "CHALLENGER" and r["winner"] in (1, 2)
                for r in group),
        } for name, group in cohorts["groups"].items()
    }
    report["exclusions"] = data.get("exclusions")
    report["as_of"] = _utc(as_of).isoformat()
    report["row_ids_sha256"] = _sha(sorted(
        r["match_id"] for r in rows if r["tour"] == "CHALLENGER"))
    return report


def main(argv: list[str] | None = None) -> int:
    import json
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--as-of-utc", default=HISTORICAL_AS_OF.isoformat())
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("authorized TENNIS_DATABASE_URL required")
    as_of = datetime.fromisoformat(args.as_of_utc)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=15,
                         options="-c default_transaction_read_only=on") as conn:
        report = run(conn, as_of=as_of)
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
