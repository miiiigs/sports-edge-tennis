"""Pre-registered offline 401–600 exclusion diagnostic; cannot authorize a live filter.

Accuracy comparisons are paired on the same original first-production selections.
Unknown ranks remain in the baseline and retained cohort. No odds are fabricated.
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping

from research.tennis_pit_rank_evidence_20261022.enrichment import timestamp


def scored(rows: Iterable[Mapping[str, Any]], agent: str, population: str) -> list[dict[str, Any]]:
    if agent not in {"manager", "a1", "a2"} or population not in {"MANAGER", "A1_A2_TRACKING"}:
        raise ValueError("unknown agent or population")
    output = []
    seen: set[str] = set()
    for r in rows:
        if r.get("population") != population:
            continue
        match_id = r.get("canonical_match_id")
        if match_id in seen:
            raise ValueError("not one selection per canonical match in this population")
        seen.add(match_id)
        side = r.get(f"{agent}_side")
        confidence = r.get(f"{agent}_p")
        if r.get("outcome_status") != "COMPLETED" or r.get("winner_side") not in (1, 2):
            continue
        if side not in (1, 2) or not isinstance(confidence, (float, int)):
            continue
        if not 0 <= confidence <= 1:
            continue
        output.append({
            "match_id": match_id, "picked_at": timestamp(r["picked_at"]),
            "won": int(side == r["winner_side"]),
            "excluded": r.get("selected_rank_status") == "VERIFIED"
                        and r.get("selected_rank_band") == "401-600",
            "surface": r.get("surface"), "tier": r.get("tournament_tier"),
            "confidence": float(confidence),
        })
    return output


def snapshot(items: list[dict[str, Any]]) -> dict[str, Any]:
    retained = [x for x in items if not x["excluded"]]
    total = len(items)
    return {
        "evaluated": total,
        "baseline_wins": sum(x["won"] for x in items),
        "baseline_accuracy": sum(x["won"] for x in items) / total if total else None,
        "retained": len(retained),
        "retained_wins": sum(x["won"] for x in retained),
        "retained_accuracy": sum(x["won"] for x in retained) / len(retained) if retained else None,
        "retention": len(retained) / total if total else None,
        "lost_winning_selections": sum(x["won"] for x in items if x["excluded"]),
        "excluded": total - len(retained),
        "accuracy_delta": (
            sum(x["won"] for x in retained) / len(retained)
            - sum(x["won"] for x in items) / total
            if total and retained else None
        ),
    }


def paired_bootstrap_delta(items: list[dict[str, Any]], draws: int = 2000,
                           seed: int = 257) -> tuple[float, float] | None:
    """Paired resampling of unique sporting matches, not independent binomial samples."""
    if len(items) < 40 or sum(x["excluded"] for x in items) < 20:
        return None
    if sum(not x["excluded"] for x in items) < 20:
        return None
    rng = random.Random(seed)
    deltas = []
    for _ in range(draws):
        sampled = [items[rng.randrange(len(items))] for _ in items]
        delta = snapshot(sampled)["accuracy_delta"]
        if delta is not None:
            deltas.append(delta)
    if len(deltas) < draws * .95:
        return None
    deltas.sort()
    return deltas[int(.025 * (len(deltas) - 1))], deltas[int(.975 * (len(deltas) - 1))]


def assess_predeclared_filter(rows: Iterable[Mapping[str, Any]], *,
                              agent: str, population: str,
                              holdout_starts_at: str) -> dict[str, Any]:
    """Fixed-date training/holdout, no parameter tuning on holdout results."""
    cutoff = timestamp(holdout_starts_at)
    items = scored(rows, agent, population)
    training = [x for x in items if x["picked_at"] < cutoff]
    heldout = [x for x in items if x["picked_at"] >= cutoff]
    train = snapshot(training)
    hold = snapshot(heldout)
    ci = paired_bootstrap_delta(heldout)
    monthly: dict[str, list[dict[str, Any]]] = defaultdict(list)
    effects: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for x in heldout:
        monthly[x["picked_at"].strftime("%Y-%m")].append(x)
        effects[f"surface={x['surface']}|tier={x['tier']}"].append(x)
    candidate_in_training = (
        train["evaluated"] >= 40 and train["excluded"] >= 20
        and train["retained"] >= 20 and train["accuracy_delta"] is not None
        and train["accuracy_delta"] > 0
    )
    evidence_in_holdout = (
        candidate_in_training and ci is not None and ci[0] > 0
        and hold["retention"] is not None and hold["retention"] >= .75
    )
    return {
        "population": population, "agent": agent, "criterion": "verified selected rank 401-600",
        "holdout_starts_at": cutoff.isoformat(),
        "train": train, "holdout": hold,
        "holdout_delta_bootstrap_95": ci,
        "monthly_holdout": {k: snapshot(v) for k, v in sorted(monthly.items())},
        "surface_tier_holdout": {k: snapshot(v) for k, v in sorted(effects.items())},
        "candidate_training_gate": candidate_in_training,
        "holdout_statistical_gate": evidence_in_holdout,
        "independent_review_required": True,
        "decision": "NO_LIVE_RULE_CHANGE",
        "roi": None,
        "roi_reason": "unavailable unless original issued decision prices independently verified",
    }
