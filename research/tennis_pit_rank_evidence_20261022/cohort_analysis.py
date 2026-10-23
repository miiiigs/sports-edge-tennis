"""Research-only cohort aggregation across selected/opponent rank, gap, surface and tier."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.tennis_pit_rank_evidence_20261022.contract import score_completed
from research.tennis_pit_rank_evidence_20261022.enrichment import file_sha256, read_jsonl


def gap_band(value: int | None) -> str:
    """Positive = selected player is higher-ranked (smaller number) than opponent."""
    if value is None:
        return "MISSING_OR_UNRANKED"
    if value <= -201:
        return "OPPONENT_BETTER_201+"
    if value < 0:
        return "OPPONENT_BETTER_1-200"
    if value == 0:
        return "EQUAL"
    if value <= 200:
        return "SELECTED_BETTER_1-200"
    return "SELECTED_BETTER_201+"


def ece(rows: Iterable[Mapping[str, Any]], agent: str, bins: int = 10) -> float | None:
    """Fixed equal-width reliability bins using the same completed outcomes as scoring."""
    samples: list[tuple[float, int]] = []
    for r in rows:
        p = r.get(f"{agent}_p")
        side = r.get(f"{agent}_side")
        if r.get("outcome_status") != "COMPLETED" or r.get("winner_side") not in (1, 2):
            continue
        if side not in (1, 2) or type(p) not in (int, float) or not (0 <= p <= 1):
            continue
        samples.append((float(p), int(side == r["winner_side"])))
    if not samples:
        return None
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, won in samples:
        buckets[min(int(p * bins), bins - 1)].append((p, won))
    return sum(
        len(b) / len(samples) * abs(sum(p for p, _ in b) / len(b)
                                  - sum(y for _, y in b) / len(b))
        for b in buckets if b
    )


def analyze(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group scores without pooling population or agent, with explicit missing categories."""
    collected: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        for axis, band in (
            ("selected_rank", row["selected_rank_band"]),
            ("opponent_rank", row["opponent_rank_band"]),
            ("rank_gap", gap_band(row["rank_gap_opponent_minus_selected"])),
            ("surface", row.get("surface") or "UNKNOWN"),
            ("tournament_tier", row.get("tournament_tier") or "UNKNOWN"),
        ):
            for agent in ("manager", "a1", "a2"):
                collected[(row["population"], agent, axis, band)].append(row)
    results = []
    for (population, agent, axis, band), subrows in sorted(collected.items()):
        metrics = score_completed(
            subrows, selected_side_key=f"{agent}_side", probability_key=f"{agent}_p"
        )
        results.append({
            "population": population, "agent": agent,
            "axis": axis, "cohort": band, **metrics,
            "ece_10": ece(subrows, agent),
        })
    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enriched-jsonl", required=True, type=Path)
    parser.add_argument("--expected-enriched-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if file_sha256(args.enriched_jsonl) != args.expected_enriched_sha256:
        parser.error("enriched cohort fingerprint mismatch")
    report = {
        "sha256": args.expected_enriched_sha256,
        "cohorts": analyze(read_jsonl(args.enriched_jsonl)),
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
