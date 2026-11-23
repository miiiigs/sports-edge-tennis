"""Brian P0: determine whether Manager C's 20%-to-40% added matches are nested.

This uses exactly the original first completed nonexperimental production run per
match from the established PR357 PIT SQL. The 20%/40% selection cutoffs are
frozen on Oct 15-Nov 3 prediction confidence only, then applied unchanged to
Nov 4-Nov 20. No recalibration, optimization, DB writes or odds requests.

The November holdout has been repeatedly inspected: output is AMBER
historical diagnostic, NOT new untouched prospective performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import psycopg

from sports_edge_tennis.performance.calibration import brier, log_loss
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES, _available_agents, _utc, _wilson, source_sql,
)
from sports_edge_tennis.validation.manager_abc_validation_frozen_risk_coverage import (
    evaluate_selected, freeze_threshold, normalize,
)

VERSION = "MANAGER_C_MARGINAL_NESTED_FIRST_ISSUE_V1"
POLICY = "C_uncalibrated_mean_floor60"


def _summary(selected: list[dict[str, Any]], *, agent_evidence: dict[int, dict]) -> dict:
    settled = [r for r in selected if r["winner"] in (1, 2)]
    wins = sum(r["candidates"][POLICY][0] == r["winner"] for r in settled)
    pairs = [(float(r["candidates"][POLICY][1]), int(r["winner"] == 1))
             for r in settled]
    confidence = [max(p, 1 - p) for r in selected
                  for p in [float(r["candidates"][POLICY][1])]]
    by_tour = {}
    for tour in ("ATP_MAIN", "CHALLENGER"):
        rows = [r for r in selected if r["tour"] == tour]
        graded = [r for r in rows if r["winner"] in (1, 2)]
        correct = sum(r["winner"] == r["candidates"][POLICY][0] for r in graded)
        by_tour[tour] = {
            "selected": len(rows), "settled": len(graded),
            "wins": correct, "losses": len(graded) - correct,
            "accuracy": correct / len(graded) if graded else None,
        }
    disagree = Counter()
    diffs = []
    by_agreement = defaultdict(lambda: [0, 0, 0])
    for r in selected:
        agent = agent_evidence.get(r["match_id"], {})
        a1, a2 = agent.get(CODES[0]), agent.get(CODES[1])
        status = "NOT_BOTH_PREDICTED"
        if a1 is not None and a2 is not None:
            status = "AGREE" if a1[0] == a2[0] else "DISAGREE"
            diffs.append(abs(a1[1] - a2[1]))
        disagree[status] += 1
        tally = by_agreement[status]
        tally[0] += 1
        if r["winner"] in (1, 2):
            tally[1] += 1
            tally[2] += int(r["candidates"][POLICY][0] == r["winner"])
    return {
        "selected": len(selected), "settled": len(settled),
        "wins": wins, "losses": len(settled) - wins,
        "ungraded": len(selected) - len(settled),
        "accuracy": wins / len(settled) if settled else None,
        "accuracy_wilson95": _wilson(wins, len(settled)),
        "brier": brier(pairs) if pairs else None,
        "log_loss": log_loss(pairs) if pairs else None,
        "mean_winner_confidence": sum(confidence) / len(confidence) if confidence else None,
        "mean_agent_probability_disagreement": sum(diffs) / len(diffs) if diffs else None,
        "a1_a2": {
            "counts": dict(sorted(disagree.items())),
            "performance": {
                name: {"selected": value[0], "settled": value[1],
                       "wins": value[2],
                       "losses": value[1] - value[2],
                       "accuracy": value[2]/value[1] if value[1] else None}
                for name, value in sorted(by_agreement.items())
            },
        },
        "by_tour": by_tour,
    }


def _original_agent_evidence(raw_rows: list[tuple]) -> dict[int, dict]:
    """Extract only first-run time-verified persisted A1/A2/A3 predictions.

    normalize() separately refuses late source times, mixed Manager results,
    duplicated original agent rows and unsafe original first-issue cutoffs.
    """
    raw = defaultdict(dict)
    for row in raw_rows:
        mid = int(row[0])
        code = row[6]
        if code is None:
            continue
        if code not in CODES or code in raw[mid]:
            raise ValueError("duplicate or unexpected original agent")
        raw[mid][code] = (
            str(row[7]), int(row[8]) if row[8] is not None else None,
            float(row[9]) if row[9] is not None else None,
        )
    return {mid: _available_agents(predictions)
            for mid, predictions in raw.items()}


def analyze(records: list[dict], *, validation_end: datetime,
            original_agents: dict[int, dict] | None = None,
            dataset_sha256: str | None = None) -> dict[str, Any]:
    validation_end = _utc(validation_end)
    if len({r["match_id"] for r in records}) != len(records):
        raise ValueError("duplicate canonical first-issue fixture")
    if any(r["issued_at"] > r["start_at"] for r in records):
        raise ValueError("forecast generated after sporting event start")
    validation = [r for r in records if r["start_at"] < validation_end]
    evaluation = [r for r in records if r["start_at"] >= validation_end]
    if not validation or not evaluation:
        raise ValueError("chronological validation and evaluation required")
    lock_narrow = freeze_threshold(validation, policy=POLICY, coverage_target=.20)
    lock_broad = freeze_threshold(validation, policy=POLICY, coverage_target=.40)
    if lock_narrow["threshold"] is None or lock_broad["threshold"] is None:
        return {
            "version": VERSION,
            "status": "UNREACHABLE_VALIDATION_CANDIDATE_CAPACITY",
            "locks": {"narrow": lock_narrow, "broad": lock_broad},
            "original_first_run_denominator": len(evaluation),
            "all_official_eligible_denominator": None,
        }
    _, narrow = evaluate_selected(
        evaluation, policy=POLICY, cutoff=float(lock_narrow["threshold"]))
    _, broad = evaluate_selected(
        evaluation, policy=POLICY, cutoff=float(lock_broad["threshold"]))
    narrow_ids = {r["match_id"] for r in narrow}
    broad_ids = {r["match_id"] for r in broad}
    missing = sorted(narrow_ids - broad_ids)
    extras = [r for r in broad if r["match_id"] not in narrow_ids]
    nested = not missing
    agents = original_agents or {}
    dataset_record_hash = hashlib.sha256(json.dumps(
        [[r["match_id"], r["winner"], r["tour"],
          r["candidates"][POLICY]]
         for r in sorted(evaluation, key=lambda x: x["match_id"])],
        separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    result = {
        "version": VERSION,
        "status": "NESTED_VERIFIED_BY_EXACT_MATCH_IDS" if nested else "NOT_NESTED_DO_NOT_SUBTRACT_SCORECARDS",
        "historical_evaluation_previously_inspected": True,
        "official_fixture_denominator": None,
        "independent_forward_validation": False,
        "dataset_sha256": dataset_sha256,
        "evaluation_match_ids_and_outcomes_sha256": dataset_record_hash,
        "validation_targets": len(validation),
        "evaluation_first_run_targets": len(evaluation),
        "cutoffs_fitted_earlier_validation_only": {
            "narrow_20pct": lock_narrow,
            "broad_40pct": lock_broad,
        },
        "selected_ids": {
            "narrow": len(narrow_ids), "broad": len(broad_ids),
            "narrow_missing_from_broad": len(missing),
            "broad_only": len(extras),
            "narrow_missing_ids": missing[:100],
            "broad_only_ids_sha256": hashlib.sha256(
                json.dumps(sorted(r["match_id"] for r in extras),
                           separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "narrow": _summary(narrow, agent_evidence=agents),
        "broad": _summary(broad, agent_evidence=agents),
        "additional_only": _summary(extras, agent_evidence=agents),
        "conditional_coverage": {
            "narrow": len(narrow) / len(evaluation),
            "broad": len(broad) / len(evaluation),
            "additional": len(extras) / len(evaluation),
            "official_eligible_coverage": None,
        },
        "historical_gap_to_72pct_broad": (
            max(0, __import__("math").ceil(.72 * len([r for r in broad
                      if r["winner"] in (1, 2)])) -
                sum(r["candidates"][POLICY][0] == r["winner"] for r in broad
                    if r["winner"] in (1, 2)))
        ),
        "historical_gap_to_75pct_broad": (
            max(0, __import__("math").ceil(.75 * len([r for r in broad
                      if r["winner"] in (1, 2)])) -
                sum(r["candidates"][POLICY][0] == r["winner"] for r in broad
                    if r["winner"] in (1, 2)))
        ),
        "not_available_from_validated_first_run_source": [
            "PIT_Elo_differences", "PIT_surface_Elo", "PIT_player_history_depth",
            "PIT_serve_return_completeness", "PIT_inactivity",
            "PIT_surface_label", "PIT_bookmaker_odds", "PIT_ranking_revisions",
        ],
        "source": "unchanged PR357 original first-run PIT SQL, read only",
        "no_retrospective_training_or_threshold_selection": True,
        "no_production_changes": True,
    }
    if not nested:
        result["additional_only_interpretation"] = (
            "Broad-minus-narrow is not the full difference because some narrow "
            "picks are absent from broad; the alleged 96W-53L arithmetic cannot "
            "be claimed as verified until exact nesting holds."
        )
    else:
        result["additional_only_interpretation"] = (
            "Exact original match ID nesting verified on this frozen data cohort; "
            "the marginal group is broad MINUS narrow, not a newly tuned model "
            "or independent prospective performance."
        )
    return result


def run(conn: psycopg.Connection, *, start: datetime, end: datetime,
        as_of: datetime, validation_end: datetime) -> dict:
    conn.execute("SET default_transaction_read_only=on")
    rows = conn.execute(source_sql(), {
        "start": _utc(start), "end": _utc(end), "as_of": _utc(as_of)
    }).fetchall()
    normalized, excluded, sha = normalize(
        rows, start=start, end=end, as_of=as_of)
    report = analyze(normalized, validation_end=validation_end,
                     original_agents=_original_agent_evidence(rows),
                     dataset_sha256=sha)
    report["unsafe_original_exclusions"] = excluded
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("start", "end", "as_of", "validation_end"):
        parser.add_argument("--" + field.replace("_", "-") + "-utc", required=True)
    args = parser.parse_args(argv)
    raw = [getattr(args, key + "_utc") for key in (
        "start", "end", "as_of", "validation_end")]
    if any(not t.endswith("Z") for t in raw):
        parser.error("explicit UTC Z timestamps required")
    start, end, as_of, validation_end = [
        datetime.fromisoformat(t.replace("Z", "+00:00")) for t in raw
    ]
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL is required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        report = run(conn, start=start, end=end, as_of=as_of,
                     validation_end=validation_end)
    print("TENNIS_MANAGER_C_MARGINAL " + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
