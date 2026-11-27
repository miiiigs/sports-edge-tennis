"""Brian P1: Challenger favourites with limited observable history.

Read-only descriptive research built on the *unchanged* frozen Manager C
first-issue cohort. It does not train or retune Manager C, does not alter the
frozen envelope/weights/policy, and writes nothing.

Question
--------
An independent 95,100-match analysis reported calibration problems when
players with fewer than 20 prior matches are favourites. This module measures
that condition inside the existing narrow/broad/marginal Challenger cohorts:

  * how many selections have a favourite with <20 observable prior matches at
    the original forecast cutoff;
  * which part of that deficit is recoverable by the existing PIT identity
    bridge (``data.model_identity.resolve_model_player_ids``);
  * whether the deficit separates genuine inexperience, incomplete identity
    links, ITF-to-Challenger transition or return from inactivity - and which
    parts cannot be separated from the data we actually hold;
  * actual wins vs the original AI expected wins, residuals, accuracy, Brier,
    log loss, uncertainty and sample sizes, controlling for original
    probability, surface, competition level and history availability.

History definition
------------------
Prior matches are counted with exactly the Agent 1 PIT definition (see
``data.agent1_history.load_agent1_history``): a match is observable at cutoff
T when at least one result observation and one matching match version were
visible by T. The module builds the same eligible match set once and counts
through it, which was verified against the real loader at sampled cutoffs (see
the accompanying evidence document). ITF/FUT results are not ingested, so an
ITF transition cannot be distinguished from a genuine debut; that class is
reported explicitly as unresolved rather than guessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import psycopg

from sports_edge_tennis.data.model_identity import resolve_model_player_ids
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import _utc, _wilson
from sports_edge_tennis.validation.manager_c_forward_feature_ablation import (
    MIN_TRAIN,
    _fit,
    _score,
)
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY
from sports_edge_tennis.validation.manager_c_original_source import (
    HISTORICAL_AS_OF,
    TRAIN_END,
    TRAIN_START,
    VALIDATION_END,
    decode,
    original_sql,
    select_cohorts,
)

VERSION = "MANAGER_C_CHALLENGER_HISTORY_COMPLETENESS_V1"
MIN_HISTORY = 20
INACTIVITY_DAYS = 180
SHORT_CAREER_DAYS = 365
IDENTITY_RECOVERY_MIN = 5
BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_SEED = 20261126
PROBABILITY_BINS = ((0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 1.01))
SURFACES = ("HARD", "CLAY", "GRASS", "CARPET", "UNKNOWN")

# Match-level PIT visibility. ``results.first_visible`` is the earliest moment at
# which *any* result observation for the match was fully visible. A match is in
# Agent 1 history at cutoff T exactly when first_visible <= T; the match version
# supplies the canonical player orientation. This was validated against
# ``load_agent1_history`` (identical match IDs and per-player counts).
HISTORY_INDEX_SQL = """
WITH results AS (
    SELECT match_id,
           min(GREATEST(as_of, source_observed_at)) AS first_visible
    FROM match_result_observations
    WHERE as_of <= %(as_of)s AND source_observed_at <= %(as_of)s
    GROUP BY match_id
)
SELECT r.match_id, v.player_1_id, v.player_2_id, v.surface::text,
       v.tour_scope::text, v.competition_tier::text, r.first_visible,
       v.match_start_at
