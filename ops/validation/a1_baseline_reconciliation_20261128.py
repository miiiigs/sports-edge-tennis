"""Reproduce the November 28 fixture-level baseline reconciliation offline.

No fitting, network, database or mutation of historical source evidence.
The word original means unbridged replay, not stored live output.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

EVIDENCE = Path("docs/research/evidence")
FULL = "a1_selective_population_20261126.csv"
RELIABILITY = "a1_reliability_scores_20261127.csv"
FRACTIONS = (40, 45, 50)


def read_unique(path):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ids = {row["match_id"] for row in rows}
    if len(ids) != len(rows) or len({row["prediction_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate fixture or prediction identity")
    return rows


def rank(rows, field):
    return sorted(rows, key=lambda row: (-float(row[field]),
                                         int(row["prediction_id"]), int(row["match_id"])))


def wins(rows, field):
    n = len(rows)
    w = sum(row[field] in ("1", "True") for row in rows)
    return {"n": n, "w": w, "l": n - w}


def selection(rows, field, coverage):
    return rank(rows, field)[:len(rows) * coverage // 100]


def reconcile(full, reliability):
    source = {row["match_id"]: row for row in full}
    scored = {row["match_id"]: row for row in reliability}
    if len(source) != len(full) or len(scored) != len(reliability):
        raise ValueError("duplicate fixture identity")
    strict = [row for row in full if datetime.fromisoformat(row["as_of"]) <
              datetime.fromisoformat(row["match_start_at"])]
    if {row["match_id"] for row in strict} != scored.keys():
        raise ValueError("strict pre-match membership differs")
    for row in reliability:
        other = source[row["match_id"]]
        if (row["prediction_id"] != other["prediction_id"]
                or float(row["a1_probability_p1"]) != float(other["p1_original"])
                or row["a1_selected_side"] != other["side_original"]
                or (row["correct"] == "True") != (other["correct_original"] == "1")
                or float(row["confidence"]) != float(other["confidence_original"])
                or row["date"] != other["date"] or row["tour"] != other["tour"]):
            raise ValueError("unbridged reliability baseline differs: " + row["match_id"])
    result = {"counts": {"full": len(full), "reliability": len(reliability),
                         "overlap": len(scored), "full_only": len(full)-len(scored),
                         "reliability_only": 0, "strict": len(strict),
                         "strict_membership_exact": True},
              "points": {}, "replacement": {},
              "classification": "DEVELOPMENT_REUSED_NOT_PROSPECTIVE",
              "can_promote": False}
    for coverage in FRACTIONS:
        key = str(coverage / 100)
        arms = {"original_full": (full, "confidence_original", "correct_original"),
                "corrected_full": (full, "confidence_corrected", "correct_corrected"),
                "original_strict": (strict, "confidence_original", "correct_original"),
                "corrected_strict": (strict, "confidence_corrected", "correct_corrected"),
                "r0": (reliability, "confidence", "correct"),
                "r1": (reliability, "selection_score", "correct")}
        result["points"][key] = {
            arm: wins(selection(rows, field, coverage), outcome)
            for arm, (rows, field, outcome) in arms.items()}
        a = selection(reliability, "confidence", coverage)
        b = selection(reliability, "selection_score", coverage)
        a_ids, b_ids = ({row["match_id"] for row in rows} for rows in (a, b))
        retained = [row for row in a if row["match_id"] in b_ids]
        removed = [row for row in a if row["match_id"] not in b_ids]
        added = [row for row in b if row["match_id"] not in a_ids]
        if len(removed) != len(added):
            raise ValueError("replacement volume mismatch")
        result["replacement"][key] = {
            "retained": wins(retained, "correct"),
            "removed": wins(removed, "correct"), "added": wins(added, "correct"),
            "net_wins": wins(added, "correct")["w"]-wins(removed, "correct")["w"]}
    nov = [row for row in full if row["date"] >= "2026-11-01"]
    marginal = [row for row in nov if 0.648708 - 1e-12 <=
                float(row["confidence_corrected"]) < 0.683286 - 1e-12]
    experienced = [row for row in marginal if
                   int(row["overall_matches_corrected_p"+row["side_corrected"]]) >= 20]
    ids = {row["match_id"] for row in experienced}
    limited = [row for row in marginal if row["match_id"] not in ids]
    result["marginal"] = {"november_n": len(nov), "n": len(marginal),
                          "groups": {"all": {
                              "experienced": wins(experienced, "correct_corrected"),
                              "limited": wins(limited, "correct_corrected")}}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    result = reconcile(read_unique(args.evidence_dir/FULL),
                       read_unique(args.evidence_dir/RELIABILITY))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
