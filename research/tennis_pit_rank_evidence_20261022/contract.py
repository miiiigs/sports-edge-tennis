"""Offline research helpers; no database connection, pricing, or live rules.

Official rankings require independently verified point-in-time source publication.
Historical Elo ratings may not be substituted for official rankings.
"""
from __future__ import annotations

import math
from datetime import datetime
from collections.abc import Iterable, Mapping
from typing import Any


def rank_band(rank: int | None) -> str:
    if rank is None or rank < 1:
        return "MISSING_OR_UNRANKED"
    if rank <= 100:
        return "1-100"
    if rank <= 200:
        return "101-200"
    if rank <= 400:
        return "201-400"
    if rank <= 600:
        return "401-600"
    return "601+"


def as_of_rank(
    pick_at: datetime, archived: Iterable[Mapping[str, Any]]
) -> int | None:
    """Never select a source published after the pick or unverified archive."""
    if pick_at.tzinfo is None:
        raise ValueError("pick_at requires timezone")
    candidates = []
    for row in archived:
        effective = row.get("rank_as_of")
        published = row.get("source_published_at")
        rank = row.get("rank")
        if not row.get("verified_official_archive"):
            continue
        if not isinstance(effective, datetime) or not isinstance(published, datetime):
            continue
        if effective.tzinfo is None or published.tzinfo is None:
            continue
        if not isinstance(rank, int) or rank < 1:
            continue
        if effective <= published <= pick_at:
            candidates.append((effective, published, rank))
    if not candidates:
        return None
    return max(candidates)[2]


def wilson_interval(wins: int, count: int) -> tuple[float, float] | None:
    if count == 0:
        return None
    if not 0 <= wins <= count:
        raise ValueError("wins must be between zero and count")
    z = 1.959963984540054
    p = wins / count
    d = 1 + z * z / count
    center = (p + z * z / (2 * count)) / d
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / d
    return center - half, center + half


def first_qualified(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Deduplicate within populations; intentionally never merge their scores."""
    eligible = [r for r in rows if r["population"] in ("MANAGER", "A1_A2_TRACKING")]
    eligible.sort(key=lambda r: (r["picked_at"], r["pick_id"]))
    selected = {}
    for row in eligible:
        key = (row["population"], row["match_id"])
        selected.setdefault(key, row)
    return list(selected.values())


def score_completed(rows: Iterable[Mapping[str, Any]], *,
                    selected_side_key: str, probability_key: str) -> dict[str, Any]:
    """Only verified COMPLETED outcomes contribute to accuracy/calibration."""
    all_rows = list(rows)
    settled = [
        (int(r[selected_side_key] == r["winner_side"]), float(r[probability_key]))
        for r in all_rows
        if r.get("outcome_status") == "COMPLETED"
        and r.get("winner_side") in (1, 2)
        and r.get(selected_side_key) in (1, 2)
        and r.get(probability_key) is not None
        and 0 <= float(r[probability_key]) <= 1
    ]
    n = len(settled)
    wins = sum(y for y, _ in settled)
    avg_conf = sum(p for _, p in settled) / n if n else None
    return {
        "eligible": len(all_rows),
        "settled_with_prediction": n,
        "wins": wins,
        "losses": n - wins,
        "accuracy": wins / n if n else None,
        "mean_confidence": avg_conf,
        "calibration_gap": avg_conf - wins / n if n else None,
        "brier": sum((p - y) ** 2 for y, p in settled) / n if n else None,
        "wilson_95": wilson_interval(wins, n),
        "exploratory_only": n < 30,
    }