FROM results r
JOIN LATERAL (
    SELECT mv.match_id, mv.player_1_id, mv.player_2_id, mv.surface,
           mv.tour_scope, mv.competition_tier, mv.match_start_at
    FROM match_versions mv
    WHERE mv.match_id = r.match_id
      AND mv.as_of <= %(as_of)s
      AND mv.source_observed_at <= %(as_of)s
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
    LIMIT 1
) v ON TRUE
"""


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def extended_original_sql() -> str:
    """The frozen original-source SQL plus player orientation and cutoff only.

    The extra columns are appended after every column the existing decoders
    read, so the registered ``dataset_sha256`` is unaffected. The marker is
    asserted so a future edit to the frozen SQL fails loudly instead of
    silently dropping lineage.
    """
    marker = "AS p2_rating_sample_visible\n    FROM original o"
    extra = ("AS p2_rating_sample_visible,v.player_1_id,v.player_2_id,"
             "p.as_of\n    FROM original o")
    sql = original_sql()
    if marker not in sql:
        raise RuntimeError("frozen original SQL changed; update the extension")
    return sql.replace(marker, extra)


def read_source(conn: psycopg.Connection, *, start, end, as_of) -> dict[str, Any]:
    if not conn.autocommit:
        raise ValueError("dedicated autocommit connection required")
    start, end, as_of = map(_utc, (start, end, as_of))
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '180s'")
        conn.execute("SET LOCAL lock_timeout = '3s'")
        if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
            raise RuntimeError("read-only transaction not established")
        raw = conn.execute(extended_original_sql(), {
            "start": start, "end": end, "as_of": as_of}).fetchall()
        version_ids = sorted({int(row[23]) for row in raw if row[23] is not None})
        tiers = {
            int(version_id): tier
            for version_id, tier in conn.execute(
                """
                SELECT match_version_id, competition_tier::text
                FROM match_versions WHERE match_version_id = ANY(%s)
                """,
                (version_ids,),
            ).fetchall()
        } if version_ids else {}
    data = decode(raw, start=start, end=end, as_of=as_of)
    meta = data["meta"]
    for row in raw:
        mid = int(row[0])
        if mid not in meta:
            continue
        if row[6] is None:
            continue
        entry = meta[mid]
        for index, key in ((28, "player_1_id"), (29, "player_2_id"),
                           (30, "as_of")):
            value = row[index]
            if entry.get(key) not in (None, value):
                raise ValueError(f"inconsistent original fixture {key}")
            entry[key] = value
        entry["competition_tier"] = tiers.get(int(row[23])) if row[23] else None
    data["raw"] = raw
    data["cutoffs"] = {
        mid: entry["as_of"] for mid, entry in meta.items()
        if entry.get("as_of") is not None
    }
    return data


def load_history_index(conn: psycopg.Connection, *, as_of) -> list[tuple]:
    as_of = _utc(as_of)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '180s'")
        if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
            raise RuntimeError("read-only transaction not established")
        return conn.execute(HISTORY_INDEX_SQL, {"as_of": as_of}).fetchall()


class PlayerHistory:
    """Per-player PIT history with prefix aggregates for bounded lookups."""

    __slots__ = (
        "prefix_max_start",
        "prefix_min_start",
        "rows",
        "start_dates_complete",
        "visible",
    )

    def __init__(self, rows: list[tuple]) -> None:
        rows = sorted(rows, key=lambda r: r[0])
        self.rows = rows
        self.visible = [r[0] for r in rows]
        pmin, pmax = [], []
        lo = hi = None
        complete = True
        for row in rows:
            start = row[1]
            if start is None:
                # SACKMANN historical versions carry no match_start_at in
                # production (0003 intentionally dropped NOT NULL). Career and
                # inactivity must not be inferred from a partial date range.
                complete = False
                pmin.append(lo)
                pmax.append(hi)
                continue
            lo = start if lo is None or start < lo else lo
            hi = start if hi is None or start > hi else hi
            pmin.append(lo)
            pmax.append(hi)
        self.prefix_min_start = pmin
        self.prefix_max_start = pmax
        self.start_dates_complete = complete

    def count(self, cutoff: datetime) -> int:
        return bisect_right(self.visible, cutoff)

    def window(self, cutoff: datetime) -> list[tuple]:
        return self.rows[:bisect_right(self.visible, cutoff)]

    def first_start(self, cutoff: datetime) -> datetime | None:
        k = bisect_right(self.visible, cutoff)
        return self.prefix_min_start[k - 1] if k else None

    def last_start(self, cutoff: datetime) -> datetime | None:
        k = bisect_right(self.visible, cutoff)
        return self.prefix_max_start[k - 1] if k else None

    def surface_count(self, cutoff: datetime, surface: str | None) -> int:
        if surface is None:
            return 0
        return sum(1 for row in self.window(cutoff) if row[2] == surface)

    def level_counts(self, cutoff: datetime) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for row in self.window(cutoff):
            counts[row[3] or "UNKNOWN"] += 1
        return dict(sorted(counts.items()))


def build_index(rows: list[tuple], mapping: dict[int, int] | None = None
                ) -> dict[int, PlayerHistory]:
    buckets: dict[int, list[tuple]] = defaultdict(list)
    for match_id, p1, p2, surface, tour, tier, visible, start_at in rows:
        p1, p2 = int(p1), int(p2)
        if mapping:
            m1, m2 = mapping.get(p1, p1), mapping.get(p2, p2)
            if m1 != m2:
                p1, p2 = m1, m2
            # A colliding bridge keeps both raw IDs, exactly as Agent 1 does.
        payload = (visible, start_at, surface, tour, tier, match_id)
        buckets[p1].append(payload)
        if p2 != p1:
            buckets[p2].append(payload)
    return {pid: PlayerHistory(items) for pid, items in buckets.items()}


def resolve_mapping(conn: psycopg.Connection, player_ids, *, as_of) -> dict[int, int]:
    return resolve_model_player_ids(conn, player_ids=player_ids, as_of=_utc(as_of))


def _has_historical_identity(conn: psycopg.Connection, player_id: int,
                             live_sources: tuple[str, ...]) -> bool:
    return conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM player_source_ids
            WHERE player_id = %s AND source_system <> ALL(%s))
        """,
        (player_id, list(live_sources)),
    ).fetchone()[0]


