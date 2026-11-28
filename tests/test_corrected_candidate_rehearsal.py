from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.agents.agent1_surface_elo import (
    HistoricalMatchObservation, PredictionRequest,
)
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES
from sports_edge_tennis.validation.corrected_candidate_rehearsal import (
    rehearse_corrected_candidates,
)
from sports_edge_tennis.validation.prospective_policy_registry import (
    corrected_a1_decisions, corrected_manager_c_decision,
)

AT = datetime(2026, 11, 27, 12, tzinfo=UTC)


def inputs():
    history = []
    identity_map = {11: 101, 101: 101, 202: 202}
    for i in range(8):
        opponent = 1000 + i
        identity_map[opponent] = opponent
        visible = AT - timedelta(days=10-i)
        history.append(HistoricalMatchObservation(
            match_id=i+1, player_1_id=11, player_2_id=opponent,
            surface="HARD", tour_scope="CHALLENGER", competition_tier="CHALLENGER",
            best_of_sets=3, qualifying_flag=False,
            match_start_at=visible-timedelta(hours=2), outcome_status="COMPLETED",
            winner_side=1, source_observed_at=visible, as_of=visible))
    return dict(
        match_id=100, request=PredictionRequest(
            player_1_id=101, player_2_id=202, surface="HARD",
            tour_scope="CHALLENGER", competition_tier="CHALLENGER",
            best_of_sets=3, qualifying_flag=False, as_of=AT),
        scheduled_start=AT+timedelta(hours=2), history=history,
        identity_map=identity_map,
        other_agents={CODES[1]: ("PREDICTED", 1, 0.8),
                      CODES[2]: ("ABSTAINED", None, None)})


def test_rehearsal_runs_real_replay_and_both_frozen_policies():
    params = inputs()
    result = rehearse_corrected_candidates(**params)
    p1 = result["a1_probability"]
    assert p1 > 0.5
    assert result["diagnostics"]["player_1_overall_matches"] == 8
    assert result["original_history_sha256"] != result["bridged_history_sha256"]
    b, d = corrected_a1_decisions(status="PREDICTED", p1=p1)
    assert result["decisions"]["B"]["status"] == b.status
    assert result["decisions"]["D"]["status"] == d.status
    assert result["decisions"]["B"]["p1"] == result["decisions"]["D"]["p1"] == p1
    c = corrected_manager_c_decision(
        corrected_a1_status="PREDICTED", corrected_a1_p1=p1,
        other_agents=params["other_agents"])
    assert result["decisions"]["C"]["p1"] == c.p1
    assert result["eligible_for_prospective_evidence"] is False
    assert result["can_promote"] is False
    assert result["evidence_sha256"] == rehearse_corrected_candidates(**params)["evidence_sha256"]


def test_missing_identity_or_collision_fails_closed():
    params = inputs()
    del params["identity_map"][11]
    with pytest.raises(ValueError, match="identity missing"):
        rehearse_corrected_candidates(**params)
    params = inputs()
    params["identity_map"][11] = 1000
    with pytest.raises(ValueError, match="collision"):
        rehearse_corrected_candidates(**params)


def test_target_in_history_or_late_fixture_is_rejected():
    params = inputs()
    params["match_id"] = 1
    with pytest.raises(ValueError, match="target or duplicate"):
        rehearse_corrected_candidates(**params)
    params = inputs()
    params["scheduled_start"] = AT + timedelta(minutes=29)
    with pytest.raises(ValueError, match="eligibility"):
        rehearse_corrected_candidates(**params)
