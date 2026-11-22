"""Original preissue bookmaker gaps: never backdate or pair independent markets."""
from datetime import UTC,datetime,timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.original_preissue_odds_gap_audit import (
    _pair, _safe_live_quote,audit,classify,run,
)

ISSUE=datetime(2026,11,12,7,tzinfo=UTC)
START=ISSUE+timedelta(hours=8)
ASOF=ISSUE+timedelta(days=5)


def pick(*,mapped=1,issue=ISSUE,start=START,mid=1333):
    return {
        "match_id":mid,"run_id":10,
        "model_as_of":issue-timedelta(minutes=3),
        "prediction_finalized_at":issue,
        "pick_type":"MANAGER_CONSENSUS",
        "selected_side":1,"model_probability":.7,
        "scheduled_start":start,"tour":"CHALLENGER",
        "oddspapi_id_count":mapped,
        "unique_oddspapi_ids":1 if mapped else 0,
    }


def quote(side=1,*,mid=1333,book="PINNACLE",source="OFFERED",
          snapshot="same-source-snapshot",observed=ISSUE-timedelta(minutes=10),
          ingested=ISSUE-timedelta(minutes=9),market="OPEN",
          origin="LIVE_PROVIDER",received=ISSUE-timedelta(minutes=9),
          raw="ODDSPAPI",price=1.87):
    return {
        "match_id":mid,"odds_snapshot_id":side,
        "source_system":source,"bookmaker_code":book,
        "source_snapshot_id":snapshot,
        "source_observed_at":observed,"as_of":observed,
        "ingested_at":ingested,"market_status":market,
        "selection_type":f"PLAYER_{side}",
        "decimal_price":price,"odds_origin":origin,
        "raw_provider":raw,
        "raw_received_at":received,
        "raw_provider_observed_at":observed,
        "raw_time_basis":"PROVIDER_TIME",
    }


def test_qualifying_open_preissue_same_book_same_snapshot_pair():
    q=[quote(1),quote(2,price=2.01)]
    c=classify(pick(),q)
    assert c["category"]=="QUALIFIED_ORIGINAL_LIVE_PREISSUE_PAIR"
    assert c["qualified_pair_count"]==1
    assert c["latest_qualified_pair"]["bookmaker_favourite"]==1


def test_market_post_original_issue_is_not_preissue():
    late=ISSUE+timedelta(minutes=1)
    q=[quote(1,observed=late,ingested=late,received=late),
       quote(2,observed=late,ingested=late,received=late)]
    c=classify(pick(),q)
    assert c["category"]=="ONLY_LATER_OR_BACKFILLED_ODDS"
    assert not c["qualifying_market_is_live_and_received_before_first_issue"]


def test_retro_imported_old_source_timestamp_stays_unqualified():
    q=[quote(1,ingested=ISSUE+timedelta(days=1),received=ISSUE+timedelta(days=1)),
       quote(2,ingested=ISSUE+timedelta(days=1),received=ISSUE+timedelta(days=1))]
    c=classify(pick(),q)
    assert c["category"]=="ONLY_LATER_OR_BACKFILLED_ODDS"


def test_raw_received_after_original_issue_even_if_odds_observed_earlier():
    late=ISSUE+timedelta(minutes=1)
    q=[quote(1,received=late),quote(2,received=late)]
    assert classify(pick(),q)["qualified_pair_count"]==0


def test_different_snapshot_id_or_different_bookmaker_must_not_pair():
    q=[quote(1),quote(2,snapshot="different")]
    assert classify(pick(),q)["category"]=="PREISSUE_PARTIAL_OR_UNPAIRED_LIVE_MARKET"
    q=[quote(1),quote(2,book="BET365")]
    assert classify(pick(),q)["category"]=="PREISSUE_PARTIAL_OR_UNPAIRED_LIVE_MARKET"


def test_historical_synthetic_or_suspended_not_original_live_pair():
    q=[quote(1,origin="HISTORICAL"),quote(2,origin="HISTORICAL")]
    assert classify(pick(),q)["category"]=="PREISSUE_ODDS_NONLIVE_SUSPENDED_OR_UNSAFE"
    q=[quote(1,market="SUSPENDED"),quote(2,market="SUSPENDED")]
    assert classify(pick(),q)["category"]=="PREISSUE_ODDS_NONLIVE_SUSPENDED_OR_UNSAFE"
    q=[quote(1,origin="SYNTHETIC"),quote(2,origin="SYNTHETIC")]
    assert classify(pick(),q)["qualified_pair_count"]==0


def test_market_unknown_snapshot_or_missing_raw_cannot_qualify():
    assert not _safe_live_quote(quote(1,snapshot=None),ISSUE)
    assert not _safe_live_quote(quote(1,raw=None),ISSUE)
    assert not _safe_live_quote(quote(1,received=None),ISSUE)
    assert not _safe_live_quote(quote(1,price=float("nan")),ISSUE)


def test_duplicated_conflicting_prices_in_one_snapshot_are_rejected():
    q=[quote(1,price=1.7),quote(1,price=1.9),quote(2,price=2.1)]
    assert _pair(q,issue=ISSUE)==[]


def test_tie_favourite_not_invented():
    p=_pair([quote(1,price=1.9),quote(2,price=1.9)],issue=ISSUE)
    assert len(p)==1
    assert p[0]["bookmaker_favourite"] is None


def test_no_odds_and_odds_mapping_taxonomy():
    assert classify(pick(mapped=0),[])["category"]=="NO_ODDSPAPI_FIXTURE_MAPPING_NO_QUOTE"
    assert classify(pick(mapped=1),[])["category"]=="MAPPED_ODDSPAPI_BUT_NO_STORED_ODDS"
    assert classify(pick(start=ISSUE+timedelta(minutes=8)),[])["category"]=="INVALID_OR_LATE_PIT_ORIGINAL_ISSUE"


def test_100_unmatched_case_sampling_is_deterministic_and_unique():
    picks=[pick(mid=i,mapped=0) for i in range(110)]
    r=audit(picks=picks,quotes=[],as_of=ASOF,sample_size=100)
    assert r["original_selection_rows"]==110
    assert r["unmatched_sample_count"]==100
    assert r["unqualified_original_selections"]==110
    assert r["failure_categories"]=={"NO_ODDSPAPI_FIXTURE_MAPPING_NO_QUOTE":110}
    again=audit(picks=picks,quotes=[],as_of=ASOF,sample_size=100)
    assert r["sample_sha256"]==again["sample_sha256"]
    with pytest.raises(ValueError,match="duplicate"):
        audit(picks=[pick(),pick()],quotes=[],as_of=ASOF)


def test_empty_disposable_database_read_only_smoke(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        r=run(conn,start=ISSUE-timedelta(days=10),end=ASOF,
              as_of=ASOF,sample_size=100)
    assert r["original_selection_rows"]==0
    assert r["no_db_writes"] is True
    assert r["no_new_provider_calls"] is True
