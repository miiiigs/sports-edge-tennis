"""Pure research-logic tests for the Challenger history-completeness study."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.validation.manager_c_challenger_history import (
    MIN_HISTORY,
    PlayerHistory,
    _classify,
    _stats,
    build_index,
    extended_original_sql,
    selected_probability,
)
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY
from sports_edge_tennis.validation.manager_c_original_source import select_cohorts

CUTOFF = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def test_selected_probability_is_the_pick_not_always_player_one():
    assert selected_probability({"side": 1, "p": 0.72}) == pytest.approx(0.72)
    assert selected_probability({"side": 2, "p": 0.28}) == pytest.approx(0.72)


def test_stats_use_player_one_probability_for_scoring_on_side_two():
    row = {
        "match_id": 1, "side": 2, "p": 0.30, "winner": 2,
        "cluster": 10,
    }
    stats = _stats([row])
    assert stats["wins"] == 1
    assert stats["expected_wins"] == pytest.approx(0.70)
    assert stats["residual"] == pytest.approx(0.30)
    assert stats["brier"] == pytest.approx(0.30 ** 2)
    assert stats["log_loss"] == pytest.approx(-__import__("math").log(0.70))


def test_identity_gap_is_not_labeled_inexperience():
    labels = _classify(
        1, raw=5, bridged=120, cutoff=CUTOFF,
        first_start=CUTOFF - timedelta(days=900),
        last_start=CUTOFF - timedelta(days=10),
        historical_identity=True, resolved=True, starts_complete=True,
    )
    assert labels["primary"] == "IDENTITY_GAP_RECOVERED"
    assert "IDENTITY_GAP_RECOVERED" in labels["flags"]
    assert "SHORT_CAREER_ITF_OR_DEBUT" not in labels["flags"]


def test_partial_identity_gain_stays_explicit():
    labels = _classify(
        1, raw=4, bridged=6, cutoff=CUTOFF,
        first_start=CUTOFF - timedelta(days=900),
        last_start=CUTOFF - timedelta(days=10),
        historical_identity=True, resolved=True, starts_complete=True,
    )
    assert "IDENTITY_GAP_PARTIAL" in labels["flags"]
    assert labels["primary"] != "IDENTITY_GAP_RECOVERED"


def test_unresolved_live_only_identity_is_explicit():
    labels = _classify(
        1, raw=6, bridged=6, cutoff=CUTOFF,
        first_start=CUTOFF - timedelta(days=900),
        last_start=CUTOFF - timedelta(days=30),
        historical_identity=False, resolved=False, starts_complete=True,
    )
    assert "IDENTITY_LINK_UNRESOLVED" in labels["flags"]
    assert labels["primary"] == "LOW_HISTORY_OTHER"


def test_inactivity_requires_a_complete_date_range():
    active = _classify(
        1, raw=10, bridged=10, cutoff=CUTOFF,
        first_start=CUTOFF - timedelta(days=1200),
        last_start=CUTOFF - timedelta(days=200),
        historical_identity=True, resolved=True, starts_complete=True,
    )
    assert active["primary"] == "RETURN_FROM_INACTIVITY"

    partial = _classify(
        1, raw=10, bridged=10, cutoff=CUTOFF,
        first_start=None, last_start=None,
        historical_identity=True, resolved=True, starts_complete=False,
    )
    assert "START_DATE_INCOMPLETE" in partial["flags"]
    assert "RETURN_FROM_INACTIVITY" not in partial["flags"]


def test_short_career_is_a_union_not_a_genuine_debut_claim():
    labels = _classify(
        1, raw=8, bridged=8, cutoff=CUTOFF,
        first_start=CUTOFF - timedelta(days=100),
        last_start=CUTOFF - timedelta(days=5),
        historical_identity=True, resolved=True, starts_complete=True,
    )
    assert labels["primary"] == "SHORT_CAREER_ITF_OR_DEBUT"
    assert "SHORT_CAREER_ITF_OR_DEBUT" in labels["flags"]


def test_history_at_threshold_is_not_flagged():
    labels = _classify(
        1, raw=MIN_HISTORY, bridged=MIN_HISTORY, cutoff=CUTOFF,
        first_start=CUTOFF - timedelta(days=900),
        last_start=CUTOFF - timedelta(days=5),
        historical_identity=True, resolved=True, starts_complete=True,
    )
    assert labels["primary"] == "HISTORY_AT_LEAST_20"
    assert "FAVOURITE_UNDER_20" not in labels["flags"]


def test_player_history_does_not_infer_start_from_null_dates():
    rows = [
        (CUTOFF - timedelta(days=20), None, "HARD", "CHALLENGER", "CHALLENGER", 1),
        (CUTOFF - timedelta(days=10), CUTOFF - timedelta(days=400), "HARD",
         "CHALLENGER", "CHALLENGER", 2),
    ]
    history = PlayerHistory(rows)
    assert history.count(CUTOFF) == 2
    assert history.start_dates_complete is False
    assert history.first_start(CUTOFF) == CUTOFF - timedelta(days=400)


def test_build_index_applies_bridge_and_keeps_collisions_raw():
    rows = [
        (1, 100, 200, "HARD", "CHALLENGER", "CHALLENGER",
         CUTOFF - timedelta(days=3), CUTOFF - timedelta(days=5)),
    ]
    bridged = build_index(rows, {100: 900})
    assert bridged[900].count(CUTOFF) == 1
    assert 100 not in bridged

    collision = build_index(rows, {100: 5, 200: 5})
    assert collision[100].count(CUTOFF) == 1
    assert collision[200].count(CUTOFF) == 1
    assert 5 not in collision


def test_extended_source_sql_only_appends_lineage_columns():
    from sports_edge_tennis.validation.manager_c_original_source import original_sql

    extended = extended_original_sql()
    expected = original_sql().replace(
        "AS p2_rating_sample_visible\n    FROM original o",
        "AS p2_rating_sample_visible,v.player_1_id,v.player_2_id,"
        "p.as_of\n    FROM original o",
    )
    assert extended == expected
    assert extended.count("v.player_1_id,v.player_2_id,p.as_of") == 1


def _record(mid: int, start: datetime, probability: float, winner: int | None):
    return {
        "match_id": mid,
        "run_id": mid,
        "tour": "CHALLENGER",
        "edition": "1",
        "issued_at": start - timedelta(hours=3),
        "start_at": start,
        "winner": winner,
        "result_status": "COMPLETED",
        "candidates": {POLICY: (1 if probability > 0.5 else 2, probability)},
    }


def test_select_cohorts_keeps_narrow_nested_inside_broad():
    validation_end = datetime(2026, 11, 4, tzinfo=UTC)
    early, later = [], []
    for index in range(40):
        probability = 0.50 + (index % 20) * 0.02
        early.append(_record(index + 1, validation_end - timedelta(days=30 - index % 20),
                             probability, 1))
    for index in range(40):
        probability = 0.50 + (index % 20) * 0.02
        later.append(_record(1000 + index, validation_end + timedelta(days=1 + index),
                             probability, 1 if index % 2 else 2))
    data = {
        "records": early + later,
        "agents": {},
        "meta": {},
        "source_sha256": "x",
    }
    cohorts = select_cohorts(data, validation_end=validation_end)
    groups = cohorts["groups"]
    narrow = {r["match_id"] for r in groups["narrow"]}
    broad = {r["match_id"] for r in groups["broad"]}
    marginal = {r["match_id"] for r in groups["marginal"]}
    rejected = {r["match_id"] for r in groups["rejected"]}
    assert narrow and broad
    assert narrow <= broad
    assert marginal == broad - narrow
    assert rejected == {r["match_id"] for r in later} - broad
    assert cohorts["thresholds"]["narrow"]["threshold"] >= cohorts["thresholds"]["broad"]["threshold"]
