"""Synthetic offline tests; no actual ranking cohort conclusions are implied."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from research.tennis_pit_rank_evidence_20261022.enrichment import (
    agent_rank_view, asof_one, coverage, enrich, freeze_first_production,
    group_scores, timestamp, valid_rank_evidence,
)

FINGERPRINT = "c" * 64


def pick(**changes):
    row = {
        "pick_id": "pick-1", "population": "MANAGER",
        "selection_definition": "MANAGER_PRIMARY",
        "canonical_match_id": "sporting-match-1", "prediction_run_id": "run-1",
        "model_version": "m-v1", "first_production": True,
        "prediction_finalized": True, "is_experiment": False,
        "tour": "ATP", "picked_at": "2026-10-20T08:00:00Z",
        "scheduled_start_at": "2026-10-20T14:00:00Z",
        "event_date": "2026-10-20", "selected_player_id": "canonical-p1",
        "opponent_player_id": "canonical-p2",
        "side1_player_id": "canonical-p1", "side2_player_id": "canonical-p2",
        "manager_side": 1, "manager_p": .67,
        "a1_side": 1, "a1_p": .72, "a2_side": 2, "a2_p": .61,
        "outcome_status": "COMPLETED", "winner_side": 1,
        "surface": "HARD", "tournament_tier": "CHALLENGER",
    }
    row.update(changes)
    return row


def rank(player_id="canonical-p1", value=451, **changes):
    row = {
        "authority": "ATP", "tour": "ATP",
        "source_kind": "OFFICIAL", "ranking_type": "SINGLES",
        "license_authorized": True,
        "identity_verified": True, "published_at_verified": True,
        "player_id": player_id, "identity_evidence_id": "verified-crosswalk-1",
        "release_evidence_id": "publisher-release-1", "source_record_id": "r-1",
        "source_url": "https://example.org/authorized-export",
        "snapshot_sha256": FINGERPRINT, "rank": value,
        "effective_date": "2026-10-19",
        "source_published_at": "2026-10-19T09:30:00+00:00",
    }
    row.update(changes)
    return row


def test_only_explicit_timestamp_and_rank_evidence():
    with pytest.raises(ValueError):
        timestamp("2026-10-19")
    with pytest.raises(ValueError):
        timestamp("2026-10-19T08:00:00")
    assert timestamp("2026-10-19T08:00:00+08:00") == datetime(
        2026, 10, 19, tzinfo=timezone.utc
    )
    assert valid_rank_evidence(rank())
    assert not valid_rank_evidence(rank(source_published_at="2026-10-19"))
    assert not valid_rank_evidence(rank(published_at_verified=False))
    assert not valid_rank_evidence(rank(source_kind="RECONSTRUCTED"))
    assert not valid_rank_evidence(rank(license_authorized=False))
    assert not valid_rank_evidence(rank(identity_verified=False))
    assert not valid_rank_evidence(rank(rank=True))


def test_join_by_exact_canonical_id_and_tour():
    result = enrich([pick()], [
        rank(), rank("canonical-p2", 31, source_record_id="r-2"),
        rank("canonical-p1", 1, tour="WTA", authority="WTA"),
        rank("wrong-id", 3)
    ])
    assert len(result) == 1
    assert result[0]["selected_rank"] == 451
    assert result[0]["opponent_rank"] == 31
    assert result[0]["rank_gap_opponent_minus_selected"] == -420
    assert result[0]["selected_rank_band"] == "401-600"
    assert result[0]["selected_rank_evidence"]["release_evidence_id"] == "publisher-release-1"


def test_future_ranking_cannot_leak_backward_even_if_effective_date_old():
    result = enrich([pick()], [rank(source_published_at="2026-10-20T08:00:01Z")])
    assert result[0]["selected_rank"] is None
    assert result[0]["selected_rank_status"] == "NO_AUTHORIZED_VERIFIED_PRE_PICK_RANK"


def test_wrong_sporting_event_date_and_stale_rank_fail_closed():
    result = enrich([pick()], [rank(effective_date="2026-07-01")])
    assert result[0]["selected_rank"] is None
    result = enrich([pick()], [rank(effective_date="2026-10-21", source_published_at="2026-10-21T00:00:00Z")])
    assert result[0]["selected_rank"] is None
    with pytest.raises(ValueError, match="event date"):
        enrich([pick(event_date="2026-10-16")], [])


def test_unknown_or_unsigned_source_is_not_unranked_proof():
    out = enrich([pick()], [rank(license_authorized=False)])
    assert out[0]["selected_rank_band"] == "MISSING_OR_UNRANKED"
    assert out[0]["selected_rank_status"] == "NO_AUTHORIZED_VERIFIED_PRE_PICK_RANK"
    assert coverage(out)[0]["both_verified"] == 0


def test_conflicting_same_week_official_sources_do_not_pick_favorable_rank():
    out = enrich([pick()], [rank(value=451), rank(value=99, source_record_id="r-2")])
    assert out[0]["selected_rank"] is None
    assert out[0]["selected_rank_status"] == "CONFLICTING_OFFICIAL_RANK"


def test_latest_eligible_week_wins_and_future_week_rejected():
    ranked = [
        rank(value=501, effective_date="2026-10-12", source_published_at="2026-10-12T09:30:00Z"),
        rank(value=451), rank(value=200, source_published_at="2026-10-21T09:30:00Z"),
    ]
    assert enrich([pick()], ranked)[0]["selected_rank"] == 451


def test_first_production_is_stratified_by_canonical_sporting_match():
    p = pick()
    later = pick(pick_id="pick-2", picked_at="2026-10-20T09:00:00Z", prediction_run_id="run-2")
    tracking = pick(pick_id="pick-3", population="A1_A2_TRACKING",
                    selection_definition="A1_A2_WIDE_SPREAD")
    out = enrich([later, tracking, p], [rank()])
    assert len(out) == 2
    assert [r["pick_id"] for r in out] == ["pick-1", "pick-3"]
    assert out[0]["model_version"] == "m-v1"


@pytest.mark.parametrize("change", [
    {"first_production": False},
    {"is_experiment": True},
    {"prediction_finalized": False},
    {"selected_player_id": "canonical-p2"},
    {"model_version": ""},
    {"selection_definition": "A1_A2_WIDE_SPREAD"},
    {"picked_at": "2026-10-21T08:00:00Z"},
])
def test_invalid_population_or_provenance_fails(change):
    with pytest.raises(ValueError):
        freeze_first_production([pick(**change)])


def test_agent_scores_are_separate_and_void_excluded():
    rows = enrich([
        pick(),
        pick(pick_id="pick-2", canonical_match_id="sporting-match-2",
             outcome_status="WALKOVER", winner_side=None),
    ], [rank(), rank("canonical-p2", 31, source_record_id="r-2")])
    assert len(rows) == 2
    manager = [r for r in group_scores(rows)
               if r["agent"] == "manager" and r["rank_axis"] == "selected"
               and r["band"] == "401-600"][0]
    a2 = [r for r in group_scores(rows)
          if r["agent"] == "a2" and r["rank_axis"] == "selected"
          and r["band"] == "1-100"][0]
    assert (manager["settled_with_prediction"], manager["wins"]) == (1, 1)
    assert (a2["settled_with_prediction"], a2["losses"]) == (1, 1)
    assert manager["exploratory_only"]


def test_real_rank_cohort_is_not_produced_when_no_evidence():
    rows = enrich([pick()], [])
    grouped = [r for r in group_scores(rows)
               if r["rank_axis"] == "selected" and r["agent"] == "manager"]
    assert len(grouped) == 1
    assert grouped[0]["band"] == "MISSING_OR_UNRANKED"
    assert coverage(rows)[0]["selected_verified"] == 0


def test_agent_chooses_opposite_side_and_gets_opponent_rank_as_selected():
    enriched = enrich([pick()], [
        rank(), rank("canonical-p2", 31, source_record_id="other")
    ])[0]
    manager = agent_rank_view(enriched, "manager")
    a2 = agent_rank_view(enriched, "a2")
    assert manager["selected_rank"] == 451
    assert manager["opponent_rank"] == 31
    assert manager["gap"] == -420
    assert a2["selected_rank"] == 31
    assert a2["selected_rank_band"] == "1-100"
    assert a2["opponent_rank"] == 451
    assert a2["gap"] == 420


def test_manager_selected_identity_must_match_original_player_side():
    with pytest.raises(ValueError, match="identity/side mismatch"):
        enrich([pick(selected_player_id="canonical-p2", opponent_player_id="canonical-p1")], [])