LIVE_SOURCES = ("API_TENNIS", "SPORTRADAR", "ODDSPAPI", "PINNAPI")


def _classify(player_id: int, *, raw: int, bridged: int, cutoff: datetime,
              first_start: datetime | None, last_start: datetime | None,
              historical_identity: bool, resolved: bool,
              starts_complete: bool) -> dict[str, Any]:
    """Evidence-based labels; overlapping flags stay visible.

    ``ITF`` cannot be separated from a genuine debut because no ITF result is
    ingested. Any short observable career is therefore reported as
    ``SHORT_CAREER_ITF_OR_DEBUT``; it is never labelled genuine inexperience.
    """
    flags: list[str] = []
    if raw == 0:
        flags.append("NO_OBSERVABLE_HISTORY")
    if raw < MIN_HISTORY:
        flags.append("FAVOURITE_UNDER_20")
    if bridged > raw and raw < MIN_HISTORY:
        if bridged >= MIN_HISTORY or bridged - raw >= IDENTITY_RECOVERY_MIN:
            flags.append("IDENTITY_GAP_RECOVERED")
        elif bridged > raw:
            flags.append("IDENTITY_GAP_PARTIAL")
    if raw < MIN_HISTORY and not resolved and not historical_identity:
        flags.append("IDENTITY_LINK_UNRESOLVED")
    last_gap = (cutoff - last_start).days if last_start is not None else None
    career_days = ((cutoff - first_start).days if first_start is not None else None)
    if not starts_complete:
        # Do not invent a career start or an inactivity gap from a partial
        # date range; the historical corpus has versions without start time.
        flags.append("START_DATE_INCOMPLETE")
        last_gap = None
        career_days = None
    else:
        if raw and last_gap is not None and last_gap >= INACTIVITY_DAYS:
            flags.append("RETURN_FROM_INACTIVITY")
        if (raw < MIN_HISTORY and career_days is not None
                and career_days <= SHORT_CAREER_DAYS):
            flags.append("SHORT_CAREER_ITF_OR_DEBUT")
    if raw and raw < MIN_HISTORY and not any(
            f in flags for f in ("IDENTITY_GAP_RECOVERED", "RETURN_FROM_INACTIVITY",
                                 "SHORT_CAREER_ITF_OR_DEBUT")):
        flags.append("LOW_HISTORY_OTHER")

    if "IDENTITY_GAP_RECOVERED" in flags:
        primary = "IDENTITY_GAP_RECOVERED"
    elif "RETURN_FROM_INACTIVITY" in flags:
        primary = "RETURN_FROM_INACTIVITY"
    elif "SHORT_CAREER_ITF_OR_DEBUT" in flags:
        primary = "SHORT_CAREER_ITF_OR_DEBUT"
    elif "NO_OBSERVABLE_HISTORY" in flags:
        primary = "NO_OBSERVABLE_HISTORY"
    elif "LOW_HISTORY_OTHER" in flags:
        primary = "LOW_HISTORY_OTHER"
    else:
        primary = "HISTORY_AT_LEAST_20"
    return {"primary": primary, "flags": sorted(flags),
            "corrected_last_prior_days": last_gap,
            "corrected_career_days": career_days,
            "historical_identity_present": historical_identity,
            "identity_resolved": resolved}


