"""Inactive corrected A1/Manager C execution rehearsal.

Runs the actual A1 replay and frozen selection implementations on explicit
synthetic inputs. It never loads commercial data, publishes or writes to a
database. Its output is ineligible for prospective or promotion evidence.
"""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

from sports_edge_tennis.agents import agent1_surface_elo as a1
from sports_edge_tennis.validation import prospective_policy_registry as registry


def _digest(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str,
        allow_nan=False).encode()).hexdigest()


def _file_digest(module):
    return sha256(Path(module.__file__).read_bytes()).hexdigest()


def _aware(value):
    return isinstance(value, datetime) and value.utcoffset() is not None


def rehearse_corrected_candidates(
    *, match_id, request, scheduled_start, history, identity_map, other_agents,
):
    """Execute corrected B/D and C using explicit simulated identity evidence.

    The caller supplies original history and a complete simulated mapping.
    Missing or colliding mappings fail closed. Agent 2/3 inputs are injected,
    not independently executed. The default A1 config and registry thresholds
    are used unchanged. No formal activation pathway exists in this module.
    """
    if not _aware(request.as_of) or not _aware(scheduled_start):
        raise ValueError("aware cutoff and start required")
    if request.qualifying_flag or scheduled_start - request.as_of < timedelta(minutes=30):
        raise ValueError("outside main-draw pre-match eligibility")
    if type(match_id) is not int or match_id <= 0:
        raise ValueError("invalid target match ID")
    history = list(history)
    ids = [row.match_id for row in history]
    if match_id in ids or len(set(ids)) != len(ids):
        raise ValueError("target or duplicate history match")
    for row in history:
        if (not _aware(row.as_of) or not _aware(row.source_observed_at)
                or row.as_of > request.as_of
                or row.source_observed_at > request.as_of
                or (row.match_start_at is not None and
                    (not _aware(row.match_start_at)
                     or row.match_start_at >= request.as_of))):
            raise ValueError("history violates rehearsal cutoff")

    def model_id(player):
        resolved = identity_map.get(player)
        if type(resolved) is not int or resolved <= 0:
            raise ValueError("explicit model identity missing")
        return resolved

    def pair(p1, p2):
        resolved = (model_id(p1), model_id(p2))
        if resolved[0] == resolved[1]:
            raise ValueError("identity mapping collision")
        return resolved

    p1, p2 = pair(request.player_1_id, request.player_2_id)
    bridged_request = replace(request, player_1_id=p1, player_2_id=p2)
    history.sort(key=lambda row: (
        row.as_of, row.source_observed_at, row.match_id,
        row.player_1_id, row.player_2_id))
    bridged = []
    for row in history:
        h1, h2 = pair(row.player_1_id, row.player_2_id)
        bridged.append(replace(row, player_1_id=h1, player_2_id=h2))
    replay = a1.replay_history(bridged, request.as_of, a1.DEFAULT_CONFIG)
    result = a1.predict_from_replay(bridged_request, replay, a1.DEFAULT_CONFIG)
    probability = float(result.player_1_probability)
    primary, secondary = registry.corrected_a1_decisions(
        status=result.prediction_status, p1=probability)
    manager = registry.corrected_manager_c_decision(
        corrected_a1_status=result.prediction_status,
        corrected_a1_p1=probability, other_agents=other_agents)
    evidence = {
        "classification": "SYNTHETIC_REHEARSAL_ONLY",
        "match_id": match_id,
        "cutoff": request.as_of.isoformat(),
        "scheduled_start": scheduled_start.isoformat(),
        "original_player_ids": [request.player_1_id, request.player_2_id],
        "model_player_ids": [p1, p2],
        "history_matches": len(history),
        "eligible_history_matches": replay.eligible_history_match_count,
        "model_version": result.model_version,
        "feature_version": result.feature_version,
        "model_source_sha256": _file_digest(a1),
        "policy_source_sha256": _file_digest(registry),
        "configuration_sha256": _digest(a1.DEFAULT_CONFIG.to_payload()),
        "original_history_sha256": _digest([asdict(r) for r in history]),
        "bridged_history_sha256": _digest([asdict(r) for r in bridged]),
        "identity_map_sha256": _digest(sorted(identity_map.items())),
        "rating_state_sha256": _digest(asdict(replay)),
        "feature_input_sha256": _digest(asdict(bridged_request)),
        "other_agent_inputs_sha256": _digest(other_agents),
        "a1_probability": probability,
        "diagnostics": result.diagnostics.to_payload(),
        "decisions": {"B": asdict(primary), "C": asdict(manager),
                      "D": asdict(secondary)},
        "other_agents_executed": False,
        "independent_receipt_verified": False,
        "eligible_for_prospective_evidence": False,
        "production_changed": False,
        "can_promote": False,
    }
    return {**evidence, "evidence_sha256": _digest(evidence)}
