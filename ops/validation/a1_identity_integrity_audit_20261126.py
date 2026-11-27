"""Read-only identity, history and population-integrity audit for Agent 1.

Complements the selective-performance experiment with evidence that cannot be
derived from the settled CSV alone: exact settlement-snapshot differences,
re-issued target churn, history attribution changes, duplicate-match risk and
timestamp precision.  No writes, no provider calls, no model training.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sports_edge_tennis.data.agent1_history import load_agent1_history

E3_CUTOFF = datetime(2026, 11, 25, 7, 20, tzinfo=UTC)
DEFAULT_DSN_FILE = Path(r"C:\Users\Desktop 2\AppData\Local\Temp\opencode\tennis_dsn.txt")
EVIDENCE = ROOT / "docs" / "research" / "evidence"
POPULATION = EVIDENCE / "a1_selective_population_20261126.csv"
SINCE = datetime(2026, 9, 10, tzinfo=UTC)

TARGET_SQL = """
WITH a1 AS (
  SELECT DISTINCT ON (p.match_id) p.prediction_id, p.match_id, p.prediction_run_id, p.as_of
  FROM predictions p JOIN prediction_runs pr USING (prediction_run_id)
  WHERE p.agent_code='AGENT_1_SURFACE_ELO' AND p.prediction_status='PREDICTED'
    AND p.experiment_id IS NULL AND pr.experiment_id IS NULL
    AND pr.run_status='COMPLETED' AND p.as_of >= %(since)s AND p.as_of <= %(cut)s
  ORDER BY p.match_id, p.created_at DESC
) SELECT prediction_id, match_id, prediction_run_id, as_of FROM a1 ORDER BY prediction_id
"""


def dsn_from_file(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"DSN file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def target_set(conn, cutoff):
    return [tuple(row) for row in conn.execute(TARGET_SQL, {"since": SINCE, "cut": cutoff}).fetchall()]


def settlement_snapshot(conn, cutoff):
    rows = conn.execute(
        """
        WITH a1 AS (
          SELECT DISTINCT ON (p.match_id) p.match_id
          FROM predictions p JOIN prediction_runs pr USING (prediction_run_id)
          WHERE p.agent_code='AGENT_1_SURFACE_ELO' AND p.prediction_status='PREDICTED'
            AND p.experiment_id IS NULL AND pr.experiment_id IS NULL
            AND pr.run_status='COMPLETED' AND p.as_of >= %(since)s AND p.as_of <= %(freeze)s
          ORDER BY p.match_id, p.created_at DESC
        ), latest AS (
          SELECT DISTINCT ON (mro.match_id) mro.match_id, mro.outcome_status::text status,
                 mro.winner_side, mro.as_of
          FROM match_result_observations mro JOIN a1 USING (match_id)
          ORDER BY mro.match_id, mro.as_of DESC, mro.source_observed_at DESC,
                   mro.ingested_at DESC, mro.match_result_observation_id DESC
        )
        SELECT match_id, as_of FROM latest
        WHERE status='COMPLETED' AND winner_side IN (1,2) AND as_of <= %(cutoff)s
        ORDER BY as_of, match_id
        """,
        {"since": SINCE, "freeze": E3_CUTOFF, "cutoff": cutoff},
    ).fetchall()
    return [int(row[0]) for row in rows], [row[1] for row in rows]


def set_diff_report(label, left, right):
    left_ids = {int(row[0]) for row in left}
    right_ids = {int(row[0]) for row in right}
    added = sorted(right_ids - left_ids)
    removed = sorted(left_ids - right_ids)
    left_matches = {int(row[1]) for row in left}
    right_matches = {int(row[1]) for row in right}
    return {
        "label": label,
        "left_targets": len(left_ids),
        "right_targets": len(right_ids),
        "net_targets": len(right_ids) - len(left_ids),
        "targets_added": len(added),
        "targets_removed": len(removed),
        "targets_added_ids": added,
        "targets_removed_ids": removed,
        "left_matches": len(left_matches),
        "right_matches": len(right_matches),
        "matches_added": len(right_matches - left_matches),
        "matches_removed": len(left_matches - right_matches),
        "matches_added_ids": sorted(right_matches - left_matches),
        "matches_removed_ids": sorted(left_matches - right_matches),
        "note": (
            "A re-issued match can replace its selected prediction target without changing "
            "the fixture. Net counts therefore hide churn; both levels are reported."
        ),
    }


def history_attribution(conn):
    unbridged = load_agent1_history(conn, cutoff=E3_CUTOFF, ingested_by=E3_CUTOFF)
    bridged = load_agent1_history(
        conn, cutoff=E3_CUTOFF, ingested_by=E3_CUTOFF, bridge_model_identities=True
    )
    unbridged_pairs = {o.match_id: (o.player_1_id, o.player_2_id) for o in unbridged}
    bridged_pairs = {o.match_id: (o.player_1_id, o.player_2_id) for o in bridged}
    common = set(unbridged_pairs) & set(bridged_pairs)
    changed = [match for match in common if unbridged_pairs[match] != bridged_pairs[match]]
    recovered_players: dict[int, set[int]] = defaultdict(set)
    live_players: dict[int, set[int]] = defaultdict(set)
    for match in common:
        for live, model in zip(unbridged_pairs[match], bridged_pairs[match], strict=True):
            if live != model:
                recovered_players[model].add(match)
                live_players[live].add(match)
    return {
        "unbridged_history_matches": len(unbridged_pairs),
        "bridged_history_matches": len(bridged_pairs),
        "common_matches": len(common),
        "attribution_changed_matches": len(changed),
        "attribution_changed_share": len(changed) / len(common) if common else None,
        "players_receiving_recovered_matches": len(recovered_players),
        "unique_recovered_player_match_links": sum(len(v) for v in recovered_players.values()),
        "live_players_with_attribution_change": len(live_players),
        "note": (
            "A 'recovered match' is a history match whose bridged player id differs from the "
            "live id. The same match can be recovered for both players and for several targets; "
            "cumulative per-target recovered matches are therefore larger than unique matches."
        ),
    }


def duplicate_risk(conn):
    duplicates = conn.execute(
        """
        WITH normalised AS (
          SELECT match_id,
                 LEAST(player_1_id, player_2_id) AS p_low,
                 GREATEST(player_1_id, player_2_id) AS p_high,
                 date_trunc('day', match_start_at) AS day,
                 surface::text AS surface
          FROM match_versions
          WHERE match_start_at IS NOT NULL
        )
        SELECT p_low, p_high, day, surface, count(DISTINCT match_id) AS matches
        FROM normalised
        GROUP BY 1,2,3,4
        HAVING count(DISTINCT match_id) > 1
        """
    ).fetchall()
    return {
        "same_players_same_day_groups": len(duplicates),
        "same_players_same_day_match_ids": sum(int(row[4]) for row in duplicates),
        "examples": [
            {"player_low": int(row[0]), "player_high": int(row[1]), "day": str(row[2]),
             "surface": row[3], "distinct_match_ids": int(row[4])}
            for row in duplicates[:20]
        ],
        "note": (
            "Same unordered player pair on the same calendar day can be a legitimate "
            "best-of-three rematch or a duplicated source row. It is a risk flag, not proof "
            "of duplication; the replay deduplicates by match_id."
        ),
    }


def timestamp_precision(conn):
    versions = conn.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE ingested_at < source_observed_at),
               count(*) FILTER (WHERE as_of < source_observed_at),
               count(*) FILTER (WHERE created_at < ingested_at)
        FROM match_versions
        """
    ).fetchone()
    results = conn.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE mro.ingested_at < mro.source_observed_at),
               count(*) FILTER (WHERE mro.as_of < mro.source_observed_at),
               count(*) FILTER (WHERE mro.source_observed_at < mv.match_start_at)
        FROM match_result_observations mro
        JOIN LATERAL (
            SELECT match_start_at FROM match_versions mv0
            WHERE mv0.match_id = mro.match_id
            ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        """
    ).fetchone()
    return {
        "match_versions": {
            "rows": int(versions[0]),
            "ingested_before_observed": int(versions[1]),
            "as_of_before_observed": int(versions[2]),
            "created_before_ingested": int(versions[3]),
        },
        "result_observations": {
            "rows": int(results[0]),
            "ingested_before_observed": int(results[1]),
            "as_of_before_observed": int(results[2]),
            "observed_before_match_start": int(results[3]),
        },
        "note": (
            "Each result is compared with the latest match version start time. "
            "Observed-before-start rows are data-integrity flags, not forecasts."
        ),
    }


def rating_stability_from_csv(path: Path):
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    deltas = []
    surface_deltas = []
    for row in rows:
        for slot in ("p1", "p2"):
            try:
                overall_a = float(row[f"overall_rating_original_{slot}"])
                overall_b = float(row[f"overall_rating_corrected_{slot}"])
                surface_a = float(row[f"surface_rating_original_{slot}"])
                surface_b = float(row[f"surface_rating_corrected_{slot}"])
            except (KeyError, TypeError, ValueError):
                continue
            deltas.append(abs(overall_b - overall_a))
            surface_deltas.append(abs(surface_b - surface_a))
    if not deltas:
        return {"status": "NO_RATING_ROWS"}
    ordered = sorted(deltas)
    return {
        "player_slots_with_both_ratings": len(deltas),
        "overall_abs_delta": {
            "mean": statistics.fmean(deltas),
            "median": statistics.median(deltas),
            "p95": ordered[int(0.95 * (len(ordered) - 1))],
            "max": ordered[-1],
            "share_gt_100": sum(1 for value in deltas if value > 100) / len(deltas),
            "share_zero": sum(1 for value in deltas if value == 0) / len(deltas),
        },
        "surface_abs_delta": {
            "mean": statistics.fmean(surface_deltas),
            "median": statistics.median(surface_deltas),
            "max": max(surface_deltas),
        },
        "note": (
            "Large rating changes are expected when a fragmented identity recovers history; "
            "they are also the main stability risk. Surface and overall ratings can move by "
            "different amounts because surface history is weighted separately."
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-file", default=str(DEFAULT_DSN_FILE))
    parser.add_argument(
        "--out", default=str(EVIDENCE / "a1_identity_integrity_20261126.json")
    )
    parser.add_argument("--population", default=str(POPULATION))
    args = parser.parse_args(argv)

    dsn = dsn_from_file(Path(args.dsn_file))
    cut_0720 = datetime(2026, 11, 25, 7, 20, tzinfo=UTC)
    cut_1100 = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
    cut_1213 = datetime(2026, 11, 25, 12, 13, tzinfo=UTC)
    with psycopg.connect(
        dsn, autocommit=True, options="-c default_transaction_read_only=on -c statement_timeout=1800000"
    ) as conn:
        t0720 = target_set(conn, cut_0720)
        t1100 = target_set(conn, cut_1100)
        t1213 = target_set(conn, cut_1213)
        snap3223, times3223 = settlement_snapshot(conn, datetime(2026, 11, 25, 7, 20, tzinfo=UTC))
        snap3271, times3271 = settlement_snapshot(conn, datetime(2026, 11, 25, 9, 41, tzinfo=UTC))
        # 3,284 is the settlement count at the instant of its 3,284th completed
        # result, not an arbitrary wall-clock cutoff. Reproduce it exactly.
        all_snap, all_times = settlement_snapshot(conn, datetime(2026, 11, 27, tzinfo=UTC))
        snap3284, times3284 = all_snap[:3284], all_times[:3284]
        snap_0404, times_0404 = settlement_snapshot(conn, datetime(2026, 11, 26, 4, 4, tzinfo=UTC))
        history = history_attribution(conn)
        duplicates = duplicate_risk(conn)
        timestamps = timestamp_precision(conn)

    with Path(args.population).open(newline="", encoding="utf-8") as handle:
        population = list(csv.DictReader(handle))
    methods = Counter()
    for row in population:
        for slot in ("p1", "p2"):
            methods[row[f"resolution_{slot}_method"]] += 1

    report = {
        "version": "A1_IDENTITY_INTEGRITY_V1",
        "generated_at": datetime.now(UTC).isoformat(),
        "classification": "CORRECTED_RETROSPECTIVE_RESEARCH",
        "can_promote": False,
        "production_changed": False,
        "database_writes": False,
        "freeze": {
            "since": SINCE.isoformat(),
            "e3_cutoff": E3_CUTOFF.isoformat(),
        },
        "target_universe_reconciliation": {
            "cutoff_0720": len(t0720),
            "cutoff_1100": len(t1100),
            "cutoff_1213": len(t1213),
            "churn_0720_to_1100": set_diff_report("0720->1100", t0720, t1100),
            "churn_1100_to_1213": set_diff_report("1100->1213", t1100, t1213),
            "churn_0720_to_1213": set_diff_report("0720->1213", t0720, t1213),
        },
        "settlement_snapshots": {
            "at_e3_cutoff_0720": {"settled": len(snap3223), "last_result_as_of": times3223[-1].isoformat() if times3223 else None},
            "at_manager_run_0941": {"settled": len(snap3271), "last_result_as_of": times3271[-1].isoformat() if times3271 else None},
            "at_2026_11_26_0403_46": {"settled": len(snap3284), "last_result_as_of": times3284[-1].isoformat() if times3284 else None},
            "at_2026_11_26_0404_wall_clock": {"settled": len(snap_0404), "last_result_as_of": times_0404[-1].isoformat() if times_0404 else None},
            "added_3223_to_3271": sorted(set(snap3271) - set(snap3223)),
            "added_3271_to_3284": sorted(set(snap3284) - set(snap3271)),
            "note": (
                "3,223, 3,271 and 3,284 are successive settlement snapshots of the same "
                "3,776-target universe. They are not separate eligibility definitions. "
                "The additional fixtures are listed exactly. 3,284 uses the instant of its "
                "3,284th completed result; a 04:04 wall-clock cutoff already contains 3,285."
            ),
        },
        "resolution_census": {
            "settled_3223_slots": 2 * len(population),
            "settled_3223_methods": dict(methods),
            "settled_3223_gap": 2 * len(population) - sum(methods.values()),
            "ledger_3776_targets": 3776,
            "ledger_3776_slots": 7552,
            "ledger_3776_methods": {
                "ALIAS": 6547,
                "AMBIGUOUS": 226,
                "HISTORICAL_ID": 5,
                "NO_HISTORICAL_CANDIDATE": 768,
                "PERSISTED_BRIDGE": 6,
            },
            "reference_policy_artifact_3778": {
                "targets": 3778,
                "slots": 7556,
                "declared_methods": {
                    "ALIAS": 6550,
                    "AMBIGUOUS": 225,
                    "HISTORICAL_ID": 5,
                    "NO_HISTORICAL_CANDIDATE": 765,
                    "PERSISTED_BRIDGE": 6,
                },
                "declared_total": 7551,
                "unclassified_slots": 5,
            },
            "five_record_discrepancy": {
                "finding": (
                    "The 3,778-target reference-policy artifact declares 7,551 classified "
                    "resolutions for 7,556 player slots. The five-slot gap is a reporting "
                    "gap in that artifact, not five missing fixtures and not five missing "
                    "settled forecasts."
                ),
                "evidence": (
                    "The 3,776-target ledger is fully classified (7,552/7,552). The settled "
                    "3,223 population is fully classified (6,446/6,446). The 3,778 artifact "
                    "adds two fixtures (four slots): 7,552 + 4 - 5 = 7,551, so exactly five "
                    "slots are unclassified in that artifact."
                ),
                "disposition": (
                    "Bounded and non-material to the 3,223 evaluation population. It must be "
                    "closed in the next v3 census by emitting a per-slot record, including "
                    "NO_PLAYER/NO_CANDIDATE, instead of only a method aggregate."
                ),
            },
        },
        "history": history,
        "duplicate_match_risk": duplicates,
        "timestamp_precision": timestamps,
        "rating_stability": rating_stability_from_csv(Path(args.population)),
        "limitations": [
            "Retrospective reconstruction; not an independent forward audit.",
            "The reference-policy artifact was measured under v2; v3 has not been re-measured on production because 0025 is not applied.",
            "The duplicate screen flags same-pair same-day fixtures; it cannot prove duplication without source payloads.",
        ],
    }
    out = Path(args.out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True)[:6000])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