def _bin(p: float) -> str:
    for low, high in PROBABILITY_BINS:
        if low <= p < high:
            return f"[{low:.2f},{high:.2f})"
    return "outside"


def selected_probability(row: dict) -> float:
    """Probability of the *selected side*.

    Manager C stores the uncalibrated mean probability of player 1 together
    with the side it selects. For side 2 the selected-side probability is the
    complement, so this must never be used as if it were always P(side 1).
    """
    return row["p"] if row["side"] == 1 else 1 - row["p"]


def _stats(rows: list[dict]) -> dict[str, Any]:
    settled = [r for r in rows if r["winner"] in (1, 2)]
    wins = sum(r["winner"] == r["side"] for r in settled)
    pairs = [(r["p"], int(r["winner"] == 1)) for r in settled]
    expected = sum(selected_probability(r) for r in settled)
    variance = sum(selected_probability(r) * (1 - selected_probability(r))
                   for r in settled)
    residual = wins - expected
    n = len(settled)
    return {
        "selected": len(rows), "settled": n, "ungraded": len(rows) - n,
        "wins": wins, "losses": n - wins,
        "accuracy": wins / n if n else None,
        "accuracy_wilson95": _wilson(wins, n),
        "expected_wins": expected, "residual": residual,
        "residual_standard_error": math.sqrt(variance) if variance else None,
        "residual_z": residual / math.sqrt(variance) if variance else None,
        "mean_selected_probability": expected / n if n else None,
        "brier": sum((p - y) ** 2 for p, y in pairs) / n if n else None,
        "log_loss": (-sum(y * math.log(p) + (1 - y) * math.log1p(-p)
                          for p, y in pairs) / n) if n else None,
        "ids_sha256": _sha(sorted(r["match_id"] for r in rows)),
        "_settled_scores": [(r["match_id"], r["side"], r["winner"],
                             selected_probability(r), r["cluster"])
                            for r in settled],
    }


def _bootstrap_ci(rows: list[dict]) -> dict[str, Any]:
    """Player-clustered bootstrap: selections by the same favourite are dependent."""
    settled = [r for r in rows if r["winner"] in (1, 2)]
    clusters: dict[int, list[dict]] = defaultdict(list)
    for row in settled:
        clusters[row["cluster"]].append(row)
    keys = sorted(clusters)
    if len(keys) < 2 or len(settled) < 2:
        return {"available": False, "clusters": len(keys)}
    rng = random.Random(BOOTSTRAP_SEED)
    accuracies, residuals, briers, losses = [], [], [], []
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample = [clusters[keys[rng.randrange(len(keys))]] for _ in keys]
        picked = [row for group in sample for row in group]
        n = len(picked)
        if not n:
            continue
        wins = sum(r["winner"] == r["side"] for r in picked)
        expected = sum(r["p"] for r in picked)
        accuracies.append(wins / n)
        residuals.append(wins - expected)
        briers.append(sum((r["p"] - int(r["winner"] == 1)) ** 2 for r in picked) / n)
        losses.append(-sum(
            int(r["winner"] == 1) * math.log(r["p"])
            + int(r["winner"] != 1) * math.log1p(-r["p"]) for r in picked) / n)

    def interval(values):
        values = sorted(values)
        return [values[int(.025 * len(values))],
                values[min(len(values) - 1, int(.975 * len(values)))]]

    return {"available": True, "clusters": len(keys), "iterations": BOOTSTRAP_ITERATIONS,
            "seed": BOOTSTRAP_SEED, "accuracy95": interval(accuracies),
            "residual95": interval(residuals), "brier95": interval(briers),
            "log_loss95": interval(losses)}


def _public(stats: dict) -> dict:
    return {k: v for k, v in stats.items() if not k.startswith("_")}


