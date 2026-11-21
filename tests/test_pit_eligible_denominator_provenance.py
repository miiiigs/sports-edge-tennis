"""Strict PIT eligibility, first-issue and legacy-boundary regression checks."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.agent1_walk_forward import (
    ValidationObservation,
    build_prediction_target,
    run_walk_forward_validation,
)
from sports_edge_tennis.validation.pit_eligible_denominator_provenance import (
    _classify, fixture_source_sql, legacy_historical_sql,
    run, summarize, summarize_legacy,
)

START=datetime(2026,10,15,tzinfo=UTC)
END=datetime(2026,11,21,tzinfo=UTC)
ASOF=datetime(2026,11,21,3,30,tzinfo=UTC)
GAME=datetime(2026,11,5,12,tzinfo=UTC)


def fixture(
    mid:int, *,tour="ATP_MAIN",qualifying=False,
    pit=True, pit_status="SCHEDULED",pit_start=None,
    run_at=None,run_id=55,pick_at=None,reason="AGENT3_ABSTENTION",
    manager_status="NO_BET",result_status="COMPLETED",
    result_known=None,fixture_known=None,
):
    when=pit_start if pit_start is not None else GAME
    at=run_at if run_at is not None else GAME-timedelta(hours=2)
    known=fixture_known if fixture_known is not None else when-timedelta(days=2)
    result_at=(result_known if result_known is not None else GAME+timedelta(hours=2))
    return (
        mid,GAME,tour,qualifying,"COMPLETED",GAME+timedelta(days=2),
        when if pit else None,tour if pit else None,
        qualifying if pit else None,pit_status if pit else None,
        known if pit else None,
        run_id,at if run_id is not None else None,
        1,1,0,0,0,1,manager_status,reason,pick_at,
        "MANAGER_CONSENSUS" if pick_at is not None else None,
        result_status,1 if result_status=="COMPLETED" else None,
        result_at if result_status=="COMPLETED" else None,
        "API_TENNIS,ODDSPAPI",
    )


def assess(items):
    return summarize(items,start=START,end=END,as_of=ASOF)


def test_eligible_slates_count_original_issuance_and_aligned_separately():
    f1=fixture(1,pick_at=GAME-timedelta(hours=1),manager_status="CONSENSUS")
    f2=fixture(2,run_id=None)
    f3=fixture(3,qualifying=True,pick_at=GAME-timedelta(hours=1))
    f4=fixture(4,tour="CHALLENGER",pit=False)
    f5=fixture(5,tour="CHALLENGER",pit_status="POSTPONED")
    o=assess([f1,f2,f3,f4,f5])
    assert o["stored_canonical_fixtures"]==5
    assert o["pit_visible_at_least_30m"]==4
    assert o["main_draw_pit_proxy"]==2
    assert o["by_tour"]["ATP_MAIN"]["pit_scheduled_qualifying"]==1
    assert o["by_tour"]["ATP_MAIN"]["first_production_completed_run_on_time"]==1
    assert o["by_tour"]["ATP_MAIN"]["on_time_aligned_model_picks"]==1
    assert o["by_tour"]["ATP_MAIN"]["aligned_pick_coverage_of_pit_proxy"]==.5
    assert o["issue_counts"]["NO_TRUSTED_IN_WINDOW_PIT_FIXTURE"]==1
    assert o["issue_counts"]["PIT_NOT_SCHEDULED"]==1
    assert o["issue_counts"]["QUALIFYING_REPORTED_SEPARATELY"]==1
    assert o["pit_proxy_coverage_not_all_official_eligible_matches"] is True
    assert o["cannot_establish_unobserved_provider_fixtures"] is True


def test_match_prediction_after_preissue_cutoff_is_not_counted():
    r=_classify(fixture(1,run_at=GAME-timedelta(minutes=1)),
                start=START,end=END,as_of=ASOF)
    assert r["first_run_targeted"]
    assert not r["first_run_on_time"]
    assert r["issue"]=="FIRST_PRODUCTION_RUN_AFTER_PREMATCH_CUTOFF"
    assert not r["a1_predicted"]


def test_pit_fixture_ingested_with_only_ten_minute_lead_is_not_eligible():
    r=_classify(fixture(
        1,fixture_known=GAME-timedelta(minutes=10)),
        start=START,end=END,as_of=ASOF)
    assert not r["pit_visible"]
    assert not r["main_draw_pit_proxy"]
    assert r["issue"]=="NO_TRUSTED_IN_WINDOW_PIT_FIXTURE"


def test_rescheduled_cross_window_match_excluded_from_pit_window():
    r=_classify(fixture(
        1,pit_start=START-timedelta(days=1)),
        start=START,end=END,as_of=ASOF)
    assert not r["pit_visible"]
    assert r["issue"]=="NO_TRUSTED_IN_WINDOW_PIT_FIXTURE"


def test_no_hindsight_market_read_model_pick_after_match_counts():
    r=_classify(fixture(1,pick_at=GAME+timedelta(hours=2)),
                start=START,end=END,as_of=ASOF)
    assert r["first_run_on_time"]
    assert not r["on_time_aligned_pick"]
    assert r["issue"]=="NO_ON_TIME_ALIGNED_MODEL_PICK"


def test_completed_result_must_have_timestamp_after_match_start():
    r=_classify(fixture(1,pick_at=GAME-timedelta(hours=1),
                        result_known=GAME-timedelta(hours=1)),
                start=START,end=END,as_of=ASOF)
    assert r["on_time_aligned_pick"]
    assert not r["valid_completed_result"]


def test_no_duplicate_canonical_match_can_inflate_coverage():
    with pytest.raises(ValueError,match="duplicate canonical match"):
        assess([fixture(1),fixture(1)])


def test_source_query_contains_all_three_point_in_time_timestamp_fences():
    s=fixture_source_sql()
    assert "v.as_of <= v.match_start_at-INTERVAL '30 minutes'" in s
    assert "v.source_observed_at <= v.match_start_at-INTERVAL '30 minutes'" in s
    assert "v.ingested_at <= v.match_start_at-INTERVAL '30 minutes'" in s
    assert "pr.run_status='COMPLETED'" in s
    assert "pr.experiment_id IS NULL" in s
    assert "aligned_model_picks" in s
    assert "odds_snapshots" not in s


def test_legacy_result_timestamp_is_not_pre_match_issue_timestamp():
    """Directly assert the risky historical evaluator's actual boundary."""
    observation=ValidationObservation(
        season_year=2024,match_id=98,player_1_id=10,player_2_id=20,
        surface="HARD",tour_scope="ATP_MAIN",competition_tier="ATP_250",
        best_of_sets=3,qualifying_flag=False,match_start_at=GAME,
        outcome_status="COMPLETED",winner_side=1,
        source_observed_at=GAME+timedelta(hours=3),
        as_of=GAME+timedelta(hours=3),
    )
    target=build_prediction_target(observation)
    assert target.prediction_boundary==GAME+timedelta(hours=3)
    assert target.prediction_boundary>GAME
    # Same result is deliberately NOT ingested into its own player-rating
    # state before prediction, but another later finished match could be.
    assert "legacy_boundary" in legacy_historical_sql()


