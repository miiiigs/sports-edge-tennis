from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from sports_edge_tennis.agents.agent1_surface_elo import (
    HistoricalMatchObservation, PredictionRequest,
)
from sports_edge_tennis.validation.a1_chronology_acceptance import (
    HistoryEvidence, audit_replay, strict_history,
)

T = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
REQ = PredictionRequest(1, 2, "HARD", "ATP_MAIN", "ATP_250", 3, False, T)


def evidence(mid, *, start=None, observed=None, ingested=None, winner=1,
             revision=None, precision="EXACT", qualifying=False):
    start = start or T-timedelta(hours=4)
    observed = observed or T-timedelta(hours=2)
    ingested = ingested or observed
    obs = HistoricalMatchObservation(mid, 1, 2, "HARD", "ATP_MAIN",
        "ATP_250", 3, qualifying, start, "COMPLETED", winner, observed, observed)
    return HistoryEvidence(obs, revision or mid, mid, ingested, start, start, precision)


def test_destroy_future_results_late_arrivals_and_target():
    original = evidence(1, qualifying=True)
    future = evidence(2, start=T+timedelta(hours=1), observed=T+timedelta(hours=3))
    late = evidence(3, ingested=T+timedelta(seconds=1), winner=2)
    later_round = evidence(4, start=T+timedelta(hours=2),
                           observed=T+timedelta(hours=4))
    target = evidence(99)
    report = audit_replay(REQ, target_match_id=99, frozen_rows=[original],
                          extended_rows=[later_round, late, original, future, target])
    assert report["exact_parity"]
    assert report["frozen_rows"] == report["extended_visible_rows"] == 1
    assert report["extended_excluded"] == {"NOT_VISIBLE_AT_CUTOFF": 3, "TARGET_RESULT": 1}
    assert report["live_runtime_verified"] is False


def test_date_only_and_impossible_result_do_not_become_training_rows():
    rows = [evidence(1, precision="TOURNAMENT_DATE_ONLY"),
            replace(evidence(2, start=T-timedelta(hours=1), observed=T-timedelta(hours=2)),
                    version_visible_at=T-timedelta(hours=3),
                    version_ingested_at=T-timedelta(hours=3)),
            evidence(3)]
    accepted, rejected = strict_history(rows, cutoff=T, target_match_id=99)
    assert [x.observation.match_id for x in accepted] == [3]
    assert rejected == {"UNVERIFIED_START_PRECISION": 1, "RESULT_AT_OR_BEFORE_START": 1}


def test_later_correction_not_visible_then_becomes_latest_without_double_count():
    original = evidence(1)
    corrected = evidence(1, observed=T-timedelta(hours=1),
                         ingested=T+timedelta(minutes=1), winner=2, revision=20)
    report = audit_replay(REQ, target_match_id=99, frozen_rows=[original],
                          extended_rows=[corrected, original])
    assert report["exact_parity"]
    rows, _ = strict_history([corrected, original], cutoff=T+timedelta(minutes=2),
                             target_match_id=99)
    assert len(rows) == 1 and rows[0].observation.winner_side == 2


def test_visible_revision_with_ambiguous_date_is_not_silently_replaced_by_old():
    original = evidence(1)
    corrected = evidence(1, observed=T-timedelta(hours=1),
                         revision=20, precision="DATE_ONLY")
    rows, rejected = strict_history([original, corrected], cutoff=T, target_match_id=99)
    assert rows == []
    assert rejected == {"UNVERIFIED_START_PRECISION": 1}


def test_parity_checks_lineage_even_when_probability_identical():
    original = evidence(1)
    replacement = replace(original, result_observation_id=20)
    result = audit_replay(REQ, target_match_id=99, frozen_rows=[original],
                          extended_rows=[replacement])
    assert result["research_probability"] == result["runner_core_probability"]
    assert not result["exact_parity"]


def test_duplicate_source_pair_and_naive_timestamps_rejected():
    row = evidence(1)
    with pytest.raises(ValueError, match="repeated"):
        strict_history([row,row], cutoff=T, target_match_id=99)
    with pytest.raises(ValueError, match="visibility"):
        strict_history([replace(row, result_ingested_at=T.replace(tzinfo=None))],
                       cutoff=T, target_match_id=99)


def test_latest_visible_result_with_incompatible_version_does_not_resurrect_prior():
    original = evidence(1)
    corrected = replace(evidence(1, observed=T-timedelta(hours=1), revision=20),
                        version_visible_at=T-timedelta(minutes=10))
    rows, excluded = strict_history([original, corrected], cutoff=T, target_match_id=99)
    assert rows == []
    assert excluded == {"VERSION_AFTER_RESULT": 1}