def _group_block(rows: list[dict]) -> dict[str, Any]:
    out = _public(_stats(rows))
    out["clustered_bootstrap"] = _bootstrap_ci(rows)
    return out


def _stratified(rows: list[dict], key) -> dict[str, Any]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[key(row)].append(row)
    return {name: _group_block(group) for name, group in sorted(buckets.items())}


def analyse_rows(rows: list[dict]) -> dict[str, Any]:
    low = [r for r in rows if r["fav_raw_count"] < MIN_HISTORY]
    healthy = [r for r in rows if r["fav_raw_count"] >= MIN_HISTORY]
    low_bridged = [r for r in rows if r["fav_bridged_count"] < MIN_HISTORY]
    return {
        "all": _group_block(rows),
        "favourite_under_20_raw": _group_block(low),
        "favourite_at_least_20_raw": _group_block(healthy),
        "favourite_under_20_bridged": _group_block(low_bridged),
        "favourite_under_20_raw_challenger": _group_block(
            [r for r in low if r["tour"] == "CHALLENGER"]),
        "favourite_under_20_raw_atp": _group_block(
            [r for r in low if r["tour"] == "ATP_MAIN"]),
        "by_probability_bin_under_20": _stratified(low, lambda r: r["probability_bin"]),
        "by_probability_bin_at_least_20": _stratified(healthy, lambda r: r["probability_bin"]),
        "by_surface_under_20": _stratified(low, lambda r: r["surface"] or "UNKNOWN"),
        "by_surface_at_least_20": _stratified(healthy, lambda r: r["surface"] or "UNKNOWN"),
        "by_primary_class": _stratified(low, lambda r: r["primary"]),
        "by_primary_class_challenger": _stratified(
            [r for r in low if r["tour"] == "CHALLENGER"], lambda r: r["primary"]),
        "by_history_count": _stratified(
            rows, lambda r: ("0" if r["fav_raw_count"] == 0 else
                             "1-9" if r["fav_raw_count"] < 10 else
                             "10-19" if r["fav_raw_count"] < 20 else "20+")),
        "by_qualifying": _stratified(low, lambda r: "QUALIFYING" if r["qualifying"] else "MAIN_DRAW"),
    }


def class_census(rows: list[dict]) -> dict[str, Any]:
    low = [r for r in rows if r["fav_raw_count"] < MIN_HISTORY]
    flag_counts: Counter[str] = Counter()
    overlap: Counter[str] = Counter()
    for row in low:
        flag_counts.update(row["flags"])
        overlap["+".join(row["flags"])] += 1
    return {
        "selections": len(rows), "favourite_under_20": len(low),
        "favourite_under_20_challenger": sum(r["tour"] == "CHALLENGER" for r in low),
        "no_observable_history": sum(r["fav_raw_count"] == 0 for r in low),
        "primary_counts": dict(sorted(Counter(r["primary"] for r in low).items())),
        "flag_counts": dict(sorted(flag_counts.items())),
        "overlap_combinations": dict(sorted(overlap.items())),
        "identity_bridge": {
            "recovered_to_at_least_20": sum(
                "IDENTITY_GAP_RECOVERED" in r["flags"] for r in low),
            "partial_gain": sum("IDENTITY_GAP_PARTIAL" in r["flags"] for r in low),
            "unresolved_link": sum("IDENTITY_LINK_UNRESOLVED" in r["flags"] for r in low),
            "mean_raw_count": (sum(r["fav_raw_count"] for r in low) / len(low)) if low else None,
            "mean_bridged_count": (sum(r["fav_bridged_count"] for r in low) / len(low)) if low else None,
        },
        "itf_separation": ("NOT_POSSIBLE: no ITF/FUT result is ingested. "
                           "SHORT_CAREER_ITF_OR_DEBUT is a union, not a split."),
        "start_date_incomplete": sum(
            "START_DATE_INCOMPLETE" in r["flags"] for r in low),
    }


def _row_features(row: dict) -> tuple:
    p = row["p"]  # P(player 1 wins)
    x = (1.0, math.log(p / (1 - p)))
    return x, x + (
        1.0 if row["fav_raw_count"] < MIN_HISTORY else 0.0,
        math.log1p(row["fav_raw_count"]),
        1.0 if row["fav_raw_count"] == 0 else 0.0,
        1.0 if (row["corrected_last_prior_days"] or 0) >= INACTIVITY_DAYS else 0.0,
    )


