"""Deterministic synthetic self-test of the full replay harness.

The fixture is synthetic. These numbers validate the harness and determinism;
they are not CURRENT_SYSTEM_REPLAY_20260910_V1 production performance.
"""

from __future__ import annotations

from decimal import Decimal

from sports_edge_tennis.replay.first_qualified import (
    first_qualified_model_picks,
    first_qualified_value_bets,
    value_bet_metrics,
)
from sports_edge_tennis.replay.manifest import reproducibility_sha256
from sports_edge_tennis.replay.selftest import (
    build_selftest_manifest,
    build_selftest_result,
    write_selftest_artifact,
)


def test_selftest_exercises_the_full_funnel():
    result = build_selftest_result()

    first_picks = first_qualified_model_picks(result.model_picks)
    first_bets = first_qualified_value_bets(result.value_bets)

    assert len(first_picks) == 6
    assert len(first_bets) == 3
    metrics = value_bet_metrics(first_bets)
    assert metrics.wins == 1
    assert metrics.losses == 1
    assert metrics.voids == 1
    assert metrics.profit_units == Decimal("0.30")
    assert metrics.roi == Decimal("0.30") / Decimal(3)
    assert metrics.hit_rate == 0.5

    outcomes = {opportunity.outcome for opportunity in result.opportunities}
    assert {
        "QUALIFIED_VALUE_BET",
        "INSUFFICIENT_EDGE",
        "VETO_REJECTION",
        "MANAGER_REJECTION",
        "DATA_NOT_AVAILABLE_PIT",
    }.issubset(outcomes)

    exclusion_reasons = {exclusion.reason for exclusion in result.exclusions}
    assert {
        "IDENTITY_NOT_AVAILABLE_PIT",
        "SURFACE_BLOCKED",
        "MISSING_MARKET_ODDS",
    }.issubset(exclusion_reasons)

    classifications = {pick.classification for pick in result.model_picks}
    assert "A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED" in classifications


def test_selftest_is_reproducible():
    first = build_selftest_result()
    second = build_selftest_result()
    assert first.model_picks == second.model_picks
    assert first.value_bets == second.value_bets
    assert first.exclusions == second.exclusions
    assert first.opportunities == second.opportunities

    first_manifest = build_selftest_manifest(first)
    second_manifest = build_selftest_manifest(second)
    assert first_manifest.ledger_sha256 == second_manifest.ledger_sha256
    assert reproducibility_sha256(first_manifest) == reproducibility_sha256(second_manifest)


def test_selftest_manifest_counts():
    result = build_selftest_result()
    manifest = build_selftest_manifest(result)

    assert manifest.first_qualified_model_picks == 6
    assert manifest.first_qualified_value_bets == 3
    assert manifest.identity_failures == 1
    assert manifest.surface_failures == 1
    # Matches 2 and 7 have no captured odds in run 1.
    assert manifest.missing_historical_odds == 2
    assert manifest.pit_incomplete_cases == 4
    assert manifest.data_not_available_pit_count == 8
    assert manifest.provisional is True
    assert manifest.provisional_label == "PROVISIONAL - HISTORICAL RECOVERY SNAPSHOT NOT FROZEN"


def test_selftest_writes_immutable_artifact(tmp_path):
    paths = write_selftest_artifact(tmp_path)
    assert paths.manifest_path.exists()
    assert paths.value_bets_path.exists()
