from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sports_edge_tennis.validation.acquisition_delay_decomposition import (
    FixtureAcquisitionClocks,
    build_report,
    decompose,
    summarize_decomposition,
)

T = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)


def _clocks(**overrides) -> FixtureAcquisitionClocks:
    values = {
        "match_id": 1,
        "provider_code": "ODDSPAPI",
        "first_fixture_discovered_at": T,
        "first_player_identity_resolved_at": T + timedelta(minutes=5),
        "first_bookmaker_fixture_discovered_at": T + timedelta(hours=1),
        "first_cross_provider_mapping_at": T + timedelta(hours=2, minutes=15),
        "first_individual_price_observed_at": T + timedelta(hours=2, minutes=10),
        "first_two_sided_market_at": T + timedelta(hours=2, minutes=10),
        "original_ai_issued_at": T,
        "prediction_locked_at": T + timedelta(minutes=1),
        "scheduled_start_at": T + timedelta(hours=20),
        "actual_competitive_start_at": None,
        "provider_fixture_updated_at": T + timedelta(hours=2, minutes=9),
        "provider_price_changed_at": T + timedelta(hours=2),
        "first_price_received_at": T + timedelta(hours=2, minutes=10),
        "first_price_persisted_at": T + timedelta(hours=2, minutes=10, seconds=5),
        "first_price_batch_started_at": T + timedelta(hours=2),
        "first_price_classification": "LIVE_OBSERVED",
        "provider_start_time": T + timedelta(hours=20, minutes=-10),
    }
    values.update(overrides)
    return FixtureAcquisitionClocks(**values)


def test_decomposition_attributes_measured_stages():
    result = decompose(_clocks())
    assert result["issue_to_first_price_seconds"] == 7800
    assert result["batch_schedule_wait_seconds"] == 7200
    assert result["within_batch_identity_wait_seconds"] == 0
    assert result["post_identity_provider_wait_seconds"] == 600
    assert result["persistence_wait_seconds"] == 5
    assert result["price_received_to_persisted_seconds"] == 5
    assert result["provider_start_to_scheduled_start_seconds"] == -600
    assert result["observed_after_scheduled_start"] is False
    assert "actual_competitive_start_at" in result["unavailable_clocks"]


def test_price_before_issue_is_reported_and_never_promoted():
    result = decompose(_clocks(
        first_individual_price_observed_at=T - timedelta(hours=1),
        first_price_received_at=T - timedelta(hours=1),
        first_price_persisted_at=T - timedelta(hours=1) + timedelta(seconds=1),
    ))
    assert result["issue_to_first_price_seconds"] == -3600
    assert result["issue_before_price"] is False


def test_missing_clocks_are_counted_not_imputed():
    result = decompose(_clocks(
        first_bookmaker_fixture_discovered_at=None,
        first_price_batch_started_at=None,
    ))
    assert result["identity_discovery_wait_seconds"] is None
    assert result["scheduling_wait_seconds"] is None
    assert result["batch_schedule_wait_seconds"] is None
    assert "first_bookmaker_fixture_discovered_at" in result["unavailable_clocks"]

    report = build_report([_clocks()])
    assert report["fixtures"] == 1
    assert report["fixtures_with_positive_issue_to_price"] == 1
    assert report["missing_actual_competitive_start"] == 1
    assert report["limitations"]
    assert report["fixtures_detail"][0]["match_id"] == 1


def test_summary_reports_shares_and_small_samples():
    rows = [decompose(_clocks()), decompose(_clocks(match_id=2))]
    summary = summarize_decomposition(rows)
    assert summary["fixtures"] == 2
    assert summary["issue_to_first_price"]["n"] == 2
    shares = summary["stage_component_medians_share_of_positive_lag"]
    assert shares["post_identity_provider_wait_seconds"] is not None