def _fit_pair(train: list[dict]) -> dict[str, Any]:
    data = []
    for row in train:
        if row["winner"] not in (1, 2):
            continue
        x_cal, x_hist = _row_features(row)
        y = int(row["winner"] == 1)
        data.append((x_cal, x_hist, y))
    if len(data) < MIN_TRAIN or len({d[2] for d in data}) != 2:
        return {"status": "INSUFFICIENT_OR_SINGLE_CLASS_TRAINING",
                "training_n": len(data)}
    w_cal = _fit([(x, y) for x, _, y in data])
    w_hist = _fit([(x, y) for _, x, y in data])
    return {"status": "FITTED", "training_n": len(data),
            "calibration_only_weights": w_cal, "history_added_weights": w_hist,
            "history_added_terms": ["logit_p", "under_20", "log1p_count",
                                    "zero_history", "inactive_180d"]}


def _paired_delta_ci(rows: list[dict], scores_a: dict, scores_b: dict) -> dict[str, Any]:
    clusters: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        if row["winner"] in (1, 2):
            clusters[row["cluster"]].append(row)
    keys = sorted(clusters)
    if len(keys) < 2 or not rows:
        return {"available": False, "clusters": len(keys)}

    def delta(group):
        a = sum((scores_a[r["match_id"]] - int(r["winner"] == 1)) ** 2
                for r in group)
        b = sum((scores_b[r["match_id"]] - int(r["winner"] == 1)) ** 2
                for r in group)
        return (a - b) / len(group)

    rng = random.Random(BOOTSTRAP_SEED + 1)
    deltas = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        picked = [row for _ in keys for row in clusters[keys[rng.randrange(len(keys))]]]
        if picked:
            deltas.append(delta(picked))
    deltas.sort()
    return {"available": True, "clusters": len(keys),
            "iterations": BOOTSTRAP_ITERATIONS,
            "brier_improvement_95": [deltas[int(.025 * len(deltas))],
                                     deltas[min(len(deltas) - 1, int(.975 * len(deltas)))]],
            "brier_improvement_mean": sum(deltas) / len(deltas)}


def _logistic_comparison(train: list[dict], validation: list[dict]) -> dict[str, Any]:
    fit = _fit_pair(train)
    out = dict(fit)
    if fit["status"] != "FITTED":
        return out
    data = [r for r in validation if r["winner"] in (1, 2)]
    if not data:
        out["status"] = "NO_VALIDATION_OUTCOMES"
        return out
    w_cal, w_hist = fit["calibration_only_weights"], fit["history_added_weights"]
    cal, hist = {}, {}
    for row in data:
        x_cal, x_hist = _row_features(row)
        cal[row["match_id"]] = _score(w_cal, x_cal)
        hist[row["match_id"]] = _score(w_hist, x_hist)

    def score_block(scores):
        pairs = [(scores[r["match_id"]], int(r["winner"] == 1)) for r in data]
        return {
            "brier": sum((p - y) ** 2 for p, y in pairs) / len(pairs),
            "log_loss": -sum(y * math.log(max(p, 1e-12))
                             + (1 - y) * math.log(max(1 - p, 1e-12))
                             for p, y in pairs) / len(pairs),
        }

    cal_block, hist_block = score_block(cal), score_block(hist)
    out.update({
        "validation_n": len(data),
        "validation_previously_inspected": True,
        "calibration_only": cal_block,
        "history_added": hist_block,
        "brier_improvement": (cal_block["brier"] - hist_block["brier"]),
        "log_loss_improvement": (cal_block["log_loss"] - hist_block["log_loss"]),
        "paired_cluster_bootstrap": _paired_delta_ci(data, cal, hist),
        "interpretation": ("Positive improvement means the history-augmented "
                           "model scored better. This is a new descriptive "
                           "research fit, not the frozen MANAGER_C_FORWARD_"
                           "ABLATION_V1 manifest and not a policy change."),
    })
    return out


