"""January 2027 scope: authoritative dates, formats and explicit unknowns."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sports_edge_tennis.validation.january_scope import (
    JanuaryScopeError,
    assert_readiness_precedes,
    eligible_competitions,
    first_eligible_start,
    load_scope,
    qualifying_window,
    readiness_matrix,
    validate_scope,
)

SPEC = Path("specs/january_2027_scope_20261127.json")


@pytest.fixture(scope="module")
def spec():
    return load_scope(SPEC)


def test_spec_file_exists_and_validates():
    assert SPEC.exists()
    payload = json.loads(SPEC.read_text(encoding="utf-8"))
    validate_scope(payload)
    assert payload["fixtures"] == []
    assert payload["entries_published"] is False
    assert payload["draws_published"] is False


def test_australian_open_is_best_of_five_and_qualifying_best_of_three(spec):
    ao = next(
        item for item in spec["competitions"] if item["key"] == "AUSTRALIAN_OPEN_2027"
    )
    qualifying = next(
        item
        for item in spec["competitions"]
        if item["competition_tier"] == "GRAND_SLAM_QUALIFYING"
    )
    assert ao["best_of_sets"] == 5
    assert ao["deciding_set_rule"] == "TB10_AT_6_6"
    assert ao["scheduled_start"] == "2027-01-17"
    assert ao["scheduled_end"] == "2027-01-31"
    assert qualifying["best_of_sets"] == 3
    assert qualifying["exact_first_match_utc"] is None
    assert qualifying["forecast_enabled"] is False


def test_opening_week_and_qualifying_window_are_not_conflated(spec):
    start, end = qualifying_window(spec)
    assert start == date(2027, 1, 10)
    assert end == date(2027, 1, 16)
    ao = next(
        item for item in spec["competitions"] if item["key"] == "AUSTRALIAN_OPEN_2027"
    )
    assert date.fromisoformat(ao["scheduled_start"]) > end


def test_first_eligible_fixture_precedes_the_australian_open(spec):
    assert first_eligible_start(spec) == date(2027, 1, 4)
    keys = {competition["key"] for competition in eligible_competitions(spec)}
    assert "BRISBANE_INTERNATIONAL_2027" in keys
    assert "HONG_KONG_TENNIS_OPEN_2027" in keys
    assert "CANBERRA_CHALLENGER_2027" in keys


def test_united_cup_and_team_events_are_explicitly_unsupported(spec):
    united = next(
        item for item in spec["competitions"] if item["key"] == "UNITED_CUP_2027"
    )
    assert united["singles_eligible"] is False
    assert "team" in united["exclusion_reason"].lower()
    unsupported = {item["key"] for item in spec["unsupported_competitions"]}
    assert "DAVIS_CUP_2027" in unsupported
    assert "LAVER_CUP_2027" in unsupported
    assert "KOOYONG_CLASSIC_2027" in unsupported


def test_readiness_must_precede_the_first_eligible_fixture(spec):
    with pytest.raises(JanuaryScopeError, match="must precede"):
        assert_readiness_precedes(
            spec, datetime(2027, 1, 4, 0, 0, tzinfo=UTC)
        )
    assert_readiness_precedes(spec, datetime(2027, 1, 3, 23, 59, tzinfo=UTC))


def test_readiness_matrix_keeps_unknowns_unknown(spec):
    matrix = {item["item"]: item for item in readiness_matrix(spec)}
    assert matrix["2027 edition-specific AO rulebook"]["status"] == "BLOCKED"
    assert (
        matrix["Published 2027 entry lists and draws"]["status"]
        == "UNKNOWN_NOT_PUBLISHED"
    )
    assert (
        matrix["API-Tennis get_draw and include_qualification entitlement"]["status"]
        == "BLOCKED_UNVERIFIED"
    )
    assert (
        matrix["Owner approval and controlled release"]["status"]
        == "BLOCKED_NOT_REQUESTED"
    )


def test_scope_rejects_published_entries_or_invented_fixtures(spec):
    mutated = json.loads(json.dumps(spec))
    mutated["entries_published"] = True
    with pytest.raises(JanuaryScopeError, match="published entries"):
        validate_scope(mutated)
    mutated = json.loads(json.dumps(spec))
    mutated["fixtures"] = [{"match_id": "invented"}]
    with pytest.raises(JanuaryScopeError, match="invent fixtures"):
        validate_scope(mutated)


def test_scope_rejects_a_wrong_grand_slam_format(spec):
    mutated = json.loads(json.dumps(spec))
    ao = next(
        item
        for item in mutated["competitions"]
        if item["key"] == "AUSTRALIAN_OPEN_2027"
    )
    ao["best_of_sets"] = 3
    with pytest.raises(JanuaryScopeError, match="must be BO5"):
        validate_scope(mutated)


def test_scope_rejects_unknown_sources(spec):
    mutated = json.loads(json.dumps(spec))
    mutated["competitions"][0]["source_ids"] = ["S_UNKNOWN"]
    with pytest.raises(JanuaryScopeError, match="unknown source"):
        validate_scope(mutated)
