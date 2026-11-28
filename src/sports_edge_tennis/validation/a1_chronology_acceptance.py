"""Independent chronology and replay parity audit, without changing Agent 1.

A strict evidence view requires actual ingestion timestamps, an explicit
start-time precision classification and source-visible version identifiers.
Unknown dates are quarantined, never silently treated as midnight matches.
This is a research acceptance contract, not a production loader.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Iterable

from sports_edge_tennis.agents import agent1_surface_elo as a1


@dataclass(frozen=True)
class HistoryEvidence:
    observation: a1.HistoricalMatchObservation
    result_observation_id: int
    match_version_id: int
    result_ingested_at: datetime
    version_ingested_at: datetime
    version_visible_at: datetime
    start_precision: str


def _aware(value):
    return isinstance(value, datetime) and value.utcoffset() is not None


def _digest(value):
    return sha256(json.dumps(value, default=str, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def strict_history(rows: Iterable[HistoryEvidence], *, cutoff, target_match_id):
    if not _aware(cutoff):
        raise ValueError("aware cutoff required")
    selected = {}
    excluded = {}
    seen = set()
    for row in rows:
        obs = row.observation
        key = (row.result_observation_id, row.match_version_id)
        if any(type(i) is not int or i <= 0 for i in key) or key in seen:
            raise ValueError("invalid or repeated source version pair")
        seen.add(key)
        timestamps = (obs.as_of, obs.source_observed_at, row.result_ingested_at,
                      row.version_ingested_at, row.version_visible_at)
        if not all(_aware(t) for t in timestamps):
            raise ValueError("missing visibility timestamps")
        if obs.match_id == target_match_id:
            reason = "TARGET_RESULT"
        elif any(t > cutoff for t in timestamps):
            reason = "NOT_VISIBLE_AT_CUTOFF"
        else:
            reason = None
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        # Pick the newest visible revision first. Never resurrect an older
        # valid result merely because a later revision has poor chronology.
        ordering = (obs.as_of, obs.source_observed_at, row.result_observation_id,
                    row.version_visible_at, row.match_version_id)
        previous = selected.get(obs.match_id)
        if previous is None or ordering > previous[0]:
            selected[obs.match_id] = (ordering, row)
    accepted = []
    for _, row in selected.values():
        obs = row.observation
        if row.version_visible_at > obs.as_of:
            reason = "VERSION_AFTER_RESULT"
        elif row.start_precision != "EXACT" or not _aware(obs.match_start_at):
            reason = "UNVERIFIED_START_PRECISION"
        elif obs.match_start_at >= obs.source_observed_at:
            reason = "RESULT_AT_OR_BEFORE_START"
        else:
            reason = None
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            accepted.append(row)
    accepted.sort(key=lambda r: (r.observation.as_of, r.observation.source_observed_at,
                                r.observation.match_id))
    return accepted, excluded


def audit_replay(request, *, target_match_id, frozen_rows, extended_rows):
    """Compare independently supplied frozen and extended history snapshots.

    Future destruction must preserve exact selected source rows and forecast,
    not merely a coincidentally equal probability. No live-equivalence claim
    is inferred from this isolated model-core check.
    """
    before, before_excluded = strict_history(frozen_rows, cutoff=request.as_of,
                                             target_match_id=target_match_id)
    after, after_excluded = strict_history(extended_rows, cutoff=request.as_of,
                                           target_match_id=target_match_id)
    before_hash, after_hash = (_digest([asdict(r) for r in rows])
                               for rows in (before, after))
    history = [row.observation for row in before]
    research = a1.predict(request, history, a1.DEFAULT_CONFIG)
    replay = a1.replay_history([row.observation for row in after], request.as_of,
                              a1.DEFAULT_CONFIG)
    runner_core = a1.predict_from_replay(request, replay, a1.DEFAULT_CONFIG)
    equivalent = before_hash == after_hash and research == runner_core
    return {"classification": "ISOLATED_REPLAY_CORE_PARITY",
            "frozen_history_sha256": before_hash,
            "extended_history_sha256": after_hash,
            "configuration_sha256": _digest(a1.DEFAULT_CONFIG.to_payload()),
            "request_sha256": _digest(asdict(request)),
            "frozen_rows": len(before), "extended_visible_rows": len(after),
            "frozen_excluded": before_excluded, "extended_excluded": after_excluded,
            "research_probability": str(research.player_1_probability),
            "runner_core_probability": str(runner_core.player_1_probability),
            "research_side": research.selection_side,
            "runner_core_side": runner_core.selection_side,
            "exact_parity": equivalent, "live_runtime_verified": False,
            "can_promote": False}