def analyse(data: dict[str, Any], *, history_rows: list[tuple],
            mapping: dict[int, int], as_of: datetime,
            conn: psycopg.Connection) -> dict[str, Any]:
    as_of = _utc(as_of)
    cohorts = select_cohorts(data)
    meta = data["meta"]
    raw_index = build_index(history_rows)
    bridged_index = build_index(history_rows, mapping)
    identity_cache: dict[int, bool] = {}

    def has_identity(player_id: int) -> bool:
        if player_id not in identity_cache:
            identity_cache[player_id] = _has_historical_identity(
                conn, player_id, LIVE_SOURCES)
        return identity_cache[player_id]

    records: dict[int, list[dict]] = cohorts["groups"]

    def enrich(record: dict) -> dict:
        mid = record["match_id"]
        m = meta[mid]
        if m.get("player_1_id") is None or m.get("as_of") is None:
            raise ValueError("missing original player orientation or cutoff")
        pick = record["candidates"][POLICY]
        if pick is None:
            raise ValueError("selected cohort row lacks a policy candidate")
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
        first_start = raw_hist.first_start(cutoff) if raw_hist else None
        last_start = raw_hist.last_start(cutoff) if raw_hist else None
        bridged_first_start = (bridged_hist.first_start(cutoff)
                               if bridged_hist else None)
        bridged_last_start = (bridged_hist.last_start(cutoff)
                              if bridged_hist else None)
        surface = m.get("surface")
        historical_identity = has_identity(fav_id)
        # Career/inactivity classification uses the identity-corrected history:
        # a live-only player's raw date range starts when the live provider first
        # saw them, which would otherwise look like a debut.
        starts_complete = (bridged_hist.start_dates_complete
                           if bridged_hist else True)
        labels = _classify(
            fav_id, raw=raw_count, bridged=bridged_count, cutoff=cutoff,
            first_start=bridged_first_start, last_start=bridged_last_start,
            historical_identity=historical_identity,
            resolved=fav_map != fav_id, starts_complete=starts_complete)
        return {
            **record,
            "side": side, "p": probability,
            "probability_bin": _bin(max(probability, 1 - probability)),
            "surface": surface,
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
            "fav_bridged_first_start": (bridged_first_start.isoformat()
                                        if bridged_first_start else None),
            "fav_bridged_last_start": (bridged_last_start.isoformat()
                                       if bridged_last_start else None),
            "fav_raw_first_start": (first_start.isoformat() if first_start else None),
            "fav_raw_last_start": (last_start.isoformat() if last_start else None),
            "dog_surface_count": (dog_raw.surface_count(cutoff, surface)
                                  if dog_raw else 0),
            "fav_level_counts": raw_hist.level_counts(cutoff) if raw_hist else {},
            "identity_mapped": fav_map != fav_id,
            "identity_map_sha256": _sha([fav_id, fav_map]),
            **labels,
        }

    enriched: dict[str, list[dict]] = {}
    for name, group in records.items():
        if name == "rejected":
            # Rejected rows include records with no policy candidate; they are
            # not part of any selected cohort and are only counted.
            continue
        enriched[name] = [enrich(record) for record in group]
    early_enriched = [
        enrich(record) for record in cohorts["early"]
        if record["candidates"][POLICY] is not None
    ]

    train_cutoff = TRAIN_END
    train = [r for r in early_enriched
             if r["winner"] in (1, 2)
             and data["known"].get(r["match_id"]) is not None
             and _utc(data["known"][r["match_id"]]) <= train_cutoff]
    train = list({r["match_id"]: r for r in train}.values())
    validation = [r for r in enriched["broad"]
                  if r["start_at"] >= train_cutoff and r["winner"] in (1, 2)]

    report: dict[str, Any] = {
        "version": VERSION,
        "status": "DESCRIPTIVE_HISTORICAL_RESEARCH_ONLY",
        "independent_forward_validation": False,
        "historical_evaluation_previously_inspected": True,
        "as_of": as_of.isoformat(),
        "policy": POLICY,
        "min_history_threshold": MIN_HISTORY,
        "inactivity_days": INACTIVITY_DAYS,
        "short_career_days": SHORT_CAREER_DAYS,
        "source_hashes": {
            "dataset_sha256": data.get("source_sha256"),
            "full_source_sha256": data.get("full_source_sha256"),
            "registered_baseline_match": (
                data.get("source_sha256")
                == "e7d70fb821d7c69339adfce51bbd7fe35161adda49c595124d1791fef1b56863"),
        },
        "cohort_thresholds": cohorts["thresholds"],
        "cohort_sizes": {
            name: {
                "selected": len(group),
                "challenger": sum(r["tour"] == "CHALLENGER" for r in group),
                "settled": sum(r["winner"] in (1, 2) for r in group),
                "challenger_settled": sum(
                    r["tour"] == "CHALLENGER" and r["winner"] in (1, 2)
                    for r in group),
            } for name, group in records.items()
        },
        "class_census": {
            name: class_census(enriched[name]) for name in ("narrow", "broad", "marginal")
        },
        "analysis": {
            name: analyse_rows(enriched[name])
            for name in ("narrow", "broad", "marginal")
        },
        "logistic_information_beyond_calibration": _logistic_comparison(train, validation),
        "logistic_training": {
            "train_window": f"start < {TRAIN_END.isoformat()}",
            "train_n": len(train),
            "validation_window": f"{TRAIN_END.isoformat()} <= start < {VALIDATION_END.isoformat()}",
            "validation_n": len(validation),
        },
        "exclusions": data.get("exclusions"),
        "limitations": [
            "Frozen Manager C envelope, manifest, weights and settlement boundary are unchanged.",
            "November window was previously inspected: descriptive, not independent validation.",
            "No ITF/FUT result is ingested, so short-career players cannot be split into genuine debut vs ITF transition.",
            "Observable history is the Agent 1 PIT pool (ATP main + Challenger only).",
            "Expected wins use the original Manager C selected-side probability, not a recalibration.",
            "Selections sharing a favourite player are dependent; cluster bootstrap is reported.",
            "Counts are research-derived from PIT-visible history, not persisted original-run snapshots.",
        ],
    }
    return report


