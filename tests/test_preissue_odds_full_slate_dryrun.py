"""Read-only preissue market candidate budget never buys/fetches extra odds."""
from datetime import UTC,datetime,timedelta

import psycopg
import pytest

from sports_edge_tennis.live.oddspapi_budget import OddspapiBudgetPolicy
from sports_edge_tennis.live.oddspapi_identity_cache import OddspapiCachedIdentity
from sports_edge_tennis.validation.preissue_odds_full_slate_dryrun import (
    dry_run,run,
)

NOW=datetime(2026,11,22,15,40,tzinfo=UTC)


def fixture(mid=1,*,start=None,qualifying=False):
    return {
        "match_id":mid,"match_start":start or NOW+timedelta(days=1),
        "tour_scope":"CHALLENGER","qualifying_flag":qualifying,
        "sportradar_event_id":f"SR{mid}",
    }


def ident(mid=1,*,tid=120,last=None,resolution="EXACT",event=None):
    return OddspapiCachedIdentity(
        canonical_match_id=mid,
        sportradar_event_id=f"SR{mid}",
        oddspapi_event_id=event or f"OP{mid}",
        betradar_id=None,oddspapi_tournament_id=tid,
        player_orientation="AS_LISTED",
        resolution_status=resolution,
        scheduled_start=NOW+timedelta(days=1),
        resolved_at=NOW-timedelta(minutes=30),
        last_verified_at=last or NOW-timedelta(minutes=10),
    )


def test_all_canonical_future_matches_included_not_just_manager_picks():
    x=dry_run(candidates=[fixture(1),fixture(2),fixture(3)],
              identity_resolver=lambda i: ident(i,tid=1),
              now=NOW)
    assert x["counts"]["internal_eligible_main_singles"]==3
    assert x["verified_warm_fixture_count"]==3
    assert x["warm_unique_tournament_count"]==1
    assert x["provisionally_schedulable_warm_fixture_count"]==3
    assert x["estimated_bookmaker_queries_per_cycle_IF_APPROVED"]==3
    assert x["no_provider_calls_made"] and x["no_database_writes"]
    assert x["not_all_official_fixture_denominator"]


def test_unknown_identity_and_qualifying_are_excluded_without_fuzzy_matching():
    x=dry_run(candidates=[fixture(1),fixture(2,qualifying=True),fixture(3)],
              identity_resolver=lambda i:ident(i) if i==1 else None,
              now=NOW)
    assert x["counts"]["qualifying_or_unknown_draw_excluded"]==1
    assert x["counts"]["identity_cache_missing"]==1
    assert x["verified_warm_fixture_count"]==1
    assert x["missing_or_invalid_cache_sample"]==[
        {"match_id":3,"reason":"NO_CACHED_ODDSPAPI_ID"}
    ]


def test_stale_cache_invalidated_only_as_readonly_report_not_deleted():
    x=dry_run(candidates=[fixture(1)],
              identity_resolver=lambda i:ident(i,last=NOW-timedelta(hours=25)),
              now=NOW)
    assert x["counts"]["identity_identity_verification_expired"]==1
    assert x["verified_warm_fixture_count"]==0
    assert x["no_database_writes"] is True


def test_5calls_cycle_hard_bound_and_deferred_unknown_early_ids():
    inputs=[fixture(i) for i in range(1,9)]
    x=dry_run(candidates=inputs,
              identity_resolver=lambda i:ident(i,tid=i),
              now=NOW,
              policy=OddspapiBudgetPolicy(
                  monthly_limit=5000,reserve=500,
                  per_cycle_limit=5,tournaments_per_call=5,
              ))
    assert x["provisionally_schedulable_warm_fixture_count"]==5
    assert x["deferred_warm_fixture_count"]==3
    assert x["estimated_bookmaker_queries_per_cycle_IF_APPROVED"]==3
    assert x["requires_separate_approval_before_actual_request_work"]


def test_empty_live_disposable_db_safe_smoke(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        x=run(conn,now=NOW,horizon_days=2)
    assert x["input_unique_canonical_fixture_count"]==0
    assert x["estimated_bookmaker_queries_per_cycle_IF_APPROVED"]==0
    assert x["no_provider_calls_made"]


def test_original_deadline_and_unverifiable_draw_excluded():
    x=dry_run(candidates=[
        fixture(1,start=NOW+timedelta(minutes=15)),
        fixture(2,qualifying=None),
    ],identity_resolver=lambda i:ident(i),now=NOW)
    assert x["counts"]["outside_prematch_or_horizon"]==1
    assert x["counts"]["qualifying_or_unknown_draw_excluded"]==1
