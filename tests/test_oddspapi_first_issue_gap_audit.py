"""OddsPapi match IDs and genuinely pre-issued two-sided bookmaker quote tests."""
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.oddspapi_first_issue_gap_audit import (
    _reason, query, run, summarize,
)

ASOF=datetime(2026,11,21,3,30,tzinfo=UTC)
ISSUE=datetime(2026,11,15,12,tzinfo=UTC)


def fixture(mid=1,*,mapped_ever=0,mapped_pit=0,mapped_late=0,
            ever=0,pit=0,recent=0,opened=0,p1=0,p2=0,
            paired=False,conflict=False,late=False,tour="ATP_MAIN"):
    return (
        mid,ISSUE,ISSUE+(timedelta(minutes=10) if late else timedelta(hours=2)),
        tour,conflict,
        "ODDSPAPI" if paired else None,
        "PINNACLE" if paired else None,
        1.45 if paired else None,2.75 if paired else None,
        mapped_ever,mapped_pit,mapped_late,
        ever,pit,recent,opened,p1,p2,
        ISSUE-timedelta(minutes=3) if ever else None,
        ISSUE-timedelta(minutes=1) if pit else None,
        1 if p1 or p2 else 0,
    )


def report(rows):
    return summarize(rows,as_of=ASOF)


def test_no_source_ids_and_no_odds_recorded_are_separate():
    z=report([fixture(1)])
    assert z["funnel_not_a_strictly_nested_chain"]["original_aligned_picks"]==1
    assert z["mutually_exclusive_first_issue_quote_blockers"]=={
        "NO_NORMALIZED_MATCH_WINNER_ODDS_STORED":1
    }
    assert "odds_papi_match_id_ever" not in z["funnel_not_a_strictly_nested_chain"]
    assert z["subscriptions_purchased"]==0


def test_mapping_created_after_issue_does_not_become_pit_mapping():
    r=report([fixture(1,mapped_ever=1,mapped_late=1,
                      ever=3,pit=0)])
    d=r["funnel_not_a_strictly_nested_chain"]
    assert d["odds_papi_match_id_ever"]==1
    assert d["odds_papi_mapping_added_after_pick"]==1
    assert "odds_papi_match_id_by_pick" not in d
    assert r["mutually_exclusive_first_issue_quote_blockers"]=={
        "QUOTES_STORED_BUT_NOT_AVAILABLE_AT_ORIGINAL_ISSUE":1
    }


def test_old_preissue_quotes_are_not_fresh():
    r=report([fixture(1,mapped_ever=1,mapped_pit=1,
                      ever=10,pit=2,recent=0)])
    assert r["mutually_exclusive_first_issue_quote_blockers"]=={
        "AVAILABLE_QUOTES_OLDER_THAN_SIX_HOURS":1
    }


def test_both_player_sides_stored_but_cannot_be_paired():
    r=report([fixture(1,ever=10,pit=8,recent=8,
                      opened=6,p1=3,p2=3,paired=False)])
    assert r["mutually_exclusive_first_issue_quote_blockers"]=={
        "BOTH_SIDES_EXIST_BUT_UNMATCHED_BOOK_OR_TIME_OR_STATUS":1
    }
    assert r["funnel_not_a_strictly_nested_chain"]["both_open_player_sides_in_same_fixture"]==1
    assert "strict_synchronized_pair" not in r["funnel_not_a_strictly_nested_chain"]


def test_paired_bookmaker_requires_same_fixture_early_timestamp_and_both_sides():
    r=report([fixture(1,ever=10,pit=8,recent=7,opened=6,p1=3,p2=3,paired=True)])
    assert r["mutually_exclusive_first_issue_quote_blockers"]=={
        "QUALIFIED_SAME_ISSUE_TWO_SIDED_BOOKMAKER_QUOTE":1
    }
    assert r["funnel_not_a_strictly_nested_chain"]["strict_synchronized_pair"]==1
    assert r["first_50_unpaired_examples_no_provider_secrets"]==[]


def test_invalid_pit_fixture_and_side_reversal_not_overlooked():
    r=report([fixture(1,late=True),fixture(2,conflict=True)])
    assert r["mutually_exclusive_first_issue_quote_blockers"]=={
        "UNSAFE_OR_MISSING_ORIGINAL_PIT_FIXTURE_OR_PLAYER_ORDER":2
    }
    assert "same_issue_safe_fixture" not in r["funnel_not_a_strictly_nested_chain"]


def test_duplicates_and_inconsistent_mapping_count_rejected():
    with pytest.raises(ValueError,match="duplicate"):
        report([fixture(1),fixture(1)])
    with pytest.raises(ValueError,match="impossible mapping"):
        report([fixture(1,mapped_ever=0,mapped_pit=1)])
    with pytest.raises(ValueError,match="impossible quote"):
        report([fixture(1,ever=0,pit=1)])
    with pytest.raises(ValueError,match="paired quote lacks"):
        report([fixture(1,paired=True,ever=3,pit=2,recent=2,p1=0,p2=2)])


def test_every_unpaired_example_has_no_provider_secrets():
    z=report([fixture(1,ever=1)])
    a=z["first_50_unpaired_examples_no_provider_secrets"]
    assert len(a)==1
    assert a[0]["match_id"]==1
    assert "api_key" not in str(a).lower()
    assert z["paid_api_calls"]==0
    assert z["market_quotes_retrospectively_repaired"] is False


def test_sql_original_pit_and_empty_db_readonly(migrated_postgres):
    s=query()
    assert "o.ingested_at<=b.picked_at" in s
    assert "msi.created_at <= b.picked_at" in s
    assert "LIKE '%%ODDS%%PAPI%%'" in s
    assert "o.market_type='MATCH_WINNER'" in s
    assert "o.market_status='OPEN'" in s
    assert "INSERT " not in s
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        z=run(conn,as_of=ASOF)
    assert z["funnel_not_a_strictly_nested_chain"]=={}
    assert z["database_writes"] is False