# The analysis is called with the live read-only connection available for the
# bounded identity-completeness lookup. Keeping it explicit avoids a hidden
# module-level connection.
def run(conn: psycopg.Connection, *, start=TRAIN_START, end=VALIDATION_END,
        as_of=HISTORICAL_AS_OF) -> dict[str, Any]:
    data = read_source(conn, start=start, end=end, as_of=as_of)
    history_rows = load_history_index(conn, as_of=_utc(as_of))
    cutoffs = sorted(set(data["cutoffs"].values()))
    if not cutoffs:
        raise ValueError("no original forecast cutoffs available")
    targets = {
        int(row[index]) for index in (28, 29) for row in data["raw"]
        if row[index] is not None
    }
    players = targets | {
        int(v) for row in history_rows for v in (row[1], row[2])
    }
    samples = sorted({cutoffs[0], cutoffs[len(cutoffs) // 2], cutoffs[-1]})
    mappings = [resolve_mapping(conn, players, as_of=c) for c in samples]
    invariant = all(m == mappings[0] for m in mappings[1:])
    mapping = mappings[-1]
    target_diff = {
        pid for pid in targets
        if any(m.get(pid, pid) != mappings[0].get(pid, pid) for m in mappings)
    }
    report = analyse(data, history_rows=history_rows, mapping=mapping,
                     as_of=_utc(as_of), conn=conn)
    report["historical_start_date_coverage"] = {
        "indexed_matches": len(history_rows),
        "versions_without_match_start_at": sum(
            row[7] is None for row in history_rows),
        "note": ("SACKMANN historical match versions in production carry no "
                 "match_start_at (migration 0003 intentionally dropped NOT "
                 "NULL). Counts remain valid; career and inactivity are not "
                 "inferred for affected players."),
    }
    report["identity_mapping"] = {
        "players_considered": len(players),
        "target_players": len(targets),
        "mapped_targets": sum(mapping.get(p, p) != p for p in targets),
        "invariant_across_sampled_cutoffs": invariant,
        "cutoffs_sampled": [c.isoformat() for c in samples],
        "target_ids_changed_across_cutoffs": len(target_diff),
        "activated_bridge_events_note": (
            "Production has one activated BRIDGE event; correction is "
            "genuinely applied where resolvable and otherwise fails closed."),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="optional JSON output path")
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