def test_legacy_evaluator_can_use_result_of_match_played_after_target():
    """Negative control: this old evaluation is ordered by result arrival.

    An earlier match's winner is held out, but a later-played match with
    faster result ingestion is already present in the Elo history when the
    earlier match is finally evaluated. That is future match information
    relative to the *earlier fixture's start*, despite its own label holdout.
    """
    older_game=datetime(2024,6,1,12,tzinfo=UTC)
    later_game=older_game+timedelta(days=3)
    earlier=ValidationObservation(
        season_year=2024,match_id=7001,player_1_id=100,player_2_id=200,
        surface="HARD",tour_scope="ATP_MAIN",competition_tier="ATP_250",
        best_of_sets=3,qualifying_flag=False,match_start_at=older_game,
        outcome_status="COMPLETED",winner_side=2,
        source_observed_at=older_game+timedelta(days=7),
        as_of=older_game+timedelta(days=7),
    )
    later=ValidationObservation(
        season_year=2024,match_id=7002,player_1_id=100,player_2_id=300,
        surface="HARD",tour_scope="ATP_MAIN",competition_tier="ATP_250",
        best_of_sets=3,qualifying_flag=False,match_start_at=later_game,
        outcome_status="COMPLETED",winner_side=1,
        source_observed_at=later_game+timedelta(hours=3),
        as_of=later_game+timedelta(hours=3),
    )
    report=run_walk_forward_validation([earlier,later])
    records={p.match_id:p for p in report["prediction_records"]}
    assert later_game > older_game
    assert later.visibility_at < earlier.visibility_at
    assert records[7001].prediction_as_of > older_game
    assert records[7001].player_1_overall_prior_matches >= 1
    assert records[7002].player_1_overall_prior_matches == 0


def test_legacy_census_separates_result_order_and_boundary_evidence():
    row=(2024,13,"COMPLETED",1,GAME+timedelta(hours=2),GAME,
         3,4,GAME-timedelta(hours=2),GAME-timedelta(hours=2),
         GAME-timedelta(hours=2))
    row2=(2025,14,"COMPLETED",2,GAME+timedelta(hours=1),GAME,
          10,11,GAME+timedelta(hours=2),GAME+timedelta(hours=2),
          GAME+timedelta(hours=2))
    r=summarize_legacy([row,row2])
    assert r["totals"]["stored_result_matches"]==2
    assert r["totals"]["winner_player_1_stored"]==1
    assert r["totals"]["winner_player_2_stored"]==1
    assert r["totals"]["legacy_prediction_boundary_after_preissue_cutoff"]==2
    assert r["totals"]["legacy_prediction_boundary_at_or_after_start"]==2
    assert r["totals"]["latest_legacy_joined_fixture_version_not_pit"]==1
    assert r["cannot_validate_claimed_14000_at_75pct"] is True


def test_empty_migrated_postgres_is_read_only_and_has_no_fake_denominator(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        out=run(conn,start=START,end=END,as_of=ASOF)
    assert out["stored_canonical_fixtures"]==0
    assert out["main_draw_pit_proxy"]==0
    assert out["legacy_historical_provenance"]["totals"]=={}
    assert out["db_writes"] is False


def test_invalid_window_rejected_without_sql():
    with pytest.raises(ValueError,match="start < end"):
        summarize([],start=END,end=START,as_of=ASOF)
